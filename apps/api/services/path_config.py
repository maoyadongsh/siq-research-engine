"""Centralized filesystem paths for SIQ runtime services.

New SIQ_* environment variables take precedence. Existing WIKI_ROOT,
PDF2MD_ROOT, REPORT_DOWNLOADS_ROOT, and SIQ_* variables remain supported so
current local deployments can opt into compatibility paths during migration.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(*names: str, default: str | Path) -> Path:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value).expanduser().resolve()
    return Path(default).expanduser().resolve()


def _first_existing_path(*paths: str | Path, marker: str | Path | None = None, default: str | Path) -> Path:
    for path in paths:
        candidate = Path(path).expanduser()
        probe = candidate / marker if marker else candidate
        if probe.exists():
            return candidate
    return Path(default).expanduser()


def _first_existing_env_path(*env_names: str, candidates: str | Path | list[str | Path] | tuple[str | Path, ...], default: str | Path) -> Path:
    for name in env_names:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value).expanduser().resolve()

    candidate_paths = candidates if isinstance(candidates, (list, tuple)) else (candidates,)
    for path in candidate_paths:
        candidate = Path(path).expanduser()
        if candidate.exists():
            return candidate.resolve()
    return Path(default).expanduser().resolve()


def _find_repo_root(start: Path) -> Path:
    for path in (start, *start.parents):
        if (path / ".git").exists():
            return path
    for path in (start, *start.parents):
        if (path / "apps" / "api").is_dir():
            return path
    return Path(__file__).resolve().parents[3]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())

PROJECT_ROOT = _env_path(
    "SIQ_PROJECT_ROOT",
    "SIQ_PROJECT_ROOT",
    default=REPO_ROOT,
)
BACKEND_ROOT = _env_path(
    "SIQ_BACKEND_ROOT",
    "SIQ_BACKEND_ROOT",
    default=PROJECT_ROOT / "apps" / "api",
)
FRONTEND_ROOT = _env_path(
    "SIQ_FRONTEND_ROOT",
    "SIQ_FRONTEND_ROOT",
    default=PROJECT_ROOT / "apps" / "web",
)
PDF2MD_ROOT = _env_path(
    "SIQ_PDF2MD_ROOT",
    "PDF2MD_ROOT",
    "SIQ_PDF2MD_ROOT",
    default=PROJECT_ROOT / "apps" / "pdf-parser",
)
DATA_ROOT = _env_path(
    "SIQ_DATA_ROOT",
    "SIQ_DATA_ROOT",
    default=PROJECT_ROOT / "data",
)
BACKEND_DATA_ROOT = _env_path(
    "SIQ_BACKEND_DATA_ROOT",
    "SIQ_BACKEND_DATA_ROOT",
    default=DATA_ROOT / "backend",
)
PDF2MD_DATA_ROOT = _env_path(
    "SIQ_PDF2MD_DATA_DIR",
    "PDF2MD_DATA_DIR",
    "SIQ_PDF2MD_DATA_DIR",
    default=DATA_ROOT / "pdf-parser",
)
PDF_RESULTS_ROOT = _env_path(
    "SIQ_PDF_RESULTS_ROOT",
    "PDF_RESULTS_ROOT",
    "SIQ_PDF_RESULTS_ROOT",
    default=PDF2MD_DATA_ROOT / "results",
)
PDF_OUTPUT_ROOT = _env_path(
    "SIQ_PDF_OUTPUT_ROOT",
    "PDF_OUTPUT_ROOT",
    "SIQ_PDF_OUTPUT_ROOT",
    default=PDF2MD_DATA_ROOT / "output",
)
DOCUMENT_PARSER_DATA_ROOT = _env_path(
    "SIQ_DOCUMENT_PARSE_DATA_DIR",
    "SIQ_DOCUMENT_PARSER_DATA_DIR",
    "DOCUMENT_PARSER_DATA_DIR",
    default=DATA_ROOT / "document-parser",
)
DOCUMENT_PARSER_RESULTS_ROOT = _env_path(
    "SIQ_DOCUMENT_PARSE_RESULTS_ROOT",
    "SIQ_DOCUMENT_PARSER_RESULTS_ROOT",
    "DOCUMENT_PARSER_RESULTS_ROOT",
    default=DOCUMENT_PARSER_DATA_ROOT / "results",
)

DEFAULT_WIKI_ROOT = _first_existing_path(
    PROJECT_ROOT / "data" / "wiki",
    marker="companies",
    default=PROJECT_ROOT / "data" / "wiki",
)
DEFAULT_REPORT_FINDER_ROOT = _first_existing_path(
    PROJECT_ROOT / "services" / "market-report-finder",
    marker="pyproject.toml",
    default=PROJECT_ROOT / "services" / "market-report-finder",
)
DEFAULT_HERMES_HOME = _first_existing_path(
    PROJECT_ROOT / "data" / "hermes" / "home",
    marker=Path("profiles") / "siq_assistant" / "config.yaml",
    default=PROJECT_ROOT / "data" / "hermes" / "home",
)
DEFAULT_DB_ROOT = PROJECT_ROOT / "db"


def _default_db_program_root(db_root: Path) -> Path:
    if (db_root / "imports" / "import_document_full_to_postgres.py").exists():
        return db_root / "imports"
    return db_root / "PROGRAM"

WIKI_ROOT = _env_path(
    "SIQ_WIKI_ROOT",
    "WIKI_ROOT",
    "SIQ_WIKI_ROOT",
    default=DEFAULT_WIKI_ROOT,
)
DOCUMENT_WIKI_ROOT = _env_path(
    "SIQ_DOCUMENT_WIKI_ROOT",
    "DOCUMENT_WIKI_ROOT",
    "SIQ_DOCUMENT_WIKI_ROOT",
    default=WIKI_ROOT / "documents",
)
ASSISTANT_WIKI_ROOT = _env_path(
    "SIQ_ASSISTANT_WIKI_ROOT",
    "SIQ_ASSISTANT_WIKI_ROOT",
    default=WIKI_ROOT,
)
WIKISET_ROOT = _env_path(
    "SIQ_WIKISET_ROOT",
    "WIKISET_ROOT",
    "SIQ_WIKISET_ROOT",
    default=WIKI_ROOT / "wikiset",
)

REPORT_FINDER_ROOT = _env_path(
    "SIQ_REPORT_FINDER_ROOT",
    "REPORT_FINDER_ROOT",
    "SIQ_REPORT_FINDER_ROOT",
    default=DEFAULT_REPORT_FINDER_ROOT,
)
REPORT_DOWNLOADS_ROOT = _env_path(
    "SIQ_REPORT_DOWNLOADS_ROOT",
    "REPORT_DOWNLOADS_ROOT",
    "SIQ_REPORT_DOWNLOADS_ROOT",
    default=DATA_ROOT / "market-report-finder" / "downloads",
)

HERMES_HOME = _env_path(
    "SIQ_HERMES_HOME",
    "HERMES_HOME",
    "SIQ_HERMES_HOME",
    default=DEFAULT_HERMES_HOME,
)
HERMES_PROFILES_ROOT = _env_path(
    "SIQ_HERMES_PROFILES_ROOT",
    "HERMES_PROFILES_ROOT",
    "SIQ_HERMES_PROFILES_ROOT",
    default=HERMES_HOME / "profiles",
)
HERMES_SHARED_SCRIPTS_ROOT = _env_path(
    "SIQ_HERMES_SHARED_SCRIPTS_ROOT",
    "HERMES_SHARED_SCRIPTS_ROOT",
    "SIQ_HERMES_SHARED_SCRIPTS_ROOT",
    default=PROJECT_ROOT / "agents" / "hermes" / "profiles" / "shared" / "scripts",
)


def _hermes_profile_root(profile: str, env_prefix: str) -> Path:
    return _first_existing_env_path(
        f"SIQ_HERMES_{env_prefix}_PROFILE_ROOT",
        f"HERMES_{env_prefix}_PROFILE_ROOT",
        candidates=(
            HERMES_PROFILES_ROOT / profile,
            PROJECT_ROOT / "agents" / "hermes" / "profiles" / profile,
        ),
        default=HERMES_PROFILES_ROOT / profile,
    )


HERMES_PROFILE_ROOTS = {
    "siq_assistant": _hermes_profile_root("siq_assistant", "ASSISTANT"),
    "siq_analysis": _hermes_profile_root("siq_analysis", "ANALYSIS"),
    "siq_factchecker": _hermes_profile_root("siq_factchecker", "FACTCHECKER"),
    "siq_tracking": _hermes_profile_root("siq_tracking", "TRACKING"),
    "siq_legal": _hermes_profile_root("siq_legal", "LEGAL"),
    "siq_ic_master_coordinator": _hermes_profile_root("siq_ic_master_coordinator", "IC_MASTER"),
    "siq_ic_chairman": _hermes_profile_root("siq_ic_chairman", "IC_CHAIRMAN"),
    "siq_ic_strategist": _hermes_profile_root("siq_ic_strategist", "IC_STRATEGIST"),
    "siq_ic_sector_expert": _hermes_profile_root("siq_ic_sector_expert", "IC_SECTOR"),
    "siq_ic_finance_auditor": _hermes_profile_root("siq_ic_finance_auditor", "IC_FINANCE"),
    "siq_ic_legal_scanner": _hermes_profile_root("siq_ic_legal_scanner", "IC_LEGAL"),
    "siq_ic_risk_controller": _hermes_profile_root("siq_ic_risk_controller", "IC_RISK"),
}
FINANCIAL_CALCULATOR_SCRIPT = _env_path(
    "SIQ_FINANCIAL_CALCULATOR_SCRIPT",
    "FINANCIAL_CALCULATOR_SCRIPT",
    "SIQ_FINANCIAL_CALCULATOR_SCRIPT",
    default=HERMES_SHARED_SCRIPTS_ROOT / "financial_calculator.py",
)
FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT = _env_path(
    "SIQ_FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT",
    "FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT",
    "SIQ_FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT",
    default=HERMES_SHARED_SCRIPTS_ROOT / "financial_reconciliation_validator.py",
)

DB_ROOT = _env_path(
    "SIQ_DB_ROOT",
    "DB_ROOT",
    "SIQ_DB_ROOT",
    default=DEFAULT_DB_ROOT,
)
DB_PROGRAM_ROOT = _env_path(
    "SIQ_DB_PROGRAM_ROOT",
    "DB_PROGRAM_ROOT",
    "SIQ_DB_PROGRAM_ROOT",
    default=_default_db_program_root(DB_ROOT),
)
DB_IMPORT_SCRIPT = _env_path(
    "SIQ_DB_IMPORT_SCRIPT",
    "DB_IMPORT_SCRIPT",
    "SIQ_DB_IMPORT_SCRIPT",
    default=DB_PROGRAM_ROOT / "import_document_full_to_postgres.py",
)
DOCUMENT_DB_IMPORT_SCRIPT = _env_path(
    "SIQ_DOCUMENT_DB_IMPORT_SCRIPT",
    "DOCUMENT_DB_IMPORT_SCRIPT",
    "SIQ_DOCUMENT_DB_IMPORT_SCRIPT",
    default=DB_ROOT / "imports" / "import_document_parse_package_to_postgres.py",
)
DB_CONFIG_PY = _env_path(
    "SIQ_DB_CONFIG_PY",
    "DB_CONFIG_PY",
    "SIQ_DB_CONFIG_PY",
    default=DB_PROGRAM_ROOT / "postgresql_connect.py",
)

MINERU_VENV = _env_path(
    "SIQ_MINERU_VENV",
    "MINERU_VENV",
    "SIQ_MINERU_VENV",
    default=_first_existing_path(
        PROJECT_ROOT / "runtimes" / "mineru-native",
        "/home/maoyd/.venvs/mineru_native",
        marker=Path("bin") / "python",
        default=PROJECT_ROOT / "runtimes" / "mineru-native",
    ),
)

LLM_COST_LOG_ROOT = _env_path(
    "SIQ_LLM_COST_LOG_ROOT",
    "SIQ_LLM_COST_LOG_ROOT",
    default=BACKEND_DATA_ROOT / "llm_costs",
)
WORKFLOW_JOB_STORE = _env_path(
    "SIQ_WORKFLOW_JOB_STORE",
    "SIQ_WORKFLOW_JOB_STORE",
    default=PDF2MD_DATA_ROOT / "workflow_jobs.json",
)
PDF_TASK_DB_PATH = _env_path(
    "SIQ_PDF_TASK_DB_PATH",
    "TASK_DB_PATH",
    "SIQ_PDF_TASK_DB_PATH",
    default=PDF2MD_DATA_ROOT / "db" / "tasks.db",
)

PDF_RESULT_ROOT_CANDIDATES = tuple(
    Path(path)
    for path in dict.fromkeys(
        str(path)
        for path in (
            PDF_RESULTS_ROOT,
            PDF2MD_ROOT / "results",
        )
    )
)
PDF_OUTPUT_ROOT_CANDIDATES = tuple(
    Path(path)
    for path in dict.fromkeys(
        str(path)
        for path in (
            PDF_OUTPUT_ROOT,
            PDF2MD_ROOT / "output",
        )
    )
)
