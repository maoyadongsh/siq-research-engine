from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/openshell/configure_bind_mount_contract.sh"
PREPARE = ROOT / "scripts/openshell/prepare_gateway.sh"
TEMPLATE = ROOT / "infra/openshell/gateway/siq-openshell-dev.toml.template"


def test_gateway_template_requires_renderer_owned_bind_configuration() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    assert template.count("${SIQ_OPENSHELL_BIND_MOUNT_CONFIG}") == 1
    assert "enable_bind_mounts = true" not in template
    assert "bind_mount_contract =" not in template
    assert "bind_mount_project_root =" not in template


def test_prepare_refuses_unowned_transactions_and_verifies_activation() -> None:
    prepare = PREPARE.read_text(encoding="utf-8")

    assert "Incomplete or foreign bind-contract transaction requires recovery" in prepare
    assert "SIQ_OPENSHELL_BIND_TRANSACTION_ID" in prepare
    assert "SIQ_OPENSHELL_MAINTENANCE_FD" in prepare
    assert "gateway_bind_contract.py" in prepare
    assert "verify-activation" in prepare
    assert prepare.index("verify-activation") < prepare.index("render_gateway_config.py")


def test_activation_has_durable_phases_and_verified_rollback() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    for phase in (
        "prepared",
        "gateway_stopped",
        "config_switched",
        "gateway_started",
        "committed",
        "rollback_incomplete",
    ):
        assert phase in script
    assert "fsync_regular_file" in script
    assert "fsync_directory" in script
    assert "restore_previous_state" in script
    assert "Bind-contract change failed; restoring" in script
    prepared = script.index("write_journal prepared")
    config_switched = script.index("write_journal config_switched", prepared)
    assert prepared < script.index("stop_gateway.sh", prepared)
    assert config_switched < script.index("start_gateway.sh", config_switched)


def test_activation_is_scoped_to_isolated_gateway_and_empty_inventory() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'GATEWAY_NAME="siq-openshell-dev"' in script
    assert "assert_gateway_inventory_empty" in script
    assert "assert_no_managed_sandbox_containers" in script
    assert "openshell.ai/sandbox-namespace=siq-openshell-dev" in script
    assert "18789" in script
    assert "nemoclaw" not in script.lower()
    assert "kill -9" not in script
    assert "docker rm" not in script


def test_activation_requires_patched_runtime_before_first_mutation() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    verify = script.index('verify-runtime >/dev/null 2>&1')
    legacy_verify = script.index('verify_legacy_active_contract >/dev/null', verify)
    prepared = script.index("write_journal prepared")
    stopped = script.index('"$SCRIPT_DIR/stop_gateway.sh"', prepared)
    assert verify < legacy_verify < prepared < stopped
    assert "--require-active-legacy-contract" in script
