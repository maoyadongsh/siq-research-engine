from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.openshell import runtime_state_lifecycle_smoke as runtime_smoke

SOURCE = Path(__file__).resolve().parents[1] / "build_policy.py"
ROOT = "/home/siq/siq-research-engine"
SHA = "a" * 64
TEST_BRIDGE_GATEWAY_IP = "172.28.0.1"
SAFE_ETC_READ_ONLY = [
    "/etc/alternatives",
    "/etc/ca-certificates",
    "/etc/ca-certificates.conf",
    "/etc/debian_version",
    "/etc/fonts",
    "/etc/gai.conf",
    "/etc/group",
    "/etc/host.conf",
    "/etc/hostname",
    "/etc/hosts",
    "/etc/inputrc",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/netconfig",
    "/etc/networks",
    "/etc/nsswitch.conf",
    "/etc/os-release",
    "/etc/passwd",
    "/etc/profile",
    "/etc/protocols",
    "/etc/resolv.conf",
    "/etc/services",
    "/etc/ssl",
    "/etc/terminfo",
]


def _module():
    spec = importlib.util.spec_from_file_location("siq_build_policy_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base() -> dict[str, object]:
    return {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": [
                *SAFE_ETC_READ_ONLY,
                "/home/sandbox/.bashrc",
                "/home/sandbox/.profile",
                "/lib",
                "/opt/hermes-agent",
                "/opt/siq",
                "/proc",
                "/usr",
                "${SIQ_PROJECT_ROOT}",
            ],
            "read_write": ["/dev/null", "/sandbox", "/tmp"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": "sandbox", "run_as_group": "sandbox"},
        "network_policies": {
            "siq_egress_guard": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18792}],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
            "siq_data_broker": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18793}],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
            "siq_internal_services": {
                "endpoints": [
                    {"host": "host.openshell.internal", "port": 8004},
                    {"host": "host.openshell.internal", "port": 8006},
                    {"host": "host.openshell.internal", "port": 8007},
                    {"host": "host.openshell.internal", "port": 8013},
                ],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
        },
    }


def _profile(*, static_write: list[str] | None = None, require_task: bool = True) -> dict[str, object]:
    return {
        "schema_version": "siq.openshell.profile_policy.v1",
        "profile": "siq_analysis",
        "filesystem_policy": {
            "read_write": [
                *(static_write or ["${SIQ_PROJECT_ROOT}/data/hermes/home/profiles/siq_analysis/cache"]),
            ],
            "required_files": [],
            "dynamic_write_kinds": ["analysis"],
            "require_task_write_path": require_task,
        },
        "required_external_controls": ["egress guard"],
    }


def _registry(*paths: str) -> dict[str, object]:
    entries = [
        {
            "path": path,
            "kind": "finalized_report",
            "owner": "ingestion",
            "identity": {
                "market": "CN",
                "company_id": "CN:600001",
                "report_id": "2025-annual",
            },
            "source_manifest": f"{path}/artifact_manifest.json",
            "manifest_sha256": SHA,
            "finalization_sha256": SHA,
            "recursive": True,
        }
        for path in paths
    ]
    return {
        "schema_version": "siq.immutable_paths.v1",
        "project_root": "${SIQ_PROJECT_ROOT}",
        "source_digest": SHA,
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "by_kind": {"finalized_report": len(entries)},
            "skipped_by_reason": {},
        },
    }


def test_compile_expands_project_root_and_allows_task_analysis_child() -> None:
    module = _module()
    compiled = module.compile_policy(
        base=_base(),
        profile=_profile(),
        registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
        project_root=ROOT,
        bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
        writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
    )

    fs = compiled.policy["filesystem_policy"]
    assert fs["include_workdir"] is False
    assert ROOT in fs["read_only"]
    assert "/etc/profile" in fs["read_only"]
    assert "/home/sandbox/.profile" in fs["read_only"]
    assert "/home/sandbox/.bashrc" in fs["read_only"]
    assert f"{ROOT}/data/wiki/companies/600001-Test/analysis" in fs["read_write"]
    for rule in compiled.policy["network_policies"].values():
        assert all(endpoint["allowed_ips"] == [f"{TEST_BRIDGE_GATEWAY_IP}/32"] for endpoint in rule["endpoints"])
    assert compiled.summary["network_gateway_cidr"] == f"{TEST_BRIDGE_GATEWAY_IP}/32"
    assert compiled.summary["immutable_entry_count"] == 1
    assert compiled.summary["task_scoped_write_count"] == 1
    assert json.loads(compiled.content)["landlock"]["compatibility"] == "hard_requirement"


@pytest.mark.parametrize(
    "write_path",
    [
        f"{ROOT}/data/wiki/companies/600001-Test/analysis/.work/pilot-a17e4c9b620d",
        f"{ROOT}/data/wiki/hk/companies/600001-Test/analysis/.work/pilot-a17e4c9b620d",
    ],
)
def test_compile_allows_only_the_requested_task_scoped_analysis_descendant(write_path: str) -> None:
    module = _module()
    compiled = module.compile_policy(
        base=_base(),
        profile=_profile(),
        registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
        project_root=ROOT,
        bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
        writable_paths=[write_path],
    )

    read_write = compiled.policy["filesystem_policy"]["read_write"]
    assert write_path in read_write
    assert write_path.rsplit("/", 2)[0] not in read_write
    assert write_path.rsplit("/", 1)[0] not in read_write
    assert compiled.summary["task_scoped_write_count"] == 1


@pytest.mark.parametrize("path", ["/", "/etc", "/etc/openshell", "/etc/openshell/tls"])
def test_compile_rejects_read_access_to_openshell_control_credentials(path: str) -> None:
    module = _module()
    base = _base()
    base["filesystem_policy"]["read_only"].append(path)

    with pytest.raises(module.PolicyCompileError, match="control credentials"):
        module.compile_policy(
            base=base,
            profile=_profile(),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
        )


@pytest.mark.parametrize(
    "write_path",
    [
        f"{ROOT}/data/wiki",
        f"{ROOT}/data/wiki/companies/600001-Test/reports/2025-annual",
        f"{ROOT}/data/wiki/companies/600001-Test/reports/2025-annual/tmp",
    ],
)
def test_compile_rejects_write_paths_overlapping_immutable_data(write_path: str) -> None:
    module = _module()

    with pytest.raises(module.PolicyCompileError, match="(immutable|controlled)"):
        module.compile_policy(
            base=_base(),
            profile=_profile(static_write=[write_path], require_task=False),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[],
        )


def test_compile_rejects_code_or_wrong_profile_output_as_dynamic_write() -> None:
    module = _module()
    for path in (f"{ROOT}/apps/api", f"{ROOT}/data/wiki/companies/600001-Test/factcheck"):
        with pytest.raises(module.PolicyCompileError, match="allowed output class"):
            module.compile_policy(
                base=_base(),
                profile=_profile(),
                registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
                project_root=ROOT,
                bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
                writable_paths=[path],
            )


def test_compile_rejects_profile_that_expands_dynamic_output_class() -> None:
    module = _module()
    profile = _profile()
    profile["filesystem_policy"]["dynamic_write_kinds"] = ["analysis", "factcheck"]  # type: ignore[index]

    with pytest.raises(module.PolicyCompileError, match="fixed to analysis"):
        module.compile_policy(
            base=_base(),
            profile=profile,
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
        )


def test_profile_rejects_legacy_per_file_runtime_write_contract(tmp_path: Path) -> None:
    module = _module()
    profile = _profile()
    assert module._validate_required_runtime_files(profile, tmp_path) == []

    profile["filesystem_policy"]["required_files"] = [  # type: ignore[index]
        "${SIQ_PROJECT_ROOT}/data/hermes/home/profiles/siq_analysis/state.db"
    ]
    with pytest.raises(module.PolicyCompileError, match="required Hermes runtime file"):
        module._validate_required_runtime_files(profile, tmp_path)


def test_candidate_image_attestation_replaces_host_marker_validation(tmp_path: Path) -> None:
    module = _module()
    state_root = tmp_path / "var/openshell/siq-analysis"
    state_root.mkdir(parents=True)
    script = tmp_path / module.CANDIDATE_SMOKE_SCRIPT_RELATIVE
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    candidate = {
        "schema_version": "siq.openshell.candidate_image.v1",
        "image_ref": "siq/hermes-openshell-siq-analysis:" + "a" * 24,
        "image_id": "sha256:" + "b" * 64,
        "architecture": "arm64",
        "user": "sandbox:sandbox",
        "context_sha256": "c" * 64,
        "runtime_config_sha256": "d" * 64,
    }
    candidate_path = tmp_path / module.CANDIDATE_STATE_RELATIVE
    candidate_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    candidate_path.chmod(0o600)
    runtime_root = tmp_path / "runtime-lifecycle-evidence"
    runtime_root.mkdir(mode=0o700)
    runtime_lifecycle = runtime_smoke.run_lifecycle_smoke(runtime_root)
    runtime_root.rmdir()
    smoke = {
        "schema_version": "siq.openshell.candidate_image_smoke.v1",
        "status": "passed",
        "profile": "siq_analysis",
        "image_ref": candidate["image_ref"],
        "image_id": candidate["image_id"],
        "candidate_state_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "smoke_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "readiness_effect": "none",
        "runtime_lifecycle": runtime_lifecycle,
        "checks": module.CANDIDATE_SMOKE_CHECKS,
    }
    smoke_path = tmp_path / module.CANDIDATE_SMOKE_RELATIVE
    smoke_path.write_text(json.dumps(smoke) + "\n", encoding="utf-8")
    smoke_path.chmod(0o600)

    assert "hermes_version_exact" in module.CANDIDATE_SMOKE_CHECKS
    assert (
        module._validate_candidate_runtime_attestation(tmp_path) == hashlib.sha256(smoke_path.read_bytes()).hexdigest()
    )
    smoke["checks"] = [check for check in smoke["checks"] if check != "hermes_version_exact"]
    smoke_path.write_text(json.dumps(smoke) + "\n", encoding="utf-8")
    with pytest.raises(module.PolicyCompileError, match="missing or stale"):
        module._validate_candidate_runtime_attestation(tmp_path)

    smoke["checks"] = module.CANDIDATE_SMOKE_CHECKS
    smoke["runtime_lifecycle"]["provider_contacted"] = True
    smoke_path.write_text(json.dumps(smoke) + "\n", encoding="utf-8")
    with pytest.raises(module.PolicyCompileError, match="missing or stale"):
        module._validate_candidate_runtime_attestation(tmp_path)


def test_compile_rejects_control_plane_and_open_shell_state_static_write_roots() -> None:
    module = _module()
    for path in (f"{ROOT}/apps/api", f"{ROOT}/var", f"{ROOT}/var/openshell"):
        with pytest.raises(module.PolicyCompileError, match="(controlled|OpenShell)"):
            module.compile_policy(
                base=_base(),
                profile=_profile(static_write=[path], require_task=False),
                registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
                project_root=ROOT,
                bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
                writable_paths=[],
            )


@pytest.mark.parametrize("path", ["/etc/siq-agent", "/opt/siq-agent", "/home/other/siq-agent"])
def test_compile_rejects_all_non_project_profile_write_paths(path: str) -> None:
    module = _module()

    with pytest.raises(module.PolicyCompileError, match="non-project write path"):
        module.compile_policy(
            base=_base(),
            profile=_profile(static_write=[path], require_task=False),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[],
        )


def test_write_target_preflight_rejects_symlink_alias(tmp_path: Path) -> None:
    module = _module()
    target = tmp_path / "data/wiki/companies/600001-Test/reports/2025-annual"
    target.mkdir(parents=True)
    alias = tmp_path / "data/wiki/companies/600001-Test/analysis"
    alias.symlink_to(target, target_is_directory=True)

    with pytest.raises(module.PolicyCompileError, match="uses a symlink"):
        module._validate_write_target_aliases(
            {"filesystem_policy": {"read_write": [str(alias)]}},
            tmp_path,
        )


def test_registry_source_verification_rejects_stale_manifest(tmp_path: Path) -> None:
    module = _module()
    report = tmp_path / "data/wiki/companies/600001-Test/reports/2025-annual"
    report.mkdir(parents=True)
    manifest = report / "artifact_manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    registry = _registry("data/wiki/companies/600001-Test/reports/2025-annual")
    registry["entries"][0]["manifest_sha256"] = module._sha256(manifest)  # type: ignore[index]

    module._validate_registry(registry, module.PurePosixPath(str(tmp_path)), verify_sources=True)
    manifest.write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(module.PolicyCompileError, match="digest is stale"):
        module._validate_registry(registry, module.PurePosixPath(str(tmp_path)), verify_sources=True)


def test_compile_rejects_unsafe_static_policy() -> None:
    module = _module()
    base = _base()
    base["filesystem_policy"]["include_workdir"] = True  # type: ignore[index]

    with pytest.raises(module.PolicyCompileError, match="include_workdir"):
        module.compile_policy(
            base=base,
            profile=_profile(),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
        )


def test_compile_rejects_direct_database_network_bypass() -> None:
    module = _module()
    base = _base()
    base["network_policies"]["siq_internal_services"]["endpoints"].append(  # type: ignore[index]
        {"host": "host.openshell.internal", "port": 15432}
    )

    with pytest.raises(module.PolicyCompileError, match="internal service endpoints"):
        module.compile_policy(
            base=base,
            profile=_profile(),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
        )


def test_compile_rejects_static_or_broad_bridge_ip_allowlists() -> None:
    module = _module()
    base = _base()
    base["network_policies"]["siq_data_broker"]["endpoints"][0]["allowed_ips"] = [  # type: ignore[index]
        "172.28.0.0/16"
    ]

    with pytest.raises(module.PolicyCompileError, match="cannot predeclare"):
        module.compile_policy(
            base=base,
            profile=_profile(),
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
        )


def test_compile_is_deterministic_and_enforces_path_limit() -> None:
    module = _module()
    writes = [f"{ROOT}/data/wiki/companies/{index:06d}-Test/analysis" for index in range(255)]
    profile = _profile()

    with pytest.raises(module.PolicyCompileError, match="256-path"):
        module.compile_policy(
            base=_base(),
            profile=profile,
            registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
            project_root=ROOT,
            bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
            writable_paths=writes,
        )

    first = module.compile_policy(
        base=_base(),
        profile=_profile(),
        registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
        project_root=ROOT,
        bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
        writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
    )
    second = module.compile_policy(
        base=_base(),
        profile=_profile(),
        registry=_registry("data/wiki/companies/600001-Test/reports/2025-annual"),
        project_root=ROOT,
        bridge_gateway_ip=TEST_BRIDGE_GATEWAY_IP,
        writable_paths=[f"{ROOT}/data/wiki/companies/600001-Test/analysis"],
    )
    assert first.content == second.content
    assert first.summary_content == second.summary_content


def test_cli_rejects_policy_input_outside_project_without_hanging(tmp_path: Path) -> None:
    module = _module()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(module.PolicyCompileError, match="outside the project root"):
        module._safe_input(project, outside)


def test_registry_sidecar_must_match_relative_path_and_digest(tmp_path: Path) -> None:
    module = _module()
    project = tmp_path / "project"
    registry = project / "var" / "openshell" / "registry" / "immutable-paths.json"
    digest = registry.with_suffix(".sha256")
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b"{}\n")
    digest.write_text(f"{module._sha256(registry)}  var/openshell/registry/immutable-paths.json\n", encoding="ascii")

    assert module._verify_registry_digest(registry, digest, project) == module._sha256(registry)
    digest.write_text(f"{module._sha256(registry)}  wrong.json\n", encoding="ascii")
    with pytest.raises(module.PolicyCompileError, match="sidecar"):
        module._verify_registry_digest(registry, digest, project)
