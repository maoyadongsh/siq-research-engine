"""Centralized settings for market report routing and workers."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from services.path_config import REPO_ROOT


def _env_str(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


def _env_float(*names: str, default: float) -> float:
    raw = _env_str(*names, default=str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _env_path(*names: str, default: Path) -> Path:
    return Path(_env_str(*names, default=str(default))).expanduser().resolve()


def _load_market_ingestion_contract() -> ModuleType:
    source = REPO_ROOT / "db" / "imports" / "market_ingestion_contract.py"
    spec = importlib.util.spec_from_file_location("siq_market_ingestion_contract_for_api_settings", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load market ingestion contract: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _default_vector_collection(market: str) -> str:
    contract = _load_market_ingestion_contract()
    target = contract.target_for_market(market)
    collection = str(target.default_collection or "").strip()
    if not collection:
        raise RuntimeError(f"Missing default vector collection in market ingestion contract for {market}")
    return collection


REPORT_FINDER_BASE = _env_str(
    "SIQ_REPORT_FINDER_BASE",
    "REPORT_FINDER_BASE",
    default="http://127.0.0.1:18000",
).rstrip("/")

MARKET_RULES_BASE = _env_str(
    "SIQ_MARKET_REPORT_RULES_BASE",
    "MARKET_REPORT_RULES_BASE",
    default="http://127.0.0.1:18020",
).rstrip("/")

MARKET_REPORT_PROXY_TIMEOUT = _env_float("SIQ_MARKET_REPORT_PROXY_TIMEOUT", default=120.0)
MARKET_REPORT_ASSIST_TIMEOUT = _env_float("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", default=45.0)

US_SEC_CASE_SET_PATH = _env_path(
    "SIQ_US_CASE_SET_PATH",
    "SIQ_US_SEC_CASE_SET_PATH",
    default=REPO_ROOT / "data" / "wiki" / "us" / "_meta" / "case_set_50_us_10k.json",
)
US_SEC_INGEST_REPORT_PATH = _env_path(
    "SIQ_US_INGEST_REPORT_PATH",
    "SIQ_US_SEC_INGEST_REPORT_PATH",
    default=REPO_ROOT / "data" / "wiki" / "us" / "_meta" / "case_set_50_us_10k_ingest_report.json",
)
US_SEC_INGEST_SCRIPT = _env_path(
    "SIQ_US_SEC_INGEST_SCRIPT",
    default=REPO_ROOT / "scripts" / "us-sec" / "ingest_sec_case_set.py",
)
US_SEC_WIKI_ROOT = _env_path(
    "SIQ_US_WIKI_ROOT",
    "SIQ_US_SEC_WIKI_ROOT",
    default=REPO_ROOT / "data" / "wiki" / "us",
)
US_SEC_PACKAGE_BUILD_SCRIPT = _env_path(
    "SIQ_US_SEC_PACKAGE_BUILD_SCRIPT",
    default=REPO_ROOT / "scripts" / "us-sec" / "build_sec_evidence_package.py",
)
MARKET_VECTOR_INGEST_SCRIPT = _env_path(
    "SIQ_MARKET_VECTOR_INGEST_SCRIPT",
    default=REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_market_evidence_chunks.py",
)
MARKET_INGESTION_EVAL_SCRIPT = _env_path(
    "SIQ_MARKET_INGESTION_EVAL_SCRIPT",
    default=REPO_ROOT / "scripts" / "maintenance" / "run_market_ingestion_eval.py",
)
MARKET_INGESTION_EVAL_REPORT_PATH = _env_path(
    "SIQ_MARKET_INGESTION_EVAL_REPORT_PATH",
    default=REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.json",
)
MARKET_INGESTION_EVAL_MARKDOWN_PATH = _env_path(
    "SIQ_MARKET_INGESTION_EVAL_MARKDOWN_PATH",
    default=REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.md",
)

MARKET_WIKI_ROOTS = {
    "US": _env_path("SIQ_US_WIKI_ROOT", "SIQ_US_SEC_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "us"),
    "HK": _env_path("SIQ_HK_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "hk"),
    "JP": _env_path("SIQ_JP_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "jp"),
    "KR": _env_path("SIQ_KR_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "kr"),
    "EU": _env_path("SIQ_EU_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "eu"),
}

MARKET_BUILD_SCRIPTS = {
    "US": US_SEC_PACKAGE_BUILD_SCRIPT,
    "HK": _env_path("SIQ_HK_PACKAGE_BUILD_SCRIPT", default=REPO_ROOT / "scripts" / "hk" / "build_hk_evidence_package.py"),
    "JP": _env_path("SIQ_JP_PACKAGE_BUILD_SCRIPT", default=REPO_ROOT / "scripts" / "jp" / "build_jp_evidence_package.py"),
    "KR": _env_path("SIQ_KR_PACKAGE_BUILD_SCRIPT", default=REPO_ROOT / "scripts" / "kr" / "build_kr_evidence_package.py"),
    "EU": _env_path("SIQ_EU_PACKAGE_BUILD_SCRIPT", default=REPO_ROOT / "scripts" / "eu" / "build_eu_pdf_evidence_package.py"),
}

EU_ESEF_PACKAGE_BUILD_SCRIPT = _env_path(
    "SIQ_EU_ESEF_PACKAGE_BUILD_SCRIPT",
    default=REPO_ROOT / "scripts" / "eu" / "build_eu_esef_evidence_package.py",
)

MARKET_IMPORT_SCRIPTS = {
    "US": _env_path("SIQ_US_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_sec_filing_to_postgres.py"),
    "HK": _env_path("SIQ_HK_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_hk_evidence_package_to_postgres.py"),
    "JP": _env_path("SIQ_JP_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_jp_evidence_package_to_postgres.py"),
    "KR": _env_path("SIQ_KR_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_kr_evidence_package_to_postgres.py"),
    "EU": _env_path("SIQ_EU_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_eu_evidence_package_to_postgres.py"),
}

MARKET_DOCUMENT_FULL_ROOTS = {
    "US": _env_path("SIQ_US_DOCUMENT_FULL_ROOT", default=REPO_ROOT / "data" / "parser-results" / "us-sec"),
    "HK": _env_path("SIQ_HK_DOCUMENT_FULL_ROOT", default=REPO_ROOT / "data" / "pdf-parser" / "results"),
    "JP": _env_path("SIQ_JP_DOCUMENT_FULL_ROOT", default=REPO_ROOT / "data" / "pdf-parser" / "results"),
    "KR": _env_path("SIQ_KR_DOCUMENT_FULL_ROOT", default=REPO_ROOT / "data" / "pdf-parser" / "results"),
    "EU": _env_path("SIQ_EU_DOCUMENT_FULL_ROOT", default=REPO_ROOT / "data" / "pdf-parser" / "results"),
}
MARKET_DOCUMENT_FULL_ROOTS["US_SEC"] = MARKET_DOCUMENT_FULL_ROOTS["US"]

MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS = {
    "US": _env_path("SIQ_US_DOCUMENT_FULL_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_us_sec_document_full_to_postgres.py"),
    "HK": _env_path("SIQ_HK_DOCUMENT_FULL_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_hk_document_full_to_postgres.py"),
    "JP": _env_path("SIQ_JP_DOCUMENT_FULL_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_jp_document_full_to_postgres.py"),
    "KR": _env_path("SIQ_KR_DOCUMENT_FULL_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_kr_document_full_to_postgres.py"),
    "EU": _env_path("SIQ_EU_DOCUMENT_FULL_IMPORT_SCRIPT", default=REPO_ROOT / "db" / "imports" / "import_eu_document_full_to_postgres.py"),
}
MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["US_SEC"] = MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["US"]

MARKET_DATABASES = {
    "US": _env_str("SIQ_US_PGDATABASE", default="siq_us"),
    "HK": _env_str("SIQ_HK_PGDATABASE", default="siq_hk"),
    "JP": _env_str("SIQ_JP_PGDATABASE", default="siq_jp"),
    "KR": _env_str("SIQ_KR_PGDATABASE", default="siq_kr"),
    "EU": _env_str("SIQ_EU_PGDATABASE", default="siq_eu"),
}
MARKET_DATABASES["US_SEC"] = MARKET_DATABASES["US"]

MARKET_VECTOR_COLLECTIONS = {
    "US": _env_str("SIQ_US_VECTOR_COLLECTION", default=_default_vector_collection("US")),
    "HK": _env_str("SIQ_HK_VECTOR_COLLECTION", default=_default_vector_collection("HK")),
    "JP": _env_str("SIQ_JP_VECTOR_COLLECTION", default=_default_vector_collection("JP")),
    "KR": _env_str("SIQ_KR_VECTOR_COLLECTION", default=_default_vector_collection("KR")),
    "EU": _env_str("SIQ_EU_VECTOR_COLLECTION", default=_default_vector_collection("EU")),
}
MARKET_VECTOR_COLLECTIONS["US_SEC"] = MARKET_VECTOR_COLLECTIONS["US"]
