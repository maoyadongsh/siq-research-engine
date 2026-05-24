import json
import importlib.util
import os
import subprocess
import sys
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/workflow", tags=["workflow"])

WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", "/home/maoyd/wiki")).resolve()
PDF2MD_ROOT = Path(os.environ.get("PDF2MD_ROOT", "/home/maoyd/finsight/pdf2md_web")).resolve()
PDF_RESULTS_ROOT = Path(os.environ.get("PDF_RESULTS_ROOT", "/home/maoyd/finsight/pdf2md_web/results")).resolve()
WIKISET_ROOT = Path(os.environ.get("WIKISET_ROOT", "/home/maoyd/wiki/wikiset")).resolve()
WIKI_REBUILD_SCRIPT = Path(os.environ.get("WIKI_REBUILD_SCRIPT", str(WIKISET_ROOT / "rebuild_wiki_v2.py"))).resolve()
SEMANTIC_SCRIPT = Path(os.environ.get("SEMANTIC_SCRIPT", str(WIKISET_ROOT / "extract_company_semantics.py"))).resolve()
OBSIDIAN_SCRIPT = Path(os.environ.get("OBSIDIAN_SCRIPT", str(WIKISET_ROOT / "generate_obsidian_graph.py"))).resolve()
LLM_SEMANTIC_SCRIPT = Path(os.environ.get("LLM_SEMANTIC_SCRIPT", str(WIKISET_ROOT / "llm_semantic_enrichment.py"))).resolve()
DB_IMPORT_SCRIPT = Path(os.environ.get("DB_IMPORT_SCRIPT", "/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py")).resolve()
DB_CONFIG_PY = Path(os.environ.get("DB_CONFIG_PY", "/home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py")).resolve()
WIKI_NAMING_REPAIR_SCRIPT = Path(os.environ.get("WIKI_NAMING_REPAIR_SCRIPT", str(WIKISET_ROOT / "repair_wiki_naming.py"))).resolve()
WIKI_NAMING_VALIDATE_SCRIPT = Path(os.environ.get("WIKI_NAMING_VALIDATE_SCRIPT", str(WIKISET_ROOT / "validate_wiki_naming.py"))).resolve()

CORE_INPUT_ARTIFACTS = [
    "result.md",
    "result_complete.md",
    "document_full.json",
    "content_list_enhanced.json",
    "financial_data.json",
    "financial_checks.json",
    "quality_report.json",
    "table_index.json",
]
ARTIFACT_SCHEMA_EXPECTATIONS = {
    "document_full.json": 1,
    "content_list_enhanced.json": 8,
    "quality_report.json": 10,
    "financial_data.json": 13,
    "financial_checks.json": 12,
}
FINANCIAL_RULE_VERSION = "financial_rules_v14"
ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"
LLM_SEMANTIC_ENABLED = os.environ.get("LLM_SEMANTIC_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
LLM_SEMANTIC_REQUIRED = os.environ.get("LLM_SEMANTIC_REQUIRED", "true").lower() not in {"0", "false", "no", "off"}
LLM_SEMANTIC_TIMEOUT = int(os.environ.get("LLM_SEMANTIC_TIMEOUT", "900"))

_job_lock = threading.Lock()
_workflow_jobs: dict[str, dict] = {}

_wiki_builder = None
_wiki_builder_mtime = None


def _safe_task_id(task_id: str) -> str:
    value = task_id.strip()
    if not value or any(ch in value for ch in "/\\.."):
        raise HTTPException(400, "Invalid task_id")
    return value


def _find_task_document_full(task_id: str) -> Path | None:
    task_id = _safe_task_id(task_id)
    candidates = [
        PDF_RESULTS_ROOT / task_id / "document_full.json",
        Path("/home/maoyd/finsight/pdf2md_web/results") / task_id / "document_full.json",
    ]
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
    candidates = [
        PDF_RESULTS_ROOT / task_id,
        Path("/home/maoyd/finsight/pdf2md_web/results") / task_id,
    ]
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


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    data = _read_json(path, None)
    if isinstance(data, dict):
        meta = {}
        if data.get("schema_version") is not None:
            meta["schemaVersion"] = data.get("schema_version")
        if data.get("rule_version") is not None:
            meta["ruleVersion"] = data.get("rule_version")
        return meta
    if isinstance(data, list):
        return {"itemCount": len(data)}
    return {}


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
    digest_payload = {
        name: {
            "sha256": info.get("sha256"),
            "sizeBytes": info.get("sizeBytes"),
            "schemaVersion": info.get("schemaVersion"),
            "ruleVersion": info.get("ruleVersion"),
        }
        for name, info in artifacts.items()
    }
    bundle_sha = _sha256_text(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True))
    warnings = []
    if schema_mismatches:
        warnings.append("schema_mismatch")
    if rule_mismatches:
        warnings.append("financial_rule_mismatch")
    status = "missing" if missing else ("needs_review" if warnings else "ready")
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
            "ready": not missing,
            "ready_count": len(CORE_INPUT_ARTIFACTS) - len(missing),
            "total": len(CORE_INPUT_ARTIFACTS),
            "missing": missing,
            "bundle_sha256": bundle_sha,
        },
        "artifacts": artifacts,
        "checks": {
            "schema_mismatches": schema_mismatches,
            "rule_mismatches": rule_mismatches,
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
        "ready": not missing,
        "readyCount": len(CORE_INPUT_ARTIFACTS) - len(missing),
        "total": len(CORE_INPUT_ARTIFACTS),
        "missing": missing,
        "schemaMismatches": schema_mismatches,
        "ruleMismatches": rule_mismatches,
        "artifacts": artifacts,
        "warnings": warnings,
        "message": f"{len(CORE_INPUT_ARTIFACTS) - len(missing)}/{len(CORE_INPUT_ARTIFACTS)} 个核心文件已生成" if not missing else f"缺少 {len(missing)} 个核心文件",
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


def _find_report_for_task(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    companies_root = WIKI_ROOT / "companies"
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


def _find_company_for_task(task_id: str) -> str:
    companies_root = WIKI_ROOT / "companies"
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


def _wiki_import_status(task_id: str) -> dict:
    report = _find_report_for_task(task_id)
    company_dir = report.get("companyDir") or ""
    result_dir = _find_task_result_dir(task_id)
    artifact_bundle = _artifact_bundle_status(task_id)
    report_dir = report.get("reportDir")
    wiki_manifest = report_dir / ARTIFACT_MANIFEST_NAME if report_dir else None
    wiki_manifest_payload = _read_json(wiki_manifest, {}) if wiki_manifest else {}
    source_bundle_sha = artifact_bundle.get("bundleSha256")
    wiki_bundle_sha = (((wiki_manifest_payload or {}).get("core") or {}).get("bundle_sha256"))
    stale = bool(company_dir and source_bundle_sha and wiki_bundle_sha and source_bundle_sha != wiki_bundle_sha)
    status = "stale" if stale else ("ready" if company_dir else "missing")
    return {
        "status": status,
        "companyDir": company_dir,
        "reportId": report.get("reportId") or "",
        "reportDir": str(report_dir) if report_dir else "",
        "resultDir": str(result_dir) if result_dir else "",
        "bundleSha256": wiki_bundle_sha or "",
        "sourceBundleSha256": source_bundle_sha or "",
        "manifestPath": str(wiki_manifest) if wiki_manifest else "",
        "stale": stale,
        "storagePolicy": "lightweight_manifest_only",
        "message": "Wiki 需刷新" if stale else ("已导入 Wiki" if company_dir else ("可导入 Wiki" if result_dir else "未找到解析产物目录")),
    }


def _semantic_status(company_dir: str, task_id: str | None = None) -> dict:
    if not company_dir:
        return {"status": "unknown", "companyDir": "", "message": "未在 Wiki 中找到对应公司"}
    semantic_dir = WIKI_ROOT / "companies" / company_dir / "semantic"
    company_json = _read_json(WIKI_ROOT / "companies" / company_dir / "company.json", {}) or {}
    report_id = company_json.get("primary_report_id") or "2025-annual"
    report_dir = WIKI_ROOT / "companies" / company_dir / "reports" / report_id
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
            "company_json_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "company.json"),
            "report_md_sha256": _sha256_file(report_dir / "report.md"),
            "report_json_sha256": _sha256_file(report_dir / "report.json"),
            "document_full_sha256": _sha256_file(report_dir / "document_full.json"),
        }
        if (report_dir / ARTIFACT_MANIFEST_NAME).is_file():
            current_inputs["artifact_manifest_sha256"] = _sha256_file(report_dir / ARTIFACT_MANIFEST_NAME)
        stale = any((log.get("inputs") or {}).get(key) != value for key, value in current_inputs.items())
        if not stale and task_id:
            wiki_status = _wiki_import_status(task_id)
            stale = bool(wiki_status.get("stale"))
    llm = _llm_semantic_status(company_dir, report_id, stale)
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


def _llm_semantic_status(company_dir: str, report_id: str, rule_stale: bool = False) -> dict:
    if not LLM_SEMANTIC_ENABLED:
        return {"status": "disabled", "enabled": False, "message": "LLM 语义增强已关闭"}
    out_dir = WIKI_ROOT / "companies" / company_dir / "semantic" / "llm" / report_id
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
        "company_json_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "company.json"),
        "segments_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "semantic" / "segments.json"),
        "evidence_semantic_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "semantic" / "evidence_semantic.json"),
        "facts_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "semantic" / "facts.json"),
        "claims_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "semantic" / "claims.json"),
        "artifact_manifest_sha256": _sha256_file(WIKI_ROOT / "companies" / company_dir / "reports" / report_id / ARTIFACT_MANIFEST_NAME),
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
        "message": "LLM 语义增强需重新生成" if status == "stale" else ("LLM 语义增强未生成" if missing else "本地模型语义增强已生成"),
    }


def _obsidian_status(company_dir: str, semantic_status: dict | None = None) -> dict:
    if not company_dir:
        return {"status": "unknown", "companyDir": "", "message": "未在 Wiki 中找到对应公司"}
    company_root = WIKI_ROOT / "companies" / company_dir
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


def _generate_obsidian_for_company(company_dir: str) -> dict:
    if not company_dir:
        raise HTTPException(404, "Task is not linked to a Wiki company")
    if not OBSIDIAN_SCRIPT.is_file():
        raise HTTPException(500, f"Obsidian graph script not found: {OBSIDIAN_SCRIPT}")
    result = _run_command([
        sys.executable,
        str(OBSIDIAN_SCRIPT),
        "--wiki-root",
        str(WIKI_ROOT),
        "--company",
        company_dir,
    ])
    if result["returnCode"] != 0:
        raise HTTPException(500, {"stage": "obsidian", **result})
    return result


def _merge_by_report_id(items: list[dict], new_item: dict) -> list[dict]:
    report_id = new_item.get("report_id")
    merged = [item for item in items if (item or {}).get("report_id") != report_id]
    merged.append(new_item)
    return sorted(merged, key=lambda item: (int(item.get("report_year") or 0), str(item.get("report_id") or "")), reverse=True)


def _filter_report(items: list[dict], report_id: str) -> list[dict]:
    return [item for item in items if (item or {}).get("report_id") != report_id]


def _load_pg_config() -> dict | None:
    if not DB_CONFIG_PY.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("finsight_pdf2md_pg_config", DB_CONFIG_PY)
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
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "ai_platform"),
        "user": os.environ.get("PGUSER", "dgx"),
        "password": os.environ.get("PGPASSWORD", ""),
    }


def _update_catalogs(identity: dict, report_entry: dict, report_json: dict, row: dict) -> None:
    meta_dir = WIKI_ROOT / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    company_path = f"companies/{identity['company_id']}"

    company_catalog_path = meta_dir / "company_catalog.json"
    company_catalog = _read_json(company_catalog_path, {"schema_version": 1, "companies": []})
    companies = company_catalog.get("companies") or []
    company_item = {
        **identity,
        "company_path": company_path,
        "primary_report_id": report_entry["report_id"],
        "report_count": 1,
        "status": report_json.get("status") or "ready",
        "has_v641_metrics": False,
    }
    existing = [item for item in companies if (item or {}).get("company_id") != identity["company_id"]]
    old = next((item for item in companies if (item or {}).get("company_id") == identity["company_id"]), {})
    company_item["report_count"] = max(int(old.get("report_count") or 0), 1)
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


def _import_task_to_wiki(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    result_dir = _find_task_result_dir(task_id)
    if not result_dir:
        raise HTTPException(404, "解析产物目录不存在，默认读取 /home/maoyd/finsight/pdf2md_web/results/<task_id>")
    artifact_bundle = _artifact_bundle_status(task_id, write_manifest=True)
    if artifact_bundle["missing"]:
        raise HTTPException(422, {
            "message": "解析产物包不完整，不能安全导入 Wiki",
            "missing": artifact_bundle["missing"],
        })

    builder = _load_wiki_builder()
    tasks = builder.load_tasks(PDF2MD_ROOT / "tasks.db")
    row = builder.inspect_result_dir(result_dir, tasks)
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
    images = builder.copy_report_assets(row, report_dir)
    evidence = builder.build_v641_evidence(row, v641) if v641_company else builder.build_fallback_evidence(row)
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
    }
    company_reports = _merge_by_report_id(existing_company.get("reports") or [], report_entry)

    builder.write_json(company_dir / "metrics" / "three_statements.json", {
        "schema_version": 1,
        "source": "derived/three_statements_latest.json" if v641_company else "financial_data.json",
        "unit": "亿元",
        "data": v641_company or {},
        "generated_at": builder.now_iso(),
    })
    builder.write_json(company_dir / "metrics" / "key_metrics.json", {
        "schema_version": 1,
        "source": "financial_data.json",
        "data": row["financial_data"].get("key_metrics") or [],
        "generated_at": builder.now_iso(),
    })
    builder.write_json(company_dir / "metrics" / "validation.json", {
        "schema_version": 1,
        "financial_checks": row["financial_checks"],
        "wiki_v641_available": bool(v641_company),
        "generated_at": builder.now_iso(),
    })

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

    pdf_refs = []
    for item in evidence:
        if not item.get("pdf_page_number") and not item.get("table_index"):
            continue
        ref = {
            "company_id": identity["company_id"],
            "report_id": row["report_id"],
            "task_id": row["task_id"],
            "pdf_page_number": item.get("pdf_page_number"),
            "table_index": item.get("table_index"),
            "md_line": item.get("md_line"),
        }
        ref.update(builder.evidence_urls(row["task_id"], item.get("pdf_page_number"), item.get("table_index")))
        pdf_refs.append(ref)
    old_refs_payload = _read_json(company_dir / "evidence" / "pdf_refs.json", {"refs": []})
    merged_refs = _filter_report(old_refs_payload.get("refs") or [], row["report_id"]) + pdf_refs
    builder.write_json(company_dir / "evidence" / "pdf_refs.json", {
        "schema_version": 1,
        "company_id": identity["company_id"],
        "refs": merged_refs,
        "generated_at": builder.now_iso(),
    })

    company_json = {
        "schema_version": 1,
        **identity,
        "primary_report_id": company_reports[0]["report_id"],
        "reports": company_reports,
        "metrics": {
            "three_statements": "metrics/three_statements.json",
            "key_metrics": "metrics/key_metrics.json",
            "validation": "metrics/validation.json",
        },
        "evidence": {
            "evidence_index": "evidence/evidence_index.json",
            "pdf_refs": "evidence/pdf_refs.json",
            "image_manifest": "evidence/image_manifest.json",
        },
        "generated_at": builder.now_iso(),
    }
    builder.write_json(company_json_path, company_json)
    (company_dir / "company.md").write_text(builder.build_company_md(identity, company_reports, v641_company), encoding="utf-8")
    analysis_readme = company_dir / "analysis" / "README.md"
    if not analysis_readme.exists():
        analysis_readme.write_text(builder.build_analysis_readme(identity), encoding="utf-8")

    _update_catalogs(identity, report_entry, report_json, row)
    naming_check = _repair_and_validate_wiki_naming()
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
        "naming": naming_check,
        "wiki": _wiki_import_status(task_id),
    }


def _db_status(task_id: str) -> dict:
    try:
        import psycopg
    except Exception as exc:
        return {"status": "unknown", "message": f"psycopg unavailable: {exc}"}

    config = _db_connect_config()
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
                      (SELECT raw_json_hash FROM pdf2md.documents WHERE task_id = %s) AS raw_json_hash,
                      (SELECT updated_at FROM pdf2md.documents WHERE task_id = %s) AS updated_at
                    """,
                    (task_id, task_id, task_id, task_id, task_id),
                )
                row = cur.fetchone()
        imported = bool(row[0]) if row else False
        imported_sha = row[3] if row else None
        stale = bool(imported and current_sha and imported_sha and imported_sha != current_sha)
        status = "stale" if stale else ("ready" if imported else "missing")
        return {
            "status": status,
            "imported": imported,
            "statementItems": int(row[1] or 0) if row else 0,
            "tables": int(row[2] or 0) if row else 0,
            "rawJsonHash": imported_sha or "",
            "sourceDocumentFullSha256": current_sha or "",
            "updatedAt": row[4].isoformat() if row and row[4] else None,
            "stale": stale,
            "message": "PostgreSQL 需重新导入" if stale else "",
        }
    except Exception as exc:
        return {"status": "unknown", "message": str(exc)}


def _run_command(args: list[str], timeout: int = 180) -> dict:
    completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    return {
        "returnCode": completed.returncode,
        "stdout": completed.stdout[-6000:],
        "stderr": completed.stderr[-6000:],
    }


def _workflow_preflight(task_id: str) -> dict:
    task_id = _safe_task_id(task_id)
    artifacts = _artifact_bundle_status(task_id)
    wiki = _wiki_import_status(task_id)
    company_dir = wiki.get("companyDir") or _find_company_for_task(task_id)
    semantic = _semantic_status(company_dir, task_id)
    obsidian = _obsidian_status(company_dir, semantic)
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
            "label": "本地模型语义增强脚本",
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
    artifact_bundle = _artifact_bundle_status(task_id)
    document_full = _find_task_document_full(task_id)
    wiki = _wiki_import_status(task_id)
    company_dir = wiki.get("companyDir") or _find_company_for_task(task_id)
    semantic = _semantic_status(company_dir, task_id)
    obsidian = _obsidian_status(company_dir, semantic)
    database = _db_status(task_id)
    return {
        "taskId": task_id,
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


@router.get("/task/{task_id}/status")
def task_workflow_status(task_id: str):
    return _workflow_status_payload(task_id)


@router.post("/task/{task_id}/wiki-import")
def import_task_to_wiki(task_id: str):
    return _import_task_to_wiki(task_id)


@router.post("/task/{task_id}/semantic")
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
            ], timeout=LLM_SEMANTIC_TIMEOUT)
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


@router.post("/task/{task_id}/db-import")
def import_task_to_database(task_id: str):
    task_id = _safe_task_id(task_id)
    document_full = _find_task_document_full(task_id)
    if not document_full:
        raise HTTPException(404, "document_full.json not found for task")
    if not DB_IMPORT_SCRIPT.is_file():
        raise HTTPException(500, f"DB import script not found: {DB_IMPORT_SCRIPT}")
    args = [sys.executable, str(DB_IMPORT_SCRIPT), str(document_full)]
    if DB_CONFIG_PY.is_file():
        args += ["--config-py", str(DB_CONFIG_PY)]
    result = _run_command(args, timeout=300)
    if result["returnCode"] != 0:
        raise HTTPException(500, result)
    return {"ok": True, "taskId": task_id, "documentFull": str(document_full), "result": result, "database": _db_status(task_id)}


def _job_update(job_id: str, **updates) -> None:
    with _job_lock:
        job = _workflow_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updatedAt"] = _now_iso()


def _job_step(job_id: str, step: str, status: str, **updates) -> None:
    with _job_lock:
        job = _workflow_jobs.get(job_id)
        if not job:
            return
        steps = job.setdefault("steps", [])
        current = next((item for item in steps if item.get("step") == step), None)
        if not current:
            current = {"step": step, "startedAt": _now_iso()}
            steps.append(current)
        current.update({"status": status, **updates})
        if status in {"succeeded", "failed", "skipped"}:
            current.setdefault("finishedAt", _now_iso())
        job["updatedAt"] = _now_iso()


def _run_remaining_pipeline(job_id: str, task_id: str) -> None:
    try:
        _job_update(job_id, status="running")
        status = _workflow_status_payload(task_id)
        if not status["artifactBundle"]["ready"]:
            _job_update(job_id, status="failed", error="解析产物包不完整")
            return

        if status["wiki"]["status"] != "ready":
            _job_step(job_id, "wiki-import", "running")
            result = _import_task_to_wiki(task_id)
            _job_step(job_id, "wiki-import", "succeeded", result=result)
        else:
            _job_step(job_id, "wiki-import", "skipped", message="Wiki 已是最新")

        status = _workflow_status_payload(task_id)
        if status["semantic"]["status"] != "ready":
            _job_step(job_id, "semantic", "running")
            result = extract_semantic_for_task(task_id)
            _job_step(job_id, "semantic", "succeeded", result=result)
        else:
            _job_step(job_id, "semantic", "skipped", message="语义层已是最新")

        status = _workflow_status_payload(task_id)
        if status["obsidian"]["status"] != "ready":
            _job_step(job_id, "obsidian", "running")
            company_dir = status["wiki"].get("companyDir") or status["semantic"].get("companyDir") or _find_company_for_task(task_id)
            result = _generate_obsidian_for_company(company_dir)
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
        _workflow_jobs[job_id] = {
            "jobId": job_id,
            "taskId": task_id,
            "status": "queued",
            "steps": [],
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
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
