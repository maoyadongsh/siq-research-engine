#!/usr/bin/env python3
"""Audit and quarantine duplicate PDF parser result directories.

The market report downloader already de-duplicates identical downloads by
content hash. This script covers the later case where the same PDF has already
been parsed into multiple data/pdf-parser/results/<task_id> directories.

Default mode is dry-run. Use --apply --mode quarantine to move duplicates out
of the active results directory without destroying the archived parse package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = REPO_ROOT / "data" / "pdf-parser" / "results"
DEFAULT_TASK_DB = REPO_ROOT / "data" / "pdf-parser" / "db" / "tasks.db"
DEFAULT_REPORTS_DIR = REPO_ROOT / "data" / "pdf-parser" / "reports"
DEFAULT_QUARANTINE_ROOT = DEFAULT_RESULTS_DIR / "_deduped"

PDF_MARKETS = {"CN", "HK", "EU", "JP", "KR"}

FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>CN|HK|EU|JP|KR|US)_"
    r"(?P<ticker>[^_]+)_"
    r"(?P<period_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+?)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})"
    r"(?:\.[^.]+)?$",
    re.IGNORECASE,
)

REPORT_KIND_SLUGS = {
    "annual_report": "annual",
    "eu_annual_report": "annual",
    "eu_esef_annual_report": "annual",
    "有価証券報告書": "annual",
    "business_report": "annual",
    "年报": "annual",
    "年報": "annual",
    "annual": "annual",
    "annual_report_pdf": "annual",
    "interim_report": "interim",
    "half_year_report": "interim",
    "quarterly_report": "quarterly",
}

SOURCE_RANK = {
    "cninfo": 520,
    "sse": 500,
    "szse": 500,
    "hkex": 520,
    "issuer_annual_report": 520,
    "issuer": 500,
    "exchange_regulatory_news": 470,
    "six_direct": 460,
    "eu_direct": 440,
    "dart": 520,
    "dart_public": 510,
    "edinet": 520,
    "manual": 300,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return path.as_posix()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def safe_key(value: Any, fallback: str = "unknown") -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or fallback


def parse_filename(filename: Any) -> dict[str, str]:
    name = Path(str(filename or "")).name
    stem = re.sub(r"\.[^.]+$", "", name)
    match = FILENAME_RE.match(stem)
    if not match:
        return {}
    return {key: clean_text(value) for key, value in match.groupdict().items()}


def report_kind_slug(*values: Any) -> str:
    for value in values:
        key = clean_text(value)
        if not key:
            continue
        if key in REPORT_KIND_SLUGS:
            return REPORT_KIND_SLUGS[key]
        lower = key.lower()
        if lower in REPORT_KIND_SLUGS:
            return REPORT_KIND_SLUGS[lower]
        if "annual" in lower or "年报" in key or "年報" in key or "有価証券報告書" in key:
            return "annual"
    return "report"


def load_task_file_hashes(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT task_id, file_sha256 FROM tasks WHERE file_sha256 IS NOT NULL").fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {
        str(row["task_id"]): str(row["file_sha256"]).strip().lower()
        for row in rows
        if row["task_id"] and row["file_sha256"]
    }


def source_content_hash_from_sidecar(upload_path: Any) -> str | None:
    text = str(upload_path or "").strip()
    if not text:
        return None
    path = Path(text)
    sidecar = path.with_suffix(path.suffix + ".metadata.json")
    data = read_json(sidecar, {}) if sidecar.exists() else {}
    candidates = [
        data.get("content_sha256"),
        (data.get("downloaded_file") or {}).get("content_sha256") if isinstance(data.get("downloaded_file"), dict) else None,
        (data.get("source_manifest") or {}).get("content_sha256") if isinstance(data.get("source_manifest"), dict) else None,
        (data.get("download") or {}).get("content_sha256") if isinstance(data.get("download"), dict) else None,
    ]
    for candidate in candidates:
        digest = clean_text(candidate).lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    for candidate in candidates:
        text_value = clean_text(candidate).lower()
        match = re.search(r"sha256:([0-9a-f]{64})", text_value)
        if match:
            return match.group(1)
    return None


def source_content_hash(
    result_dir: Path,
    metadata: dict[str, Any],
    artifact_manifest: dict[str, Any],
    task_hashes: dict[str, str],
    *,
    hash_upload_files: bool,
) -> tuple[str | None, str | None]:
    task_id = result_dir.name
    candidates = [
        metadata.get("file_sha256"),
        metadata.get("content_sha256"),
        task_hashes.get(task_id),
        source_content_hash_from_sidecar(metadata.get("upload_path")),
    ]
    manifest_meta = artifact_manifest.get("metadata") if isinstance(artifact_manifest.get("metadata"), dict) else {}
    candidates.extend([manifest_meta.get("file_sha256"), manifest_meta.get("content_sha256")])
    for candidate in candidates:
        digest = clean_text(candidate).lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest, "source_pdf_sha256"
        match = re.search(r"sha256:([0-9a-f]{64})", digest)
        if match:
            return match.group(1), "source_pdf_sha256"
    if hash_upload_files:
        upload_path = Path(str(metadata.get("upload_path") or ""))
        digest = sha256_file(upload_path)
        if digest:
            return digest, "computed_upload_sha256"
    return None, None


def count_financial_values(financial_data: dict[str, Any]) -> int:
    count = 0
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item in statement.get("items") or []:
            if not isinstance(item, dict):
                continue
            values = item.get("values")
            if isinstance(values, dict):
                count += sum(1 for value in values.values() if value not in (None, ""))
            elif item.get("value") not in (None, ""):
                count += 1
    return count


def candidate_score(
    metadata: dict[str, Any],
    artifact_manifest: dict[str, Any],
    financial_data: dict[str, Any],
    financial_checks: dict[str, Any],
    quality_report: dict[str, Any],
    parsed: dict[str, str],
) -> tuple[int, str, str]:
    core = artifact_manifest.get("core") if isinstance(artifact_manifest.get("core"), dict) else {}
    source_id = parsed.get("source_id") or metadata.get("source_id") or metadata.get("source") or ""
    financial_status = clean_text(financial_checks.get("overall_status")).lower()
    score = 0
    score += SOURCE_RANK.get(str(source_id), 0)
    score += 5000 if core.get("ready") is True or core.get("status") == "ready" else 0
    score += int(core.get("ready_count") or 0) * 100
    score += 900 if financial_status == "pass" else 600 if financial_status == "warning" else 0
    score += len(financial_data.get("statements") or []) * 120
    score += count_financial_values(financial_data)
    score += min(int(quality_report.get("table_count") or 0), 2000)
    completed_at = clean_text(metadata.get("completed_at"))
    task_id = clean_text(metadata.get("task_id"))
    return score, completed_at, task_id


def inspect_result_dir(
    result_dir: Path,
    task_hashes: dict[str, str],
    *,
    hash_upload_files: bool,
) -> dict[str, Any] | None:
    if not result_dir.is_dir() or result_dir.name.startswith(".") or result_dir.name.startswith("_"):
        return None
    metadata = read_json(result_dir / "metadata.json", {})
    if not isinstance(metadata, dict) or not metadata:
        return None
    financial_data = read_json(result_dir / "financial_data.json", {})
    if not isinstance(financial_data, dict):
        financial_data = {}
    artifact_manifest = read_json(result_dir / "artifact_manifest.json", {})
    if not isinstance(artifact_manifest, dict):
        artifact_manifest = {}
    financial_checks = read_json(result_dir / "financial_checks.json", {})
    if not isinstance(financial_checks, dict):
        financial_checks = {}
    quality_report = read_json(result_dir / "quality_report.json", {})
    if not isinstance(quality_report, dict):
        quality_report = {}

    filename = (
        metadata.get("filename")
        or metadata.get("source_file")
        or financial_data.get("filename")
        or clean_text(result_dir.name)
    )
    parsed = parse_filename(filename)
    market = clean_text(metadata.get("market") or financial_data.get("market") or parsed.get("market")).upper()
    if market not in PDF_MARKETS:
        return None
    ticker = clean_text(
        metadata.get("ticker")
        or metadata.get("stock_code")
        or financial_data.get("ticker")
        or parsed.get("ticker")
    ).upper()
    period_end = clean_text(metadata.get("period_end") or financial_data.get("period_end") or parsed.get("period_end"))
    report_kind = report_kind_slug(metadata.get("report_kind"), metadata.get("report_type"), parsed.get("report_type"))
    source_id = clean_text(parsed.get("source_id") or metadata.get("source_id") or metadata.get("source"))
    url_hash = clean_text(parsed.get("url_hash")).lower()
    file_hash, file_hash_source = source_content_hash(
        result_dir,
        metadata,
        artifact_manifest,
        task_hashes,
        hash_upload_files=hash_upload_files,
    )
    identity_key = "|".join([market, ticker or safe_key(metadata.get("company_name")), period_end, report_kind])
    if file_hash:
        exact_key = "|".join(["source_pdf_sha256", identity_key, file_hash])
        dedupe_basis = "source_pdf_sha256"
    elif url_hash:
        exact_key = "|".join(["url_hash", identity_key, url_hash])
        dedupe_basis = "url_hash"
    else:
        exact_key = "|".join(["filename", identity_key, safe_key(filename)])
        dedupe_basis = "filename"
    content_key = ""
    if file_hash:
        content_key = "|".join(["source_pdf_sha256", market, period_end, report_kind, file_hash])
    elif url_hash:
        content_key = "|".join(["url_hash", market, period_end, report_kind, url_hash])
    return {
        "task_id": result_dir.name,
        "result_dir": result_dir,
        "result_dir_rel": rel(result_dir),
        "filename": clean_text(filename),
        "market": market,
        "ticker": ticker,
        "company_name": clean_text(metadata.get("company_name") or parsed.get("company")),
        "period_end": period_end,
        "report_kind": report_kind,
        "source_id": source_id,
        "url_hash": url_hash,
        "file_hash": file_hash,
        "file_hash_source": file_hash_source,
        "dedupe_basis": dedupe_basis,
        "exact_key": exact_key,
        "identity_key": identity_key,
        "content_key": content_key,
        "score_key": candidate_score(metadata, artifact_manifest, financial_data, financial_checks, quality_report, parsed),
        "score": candidate_score(metadata, artifact_manifest, financial_data, financial_checks, quality_report, parsed)[0],
        "completed_at": clean_text(metadata.get("completed_at")),
        "artifact_ready": bool((artifact_manifest.get("core") or {}).get("ready")),
        "artifact_status": (artifact_manifest.get("core") or {}).get("status"),
        "financial_status": financial_checks.get("overall_status"),
        "statement_count": len(financial_data.get("statements") or []),
        "financial_value_count": count_financial_values(financial_data),
        "table_count": quality_report.get("table_count"),
    }


def serialize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {"result_dir", "score_key"}
    }


def build_plan(
    results_dir: Path,
    *,
    markets: set[str],
    task_db: Path,
    hash_upload_files: bool,
) -> dict[str, Any]:
    task_hashes = load_task_file_hashes(task_db)
    rows: list[dict[str, Any]] = []
    ignored = Counter()
    for result_dir in sorted(results_dir.iterdir() if results_dir.exists() else []):
        row = inspect_result_dir(result_dir, task_hashes, hash_upload_files=hash_upload_files)
        if not row:
            ignored["not_pdf_market_or_missing_metadata"] += 1
            continue
        if markets and row["market"] not in markets:
            ignored["market_filtered"] += 1
            continue
        rows.append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["exact_key"]].append(row)

    duplicate_groups: list[dict[str, Any]] = []
    duplicate_task_ids: set[str] = set()
    kept_task_ids: set[str] = set()
    for exact_key, candidates in sorted(groups.items()):
        if len(candidates) < 2:
            kept_task_ids.add(candidates[0]["task_id"])
            continue
        candidates.sort(key=lambda item: item["score_key"], reverse=True)
        keep = candidates[0]
        kept_task_ids.add(keep["task_id"])
        duplicates = candidates[1:]
        duplicate_task_ids.update(item["task_id"] for item in duplicates)
        duplicate_groups.append(
            {
                "exact_key": exact_key,
                "dedupe_basis": keep["dedupe_basis"],
                "identity_key": keep["identity_key"],
                "keep": serialize_candidate(keep),
                "duplicates": [
                    {
                        **serialize_candidate(item),
                        "dedupe_action": "quarantine_or_delete_on_apply",
                        "reason": f"duplicate_{keep['dedupe_basis']}_same_market_ticker_period_kind",
                    }
                    for item in duplicates
                ],
            }
        )

    content_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["content_key"]:
            content_groups[row["content_key"]].append(row)
    cross_identity_groups = []
    for content_key, candidates in sorted(content_groups.items()):
        identities = sorted({item["identity_key"] for item in candidates})
        if len(identities) <= 1:
            continue
        candidates.sort(key=lambda item: item["score_key"], reverse=True)
        cross_identity_groups.append(
            {
                "content_key": content_key,
                "identity_count": len(identities),
                "identities": identities,
                "recommended_action": "review_before_delete_cross_ticker_or_cross_company_content",
                "candidates": [serialize_candidate(item) for item in candidates],
            }
        )

    summary = {
        "candidate_count": len(rows),
        "market_counts": dict(sorted(Counter(row["market"] for row in rows).items())),
        "dedupe_basis_counts": dict(sorted(Counter(row["dedupe_basis"] for row in rows).items())),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_task_count": len(duplicate_task_ids),
        "cross_identity_duplicate_group_count": len(cross_identity_groups),
        "ignored": dict(sorted(ignored.items())),
    }
    return {
        "schema_version": "pdf_parser_result_dedupe_plan_v1",
        "generated_at": now_iso(),
        "source_results_dir": rel(results_dir),
        "task_db": rel(task_db),
        "markets": sorted(markets) if markets else sorted(PDF_MARKETS),
        "hash_upload_files": hash_upload_files,
        "summary": summary,
        "duplicate_groups": duplicate_groups,
        "cross_identity_duplicate_groups": cross_identity_groups,
        "duplicate_task_ids": sorted(duplicate_task_ids),
        "kept_task_ids": sorted(kept_task_ids),
    }


def apply_plan(plan: dict[str, Any], *, mode: str, quarantine_root: Path) -> list[dict[str, Any]]:
    run_id = timestamp()
    operations = []
    for group in plan.get("duplicate_groups") or []:
        for duplicate in group.get("duplicates") or []:
            source = REPO_ROOT / duplicate["result_dir_rel"]
            operation = {
                "task_id": duplicate.get("task_id"),
                "source": rel(source),
                "mode": mode,
                "status": "pending",
            }
            if not source.exists():
                operation["status"] = "missing_source"
                operations.append(operation)
                continue
            if mode == "delete":
                shutil.rmtree(source)
                operation["status"] = "deleted"
            elif mode == "quarantine":
                market = duplicate.get("market") or "UNKNOWN"
                destination = quarantine_root / run_id / str(market) / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                operation["destination"] = rel(destination)
                operation["status"] = "quarantined"
            else:
                raise ValueError(f"Unsupported mode: {mode}")
            operations.append(operation)
    return operations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--task-db", type=Path, default=DEFAULT_TASK_DB)
    parser.add_argument("--market", action="append", help="Limit to one market. Repeatable: HK, EU, JP, KR, CN.")
    parser.add_argument("--hash-upload-files", action="store_true", help="Hash upload_path PDFs when DB/sidecar hashes are missing.")
    parser.add_argument("--apply", action="store_true", help="Apply duplicate cleanup. Defaults to dry-run.")
    parser.add_argument("--mode", choices=("quarantine", "delete"), default="quarantine")
    parser.add_argument("--quarantine-root", type=Path, default=DEFAULT_QUARANTINE_ROOT)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--write-latest", action="store_true", help="Also write data/pdf-parser/reports/parser_result_dedupe_latest.json.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markets = {str(item).upper() for item in (args.market or [])}
    unknown = markets - PDF_MARKETS
    if unknown:
        raise SystemExit(f"Unsupported PDF market(s): {', '.join(sorted(unknown))}")
    plan = build_plan(
        args.results_dir.resolve(),
        markets=markets,
        task_db=args.task_db.resolve(),
        hash_upload_files=args.hash_upload_files,
    )
    plan["dry_run"] = not args.apply
    plan["mode"] = args.mode if args.apply else "dry_run"
    if args.apply:
        plan["operations"] = apply_plan(plan, mode=args.mode, quarantine_root=args.quarantine_root.resolve())
        plan["summary"]["operations"] = dict(sorted(Counter(item["status"] for item in plan["operations"]).items()))

    output_path = args.json_output
    if not output_path:
        output_path = DEFAULT_REPORTS_DIR / f"parser_result_dedupe_{timestamp()}.json"
    write_json(output_path, plan)
    if args.write_latest or args.apply:
        write_json(DEFAULT_REPORTS_DIR / "parser_result_dedupe_latest.json", plan)

    print(
        json.dumps(
            {
                "output": rel(output_path.resolve()),
                "dry_run": plan["dry_run"],
                "mode": plan["mode"],
                "summary": plan["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
