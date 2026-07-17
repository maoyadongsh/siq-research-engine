from __future__ import annotations

import errno
import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.openshell import (
    build_milvus_write_protection_proof as proof,
    probe_milvus_sandbox_boundary as sandbox_probe,
    run_milvus_boundary_proof as boundary_lifecycle,
)
from scripts.openshell.siq_analysis_lifecycle import SandboxIdentity


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for relative in (*proof.POLICY_CONTRACT_FILES, proof.DATA_BROKER_SOURCE, proof.SANDBOX_PROBE_SOURCE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative.as_posix()}\n", encoding="utf-8")
        path.chmod(0o644)
    return root


def _policy(*, direct_milvus: bool = False) -> dict[str, object]:
    internal = [
        {"host": "host.openshell.internal", "port": port}
        for port in (8_004, 8_006, 8_007, 8_013)
    ]
    if direct_milvus:
        internal.append({"host": "host.openshell.internal", "port": 19_530})
    return {
        "network_policies": {
            "siq_data_broker": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18_793}],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
            "siq_egress_guard": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18_792}],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
            "siq_internal_services": {
                "endpoints": internal,
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
        }
    }


def _manifest() -> dict[str, object]:
    return {
        "run_id": "task-001",
        "sandbox_id": "11111111-1111-1111-1111-111111111111",
        "container_id": "a" * 64,
    }


def _receipt(policy_sha256: str, *, captured_at: int = 10_000) -> dict[str, object]:
    manifest = _manifest()
    return {
        "schema_version": sandbox_probe.SCHEMA_VERSION,
        "captured_at_unix": captured_at,
        "profile": "siq_analysis",
        "run_id": manifest["run_id"],
        "sandbox_id": manifest["sandbox_id"],
        "container_id": manifest["container_id"],
        "policy_sha256": policy_sha256,
        "broker_schema_version": sandbox_probe.BROKER_SCHEMA_VERSION,
        "milvus_catalog_sha256": "c" * 64,
        "direct_milvus": {"port": 19_530, "result": "denied", "reason_class": "connect_denied"},
        "read_operations": list(sandbox_probe.READ_OPERATIONS),
        "mutation_routes_denied": list(sandbox_probe.MUTATION_ROUTES),
        "business_rows_modified": 0,
        "passed": True,
    }


def _build(tmp_path: Path, *, direct_milvus: bool = False, now: int = 10_010) -> tuple[Path, dict[str, object]]:
    root = _project(tmp_path)
    policy_value = _policy(direct_milvus=direct_milvus)
    policy_bytes = json.dumps(policy_value, sort_keys=True).encode()
    value = proof.build_proof(
        project_root=root,
        manifest=_manifest(),
        policy=policy_value,
        policy_bytes=policy_bytes,
        receipt=_receipt(proof._sha256(policy_bytes)),
        bridge_sha256="b" * 64,
        now=now,
    )
    return root, value


def test_build_and_consume_proof_binds_current_sources_bridge_and_time(tmp_path: Path) -> None:
    root, value = _build(tmp_path)
    path = root / "proof.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)

    consumed = proof.validate_consumable_proof(path, project_root=root, now=10_011, bridge_sha256="b" * 64)

    assert consumed["decision"] == "GO"
    assert consumed["checks"] == proof.EXPECTED_CHECKS
    serialized = json.dumps(consumed)
    assert "task-001" not in serialized
    assert "11111111-1111-1111-1111-111111111111" not in serialized
    assert "a" * 64 not in serialized


def test_consumption_rejects_expired_or_environment_drifted_proof(tmp_path: Path) -> None:
    root, value = _build(tmp_path)
    path = root / "proof.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(proof.MilvusProofError, match="milvus_proof_invalid"):
        proof.validate_consumable_proof(path, project_root=root, now=20_000, bridge_sha256="b" * 64)
    with pytest.raises(proof.MilvusProofError, match="milvus_proof_invalid"):
        proof.validate_consumable_proof(path, project_root=root, now=10_011, bridge_sha256="d" * 64)

    (root / proof.DATA_BROKER_SOURCE).write_text("changed\n", encoding="utf-8")
    with pytest.raises(proof.MilvusProofError, match="milvus_proof_invalid"):
        proof.validate_consumable_proof(path, project_root=root, now=10_011, bridge_sha256="b" * 64)


def test_build_rejects_direct_milvus_route_or_incomplete_receipt(tmp_path: Path) -> None:
    with pytest.raises(proof.MilvusProofError, match="active_policy_direct_database_exposed"):
        _build(tmp_path, direct_milvus=True)

    root = _project(tmp_path / "second")
    policy_value = _policy()
    policy_bytes = json.dumps(policy_value, sort_keys=True).encode()
    receipt = _receipt(proof._sha256(policy_bytes))
    receipt["mutation_routes_denied"] = ["delete"]
    with pytest.raises(proof.MilvusProofError, match="sandbox_receipt_invalid"):
        proof.build_proof(
            project_root=root,
            manifest=_manifest(),
            policy=policy_value,
            policy_bytes=policy_bytes,
            receipt=receipt,
            bridge_sha256="b" * 64,
            now=10_010,
        )


def test_sandbox_probe_reads_only_and_rejects_every_mutation_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def request(path: str, payload=None):
        calls.append((path, payload))
        if path == "/healthz":
            return 200, {
                "ok": True,
                "schema_version": sandbox_probe.BROKER_SCHEMA_VERSION,
                "service": "siq-read-only-data-broker",
                "milvus_operations": list(sandbox_probe.READ_OPERATIONS),
            }
        if path.endswith("/describe"):
            return 200, {
                "ok": True,
                "operation": "describe",
                "description": sandbox_probe.EXPECTED_DESCRIPTION,
            }
        if path.endswith("/query"):
            return 200, {"ok": True, "operation": "query", "results": [{"id": 42}]}
        if path.endswith("/get"):
            return 200, {"ok": True, "operation": "get", "results": [{"id": 42}]}
        if path.endswith("/search"):
            return 200, {"ok": True, "operation": "search", "results": [[]]}
        return 404, {"ok": False, "error_code": "route_not_found"}

    monkeypatch.setattr(sandbox_probe, "_request", request)
    monkeypatch.setattr(sandbox_probe, "_direct_port_denied", lambda: "connect_denied")
    monkeypatch.setattr(sandbox_probe.time, "time", lambda: 10_000)

    result = sandbox_probe.run_probe(
        run_id="task-001",
        sandbox_id="11111111-1111-1111-1111-111111111111",
        container_id="a" * 64,
        policy_sha256="b" * 64,
    )

    assert result["passed"] is True
    assert result["business_rows_modified"] == 0
    assert result["milvus_catalog_sha256"] == proof._sha256(
        proof._canonical(sandbox_probe.EXPECTED_DESCRIPTION)
    )
    assert [path for path, _ in calls if path.startswith("/v1/milvus/")][-len(sandbox_probe.MUTATION_ROUTES) :] == [
        f"/v1/milvus/{operation}" for operation in sandbox_probe.MUTATION_ROUTES
    ]
    assert "42" not in json.dumps(result)


def test_sandbox_probe_uses_only_the_data_broker_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[object] = []
    opener_handlers: list[tuple[object, ...]] = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return b'{"ok":true}'

    class _Opener:
        def open(self, request, *, timeout: int):
            assert timeout == 10
            requests.append(request)
            return _Response()

    def build_opener(*handlers: object) -> _Opener:
        opener_handlers.append(handlers)
        return _Opener()

    monkeypatch.setattr(sandbox_probe.urllib.request, "build_opener", build_opener)
    monkeypatch.setenv("SIQ_OPENSHELL_DATA_IDENTITY_TOKEN", "data-token")
    monkeypatch.setenv("SIQ_OPENSHELL_BROKER_IDENTITY_TOKEN", "legacy-token")

    sandbox_probe._request("/healthz")

    headers = {name.lower(): value for name, value in requests[-1].header_items()}
    assert headers["x-siq-openshell-identity"] == "data-token"

    monkeypatch.delenv("SIQ_OPENSHELL_DATA_IDENTITY_TOKEN")
    sandbox_probe._request("/healthz")
    headers = {name.lower(): value for name, value in requests[-1].header_items()}
    assert "x-siq-openshell-identity" not in headers
    assert opener_handlers == [(), ()]


@pytest.mark.parametrize(
    ("reason", "error_code"),
    [
        (socket.gaierror(socket.EAI_NONAME, "fixture"), "broker_name_resolution_failed"),
        (ConnectionRefusedError(), "broker_connection_refused"),
        (TimeoutError(), "broker_connection_timed_out"),
        (PermissionError(), "broker_policy_denied"),
        (OSError(errno.ENETUNREACH, "fixture"), "broker_route_unreachable"),
    ],
)
def test_sandbox_probe_reports_only_stable_broker_transport_classes(reason: OSError, error_code: str) -> None:
    error = sandbox_probe._broker_transport_error(sandbox_probe.urllib.error.URLError(reason))
    assert str(error) == error_code


def test_boundary_manifest_exposes_the_run_id_consumed_by_the_proof_builder(tmp_path: Path) -> None:
    root = _project(tmp_path)
    adapter = _FakeBoundaryAdapter(root)
    plan = adapter.prepare_security_probe_runtime(adapter.spec, network_mode="data-broker-only")

    manifest = boundary_lifecycle._manifest(plan, "a" * 48)

    assert manifest["run_id"] == adapter.spec.run_id
    assert manifest["probe_id"] == adapter.spec.run_id


def test_collect_receipt_surfaces_only_known_sandbox_probe_error_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    manifest = {
        "sandbox_name": "siq-analysis-security-probe-fixture",
        "run_id": "probe-fixture",
        "sandbox_id": "11111111-1111-1111-1111-111111111111",
        "container_id": "a" * 64,
    }
    monkeypatch.setattr(
        proof,
        "_run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout='{"error_code":"direct_milvus_allowed","ok":false}\n',
            stderr="",
        ),
    )

    with pytest.raises(proof.MilvusProofError, match=r"sandbox_probe_failed\.direct_milvus_allowed"):
        proof._collect_receipt(
            project_root=root,
            manifest=manifest,
            policy_sha256="b" * 64,
            timeout=30,
        )


class _FakeBoundaryAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.deleted = False
        self.spec = SimpleNamespace(
            project_root=root,
            run_id="probe-123456789abc",
            sandbox_name="siq-analysis-security-probe-123456789abc",
            run_dir=root / "var/openshell/siq-analysis/security-probes/probe-123456789abc",
        )

    def require_security_probe_lock(self) -> None:
        return None

    def security_probe_spec(self, **_kwargs):
        return self.spec

    def prepare_security_probe_runtime(self, spec, *, network_mode: str):
        assert spec is self.spec
        assert network_mode == "data-broker-only"
        spec.run_dir.mkdir(parents=True, mode=0o700)
        snapshot = self.root / "var/openshell/siq-analysis/runtime-snapshots" / spec.run_id
        snapshot.mkdir(parents=True, mode=0o700)
        policy_path = spec.run_dir / "task-policy.yaml"
        policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
        policy_path.chmod(0o600)
        mount = spec.run_dir / "mount.json"
        mount.write_text("{}", encoding="utf-8")
        mount.chmod(0o600)
        return SimpleNamespace(
            spec=spec,
            image_ref="siq/hermes-openshell-siq-analysis:" + "b" * 24,
            image_id="sha256:" + "a" * 64,
            runtime_snapshot=snapshot,
            mount_plan=mount,
            mount_plan_sha256=proof._sha256(mount.read_bytes()),
            policy_path=policy_path,
            policy_sha256=proof._sha256(policy_path.read_bytes()),
        )

    def create_security_probe_sandbox(self, **_kwargs) -> SandboxIdentity:
        return SandboxIdentity(
            sandbox_id="11111111-1111-1111-1111-111111111111",
            container_id="a" * 64,
        )

    def delete_security_probe_sandbox(self, **_kwargs) -> None:
        self.deleted = True

    def recover_security_probe_sandbox(self, **_kwargs) -> None:
        self.deleted = True


def test_not_production_boundary_lifecycle_writes_proof_only_after_verified_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    adapter = _FakeBoundaryAdapter(root)
    receipt = _receipt("0" * 64)
    monkeypatch.setattr(boundary_lifecycle, "_issue_data_token", lambda **_kwargs: "fixture-token")
    monkeypatch.setattr(proof, "_collect_receipt", lambda **_kwargs: receipt)
    monkeypatch.setattr(proof, "bridge_binding", lambda: (object(), "b" * 64))
    monkeypatch.setattr(
        proof,
        "build_proof",
        lambda **_kwargs: {"schema_version": proof.SCHEMA_VERSION, "decision": "GO", "passed": True},
    )

    report = boundary_lifecycle.run_boundary_proof(
        project_root=root,
        profile="siq_analysis",
        market="cn",
        company="fixture",
        probe_id="probe-123456789abc",
        timeout=30,
        adapter=adapter,
    )

    assert report["decision"] == "GO"
    assert report["cleanup_verified"] is True
    assert adapter.deleted is True
    assert not adapter.spec.run_dir.exists()
    assert not (root / "var/openshell/siq-analysis/runtime-snapshots/probe-123456789abc").exists()
    assert (root / proof.PROOF_RELATIVE).is_file()


def test_not_production_boundary_lifecycle_cleans_up_and_emits_no_proof_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    adapter = _FakeBoundaryAdapter(root)
    monkeypatch.setattr(boundary_lifecycle, "_issue_data_token", lambda **_kwargs: "fixture-token")
    monkeypatch.setattr(proof, "_collect_receipt", lambda **_kwargs: _receipt("0" * 64))
    monkeypatch.setattr(proof, "bridge_binding", lambda: (object(), "b" * 64))
    monkeypatch.setattr(
        proof,
        "build_proof",
        lambda **_kwargs: (_ for _ in ()).throw(proof.MilvusProofError("fixture_failure")),
    )

    with pytest.raises(boundary_lifecycle.BoundaryLifecycleError, match="fixture_failure"):
        boundary_lifecycle.run_boundary_proof(
            project_root=root,
            profile="siq_analysis",
            market="cn",
            company="fixture",
            probe_id="probe-123456789abc",
            timeout=30,
            adapter=adapter,
        )

    assert adapter.deleted is True
    assert not adapter.spec.run_dir.exists()
    assert not (root / proof.PROOF_RELATIVE).exists()


def test_not_production_boundary_lifecycle_cleans_prepared_state_when_identity_issue_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    adapter = _FakeBoundaryAdapter(root)
    monkeypatch.setattr(
        boundary_lifecycle,
        "_issue_data_token",
        lambda **_kwargs: (_ for _ in ()).throw(boundary_lifecycle.BoundaryLifecycleError("identity_failed")),
    )

    with pytest.raises(boundary_lifecycle.BoundaryLifecycleError, match="identity_failed"):
        boundary_lifecycle.run_boundary_proof(
            project_root=root,
            profile="siq_analysis",
            market="cn",
            company="fixture",
            probe_id="probe-123456789abc",
            timeout=30,
            adapter=adapter,
        )

    assert adapter.deleted is False
    assert not adapter.spec.run_dir.exists()
    assert not (root / "var/openshell/siq-analysis/runtime-snapshots/probe-123456789abc").exists()
