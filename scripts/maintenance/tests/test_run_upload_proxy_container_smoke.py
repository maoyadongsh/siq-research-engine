import importlib.util
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "run_upload_proxy_container_smoke.py"
    spec = importlib.util.spec_from_file_location("run_upload_proxy_container_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def complete_report():
    return {
        "status": "passed",
        "evidence_boundary": "local_disposable_container",
        "production_data_touched": False,
        "production_compose_modified": False,
        "busy_response": {"status_code": 503, "retry_after": "1"},
        "slow_responses": [{"status_code": 502}, {"status_code": 502}],
        "recovery_response": {"status_code": 200},
        "proxy_metrics": {
            "active": 0,
            "max_active": 2,
            "limit": 2,
            "busy_rejections": 1,
            "upstream_timeouts": 2,
            "rolled_to_disk": 2,
            "buffered_files": 3,
            "closed_files": 3,
        },
        "container_memory": {"peak_delta_bytes": 24, "allowed_peak_delta_bytes": 64},
        "proxy_container_security": {
            "read_only": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
        },
        "cleanup_complete": True,
        "not_proven": ["external_production_ingress_behavior"],
    }


def test_contract_accepts_complete_local_container_proof():
    load_module().assert_smoke_contract(complete_report())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("busy_response", {"status_code": 503, "retry_after": None}, "Retry-After"),
        ("recovery_response", {"status_code": 502}, "released"),
        ("cleanup_complete", False, "cleaned"),
        ("evidence_boundary", "production", "boundary"),
    ],
)
def test_contract_rejects_incomplete_proof(field, value, message):
    module = load_module()
    report = complete_report()
    report[field] = value
    with pytest.raises(AssertionError, match=message):
        module.assert_smoke_contract(report)


def test_contract_rejects_memory_budget_overrun():
    module = load_module()
    report = complete_report()
    report["container_memory"]["peak_delta_bytes"] = 65
    with pytest.raises(AssertionError, match="memory"):
        module.assert_smoke_contract(report)


def test_runner_uses_disposable_isolated_containers():
    module = load_module()
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert '"docker", "network", "create", "--subnet"' in source
    assert '"--read-only"' in source
    assert '"--cap-drop", "ALL"' in source
    assert '"no-new-privileges:true"' in source
    assert '"docker", "rm", "--force"' in source
    assert module.SUPPORT_FILE.name == "upload_proxy_container_smoke_support.py"
