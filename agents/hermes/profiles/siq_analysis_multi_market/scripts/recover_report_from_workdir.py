#!/usr/bin/env python3
"""One-shot deterministic recovery for SIQ analysis report jobs.

The analysis agent should use this script when a report run has checkpoints
but is looping or cannot complete free-form generation. It renders from the
work directory, repairs citations, validates quality, and writes one JSON
result describing the next action.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
RENDER_SCRIPT = SCRIPT_DIR / "render_report_from_checkpoint.py"
REPAIR_SCRIPT = SCRIPT_DIR / "repair_report_citations.py"
VALIDATE_SCRIPT = SCRIPT_DIR / "validate_report_quality.py"


def run_step(cmd: list[str]) -> dict[str, Any]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    parsed: Any | None = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": stdout[-4000:],
        "stderr": result.stderr.strip()[-4000:],
        "json": parsed,
        "ok": result.returncode == 0,
    }


def report_files(prefix: Path) -> dict[str, str]:
    return {
        "md": str(prefix.parent / f"{prefix.name}.md"),
        "json": str(prefix.parent / f"{prefix.name}.json"),
        "html": str(prefix.parent / f"{prefix.name}.html"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True, type=Path)
    parser.add_argument("--write-json", type=Path)
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="允许覆盖已有最终报告；覆盖前由渲染器自动备份。",
    )
    parser.add_argument("--backup-dir", type=Path, help="覆盖前备份已有报告的目录")
    args = parser.parse_args()

    work_dir = args.work_dir
    output_prefix = args.output_prefix
    work_dir.mkdir(parents=True, exist_ok=True)
    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "stage": "started",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "work_dir": str(work_dir),
        "output_prefix": str(output_prefix),
        "files": report_files(output_prefix),
        "steps": {},
        "next_action": None,
    }

    render_cmd = [
        sys.executable,
        str(RENDER_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--output-prefix",
        str(output_prefix),
    ]
    if args.allow_overwrite:
        render_cmd.append("--allow-overwrite")
    if args.backup_dir:
        render_cmd.extend(["--backup-dir", str(args.backup_dir)])
    render = run_step(render_cmd)
    result["steps"]["render"] = render
    if not render["ok"]:
        render_payload = render.get("json") if isinstance(render.get("json"), dict) else {}
        result["stage"] = render_payload.get("stage", "render_failed")
        result["next_action"] = render_payload.get(
            "next_action",
            "检查 metric_snapshot.json 或 financial_data_complete.json 是否存在且 JSON 可解析。",
        )
        if render_payload:
            result["render"] = render_payload
        if args.write_json:
            args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    repair_json = work_dir / "citation_repair.json"
    repair = run_step([
        sys.executable,
        str(REPAIR_SCRIPT),
        "--prefix",
        str(output_prefix),
        "--write-json",
        str(repair_json),
    ])
    result["steps"]["repair_citations"] = repair
    if not repair["ok"]:
        result["stage"] = "citation_repair_failed"
        result["next_action"] = "打开 citation_repair.json 和 stderr，检查 task_id/table_index 是否能回溯 PDF 页。"
        if args.write_json:
            args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    validation_json = work_dir / "final_validation.json"
    validate = run_step([
        sys.executable,
        str(VALIDATE_SCRIPT),
        "--prefix",
        str(output_prefix),
        "--write-json",
        str(validation_json),
    ])
    result["steps"]["validate"] = validate
    validation_payload = validate.get("json") if isinstance(validate.get("json"), dict) else {}
    result["validation"] = validation_payload
    result["ok"] = bool(validate["ok"] and validation_payload.get("ok"))
    result["stage"] = "completed" if result["ok"] else "validation_failed"
    if result["ok"]:
        warnings = validation_payload.get("warnings") or []
        result["next_action"] = (
            "报告已通过结构质量验收；若存在 warnings，应在最终回复中披露。"
            if warnings
            else "报告已通过结构质量验收。"
        )
    else:
        failures = validation_payload.get("failures") or []
        result["next_action"] = (
            "根据 validation.failures 定向修复；禁止重复执行同一恢复命令超过 2 次。"
            if failures
            else "查看 validate step 的 stdout/stderr。"
        )

    if args.write_json:
        args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
