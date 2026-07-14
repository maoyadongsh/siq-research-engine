"""Result-level metadata and manifest helpers for PDF parser outputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cn_a_share_prospectus_profile import build_profile_analysis

METADATA_SCHEMA_VERSION = "pdf_parser_metadata_v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "pdf_parser_artifact_manifest_v1"
HASH_MANIFEST_SCHEMA_VERSION = "pdf_parser_hash_manifest_v1"
PARSER_CONFIG_VERSION = os.environ.get("SIQ_PDF_PARSE_CONFIG_VERSION", "pdf_parser_v1").strip() or "pdf_parser_v1"

REQUIRED_ARTIFACTS = (
    "result.md",
    "result_complete.md",
    "document_full.json",
    "content_list_enhanced.json",
    "table_index.json",
    "table_relations.json",
    "financial_data.json",
    "financial_checks.json",
    "quality_report.json",
    "content_list.json",
)

OPTIONAL_ARTIFACTS = (
    "middle.json",
    "model_output.json",
    "result_payload_summary.json",
    "corrections.json",
)

HASHED_ARTIFACTS = REQUIRED_ARTIFACTS + OPTIONAL_ARTIFACTS

MARKETS = {"CN", "HK", "EU", "JP", "KR", "US"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_market(value: Any) -> str | None:
    market = str(value or "").strip().upper()
    return market if market in MARKETS else None


def load_submit_config(task: dict[str, Any]) -> dict[str, Any]:
    config = task.get("submit_config")
    if isinstance(config, dict):
        return dict(config)
    raw = task.get("submit_config_json")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def infer_market_from_text(value: Any) -> str | None:
    text = str(value or "")
    for market in MARKETS:
        if f"_{market}_" in text or f"/{market}/" in text:
            return market
    if re.search(r"[\u4e00-\u9fff]", text):
        return "CN"
    return None


def infer_market(task: dict[str, Any], *payloads: dict[str, Any] | None) -> str | None:
    submit_config = load_submit_config(task)
    for value in (submit_config.get("market"), task.get("market")):
        market = normalize_market(value)
        if market:
            return market
    for key in ("filename", "upload_path", "markdown_path", "task_id"):
        market = infer_market_from_text(task.get(key))
        if market:
            return market
    for payload in payloads:
        market = normalize_market((payload or {}).get("market"))
        if market:
            return market
    return None


def parse_filename_metadata(filename: str | None) -> dict[str, Any]:
    name = Path(str(filename or "")).name
    stem = Path(name).stem
    parts = stem.split("_")
    market_idx = None
    market = None
    for idx, part in enumerate(parts):
        normalized = normalize_market(part)
        if normalized:
            market_idx = idx
            market = normalized
            break

    parsed: dict[str, Any] = {"source_file": name}
    if market_idx is None:
        return parsed

    company_slug = "_".join(parts[:market_idx]).strip()
    ticker = parts[market_idx + 1].strip() if len(parts) > market_idx + 1 else ""
    period_end = parts[market_idx + 2].strip() if len(parts) > market_idx + 2 and DATE_RE.match(parts[market_idx + 2]) else ""
    report_type = parts[market_idx + 3].strip() if len(parts) > market_idx + 3 else ""
    disclosure_date = parts[market_idx + 4].strip() if len(parts) > market_idx + 4 and DATE_RE.match(parts[market_idx + 4]) else ""
    source = parts[market_idx + 5].strip() if len(parts) > market_idx + 5 else ""

    parsed.update(
        {
            "market": market,
            "company_name": company_slug.replace("-", " ") if company_slug else "",
            "company_slug": company_slug,
            "ticker": ticker,
            "period_end": period_end,
            "report_type": report_type,
            "disclosure_date": disclosure_date,
            "source": source,
        }
    )
    if period_end[:4].isdigit():
        parsed["fiscal_year"] = int(period_end[:4])
    if market in {"HK", "JP", "KR", "CN"} and ticker:
        parsed["stock_code"] = ticker
    return {key: value for key, value in parsed.items() if value not in ("", None)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(path: Path, base: Path | None = None) -> str:
    if base:
        try:
            return str(path.resolve().relative_to(base.resolve()))
        except ValueError:
            pass
    return str(path)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as infile:
        return json.load(infile)


def artifact_entry(path: Path, name: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "exists": path.exists()}
    if not path.exists():
        return entry

    stat = path.stat()
    entry.update(
        {
            "path": safe_relative(path, repo_root),
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "sha256": sha256_file(path),
        }
    )
    if path.suffix == ".json":
        try:
            payload = read_json(path)
        except Exception as exc:  # pragma: no cover - exact parser error varies
            entry["json_status"] = "invalid"
            entry["json_error"] = str(exc)
        else:
            entry["json_status"] = "ok"
            if isinstance(payload, dict):
                if "schema_version" in payload:
                    entry["schema_version"] = payload.get("schema_version")
                if "rule_version" in payload:
                    entry["rule_version"] = payload.get("rule_version")
                if "market" in payload:
                    entry["market"] = payload.get("market")
            elif isinstance(payload, list):
                entry["item_count"] = len(payload)
    return entry


def load_payload_if_json(result_dir: Path, name: str) -> dict[str, Any] | None:
    path = result_dir / name
    if not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def infer_market_from_result_content(result_dir: Path) -> str | None:
    path = result_dir / "result.md"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return None
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if cjk_count >= 80 and ("年度报告" in text or "财务报告" in text):
        return "CN"
    return None


def build_metadata(task: dict[str, Any], result_dir: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    financial_data = load_payload_if_json(result_dir, "financial_data.json")
    quality_report = load_payload_if_json(result_dir, "quality_report.json")
    filename_meta = parse_filename_metadata(task.get("filename"))
    submit_config = load_submit_config(task)
    market = infer_market(task, financial_data, quality_report, filename_meta) or infer_market_from_result_content(result_dir)
    document_profile = submit_config.get("document_profile")
    source_context = (
        submit_config.get("source_context")
        if isinstance(submit_config.get("source_context"), dict)
        else None
    )
    profile_analysis = build_profile_analysis(document_profile, result_dir)

    metadata: dict[str, Any] = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "generated_at": generated_at,
        "task_id": task.get("task_id"),
        "market": market,
        "filename": task.get("filename"),
        "source_file": filename_meta.get("source_file") or task.get("filename"),
        "upload_path": task.get("upload_path"),
        "markdown_path": task.get("markdown_path"),
        "file_sha256": task.get("file_sha256"),
        "raw_sha256": task.get("file_sha256"),
        "parse_config_hash": task.get("parse_config_hash"),
        "document_profile": document_profile,
        "source_context": source_context,
        "pdf_page_count": task.get("pdf_page_count"),
        "status": task.get("status"),
        "stage": task.get("stage"),
        "created_at": task.get("created_at"),
        "uploaded_at": task.get("uploaded_at"),
        "submitted_at": task.get("submitted_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "parser": {
            "version": submit_config.get("parser_version") or PARSER_CONFIG_VERSION,
            "backend": submit_config.get("backend"),
            "parse_method": submit_config.get("parse_method"),
            "formula_enable": submit_config.get("formula_enable"),
            "table_enable": submit_config.get("table_enable"),
        },
    }
    if profile_analysis:
        metadata["profile_analysis"] = profile_analysis
    metadata.update({key: value for key, value in filename_meta.items() if key not in {"source_file", "market"}})

    for payload in (financial_data, quality_report):
        if not isinstance(payload, dict):
            continue
        for key in (
            "report_kind",
            "report_year",
            "accounting_standard",
            "market_profile",
            "profile_rule_version",
        ):
            if key in payload and key not in metadata:
                metadata[key] = payload.get(key)
    if "fiscal_year" not in metadata and metadata.get("report_year"):
        try:
            metadata["fiscal_year"] = int(metadata["report_year"])
        except (TypeError, ValueError):
            pass
    metadata["market_metadata"] = {
        key: value
        for key, value in {
            "ticker": metadata.get("ticker"),
            "stock_code": metadata.get("stock_code"),
            "source": metadata.get("source"),
            "accounting_standard": metadata.get("accounting_standard"),
            "market_profile": metadata.get("market_profile"),
            "profile_rule_version": metadata.get("profile_rule_version"),
        }.items()
        if value not in ("", None)
    }
    return {key: value for key, value in metadata.items() if value not in ("", None)}


def bundle_sha256(artifacts: dict[str, dict[str, Any]]) -> str | None:
    lines = []
    for name in REQUIRED_ARTIFACTS:
        sha = artifacts.get(name, {}).get("sha256")
        if sha:
            lines.append(f"{name}:{sha}")
    if not lines:
        return None
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def result_content_issues(result_dir: Path) -> list[str]:
    issues: list[str] = []
    enhanced = load_payload_if_json(result_dir, "content_list_enhanced.json") or {}
    financial_data = load_payload_if_json(result_dir, "financial_data.json") or {}
    document_full = load_payload_if_json(result_dir, "document_full.json") or {}
    table_relations = load_payload_if_json(result_dir, "table_relations.json") or {}
    table_index_path = result_dir / "table_index.json"
    table_index: Any = None
    try:
        table_index = read_json(table_index_path)
    except Exception:
        table_index = None

    if isinstance(enhanced, dict):
        table_count = int(enhanced.get("table_count") or len(enhanced.get("tables") or []) or 0)
        if table_count <= 0:
            issues.append("content_list_enhanced.tables_empty")
        if len(enhanced.get("pages") or []) <= 0:
            issues.append("content_list_enhanced.pages_empty")
    if not isinstance(table_index, list) or len(table_index) <= 0:
        issues.append("table_index.empty")
    if isinstance(financial_data, dict) and len(financial_data.get("statements") or []) <= 0:
        issues.append("financial_data.statements_empty")
    if isinstance(document_full, dict):
        markdown_payload = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
        if len(str(markdown_payload.get("content") or "")) <= 1000:
            issues.append("document_full.markdown_empty")
    candidate_count = 0
    if isinstance(table_relations, dict):
        candidate_count = int(table_relations.get("candidate_table_count") or table_relations.get("physical_table_count") or 0)
    if candidate_count <= 0:
        issues.append("table_relations.candidates_empty")
    try:
        if len((result_dir / "result_complete.md").read_text(encoding="utf-8", errors="ignore")) <= 1000:
            issues.append("result_complete.empty")
    except OSError:
        pass
    return issues


def build_artifact_manifest(
    task: dict[str, Any],
    result_dir: Path,
    metadata: dict[str, Any],
    *,
    repo_root: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or metadata.get("generated_at") or utc_now_iso()
    artifacts = {
        name: artifact_entry(result_dir / name, name, repo_root=repo_root)
        for name in HASHED_ARTIFACTS
    }
    missing = [name for name in REQUIRED_ARTIFACTS if not artifacts[name].get("exists")]
    invalid_json = [
        name
        for name, entry in artifacts.items()
        if entry.get("exists") and entry.get("json_status") == "invalid"
    ]
    content_issues = result_content_issues(result_dir)
    ready = not missing and not invalid_json and not content_issues
    profile_analysis = metadata.get("profile_analysis") if isinstance(metadata.get("profile_analysis"), dict) else None
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at,
        "task_id": task.get("task_id"),
        "identity": {
            "parser_version": (metadata.get("parser") or {}).get("version"),
            "market": metadata.get("market"),
            "document_profile": metadata.get("document_profile"),
            "raw_sha256": metadata.get("raw_sha256"),
            "parse_config_hash": metadata.get("parse_config_hash"),
            "source_context": metadata.get("source_context"),
        },
        "result_dir": safe_relative(result_dir, repo_root),
        "metadata_file": "metadata.json",
        "storage_policy": {
            "wiki": True,
            "full_parse_archive": True,
            "note": "Parser result is the canonical source for PDF-market Wiki rebuilds.",
        },
        "core": {
            "status": "ready" if ready else "incomplete",
            "ready": ready,
            "ready_count": sum(1 for name in REQUIRED_ARTIFACTS if artifacts[name].get("exists")),
            "total": len(REQUIRED_ARTIFACTS),
            "missing": missing,
            "invalid_json": invalid_json,
            "content_issues": content_issues,
            "bundle_sha256": bundle_sha256(artifacts),
        },
        "metadata": {
            "market": metadata.get("market"),
            "company_name": metadata.get("company_name"),
            "ticker": metadata.get("ticker"),
            "stock_code": metadata.get("stock_code"),
            "fiscal_year": metadata.get("fiscal_year"),
            "period_end": metadata.get("period_end"),
            "report_type": metadata.get("report_type"),
            "source": metadata.get("source"),
            "document_profile": metadata.get("document_profile"),
            "parse_config_hash": metadata.get("parse_config_hash"),
        },
        "capabilities": (profile_analysis or {}).get("capabilities", {}),
        "quality": {
            "document_profile": metadata.get("document_profile"),
            "status": (profile_analysis or {}).get("quality_status"),
            "issues": (profile_analysis or {}).get("issues", []),
            "chapter_coverage": (profile_analysis or {}).get("chapter_coverage"),
            "reporting_period_check": (profile_analysis or {}).get("reporting_period_check"),
        },
        "artifacts": artifacts,
    }


def build_hash_manifest(
    task: dict[str, Any],
    result_dir: Path,
    artifact_manifest: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or artifact_manifest.get("generated_at") or utc_now_iso()
    entries = []
    for name in HASHED_ARTIFACTS:
        artifact = (artifact_manifest.get("artifacts") or {}).get(name) or {}
        if artifact.get("exists"):
            entries.append(
                {
                    "name": name,
                    "sha256": artifact.get("sha256"),
                    "size_bytes": artifact.get("size_bytes"),
                }
            )
    return {
        "schema_version": HASH_MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at,
        "task_id": task.get("task_id"),
        "identity": {
            "parser_version": ((artifact_manifest.get("identity") or {}).get("parser_version")),
            "market": ((artifact_manifest.get("identity") or {}).get("market")),
            "document_profile": ((artifact_manifest.get("identity") or {}).get("document_profile")),
            "raw_sha256": ((artifact_manifest.get("identity") or {}).get("raw_sha256")),
            "parse_config_hash": ((artifact_manifest.get("identity") or {}).get("parse_config_hash")),
        },
        "result_dir": str(result_dir),
        "algorithm": "sha256",
        "bundle_sha256": (artifact_manifest.get("core") or {}).get("bundle_sha256"),
        "entries": entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp_path.open("w", encoding="utf-8") as outfile:
            json.dump(payload, outfile, ensure_ascii=False, indent=2)
            outfile.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def build_result_contract(
    task: dict[str, Any],
    result_dir: Path,
    *,
    repo_root: Path | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    generated_at = generated_at or utc_now_iso()
    metadata = build_metadata(task, result_dir, generated_at=generated_at)
    artifact_manifest = build_artifact_manifest(
        task,
        result_dir,
        metadata,
        repo_root=repo_root,
        generated_at=generated_at,
    )
    hash_manifest = build_hash_manifest(task, result_dir, artifact_manifest, generated_at=generated_at)
    return metadata, artifact_manifest, hash_manifest


def write_result_contract(
    task: dict[str, Any],
    result_dir: Path,
    *,
    repo_root: Path | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metadata, artifact_manifest, hash_manifest = build_result_contract(
        task,
        result_dir,
        repo_root=repo_root,
        generated_at=generated_at,
    )
    write_json(result_dir / "metadata.json", metadata)
    write_json(result_dir / "artifact_manifest.json", artifact_manifest)
    write_json(result_dir / "hash_manifest.json", hash_manifest)
    return metadata, artifact_manifest, hash_manifest
