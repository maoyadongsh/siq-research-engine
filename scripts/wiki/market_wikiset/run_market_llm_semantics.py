#!/usr/bin/env python3
"""Run evidence-constrained LLM semantic enrichment for market wiki roots."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
WIKI_ROOT = REPO_ROOT / "data" / "wiki"
LLM_SCRIPT = REPO_ROOT / "scripts" / "wiki" / "wikiset" / "llm_semantic_enrichment.py"
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


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def report_id_for(company_dir: Path) -> str:
    company = read_json(company_dir / "company.json", {}) or {}
    report_id = company.get("primary_report_id") or "2025-annual"
    if not (company_dir / "reports" / report_id).is_dir():
        reports = sorted(path for path in (company_dir / "reports").glob("*") if path.is_dir())
        if reports:
            report_id = reports[0].name
    return str(report_id)


def validate_llm_output(company_dir: Path) -> dict[str, Any]:
    report_id = report_id_for(company_dir)
    out_dir = company_dir / "semantic" / "llm" / report_id
    log = read_json(out_dir / "extraction_log.json", {}) or {}
    semantic_dir = company_dir / "semantic"
    segments_payload = read_json(semantic_dir / "segments.json", {}) or {}
    evidence_payload = read_json(semantic_dir / "evidence_semantic.json", {}) or {}
    segments = segments_payload.get("segments") if isinstance(segments_payload.get("segments"), list) else []
    evidence = evidence_payload.get("evidence") or evidence_payload.get("items") or []
    allowed_segments = {str(item.get("segment_id")) for item in segments if isinstance(item, dict) and item.get("segment_id")}
    allowed_evidence = {str(item.get("evidence_id")) for item in evidence if isinstance(item, dict) and item.get("evidence_id")}
    current_inputs = {
        "company_json_sha256": sha256_file(company_dir / "company.json"),
        "segments_sha256": sha256_file(semantic_dir / "segments.json"),
        "evidence_semantic_sha256": sha256_file(semantic_dir / "evidence_semantic.json"),
        "facts_sha256": sha256_file(semantic_dir / "facts.json"),
        "claims_sha256": sha256_file(semantic_dir / "claims.json"),
        "artifact_manifest_sha256": sha256_file(company_dir / "reports" / report_id / "artifact_manifest.json"),
    }
    log_inputs = log.get("inputs") if isinstance(log.get("inputs"), dict) else {}
    has_inputs = bool(log_inputs)
    stale = has_inputs and any(log_inputs.get(key) != value for key, value in current_inputs.items())
    payloads = [
        ("business_profile", read_json(out_dir / "business_profile.json", {}) or {}, "business_profile"),
        ("claims", read_json(out_dir / "claims.json", {}) or {}, "claims"),
        ("risks", read_json(out_dir / "risks.json", {}) or {}, "risks"),
        ("events", read_json(out_dir / "events.json", {}) or {}, "events"),
    ]
    invalid_items = []
    formal_count = 0
    for file_key, payload, list_key in payloads:
        for item in payload.get(list_key) or []:
            formal_count += 1
            bad_segments = [sid for sid in item.get("source_segment_ids") or [] if sid not in allowed_segments]
            bad_evidence = [eid for eid in item.get("evidence_ids") or [] if eid not in allowed_evidence]
            if bad_segments or bad_evidence or not item.get("source_segment_ids") or not item.get("evidence_ids"):
                invalid_items.append({
                    "file": file_key,
                    "id": item.get("profile_id") or item.get("claim_id") or item.get("risk_id") or item.get("event_id"),
                    "bad_segments": bad_segments,
                    "bad_evidence": bad_evidence,
                })
    counts = log.get("counts") or {}
    return {
        "company_dir": company_dir.name,
        "report_id": report_id,
        "status": "ready" if (out_dir / "enrichment.json").is_file() and has_inputs and not invalid_items and not stale else "needs_review",
        "counts": counts,
        "formal_count": formal_count,
        "has_inputs": has_inputs,
        "stale": stale,
        "invalid_id_count": len(invalid_items),
        "invalid_items": invalid_items[:20],
        "output_dir": str(out_dir),
    }


def existing_output(company_dir: Path) -> Path:
    report_id = report_id_for(company_dir)
    return company_dir / "semantic" / "llm" / report_id / "enrichment.json"


def run_company(
    root: Path,
    company_dir: Path,
    dry_run: bool = False,
    max_segments: int | None = None,
    skip_existing: bool = False,
    persist_raw: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    cmd = [sys.executable, str(LLM_SCRIPT), "--wiki-root", str(root), "--company", company_dir.name]
    if dry_run:
        cmd.append("--dry-run")
    if max_segments:
        cmd.extend(["--max-segments", str(max_segments)])
    if skip_existing:
        cmd.append("--skip-existing")
    if persist_raw:
        cmd.append("--persist-raw")
    completed = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    result = validate_llm_output(company_dir)
    if completed.returncode != 0:
        failure = {
            "stage": "llm_semantics",
            "company_dir": company_dir.name,
            "returncode": completed.returncode,
            "stderr": completed.stderr[-4000:],
            "stdout": completed.stdout[-4000:],
        }
        return result, failure
    return result, None


def run_market(
    market: str,
    root: Path,
    company: str = "",
    dry_run: bool = False,
    max_segments: int | None = None,
    workers: int = 1,
    skip_existing: bool = False,
    persist_raw: bool = False,
) -> dict[str, Any]:
    companies = [path for path in company_dirs(root, company) if path.is_dir()]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    worker_count = max(1, int(workers or 1))

    if worker_count == 1 or len(companies) <= 1:
        for index, company_dir in enumerate(companies, start=1):
            print(f"[{market}] llm {index}/{len(companies)} {company_dir.name}", file=sys.stderr, flush=True)
            result, failure = run_company(root, company_dir, dry_run, max_segments, skip_existing, persist_raw)
            results.append(result)
            if failure:
                failures.append(failure)
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(run_company, root, company_dir, dry_run, max_segments, skip_existing, persist_raw): company_dir
                for company_dir in companies
            }
            completed_count = 0
            for future in as_completed(future_map):
                company_dir = future_map[future]
                completed_count += 1
                try:
                    result, failure = future.result()
                except Exception as exc:
                    result = {
                        "company_dir": company_dir.name,
                        "report_id": report_id_for(company_dir),
                        "status": "needs_review",
                        "counts": {},
                        "formal_count": 0,
                        "invalid_id_count": 0,
                        "invalid_items": [],
                        "output_dir": str(company_dir / "semantic" / "llm" / report_id_for(company_dir)),
                    }
                    failure = {
                        "stage": "llm_semantics",
                        "company_dir": company_dir.name,
                        "returncode": -1,
                        "stderr": repr(exc),
                        "stdout": "",
                    }
                results.append(result)
                if failure:
                    failures.append(failure)
                print(f"[{market}] llm {completed_count}/{len(companies)} {company_dir.name}", file=sys.stderr, flush=True)

    results.sort(key=lambda item: str(item.get("company_dir") or ""))
    needs_review = [item for item in results if item.get("status") != "ready"]
    manifest = {
        "schema_version": 1,
        "market": market,
        "generated_at": now_iso(),
        "engine": "LLMSemanticProfile",
        "profile": market,
        "wiki_root": str(root),
        "dry_run": dry_run,
        "workers": worker_count,
        "skip_existing": skip_existing,
        "persist_raw": persist_raw,
        "company_count": len(results),
        "ready_count": len(results) - len(needs_review),
        "needs_review_count": len(needs_review),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
    }
    if not dry_run:
        write_json(root / "_meta" / "llm_semantic_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="ALL", help="HK,KR,JP,EU,US,CN or ALL")
    parser.add_argument("--wiki-root", default="", help="Override root for a single market")
    parser.add_argument("--company", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-segments", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--persist-raw", action="store_true")
    parser.add_argument("--allow-failures", action="store_true")
    args = parser.parse_args()

    manifests = []
    exit_code = 0
    for market in markets_from_arg(args.market):
        root = Path(args.wiki_root) if args.wiki_root else MARKET_ROOTS.get(market)
        if root is None:
            print(f"skip unknown market {market}", file=sys.stderr)
            exit_code = 1
            continue
        manifest = run_market(
            market,
            root,
            args.company,
            args.dry_run,
            args.max_segments or None,
            args.workers,
            args.skip_existing,
            args.persist_raw,
        )
        summary = {
            "market": market,
            "company_count": manifest["company_count"],
            "ready_count": manifest["ready_count"],
            "needs_review_count": manifest["needs_review_count"],
            "failure_count": manifest["failure_count"],
            "manifest_path": str(root / "_meta" / "llm_semantic_manifest.json"),
        }
        manifests.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
        if manifest["failure_count"] and not args.allow_failures:
            exit_code = 1
    if len(manifests) > 1 and not args.dry_run:
        write_json(WIKI_ROOT / "_meta" / "llm_semantic_manifest.json", {
            "schema_version": 1,
            "generated_at": now_iso(),
            "markets": manifests,
        })
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
