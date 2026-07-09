#!/usr/bin/env python3
"""Run rule semantic extraction for one or more market wiki roots."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
WIKI_ROOT = REPO_ROOT / "data" / "wiki"
RULE_SCRIPT = REPO_ROOT / "data" / "wiki" / "wikiset" / "extract_company_semantics.py"
MARKET_ROOTS = {
    "CN": WIKI_ROOT,
    "HK": WIKI_ROOT / "hk",
    "KR": WIKI_ROOT / "kr",
    "JP": WIKI_ROOT / "jp",
    "EU": WIKI_ROOT / "eu",
    "US": WIKI_ROOT / "us",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markets_from_arg(value: str) -> list[str]:
    if value.upper() == "ALL":
        return ["HK", "KR", "JP", "EU", "US"]
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def company_dirs(root: Path, company: str = "") -> list[Path]:
    companies_root = root / "companies"
    if company:
        return [companies_root / company]
    if not companies_root.is_dir():
        return []
    return sorted(path for path in companies_root.iterdir() if path.is_dir())


def summarize_company(company_dir: Path) -> dict[str, Any]:
    semantic = company_dir / "semantic"
    log = read_json(semantic / "extraction_log.json", {}) or {}
    counts = log.get("counts") or {}
    quality = log.get("quality") or {}
    return {
        "company_dir": company_dir.name,
        "status": "ready" if counts.get("segments", 0) > 0 and counts.get("evidence", 0) > 0 and counts.get("facts", 0) > 0 else "needs_review",
        "counts": counts,
        "quality": quality,
        "warnings": log.get("warnings") or [],
    }


def run_market(market: str, root: Path, company: str = "") -> dict[str, Any]:
    cmd = [sys.executable, str(RULE_SCRIPT), "--wiki-root", str(root)]
    if company:
        cmd.extend(["--company", company])
    completed = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    companies = company_dirs(root, company)
    results = [summarize_company(path) for path in companies if path.is_dir()]
    failures = []
    if completed.returncode != 0:
        failures.append({
            "stage": "rule_semantics",
            "returncode": completed.returncode,
            "stderr": completed.stderr[-4000:],
            "stdout": completed.stdout[-4000:],
        })
    needs_review = [item for item in results if item.get("status") != "ready"]
    manifest = {
        "schema_version": 1,
        "market": market,
        "generated_at": now_iso(),
        "engine": "RuleSemanticEngine",
        "profile": market,
        "wiki_root": str(root),
        "company_count": len(results),
        "ready_count": len(results) - len(needs_review),
        "needs_review_count": len(needs_review),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
    }
    write_json(root / "_meta" / "semantic_extraction_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="ALL", help="HK,KR,JP,EU,US,CN or ALL")
    parser.add_argument("--wiki-root", default="", help="Override root for a single market")
    parser.add_argument("--company", default="")
    args = parser.parse_args()

    manifests = []
    exit_code = 0
    markets = markets_from_arg(args.market)
    for market in markets:
        root = Path(args.wiki_root) if args.wiki_root else MARKET_ROOTS.get(market)
        if root is None:
            print(f"skip unknown market {market}", file=sys.stderr)
            exit_code = 1
            continue
        manifest = run_market(market, root, args.company)
        manifests.append({
            "market": market,
            "company_count": manifest["company_count"],
            "ready_count": manifest["ready_count"],
            "needs_review_count": manifest["needs_review_count"],
            "failure_count": manifest["failure_count"],
            "manifest_path": str(root / "_meta" / "semantic_extraction_manifest.json"),
        })
        print(json.dumps(manifests[-1], ensure_ascii=False))
        if manifest["failure_count"]:
            exit_code = 1
    if len(manifests) > 1:
        write_json(WIKI_ROOT / "_meta" / "semantic_extraction_manifest.json", {
            "schema_version": 1,
            "generated_at": now_iso(),
            "markets": manifests,
        })
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
