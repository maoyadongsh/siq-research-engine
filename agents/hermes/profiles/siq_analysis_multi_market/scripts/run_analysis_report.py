#!/usr/bin/env python3
"""Fail-closed analysis entrypoint for parsed non-CN report packages."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis_bundle_renderer import render_analysis_bundle
from analysis_input_bundle import load_analysis_input_bundle
from formal_research_packs import build_formal_research_packs
from input_adapters import SourceAdapterError


FORMAL_BUNDLE_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_filename_part(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value or "").strip("._-")
    return text or "company"


def work_dir_for(prefix: Path) -> Path:
    return prefix.parent / ".work" / prefix.name


def report_prefix_from_bundle(bundle: dict[str, Any]) -> Path:
    target = bundle.get("research_target") if isinstance(bundle.get("research_target"), dict) else {}
    report = bundle.get("source_report") if isinstance(bundle.get("source_report"), dict) else {}
    server_paths = bundle.get("server_paths") if isinstance(bundle.get("server_paths"), dict) else {}
    analysis_dir = Path(str(server_paths.get("analysis_dir") or ""))
    code = _safe_filename_part(str(target.get("display_code") or "company"))
    name = _safe_filename_part(
        str(target.get("display_name") or target.get("company_wiki_id") or "company")
    )
    period = _safe_filename_part(str(report.get("period_end") or report.get("fiscal_year") or "period"))
    report_id = _safe_filename_part(str(report.get("report_id") or "report"))
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return analysis_dir / f"{code}-{name}-{period}-{report_id}-analysis-{timestamp}"


def run_formal_bundle_mode(args: argparse.Namespace) -> int:
    started_at = datetime.now().isoformat(timespec="seconds")
    try:
        bundle = load_analysis_input_bundle(args.input_bundle)
        identity = bundle.get("research_identity") if isinstance(bundle.get("research_identity"), dict) else {}
        market = str(identity.get("market") or "").strip().upper()
        if market not in FORMAL_BUNDLE_MARKETS:
            raise SourceAdapterError(
                "unsupported_market",
                f"siq_analysis_multi_market does not support market {market or 'unknown'}",
                details={"market": market, "supported_markets": sorted(FORMAL_BUNDLE_MARKETS)},
            )
        prefix = args.output_prefix or report_prefix_from_bundle(bundle)
        work_dir = args.work_dir or work_dir_for(prefix)
        research_pack_result = build_formal_research_packs(bundle, work_dir=work_dir)
        rendered = render_analysis_bundle(
            bundle,
            output_prefix=prefix,
            research_pack_result=research_pack_result,
            staging_dir=work_dir / "publish_staging",
            allow_overwrite=args.allow_overwrite,
        )
        checkpoints = {
            "analysis_input_bundle": str(args.input_bundle),
            **dict(research_pack_result.get("paths") or {}),
            **dict(rendered.get("checkpoints") or {}),
        }
        result: dict[str, Any] = {
            **rendered,
            "started_at": started_at,
            "pipeline_mode": "formal_analysis_input_bundle",
            "output_prefix": str(prefix),
            "work_dir": str(work_dir),
            "checkpoints": checkpoints,
        }
    except SourceAdapterError as exc:
        result = {
            "ok": False,
            "stage": exc.code,
            "started_at": started_at,
            "pipeline_mode": "formal_analysis_input_bundle",
            "details": exc.details,
            "next_action": str(exc),
        }
    if args.write_json:
        dump_json(args.write_json, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HK/US/EU/KR/JP parsed-report analysis pipeline",
    )
    parser.add_argument(
        "--input-bundle",
        type=Path,
        required=True,
        help="服务端生成并完成路径边界校验的 AnalysisInputBundle",
    )
    parser.add_argument("--output-prefix", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--force", action="store_true", help="兼容工作流命令；正式链仍遵守覆盖策略")
    parser.add_argument("--write-json", type=Path)
    return run_formal_bundle_mode(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
