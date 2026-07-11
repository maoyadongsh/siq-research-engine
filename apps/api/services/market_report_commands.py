from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import market_document_identity


ESEF_SOURCE_SUFFIXES = {".zip", ".xhtml", ".html", ".htm", ".xml", ".xbrl"}
PARSER_RESULT_MARKETS = {"HK", "JP", "KR", "EU"}
DOWNLOAD_PATH_MARKETS = {"CN", "HK", "US", "EU", "JP", "KR"}
MARKET_ALIASES = market_document_identity.MARKET_ALIASES
US_SEC_UPLOAD_CONTENT_TYPE_SUFFIXES = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/plain": ".txt",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
}
FORCE_REASON_KEYS = ("force_reason", "reason", "override_reason")
FORCE_OPERATOR_KEYS = (
    "force_operator",
    "force_operator_id",
    "operator",
    "operator_id",
    "user",
    "user_id",
    "username",
    "requested_by",
)
FORCE_TICKET_KEYS = (
    "force_ticket",
    "ticket",
    "change_id",
    "change_request",
    "approval_id",
)
FORCE_EXPIRES_KEYS = ("force_expires_at", "expires_at", "expiry", "expires")
FORCE_ONE_SHOT_KEYS = (
    "force_one_shot",
    "one_shot",
    "one_time",
    "one_time_use",
    "one_shot_id",
)


def normalize_market_code(market: str | None) -> str:
    return market_document_identity.normalize_market_code(market)


class MarketPackageBuildPlanError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class MarketPackagePlanError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class MarketPackageBuildPlan:
    market: str
    source_path: Path
    script: Path
    output_root: Path
    metadata_path: Path | None
    parser_result_path: Path | None
    force: bool


@dataclass(frozen=True)
class MarketPackageImportPlan:
    market: str
    package_dir: Path
    script: Path


@dataclass(frozen=True)
class MarketDocumentFullImportPlan:
    market: str
    document_full_path: Path
    script: Path
    identity: market_document_identity.MarketDocumentFullIdentity
    selector: dict[str, str]


@dataclass(frozen=True)
class MarketVectorIngestPlan:
    market: str
    package_dir: Path
    script: Path
    dry_run: bool


@dataclass(frozen=True)
class MarketIngestionEvalPlan:
    script: Path
    output_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class UsSecRebuildPackagePlan:
    ticker: str
    package_dir: Path
    source_path: Path
    metadata_path: Path | None
    script: Path
    output_root: Path


@dataclass(frozen=True)
class MarketPackageQualityGateDecision:
    force_enabled: bool
    blocked: bool
    audit: dict[str, str | None] | None
    error_status_code: int | None = None
    error_detail: Any = None


def select_market_build_script(
    *,
    market: str,
    source_path: Path,
    market_build_scripts: dict[str, Path],
    eu_esef_package_build_script: Path,
) -> Path:
    if market == "EU" and source_path.suffix.lower() in ESEF_SOURCE_SUFFIXES:
        return eu_esef_package_build_script
    return market_build_scripts[market]


def download_relative_path_market(value: object) -> str:
    first = str(value or "").replace("\\", "/").split("/", 1)[0].strip().upper()
    return first if first in DOWNLOAD_PATH_MARKETS else ""


def market_build_requires_parser_result(
    *,
    market: str,
    source_path: Path,
    market_build_scripts: dict[str, Path],
    eu_esef_package_build_script: Path,
) -> bool:
    if market == "EU":
        return select_market_build_script(
            market=market,
            source_path=source_path,
            market_build_scripts=market_build_scripts,
            eu_esef_package_build_script=eu_esef_package_build_script,
        ) == market_build_scripts[market]
    if market in {"JP", "KR"} and source_path.suffix.lower() == ".pdf":
        return True
    return market == "HK"


def market_build_accepts_parser_result(
    *,
    market: str,
    script: Path,
    eu_esef_package_build_script: Path,
) -> bool:
    if market == "EU" and script == eu_esef_package_build_script:
        return False
    return market in PARSER_RESULT_MARKETS


def _resolve_repo_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def build_market_package_build_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    repo_root: Path,
    market_wiki_roots: Mapping[str, Path],
    market_build_scripts: Mapping[str, Path],
    eu_esef_package_build_script: Path,
    safe_download_path: Callable[[str], Path],
    adjacent_metadata_path: Callable[[Path], Path | None],
) -> MarketPackageBuildPlan:
    download_relative_path = payload.get("download_relative_path")
    source = payload.get("source_path") or payload.get("pdf_path")
    if download_relative_path:
        download_market = download_relative_path_market(download_relative_path)
        if download_market and download_market != market:
            raise MarketPackageBuildPlanError(400, f"download_relative_path belongs to {download_market}, not {market}")
        source_path = safe_download_path(str(download_relative_path))
    else:
        source_path = _resolve_repo_path(source, repo_root=repo_root) if source else Path()

    if not source and not download_relative_path:
        raise MarketPackageBuildPlanError(400, "source_path or download_relative_path is required")
    if not source_path.is_file():
        raise MarketPackageBuildPlanError(404, "source_path not found")

    script = select_market_build_script(
        market=market,
        source_path=source_path,
        market_build_scripts=dict(market_build_scripts),
        eu_esef_package_build_script=eu_esef_package_build_script,
    )
    if not script.is_file():
        raise MarketPackageBuildPlanError(404, f"Missing package build script: {script}")

    metadata = payload.get("metadata_path")
    metadata_path: Path | None = None
    if metadata:
        metadata_path = _resolve_repo_path(metadata, repo_root=repo_root)
        if not metadata_path.is_file():
            raise MarketPackageBuildPlanError(404, "metadata_path not found")
    else:
        metadata_path = adjacent_metadata_path(source_path)

    parser_result = payload.get("parser_result")
    if market_build_requires_parser_result(
        market=market,
        source_path=source_path,
        market_build_scripts=dict(market_build_scripts),
        eu_esef_package_build_script=eu_esef_package_build_script,
    ) and not parser_result:
        raise MarketPackageBuildPlanError(400, f"parser_result is required for {market} package builds")

    parser_result_path: Path | None = None
    if parser_result and market_build_accepts_parser_result(
        market=market,
        script=script,
        eu_esef_package_build_script=eu_esef_package_build_script,
    ):
        parser_result_path = _resolve_repo_path(parser_result, repo_root=repo_root)
        if not parser_result_path.exists():
            raise MarketPackageBuildPlanError(404, "parser_result not found")

    return MarketPackageBuildPlan(
        market=market,
        source_path=source_path,
        script=script,
        output_root=market_wiki_roots[market],
        metadata_path=metadata_path,
        parser_result_path=parser_result_path,
        force=bool(payload.get("force")),
    )


def market_package_build_args(
    *,
    executable: str,
    script: Path,
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    parser_result_path: Path | None = None,
    force: bool = False,
) -> list[str]:
    args = [executable, str(script), str(source_path)]
    if metadata_path is not None:
        args.extend(["--metadata", str(metadata_path)])
    if parser_result_path is not None:
        args.extend(["--parser-result", str(parser_result_path)])
    args.extend(["--output-root", str(output_root)])
    if force:
        args.append("--force")
    return args


def market_package_import_env(
    market: str,
    market_databases: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
    database_url: str | None = None,
) -> dict[str, str]:
    market_code = normalize_market_code(market)
    database = market_databases.get(market_code)
    default_database = str(database).strip() if database else ""
    explicit_database_url = str(database_url).strip() if database_url else ""
    if not default_database and not explicit_database_url:
        return {}

    env = dict(base_env) if base_env is not None else {}
    if explicit_database_url:
        env["DATABASE_URL"] = explicit_database_url
    else:
        env.pop("DATABASE_URL", None)
    if default_database:
        env_name = f"SIQ_{market_code}_PGDATABASE"
        existing = env.get(env_name)
        env[env_name] = str(existing).strip() if existing and str(existing).strip() else default_database
    return env


def market_package_import_args(
    *,
    executable: str,
    script: Path,
    market: str,
    package_dir: Path,
    payload: dict[str, Any],
) -> list[str]:
    args = [executable, str(script)]
    if market == "US":
        args.extend(["--package", str(package_dir)])
    else:
        args.append(str(package_dir))

    if payload.get("ddl") or payload.get("run_ddl"):
        args.append("--ddl")
    return args


def build_market_package_import_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    market_import_scripts: Mapping[str, Path],
    safe_market_package_path: Callable[[str, str], Path],
) -> MarketPackageImportPlan:
    package_dir = safe_market_package_path(market, str(payload.get("package_path") or ""))
    script = market_import_scripts[market]
    if not script.is_file():
        raise MarketPackagePlanError(404, f"Missing package import script: {script}")
    return MarketPackageImportPlan(
        market=market,
        package_dir=package_dir,
        script=script,
    )


def market_document_full_import_env(
    market: str,
    market_databases: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
    database_url: str | None = None,
) -> dict[str, str]:
    env = market_package_import_env(
        market,
        market_databases,
        base_env=base_env,
        database_url=database_url,
    )
    if database_url:
        env["SIQ_ALLOW_GENERIC_MARKET_DATABASE_URL"] = "1"
    return env


def market_document_full_import_args(
    *,
    executable: str,
    script: Path,
    market: str,
    document_full_path: Path,
    payload: Mapping[str, Any],
) -> list[str]:
    args = [executable, str(script), str(document_full_path), "--market", normalize_market_code(market)]
    if payload.get("ddl") or payload.get("run_ddl"):
        args.append("--ddl")
    if payload.get("force"):
        args.append("--force")
    return args


def build_market_document_full_import_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    market_document_full_import_scripts: Mapping[str, Path],
    safe_market_document_full_path: Callable[[str, str], Path],
    repo_root: Path | None = None,
    market_document_full_roots: Mapping[str, Path] | None = None,
) -> MarketDocumentFullImportPlan:
    market = normalize_market_code(market)
    raw_path = market_document_identity.document_full_payload_value(payload)
    if raw_path in (None, ""):
        raise MarketPackagePlanError(400, "document_full_path or task_id is required")
    try:
        task_id = str(payload.get("task_id") or "").strip() or None
        if repo_root is not None and market_document_full_roots is not None:
            identity = market_document_identity.resolve_document_full_identity(
                market=market,
                repo_root=repo_root,
                market_document_full_roots=market_document_full_roots,
                safe_market_document_full_path=safe_market_document_full_path,
                payload=payload,
                task_id=task_id,
            )
            document_full_path = identity.document_full_path
            if document_full_path is None:
                raise ValueError("document_full_path must resolve to document_full.json")
        else:
            document_full_path = market_document_identity.resolve_document_full_path(
                market=market,
                value=str(raw_path),
                safe_market_document_full_path=safe_market_document_full_path,
            )
            identity = market_document_identity.MarketDocumentFullIdentity(
                market=market,
                document_full_path=document_full_path,
                task_id=task_id,
            )
    except ValueError:
        raise MarketPackagePlanError(400, "document_full_path must resolve to document_full.json")
    if not document_full_path.is_file():
        raise MarketPackagePlanError(404, "document_full_path not found")

    script = market_document_full_import_scripts[market]
    if not script.is_file():
        raise MarketPackagePlanError(404, f"Missing document_full import script: {script}")
    return MarketDocumentFullImportPlan(
        market=market,
        document_full_path=document_full_path,
        script=script,
        identity=identity,
        selector=market_document_identity.build_import_selector(identity),
    )


def market_vector_ingest_args(
    *,
    executable: str,
    script: Path,
    package_dir: Path,
    payload: dict[str, Any],
    market: str | None = None,
    market_vector_collections: Mapping[str, str] | None = None,
) -> tuple[list[str], bool]:
    args = [
        executable,
        str(script),
        "--package",
        str(package_dir),
        "--batch-tag",
        str(payload.get("batch_tag") or "market-evidence"),
    ]
    collection = payload.get("collection")
    if collection in (None, "") and market and market_vector_collections:
        collection = market_vector_collections.get(market.upper())
    optional_values = {
        "collection": collection,
        "embed_url": payload.get("embed_url"),
        "embed_model": payload.get("embed_model"),
        "vector_dim": payload.get("vector_dim"),
    }
    for key, flag in (
        ("collection", "--collection"),
        ("embed_url", "--embed-url"),
        ("embed_model", "--embed-model"),
        ("vector_dim", "--vector-dim"),
    ):
        value = optional_values.get(key)
        if value not in (None, ""):
            args.extend([flag, str(value)])
    dry_run = bool(payload.get("dry_run", True))
    if dry_run:
        args.append("--dry-run")
    return args, dry_run


def build_market_vector_ingest_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    vector_ingest_script: Path,
    safe_market_package_path: Callable[[str, str], Path],
) -> MarketVectorIngestPlan:
    package_dir = safe_market_package_path(market, str(payload.get("package_path") or ""))
    if not vector_ingest_script.is_file():
        raise MarketPackagePlanError(404, f"Missing vector ingest script: {vector_ingest_script}")
    return MarketVectorIngestPlan(
        market=market,
        package_dir=package_dir,
        script=vector_ingest_script,
        dry_run=bool(payload.get("dry_run", True)),
    )


def _absolute_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def build_market_ingestion_eval_plan(
    *,
    payload: Mapping[str, Any],
    eval_script: Path,
    repo_root: Path,
    default_output: Path,
    default_markdown: Path,
) -> MarketIngestionEvalPlan:
    if not eval_script.is_file():
        raise MarketPackagePlanError(404, f"Missing eval script: {eval_script}")
    return MarketIngestionEvalPlan(
        script=eval_script,
        output_path=_absolute_path(payload.get("output") or default_output, repo_root=repo_root),
        markdown_path=_absolute_path(payload.get("markdown") or default_markdown, repo_root=repo_root),
    )


def market_ingestion_eval_args(
    *,
    executable: str,
    script: Path,
    payload: dict[str, Any],
    repo_root: Path,
    default_output: Path,
    default_markdown: Path,
) -> tuple[list[str], Path, Path]:
    output = _absolute_path(payload.get("output") or default_output, repo_root=repo_root)
    markdown = _absolute_path(payload.get("markdown") or default_markdown, repo_root=repo_root)
    return (
        [
            executable,
            str(script),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        output,
        markdown,
    )


def us_sec_ingest_args(
    *,
    executable: str,
    script: Path,
    case_set_path: Path,
    report_path: Path,
    payload: dict[str, Any],
    tickers: str = "",
    batch_tag: str = "",
) -> list[str]:
    args = [
        executable,
        str(script),
        "--case-set",
        str(case_set_path),
        "--report",
        str(report_path),
    ]
    if payload.get("include_fail"):
        args.append("--include-fail")
    if payload.get("postgres"):
        args.append("--postgres")
    if payload.get("milvus"):
        args.append("--milvus")
    if payload.get("ddl"):
        args.append("--ddl")
    if payload.get("dry_run", True):
        args.append("--dry-run")
    if tickers:
        args.extend(["--tickers", tickers])
    if batch_tag:
        args.extend(["--batch-tag", batch_tag])
    return args


def normalize_us_sec_ingest_filters(payload: Mapping[str, Any]) -> tuple[str, str]:
    tickers = str(payload.get("tickers") or "").strip().upper()
    if tickers and not re.fullmatch(r"[A-Z0-9.,_-]{1,240}", tickers):
        raise MarketPackagePlanError(400, "Invalid tickers")
    batch_tag = str(payload.get("batch_tag") or "").strip()
    if batch_tag and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", batch_tag):
        raise MarketPackagePlanError(400, "Invalid batch_tag")
    return tickers, batch_tag


def payload_force_enabled(payload: Mapping[str, Any]) -> bool:
    value = payload.get("force")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def force_audit_sources(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [payload]
    for key in ("force_audit", "audit", "override"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            sources.append(value)
    return sources


def force_audit_text(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for source in force_audit_sources(payload):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None


def force_one_shot_marker(payload: Mapping[str, Any]) -> str | None:
    false_values = {"0", "false", "no", "off", "none", "null"}
    for source in force_audit_sources(payload):
        for key in FORCE_ONE_SHOT_KEYS:
            if key not in source:
                continue
            value = source.get(key)
            if isinstance(value, bool):
                if value:
                    return "true"
                continue
            text = str(value or "").strip()
            if text and text.lower() not in false_values:
                return text
    return None


def redact_audit_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?i)(password|passwd|secret|token|api[_-]?key)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"://([^/\s:@]+):([^/\s@]+)@", "://[redacted]@", text)
    return text[:256]


def validate_force_audit(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, str | None]:
    reason = force_audit_text(payload, FORCE_REASON_KEYS)
    operator = force_audit_text(payload, FORCE_OPERATOR_KEYS)
    ticket = force_audit_text(payload, FORCE_TICKET_KEYS)
    expires_at = force_audit_text(payload, FORCE_EXPIRES_KEYS)
    one_shot = force_one_shot_marker(payload)

    missing: list[str] = []
    invalid: list[str] = []
    if not reason:
        missing.append("reason")
    if not operator:
        missing.append("operator")
    if not ticket:
        missing.append("ticket_or_change_id")
    if not expires_at and not one_shot:
        missing.append("expires_at_or_one_shot")
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            invalid.append("expires_at")
        else:
            if expires <= (now or datetime.now(timezone.utc)):
                invalid.append("expires_at")

    if missing or invalid:
        raise MarketPackagePlanError(
            400,
            {
                "message": (
                    "force=true requires audit fields: reason, operator/user id, "
                    "ticket/change id, and expires_at or one_shot."
                ),
                "missing_fields": missing,
                "invalid_fields": invalid,
            },
        )

    return {
        "reason": reason,
        "operator": operator,
        "ticket": ticket,
        "expires_at": expires_at,
        "one_shot": one_shot,
    }


def payload_with_force_operator(
    payload: Mapping[str, Any],
    *,
    user_id: object | None = None,
    username: object | None = None,
) -> dict[str, Any]:
    if not payload_force_enabled(payload):
        return dict(payload)
    if force_audit_text(payload, FORCE_OPERATOR_KEYS):
        return dict(payload)

    audit = dict(payload.get("force_audit")) if isinstance(payload.get("force_audit"), Mapping) else {}
    if user_id is not None:
        audit["operator_id"] = str(user_id)
    if username:
        audit["operator"] = str(username)
    return {**dict(payload), "force_audit": audit}


def market_package_quality_gate_decision(
    *,
    gates: Mapping[str, Any],
    payload: Mapping[str, Any],
    action: str,
    now: datetime | None = None,
) -> MarketPackageQualityGateDecision:
    blocked_key = "vector_ingest_blocked" if action == "vector_ingest" else "import_blocked"
    blocked = bool(gates.get(blocked_key))
    force_enabled = payload_force_enabled(payload)
    audit = validate_force_audit(payload, now=now) if force_enabled else None
    if not blocked:
        return MarketPackageQualityGateDecision(force_enabled=force_enabled, blocked=False, audit=audit)
    if force_enabled and gates.get("force_allowed") is True:
        return MarketPackageQualityGateDecision(force_enabled=True, blocked=True, audit=audit)
    if force_enabled:
        return MarketPackageQualityGateDecision(
            force_enabled=True,
            blocked=True,
            audit=audit,
            error_status_code=409,
            error_detail={
                "message": (
                    "Quality gates contain hard blocks; force=true cannot run formal "
                    f"{action} and is limited to review/quarantine material."
                ),
                "action": action,
                "quality_gates": dict(gates),
            },
        )
    if gates.get("force_allowed") is True:
        message = (
            "Quality gates block this action; force=true with required audit fields "
            "can request a soft-gate exception."
        )
    else:
        message = (
            "Quality gates contain hard blocks; fix the package or keep it in "
            "review/quarantine material before formal import or vector ingest."
        )
    return MarketPackageQualityGateDecision(
        force_enabled=False,
        blocked=True,
        audit=None,
        error_status_code=409,
        error_detail={
            "message": message,
            "action": action,
            "quality_gates": dict(gates),
        },
    )


def safe_filename_part(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(".-_")
    return text or "unknown"


def file_suffix_from_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return US_SEC_UPLOAD_CONTENT_TYPE_SUFFIXES.get(normalized, "")


def us_sec_upload_metadata_payload(
    *,
    file_path: Path,
    original_name: str,
    content_type: str | None,
    digest: str,
    size_bytes: int,
    ticker: str | None,
    company_name: str | None,
    report_type: str | None,
    report_family: str | None,
    period_end: str | None,
    filing_date: str | None,
    fallback_published_at: str,
) -> dict[str, Any]:
    effective_report_type = (report_type or file_path.suffix.lower().lstrip(".") or "file").strip()
    effective_form = effective_report_type.upper() if effective_report_type not in {"file", ""} else "FILE"
    resolved_path = file_path.resolve()
    return {
        "candidate": {
            "source_id": "manual_upload",
            "source_name": "US SEC Manual Upload",
            "source_domain": "local",
            "market": "US",
            "company_id": "manual",
            "ticker": ticker,
            "company_name": company_name or file_path.parent.parent.parent.name or "Manual Upload",
            "report_type": report_family or effective_report_type,
            "report_family": report_family or "current",
            "form": effective_form,
            "title": original_name,
            "accession_number": "manual-upload",
            "primary_document": original_name,
            "report_end": period_end,
            "published_at": filing_date or fallback_published_at,
            "accepted_at": None,
            "document_url": f"file://{resolved_path}",
            "landing_url": f"file://{resolved_path}",
            "file_format": file_path.suffix.lower().lstrip(".") or "bin",
            "language": None,
            "inline_xbrl": None,
            "metadata": {
                "uploaded_filename": original_name,
                "content_type": content_type,
            },
        },
        "downloaded_file": {
            "file_name": file_path.name,
            "saved_path": str(resolved_path),
            "size_bytes": size_bytes,
            "content_type": content_type,
            "content_sha256": digest,
        },
    }


def us_sec_rebuild_package_args(
    *,
    executable: str,
    script: Path,
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    force: bool = True,
) -> list[str]:
    args = [executable, str(script), str(source_path)]
    if force:
        args.append("--force")
    if metadata_path is not None:
        args.extend(["--metadata", str(metadata_path)])
    args.extend(["--output-root", str(output_root)])
    return args


def build_us_sec_rebuild_package_plan(
    *,
    ticker: str,
    latest_case_item: Callable[[str], Mapping[str, Any] | None],
    safe_package_path: Callable[[str], Path],
    read_json_file: Callable[[Path, Any], Any],
    safe_under: Callable[[Path, Path], Path],
    package_build_script: Path,
    output_root: Path,
) -> UsSecRebuildPackagePlan:
    normalized_ticker = str(ticker or "").strip().upper()
    item = latest_case_item(normalized_ticker)
    if not item:
        raise MarketPackagePlanError(404, f"No package for ticker {normalized_ticker}")
    package_dir = safe_package_path(str(item.get("package_path") or ""))
    manifest = read_json_file(package_dir / "manifest.json", {}) or {}
    local_source = str(manifest.get("local_source_path") or "raw/filing.htm")
    source_path = safe_under(package_dir, package_dir / local_source)
    if not source_path.is_file():
        raise MarketPackagePlanError(404, "Raw SEC filing source not found in package")
    if not package_build_script.is_file():
        raise MarketPackagePlanError(404, f"Missing package build script: {package_build_script}")
    metadata_path = package_dir / "raw" / "filing.metadata.json"
    return UsSecRebuildPackagePlan(
        ticker=normalized_ticker,
        package_dir=package_dir,
        source_path=source_path,
        metadata_path=metadata_path if metadata_path.is_file() else None,
        script=package_build_script,
        output_root=output_root,
    )


def _tail(value: str | None, limit: int) -> str:
    return (value or "")[-limit:]


def _last_stdout_line(stdout: str | None) -> str | None:
    lines = (stdout or "").strip().splitlines()
    return lines[-1] if lines else None


def market_package_build_result_payload(
    *,
    completed: Any,
    command: str,
    package: dict[str, Any] | None = None,
    missing_path_message: str = "Package build did not print a package path",
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    if returncode != 0:
        return {
            "ok": False,
            "returncode": returncode,
            "stdout": _tail(stdout, 4000),
            "stderr": _tail(stderr, 4000),
            "command": command,
        }
    if package is None:
        return {
            "ok": False,
            "returncode": returncode,
            "stdout": _tail(stdout, 4000),
            "stderr": missing_path_message,
            "command": command,
        }
    result = {
        "ok": True,
        "package": package,
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "command": command,
    }
    if package.get("parser_result_dir"):
        result["parser_result_dir"] = package.get("parser_result_dir")
    if package.get("parser_result_task_id"):
        result["parser_result_task_id"] = package.get("parser_result_task_id")
    return result


def market_package_import_result_payload(*, completed: Any, command: str) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "parse_run_id": _last_stdout_line(stdout) if returncode == 0 else None,
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "command": command,
    }


def market_document_full_import_result_payload(*, completed: Any, command: str) -> dict[str, Any]:
    return market_package_import_result_payload(completed=completed, command=command)


def _json_object_from_stdout(stdout: str | None) -> dict[str, Any] | None:
    if not stdout:
        return None
    decoder = json.JSONDecoder()
    text = stdout.rstrip()
    best: tuple[int, dict[str, Any]] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text[index:])
        except Exception:
            continue
        absolute_end = index + end
        trailing = text[absolute_end:]
        if not isinstance(parsed, dict):
            continue
        if trailing.strip():
            # Allow separate log lines after a JSON block, but reject same-line
            # suffixes and later brace-looking fragments so nested objects do
            # not win over their containing summary object.
            if not trailing.lstrip(" \t").startswith(("\n", "\r")):
                continue
            if any("{" in line or "}" in line for line in trailing.splitlines() if line.strip()):
                continue
        if best is None or absolute_end > best[0]:
            best = (absolute_end, parsed)
    return best[1] if best else None


def market_vector_ingest_result_payload(*, completed: Any, dry_run: bool, command: str) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "dry_run": dry_run,
        "returncode": returncode,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "summary": _json_object_from_stdout(stdout),
        "command": command,
    }


def market_ingestion_eval_result_payload(
    *,
    completed: Any,
    report: Any,
    markdown_path: str,
    command: str,
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "report": report,
        "markdown_path": markdown_path,
        "command": command,
    }


def us_sec_case_set_ingest_result_payload(
    *,
    completed: Any,
    report: Any,
    command: str,
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "command": command,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "report": report,
    }


def us_sec_rebuild_package_result_payload(
    *,
    completed: Any,
    ticker: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    result = {
        "ok": True,
        "ticker": ticker.upper(),
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "package": package,
    }
    if package.get("parser_result_dir"):
        result["parser_result_dir"] = package.get("parser_result_dir")
    if package.get("parser_result_task_id"):
        result["parser_result_task_id"] = package.get("parser_result_task_id")
    return result
