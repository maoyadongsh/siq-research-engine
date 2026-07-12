import json
from pathlib import Path

import pytest

from services import market_report_eval_service as service


def _read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def test_run_market_ingestion_eval_owns_command_and_report_loading(tmp_path):
    eval_script = tmp_path / "scripts" / "run_market_ingestion_eval.py"
    eval_script.parent.mkdir(parents=True)
    eval_script.write_text("# eval", encoding="utf-8")
    output_path = tmp_path / "reports" / "eval.json"
    markdown_path = tmp_path / "reports" / "eval.md"
    seen = {}

    class Completed:
        returncode = 0
        stdout = "eval ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        output_path.parent.mkdir(parents=True)
        output_path.write_text(json.dumps({"score": 0.98, "cases": 4}), encoding="utf-8")
        markdown_path.write_text("# Eval", encoding="utf-8")
        return Completed()

    result = service.run_market_ingestion_eval(
        payload={"output": str(output_path), "markdown": str(markdown_path)},
        eval_script=eval_script,
        repo_root=tmp_path,
        default_output=tmp_path / "default.json",
        default_markdown=tmp_path / "default.md",
        executable="python",
        run_command=fake_run,
        command_for_display=lambda args: " ".join(args),
        read_json_file=_read_json_file,
        rel_or_abs=lambda path: str(path),
    )

    assert result["ok"] is True
    assert result["report"] == {"score": 0.98, "cases": 4}
    assert result["markdown_path"] == str(markdown_path)
    assert seen["args"] == [
        "python",
        str(eval_script),
        "--output",
        str(output_path),
        "--markdown",
        str(markdown_path),
    ]
    assert seen["kwargs"] == {"cwd": tmp_path, "timeout": 900}


def test_run_market_ingestion_eval_reports_missing_script_without_running_command(tmp_path):
    missing_script = tmp_path / "scripts" / "run_market_ingestion_eval.py"

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    with pytest.raises(service.MarketReportEvalError) as exc_info:
        service.run_market_ingestion_eval(
            payload={},
            eval_script=missing_script,
            repo_root=tmp_path,
            default_output=tmp_path / "eval.json",
            default_markdown=tmp_path / "eval.md",
            executable="python",
            run_command=fail_run,
            command_for_display=lambda args: " ".join(args),
            read_json_file=_read_json_file,
            rel_or_abs=lambda path: str(path),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == f"Missing eval script: {missing_script}"
