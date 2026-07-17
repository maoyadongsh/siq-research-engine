from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.openshell import siq_analysis_wide_pilot as wide  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import CommandResult  # noqa: E402

SCRIPTS = ROOT / "scripts/openshell"


def test_wide_pilot_is_separate_and_cannot_be_formal_readiness_evidence() -> None:
    assert wide.MODE == "NOT_PRODUCTION_WIDE_PILOT"
    assert wide.READINESS_EFFECT == "none"
    assert wide.STATE_RELATIVE == Path("var/openshell/poc/siq-analysis-wide")
    assert wide.LIFECYCLE_LABEL == "siq-analysis-wide-pilot-not-production-v1"
    assert wide.ACKNOWLEDGEMENT == "--acknowledge-not-production-wide-pilot"
    assert set(wide.PROVIDERS) == {
        "siq-minimax-cn-pool",
        "siq-stepfun",
        "siq-kimi-coding",
        "siq-tavily-search",
    }
    assert "siq-exa-search_not_configured" in wide.KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED
    assert "clash_fake_ip_egress_guard_compatibility_unresolved" in wide.KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED


def test_start_uses_formal_assets_but_an_output_leaf_policy_and_subset_providers() -> None:
    source = (SCRIPTS / "siq_analysis_wide_pilot.py").read_text(encoding="utf-8")

    assert "validate_security_probe_prerequisites" in source
    assert "snapshot_siq_analysis_runtime.snapshot_runtime" in source
    assert "build_siq_analysis_mount_plan.compile_mount_plan" in source
    assert '"--writable-path",\n                str(paths.output_root)' in source
    assert 'str(spec.analysis_root) in filesystem.get("read_write", [])' in source
    assert "broker_request_identity.issue_broker_identities" in source
    assert "_sandbox_entrypoint_env_arguments(self.project_root)" in source
    assert "SIQ_REQUIRE_OPENSHELL_PROVIDERS=0" in source
    assert 'for provider in PROVIDERS:' in source
    assert '"siq-exa-search",' not in source
    assert "siq_analysis_transaction" not in source


def test_pilot_reuses_verified_sandbox_forward_guard_and_host_receipt() -> None:
    lifecycle = (SCRIPTS / "siq_analysis_wide_pilot.py").read_text(encoding="utf-8")
    guard = (SCRIPTS / "siq_analysis_wide_pilot_guard.py").read_text(encoding="utf-8")

    assert "verify_sandbox_identity" in lifecycle
    assert "_delete_verified_sandbox" in lifecycle
    assert "_forward_arguments" in lifecycle
    assert "DestructiveActionGuard" in guard
    assert "PilotTerminator" in guard
    assert "_delete_verified_sandbox" in guard
    assert "_stable_host_receipt" in lifecycle
    assert "host_hermes_receipt_sha256" in lifecycle
    assert "result_is_formal_evidence" in lifecycle


def test_probe_checks_real_tavily_without_using_generic_egress_get_as_a_gate() -> None:
    source = (SCRIPTS / "siq_analysis_wide_pilot.py").read_text(encoding="utf-8")

    assert "TavilyWebSearchProvider().search" in source
    assert "SIQ_TAVILY_PROVIDER_PROBE" in source
    assert '"tavily_provider_status": "passed"' in source
    assert "EgressGuard" not in source
    assert "ssrf_non_public_ip" not in source


def test_wrappers_keep_host_runtime_unchanged_and_require_explicit_ack() -> None:
    start = (SCRIPTS / "start_siq_analysis_wide_pilot.sh").read_text(encoding="utf-8")
    runner = (SCRIPTS / "run_siq_analysis_wide_pilot_lifecycle.sh").read_text(encoding="utf-8")
    smoke = (SCRIPTS / "smoke_siq_analysis_wide_pilot.sh").read_text(encoding="utf-8")

    assert "run_siq_analysis_wide_pilot_lifecycle.sh" in start
    assert "siq_analysis_wide_pilot.py" in runner
    assert "siq_openshell_acquire_maintenance_lock" in runner
    assert "start_all.sh" not in start + runner + smoke
    assert "stop_hermes_gateway.sh" not in start + runner + smoke
    assert "rollback_to_host.sh" not in start + runner + smoke
    assert "test_siq_analysis_wide_pilot_contract.py" in smoke


def test_command_json_never_accepts_nonzero_or_non_object_payload() -> None:
    with pytest.raises(wide.WidePilotError, match="probe_failed"):
        wide._command_json(CommandResult(1, "{}", ""), code="probe_failed")
    with pytest.raises(wide.WidePilotError, match="probe_failed"):
        wide._command_json(CommandResult(0, json.dumps([]), ""), code="probe_failed")
    assert wide._command_json(CommandResult(0, '{"ok":true}', ""), code="probe_failed") == {"ok": True}
