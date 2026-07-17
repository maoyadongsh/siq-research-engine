from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.openshell import provision_siq_providers as providers  # noqa: E402

EXPECTED_ROUTES = {
    "siq-minimax-cn-pool": {
        ("api.minimax.chat", "GET", "/v1/models"),
        ("api.minimax.chat", "POST", "/v1/messages"),
        ("api.minimax.chat", "POST", "/v1/v1/messages"),
        ("api.minimax.chat", "POST", "/v1/chat/completions"),
    },
    "siq-stepfun": {
        ("api.stepfun.com", "GET", "/v1/models"),
        ("api.stepfun.com", "POST", "/v1/chat/completions"),
    },
    "siq-kimi-coding": {
        ("api.kimi.com", "GET", "/coding/v1/models"),
        ("api.kimi.com", "POST", "/coding/v1/messages"),
        ("api.kimi.com", "POST", "/coding/v1/chat/completions"),
    },
    "siq-tavily-search": {
        ("api.tavily.com", "POST", "/search"),
        ("api.tavily.com", "POST", "/extract"),
        ("api.tavily.com", "POST", "/crawl"),
        ("api.tavily.com", "POST", "/map"),
        ("api.tavily.com", "POST", "/research"),
        ("api.tavily.com", "GET", "/research/**"),
    },
    "siq-exa-search": {
        ("api.exa.ai", "POST", "/search"),
        ("api.exa.ai", "POST", "/contents"),
        ("api.exa.ai", "POST", "/answer"),
        ("api.exa.ai", "POST", "/context"),
        ("api.exa.ai", "POST", "/agent/runs"),
        ("api.exa.ai", "GET", "/agent/runs/**"),
    },
}


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def run(self, arguments, *, credential_env=None):
        self.calls.append((list(arguments), dict(credential_env or {})))
        return providers.CliResult(0)


class ContractRunner:
    def __init__(
        self,
        *,
        providers_v2: str = "true",
        endpoint: str = providers.EXPECTED_GATEWAY_ENDPOINT,
    ) -> None:
        self.providers_v2 = providers_v2
        self.endpoint = endpoint
        self.calls: list[list[str]] = []

    def run(self, arguments, *, credential_env=None):
        assert not credential_env
        arguments = list(arguments)
        self.calls.append(arguments)
        if arguments == ["--version"]:
            return providers.CliResult(0, "openshell 0.0.83\n")
        if arguments == ["gateway", "list", "-o", "json"]:
            return providers.CliResult(
                0,
                json.dumps(
                    [
                        {
                            "active": True,
                            "auth": "mtls",
                            "endpoint": self.endpoint,
                            "name": "siq-openshell-dev",
                            "source": "user",
                            "type": "local",
                        }
                    ]
                ),
            )
        if arguments == ["settings", "get", "--global", "--json"]:
            return providers.CliResult(
                0,
                json.dumps(
                    {
                        "scope": "global",
                        "settings": {"providers_v2_enabled": self.providers_v2},
                    }
                ),
            )
        raise AssertionError(f"unexpected command: {arguments}")


class ProfileRunner:
    def __init__(self, existing_profile_ids: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.existing_profile_ids = existing_profile_ids

    def run(self, arguments, *, credential_env=None):
        assert not credential_env
        arguments = list(arguments)
        self.calls.append(arguments)
        if arguments[:2] == ["provider", "list-profiles"]:
            return providers.CliResult(
                0,
                json.dumps([{"id": profile_id} for profile_id in self.existing_profile_ids]),
            )
        return providers.CliResult(0)


def _routes(profile: dict) -> set[tuple[str, str, str]]:
    return {
        (endpoint["host"], rule["allow"]["method"], rule["allow"]["path"])
        for endpoint in profile["endpoints"]
        for rule in endpoint["rules"]
    }


def test_assets_validate_and_pin_exact_routes() -> None:
    plan = providers.load_plan()

    assert plan.provider_names == list(EXPECTED_ROUTES)
    assert re.fullmatch(r"[0-9a-f]{64}", plan.summary_sha256)
    assert plan.manifest["gateway_endpoint"] == "https://127.0.0.1:17671"
    assert plan.manifest["gateway_type"] == "local"
    assert plan.manifest["gateway_auth"] == "mtls"
    for spec in plan.specs:
        profile = plan.profiles[spec.profile_id]
        assert _routes(profile) == EXPECTED_ROUTES[spec.name]
        assert profile["binaries"] == [providers.PYTHON_BINARY]
        assert all(endpoint["protocol"] == "rest" for endpoint in profile["endpoints"])
        assert all(endpoint["enforcement"] == "enforce" for endpoint in profile["endpoints"])

    tavily = plan.profiles["siq-tavily-search"]
    assert tavily["endpoints"][0]["request_body_credential_rewrite"] is True
    assert plan.manifest["request_body_credential_rewrite_max_bytes"] == 262_144
    for profile_id, profile in plan.profiles.items():
        if profile_id == "siq-tavily-search":
            continue
        assert not any(endpoint.get("request_body_credential_rewrite", False) for endpoint in profile["endpoints"])


@pytest.mark.parametrize("provider_name", ["siq-tavily-search", "siq-exa-search"])
def test_search_profiles_reject_host_wide_or_unreviewed_routes(provider_name: str) -> None:
    plan = providers.load_plan([provider_name])
    spec = plan.specs[0]
    profile = copy.deepcopy(plan.profiles[spec.profile_id])
    profile["endpoints"][0]["rules"].append({"allow": {"method": "POST", "path": "/**"}})

    with pytest.raises(providers.ProvisionError, match="reviewed retrieval-only contract"):
        providers._validate_profile(profile, spec)


def test_search_profile_rejects_non_prefix_wildcard() -> None:
    plan = providers.load_plan(["siq-exa-search"])
    spec = plan.specs[0]
    profile = copy.deepcopy(plan.profiles[spec.profile_id])
    profile["endpoints"][0]["rules"][-1]["allow"]["path"] = "/agent/*/events"

    with pytest.raises(providers.ProvisionError, match="reviewed prefix form"):
        providers._validate_profile(profile, spec)


def test_minimax_template_preserves_two_secret_free_placeholders() -> None:
    plan = providers.load_plan(["siq-minimax-cn-pool"])
    entries = plan.hermes_auth_template["credential_pool"]["minimax-cn"]

    assert [entry["priority"] for entry in entries] == [0, 10]
    assert [entry["id"] for entry in entries] == [
        "minimax_cn_primary_0",
        "minimax_cn_backup_10",
    ]
    assert [entry["access_token"] for entry in entries] == [
        "openshell:resolve:env:SIQ_MINIMAX_CN_PRIMARY",
        "openshell:resolve:env:SIQ_MINIMAX_CN_BACKUP",
    ]
    assert {entry["base_url"] for entry in entries} == {"https://api.minimax.chat/v1"}


def test_provider_assets_do_not_contain_token_shaped_values() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(providers.PROVIDER_ROOT.rglob("*")) if path.is_file()
    )

    assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", combined)
    assert not re.search(r"\btvly-[A-Za-z0-9_-]{16,}\b", combined)
    assert "KEY=VALUE" not in combined


def test_default_dry_run_does_not_read_or_print_environment_secrets() -> None:
    canary = "dry-run-canary-value-that-must-not-appear"
    environment = os.environ.copy()
    environment.update(
        {
            "SIQ_MINIMAX_CN_PRIMARY": canary,
            "SIQ_MINIMAX_CN_BACKUP": canary,
            "SIQ_STEPFUN_LLM_API_KEY": canary,
            "KIMI_API_KEY": canary,
            "TAVILY_API_KEY": canary,
            "EXA_API_KEY": canary,
        }
    )
    result = subprocess.run(
        [sys.executable, str(providers.__file__)],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert canary not in result.stdout
    assert canary not in result.stderr
    output = json.loads(result.stdout)
    assert set(output) == {"providers", "summary_sha256"}
    assert output["providers"] == list(EXPECTED_ROUTES)
    assert re.fullmatch(r"[0-9a-f]{64}", output["summary_sha256"])


def test_secret_dotenv_requires_owner_only_mode(tmp_path: Path) -> None:
    plan = providers.load_plan(["siq-stepfun"])
    source = tmp_path / "provider.env"
    source.write_text("SIQ_STEPFUN_LLM_API_KEY=test-stepfun-value\n", encoding="utf-8")
    source.chmod(0o644)

    with pytest.raises(providers.ProvisionError, match="security validation"):
        providers.load_secrets(
            plan,
            secret_files=[source],
            minimax_auth_json=None,
            environment={},
        )

    source.chmod(0o600)
    loaded = providers.load_secrets(
        plan,
        secret_files=[source],
        minimax_auth_json=None,
        environment={},
    )
    assert set(loaded) == {"SIQ_STEPFUN_LLM_API_KEY"}


def test_minimax_auth_source_maps_exact_reviewed_pool(tmp_path: Path) -> None:
    plan = providers.load_plan(["siq-minimax-cn-pool"])
    source = tmp_path / "auth.json"
    source.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "minimax-cn": [
                        {
                            "id": "minimax_cn_primary_0",
                            "priority": 0,
                            "base_url": "https://api.minimax.chat/v1",
                            "access_token": "test-primary-value",
                        },
                        {
                            "id": "minimax_cn_backup_10",
                            "priority": 10,
                            "base_url": "https://api.minimax.chat/v1",
                            "access_token": "test-backup-value",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    source.chmod(0o600)

    loaded = providers.load_secrets(
        plan,
        secret_files=[],
        minimax_auth_json=source,
        environment={},
    )
    assert set(loaded) == {
        "SIQ_MINIMAX_CN_PRIMARY",
        "SIQ_MINIMAX_CN_BACKUP",
    }


def test_minimax_pool_rejects_identical_environment_tokens() -> None:
    plan = providers.load_plan(["siq-minimax-cn-pool"])

    with pytest.raises(providers.ProvisionError, match="must be distinct"):
        providers.load_secrets(
            plan,
            secret_files=[],
            minimax_auth_json=None,
            environment={
                "SIQ_MINIMAX_CN_PRIMARY": "same-test-value",
                "SIQ_MINIMAX_CN_BACKUP": "same-test-value",
            },
        )


def test_minimax_pool_rejects_identical_auth_json_tokens(tmp_path: Path) -> None:
    plan = providers.load_plan(["siq-minimax-cn-pool"])
    source = tmp_path / "auth.json"
    source.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "minimax-cn": [
                        {
                            "id": "minimax_cn_primary_0",
                            "priority": 0,
                            "base_url": "https://api.minimax.chat/v1",
                            "access_token": "same-test-value",
                        },
                        {
                            "id": "minimax_cn_backup_10",
                            "priority": 10,
                            "base_url": "https://api.minimax.chat/v1",
                            "access_token": "same-test-value",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    source.chmod(0o600)

    with pytest.raises(providers.ProvisionError, match="must be distinct"):
        providers.load_secrets(
            plan,
            secret_files=[],
            minimax_auth_json=source,
            environment={},
        )


def test_credential_commands_use_bare_keys_and_scoped_child_env() -> None:
    plan = providers.load_plan(["siq-minimax-cn-pool", "siq-stepfun"])
    minimax, stepfun = plan.specs
    secret_values = {
        "SIQ_MINIMAX_CN_PRIMARY": "test-primary-value",
        "SIQ_MINIMAX_CN_BACKUP": "test-backup-value",
        "SIQ_STEPFUN_LLM_API_KEY": "test-stepfun-value",
    }
    runner = RecordingRunner()

    providers.apply_provider_actions(
        [("create", minimax), ("update", stepfun)],
        secret_values,
        runner,
    )

    assert runner.calls[0][0] == [
        "provider",
        "create",
        "--name",
        "siq-minimax-cn-pool",
        "--type",
        "siq-minimax-cn",
        "--credential",
        "SIQ_MINIMAX_CN_PRIMARY",
        "--credential",
        "SIQ_MINIMAX_CN_BACKUP",
    ]
    assert set(runner.calls[0][1]) == {
        "SIQ_MINIMAX_CN_PRIMARY",
        "SIQ_MINIMAX_CN_BACKUP",
    }
    assert runner.calls[1][0] == [
        "provider",
        "update",
        "siq-stepfun",
        "--credential",
        "SIQ_STEPFUN_LLM_API_KEY",
    ]
    assert set(runner.calls[1][1]) == {"SIQ_STEPFUN_LLM_API_KEY"}
    flattened_argv = "\n".join(argument for call, _ in runner.calls for argument in call)
    assert all(value not in flattened_argv for value in secret_values.values())
    assert all("=" not in argument for call, _ in runner.calls for argument in call)


def test_child_environment_does_not_inherit_unreviewed_host_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNRELATED_HOST_SECRET", "must-not-cross-process-boundary")

    child = providers._minimal_child_environment({"KIMI_API_KEY": "test-kimi-value"})

    assert child["KIMI_API_KEY"] == "test-kimi-value"
    assert "UNRELATED_HOST_SECRET" not in child
    assert "PYTHONPATH" not in child
    assert "LD_PRELOAD" not in child
    assert "BASH_ENV" not in child


def test_cli_preflight_uses_pinned_version_and_requires_provider_v2() -> None:
    runner = ContractRunner(providers_v2="true")

    providers._require_cli_contract(runner)

    assert runner.calls == [
        ["--version"],
        ["gateway", "list", "-o", "json"],
        ["settings", "get", "--global", "--json"],
    ]

    disabled = ContractRunner(providers_v2="<unset>")
    with pytest.raises(providers.ProvisionError, match="providers_v2_enabled"):
        providers._require_cli_contract(disabled)

    redirected = ContractRunner(endpoint="https://127.0.0.1:19999")
    with pytest.raises(providers.ProvisionError, match="pinned local mTLS target"):
        providers._require_cli_contract(redirected)
    assert redirected.calls == [
        ["--version"],
        ["gateway", "list", "-o", "json"],
    ]


def test_quiescence_check_rejects_any_sandbox_before_mutation() -> None:
    class SandboxRunner:
        def __init__(self, sandboxes: list[dict]) -> None:
            self.sandboxes = sandboxes
            self.calls: list[list[str]] = []

        def run(self, arguments, *, credential_env=None):
            assert not credential_env
            self.calls.append(list(arguments))
            return providers.CliResult(0, json.dumps(self.sandboxes))

    empty = SandboxRunner([])
    providers._require_quiescent_gateway(empty)
    assert empty.calls == [["sandbox", "list", "--limit", "1", "-o", "json"]]

    active = SandboxRunner([{"name": "siq-analysis", "phase": "ready"}])
    with pytest.raises(providers.ProvisionError, match="no sandboxes"):
        providers._require_quiescent_gateway(active)


def test_provider_preflight_rejects_unreviewed_keys_before_update() -> None:
    plan = providers.load_plan(["siq-stepfun"])

    class ProviderRunner:
        def __init__(self, provider_state: dict) -> None:
            self.provider_state = provider_state

        def run(self, arguments, *, credential_env=None):
            assert list(arguments) == [
                "provider",
                "list",
                "--limit",
                "1000",
                "-o",
                "json",
            ]
            assert not credential_env
            return providers.CliResult(0, json.dumps([self.provider_state]))

    base = {
        "name": "siq-stepfun",
        "type": "siq-stepfun",
        "credential_keys": ["SIQ_STEPFUN_LLM_API_KEY"],
    }
    with pytest.raises(providers.ProvisionError, match="unreviewed credential keys"):
        providers._provider_actions(
            plan,
            ProviderRunner({**base, "credential_keys": [*base["credential_keys"], "UNREVIEWED_KEY"]}),
        )
    with pytest.raises(providers.ProvisionError, match="unreviewed config keys"):
        providers._provider_actions(
            plan,
            ProviderRunner({**base, "config_keys": ["unreviewed_config"]}),
        )


def test_provisioning_maintenance_lock_blocks_concurrent_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "var" / "openshell"
    state_root.mkdir(parents=True, mode=0o700)
    state_root.chmod(0o700)
    lock_path = state_root / "locks" / "maintenance.lock"
    monkeypatch.setattr(providers, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(providers, "MAINTENANCE_LOCK_PATH", lock_path)

    with providers._maintenance_lock():
        with pytest.raises(providers.ProvisionError, match="in progress"):
            with providers._maintenance_lock():
                pass

    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_missing_profile_uses_reviewed_lint_and_import_syntax() -> None:
    plan = providers.load_plan(["siq-stepfun"])
    runner = ProfileRunner()

    actions = providers._profile_actions(plan, runner)
    providers._apply_profile_actions(plan, actions, runner)

    assert runner.calls == [
        ["provider", "list-profiles", "-o", "json"],
        [
            "provider",
            "profile",
            "lint",
            "--file",
            str(plan.specs[0].profile_path),
        ],
        [
            "provider",
            "profile",
            "import",
            "--file",
            str(plan.specs[0].profile_path),
        ],
    ]


def test_partial_profile_import_ignores_unselected_registered_profiles() -> None:
    plan = providers.load_plan(["siq-tavily-search"])
    runner = ProfileRunner(existing_profile_ids=("siq-minimax-cn", "siq-stepfun", "siq-kimi-coding"))

    actions = providers._profile_actions(plan, runner)
    providers._apply_profile_actions(plan, actions, runner)

    selected = plan.specs[0]
    assert actions == [("import", selected, 0)]
    assert runner.calls == [
        ["provider", "list-profiles", "-o", "json"],
        ["provider", "profile", "lint", "--file", str(selected.profile_path)],
        ["provider", "profile", "import", "--file", str(selected.profile_path)],
    ]


def test_failed_child_output_is_suppressed_from_operator_error() -> None:
    canary = "child-secret-output-must-not-escape"

    class FailingRunner:
        def run(self, arguments, *, credential_env=None):
            return providers.CliResult(1, canary, canary)

    with pytest.raises(providers.ProvisionError) as captured:
        providers._checked(
            FailingRunner(),
            ["provider", "list"],
            operation="provider list",
        )

    assert canary not in str(captured.value)


def test_installed_cli_exposes_reviewed_0_0_83_syntax_when_available() -> None:
    binary = ROOT / "var" / "openshell" / "toolchains" / "v0.0.83" / "bin" / "openshell"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip("project-local OpenShell 0.0.83 toolchain is not installed")

    commands = {
        ("--version",): ("openshell 0.0.83",),
        ("gateway", "list", "--help"): ("--output", "json"),
        ("sandbox", "list", "--help"): ("--limit", "--output", "json"),
        ("provider", "profile", "lint", "--help"): ("--file", "--from"),
        ("provider", "profile", "import", "--help"): ("--file", "--from"),
        ("provider", "create", "--help"): (
            "--name",
            "--type",
            "--credential <KEY[=VALUE]>",
        ),
        ("provider", "update", "--help"): (
            "<NAME>",
            "--credential <KEY[=VALUE]>",
        ),
    }
    for arguments, expected_fragments in commands.items():
        result = subprocess.run(
            [str(providers.CLI_PATH), *arguments],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert all(fragment in combined for fragment in expected_fragments)
