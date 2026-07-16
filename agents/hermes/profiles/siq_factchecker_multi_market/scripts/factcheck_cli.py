#!/usr/bin/env python3
"""Fail-closed fact-check entrypoint for resolved non-CN research targets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from generate_factcheck_html import generate_html
from market_factcheck_engine import load_resolved_target, run_market_factcheck


PROJECT_ROOT = Path(
    os.environ.get("SIQ_PROJECT_ROOT") or Path(__file__).resolve().parents[5]
).expanduser().resolve()
DEFAULT_WIKI_ROOT = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or PROJECT_ROOT / "data" / "wiki"
).expanduser().resolve()


def verify_resolved_target(args: argparse.Namespace) -> dict:
    wiki_root = Path(args.wiki_root or DEFAULT_WIKI_ROOT).expanduser().resolve()
    target = load_resolved_target(args.target_json, wiki_root)
    report = run_market_factcheck(target)
    source = (
        target.research_target.get("source_report")
        if isinstance(target.research_target.get("source_report"), dict)
        else {}
    )
    report_id = str(source.get("report_id") or target.report_dir.name)
    output_path = args.output or (
        target.company_dir
        / "factcheck"
        / f"{target.analysis_artifact.stem}-{report_id}-factcheck.json"
    )
    output_path = output_path.expanduser().resolve()
    factcheck_dir = (target.company_dir / "factcheck").resolve()
    try:
        output_path.relative_to(factcheck_dir)
    except ValueError as exc:
        raise ValueError(
            "factcheck output must remain inside the resolved company factcheck directory"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = output_path.with_suffix(".html")
    html_path.write_text(generate_html(str(output_path)), encoding="utf-8")
    result = {
        "status": "completed" if report.get("verdict") == "approve" else "degraded",
        "json_path": str(output_path),
        "html_path": str(html_path),
        "research_identity": report.get("research_identity"),
        "verdict": report.get("verdict"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="HK/US/EU/KR/JP resolved-target fact checker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify", help="核查服务端已解析的权威研究目标")
    verify_parser.add_argument(
        "--target-json",
        type=Path,
        required=True,
        help="服务端生成的 ResolvedReportPackage bundle",
    )
    verify_parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    verify_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verify_resolved_target(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
