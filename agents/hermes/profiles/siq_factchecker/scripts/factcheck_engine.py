#!/usr/bin/env python3
"""Compatibility wrapper for SIQ_factchecker v2.

The canonical implementation lives in factcheck_cli.py. This wrapper keeps the
old direct script entrypoint working while preserving the no-score/no-rating
output contract.
"""

import argparse
import json
import os
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

from factcheck_cli import FactCheckEngine, _load_env_file, report_to_dict  # noqa: E402
from wiki_data_accessor import WikiDataAccessor  # noqa: E402


def main() -> None:
    _load_env_file()
    parser = argparse.ArgumentParser(description="SIQ_factchecker v2 核心引擎（无评分版）")
    parser.add_argument("company_id", help="公司ID或股票代码，如 600399")
    parser.add_argument("--year", type=int, default=2025, help="报告年份")
    parser.add_argument("--output", type=str, help="输出文件路径（可选）")
    parser.add_argument(
        "--wiki-dir",
        type=str,
        default=os.environ.get("SIQ_WIKI_ROOT")
        or os.environ.get("WIKI_DIR")
        or str(Path(__file__).resolve().parents[5] / "data" / "wiki"),
        help="Wiki 目录路径",
    )
    args = parser.parse_args()

    accessor = WikiDataAccessor(wiki_dir=Path(args.wiki_dir))
    company = accessor.get_company_by_id(args.company_id) or accessor.get_company_by_stock_code(args.company_id)
    target_company_id = company.company_id if company else args.company_id
    report = FactCheckEngine(accessor).verify(target_company_id, args.year)
    payload = report_to_dict(report)

    if args.output:
        output_path = Path(args.output)
    elif company:
        factcheck_dir = accessor.ensure_factcheck_dir(company.company_id)
        output_path = factcheck_dir / f"{company.stock_code}-{company.company_short_name}-{args.year}-factcheck.json"
    else:
        output_path = Path(f"{args.company_id}-{args.year}-factcheck.json")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print("SIQ_factchecker v2 核实结果")
    print("=" * 80)
    print(f"公司: {payload['company_id']}")
    print(f"报告: {payload['report_file']}")
    print(f"判决: {payload['verdict'].upper()}")
    print(f"问题: critical={payload['summary']['critical']} warning={payload['summary']['warning']} suggestion={payload['summary']['suggestion']}")
    print(f"PostgreSQL: {payload['summary'].get('database_status')} rows={payload['summary'].get('evidence_rows')}")
    print(f"输出: {output_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
