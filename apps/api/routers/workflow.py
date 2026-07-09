import json
import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from services.command_runner import run_command as run_subprocess_command
from services.llm_settings import load_llm_settings
from services.hermes_client import hermes_profile_config
from services.hermes_model_control import infer_model_mode, set_all_profile_model_modes
from services.path_config import (
    DB_CONFIG_PY,
    DB_IMPORT_SCRIPT,
    DOCUMENT_DB_IMPORT_SCRIPT,
    DOCUMENT_PARSER_RESULTS_ROOT,
    DOCUMENT_WIKI_ROOT,
    PDF_RESULT_ROOT_CANDIDATES,
    PDF_TASK_DB_PATH,
    PDF_RESULTS_ROOT,
    WIKI_ROOT,
    REPO_ROOT,
    WIKISET_ROOT,
    WORKFLOW_JOB_STORE,
)
from services.security_utils import validate_table_name
from services import document_workflow_service
from services.workflow_job_service import (
    create_workflow_job,
    load_workflow_jobs,
    persist_workflow_jobs,
    record_workflow_job_step,
    update_workflow_job,
)

router = APIRouter(prefix="/workflow", tags=["workflow"])

WIKI_REBUILD_SCRIPT = Path(os.environ.get("WIKI_REBUILD_SCRIPT", str(WIKISET_ROOT / "rebuild_wiki_v2.py"))).resolve()
SEMANTIC_SCRIPT = Path(os.environ.get("SEMANTIC_SCRIPT", str(WIKISET_ROOT / "extract_company_semantics.py"))).resolve()
OBSIDIAN_SCRIPT = Path(os.environ.get("OBSIDIAN_SCRIPT", str(WIKISET_ROOT / "generate_obsidian_graph.py"))).resolve()
LLM_SEMANTIC_SCRIPT = Path(os.environ.get("LLM_SEMANTIC_SCRIPT", str(WIKISET_ROOT / "llm_semantic_enrichment.py"))).resolve()
WIKI_NAMING_REPAIR_SCRIPT = Path(os.environ.get("WIKI_NAMING_REPAIR_SCRIPT", str(WIKISET_ROOT / "repair_wiki_naming.py"))).resolve()
WIKI_NAMING_VALIDATE_SCRIPT = Path(os.environ.get("WIKI_NAMING_VALIDATE_SCRIPT", str(WIKISET_ROOT / "validate_wiki_naming.py"))).resolve()
MARKET_WIKISET_ROOT = REPO_ROOT / "scripts" / "wiki" / "market_wikiset"
PDF_MARKET_CODES = {"HK", "KR", "JP", "EU"}
PDF_MARKET_WIKI_INGEST_SCRIPTS = {
    "HK": MARKET_WIKISET_ROOT / "ingest_hk_pdf_wiki.py",
    "KR": MARKET_WIKISET_ROOT / "ingest_kr_pdf_wiki.py",
    "JP": MARKET_WIKISET_ROOT / "ingest_jp_pdf_wiki.py",
    "EU": MARKET_WIKISET_ROOT / "ingest_eu_pdf_wiki.py",
}

CORE_INPUT_ARTIFACTS = [
    "result.md",
    "result_complete.md",
    "document_full.json",
    "content_list_enhanced.json",
    "financial_data.json",
    "financial_checks.json",
    "quality_report.json",
    "table_relations.json",
    "table_index.json",
    "artifact_manifest.json",
    "hash_manifest.json",
    "metadata.json",
]
STABLE_BUNDLE_ARTIFACTS = [
    name
    for name in CORE_INPUT_ARTIFACTS
    if name not in {"artifact_manifest.json", "hash_manifest.json"}
]
ARTIFACT_SCHEMA_EXPECTATIONS = {
    "document_full.json": 3,
    "content_list_enhanced.json": 10,
    "quality_report.json": 11,
    "financial_data.json": 13,
    "financial_checks.json": 12,
}
FINANCIAL_RULE_VERSION = "financial_rules_v14"
ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"
DOCUMENT_PACKAGE_MANIFEST_NAME = "manifest.json"
DOCUMENT_CORE_ARTIFACTS = [
    "manifest.json",
    "document.md",
    "document_full.json",
    "blocks.json",
    "tables.json",
    "logical_tables.json",
    "table_relations.json",
    "figures.json",
    "figure_index.json",
    "comparison_map.json",
    "source_map.json",
    "quality_report.json",
]
DOCUMENT_OPTIONAL_ARTIFACTS = [
    "layout_blocks.json",
    "reading_order.json",
    "table_merge_corrections.json",
    "extraction/schema.json",
    "extraction/result.json",
    "extraction/evidence_map.json",
    "extraction/validation_report.json",
]
DOCUMENT_WIKI_LIGHTWEIGHT_ARTIFACTS = [
    "manifest.json",
    "document.md",
    "quality_report.json",
    "source_map.json",
]
DOCUMENT_WIKI_RETAINED_DIRS = [
    "raw/original",
    "images/original",
]
DOCUMENT_CHUNK_SCRIPT = Path(os.environ.get(
    "SIQ_DOCUMENT_CHUNK_SCRIPT",
    str(REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_document_chunks.py"),
)).resolve()
LLM_SEMANTIC_ENABLED = os.environ.get("LLM_SEMANTIC_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
LLM_SEMANTIC_REQUIRED = os.environ.get("LLM_SEMANTIC_REQUIRED", "false").lower() not in {"0", "false", "no", "off"}
LLM_SEMANTIC_TIMEOUT = int(os.environ.get("LLM_SEMANTIC_TIMEOUT", "900"))

_job_lock = threading.Lock()


def _load_workflow_jobs() -> dict[str, dict]:
    return load_workflow_jobs(WORKFLOW_JOB_STORE)


def _persist_workflow_jobs_locked() -> None:
    persist_workflow_jobs(WORKFLOW_JOB_STORE, _workflow_jobs)


_workflow_jobs: dict[str, dict] = _load_workflow_jobs()

_wiki_builder = None
_wiki_builder_mtime = None


def _safe_task_id(task_id: str) -> str:
    value = task_id.strip()
    if not value or any(ch in value for ch in "/\\.."):
        raise HTTPException(400, "Invalid task_id")
    return value


def _safe_document_collection(value: str | None = None) -> str:
    raw = str(value or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw).strip(".-_")
    if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise HTTPException(400, "Invalid document collection")
    return raw[:80]


def _document_key_from_manifest(task_id: str, manifest: dict) -> str:
    filename = str(manifest.get("filename") or task_id)
    stem = Path(filename).stem or task_id
    slug = re.sub(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+", "-", stem).strip(".-_")
    if not slug:
        slug = task_id
    return f"{slug[:80]}-{task_id[:8]}"


def _find_document_result_dir(task_id: str) -> Path | None:
    task_id = _safe_task_id(task_id)
    candidates = [
        DOCUMENT_PARSER_RESULTS_ROOT / task_id,
        Path(os.environ.get("SIQ_DOCUMENT_PARSER_RESULTS_FALLBACK", "")) / task_id if os.environ.get("SIQ_DOCUMENT_PARSER_RESULTS_FALLBACK") else None,
    ]
    for path in candidates:
        if path is None:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and (resolved / "manifest.json").is_file() and (resolved / "document.md").is_file():
            return resolved
    return None


def _document_package_dir(task_id: str, collection: str | None = None, manifest: dict | None = None) -> Path:
    return document_workflow_service.document_package_dir(
        task_id,
        collection,
        manifest,
        safe_task_id=_safe_task_id,
        safe_collection=_safe_document_collection,
        find_result_dir=_find_document_result_dir,
        read_json=_read_json,
        wiki_root=DOCUMENT_WIKI_ROOT,
        document_key_from_manifest=_document_key_from_manifest,
    )


def _find_task_document_full(task_id: str) -> Path | None:
    task_id = _safe_task_id(task_id)
    candidates = [root / task_id / "document_full.json" for root in PDF_RESULT_ROOT_CANDIDATES]
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _find_task_result_dir(task_id: str) -> Path | None:
    task_id = _safe_task_id(task_id)
    candidates = [root / task_id for root in PDF_RESULT_ROOT_CANDIDATES]
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and (resolved / "document_full.json").is_file():
            return resolved
    return None


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _infer_task_market(task_id: str) -> str:
    task_id = _safe_task_id(task_id)
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        return ""
    payloads = [
        _read_json(result_dir / "metadata.json", {}) or {},
        (_read_json(result_dir / ARTIFACT_MANIFEST_NAME, {}) or {}).get("metadata") or {},
        _read_json(result_dir / "financial_data.json", {}) or {},
        _read_json(result_dir / "quality_report.json", {}) or {},
    ]
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("market", "market_profile"):
            value = str(payload.get(key) or "").strip().upper()
            if value:
                return value
        nested = payload.get("market_metadata")
        if isinstance(nested, dict):
            value = str(nested.get("market") or nested.get("market_profile") or "").strip().upper()
            if value:
                return value
    filename = " ".join(
        str(payload.get("filename") or payload.get("source_file") or "")
        for payload in payloads
        if isinstance(payload, dict)
    )
    for market in ("HK", "KR", "JP", "EU", "US", "CN"):
        if f"_{market}_" in filename:
            return market
    return ""


def _wiki_root_for_market(market: str) -> Path:
    market_code = str(market or "").upper()
    if market_code in PDF_MARKET_CODES:
        return WIKI_ROOT / market_code.lower()
    return WIKI_ROOT


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_file_if_exists(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_tree_contents(src: Path, dst: Path) -> int:
    if not src.is_dir():
        return 0
    copied = 0
    src_root = src.resolve()
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(src_root)
        except ValueError:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_artifact_meta(path: Path) -> dict:
    if not path.is_file() or path.suffix.lower() != ".json":
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "jsonStatus": "invalid",
            "jsonError": str(exc)[:500],
        }
    if isinstance(data, dict):
        meta = {"jsonStatus": "ok"}
        if data.get("schema_version") is not None:
            meta["schemaVersion"] = data.get("schema_version")
        if data.get("rule_version") is not None:
            meta["ruleVersion"] = data.get("rule_version")
        return meta
    if isinstance(data, list):
        return {"jsonStatus": "ok", "itemCount": len(data)}
    return {"jsonStatus": "ok"}


def _artifact_file_info(result_dir: Path, name: str) -> dict:
    path = result_dir / name
    exists = path.is_file()
    payload = {
        "name": name,
        "path": str(path),
        "exists": exists,
        "sizeBytes": path.stat().st_size if exists else 0,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if exists else None,
        "sha256": _sha256_file(path) if exists else None,
    }
    payload.update(_json_artifact_meta(path))
    expected_schema = ARTIFACT_SCHEMA_EXPECTATIONS.get(name)
    if expected_schema is not None:
        payload["expectedSchemaVersion"] = expected_schema
        payload["schemaStatus"] = "ok" if payload.get("schemaVersion") == expected_schema else "mismatch"
    if name in {"financial_data.json", "financial_checks.json"}:
        payload["expectedRuleVersion"] = FINANCIAL_RULE_VERSION
        payload["ruleStatus"] = "ok" if payload.get("ruleVersion") == FINANCIAL_RULE_VERSION else "mismatch"
    return payload


def _artifact_bundle_status(task_id: str, *, write_manifest: bool = False) -> dict:
    task_id = _safe_task_id(task_id)
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        return {
            "status": "missing",
            "taskId": task_id,
            "resultDir": "",
            "ready": False,
            "readyCount": 0,
            "total": len(CORE_INPUT_ARTIFACTS),
            "missing": list(CORE_INPUT_ARTIFACTS),
            "artifacts": {},
            "warnings": ["未找到解析产物目录"],
            "message": "未找到解析产物目录",
        }

    artifacts = {name: _artifact_file_info(result_dir, name) for name in CORE_INPUT_ARTIFACTS}
    missing = [name for name, info in artifacts.items() if not info["exists"]]
    schema_mismatches = [
        name for name, info in artifacts.items()
        if info.get("schemaStatus") == "mismatch"
    ]
    rule_mismatches = [
        name for name, info in artifacts.items()
        if info.get("ruleStatus") == "mismatch"
    ]
    invalid_json = [
        name for name, info in artifacts.items()
        if info.get("jsonStatus") == "invalid"
    ]
    digest_payload = {
        name: {
            "sha256": info.get("sha256"),
            "sizeBytes": info.get("sizeBytes"),
            "schemaVersion": info.get("schemaVersion"),
            "ruleVersion": info.get("ruleVersion"),
        }
        for name, info in artifacts.items()
        if name in STABLE_BUNDLE_ARTIFACTS
    }
    bundle_sha = _sha256_text(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True))
    warnings = []
    if schema_mismatches:
        warnings.append("schema_mismatch")
    if rule_mismatches:
        warnings.append("financial_rule_mismatch")
    if invalid_json:
        warnings.append("json_invalid")
    ready = not missing and not invalid_json
    status = "missing" if missing else ("invalid" if invalid_json else ("needs_review" if warnings else "ready"))
    manifest = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "task_id": task_id,
        "result_dir": str(result_dir),
        "storage_policy": {
            "wiki": "lightweight_manifest_and_report_assets",
            "full_parse_archive": "postgresql_and_pdf2md_results",
            "note": "Wiki 保留可读报告、公司级索引和本 manifest；PDF 解析全量信息以 document_full 入库并保留在 results 目录。",
        },
        "core": {
            "status": status,
            "ready": ready,
            "ready_count": len(CORE_INPUT_ARTIFACTS) - len(missing),
            "total": len(CORE_INPUT_ARTIFACTS),
            "missing": missing,
            "bundle_sha256": bundle_sha,
        },
        "artifacts": artifacts,
        "checks": {
            "schema_mismatches": schema_mismatches,
            "rule_mismatches": rule_mismatches,
            "invalid_json": invalid_json,
            "warnings": warnings,
        },
    }
    if write_manifest:
        _write_json(result_dir / ARTIFACT_MANIFEST_NAME, manifest)
    return {
        "status": status,
        "taskId": task_id,
        "resultDir": str(result_dir),
        "manifestPath": str(result_dir / ARTIFACT_MANIFEST_NAME),
        "bundleSha256": bundle_sha,
        "ready": ready,
        "readyCount": len(CORE_INPUT_ARTIFACTS) - len(missing),
        "total": len(CORE_INPUT_ARTIFACTS),
        "missing": missing,
        "schemaMismatches": schema_mismatches,
        "ruleMismatches": rule_mismatches,
        "invalidJson": invalid_json,
        "artifacts": artifacts,
        "warnings": warnings,
        "message": (
            f"JSON 无效：{', '.join(invalid_json)}"
            if invalid_json else (
                f"{len(CORE_INPUT_ARTIFACTS) - len(missing)}/{len(CORE_INPUT_ARTIFACTS)} 个核心文件已生成"
                if not missing else f"缺少 {len(missing)} 个核心文件"
            )
        ),
    }


def _resolve_manifest_artifact_path(manifest: dict, artifact_name: str) -> Path | None:
    item = ((manifest or {}).get("artifacts") or {}).get(artifact_name) or {}
    path = item.get("path")
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _find_report_for_task_at_root(task_id: str, wiki_root: Path) -> dict:
    task_id = _safe_task_id(task_id)
    companies_root = wiki_root / "companies"
    if not companies_root.is_dir():
        return {}
    for company_dir in sorted(path for path in companies_root.iterdir() if path.is_dir()):
        company = _read_json(company_dir / "company.json", {}) or {}
        for report in company.get("reports") or []:
            if (report or {}).get("task_id") == task_id:
                report_id = (report or {}).get("report_id") or company.get("primary_report_id") or "2025-annual"
                return {
                    "companyDir": company_dir.name,
                    "companyPath": company_dir,
                    "reportId": report_id,
                    "reportDir": company_dir / "reports" / report_id,
                    "company": company,
                }
        primary = company.get("primary_report_id") or "2025-annual"
        report_dir = company_dir / "reports" / primary
        report_json = _read_json(report_dir / "report.json", {}) or {}
        if ((report_json.get("source") or {}).get("task_id") == task_id):
            return {
                "companyDir": company_dir.name,
                "companyPath": company_dir,
                "reportId": primary,
                "reportDir": report_dir,
                "company": company,
            }
    return {}


def _find_report_for_task(task_id: str) -> dict:
    return _find_report_for_task_at_root(task_id, WIKI_ROOT)


def _wiki_builder_fingerprint() -> tuple[tuple[str, int | None], ...]:
    dependencies = [
        WIKI_REBUILD_SCRIPT,
        WIKISET_ROOT / "company_identity.py",
    ]
    fingerprint = []
    for path in dependencies:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        fingerprint.append((str(path), mtime))
    return tuple(fingerprint)


def _load_wiki_builder():
    global _wiki_builder, _wiki_builder_mtime
    try:
        current_mtime = _wiki_builder_fingerprint()
    except OSError:
        current_mtime = None
    if _wiki_builder is not None and _wiki_builder_mtime == current_mtime:
        return _wiki_builder
    if not WIKI_REBUILD_SCRIPT.is_file():
        raise HTTPException(500, f"Wiki import script not found: {WIKI_REBUILD_SCRIPT}")
    sys.modules.pop("company_identity", None)
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location("wikiset_rebuild_wiki_v2", WIKI_REBUILD_SCRIPT)
    if spec is None or spec.loader is None:
        raise HTTPException(500, f"Cannot load wiki import script: {WIKI_REBUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _wiki_builder = module
    _wiki_builder_mtime = current_mtime
    return module


def _canonicalize_row_identity(builder, row: dict) -> dict:
    identity = dict(row.get("identity") or {})
    reports = [{"source_filename": row.get("filename") or ""}]
    payload = {
        **identity,
        "reports": reports,
    }
    canonical, _ = builder.canonicalize_company_json(payload)
    row["identity"] = {
        "company_id": canonical["company_id"],
        "stock_code": canonical["stock_code"],
        "exchange": canonical["exchange"],
        "company_short_name": canonical["company_short_name"],
        "company_full_name": canonical["company_full_name"],
        "aliases": canonical.get("aliases") or [canonical["company_short_name"], canonical["company_full_name"]],
    }
    return row


def _find_company_for_task_at_root(task_id: str, wiki_root: Path) -> str:
    companies_root = wiki_root / "companies"
    if not companies_root.is_dir():
        return ""
    for company_dir in sorted(path for path in companies_root.iterdir() if path.is_dir()):
        company_json = company_dir / "company.json"
        try:
            company = json.loads(company_json.read_text(encoding="utf-8"))
        except Exception:
            company = {}
        reports = company.get("reports") or []
        if any((report or {}).get("task_id") == task_id for report in reports):
            return company_dir.name
        primary = company.get("primary_report_id") or "2025-annual"
        report_json = company_dir / "reports" / primary / "report.json"
        try:
            report = json.loads(report_json.read_text(encoding="utf-8"))
        except Exception:
            report = {}
        if ((report.get("source") or {}).get("task_id") == task_id):
            return company_dir.name
    return ""


def _find_company_for_task(task_id: str) -> str:
    return _find_company_for_task_at_root(task_id, WIKI_ROOT)


def _wiki_import_status_at_root(task_id: str, wiki_root: Path, market: str = "") -> dict:
    report = _find_report_for_task_at_root(task_id, wiki_root)
    company_dir = report.get("companyDir") or ""
    result_dir = _find_task_result_dir(task_id)
    artifact_bundle = _artifact_bundle_status(task_id)
    report_dir = report.get("reportDir")
    wiki_manifest = report_dir / ARTIFACT_MANIFEST_NAME if report_dir else None
    wiki_manifest_payload = _read_json(wiki_manifest, {}) if wiki_manifest else {}
    source_bundle_sha = artifact_bundle.get("bundleSha256")
    wiki_bundle_sha = (((wiki_manifest_payload or {}).get("core") or {}).get("bundle_sha256"))
    source_artifacts = artifact_bundle.get("artifacts") or {}
    wiki_artifacts = (wiki_manifest_payload or {}).get("artifacts") or {}
    comparable_names = [
        name
        for name in STABLE_BUNDLE_ARTIFACTS
        if isinstance(source_artifacts, dict)
        and isinstance(wiki_artifacts, dict)
        and name in source_artifacts
        and name in wiki_artifacts
    ]
    artifact_mismatches = [
        name
        for name in comparable_names
        if ((source_artifacts.get(name) or {}).get("sha256") != (wiki_artifacts.get(name) or {}).get("sha256"))
    ]
    stale = bool(company_dir and (
        artifact_mismatches
        or (not comparable_names and source_bundle_sha and wiki_bundle_sha and source_bundle_sha != wiki_bundle_sha)
    ))
    status = "stale" if stale else ("ready" if company_dir else "missing")
    return {
        "status": status,
        "market": market or _infer_task_market(task_id),
        "wikiRoot": str(wiki_root),
        "companyDir": company_dir,
        "reportId": report.get("reportId") or "",
        "reportDir": str(report_dir) if report_dir else "",
        "resultDir": str(result_dir) if result_dir else "",
        "bundleSha256": wiki_bundle_sha or "",
        "sourceBundleSha256": source_bundle_sha or "",
        "manifestPath": str(wiki_manifest) if wiki_manifest else "",
        "stale": stale,
        "artifactMismatches": artifact_mismatches,
        "storagePolicy": "lightweight_manifest_only",
        "message": "Wiki 需刷新" if stale else ("已导入 Wiki" if company_dir else ("可导入 Wiki" if result_dir else "未找到解析产物目录")),
    }


def _wiki_import_status(task_id: str) -> dict:
    market = _infer_task_market(task_id)
    return _wiki_import_status_at_root(task_id, _wiki_root_for_market(market), market)


def _semantic_status_at_root(company_dir: str, task_id: str | None, wiki_root: Path) -> dict:
    if not company_dir:
        return {"status": "unknown", "companyDir": "", "message": "未在 Wiki 中找到对应公司"}
    semantic_dir = wiki_root / "companies" / company_dir / "semantic"
    company_json = _read_json(wiki_root / "companies" / company_dir / "company.json", {}) or {}
    report_id = company_json.get("primary_report_id") or "2025-annual"
    report_dir = wiki_root / "companies" / company_dir / "reports" / report_id
    required = [
        "subject_profile.json",
        "segments.json",
        "facts.json",
        "relations.json",
        "claims.json",
        "retrieval_index.json",
        "note_links.json",
        "evidence_semantic.json",
        "extraction_log.json",
    ]
    missing = [name for name in required if not (semantic_dir / name).is_file()]
    log = {}
    if (semantic_dir / "extraction_log.json").is_file():
        try:
            log = json.loads((semantic_dir / "extraction_log.json").read_text(encoding="utf-8"))
        except Exception:
            log = {}
    stale = False
    if not missing and log.get("inputs"):
        current_inputs = {
            "company_json_sha256": _sha256_file(wiki_root / "companies" / company_dir / "company.json"),
            "report_md_sha256": _sha256_file(report_dir / "report.md"),
            "report_json_sha256": _sha256_file(report_dir / "report.json"),
            "document_full_sha256": _sha256_file(report_dir / "document_full.json"),
        }
        if (report_dir / ARTIFACT_MANIFEST_NAME).is_file():
            current_inputs["artifact_manifest_sha256"] = _sha256_file(report_dir / ARTIFACT_MANIFEST_NAME)
        stale = any((log.get("inputs") or {}).get(key) != value for key, value in current_inputs.items())
        if not stale and task_id:
            wiki_status = _wiki_import_status_at_root(task_id, wiki_root)
            stale = bool(wiki_status.get("stale"))
    llm = _llm_semantic_status_at_root(company_dir, report_id, stale, wiki_root)
    llm_ready = not LLM_SEMANTIC_ENABLED or not LLM_SEMANTIC_REQUIRED or llm.get("status") == "ready"
    status = "stale" if stale or llm.get("status") == "stale" else ("ready" if not missing and llm_ready else "missing")
    return {
        "status": status,
        "companyDir": company_dir,
        "reportId": report_id,
        "missing": missing,
        "counts": log.get("counts") or {},
        "quality": log.get("quality") or {},
        "warnings": log.get("warnings") or [],
        "llm": llm,
        "message": "语义层需重新生成" if status == "stale" else ("LLM 语义增强未生成" if LLM_SEMANTIC_ENABLED and llm.get("status") == "missing" else ""),
    }


def _semantic_status(company_dir: str, task_id: str | None = None) -> dict:
    wiki_root = _wiki_root_for_market(_infer_task_market(task_id)) if task_id else WIKI_ROOT
    return _semantic_status_at_root(company_dir, task_id, wiki_root)


def _llm_semantic_status_at_root(company_dir: str, report_id: str, rule_stale: bool, wiki_root: Path) -> dict:
    if not LLM_SEMANTIC_ENABLED:
        return {"status": "disabled", "enabled": False, "message": "LLM 语义增强已关闭"}
    out_dir = wiki_root / "companies" / company_dir / "semantic" / "llm" / report_id
    required = [
        "enrichment.json",
        "business_profile.json",
        "claims.json",
        "risks.json",
        "events.json",
        "review_queue.json",
        "extraction_log.json",
    ]
    missing = [name for name in required if not (out_dir / name).is_file()]
    log = _read_json(out_dir / "extraction_log.json", {}) if not missing or (out_dir / "extraction_log.json").is_file() else {}
    current_inputs = {
        "company_json_sha256": _sha256_file(wiki_root / "companies" / company_dir / "company.json"),
        "segments_sha256": _sha256_file(wiki_root / "companies" / company_dir / "semantic" / "segments.json"),
        "evidence_semantic_sha256": _sha256_file(wiki_root / "companies" / company_dir / "semantic" / "evidence_semantic.json"),
        "facts_sha256": _sha256_file(wiki_root / "companies" / company_dir / "semantic" / "facts.json"),
        "claims_sha256": _sha256_file(wiki_root / "companies" / company_dir / "semantic" / "claims.json"),
        "artifact_manifest_sha256": _sha256_file(wiki_root / "companies" / company_dir / "reports" / report_id / ARTIFACT_MANIFEST_NAME),
    }
    llm_stale = False
    if not missing and log.get("inputs"):
        llm_stale = any((log.get("inputs") or {}).get(key) != value for key, value in current_inputs.items())
    status = "missing" if missing else ("stale" if rule_stale or llm_stale else "ready")
    return {
        "status": status,
        "enabled": True,
        "required": LLM_SEMANTIC_REQUIRED,
        "reportId": report_id,
        "outputDir": str(out_dir),
        "missing": missing,
        "counts": log.get("counts") or {},
        "provider": log.get("provider") or {},
        "promptVersion": log.get("prompt_version") or "",
        "enrichmentVersion": log.get("enrichment_version") or "",
        "generatedAt": log.get("generated_at"),
        "message": "LLM 语义增强需重新生成" if status == "stale" else ("LLM 语义增强未生成" if missing else "项目设置模型语义增强已生成"),
    }


def _llm_semantic_status(company_dir: str, report_id: str, rule_stale: bool = False) -> dict:
    return _llm_semantic_status_at_root(company_dir, report_id, rule_stale, WIKI_ROOT)


def _obsidian_status_at_root(company_dir: str, semantic_status: dict | None, wiki_root: Path) -> dict:
    if not company_dir:
        return {"status": "unknown", "companyDir": "", "message": "未在 Wiki 中找到对应公司"}
    company_root = wiki_root / "companies" / company_dir
    graph_index = company_root / "graph" / "graph_index.json"
    obsidian_index = company_root / "obsidian" / "index.md"
    readme = company_root / "obsidian" / "README.md"
    missing = [
        str(path.relative_to(company_root))
        for path in (graph_index, obsidian_index, readme)
        if not path.is_file()
    ]
    semantic_stale = semantic_status is not None and semantic_status.get("status") != "ready"
    graph = _read_json(graph_index, {}) if graph_index.is_file() else {}
    status = "missing" if missing else ("stale" if semantic_stale else "ready")
    return {
        "status": status,
        "companyDir": company_dir,
        "missing": missing,
        "graphIndex": str(graph_index) if graph_index.is_file() else "",
        "obsidianIndex": str(obsidian_index) if obsidian_index.is_file() else "",
        "nodeCount": graph.get("node_count", 0),
        "nodeCounts": graph.get("node_counts", {}),
        "generatedAt": graph.get("generated_at"),
        "message": "Obsidian 图谱需重新生成" if status == "stale" else ("Obsidian 图谱未生成" if missing else "Obsidian 图谱已生成"),
    }


def _obsidian_status(company_dir: str, semantic_status: dict | None = None) -> dict:
    return _obsidian_status_at_root(company_dir, semantic_status, WIKI_ROOT)


def _generate_obsidian_for_company_at_root(company_dir: str, wiki_root: Path) -> dict:
    if not company_dir:
        raise HTTPException(404, "Task is not linked to a Wiki company")
    if not OBSIDIAN_SCRIPT.is_file():
        raise HTTPException(500, f"Obsidian graph script not found: {OBSIDIAN_SCRIPT}")
    result = _run_command([
        sys.executable,
        str(OBSIDIAN_SCRIPT),
        "--wiki-root",
        str(wiki_root),
        "--company",
        company_dir,
    ])
    if result["returnCode"] != 0:
        raise HTTPException(500, {"stage": "obsidian", **result})
    return result


def _generate_obsidian_for_company(company_dir: str) -> dict:
    return _generate_obsidian_for_company_at_root(company_dir, WIKI_ROOT)


def _merge_by_report_id(items: list[dict], new_item: dict) -> list[dict]:
    report_id = new_item.get("report_id")
    merged = [item for item in items if (item or {}).get("report_id") != report_id]
    merged.append(new_item)
    return sorted(merged, key=lambda item: (int(item.get("report_year") or 0), str(item.get("report_id") or "")), reverse=True)


def _filter_report(items: list[dict], report_id: str) -> list[dict]:
    return [item for item in items if (item or {}).get("report_id") != report_id]


def _int_or_none(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _public_evidence_urls(builder, task_id: str, page: int | None, table_index: int | None) -> dict:
    urls = builder.evidence_urls(task_id, page, table_index)
    if not isinstance(urls, dict):
        urls = {}
    return {
        "open_pdf_page_url": urls.get("open_pdf_page_url") or "",
        "open_source_page_url": urls.get("open_source_page_url") or "",
        "open_source_table_url": urls.get("open_source_table_url") or "",
    }


def _pdf_ref_key(ref: dict) -> tuple:
    return (
        ref.get("company_id"),
        ref.get("report_id"),
        ref.get("task_id"),
        _int_or_none(ref.get("pdf_page_number") or ref.get("pdf_page")),
        _int_or_none(ref.get("table_index")),
        _int_or_none(ref.get("md_line") or ref.get("line")),
        ref.get("source_type") or ref.get("source_kind") or "",
    )


def _append_pdf_ref(refs: list[dict], seen: set[tuple], ref: dict) -> None:
    if not ref.get("task_id"):
        return
    page = _int_or_none(ref.get("pdf_page_number") or ref.get("pdf_page"))
    table_index = _int_or_none(ref.get("table_index"))
    if page is None and table_index is None:
        return
    ref["pdf_page_number"] = page
    ref["table_index"] = table_index
    key = _pdf_ref_key(ref)
    if key in seen:
        return
    seen.add(key)
    refs.append(ref)


def _build_pdf_refs_from_import(
    builder,
    *,
    identity: dict,
    report_id: str,
    task_id: str,
    evidence: list[dict],
    report_json: dict | None = None,
    row: dict | None = None,
) -> list[dict]:
    """Build report-level PDF/source/table refs from every available import artifact.

    `evidence_index.json` is sparse for generic PDFs. The table index in
    `quality_report.json` / `report.json` is the stable minimum contract for
    source traceability, so always mirror table anchors into `pdf_refs.json`.
    """
    company_id = identity.get("company_id")
    refs: list[dict] = []
    seen: set[tuple] = set()

    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        page = _int_or_none(item.get("pdf_page_number") or item.get("pdf_page"))
        table_index = _int_or_none(item.get("table_index"))
        ref = {
            "company_id": item.get("company_id") or company_id,
            "report_id": item.get("report_id") or report_id,
            "task_id": item.get("task_id") or task_id,
            "pdf_page_number": page,
            "table_index": table_index,
            "md_line": _int_or_none(item.get("md_line") or item.get("line")),
            "source_type": item.get("source_type") or item.get("source_kind") or "wiki_evidence",
            "metric": item.get("metric_name") or item.get("metric_key") or item.get("canonical_name"),
        }
        ref.update(_public_evidence_urls(builder, ref["task_id"], page, table_index))
        _append_pdf_ref(refs, seen, ref)

    table_sources: list[tuple[str, list]] = []
    tables = (report_json or {}).get("tables") if isinstance(report_json, dict) else None
    if isinstance(tables, list):
        table_sources.append(("report_json_table", tables))
    quality_tables = ((row or {}).get("quality") or {}).get("table_index") if isinstance(row, dict) else None
    if isinstance(quality_tables, list):
        table_sources.append(("quality_table_index", quality_tables))
    enhanced_tables = ((row or {}).get("enhanced") or {}).get("tables") if isinstance(row, dict) else None
    if isinstance(enhanced_tables, list):
        table_sources.append(("content_list_enhanced_table", enhanced_tables))

    for source_type, tables in table_sources:
        for table in tables:
            if not isinstance(table, dict):
                continue
            page = _int_or_none(table.get("pdf_page_number") or table.get("pdf_page"))
            table_index = _int_or_none(table.get("table_index"))
            ref = {
                "company_id": company_id,
                "report_id": report_id,
                "task_id": task_id,
                "pdf_page_number": page,
                "table_index": table_index,
                "md_line": _int_or_none(table.get("md_line") or table.get("line") or table.get("markdown_line")),
                "source_type": source_type,
                "heading": table.get("heading") or table.get("title") or table.get("canonical_name"),
                "preview": str(table.get("preview") or "")[:500],
            }
            ref.update(_public_evidence_urls(builder, task_id, page, table_index))
            _append_pdf_ref(refs, seen, ref)

    refs.sort(
        key=lambda item: (
            _int_or_none(item.get("pdf_page_number")) or 10**9,
            _int_or_none(item.get("table_index")) or 10**9,
            _int_or_none(item.get("md_line")) or 10**9,
            str(item.get("source_type") or ""),
        )
    )
    return refs


def _write_report_pdf_refs(
    builder,
    *,
    company_dir: Path,
    identity: dict,
    report_id: str,
    task_id: str,
    evidence: list[dict],
    report_json: dict,
    row: dict,
) -> list[dict]:
    pdf_refs = _build_pdf_refs_from_import(
        builder,
        identity=identity,
        report_id=report_id,
        task_id=task_id,
        evidence=evidence,
        report_json=report_json,
        row=row,
    )
    old_refs_payload = _read_json(company_dir / "evidence" / "pdf_refs.json", {"refs": []})
    merged_refs = _filter_report(old_refs_payload.get("refs") or [], report_id) + pdf_refs
    builder.write_json(company_dir / "evidence" / "pdf_refs.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "refs": merged_refs,
        "generated_at": builder.now_iso(),
    })
    return pdf_refs


LEGACY_METRIC_PATHS = {
    "three_statements": "metrics/three_statements.json",
    "key_metrics": "metrics/key_metrics.json",
    "validation": "metrics/validation.json",
}
LATEST_METRIC_PATHS = {
    "three_statements": "metrics/latest/three_statements.json",
    "key_metrics": "metrics/latest/key_metrics.json",
    "validation": "metrics/latest/validation.json",
}


def _metric_paths_for_report(report_id: str) -> dict:
    return {
        "three_statements": f"metrics/reports/{report_id}/three_statements.json",
        "key_metrics": f"metrics/reports/{report_id}/key_metrics.json",
        "validation": f"metrics/reports/{report_id}/validation.json",
    }


def _company_metrics_index(company_reports: list[dict]) -> dict:
    by_report = {}
    for report in company_reports:
        report_id = str((report or {}).get("report_id") or "").strip()
        if report_id:
            by_report[report_id] = _metric_paths_for_report(report_id)
    return {
        **LEGACY_METRIC_PATHS,
        "latest": LATEST_METRIC_PATHS,
        "reports_root": "metrics/reports",
        "by_report": by_report,
    }


def _write_metrics_bundle(
    company_dir: Path,
    report_id: str,
    three_statements: dict,
    key_metrics: dict,
    validation: dict,
    *,
    mirror_latest: bool,
) -> None:
    payloads = {
        "three_statements": three_statements,
        "key_metrics": key_metrics,
        "validation": validation,
    }
    for name, rel_path in _metric_paths_for_report(report_id).items():
        _write_json(company_dir / rel_path, payloads[name])
    if not mirror_latest:
        return
    for name, rel_path in LATEST_METRIC_PATHS.items():
        _write_json(company_dir / rel_path, payloads[name])
    for name, rel_path in LEGACY_METRIC_PATHS.items():
        _write_json(company_dir / rel_path, payloads[name])


def _load_pg_config() -> dict | None:
    if not DB_CONFIG_PY.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("siq_pdf2md_pg_config", DB_CONFIG_PY)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = getattr(module, "PG_CONFIG", None)
        return dict(config) if isinstance(config, dict) else None
    except Exception:
        return None


def _db_connect_config() -> dict:
    loaded = _load_pg_config()
    if loaded:
        return loaded
    return {
        "host": os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT", "15432")),
        "dbname": (
            os.environ.get("SIQ_DOCUMENT_PGDATABASE")
            or os.environ.get("SIQ_PGDATABASE")
            or os.environ.get("PGDATABASE", "siq_document_parser")
        ),
        "user": os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD", ""),
    }


def _pdf2md_db_connect_config() -> dict:
    loaded = _load_pg_config()
    if loaded:
        return loaded
    return {
        "host": os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT", "15432")),
        "dbname": (
            os.environ.get("SIQ_PDF2MD_PGDATABASE")
            or os.environ.get("SIQ_PGDATABASE")
            or os.environ.get("PGDATABASE", "siq")
        ),
        "user": os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD", ""),
    }


def _postgres_database_url(config: dict) -> str:
    from urllib.parse import quote

    user = quote(str(config.get("user") or ""), safe="")
    password = quote(str(config.get("password") or ""), safe="")
    host = str(config.get("host") or "127.0.0.1")
    port = int(config.get("port") or 15432)
    dbname = quote(str(config.get("dbname") or "siq_document_parser"), safe="")
    auth = user if not password else f"{user}:{password}"
    return f"postgresql://{auth}@{host}:{port}/{dbname}"


def _update_catalogs(
    identity: dict,
    report_entry: dict,
    report_json: dict,
    row: dict,
    three_statement_payload: dict | None = None,
    three_statement_source: str | None = None,
) -> None:
    meta_dir = WIKI_ROOT / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    company_path = f"companies/{identity['company_id']}"
    company_json = _read_json(WIKI_ROOT / company_path / "company.json", {}) or {}
    company_reports = company_json.get("reports") if isinstance(company_json.get("reports"), list) else []
    actual_report_count = len(company_reports) if company_reports else 1
    primary_report_id = company_json.get("primary_report_id") or report_entry["report_id"]

    company_catalog_path = meta_dir / "company_catalog.json"
    company_catalog = _read_json(company_catalog_path, {"schema_version": 1, "companies": []})
    companies = company_catalog.get("companies") or []
    company_item = {
        **identity,
        "company_path": company_path,
        "primary_report_id": primary_report_id,
        "report_count": actual_report_count,
        "status": report_json.get("status") or "ready",
        "has_v641_metrics": False,
    }
    if three_statement_source is not None:
        metric_count = len((three_statement_payload or {}).get("metrics") or [])
        company_item.update({
            "has_three_statement_metrics": metric_count > 0,
            "three_statement_source": three_statement_source,
            "three_statement_metric_count": metric_count,
        })
    existing = [item for item in companies if (item or {}).get("company_id") != identity["company_id"]]
    old = next((item for item in companies if (item or {}).get("company_id") == identity["company_id"]), {})
    company_item["report_count"] = actual_report_count
    existing.append({**old, **company_item})
    company_catalog.update({
        "schema_version": company_catalog.get("schema_version") or 1,
        "generated_at": _now_iso(),
        "company_count": len(existing),
        "companies": sorted(existing, key=lambda item: item.get("stock_code") or item.get("company_id") or ""),
    })
    _write_json(company_catalog_path, company_catalog)

    report_catalog_path = meta_dir / "report_catalog.json"
    report_catalog = _read_json(report_catalog_path, {"schema_version": 1, "reports": []})
    reports = [
        item for item in (report_catalog.get("reports") or [])
        if not ((item or {}).get("company_id") == identity["company_id"] and (item or {}).get("report_id") == report_entry["report_id"])
    ]
    reports.append({**identity, **report_entry, "company_path": company_path})
    report_catalog.update({
        "schema_version": report_catalog.get("schema_version") or 1,
        "generated_at": _now_iso(),
        "report_count": len(reports),
        "reports": sorted(reports, key=lambda item: (item.get("stock_code") or "", int(item.get("report_year") or 0)), reverse=False),
    })
    _write_json(report_catalog_path, report_catalog)

    manifest_path = meta_dir / "wiki_import_manifest.json"
    manifest = _read_json(manifest_path, {"schema_version": 1, "imports": []})
    imports = manifest.get("imports") or []
    imports = [
        item for item in imports
        if not ((item or {}).get("task_id") == row["task_id"] and (item or {}).get("report_id") == report_entry["report_id"])
    ]
    imports.append({
        "task_id": row["task_id"],
        "company_id": identity["company_id"],
        "report_id": report_entry["report_id"],
        "source_results_dir": row["result_dir"],
        "imported_at": _now_iso(),
        "status": report_json.get("status") or "ready",
        "warnings": row.get("warnings") or [],
    })
    manifest.update({
        "schema_version": manifest.get("schema_version") or 1,
        "generated_at": _now_iso(),
        "source_results_root": str(PDF_RESULTS_ROOT),
        "wikiset_root": str(WIKISET_ROOT),
        "import_count": len(imports),
        "imports": imports[-200:],
    })
    _write_json(manifest_path, manifest)


def _sqlite_table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    table = validate_table_name(table)
    return [row[1] for row in cur.execute(f"pragma table_info({table})").fetchall()]


def _sqlite_insert_dynamic(cur: sqlite3.Cursor, table: str, values: dict, *, skip: set[str] | None = None) -> None:
    table = validate_table_name(table)
    skip = skip or set()
    columns = [column for column in _sqlite_table_columns(cur, table) if column not in skip]
    if not columns:
        return
    placeholders = ",".join("?" for _ in columns)
    column_sql = ",".join(columns)
    cur.execute(
        f"insert or replace into {table} ({column_sql}) values ({placeholders})",
        [values.get(column) for column in columns],
    )


def _refresh_derived_three_statement_metrics(builder) -> dict:
    catalog = _read_json(WIKI_ROOT / "_meta" / "company_catalog.json", {}) or {}
    report_catalog = _read_json(WIKI_ROOT / "_meta" / "report_catalog.json", {}) or {}
    companies = catalog.get("companies") or []
    reports = report_catalog.get("reports") or []
    payloads: dict[str, dict] = {}

    for company in companies:
        code = company.get("stock_code")
        company_path = company.get("company_path")
        if not code or not company_path:
            continue
        wrapper = _read_json(WIKI_ROOT / company_path / "metrics" / "three_statements.json", {}) or {}
        payload = wrapper.get("data") if isinstance(wrapper, dict) else {}
        if isinstance(payload, dict) and payload.get("metrics"):
            payloads[code] = payload

    derived_dir = WIKI_ROOT / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    builder.write_json(derived_dir / "three_statements_latest.json", payloads)

    db_path = derived_dir / "financial_metrics.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table in ("three_statement_metrics", "validation_anomalies", "reports", "companies"):
            cur.execute(f"delete from {table}")

        company_by_code = {company.get("stock_code"): company for company in companies if company.get("stock_code")}
        report_by_code: dict[str, dict] = {}
        for report in reports:
            code = report.get("stock_code")
            if code and code not in report_by_code:
                report_by_code[code] = report

        for company in companies:
            _sqlite_insert_dynamic(cur, "companies", company)
        for report in reports:
            _sqlite_insert_dynamic(cur, "reports", report)

        metric_columns = [column for column in _sqlite_table_columns(cur, "three_statement_metrics") if column != "id"]
        placeholders = ",".join("?" for _ in metric_columns)
        column_sql = ",".join(metric_columns)
        metric_total = 0
        for code, payload in payloads.items():
            company = company_by_code.get(code) or {}
            report = report_by_code.get(code) or {}
            for metric in payload.get("metrics") or []:
                source = metric.get("source") or {}
                page = source.get("pdf_page") or source.get("pdf_page_number")
                table_index = source.get("table_index")
                urls = builder.evidence_urls(source.get("task_id") or report.get("task_id"), page, table_index)
                source_kind = source.get("source_kind") or ""
                row = {
                    "stock_code": code,
                    "company_id": company.get("company_id"),
                    "report_id": report.get("report_id") or company.get("primary_report_id"),
                    "company_name": payload.get("company") or company.get("company_short_name"),
                    "statement_type": metric.get("statement_type"),
                    "metric_key": metric.get("metric_key"),
                    "raw_value": metric.get("raw_value"),
                    "normalized_value": metric.get("normalized_value"),
                    "unit": "亿元",
                    "md_line": source.get("md_line") or source.get("line"),
                    "pdf_page_number": page,
                    "table_index": table_index,
                    "task_id": source.get("task_id") or report.get("task_id"),
                    "open_pdf_page_url": urls["open_pdf_page_url"],
                    "open_source_table_url": urls["open_source_table_url"],
                    "extraction_method": (
                        "financial_data_statement_ingest_v1"
                        if source_kind == "financial_data_statement"
                        else "v6.41_rebuild"
                    ),
                }
                cur.execute(
                    f"insert into three_statement_metrics ({column_sql}) values ({placeholders})",
                    [row.get(column) for column in metric_columns],
                )
                metric_total += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "threeStatementCompanyCount": len(payloads),
        "threeStatementMetricCount": metric_total,
        "database": str(db_path),
    }


def _repair_and_validate_wiki_naming() -> dict:
    repair = {"returncode": None, "stdout": "", "stderr": ""}
    if WIKI_NAMING_REPAIR_SCRIPT.is_file():
        completed = subprocess.run(
            [sys.executable, str(WIKI_NAMING_REPAIR_SCRIPT), "--wiki-root", str(WIKI_ROOT)],
            check=False,
            text=True,
            capture_output=True,
        )
        repair = {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        if completed.returncode != 0:
            raise HTTPException(500, {"message": "Wiki 命名修复失败", "repair": repair})

    validate = {"returncode": None, "stdout": "", "stderr": ""}
    if WIKI_NAMING_VALIDATE_SCRIPT.is_file():
        completed = subprocess.run(
            [sys.executable, str(WIKI_NAMING_VALIDATE_SCRIPT), "--wiki-root", str(WIKI_ROOT)],
            check=False,
            text=True,
            capture_output=True,
        )
        validate = {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        if completed.returncode != 0:
            raise HTTPException(422, {"message": "Wiki 命名契约校验失败", "validation": validate})
    return {"repair": repair, "validation": validate}


_GENERIC_REPORT_FINDER_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>CN|HK|US|KR|JP)_"
    r"(?P<ticker>[^_]+)_"
    r"(?P<report_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})$",
    re.IGNORECASE,
)
_GENERIC_SOURCE_SUFFIX_RE = re.compile(r"(?i)(?:[_\-\s]+)?tcm\d+[-_]\d+$")
_GENERIC_LANGUAGE_SUFFIX_RE = re.compile(r"(?i)(?:^|[\s_\-]+)(?:en|eng|cn|zh|chs|cht|ar|de|fr|es)(?=$|[\s_\-]+)")
_ENGLISH_REPORT_FILENAME_RE = re.compile(
    r"(?ix)"
    r"(?:\b(?:integrated|annual|sustainability|interim|quarterly|half[-_\s]?year|full[-_\s]?year|financial)\b.*\breport\b)"
    r"|(?:\breport\b.*\b(?:integrated|annual|sustainability|interim|quarterly|financial)\b)"
    r"|(?:\bform[-_\s]?(?:10[-_\s]?k|20[-_\s]?f|10[-_\s]?q)\b)"
)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _generic_slug(value: str, fallback: str = "SUBJECT") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    return text or fallback


def _generic_display_name(filename: str, markdown: str = "") -> str:
    stem = Path(str(filename or "")).stem.strip()
    match = _GENERIC_REPORT_FINDER_RE.match(stem)
    if match:
        return match.group("company").strip()

    text = stem or "unknown_subject"
    text = _GENERIC_SOURCE_SUFFIX_RE.sub("", text)
    text = _GENERIC_LANGUAGE_SUFFIX_RE.sub(" ", text)
    text = re.sub(r"[_\-—–]+", " ", text)
    text = re.sub(r"(?i)\b(?:integrated\s+annual|annual|integrated|sustainability|interim|quarterly|half[-\s]?year|full[-\s]?year|financial)\s+report\b", " ", text)
    text = re.sub(r"(?i)\b(?:form\s*10[-\s]?k|20[-\s]?f|10[-\s]?q)\b", " ", text)
    text = _GENERIC_LANGUAGE_SUFFIX_RE.sub(" ", text)
    text = re.sub(r"\b20\d{2}\b", " ", text)
    text = re.sub(r"(年度报告全文|年度报告|年报|半年度报告|季度报告|报告摘要)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" _-—–：:，,；;（）()[]【】")
    if text:
        return text

    for line in str(markdown or "").splitlines()[:80]:
        title = re.sub(r"^#+\s*", "", line).strip()
        title = re.sub(r"\b20\d{2}\b.*$", "", title).strip()
        if 2 <= len(title) <= 80:
            return title
    return stem or "unknown_subject"


def _looks_like_generic_foreign_report(row: dict) -> bool:
    filename = str(row.get("filename") or "")
    stem = Path(filename).stem
    if not stem or _has_cjk(stem):
        return False

    finder_match = _GENERIC_REPORT_FINDER_RE.match(stem)
    if finder_match and finder_match.group("market").upper() != "CN":
        return True
    if not _ENGLISH_REPORT_FILENAME_RE.search(stem):
        return False

    identity = row.get("identity") or {}
    stock_code = str(identity.get("stock_code") or "")
    if stock_code and re.search(rf"(?<!\d){re.escape(stock_code)}(?!\d)", stem):
        return False

    for name in [identity.get("company_short_name"), identity.get("company_full_name")]:
        name = str(name or "").strip()
        if name and _has_cjk(name) and name in filename:
            return False
    return True


def _generic_identity_from_row(row: dict) -> dict:
    filename = row.get("filename") or ""
    stem = Path(str(filename or "")).stem
    match = _GENERIC_REPORT_FINDER_RE.match(stem)
    market = "GEN"
    security_code = ""
    if match:
        market = match.group("market").upper()
        security_code = re.sub(r"[^0-9A-Za-z.\-]+", "", match.group("ticker").upper())

    short_name = _generic_display_name(filename, row.get("markdown") or "")
    full_name = short_name
    name_slug = _generic_slug(short_name, "SUBJECT")
    if security_code:
        subject_code = _generic_slug(f"{market}{security_code}", f"{market}SUBJECT")
    else:
        subject_code = f"GEN{name_slug[:32].upper()}"

    return {
        "company_id": f"{subject_code}-{name_slug}",
        "stock_code": subject_code,
        "exchange": {"HK": "HKEX", "US": "US", "CN": "CN", "KR": "KRX", "JP": "JPX"}.get(market, "GENERIC"),
        "company_short_name": short_name,
        "company_full_name": full_name,
        "aliases": [value for value in [short_name, full_name, stem] if value],
        "identity_kind": "generic_subject",
        "identity_route": "generic_non_a_share_wiki_import",
        "market": market if market != "GEN" else "",
        "security_code": security_code,
        "synthetic_stock_code": not bool(security_code),
    }


def _copy_generic_report_assets(row: dict, report_dir: Path) -> list[dict]:
    source_dir = Path(row["result_dir"])
    image_paths = sorted((row.get("image_refs") or set()) | (row.get("high_value_images") or set()))
    enhanced_by_path = {}
    for item in (row.get("enhanced") or {}).get("image_semantic_blocks") or []:
        if item.get("image_path"):
            enhanced_by_path[item["image_path"]] = item

    manifest = []
    identity = row["identity"]
    for rel in image_paths:
        source = source_dir / rel
        if not source.exists():
            continue
        dest = report_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        info = enhanced_by_path.get(rel) or {}
        manifest.append({
            "image_id": f"{identity['stock_code']}-{row.get('report_year') or 'unknown'}-img-{len(manifest) + 1:04d}",
            "company_id": identity["company_id"],
            "report_id": row["report_id"],
            "stock_code": identity["stock_code"],
            "company_short_name": identity["company_short_name"],
            "report_year": row.get("report_year"),
            "wiki_path": str(Path("reports") / row["report_id"] / rel),
            "source_path": str(source),
            "sha256": _sha256_file(source),
            "size_bytes": source.stat().st_size,
            "pdf_page_number": info.get("pdf_page_number"),
            "markdown_line": info.get("markdown_line"),
            "semantic_kind": info.get("semantic_kind") or info.get("type"),
            "detail_type": info.get("detail_type") or info.get("sub_type"),
            "confidence": info.get("confidence"),
            "actionability": info.get("actionability"),
            "recognized_preview": info.get("display_preview") or info.get("recognized_preview"),
            "chart_data": info.get("chart_data") or {},
            "source_task_id": row["task_id"],
            "copied_reason": "markdown_ref" if rel in (row.get("image_refs") or set()) else "semantic_high_value",
        })
    return manifest


def _build_generic_company_md(identity: dict, reports: list[dict]) -> str:
    primary = reports[0] if reports else {}
    lines = [
        f"# {identity['company_short_name']}",
        "",
        "- 主体类型：通用报告主体（非 A 股标准入库路线）",
        f"- 主体代码：{identity['stock_code']}",
        f"- 公司全称：{identity['company_full_name']}",
        f"- 市场：{identity.get('market') or '未提供'}",
        f"- 证券代码：{identity.get('security_code') or '未提供'}",
        f"- 主报告：{primary.get('report_id') or ''}",
        "",
        "## 可用报告",
        "",
    ]
    for report in reports:
        lines.append(f"- {report.get('report_year') or 'unknown'} {report.get('report_kind') or 'report'}：[{report['report_id']}](reports/{report['report_id']}/report.md)")
    lines.extend([
        "",
        "## 数据入口",
        "",
        "- [指标摘要](metrics/key_metrics.json)",
        "- [质量校验](metrics/validation.json)",
        "- [证据索引](evidence/evidence_index.json)",
        "- [图片证据](evidence/image_manifest.json)",
        "",
    ])
    return "\n".join(lines)


def _build_generic_analysis_readme(identity: dict) -> str:
    return "\n".join([
        f"# {identity['company_short_name']} 分析工作区",
        "",
        "本目录来自通用主体入库路线，用于非 A 股或非标准报告主体。",
        "",
        "分析时优先引用 `../reports/<report_id>/report.md`、`../reports/<report_id>/report.json` 和 `../evidence/evidence_index.json`。",
        "",
    ])


def _import_task_to_generic_wiki(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    market = _infer_task_market(task_id)
    if market in PDF_MARKET_WIKI_INGEST_SCRIPTS:
        return _import_task_to_market_wiki(task_id, market)
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        raise HTTPException(404, f"解析产物目录不存在，默认读取 {PDF_RESULTS_ROOT}/<task_id>")
    artifact_bundle = _artifact_bundle_status(task_id, write_manifest=False)
    if not artifact_bundle["ready"]:
        raise HTTPException(422, {
            "message": "解析产物包不完整，不能安全导入通用 Wiki",
            "missing": artifact_bundle["missing"],
            "invalid_json": artifact_bundle.get("invalidJson") or [],
            "warnings": artifact_bundle.get("warnings") or [],
        })

    builder = _load_wiki_builder()
    tasks = builder.load_tasks(PDF_TASK_DB_PATH)
    row = builder.inspect_result_dir(result_dir, tasks)
    if not row["markdown_path"].exists():
        fallback_md = result_dir / "result.md"
        if not fallback_md.exists():
            raise HTTPException(404, "result_complete.md/result.md not found for task")
        row["markdown_path"] = fallback_md
        row["markdown"] = fallback_md.read_text("utf-8", errors="ignore")

    if not row.get("report_year"):
        year = builder.report_year_from_text(row.get("filename"), (row.get("markdown") or "")[:5000])
        row["report_year"] = int(year) if year else None
    if not row.get("report_id") or row["report_id"] == "unknown-report":
        kind_slug = builder.REPORT_KIND_SLUG.get(row.get("report_kind"), builder.safe_name(row.get("report_kind") or "report"))
        row["report_id"] = f"{row['report_year']}-{kind_slug}" if row.get("report_year") else "unknown-report"

    row["identity"] = _generic_identity_from_row(row)
    row["identity_evidence"] = ["generic_non_a_share_import"]
    row["warnings"] = [item for item in (row.get("warnings") or []) if item != "missing_stock_code"]
    if "generic_non_a_share_identity" not in row["warnings"]:
        row["warnings"].append("generic_non_a_share_identity")

    identity = row["identity"]
    company_dir = WIKI_ROOT / "companies" / identity["company_id"]
    report_dir = company_dir / "reports" / row["report_id"]
    for subdir in ["metrics", "reports", "evidence", "analysis"]:
        (company_dir / subdir).mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(row["markdown_path"], report_dir / "report.md")
    shutil.copy2(result_dir / "document_full.json", report_dir / "document_full.json")
    wiki_manifest = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "storage_policy": {
            "mode": "lightweight_manifest_only",
            "route": "generic_non_a_share",
            "wiki_keeps": ["report.md", "report.json", "document_full.json", ARTIFACT_MANIFEST_NAME, "company/evidence/semantic indexes"],
            "full_parse_archive": "PostgreSQL pdf2md schema plus pdf2md results directory",
            "reason": "通用主体路线保留 Wiki 入口和证据索引，不触碰 A 股命名校验规则。",
        },
        "task_id": task_id,
        "source_results_dir": str(result_dir),
        "core": {
            "status": artifact_bundle["status"],
            "ready": artifact_bundle["ready"],
            "ready_count": artifact_bundle["readyCount"],
            "total": artifact_bundle["total"],
            "missing": artifact_bundle["missing"],
            "bundle_sha256": artifact_bundle["bundleSha256"],
        },
        "artifacts": artifact_bundle["artifacts"],
        "checks": {
            "schema_mismatches": artifact_bundle["schemaMismatches"],
            "rule_mismatches": artifact_bundle["ruleMismatches"],
            "warnings": artifact_bundle["warnings"],
        },
    }
    builder.write_json(report_dir / ARTIFACT_MANIFEST_NAME, wiki_manifest)

    images = _copy_generic_report_assets(row, report_dir)
    evidence = builder.build_fallback_evidence(row)
    report_json = builder.build_report_json(row, images, evidence)
    report_json["status"] = "ready" if not [w for w in row["warnings"] if w != "generic_non_a_share_identity"] else report_json.get("status", "needs_review")
    report_json["import_route"] = "generic_non_a_share"
    builder.write_json(report_dir / "report.json", report_json)

    company_json_path = company_dir / "company.json"
    existing_company = _read_json(company_json_path, {})
    report_entry = {
        "report_id": row["report_id"],
        "report_year": row["report_year"],
        "report_kind": row["report_kind"],
        "status": report_json["status"],
        "task_id": row["task_id"],
        "source_filename": row["filename"],
        "report_md": f"reports/{row['report_id']}/report.md",
        "report_json": f"reports/{row['report_id']}/report.json",
        "document_full": f"reports/{row['report_id']}/document_full.json",
        "artifact_manifest": f"reports/{row['report_id']}/{ARTIFACT_MANIFEST_NAME}",
        "artifact_bundle_sha256": artifact_bundle["bundleSha256"],
        "import_route": "generic_non_a_share",
        "metrics": _metric_paths_for_report(row["report_id"]),
    }
    company_reports = _merge_by_report_id(existing_company.get("reports") or [], report_entry)
    is_primary_report = bool(company_reports and company_reports[0].get("report_id") == row["report_id"])

    generated_at = builder.now_iso()
    three_statement_metrics = {
        "schema_version": 1,
        "source": "financial_data.json",
        "unit": "",
        "data": {},
        "generated_at": generated_at,
    }
    key_metrics = {
        "schema_version": 1,
        "source": "financial_data.json",
        "data": row["financial_data"].get("key_metrics") or [],
        "generated_at": generated_at,
    }
    validation = {
        "schema_version": 1,
        "financial_checks": row["financial_checks"],
        "wiki_v641_available": False,
        "import_route": "generic_non_a_share",
        "generated_at": generated_at,
    }
    _write_metrics_bundle(
        company_dir,
        row["report_id"],
        three_statement_metrics,
        key_metrics,
        validation,
        mirror_latest=is_primary_report,
    )

    old_evidence_payload = _read_json(company_dir / "evidence" / "evidence_index.json", {"evidence": []})
    merged_evidence = _filter_report(old_evidence_payload.get("evidence") or [], row["report_id"]) + evidence
    builder.write_json(company_dir / "evidence" / "evidence_index.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "evidence_count": len(merged_evidence),
        "evidence": merged_evidence,
        "generated_at": builder.now_iso(),
    })
    old_images_payload = _read_json(company_dir / "evidence" / "image_manifest.json", {"images": []})
    merged_images = _filter_report(old_images_payload.get("images") or [], row["report_id"]) + images
    builder.write_json(company_dir / "evidence" / "image_manifest.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "images": merged_images,
        "generated_at": builder.now_iso(),
    })
    pdf_refs = _write_report_pdf_refs(
        builder,
        company_dir=company_dir,
        identity=identity,
        report_id=row["report_id"],
        task_id=row["task_id"],
        evidence=evidence,
        report_json=report_json,
        row=row,
    )

    company_json = {
        "schema_version": 1,
        **identity,
        "primary_report_id": company_reports[0]["report_id"],
        "reports": company_reports,
        "metrics": _company_metrics_index(company_reports),
        "evidence": {
            "evidence_index": "evidence/evidence_index.json",
            "pdf_refs": "evidence/pdf_refs.json",
            "image_manifest": "evidence/image_manifest.json",
        },
        "generated_at": builder.now_iso(),
    }
    builder.write_json(company_json_path, company_json)
    (company_dir / "company.md").write_text(_build_generic_company_md(identity, company_reports), encoding="utf-8")
    analysis_readme = company_dir / "analysis" / "README.md"
    if not analysis_readme.exists():
        analysis_readme.write_text(_build_generic_analysis_readme(identity), encoding="utf-8")

    _update_catalogs(identity, report_entry, report_json, row)
    generic_manifest = _read_json(WIKI_ROOT / "_meta" / "generic_wiki_import_manifest.json", {"schema_version": 1, "imports": []})
    imports = [
        item for item in (generic_manifest.get("imports") or [])
        if not ((item or {}).get("task_id") == task_id and (item or {}).get("report_id") == row["report_id"])
    ]
    imports.append({
        "task_id": task_id,
        "company_id": identity["company_id"],
        "subject_code": identity["stock_code"],
        "report_id": row["report_id"],
        "source_results_dir": str(result_dir),
        "imported_at": _now_iso(),
    })
    generic_manifest.update({
        "schema_version": generic_manifest.get("schema_version") or 1,
        "generated_at": _now_iso(),
        "import_count": len(imports),
        "imports": imports[-200:],
    })
    _write_json(WIKI_ROOT / "_meta" / "generic_wiki_import_manifest.json", generic_manifest)

    return {
        "ok": True,
        "taskId": task_id,
        "companyDir": identity["company_id"],
        "reportId": row["report_id"],
        "resultDir": str(result_dir),
        "reportDir": str(report_dir),
        "artifactManifest": str(report_dir / ARTIFACT_MANIFEST_NAME),
        "artifactBundleSha256": artifact_bundle["bundleSha256"],
        "storagePolicy": "lightweight_manifest_only",
        "importRoute": "generic_non_a_share",
        "status": report_json.get("status") or "ready",
        "warnings": row.get("warnings") or [],
        "pdfRefsCount": len(pdf_refs),
        "wiki": _wiki_import_status(task_id),
    }


def _import_task_to_market_wiki(task_id: str, market: str | None = None) -> dict:
    task_id = _safe_task_id(task_id)
    market = str(market or _infer_task_market(task_id) or "").upper()
    if market not in PDF_MARKET_WIKI_INGEST_SCRIPTS:
        raise HTTPException(422, f"未配置 {market or 'UNKNOWN'} 市场 PDF Wiki 入库脚本")
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        raise HTTPException(404, f"解析产物目录不存在，默认读取 {PDF_RESULTS_ROOT}/<task_id>")
    artifact_bundle = _artifact_bundle_status(task_id, write_manifest=False)
    if not artifact_bundle["ready"]:
        raise HTTPException(422, {
            "message": f"{market} 解析产物包不完整，不能安全导入 Wiki",
            "missing": artifact_bundle["missing"],
            "invalid_json": artifact_bundle.get("invalidJson") or [],
            "warnings": artifact_bundle.get("warnings") or [],
        })
    script = PDF_MARKET_WIKI_INGEST_SCRIPTS[market]
    if not script.is_file():
        raise HTTPException(500, f"{market} Wiki import script not found: {script}")
    wiki_root = _wiki_root_for_market(market)
    result = _run_command([
        sys.executable,
        str(script),
        "--results-dir",
        str(result_dir.parent),
        "--output-root",
        str(wiki_root),
        "--apply",
    ], timeout=900)
    if result["returnCode"] != 0:
        raise HTTPException(500, {"stage": f"{market.lower()}_wiki_import", **result})
    wiki_status = _wiki_import_status_at_root(task_id, wiki_root, market)
    if wiki_status.get("status") == "missing":
        raise HTTPException(500, {
            "stage": f"{market.lower()}_wiki_import",
            "message": "市场 Wiki 入库脚本已执行，但未能在目标市场 Wiki 中找到该 task",
            "result": result,
            "wiki": wiki_status,
        })
    return {
        "ok": True,
        "taskId": task_id,
        "market": market,
        "resultDir": str(result_dir),
        "wikiRoot": str(wiki_root),
        "companyDir": wiki_status.get("companyDir") or "",
        "reportId": wiki_status.get("reportId") or "",
        "reportDir": wiki_status.get("reportDir") or "",
        "artifactManifest": wiki_status.get("manifestPath") or "",
        "artifactBundleSha256": artifact_bundle["bundleSha256"],
        "storagePolicy": "market_wiki_root",
        "importRoute": f"{market.lower()}_pdf_market_wiki_import",
        "result": result,
        "wiki": wiki_status,
    }


def _import_task_to_wiki(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    market = _infer_task_market(task_id)
    if market in PDF_MARKET_WIKI_INGEST_SCRIPTS:
        return _import_task_to_market_wiki(task_id, market)
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        raise HTTPException(404, f"解析产物目录不存在，默认读取 {PDF_RESULTS_ROOT}/<task_id>")
    artifact_bundle = _artifact_bundle_status(task_id, write_manifest=True)
    if not artifact_bundle["ready"]:
        raise HTTPException(422, {
            "message": "解析产物包不完整，不能安全导入 Wiki",
            "missing": artifact_bundle["missing"],
            "invalid_json": artifact_bundle.get("invalidJson") or [],
            "warnings": artifact_bundle.get("warnings") or [],
        })

    builder = _load_wiki_builder()
    tasks = builder.load_tasks(PDF_TASK_DB_PATH)
    row = builder.inspect_result_dir(result_dir, tasks)
    if _looks_like_generic_foreign_report(row):
        raise HTTPException(422, {
            "message": "该文件看起来是非 A 股/通用主体报告，请使用“通用主体入库”，避免误识别为 A 股公司",
            "importRoute": "generic_non_a_share",
            "filename": row.get("filename") or "",
            "detectedIdentity": row.get("identity") or {},
        })
    row = _canonicalize_row_identity(builder, row)
    if not row.get("identity", {}).get("stock_code"):
        raise HTTPException(422, {"message": "无法识别股票代码，不能安全导入 Wiki", "warnings": row.get("warnings") or []})
    if not row.get("report_year"):
        raise HTTPException(422, {"message": "无法识别报告年份，不能安全导入 Wiki", "warnings": row.get("warnings") or []})

    if not row["markdown_path"].exists():
        fallback_md = result_dir / "result.md"
        if not fallback_md.exists():
            raise HTTPException(404, "result_complete.md/result.md not found for task")
        row["markdown_path"] = fallback_md
        row["markdown"] = fallback_md.read_text("utf-8", errors="ignore")

    identity = row["identity"]
    company_dir = WIKI_ROOT / "companies" / identity["company_id"]
    report_dir = company_dir / "reports" / row["report_id"]
    for subdir in ["metrics", "reports", "evidence", "analysis"]:
        (company_dir / subdir).mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(row["markdown_path"], report_dir / "report.md")
    shutil.copy2(result_dir / "document_full.json", report_dir / "document_full.json")
    wiki_manifest = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "storage_policy": {
            "mode": "lightweight_manifest_only",
            "wiki_keeps": ["report.md", "report.json", "document_full.json", ARTIFACT_MANIFEST_NAME, "company/evidence/semantic indexes"],
            "full_parse_archive": "PostgreSQL pdf2md schema plus pdf2md results directory",
            "reason": "Wiki 作为知识入口和证据索引，不重复存放全量解析产物包。",
        },
        "task_id": task_id,
        "source_results_dir": str(result_dir),
        "core": {
            "status": artifact_bundle["status"],
            "ready": artifact_bundle["ready"],
            "ready_count": artifact_bundle["readyCount"],
            "total": artifact_bundle["total"],
            "missing": artifact_bundle["missing"],
            "bundle_sha256": artifact_bundle["bundleSha256"],
        },
        "artifacts": artifact_bundle["artifacts"],
        "checks": {
            "schema_mismatches": artifact_bundle["schemaMismatches"],
            "rule_mismatches": artifact_bundle["ruleMismatches"],
            "warnings": artifact_bundle["warnings"],
        },
    }
    builder.write_json(report_dir / ARTIFACT_MANIFEST_NAME, wiki_manifest)

    v641 = _read_json(WIKI_ROOT / "derived" / "three_statements_latest.json", {})
    v641_company = v641.get(identity["stock_code"]) if isinstance(v641, dict) else None
    three_statement_payload = builder.build_three_statement_payload(row, v641_company)
    three_statement_source = builder.three_statement_payload_source(three_statement_payload)
    images = builder.copy_report_assets(row, report_dir)
    evidence = (
        builder.build_three_statement_evidence(row, three_statement_payload)
        if three_statement_payload
        else builder.build_fallback_evidence(row)
    )
    report_json = builder.build_report_json(row, images, evidence)
    builder.write_json(report_dir / "report.json", report_json)

    company_json_path = company_dir / "company.json"
    existing_company = _read_json(company_json_path, {})
    report_entry = {
        "report_id": row["report_id"],
        "report_year": row["report_year"],
        "report_kind": row["report_kind"],
        "status": report_json["status"],
        "task_id": row["task_id"],
        "source_filename": row["filename"],
        "report_md": f"reports/{row['report_id']}/report.md",
        "report_json": f"reports/{row['report_id']}/report.json",
        "document_full": f"reports/{row['report_id']}/document_full.json",
        "artifact_manifest": f"reports/{row['report_id']}/{ARTIFACT_MANIFEST_NAME}",
        "artifact_bundle_sha256": artifact_bundle["bundleSha256"],
        "metrics": _metric_paths_for_report(row["report_id"]),
    }
    company_reports = _merge_by_report_id(existing_company.get("reports") or [], report_entry)
    is_primary_report = bool(company_reports and company_reports[0].get("report_id") == row["report_id"])

    generated_at = builder.now_iso()
    three_statement_metrics = {
        "schema_version": 1,
        "source": three_statement_source,
        "unit": "亿元",
        "data": three_statement_payload or {},
        "generated_at": generated_at,
    }
    key_metrics = {
        "schema_version": 1,
        "source": "financial_data.json",
        "data": row["financial_data"].get("key_metrics") or [],
        "generated_at": generated_at,
    }
    validation = {
        "schema_version": 1,
        "financial_checks": row["financial_checks"],
        "wiki_v641_available": bool(v641_company),
        "three_statement_source": three_statement_source,
        "three_statement_metric_count": len((three_statement_payload or {}).get("metrics") or []),
        "generated_at": generated_at,
    }
    _write_metrics_bundle(
        company_dir,
        row["report_id"],
        three_statement_metrics,
        key_metrics,
        validation,
        mirror_latest=is_primary_report,
    )

    old_evidence_payload = _read_json(company_dir / "evidence" / "evidence_index.json", {"evidence": []})
    merged_evidence = _filter_report(old_evidence_payload.get("evidence") or [], row["report_id"]) + evidence
    builder.write_json(company_dir / "evidence" / "evidence_index.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "evidence_count": len(merged_evidence),
        "evidence": merged_evidence,
        "generated_at": builder.now_iso(),
    })

    old_images_payload = _read_json(company_dir / "evidence" / "image_manifest.json", {"images": []})
    merged_images = _filter_report(old_images_payload.get("images") or [], row["report_id"]) + images
    builder.write_json(company_dir / "evidence" / "image_manifest.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "images": merged_images,
        "generated_at": builder.now_iso(),
    })

    pdf_refs = _write_report_pdf_refs(
        builder,
        company_dir=company_dir,
        identity=identity,
        report_id=row["report_id"],
        task_id=row["task_id"],
        evidence=evidence,
        report_json=report_json,
        row=row,
    )

    company_json = {
        "schema_version": 1,
        **identity,
        "primary_report_id": company_reports[0]["report_id"],
        "reports": company_reports,
        "metrics": _company_metrics_index(company_reports),
        "evidence": {
            "evidence_index": "evidence/evidence_index.json",
            "pdf_refs": "evidence/pdf_refs.json",
            "image_manifest": "evidence/image_manifest.json",
        },
        "generated_at": builder.now_iso(),
    }
    builder.write_json(company_json_path, company_json)
    (company_dir / "company.md").write_text(builder.build_company_md(identity, company_reports, three_statement_payload), encoding="utf-8")
    analysis_readme = company_dir / "analysis" / "README.md"
    if not analysis_readme.exists():
        analysis_readme.write_text(builder.build_analysis_readme(identity), encoding="utf-8")

    _update_catalogs(identity, report_entry, report_json, row, three_statement_payload, three_statement_source)
    naming_check = _repair_and_validate_wiki_naming()
    derived_check = _refresh_derived_three_statement_metrics(builder)
    company_dir_name = _find_company_for_task(task_id) or identity["company_id"]
    report_dir = WIKI_ROOT / "companies" / company_dir_name / "reports" / row["report_id"]
    return {
        "ok": True,
        "taskId": task_id,
        "companyDir": company_dir_name,
        "reportId": row["report_id"],
        "resultDir": str(result_dir),
        "reportDir": str(report_dir),
        "artifactManifest": str(report_dir / ARTIFACT_MANIFEST_NAME),
        "artifactBundleSha256": artifact_bundle["bundleSha256"],
        "storagePolicy": "lightweight_manifest_only",
        "status": report_json.get("status") or "ready",
        "warnings": row.get("warnings") or [],
        "pdfRefsCount": len(pdf_refs),
        "naming": naming_check,
        "derived": derived_check,
        "wiki": _wiki_import_status(task_id),
    }


def _db_status(task_id: str) -> dict:
    try:
        import psycopg
    except Exception as exc:
        return {"status": "unknown", "message": f"psycopg unavailable: {exc}"}

    config = _pdf2md_db_connect_config()
    document_full = _find_task_document_full(task_id)
    current_sha = _sha256_file(document_full) if document_full else None
    try:
        with psycopg.connect(**config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      EXISTS(SELECT 1 FROM pdf2md.documents WHERE task_id = %s) AS imported,
                      (SELECT count(*) FROM pdf2md.financial_statement_items WHERE task_id = %s) AS statement_items,
                      (SELECT count(*) FROM pdf2md.document_tables WHERE task_id = %s) AS tables,
                      (SELECT count(*) FROM pdf2md.financial_key_metrics WHERE task_id = %s) AS key_metrics,
                      (SELECT count(*) FROM pdf2md.content_blocks WHERE task_id = %s) AS content_blocks,
                      (SELECT count(*) FROM pdf2md.document_pages WHERE task_id = %s) AS pages,
                      (SELECT raw_json_hash FROM pdf2md.documents WHERE task_id = %s) AS raw_json_hash,
                      (SELECT updated_at FROM pdf2md.documents WHERE task_id = %s) AS updated_at
                    """,
                    (task_id, task_id, task_id, task_id, task_id, task_id, task_id, task_id),
                )
                row = cur.fetchone()
        imported = bool(row[0]) if row else False
        imported_sha = row[6] if row else None
        stale = bool(imported and current_sha and imported_sha and imported_sha != current_sha)
        child_counts = {
            "statementItems": int(row[1] or 0) if row else 0,
            "tables": int(row[2] or 0) if row else 0,
            "keyMetrics": int(row[3] or 0) if row else 0,
            "contentBlocks": int(row[4] or 0) if row else 0,
            "pages": int(row[5] or 0) if row else 0,
        }
        has_child_rows = any(child_counts.values())
        status = "stale" if stale else ("partial" if imported and not has_child_rows else ("ready" if imported else "missing"))
        message = ""
        if stale:
            message = "PostgreSQL 需重新导入"
        elif imported and not has_child_rows:
            message = "PostgreSQL 只有主文档记录，缺少子表数据，需重新导入"
        return {
            "status": status,
            "imported": imported,
            **child_counts,
            "rawJsonHash": imported_sha or "",
            "sourceDocumentFullSha256": current_sha or "",
            "updatedAt": row[7].isoformat() if row and row[7] else None,
            "stale": stale,
            "partial": bool(imported and not has_child_rows),
            "message": message,
        }
    except Exception as exc:
        return {"status": "unknown", "message": str(exc)}


def _run_command(args: list[str], timeout: int = 180, env: dict[str, str] | None = None) -> dict:
    completed = run_subprocess_command(args, timeout=timeout, env=env)
    return {
        "returnCode": completed.returncode,
        "stdout": completed.stdout[-6000:],
        "stderr": completed.stderr[-6000:],
    }


def _semantic_provider_from_settings(settings: dict) -> tuple[str, dict]:
    providers = settings.get("providers") or {}
    active_key = str(settings.get("activeProvider") or "local")
    preferred = providers.get(active_key)
    if isinstance(preferred, dict) and preferred.get("enabled", True):
        return active_key, preferred
    for key in ("local", "cloud"):
        provider = providers.get(key)
        if isinstance(provider, dict) and provider.get("enabled", True):
            return key, provider
    return active_key, {}


def _set_semantic_provider_env(env: dict[str, str], provider_key: str, provider: dict) -> dict[str, str]:
    base_url = str(provider.get("baseUrl") or "").strip().rstrip("/")
    model = str(provider.get("model") or "").strip()
    api_key = str(provider.get("apiKey") or "").strip()
    chat_template_kwargs = provider.get("chatTemplateKwargs") if isinstance(provider.get("chatTemplateKwargs"), dict) else {}

    if base_url.startswith("hermes://"):
        mode = infer_model_mode(
            provider_name=str(provider.get("providerName") or ""),
            provider=str(provider.get("provider") or ""),
            model=model,
            base_url=base_url,
        )
        if mode:
            try:
                set_all_profile_model_modes(mode)
            except Exception:
                pass
        try:
            profile_config = hermes_profile_config("siq_analysis")
        except Exception:
            profile_config = {}
        runs_url = str(profile_config.get("base") or "").rstrip("/")
        env["SIQ_LLM_SEMANTIC_HERMES_PROFILE"] = "siq_analysis"
        env["FINSIGHT_LLM_SEMANTIC_HERMES_PROFILE"] = "siq_analysis"
        if runs_url:
            env["SIQ_LLM_SEMANTIC_HERMES_RUNS_URL"] = runs_url
            env["FINSIGHT_LLM_SEMANTIC_HERMES_RUNS_URL"] = runs_url
        if mode:
            env["SIQ_LLM_SEMANTIC_HERMES_MODE"] = mode
            env["FINSIGHT_LLM_SEMANTIC_HERMES_MODE"] = mode
        env["SIQ_LLM_SEMANTIC_PROVIDER_BASE_URL"] = base_url
        env["FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"] = base_url
        model = str(profile_config.get("model") or model or "siq_analysis")
        api_key = ""

    def set_many(suffix: str, value: object) -> None:
        raw = str(value)
        env[f"SIQ_{suffix}"] = raw
        env[f"FINSIGHT_{suffix}"] = raw

    set_many("LLM_SEMANTIC_PROVIDER_BASE_URL", base_url)
    set_many("LLM_SEMANTIC_MODEL", model)
    set_many("LLM_SEMANTIC_API_KEY", api_key)
    if base_url:
        set_many("LOCAL_LLM_BASE_URL", base_url)
    if model:
        set_many("LOCAL_LLM_MODEL", model)
    set_many("LOCAL_LLM_API_KEY", api_key)
    if provider.get("timeoutSeconds"):
        set_many("LLM_SEMANTIC_TIMEOUT", provider["timeoutSeconds"])
    if provider.get("maxTokens"):
        set_many("LLM_SEMANTIC_MAX_TOKENS", provider["maxTokens"])
    if provider.get("temperature") is not None:
        set_many("LLM_SEMANTIC_TEMPERATURE", provider["temperature"])
    if chat_template_kwargs:
        set_many("LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS", json.dumps(chat_template_kwargs, ensure_ascii=False))
    env["SIQ_LLM_SEMANTIC_PROVIDER"] = provider_key
    env["FINSIGHT_LLM_SEMANTIC_PROVIDER"] = provider_key
    return env


def _llm_semantic_env() -> dict[str, str]:
    env = os.environ.copy()
    try:
        settings = load_llm_settings(include_secrets=True)
    except Exception:
        return env
    provider_key, provider = _semantic_provider_from_settings(settings)
    if not isinstance(provider, dict):
        return env
    return _set_semantic_provider_env(env, provider_key, provider)


def _workflow_preflight(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    market = _infer_task_market(task_id)
    wiki_root = _wiki_root_for_market(market)
    artifacts = _artifact_bundle_status(task_id)
    wiki = _wiki_import_status_at_root(task_id, wiki_root, market)
    company_dir = wiki.get("companyDir") or _find_company_for_task_at_root(task_id, wiki_root)
    semantic = _semantic_status_at_root(company_dir, task_id, wiki_root)
    obsidian = _obsidian_status_at_root(company_dir, semantic, wiki_root)
    database = _db_status(task_id)
    checks = [
        {
            "id": "artifact_bundle",
            "label": "解析产物包",
            "ok": artifacts["ready"],
            "status": artifacts["status"],
            "message": artifacts["message"],
            "blocking": not artifacts["ready"],
        },
        {
            "id": "wiki_identity",
            "label": "Wiki 身份识别",
            "ok": wiki["status"] in {"ready", "stale"} or artifacts["ready"],
            "status": wiki["status"],
            "message": wiki["message"],
            "blocking": False,
        },
        {
            "id": "semantic_script",
            "label": "规则语义层脚本",
            "ok": SEMANTIC_SCRIPT.is_file(),
            "status": "ready" if SEMANTIC_SCRIPT.is_file() else "missing",
            "message": str(SEMANTIC_SCRIPT),
            "blocking": not SEMANTIC_SCRIPT.is_file(),
        },
        {
            "id": "obsidian_script",
            "label": "Obsidian 图谱脚本",
            "ok": OBSIDIAN_SCRIPT.is_file(),
            "status": "ready" if OBSIDIAN_SCRIPT.is_file() else "missing",
            "message": str(OBSIDIAN_SCRIPT),
            "blocking": not OBSIDIAN_SCRIPT.is_file(),
        },
        {
            "id": "db_import_script",
            "label": "PostgreSQL 入库脚本",
            "ok": DB_IMPORT_SCRIPT.is_file(),
            "status": "ready" if DB_IMPORT_SCRIPT.is_file() else "missing",
            "message": str(DB_IMPORT_SCRIPT),
            "blocking": not DB_IMPORT_SCRIPT.is_file(),
        },
    ]
    if LLM_SEMANTIC_ENABLED:
        checks.insert(4, {
            "id": "llm_semantic_script",
            "label": "项目设置模型语义增强脚本",
            "ok": LLM_SEMANTIC_SCRIPT.is_file(),
            "status": "ready" if LLM_SEMANTIC_SCRIPT.is_file() else "missing",
            "message": str(LLM_SEMANTIC_SCRIPT),
            "blocking": LLM_SEMANTIC_REQUIRED and not LLM_SEMANTIC_SCRIPT.is_file(),
        })
    if database["status"] == "unknown":
        checks.append({
            "id": "database_connection",
            "label": "PostgreSQL 连接",
            "ok": False,
            "status": "unknown",
            "message": database.get("message") or "无法确认数据库状态",
            "blocking": False,
        })
    blocking = [item for item in checks if item.get("blocking")]
    return {
        "ok": not blocking,
        "checks": checks,
        "blocking": blocking,
        "artifacts": artifacts,
        "wiki": wiki,
        "semantic": semantic,
        "obsidian": obsidian,
        "database": database,
    }


def _workflow_status_payload(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    market = _infer_task_market(task_id)
    wiki_root = _wiki_root_for_market(market)
    artifact_bundle = _artifact_bundle_status(task_id)
    document_full = _find_task_document_full(task_id)
    wiki = _wiki_import_status_at_root(task_id, wiki_root, market)
    company_dir = wiki.get("companyDir") or _find_company_for_task_at_root(task_id, wiki_root)
    semantic = _semantic_status_at_root(company_dir, task_id, wiki_root)
    obsidian = _obsidian_status_at_root(company_dir, semantic, wiki_root)
    database = _db_status(task_id)
    return {
        "taskId": task_id,
        "market": market,
        "wikiRoot": str(wiki_root),
        "artifactBundle": artifact_bundle,
        "documentFull": {
            "status": "ready" if document_full else "missing",
            "path": str(document_full) if document_full else "",
            "sha256": _sha256_file(document_full) if document_full else None,
        },
        "wiki": wiki,
        "semantic": semantic,
        "obsidian": obsidian,
        "database": database,
        "preflight": _workflow_preflight(task_id),
    }


def _document_artifact_status(task_id: str) -> dict:
    return document_workflow_service.document_artifact_status(
        task_id,
        safe_task_id=_safe_task_id,
        find_result_dir=_find_document_result_dir,
        core_artifacts=DOCUMENT_CORE_ARTIFACTS,
        artifact_file_info=_artifact_file_info,
    )


def _document_wiki_status(task_id: str, collection: str | None = None) -> dict:
    return document_workflow_service.document_wiki_status(
        task_id,
        collection,
        safe_task_id=_safe_task_id,
        safe_collection=_safe_document_collection,
        find_result_dir=_find_document_result_dir,
        read_json=_read_json,
        sha256_file=_sha256_file,
        wiki_root=DOCUMENT_WIKI_ROOT,
        package_manifest_name=DOCUMENT_PACKAGE_MANIFEST_NAME,
        document_key_from_manifest=_document_key_from_manifest,
    )


def _document_workflow_status_payload(task_id: str, collection: str | None = None) -> dict:
    return document_workflow_service.document_workflow_status_payload(
        task_id,
        collection,
        safe_task_id=_safe_task_id,
        artifact_status=_document_artifact_status,
        wiki_status=_document_wiki_status,
        postgres_status=_document_postgres_status,
        milvus_status=_document_milvus_status,
    )


def _document_postgres_status(task_id: str, collection: str | None = None) -> dict:
    wiki = _document_wiki_status(task_id, collection)
    return document_workflow_service.document_postgres_status_payload(
        task_id=_safe_task_id(task_id),
        wiki_status=wiki,
        script_path=DOCUMENT_DB_IMPORT_SCRIPT,
        script_exists=DOCUMENT_DB_IMPORT_SCRIPT.is_file(),
    )


def _document_milvus_status(task_id: str, collection: str | None = None) -> dict:
    wiki = _document_wiki_status(task_id, collection)
    package_dir = Path(str(wiki.get("path") or ""))
    chunks_path = package_dir / "semantic" / "chunks.jsonl"
    report_path = package_dir / "semantic" / "ingest_report.json"
    report = _read_json(report_path, {}) if report_path.is_file() else {}
    chunk_count = 0
    if chunks_path.is_file():
        chunk_count = sum(1 for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return document_workflow_service.document_milvus_status_payload(
        wiki_status=wiki,
        script_path=DOCUMENT_CHUNK_SCRIPT,
        script_exists=DOCUMENT_CHUNK_SCRIPT.is_file(),
        chunks_exists=chunks_path.is_file(),
        chunk_count=chunk_count,
        report_path=report_path if report_path.is_file() else None,
        report=report if isinstance(report, dict) else {},
    )


def _write_document_package_readme(package_dir: Path, package_manifest: dict, document_markdown: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text(
        document_workflow_service.document_package_readme_content(package_manifest, document_markdown),
        encoding="utf-8",
    )


def _import_document_task_to_wiki(task_id: str, collection: str | None = None) -> dict:
    task_id = _safe_task_id(task_id)
    collection_name = _safe_document_collection(collection)
    result_dir = _find_document_result_dir(task_id)
    if not result_dir:
        raise HTTPException(404, "document parser result not found for task")
    artifact_status = _document_artifact_status(task_id)
    if not artifact_status["ready"]:
        raise HTTPException(422, {"message": "通用文档解析产物不完整", "missing": artifact_status["missing"]})

    manifest = _read_json(result_dir / "manifest.json", {}) or {}
    document_key = _document_key_from_manifest(task_id, manifest)
    package_dir = DOCUMENT_WIKI_ROOT / collection_name / document_key
    package_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    for name in DOCUMENT_WIKI_LIGHTWEIGHT_ARTIFACTS:
        if _copy_file_if_exists(result_dir / name, package_dir / _document_package_target(name)):
            copied_files.append(name)

    copied_directories: dict[str, int] = {}
    for rel_dir in DOCUMENT_WIKI_RETAINED_DIRS:
        copied_count = _copy_tree_contents(result_dir / rel_dir, package_dir / rel_dir)
        if copied_count:
            copied_directories[rel_dir] = copied_count

    document_markdown = (result_dir / "document.md").read_text(encoding="utf-8")
    document_full_sha = _sha256_file(result_dir / "document_full.json")
    package_manifest, artifact_manifest = document_workflow_service.build_document_package_manifests(
        task_id=task_id,
        collection_name=collection_name,
        document_key=document_key,
        result_dir=result_dir,
        parser_manifest=manifest,
        created_at=_now_iso(),
        document_full_sha=document_full_sha,
        core_artifacts=DOCUMENT_CORE_ARTIFACTS,
        optional_artifacts=DOCUMENT_OPTIONAL_ARTIFACTS,
        lightweight_artifacts=set(DOCUMENT_WIKI_LIGHTWEIGHT_ARTIFACTS),
        copied_files=copied_files,
        retained_dirs=DOCUMENT_WIKI_RETAINED_DIRS,
        package_manifest_name=DOCUMENT_PACKAGE_MANIFEST_NAME,
        artifact_manifest_name=ARTIFACT_MANIFEST_NAME,
        sha256_file=_sha256_file,
        json_artifact_meta=_json_artifact_meta,
    )
    _write_json(package_dir / DOCUMENT_PACKAGE_MANIFEST_NAME, package_manifest)
    _write_json(package_dir / ARTIFACT_MANIFEST_NAME, artifact_manifest)
    _write_document_package_readme(package_dir, package_manifest, document_markdown)

    index_path = DOCUMENT_WIKI_ROOT / collection_name / "index.json"
    index_payload = _read_json(index_path, {"schema_version": "generic_document_collection_index_v1", "collection": collection_name, "documents": []}) or {}
    _write_json(
        index_path,
        document_workflow_service.build_document_collection_index(
            index_payload,
            task_id=task_id,
            collection_name=collection_name,
            package_dir=package_dir,
            package_manifest=package_manifest,
            generated_at=_now_iso(),
        ),
    )

    return {
        "ok": True,
        "taskId": task_id,
        "collection": collection_name,
        "documentKey": document_key,
        "packageDir": str(package_dir),
        "manifestPath": str(package_dir / DOCUMENT_PACKAGE_MANIFEST_NAME),
        "copiedFiles": copied_files,
        "copiedDirectories": copied_directories,
        "wiki": _document_wiki_status(task_id, collection_name),
    }


def _document_package_target(artifact_name: str) -> str:
    return document_workflow_service.document_package_target(artifact_name)


@router.get("/task/{task_id}/status")
def task_workflow_status(task_id: str):
    return _workflow_status_payload(task_id)


@router.get("/document/{task_id}/status")
def document_workflow_status(task_id: str, collection: str = "default"):
    return _document_workflow_status_payload(task_id, collection)


@router.post("/document/{task_id}/wiki-import")
def import_document_task_to_wiki(task_id: str, collection: str = "default"):
    return _import_document_task_to_wiki(task_id, collection)


@router.post("/document/{task_id}/db-import")
def import_document_task_to_database(task_id: str, collection: str = "default"):
    task_id = _safe_task_id(task_id)
    wiki = _document_wiki_status(task_id, collection)
    if wiki.get("status") not in {"ready", "stale"}:
        raise HTTPException(422, "请先导入通用文档 Wiki 包")
    if not DOCUMENT_DB_IMPORT_SCRIPT.is_file():
        raise HTTPException(500, f"Document DB import script not found: {DOCUMENT_DB_IMPORT_SCRIPT}")
    package_dir = Path(str(wiki.get("path") or ""))
    if not (package_dir / DOCUMENT_PACKAGE_MANIFEST_NAME).is_file():
        raise HTTPException(404, "Document Wiki package manifest not found")
    pg_config = _db_connect_config()
    database_url = _postgres_database_url(pg_config)
    command = document_workflow_service.document_db_import_plan(
        executable=sys.executable,
        script_path=DOCUMENT_DB_IMPORT_SCRIPT,
        package_dir=package_dir,
        base_env=os.environ,
        pg_config=pg_config,
        database_url=database_url,
    )
    result = _run_command(
        command["args"],
        timeout=command["timeout"],
        env=command["env"],
    )
    if result["returnCode"] != 0:
        raise HTTPException(500, result)
    return {
        "ok": True,
        "taskId": task_id,
        "collection": _safe_document_collection(collection),
        "packageDir": str(package_dir),
        "result": result,
        "postgres": _document_postgres_status(task_id, collection),
    }


@router.post("/document/{task_id}/semantic")
def build_document_semantic_chunks(
    task_id: str,
    collection: str = "default",
    milvus: bool = False,
):
    task_id = _safe_task_id(task_id)
    wiki = _document_wiki_status(task_id, collection)
    if wiki.get("status") not in {"ready", "stale"}:
        raise HTTPException(422, "请先导入通用文档 Wiki 包")
    if not DOCUMENT_CHUNK_SCRIPT.is_file():
        raise HTTPException(500, f"Document chunk script not found: {DOCUMENT_CHUNK_SCRIPT}")
    package_dir = Path(str(wiki.get("path") or ""))
    command = document_workflow_service.document_semantic_plan(
        executable=sys.executable,
        script_path=DOCUMENT_CHUNK_SCRIPT,
        package_dir=package_dir,
        milvus=milvus,
    )
    result = _run_command(command["args"], timeout=command["timeout"])
    if result["returnCode"] != 0:
        raise HTTPException(500, result)
    return {
        "ok": True,
        "taskId": task_id,
        "collection": _safe_document_collection(collection),
        "packageDir": str(package_dir),
        "result": result,
        "semanticMode": command["semantic_mode"],
        "milvus": _document_milvus_status(task_id, collection),
    }


@router.post("/task/{task_id}/wiki-import")
def import_task_to_wiki(task_id: str):
    return _import_task_to_wiki(task_id)


@router.post("/task/{task_id}/wiki-import-generic")
def import_task_to_generic_wiki(task_id: str):
    return _import_task_to_generic_wiki(task_id)


def extract_semantic_for_task(task_id: str):
    task_id = _safe_task_id(task_id)
    company_dir = _find_company_for_task(task_id)
    if not company_dir:
        raise HTTPException(404, "Task is not linked to a Wiki company")
    pre_naming_check = _repair_and_validate_wiki_naming()
    company_dir = _find_company_for_task(task_id) or company_dir
    if not SEMANTIC_SCRIPT.is_file():
        raise HTTPException(500, f"Semantic script not found: {SEMANTIC_SCRIPT}")
    rule_result = _run_command([
        sys.executable,
        str(SEMANTIC_SCRIPT),
        "--wiki-root",
        str(WIKI_ROOT),
        "--company",
        company_dir,
    ])
    if rule_result["returnCode"] != 0:
        raise HTTPException(500, {"stage": "rule_semantic", **rule_result})

    llm_result = None
    if LLM_SEMANTIC_ENABLED:
        if not LLM_SEMANTIC_SCRIPT.is_file():
            detail = {"stage": "llm_semantic", "returnCode": 127, "stdout": "", "stderr": f"LLM semantic script not found: {LLM_SEMANTIC_SCRIPT}"}
            if LLM_SEMANTIC_REQUIRED:
                raise HTTPException(500, detail)
            llm_result = detail
        else:
            llm_result = _run_command([
                sys.executable,
                str(LLM_SEMANTIC_SCRIPT),
                "--wiki-root",
                str(WIKI_ROOT),
                "--company",
                company_dir,
            ], timeout=LLM_SEMANTIC_TIMEOUT, env=_llm_semantic_env())
            if llm_result["returnCode"] != 0 and LLM_SEMANTIC_REQUIRED:
                raise HTTPException(500, {"stage": "llm_semantic", **llm_result})

    obsidian_result = _generate_obsidian_for_company(company_dir)
    post_naming_check = _repair_and_validate_wiki_naming()
    return {
        "ok": True,
        "companyDir": company_dir,
        "result": {"rule": rule_result, "obsidian": obsidian_result, "llm": llm_result},
        "naming": {"before": pre_naming_check, "after": post_naming_check},
        "semantic": _semantic_status(company_dir, task_id),
    }


def extract_generic_semantic_for_task(task_id: str):
    task_id = _safe_task_id(task_id)
    market = _infer_task_market(task_id)
    is_pdf_market = market in PDF_MARKET_CODES
    wiki_root = _wiki_root_for_market(market)
    company_dir = _find_company_for_task_at_root(task_id, wiki_root) if is_pdf_market else _find_company_for_task(task_id)
    if not company_dir:
        raise HTTPException(404, "Task is not linked to a Wiki company")
    company_json = _read_json(wiki_root / "companies" / company_dir / "company.json", {}) or {}
    if not is_pdf_market and company_json.get("identity_route") != "generic_non_a_share_wiki_import":
        raise HTTPException(422, "该任务不是通用主体入库路线，请使用标准语义层接口")
    if not SEMANTIC_SCRIPT.is_file():
        raise HTTPException(500, f"Semantic script not found: {SEMANTIC_SCRIPT}")
    rule_result = _run_command([
        sys.executable,
        str(SEMANTIC_SCRIPT),
        "--wiki-root",
        str(wiki_root),
        "--company",
        company_dir,
    ])
    if rule_result["returnCode"] != 0:
        raise HTTPException(500, {"stage": "rule_semantic", **rule_result})

    llm_result = None
    if LLM_SEMANTIC_ENABLED:
        if not LLM_SEMANTIC_SCRIPT.is_file():
            detail = {"stage": "llm_semantic", "returnCode": 127, "stdout": "", "stderr": f"LLM semantic script not found: {LLM_SEMANTIC_SCRIPT}"}
            if LLM_SEMANTIC_REQUIRED:
                raise HTTPException(500, detail)
            llm_result = detail
        else:
            llm_result = _run_command([
                sys.executable,
                str(LLM_SEMANTIC_SCRIPT),
                "--wiki-root",
                str(wiki_root),
                "--company",
                company_dir,
            ], timeout=LLM_SEMANTIC_TIMEOUT, env=_llm_semantic_env())
            if llm_result["returnCode"] != 0 and LLM_SEMANTIC_REQUIRED:
                raise HTTPException(500, {"stage": "llm_semantic", **llm_result})

    obsidian_result = _generate_obsidian_for_company_at_root(company_dir, wiki_root) if is_pdf_market else _generate_obsidian_for_company(company_dir)
    semantic_status = _semantic_status_at_root(company_dir, task_id, wiki_root) if is_pdf_market else _semantic_status(company_dir, task_id)
    return {
        "ok": True,
        "market": market,
        "wikiRoot": str(wiki_root),
        "companyDir": company_dir,
        "result": {"rule": rule_result, "obsidian": obsidian_result, "llm": llm_result},
        "semantic": semantic_status,
    }


@router.post("/task/{task_id}/semantic")
def start_semantic_for_task(task_id: str):
    task_id = _safe_task_id(task_id)
    return _start_workflow_step_job(task_id, "semantic", lambda: extract_semantic_for_task(task_id))


@router.post("/task/{task_id}/semantic-generic")
def start_generic_semantic_for_task(task_id: str):
    task_id = _safe_task_id(task_id)
    return _start_workflow_step_job(task_id, "semantic-generic", lambda: extract_generic_semantic_for_task(task_id))


@router.post("/task/{task_id}/db-import")
def import_task_to_database(task_id: str):
    task_id = _safe_task_id(task_id)
    document_full = _find_task_document_full(task_id)
    if not document_full:
        raise HTTPException(404, "document_full.json not found for task")
    if not DB_IMPORT_SCRIPT.is_file():
        raise HTTPException(500, f"DB import script not found: {DB_IMPORT_SCRIPT}")
    args = [sys.executable, str(DB_IMPORT_SCRIPT), str(document_full), "--ddl"]
    if DB_CONFIG_PY.is_file():
        args += ["--config-py", str(DB_CONFIG_PY)]
        command_env = None
    else:
        pg_config = _pdf2md_db_connect_config()
        args += ["--database-url", _postgres_database_url(pg_config)]
        command_env = os.environ.copy()
        command_env.update({
            "PGHOST": str(pg_config["host"]),
            "PGPORT": str(pg_config["port"]),
            "PGDATABASE": str(pg_config["dbname"]),
            "PGUSER": str(pg_config["user"]),
            "PGPASSWORD": str(pg_config["password"]),
            "DATABASE_URL": _postgres_database_url(pg_config),
        })
    result = _run_command(args, timeout=300, env=command_env)
    if result["returnCode"] != 0:
        raise HTTPException(500, result)
    return {"ok": True, "taskId": task_id, "documentFull": str(document_full), "result": result, "database": _db_status(task_id)}


def _job_update(job_id: str, **updates) -> None:
    with _job_lock:
        if update_workflow_job(_workflow_jobs, job_id, now=_now_iso, **updates):
            _persist_workflow_jobs_locked()


def _job_step(job_id: str, step: str, status: str, **updates) -> None:
    with _job_lock:
        if record_workflow_job_step(_workflow_jobs, job_id, step, status, now=_now_iso, **updates):
            _persist_workflow_jobs_locked()


def _http_exception_payload(exc: HTTPException) -> dict:
    return {
        "statusCode": exc.status_code,
        "detail": exc.detail,
    }


def _run_workflow_step_job(job_id: str, step: str, runner) -> None:
    try:
        _job_update(job_id, status="running")
        _job_step(job_id, step, "running")
        result = runner()
        _job_step(job_id, step, "succeeded", result=result)
        _job_update(job_id, status="succeeded", result=result)
    except HTTPException as exc:
        payload = _http_exception_payload(exc)
        _job_step(job_id, step, "failed", error=str(exc.detail), result=payload)
        _job_update(job_id, status="failed", error=str(exc.detail), result=payload)
    except Exception as exc:
        _job_step(job_id, step, "failed", error=str(exc))
        _job_update(job_id, status="failed", error=str(exc))


def _start_workflow_step_job(task_id: str, step: str, runner, *, metadata: dict | None = None) -> dict:
    job_id = uuid.uuid4().hex
    with _job_lock:
        job = create_workflow_job(_workflow_jobs, job_id=job_id, task_id=task_id, now=_now_iso)
        if metadata:
            job["metadata"] = metadata
        _persist_workflow_jobs_locked()
    thread = threading.Thread(target=_run_workflow_step_job, args=(job_id, step, runner), daemon=True)
    thread.start()
    return _workflow_jobs[job_id]


def _run_remaining_pipeline(job_id: str, task_id: str) -> None:
    try:
        _job_update(job_id, status="running")
        market = _infer_task_market(task_id)
        wiki_root = _wiki_root_for_market(market)
        status = _workflow_status_payload(task_id)
        if not status["artifactBundle"]["ready"]:
            _job_update(job_id, status="failed", error="解析产物包不完整")
            return

        if status["wiki"]["status"] != "ready":
            _job_step(job_id, "wiki-import", "running")
            result = _import_task_to_market_wiki(task_id, market) if market in PDF_MARKET_WIKI_INGEST_SCRIPTS else _import_task_to_wiki(task_id)
            _job_step(job_id, "wiki-import", "succeeded", result=result)
        else:
            _job_step(job_id, "wiki-import", "skipped", message="Wiki 已是最新")

        status = _workflow_status_payload(task_id)
        if status["semantic"]["status"] != "ready":
            _job_step(job_id, "semantic", "running")
            result = extract_generic_semantic_for_task(task_id) if market in PDF_MARKET_WIKI_INGEST_SCRIPTS else extract_semantic_for_task(task_id)
            _job_step(job_id, "semantic", "succeeded", result=result)
        else:
            _job_step(job_id, "semantic", "skipped", message="语义层已是最新")

        status = _workflow_status_payload(task_id)
        if status["obsidian"]["status"] != "ready":
            _job_step(job_id, "obsidian", "running")
            company_dir = status["wiki"].get("companyDir") or status["semantic"].get("companyDir") or _find_company_for_task_at_root(task_id, wiki_root)
            result = _generate_obsidian_for_company_at_root(company_dir, wiki_root) if market in PDF_MARKET_WIKI_INGEST_SCRIPTS else _generate_obsidian_for_company(company_dir)
            _job_step(job_id, "obsidian", "succeeded", result=result)
        else:
            _job_step(job_id, "obsidian", "skipped", message="Obsidian 图谱已是最新")

        status = _workflow_status_payload(task_id)
        if status["database"]["status"] != "ready":
            _job_step(job_id, "db-import", "running")
            result = import_task_to_database(task_id)
            _job_step(job_id, "db-import", "succeeded", result=result)
        else:
            _job_step(job_id, "db-import", "skipped", message="PostgreSQL 已是最新")

        _job_update(job_id, status="succeeded", result=_workflow_status_payload(task_id))
    except Exception as exc:
        _job_update(job_id, status="failed", error=str(exc))


@router.get("/task/{task_id}/preflight")
def workflow_preflight(task_id: str):
    return _workflow_preflight(task_id)


@router.post("/task/{task_id}/run-remaining")
def run_remaining_workflow(task_id: str):
    task_id = _safe_task_id(task_id)
    preflight = _workflow_preflight(task_id)
    if not preflight["ok"]:
        raise HTTPException(422, {"message": "预检未通过", "blocking": preflight["blocking"], "checks": preflight["checks"]})
    job_id = uuid.uuid4().hex
    with _job_lock:
        create_workflow_job(_workflow_jobs, job_id=job_id, task_id=task_id, now=_now_iso)
        _persist_workflow_jobs_locked()
    thread = threading.Thread(target=_run_remaining_pipeline, args=(job_id, task_id), daemon=True)
    thread.start()
    return _workflow_jobs[job_id]


@router.get("/job/{job_id}")
def workflow_job_status(job_id: str):
    with _job_lock:
        job = _workflow_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Workflow job not found")
        return job
