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
