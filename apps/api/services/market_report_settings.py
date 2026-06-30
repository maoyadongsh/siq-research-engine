"""Centralized settings for market report routing and workers."""

from __future__ import annotations

import os
from pathlib import Path

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
    "SIQ_US_SEC_CASE_SET_PATH",
    default=REPO_ROOT / "data" / "wiki" / "us_sec" / "case_set_50_us_10k.json",
)
US_SEC_INGEST_REPORT_PATH = _env_path(
    "SIQ_US_SEC_INGEST_REPORT_PATH",
    default=REPO_ROOT / "data" / "wiki" / "us_sec" / "case_set_50_us_10k_ingest_report.json",
)
US_SEC_INGEST_SCRIPT = _env_path(
    "SIQ_US_SEC_INGEST_SCRIPT",
    default=REPO_ROOT / "scripts" / "us-sec" / "ingest_sec_case_set.py",
)
US_SEC_WIKI_ROOT = _env_path(
    "SIQ_US_SEC_WIKI_ROOT",
    default=REPO_ROOT / "data" / "wiki" / "us_sec",
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
    "US": _env_path("SIQ_US_SEC_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "us_sec"),
    "HK": _env_path("SIQ_HK_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "hk_reports"),
    "JP": _env_path("SIQ_JP_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "jp_reports"),
    "KR": _env_path("SIQ_KR_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "kr_reports"),
    "EU": _env_path("SIQ_EU_WIKI_ROOT", default=REPO_ROOT / "data" / "wiki" / "eu_reports"),
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
