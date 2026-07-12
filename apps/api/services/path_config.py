"""Centralized filesystem paths for SIQ runtime services.

Leaf-level SIQ_* environment variables take precedence. Legacy aliases remain
supported, while SIQ_RUNTIME_ROOT and SIQ_ARTIFACTS_ROOT can opt deployments
into the newer split layout without moving existing data/.
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


def _env_has_value(*names: str) -> bool:
    return any(bool(os.environ.get(name, "").strip()) for name in names)


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


def _runtime_default(service: str, legacy_default: str | Path) -> Path:
    if _env_has_value("SIQ_RUNTIME_ROOT"):
        return RUNTIME_ROOT / service
    return Path(legacy_default)


def _artifact_default(service: str, leaf: str, legacy_default: str | Path) -> Path:
    if _env_has_value("SIQ_ARTIFACTS_ROOT"):
        return ARTIFACTS_ROOT / service / leaf
    return Path(legacy_default)


def _unique_paths(*paths: str | Path) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        candidate = Path(path).expanduser().resolve()
        key = str(candidate)
        if key not in seen:
            ordered.append(candidate)
            seen.add(key)
    return tuple(ordered)


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
LEGACY_DATA_ROOT = (PROJECT_ROOT / "data").expanduser().resolve()
RUNTIME_ROOT = _env_path(
    "SIQ_RUNTIME_ROOT",
    default=PROJECT_ROOT / "runtime",
)
ARTIFACTS_ROOT = _env_path(
    "SIQ_ARTIFACTS_ROOT",
    default=PROJECT_ROOT / "artifacts",
)
BACKEND_DATA_ROOT = _env_path(
    "SIQ_BACKEND_DATA_ROOT",
    "SIQ_BACKEND_DATA_ROOT",
    default=_runtime_default("api", DATA_ROOT / "backend"),
)
PDF2MD_DATA_ROOT = _env_path(
    "SIQ_PDF2MD_DATA_DIR",
    "PDF2MD_DATA_DIR",
    "SIQ_PDF2MD_DATA_DIR",
    default=_runtime_default("pdf-parser", DATA_ROOT / "pdf-parser"),
)
PDF_RESULTS_ROOT = _env_path(
    "SIQ_PDF_RESULTS_ROOT",
    "PDF_RESULTS_ROOT",
    "SIQ_PDF_RESULTS_ROOT",
    default=_artifact_default("pdf-parser", "results", PDF2MD_DATA_ROOT / "results"),
)
PDF_OUTPUT_ROOT = _env_path(
    "SIQ_PDF_OUTPUT_ROOT",
    "PDF_OUTPUT_ROOT",
    "SIQ_PDF_OUTPUT_ROOT",
    default=_artifact_default("pdf-parser", "output", PDF2MD_DATA_ROOT / "output"),
)
DOCUMENT_PARSER_DATA_ROOT = _env_path(
    "SIQ_DOCUMENT_PARSE_DATA_DIR",
    "SIQ_DOCUMENT_PARSER_DATA_DIR",
    "DOCUMENT_PARSER_DATA_DIR",
    default=_runtime_default("document-parser", DATA_ROOT / "document-parser"),
)
DOCUMENT_PARSER_RESULTS_ROOT = _env_path(
    "SIQ_DOCUMENT_PARSE_RESULTS_ROOT",
    "SIQ_DOCUMENT_PARSER_RESULTS_ROOT",
    "DOCUMENT_PARSER_RESULTS_ROOT",
    default=_artifact_default("document-parser", "results", DOCUMENT_PARSER_DATA_ROOT / "results"),
)

if _env_has_value("SIQ_DATA_ROOT"):
    DEFAULT_WIKI_ROOT = DATA_ROOT / "wiki"
else:
    DEFAULT_WIKI_ROOT = _first_existing_path(
        LEGACY_DATA_ROOT / "wiki",
        marker="companies",
        default=LEGACY_DATA_ROOT / "wiki",
    )
DEFAULT_REPORT_FINDER_ROOT = _first_existing_path(
    PROJECT_ROOT / "services" / "market-report-finder",
    marker="pyproject.toml",
    default=PROJECT_ROOT / "services" / "market-report-finder",
)
if _env_has_value("SIQ_RUNTIME_ROOT"):
    DEFAULT_HERMES_HOME = RUNTIME_ROOT / "hermes" / "home"
elif _env_has_value("SIQ_DATA_ROOT"):
    DEFAULT_HERMES_HOME = DATA_ROOT / "hermes" / "home"
else:
    DEFAULT_HERMES_HOME = _first_existing_path(
        LEGACY_DATA_ROOT / "hermes" / "home",
        marker=Path("profiles") / "siq_assistant" / "config.yaml",
        default=LEGACY_DATA_ROOT / "hermes" / "home",
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
    default=REPO_ROOT / "scripts" / "wiki" / "wikiset",
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
    default=_artifact_default("market-report-finder", "downloads", DATA_ROOT / "market-report-finder" / "downloads"),
)

HERMES_HOME = _env_path(
    "SIQ_HERMES_HOME",
    "HERMES_HOME",
    "SIQ_HERMES_HOME",
    default=DEFAULT_HERMES_HOME,
)
HERMES_HOST_HOME = _env_path(
    "SIQ_HERMES_HOST_HOME",
    default=HERMES_HOME,
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
HERMES_HOST_SHARED_SCRIPTS_ROOT = HERMES_HOST_HOME / "profiles" / "shared" / "scripts"


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

PDF_RESULT_ROOT_CANDIDATES = _unique_paths(
    PDF_RESULTS_ROOT,
    PDF2MD_DATA_ROOT / "results",
    DATA_ROOT / "pdf-parser" / "results",
    LEGACY_DATA_ROOT / "pdf-parser" / "results",
    PDF2MD_ROOT / "results",
)
PDF_OUTPUT_ROOT_CANDIDATES = _unique_paths(
    PDF_OUTPUT_ROOT,
    PDF2MD_DATA_ROOT / "output",
    DATA_ROOT / "pdf-parser" / "output",
    LEGACY_DATA_ROOT / "pdf-parser" / "output",
    PDF2MD_ROOT / "output",
)
DOCUMENT_PARSER_RESULT_ROOT_CANDIDATES = _unique_paths(
    DOCUMENT_PARSER_RESULTS_ROOT,
    DOCUMENT_PARSER_DATA_ROOT / "results",
    DATA_ROOT / "document-parser" / "results",
    LEGACY_DATA_ROOT / "document-parser" / "results",
)
REPORT_DOWNLOAD_ROOT_CANDIDATES = _unique_paths(
    REPORT_DOWNLOADS_ROOT,
    DATA_ROOT / "market-report-finder" / "downloads",
    LEGACY_DATA_ROOT / "market-report-finder" / "downloads",
)
WIKI_ROOT_CANDIDATES = _unique_paths(
    WIKI_ROOT,
    DATA_ROOT / "wiki",
    LEGACY_DATA_ROOT / "wiki",
)
