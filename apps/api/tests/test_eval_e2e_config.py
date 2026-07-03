import importlib.util
import sys
import types
from pathlib import Path


def _load_eval_e2e_module(tmp_name: str = "temp_eval_e2e_config"):
    routers_module = types.ModuleType("routers")
    workflow_module = types.ModuleType("routers.workflow")
    routers_module.workflow = workflow_module
    sys.modules.setdefault("routers", routers_module)
    sys.modules.setdefault("routers.workflow", workflow_module)

    services_module = types.ModuleType("services")
    hermes_module = types.ModuleType("services.hermes_client")
    hermes_module.collect_run_result = None
    hermes_module.create_run = None
    path_config_module = types.ModuleType("services.path_config")
    path_config_module.REPORT_DOWNLOADS_ROOT = Path("/tmp/siq-test-downloads")
    path_config_module.WIKI_ROOT = Path("/tmp/siq-test-wiki")
    sys.modules.setdefault("services", services_module)
    sys.modules.setdefault("services.hermes_client", hermes_module)
    sys.modules.setdefault("services.path_config", path_config_module)

    source = Path(__file__).resolve().parents[1] / "routers" / "eval_e2e.py"
    spec = importlib.util.spec_from_file_location(tmp_name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_e2e_default_profile_keeps_automotive_focus():
    eval_e2e = _load_eval_e2e_module("temp_eval_e2e_default_profile")
    profile = eval_e2e._industry_profile()
    focus = eval_e2e._task_focus("请分析行业经营问题", profile)

    assert profile.key == "automotive"
    assert "汽车行业价格竞争" in focus["checklist"]
    assert "新能源转型" in focus["checklist"]


def test_eval_e2e_generic_profile_does_not_force_auto_tone():
    eval_e2e = _load_eval_e2e_module("temp_eval_e2e_generic_profile")
    profile = eval_e2e._industry_profile("generic")
    target = {"company_name": "示例科技", "company_code": "000001", "year": 2025}
    snapshot = {}

    focus = eval_e2e._task_focus("请分析行业经营问题和业务结构", profile)
    output = "\n".join(
        [
            eval_e2e._compose_industry_insight(target, snapshot, profile),
            eval_e2e._compose_risk_tracking_section(snapshot, profile),
            eval_e2e._compose_formal_report_section(target, snapshot, profile),
        ]
    )

    assert profile.key == "generic"
    assert "行业竞争格局" in focus["checklist"]
    assert "业务结构" in output
    assert "新能源" not in output
    assert "价格战" not in output
    assert "汽车行业" not in output


def test_eval_e2e_request_profile_accepts_input_aliases():
    eval_e2e = _load_eval_e2e_module("temp_eval_e2e_profile_aliases")

    assert eval_e2e._request_industry_profile(
        eval_e2e.EvalE2ERequest(input={"profile": "general"})
    ).key == "generic"
    assert eval_e2e._request_industry_profile(
        eval_e2e.EvalE2ERequest(input={"industry_profile": "car"})
    ).key == "automotive"


def test_eval_e2e_request_profile_prefers_top_level_and_falls_back_to_default():
    eval_e2e = _load_eval_e2e_module("temp_eval_e2e_profile_precedence")

    assert eval_e2e._request_industry_profile(
        eval_e2e.EvalE2ERequest(industry_profile="generic", input={"profile": "auto"})
    ).key == "generic"
    assert eval_e2e._request_industry_profile(
        eval_e2e.EvalE2ERequest(industry_profile="unknown")
    ).key == "automotive"
