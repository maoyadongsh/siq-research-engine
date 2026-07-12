import importlib.util
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "run_report_finder_egress_smoke.py"
    spec = importlib.util.spec_from_file_location("report_finder_egress_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def complete_report():
    return {
        "status": "passed",
        "production_compose_modified": False,
        "production_compose_network_audit": {
            "cap_drop_all": True,
            "explicit_egress_proxy": False,
            "explicit_internal_network": False,
            "infrastructure_egress_policy_proven": False,
        },
        "all_networks_internal": True,
        "runner_read_only": True,
        "runner_capabilities_dropped": True,
        "runner_no_new_privileges": True,
        "cleanup_complete": True,
        "official_allowlist": {
            "status_code": 200,
            "connect_attempts": 1,
            "connected_ip": "93.184.216.10",
            "body_verified": True,
            "host_header": "www.sec.gov:18080",
            "policy_validated": True,
        },
        "blocked_destinations": {
            name: {"blocked_before_connect": True, "trap_hits": 0}
            for name in ("private", "link_local", "metadata", "loopback")
        },
        "redirect_to_metadata": {
            "initial_redirect_observed": True,
            "blocked_before_second_connect": True,
            "metadata_trap_hits": 0,
        },
        "dns_rebind": {"blocked_before_connect": True, "official_stub_hits": 0},
        "dns_observations": [
            {"name": "private.sec.gov", "address": "10.77.0.10", "qtype": 1},
            {"name": "linklocal.sec.gov", "address": "169.254.240.10", "qtype": 1},
            {"name": "metadata.sec.gov", "address": "169.254.169.254", "qtype": 1},
            {"name": "loopback.sec.gov", "address": "127.0.0.1", "qtype": 1},
            {"name": "rebind.sec.gov", "address": "93.184.216.10", "qtype": 1},
            {"name": "rebind.sec.gov", "address": "127.0.0.1", "qtype": 1},
        ],
    }


def test_contract_accepts_complete_container_network_proof():
    load_module().assert_smoke_contract(complete_report())


@pytest.mark.parametrize("name", ["private", "link_local", "metadata", "loopback"])
def test_contract_rejects_any_forbidden_destination_connect(name):
    module = load_module()
    report = complete_report()
    report["blocked_destinations"][name]["blocked_before_connect"] = False

    with pytest.raises(AssertionError, match=name):
        module.assert_smoke_contract(report)


def test_contract_rejects_redirect_metadata_trap_hit():
    module = load_module()
    report = complete_report()
    report["redirect_to_metadata"]["metadata_trap_hits"] = 1

    with pytest.raises(AssertionError, match="redirect"):
        module.assert_smoke_contract(report)


def test_contract_rejects_rebind_connection_attempt():
    module = load_module()
    report = complete_report()
    report["dns_rebind"]["blocked_before_connect"] = False

    with pytest.raises(AssertionError, match="rebind"):
        module.assert_smoke_contract(report)


def test_smoke_uses_disposable_internal_networks_without_editing_compose():
    module = load_module()
    source = Path(module.__file__).read_text(encoding="utf-8")
    compose = (Path(module.__file__).resolve().parents[2] / "infra" / "docker" / "docker-compose.yml")

    assert '"docker", "network", "create", "--internal"' in source
    assert '"docker", "network", "rm"' in source
    assert '"--read-only"' in source
    assert '"--cap-drop", "ALL"' in source
    assert '"no-new-privileges:true"' in source
    assert module.COMPOSE_FILE == compose


def test_compose_audit_does_not_overclaim_infrastructure_egress_policy():
    module = load_module()
    audit = module.production_compose_network_audit(
        """
services:
  report-finder:
    image: report-finder
    cap_drop:
      - ALL
    environment:
      - SEC_USER_AGENT=SIQ
  api:
    image: api
"""
    )

    assert audit == {
        "cap_drop_all": True,
        "explicit_egress_proxy": False,
        "explicit_internal_network": False,
        "infrastructure_egress_policy_proven": False,
    }
