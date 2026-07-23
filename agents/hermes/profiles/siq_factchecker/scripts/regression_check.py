#!/usr/bin/env python3
"""Lightweight regression checks for SIQ_factchecker outputs."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_DIR")
    or Path(__file__).resolve().parents[5] / "data" / "wiki"
)
LAUNCHER = ROOT / "SIQ_factchecker"


def run_verify(company: str) -> None:
    subprocess.run([str(LAUNCHER), "verify", company, "--year", "2025"], cwd=ROOT, check=True)


def load_factcheck(company_id: str, stock_code: str, short_name: str) -> dict:
    path = WIKI / "companies" / company_id / "factcheck" / f"{stock_code}-{short_name}-2025-factcheck.json"
    if not path.exists():
        raise AssertionError(f"missing factcheck json: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assert_contract(payload: dict) -> None:
    for key in ("verdict", "summary", "checks", "evidence_summary", "metric_evidence_map", "calculation_audit"):
        if key not in payload:
            raise AssertionError(f"missing top-level key: {key}")
    if not payload["evidence_summary"]:
        raise AssertionError("evidence_summary must not be empty")
    for check_name, check in payload["checks"].items():
        for issue in check.get("issues", []):
            if "evidence_refs" not in issue:
                raise AssertionError(f"{check_name} issue missing evidence_refs: {issue.get('message')}")


def main() -> int:
    samples = [
        ("601238", "601238-广汽集团", "601238", "广汽集团"),
        ("601127", "601127-赛力斯", "601127", "赛力斯"),
    ]
    for company, company_id, stock_code, short_name in samples:
        run_verify(company)
        payload = load_factcheck(company_id, stock_code, short_name)
        assert_contract(payload)
        if payload["summary"].get("company_evidence_status") not in {"local_wiki_available", "postgresql_available"}:
            raise AssertionError(f"unexpected evidence status for {company_id}: {payload['summary'].get('company_evidence_status')}")
        if payload["summary"].get("calculation_audit_items", 0) <= 0:
            raise AssertionError(f"expected calculation audit items for {company_id}")
    print("regression_check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
