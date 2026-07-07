import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SPEC = importlib.util.spec_from_file_location("workflow_subprocess_contracts", BACKEND_ROOT / "routers" / "workflow.py")
assert WORKFLOW_SPEC and WORKFLOW_SPEC.loader
workflow = importlib.util.module_from_spec(WORKFLOW_SPEC)
WORKFLOW_SPEC.loader.exec_module(workflow)


def test_generate_obsidian_for_company_runs_expected_command(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    script_path = tmp_path / "wikiset" / "generate_obsidian_graph.py"
    script_path.parent.mkdir()
    script_path.write_text("# test script\n", encoding="utf-8")
    calls = []

    def fake_run_command(args):
        calls.append(args)
        return {"returnCode": 0, "stdout": "generated\n", "stderr": ""}

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "OBSIDIAN_SCRIPT", script_path)
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)

    result = workflow._generate_obsidian_for_company("000001-测试公司")

    assert result == {"returnCode": 0, "stdout": "generated\n", "stderr": ""}
    assert calls == [
        [
            sys.executable,
            str(script_path),
            "--wiki-root",
            str(wiki_root),
            "--company",
            "000001-测试公司",
        ]
    ]


def test_generate_obsidian_for_company_maps_command_failure(monkeypatch, tmp_path):
    script_path = tmp_path / "generate_obsidian_graph.py"
    script_path.write_text("# test script\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "OBSIDIAN_SCRIPT", script_path)
    monkeypatch.setattr(
        workflow,
        "_run_command",
        lambda args: {"returnCode": 2, "stdout": "", "stderr": "bad graph"},
    )

    with pytest.raises(HTTPException) as exc_info:
        workflow._generate_obsidian_for_company("000001-测试公司")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "stage": "obsidian",
        "returnCode": 2,
        "stdout": "",
        "stderr": "bad graph",
    }


def test_repair_and_validate_wiki_naming_runs_repair_then_validate(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    repair_script = tmp_path / "repair_wiki_naming.py"
    validate_script = tmp_path / "validate_wiki_naming.py"
    repair_script.write_text("# repair\n", encoding="utf-8")
    validate_script.write_text("# validate\n", encoding="utf-8")
    calls = []

    def fake_subprocess_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=f" ok {len(calls)} \n", stderr=" warn \n")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "WIKI_NAMING_REPAIR_SCRIPT", repair_script)
    monkeypatch.setattr(workflow, "WIKI_NAMING_VALIDATE_SCRIPT", validate_script)
    monkeypatch.setattr(workflow.subprocess, "run", fake_subprocess_run)

    result = workflow._repair_and_validate_wiki_naming()

    expected_kwargs = {"check": False, "text": True, "capture_output": True}
    assert calls == [
        ([sys.executable, str(repair_script), "--wiki-root", str(wiki_root)], expected_kwargs),
        ([sys.executable, str(validate_script), "--wiki-root", str(wiki_root)], expected_kwargs),
    ]
    assert result == {
        "repair": {"returncode": 0, "stdout": "ok 1", "stderr": "warn"},
        "validation": {"returncode": 0, "stdout": "ok 2", "stderr": "warn"},
    }


def test_repair_and_validate_wiki_naming_maps_validation_failure(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    repair_script = tmp_path / "repair_wiki_naming.py"
    validate_script = tmp_path / "validate_wiki_naming.py"
    repair_script.write_text("# repair\n", encoding="utf-8")
    validate_script.write_text("# validate\n", encoding="utf-8")

    def fake_subprocess_run(args, **kwargs):
        if args[1] == str(repair_script):
            return subprocess.CompletedProcess(args, 0, stdout="repaired\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout='{"issue_count": 1}\n', stderr="invalid names\n")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "WIKI_NAMING_REPAIR_SCRIPT", repair_script)
    monkeypatch.setattr(workflow, "WIKI_NAMING_VALIDATE_SCRIPT", validate_script)
    monkeypatch.setattr(workflow.subprocess, "run", fake_subprocess_run)

    with pytest.raises(HTTPException) as exc_info:
        workflow._repair_and_validate_wiki_naming()

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "message": "Wiki 命名契约校验失败",
        "validation": {
            "returncode": 1,
            "stdout": '{"issue_count": 1}',
            "stderr": "invalid names",
        },
    }


def test_repair_and_validate_wiki_naming_maps_repair_failure(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    repair_script = tmp_path / "repair_wiki_naming.py"
    validate_script = tmp_path / "validate_wiki_naming.py"
    repair_script.write_text("# repair\n", encoding="utf-8")
    validate_script.write_text("# validate\n", encoding="utf-8")
    calls = []

    def fake_subprocess_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 3, stdout="partial repair\n", stderr="cannot repair\n")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "WIKI_NAMING_REPAIR_SCRIPT", repair_script)
    monkeypatch.setattr(workflow, "WIKI_NAMING_VALIDATE_SCRIPT", validate_script)
    monkeypatch.setattr(workflow.subprocess, "run", fake_subprocess_run)

    with pytest.raises(HTTPException) as exc_info:
        workflow._repair_and_validate_wiki_naming()

    assert calls == [[sys.executable, str(repair_script), "--wiki-root", str(wiki_root)]]
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "message": "Wiki 命名修复失败",
        "repair": {
            "returncode": 3,
            "stdout": "partial repair",
            "stderr": "cannot repair",
        },
    }


def test_extract_semantic_for_task_runs_rule_llm_obsidian_and_naming_contract(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    semantic_script = tmp_path / "extract_company_semantics.py"
    llm_script = tmp_path / "llm_semantic_enrichment.py"
    semantic_script.write_text("# rule\n", encoding="utf-8")
    llm_script.write_text("# llm\n", encoding="utf-8")
    calls = []
    find_results = iter(["000001-旧名称", "000001-新名称"])

    def fake_run_command(args, timeout=180, env=None):
        calls.append({"kind": Path(args[1]).name, "args": args, "timeout": timeout, "env": env})
        return {"returnCode": 0, "stdout": f"{Path(args[1]).stem} ok", "stderr": ""}

    naming_calls = []

    def fake_naming_check():
        naming_calls.append(len(naming_calls) + 1)
        return {"repair": {"returncode": 0}, "validation": {"returncode": 0}, "call": len(naming_calls)}

    obsidian_calls = []

    def fake_obsidian(company_dir):
        obsidian_calls.append(company_dir)
        return {"returnCode": 0, "stdout": "obsidian ok", "stderr": ""}

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_TIMEOUT", 77)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: next(find_results))
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", fake_naming_check)
    monkeypatch.setattr(workflow, "_generate_obsidian_for_company", fake_obsidian)
    monkeypatch.setattr(workflow, "_semantic_status", lambda company_dir, task_id: {"companyDir": company_dir, "taskId": task_id, "status": "ready"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(
        workflow,
        "load_llm_settings",
        lambda include_secrets=True: {
            "providers": {
                "local": {
                    "baseUrl": "http://llm.local/v1",
                    "model": "qwen-local",
                    "apiKey": "secret",
                    "timeoutSeconds": 44,
                    "maxTokens": 2048,
                    "temperature": 0.2,
                    "chatTemplateKwargs": {"enable_thinking": False},
                }
            }
        },
    )

    result = workflow.extract_semantic_for_task("task-semantic-contract")

    assert result["ok"] is True
    assert result["companyDir"] == "000001-新名称"
    assert result["naming"]["before"]["call"] == 1
    assert result["naming"]["after"]["call"] == 2
    assert obsidian_calls == ["000001-新名称"]
    assert [call["kind"] for call in calls] == ["extract_company_semantics.py", "llm_semantic_enrichment.py"]
    assert calls[0]["args"] == [
        sys.executable,
        str(semantic_script),
        "--wiki-root",
        str(wiki_root),
        "--company",
        "000001-新名称",
    ]
    assert calls[0]["timeout"] == 180
    assert calls[0]["env"] is None
    assert calls[1]["args"] == [
        sys.executable,
        str(llm_script),
        "--wiki-root",
        str(wiki_root),
        "--company",
        "000001-新名称",
    ]
    assert calls[1]["timeout"] == 77
    assert calls[1]["env"]["SIQ_LOCAL_LLM_BASE_URL"] == "http://llm.local/v1"
    assert calls[1]["env"]["SIQ_LOCAL_LLM_MODEL"] == "qwen-local"
    assert calls[1]["env"]["SIQ_LOCAL_LLM_API_KEY"] == "secret"
    assert calls[1]["env"]["SIQ_LLM_SEMANTIC_TIMEOUT"] == "44"
    assert calls[1]["env"]["SIQ_LLM_SEMANTIC_MAX_TOKENS"] == "2048"
    assert calls[1]["env"]["SIQ_LLM_SEMANTIC_TEMPERATURE"] == "0.2"
    assert calls[1]["env"]["SIQ_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] == '{"enable_thinking": false}'
    assert calls[1]["env"]["FINSIGHT_LOCAL_LLM_BASE_URL"] == "http://llm.local/v1"
    assert calls[1]["env"]["FINSIGHT_LOCAL_LLM_MODEL"] == "qwen-local"
    assert calls[1]["env"]["FINSIGHT_LOCAL_LLM_API_KEY"] == "secret"
    assert calls[1]["env"]["FINSIGHT_LLM_SEMANTIC_TIMEOUT"] == "44"
    assert calls[1]["env"]["FINSIGHT_LLM_SEMANTIC_MAX_TOKENS"] == "2048"
    assert calls[1]["env"]["FINSIGHT_LLM_SEMANTIC_TEMPERATURE"] == "0.2"
    assert calls[1]["env"]["FINSIGHT_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] == '{"enable_thinking": false}'
    assert calls[1]["env"]["SIQ_LLM_SEMANTIC_PROVIDER"] == "local"
    assert calls[1]["env"]["FINSIGHT_LLM_SEMANTIC_PROVIDER"] == "local"


def test_llm_semantic_env_uses_active_cloud_provider(monkeypatch):
    monkeypatch.setattr(
        workflow,
        "load_llm_settings",
        lambda include_secrets=True: {
            "activeProvider": "cloud",
            "providers": {
                "local": {
                    "baseUrl": "http://llm.local/v1",
                    "model": "qwen-local",
                },
                "cloud": {
                    "baseUrl": "https://llm.example/v1",
                    "model": "cloud-model",
                    "apiKey": "cloud-secret",
                    "timeoutSeconds": 55,
                    "maxTokens": 4096,
                    "temperature": 0.1,
                },
            },
        },
    )

    env = workflow._llm_semantic_env()

    assert env["SIQ_LOCAL_LLM_BASE_URL"] == "https://llm.example/v1"
    assert env["SIQ_LOCAL_LLM_MODEL"] == "cloud-model"
    assert env["SIQ_LOCAL_LLM_API_KEY"] == "cloud-secret"
    assert env["SIQ_LLM_SEMANTIC_TIMEOUT"] == "55"
    assert env["SIQ_LLM_SEMANTIC_MAX_TOKENS"] == "4096"
    assert env["SIQ_LLM_SEMANTIC_TEMPERATURE"] == "0.1"
    assert env["SIQ_LLM_SEMANTIC_PROVIDER"] == "cloud"
    assert env["FINSIGHT_LOCAL_LLM_BASE_URL"] == "https://llm.example/v1"
    assert env["FINSIGHT_LOCAL_LLM_MODEL"] == "cloud-model"
    assert env["FINSIGHT_LOCAL_LLM_API_KEY"] == "cloud-secret"
    assert env["FINSIGHT_LLM_SEMANTIC_PROVIDER"] == "cloud"


def test_llm_semantic_env_maps_hermes_provider_contract(monkeypatch):
    modes = []
    monkeypatch.setattr(workflow, "infer_model_mode", lambda **_kwargs: "thinking")
    monkeypatch.setattr(workflow, "set_all_profile_model_modes", lambda mode: modes.append(mode))
    monkeypatch.setattr(
        workflow,
        "hermes_profile_config",
        lambda profile: {"base": "http://hermes.local/runs", "model": f"{profile}-model"},
    )
    monkeypatch.setattr(
        workflow,
        "load_llm_settings",
        lambda include_secrets=True: {
            "providers": {
                "local": {
                    "baseUrl": "hermes://siq_analysis",
                    "model": "ignored-model",
                    "apiKey": "ignored-secret",
                }
            }
        },
    )

    env = workflow._llm_semantic_env()

    assert modes == ["thinking"]
    assert env["SIQ_LLM_SEMANTIC_HERMES_PROFILE"] == "siq_analysis"
    assert env["FINSIGHT_LLM_SEMANTIC_HERMES_PROFILE"] == "siq_analysis"
    assert env["SIQ_LLM_SEMANTIC_HERMES_RUNS_URL"] == "http://hermes.local/runs"
    assert env["FINSIGHT_LLM_SEMANTIC_HERMES_RUNS_URL"] == "http://hermes.local/runs"
    assert env["SIQ_LLM_SEMANTIC_HERMES_MODE"] == "thinking"
    assert env["FINSIGHT_LLM_SEMANTIC_HERMES_MODE"] == "thinking"
    assert env["SIQ_LLM_SEMANTIC_PROVIDER_BASE_URL"] == "hermes://siq_analysis"
    assert env["FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"] == "hermes://siq_analysis"
    assert env["SIQ_LOCAL_LLM_BASE_URL"] == "hermes://siq_analysis"
    assert env["SIQ_LOCAL_LLM_MODEL"] == "siq_analysis-model"
    assert env["SIQ_LOCAL_LLM_API_KEY"] == ""
    assert env["FINSIGHT_LOCAL_LLM_MODEL"] == "siq_analysis-model"
    assert env["FINSIGHT_LOCAL_LLM_API_KEY"] == ""


def test_extract_generic_semantic_rejects_non_generic_identity_without_command(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = "000001-测试公司"
    company_path = wiki_root / "companies" / company_dir
    company_path.mkdir(parents=True)
    (company_path / "company.json").write_text('{"identity_route":"a_share_wiki_import"}', encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("semantic command should not run for non-generic company identity")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: company_dir)
    monkeypatch.setattr(workflow, "_run_command", fail_run)

    with pytest.raises(HTTPException) as exc_info:
        workflow.extract_generic_semantic_for_task("task-generic-contract")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "该任务不是通用主体入库路线，请使用标准语义层接口"


def test_extract_semantic_for_task_maps_rule_failure(monkeypatch, tmp_path):
    semantic_script = tmp_path / "extract_company_semantics.py"
    semantic_script.write_text("# rule\n", encoding="utf-8")
    failure = {"returnCode": 2, "stdout": "", "stderr": "rule failed"}

    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: "000001-测试公司")
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", lambda: {"ok": True})
    monkeypatch.setattr(workflow, "_run_command", lambda *args, **kwargs: failure)

    with pytest.raises(HTTPException) as exc_info:
        workflow.extract_semantic_for_task("task-semantic-rule-failure")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {"stage": "rule_semantic", **failure}


def test_extract_semantic_for_task_maps_required_llm_failure(monkeypatch, tmp_path):
    semantic_script = tmp_path / "extract_company_semantics.py"
    llm_script = tmp_path / "llm_semantic_enrichment.py"
    semantic_script.write_text("# rule\n", encoding="utf-8")
    llm_script.write_text("# llm\n", encoding="utf-8")
    calls = []

    def fake_run_command(args, timeout=180, env=None):
        calls.append(Path(args[1]).name)
        if Path(args[1]) == llm_script:
            return {"returnCode": 3, "stdout": "", "stderr": "llm failed"}
        return {"returnCode": 0, "stdout": "rule ok", "stderr": ""}

    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", True)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: "000001-测试公司")
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", lambda: {"ok": True})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "load_llm_settings", lambda include_secrets=True: {"providers": {}})

    with pytest.raises(HTTPException) as exc_info:
        workflow.extract_semantic_for_task("task-semantic-llm-failure")

    assert calls == ["extract_company_semantics.py", "llm_semantic_enrichment.py"]
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "stage": "llm_semantic",
        "returnCode": 3,
        "stdout": "",
        "stderr": "llm failed",
    }


def test_extract_semantic_for_task_keeps_optional_missing_llm_detail(monkeypatch, tmp_path):
    semantic_script = tmp_path / "extract_company_semantics.py"
    missing_llm_script = tmp_path / "missing_llm_semantic_enrichment.py"
    semantic_script.write_text("# rule\n", encoding="utf-8")
    obsidian_calls = []

    def fake_run_command(args, timeout=180, env=None):
        return {"returnCode": 0, "stdout": "rule ok", "stderr": ""}

    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_SCRIPT", missing_llm_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", False)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: "000001-测试公司")
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", lambda: {"ok": True})
    monkeypatch.setattr(workflow, "_generate_obsidian_for_company", lambda company_dir: obsidian_calls.append(company_dir) or {"returnCode": 0})
    monkeypatch.setattr(workflow, "_semantic_status", lambda company_dir, task_id: {"status": "ready"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)

    result = workflow.extract_semantic_for_task("task-semantic-optional-llm")

    assert result["ok"] is True
    assert result["result"]["llm"] == {
        "stage": "llm_semantic",
        "returnCode": 127,
        "stdout": "",
        "stderr": f"LLM semantic script not found: {missing_llm_script}",
    }
    assert obsidian_calls == ["000001-测试公司"]


def test_extract_semantic_for_task_keeps_optional_llm_failure_non_blocking(monkeypatch, tmp_path):
    semantic_script = tmp_path / "extract_company_semantics.py"
    llm_script = tmp_path / "llm_semantic_enrichment.py"
    semantic_script.write_text("# rule\n", encoding="utf-8")
    llm_script.write_text("# llm\n", encoding="utf-8")
    obsidian_calls = []

    def fake_run_command(args, timeout=180, env=None):
        if Path(args[1]) == llm_script:
            return {"returnCode": 9, "stdout": "", "stderr": "optional llm failed"}
        return {"returnCode": 0, "stdout": "rule ok", "stderr": ""}

    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", False)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: "000001-测试公司")
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", lambda: {"ok": True})
    monkeypatch.setattr(workflow, "_generate_obsidian_for_company", lambda company_dir: obsidian_calls.append(company_dir) or {"returnCode": 0})
    monkeypatch.setattr(workflow, "_semantic_status", lambda company_dir, task_id: {"status": "ready"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "load_llm_settings", lambda include_secrets=True: {"providers": {}})

    result = workflow.extract_semantic_for_task("task-semantic-optional-llm-failure")

    assert result["ok"] is True
    assert result["result"]["llm"] == {"returnCode": 9, "stdout": "", "stderr": "optional llm failed"}
    assert obsidian_calls == ["000001-测试公司"]


def test_extract_generic_semantic_success_runs_rule_llm_obsidian_without_naming(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = "ACME_US_ACME"
    company_path = wiki_root / "companies" / company_dir
    semantic_script = tmp_path / "extract_company_semantics.py"
    llm_script = tmp_path / "llm_semantic_enrichment.py"
    company_path.mkdir(parents=True)
    (company_path / "company.json").write_text('{"identity_route":"generic_non_a_share_wiki_import"}', encoding="utf-8")
    semantic_script.write_text("# rule\n", encoding="utf-8")
    llm_script.write_text("# llm\n", encoding="utf-8")
    calls = []
    obsidian_calls = []

    def fake_run_command(args, timeout=180, env=None):
        calls.append({"kind": Path(args[1]).name, "args": args, "timeout": timeout, "env": env})
        return {"returnCode": 0, "stdout": "ok", "stderr": ""}

    def fail_naming():
        raise AssertionError("generic semantic route should not run naming repair/validate")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "SEMANTIC_SCRIPT", semantic_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", False)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_TIMEOUT", 66)
    monkeypatch.setattr(workflow, "_find_company_for_task", lambda task_id: company_dir)
    monkeypatch.setattr(workflow, "_repair_and_validate_wiki_naming", fail_naming)
    monkeypatch.setattr(workflow, "_generate_obsidian_for_company", lambda seen_company_dir: obsidian_calls.append(seen_company_dir) or {"returnCode": 0})
    monkeypatch.setattr(workflow, "_semantic_status", lambda seen_company_dir, task_id: {"companyDir": seen_company_dir, "taskId": task_id, "status": "ready"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "load_llm_settings", lambda include_secrets=True: {"providers": {"local": {"model": "qwen-local"}}})

    result = workflow.extract_generic_semantic_for_task("task-generic-success")

    assert result["ok"] is True
    assert result["companyDir"] == company_dir
    assert obsidian_calls == [company_dir]
    assert [call["kind"] for call in calls] == ["extract_company_semantics.py", "llm_semantic_enrichment.py"]
    assert calls[0]["args"] == [
        sys.executable,
        str(semantic_script),
        "--wiki-root",
        str(wiki_root),
        "--company",
        company_dir,
    ]
    assert calls[0]["timeout"] == 180
    assert calls[0]["env"] is None
    assert calls[1]["args"] == [
        sys.executable,
        str(llm_script),
        "--wiki-root",
        str(wiki_root),
        "--company",
        company_dir,
    ]
    assert calls[1]["timeout"] == 66
    assert calls[1]["env"]["SIQ_LOCAL_LLM_MODEL"] == "qwen-local"
    assert calls[1]["env"]["FINSIGHT_LOCAL_LLM_MODEL"] == "qwen-local"
    assert result["semantic"] == {"companyDir": company_dir, "taskId": "task-generic-success", "status": "ready"}
