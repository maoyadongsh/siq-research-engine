"""Manifest-first, path-safe resolution of multi-market report packages."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siq_market_contracts import (
    AgentArtifactV2,
    ContractValidationError,
    ResearchIdentity,
    ResearchTargetV1,
    SourceReportV1,
)

from services import agent_runtime_catalog, agent_runtime_context, agent_runtime_wiki_context
from services.research_universe_contracts import (
    RESEARCH_MARKET_ORDER,
    ResearchUniverseError,
    normalize_agent_type,
    normalize_market,
)

FACT_DIRECTORIES = frozenset({"reports", "metrics", "evidence", "semantic", "graph"})
OUTPUT_DIRECTORY_NAMES = ("analysis", "factcheck", "tracking")
CLIENT_PATH_KEYS = frozenset(
    {
        "company_dir",
        "report_dir",
        "output_dir",
        "company_path",
        "source_report_path",
        "manifest_path",
        "html_file",
        "path",
    }
)


@dataclass(frozen=True)
class ResolvedCompany:
    market: str
    company_key: str
    company_id: str
    company_wiki_id: str
    display_code: str
    display_name: str
    market_root: Path
    company_dir: Path
    catalog_company: Mapping[str, Any]
    company_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ResolvedReportPackage:
    research_target: ResearchTargetV1
    agent_type: str
    market_root: Path
    company_dir: Path
    report_dir: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    fulltext_paths: tuple[Path, ...]
    metric_paths: tuple[Path, ...]
    evidence_paths: tuple[Path, ...]
    xbrl_paths: tuple[Path, ...]
    output_dirs: Mapping[str, Path]
    readiness: Mapping[str, bool]
    capabilities: Mapping[str, bool]
    degraded_reasons: tuple[str, ...]
    compatibility_mode: str | None = None

    @property
    def market(self) -> str:
        return self.research_target.research_identity.market

    @property
    def company_key(self) -> str:
        return self.research_target.company_key

    @property
    def report_id(self) -> str:
        return self.research_target.source_report.report_id

    @property
    def research_identity(self) -> ResearchIdentity:
        return self.research_target.research_identity

    @property
    def output_dir(self) -> Path:
        output_type = self.agent_type if self.agent_type in self.output_dirs else "analysis"
        return self.output_dirs[output_type]

    def output_dir_for(self, artifact_type: str) -> Path:
        try:
            return self.output_dirs[artifact_type]
        except KeyError as exc:
            raise ResearchUniverseError(
                "artifact_type_not_supported",
                "The requested artifact type is not supported.",
                400,
            ) from exc

    def to_research_target_dict(self) -> dict[str, Any]:
        return self.research_target.to_dict()


@dataclass(frozen=True)
class ExactArtifactPage:
    items: tuple[tuple[AgentArtifactV2, Path, Path], ...]
    next_offset: int
    has_more: bool


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_id(value: Any, *, code: str) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or len(path.parts) != 1 or text in {".", ".."}:
        raise ResearchUniverseError(code, "An unsafe identifier was rejected.", 400)
    return text


def _within(root: Path, candidate: Path, *, require_exists: bool = False) -> Path:
    root = root.resolve()
    try:
        resolved = candidate.resolve(strict=require_exists)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchUniverseError(
            "unsafe_path_rejected",
            "A path escaped the approved market workspace.",
            400,
        ) from exc
    return resolved


def _readable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def company_key_for(market: str, company_id: str, company_wiki_id: str) -> str:
    market = normalize_market(market)
    payload = f"siq-research-company-v1\0{market}\0{company_id}\0{company_wiki_id}".encode("utf-8")
    return "rk1_" + hashlib.sha256(payload).hexdigest()[:32]


def _company_identity(
    catalog_company: Mapping[str, Any],
    company_metadata: Mapping[str, Any],
    *,
    catalog_resolved_wiki_id: str,
) -> tuple[str, str, str, str]:
    company_id = str(
        catalog_company.get("company_id")
        or company_metadata.get("company_id")
        or catalog_company.get("company_wiki_id")
        or ""
    ).strip()
    wiki_id = str(
        catalog_company.get("company_wiki_id")
        or company_metadata.get("company_wiki_id")
        or catalog_resolved_wiki_id
        or ""
    ).strip()
    code = agent_runtime_catalog.catalog_company_code(dict(catalog_company)) or wiki_id.split("-", 1)[0]
    name = agent_runtime_catalog.catalog_company_name(dict(catalog_company))
    if not name:
        name = str(company_metadata.get("company_name") or company_metadata.get("company_short_name") or "").strip()
    if not name and "-" in wiki_id:
        name = wiki_id.split("-", 1)[1]
    return company_id, wiki_id, code, name


def enumerate_companies(
    *,
    wiki_root: Path | str | None = None,
    markets: Sequence[str] | None = None,
) -> tuple[ResolvedCompany, ...]:
    root = Path(wiki_root) if wiki_root is not None else agent_runtime_catalog.WIKI_ROOT
    selected = tuple(normalize_market(item) for item in (markets or RESEARCH_MARKET_ORDER))
    catalogs = agent_runtime_catalog.load_market_catalogs(
        wiki_root=root,
        markets=selected,
        include_unclassified=False,
    )
    output: list[ResolvedCompany] = []
    seen_keys: set[str] = set()
    for catalog in catalogs:
        companies_root = (catalog.wiki_root / "companies").resolve()
        for item in catalog.companies:
            raw_dir = agent_runtime_catalog.catalog_company_dir(catalog, dict(item))
            if raw_dir is None:
                continue
            try:
                company_dir = _within(companies_root, raw_dir, require_exists=True)
            except ResearchUniverseError:
                continue
            company_metadata = _read_json(company_dir / "company.json") or {}
            company_id, wiki_id, code, name = _company_identity(
                item,
                company_metadata,
                catalog_resolved_wiki_id=company_dir.name,
            )
            if not all((company_id, wiki_id, code, name)):
                continue
            key = company_key_for(catalog.market, company_id, wiki_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            output.append(
                ResolvedCompany(
                    market=catalog.market,
                    company_key=key,
                    company_id=company_id,
                    company_wiki_id=wiki_id,
                    display_code=code,
                    display_name=name,
                    market_root=catalog.wiki_root.resolve(),
                    company_dir=company_dir,
                    catalog_company=dict(item),
                    company_metadata=company_metadata,
                )
            )
    market_index = {market: index for index, market in enumerate(RESEARCH_MARKET_ORDER)}
    output.sort(key=lambda item: (market_index[item.market], item.display_code.upper(), item.display_name.casefold()))
    return tuple(output)


def resolve_company(
    *,
    market: str,
    company_key: str,
    wiki_root: Path | str | None = None,
) -> ResolvedCompany:
    normalized_market = normalize_market(market)
    company_key = _safe_id(company_key, code="company_not_found")
    matches = [
        item
        for item in enumerate_companies(wiki_root=wiki_root, markets=(normalized_market,))
        if item.company_key == company_key
    ]
    if len(matches) == 1:
        return matches[0]
    cross_market = any(item.company_key == company_key for item in enumerate_companies(wiki_root=wiki_root))
    if cross_market:
        raise ResearchUniverseError(
            "company_market_mismatch",
            "The company key does not belong to the requested market.",
            409,
        )
    raise ResearchUniverseError("company_not_found", "The requested company was not found.", 404)


def _report_records(company: ResolvedCompany) -> tuple[dict[str, Any], ...]:
    records: dict[str, dict[str, Any]] = {}
    raw_reports = company.company_metadata.get("reports")
    if isinstance(raw_reports, list):
        for raw in raw_reports:
            if not isinstance(raw, Mapping):
                continue
            try:
                report_id = _safe_id(raw.get("report_id"), code="source_report_not_found")
            except ResearchUniverseError:
                continue
            records[report_id] = dict(raw)
    reports_root = _within(company.company_dir, company.company_dir / "reports")
    if reports_root.is_dir():
        for path in reports_root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                report_id = _safe_id(path.name, code="source_report_not_found")
                _within(reports_root, path, require_exists=True)
            except ResearchUniverseError:
                continue
            if (path / "manifest.json").is_file() or company.market == "CN":
                records.setdefault(report_id, {"report_id": report_id})
    return tuple(records[key] for key in sorted(records))


def _manifest_and_mode(company: ResolvedCompany, report_dir: Path) -> tuple[Path, dict[str, Any], str | None]:
    manifest_path = _within(report_dir, report_dir / "manifest.json")
    manifest = _read_json(manifest_path)
    if manifest is not None:
        return manifest_path, manifest, None
    if company.market == "CN":
        compatibility_path = _within(report_dir, report_dir / "artifact_manifest.json")
        compatibility = _read_json(compatibility_path)
        if compatibility is not None:
            return compatibility_path, compatibility, "cn_legacy_artifact_manifest"
    return manifest_path, {}, None


def _source_family(market: str, manifest: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    explicit = str(manifest.get("source_family") or record.get("source_family") or "").strip().lower()
    if explicit in {"pdf_market", "sec_ixbrl", "esef_ixbrl"}:
        return explicit
    source_id = str(manifest.get("source_id") or record.get("source_id") or "").strip().lower()
    document_format = str(manifest.get("document_format") or record.get("document_format") or "").lower()
    if source_id == "sec" or (market == "US" and "ixbrl" in document_format):
        return "sec_ixbrl"
    if market == "EU" and "ixbrl" in document_format:
        return "esef_ixbrl"
    return "pdf_market"


def _document_format(source_family: str, manifest: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    value = str(manifest.get("document_format") or record.get("document_format") or "").strip().lower()
    aliases = {"htm": "html", "xhtml": "ixbrl_html", "ixbrl": "ixbrl_html"}
    value = aliases.get(value, value)
    if value in {"pdf", "html", "ixbrl_html", "markdown", "json"}:
        return value
    return "ixbrl_html" if source_family in {"sec_ixbrl", "esef_ixbrl"} else "pdf"


def _quality_status(manifest: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    value = str(
        manifest.get("quality_status") or record.get("quality_status") or record.get("status") or "unknown"
    ).lower()
    if value == "ready":
        return "pass"
    return value if value in {"pass", "warning", "fail"} else "unknown"


def _normalized_identity_identifier(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    if upper.startswith("US_SEC:") or upper.startswith("US-SEC:"):
        return f"US:{text.split(':', 1)[1]}"
    return text


def _manifest_path_value(manifest: Mapping[str, Any], key: str) -> Any:
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    paths = manifest.get("paths") if isinstance(manifest.get("paths"), Mapping) else {}
    value = artifacts.get(key) or paths.get(key)
    if isinstance(value, Mapping):
        return value.get("path")
    return value


def _candidate_path(
    company: ResolvedCompany,
    report_dir: Path,
    value: Any,
    *,
    base: str = "report",
) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    relative = Path(text)
    if relative.is_absolute():
        raise ResearchUniverseError("unsafe_path_rejected", "An absolute manifest artifact path was rejected.", 400)
    if any(part == ".." for part in relative.parts):
        raise ResearchUniverseError("unsafe_path_rejected", "A traversing manifest artifact path was rejected.", 400)
    if relative.parts[:1] == ("reports",):
        candidate = company.company_dir / relative
    elif relative.parts[:2] == ("data", "wiki"):
        raise ResearchUniverseError(
            "unsafe_path_rejected", "A repository-relative manifest artifact path was rejected.", 400
        )
    elif base == "company":
        candidate = company.company_dir / relative
    else:
        candidate = report_dir / relative
    return _within(company.company_dir, candidate)


def _existing_paths(
    company: ResolvedCompany,
    report_dir: Path,
    values: Sequence[tuple[Any, str]],
) -> tuple[Path, ...]:
    output: list[Path] = []
    seen: set[Path] = set()
    for value, base in values:
        if value in (None, ""):
            continue
        candidate = _candidate_path(company, report_dir, value, base=base)
        if candidate is not None and candidate not in seen and _readable_file(candidate):
            seen.add(candidate)
            output.append(candidate)
    return tuple(output)


def _adapter_available(source_family: str) -> bool:
    if source_family == "pdf_market":
        return True
    if source_family == "sec_ixbrl":
        return os.getenv("SIQ_US_SEC_ANALYSIS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    return False


def _identity_matches_expected(actual: ResearchIdentity, expected: Mapping[str, Any] | ResearchIdentity | None) -> None:
    if expected is None:
        return
    raw = expected.to_dict() if isinstance(expected, ResearchIdentity) else dict(expected)
    provided = {key: str(raw.get(key) or "").strip() for key in ("market", "company_id", "filing_id", "parse_run_id")}
    if actual.market != "CN" and any(provided.values()) and not all(provided.values()):
        raise ResearchUniverseError(
            "research_identity_incomplete",
            "A complete ResearchIdentity is required for non-CN reports.",
            409,
        )
    if all(provided.values()):
        try:
            normalized_expected = ResearchIdentity.from_dict(provided)
        except ContractValidationError as exc:
            raise ResearchUniverseError(
                "research_identity_incomplete",
                "The requested ResearchIdentity is invalid.",
                409,
            ) from exc
        if not actual.matches(normalized_expected):
            raise ResearchUniverseError(
                "research_identity_mismatch",
                "The requested ResearchIdentity does not match the authoritative report manifest.",
                409,
            )
        return
    actual_values = actual.to_dict()
    for key, value in provided.items():
        if value and (value.upper() if key == "market" else value) != actual_values[key]:
            raise ResearchUniverseError(
                "research_identity_mismatch",
                "The requested ResearchIdentity does not match the authoritative report manifest.",
                409,
            )


def _build_report_package(
    company: ResolvedCompany,
    record: Mapping[str, Any],
    *,
    agent_type: str,
) -> ResolvedReportPackage:
    report_id = _safe_id(record.get("report_id"), code="source_report_not_found")
    reports_root = _within(company.company_dir, company.company_dir / "reports")
    report_dir = _within(reports_root, reports_root / report_id)
    manifest_path, manifest, compatibility_mode = _manifest_and_mode(company, report_dir)
    reasons: list[str] = []
    if not manifest:
        reasons.append("manifest_missing_or_invalid")

    manifest_market = str(manifest.get("market") or record.get("market") or company.market).strip().upper()
    if manifest_market and manifest_market != company.market:
        reasons.append("manifest_market_mismatch")
    manifest_wiki_id = str(manifest.get("company_wiki_id") or "").strip()
    if manifest_wiki_id and manifest_wiki_id != company.company_wiki_id:
        reasons.append("manifest_company_mismatch")
    manifest_report_id = str(manifest.get("report_id") or "").strip()
    if manifest_report_id and manifest_report_id != report_id:
        reasons.append("manifest_report_mismatch")
    for field in ("company_id", "filing_id", "parse_run_id"):
        record_value = _normalized_identity_identifier(record.get(field))
        manifest_value = _normalized_identity_identifier(manifest.get(field))
        if record_value and manifest_value and record_value != manifest_value:
            reasons.append("report_manifest_identity_mismatch")

    company_id = str(manifest.get("company_id") or record.get("company_id") or company.company_id).strip()
    filing_id = str(manifest.get("filing_id") or record.get("filing_id") or "").strip()
    parse_run_id = str(manifest.get("parse_run_id") or record.get("parse_run_id") or "").strip()
    if compatibility_mode:
        task_id = str(record.get("task_id") or manifest.get("task_id") or "").strip()
        filing_id = filing_id or (f"CN:{company_id}:{report_id}" if company_id else "")
        parse_run_id = parse_run_id or task_id

    identity: ResearchIdentity | None = None
    try:
        identity = ResearchIdentity(
            market=company.market,
            company_id=company_id,
            filing_id=filing_id,
            parse_run_id=parse_run_id,
        )
    except ContractValidationError:
        reasons.append("research_identity_incomplete")
    if identity is not None and company.market != "CN":
        existing_selection = agent_runtime_wiki_context.select_report_for_research_identity(
            company.company_metadata,
            company.company_dir,
            identity.to_dict(),
            read_json_file=_read_json,
        )
        if existing_selection.get("selection_status") != "identity_exact":
            reasons.append("runtime_identity_selection_mismatch")

    source_family = _source_family(company.market, manifest, record)
    document_format = _document_format(source_family, manifest, record)
    quality_status = _quality_status(manifest, record)
    if quality_status == "fail":
        reasons.append("quality_status_fail")

    fulltext_paths = _existing_paths(
        company,
        report_dir,
        (
            (_manifest_path_value(manifest, "document_full"), "report"),
            (_manifest_path_value(manifest, "wiki_report_complete"), "report"),
            (_manifest_path_value(manifest, "report_complete"), "report"),
            (record.get("document_full"), "company"),
            (record.get("report_md"), "company"),
            ("document_full.json", "report"),
            ("report.md", "report"),
            ("parser/document_full.json", "report"),
            ("sections/report_complete.md", "report"),
            ("parser/report_complete.md", "report"),
        ),
    )
    metric_paths = _existing_paths(
        company,
        report_dir,
        (
            (_manifest_path_value(manifest, "financial_data"), "report"),
            (_manifest_path_value(manifest, "normalized_metrics"), "report"),
            (_manifest_path_value(manifest, "financial_checks"), "report"),
            (f"metrics/reports/{report_id}/three_statements.json", "company"),
            (f"metrics/reports/{report_id}/key_metrics.json", "company"),
            (f"metrics/reports/{report_id}/financial_data.json", "company"),
            ("metrics/financial_data.json", "report"),
            ("metrics/normalized_metrics.json", "report"),
            ("metrics/key_metrics.json", "report"),
        ),
    )
    evidence_paths = _existing_paths(
        company,
        report_dir,
        (
            (_manifest_path_value(manifest, "source_map"), "report"),
            ("qa/source_map.json", "report"),
            ("evidence/source_map_latest.json", "company"),
            ("evidence/evidence_index.json", "company"),
            ("evidence/pdf_refs.json", "company"),
        ),
    )
    xbrl_paths = _existing_paths(
        company,
        report_dir,
        tuple(
            (_manifest_path_value(manifest, key) or default, "report")
            for key, default in (
                ("xbrl_facts_raw", "xbrl/facts_raw.json"),
                ("xbrl_contexts", "xbrl/contexts.json"),
                ("xbrl_units", "xbrl/units.json"),
                ("xbrl_labels", "xbrl/labels.json"),
                ("table_index", "tables/table_index.json"),
            )
        ),
    )
    if not fulltext_paths:
        reasons.append("fulltext_missing")
    if not metric_paths:
        reasons.append("structured_metrics_missing")
    if not evidence_paths:
        reasons.append("evidence_source_map_missing")

    identity_ready = identity is not None and not any(reason.endswith("mismatch") for reason in reasons)
    parsed_ready = (
        bool(manifest)
        and identity_ready
        and quality_status != "fail"
        and bool(fulltext_paths and metric_paths and evidence_paths)
    )
    adapter_available = _adapter_available(source_family)
    if parsed_ready and not adapter_available:
        reasons.append("source_adapter_unavailable")

    if identity is None:
        # The incomplete target is not exposed as a formal package. Use stable
        # placeholders only for diagnostics inside the unavailable report row.
        raise ResearchUniverseError(
            "research_identity_incomplete",
            "The report package does not contain a complete ResearchIdentity.",
            409,
        )

    try:
        source_report = SourceReportV1(
            report_id=report_id,
            source_family=source_family,
            document_format=document_format,
            report_type=str(
                manifest.get("report_type") or record.get("report_type") or record.get("report_kind") or "unknown"
            ),
            form_type=str(manifest.get("form") or record.get("form") or "").strip() or None,
            fiscal_year=manifest.get("fiscal_year") or record.get("fiscal_year") or record.get("report_year"),
            period_end=manifest.get("period_end") or record.get("period_end"),
            published_at=manifest.get("published_at") or manifest.get("filing_date") or record.get("published_at"),
            accounting_standard=str(manifest.get("accounting_standard") or "").strip() or None,
            reporting_currency=str(manifest.get("reporting_currency") or manifest.get("currency") or "").strip()
            or None,
            quality_status=quality_status,
        )
        target = ResearchTargetV1(
            company_key=company.company_key,
            company_wiki_id=company.company_wiki_id,
            display_code=company.display_code,
            display_name=company.display_name,
            research_identity=identity,
            source_report=source_report,
        )
    except ContractValidationError as exc:
        raise ResearchUniverseError("source_package_not_ready", str(exc), 409) from exc

    output_dirs = {name: _within(company.company_dir, company.company_dir / name) for name in OUTPUT_DIRECTORY_NAMES}
    readiness = {
        "catalog_visible": True,
        "identity_ready": identity_ready,
        "parsed_ready": parsed_ready,
    }
    capabilities = {
        "analysis_input_ready": parsed_ready and adapter_available,
        "analysis_output_ready": False,
        "factcheck_ready": False,
        "tracking_ready": False,
    }
    return ResolvedReportPackage(
        research_target=target,
        agent_type=agent_type,
        market_root=company.market_root,
        company_dir=company.company_dir,
        report_dir=report_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        fulltext_paths=fulltext_paths,
        metric_paths=metric_paths,
        evidence_paths=evidence_paths,
        xbrl_paths=xbrl_paths,
        output_dirs=output_dirs,
        readiness=readiness,
        capabilities=capabilities,
        degraded_reasons=tuple(dict.fromkeys(reasons)),
        compatibility_mode=compatibility_mode,
    )


def enumerate_report_packages(
    company: ResolvedCompany,
    *,
    agent_type: str,
    include_unready: bool = False,
) -> tuple[ResolvedReportPackage, ...]:
    agent_type = normalize_agent_type(agent_type)
    packages: list[ResolvedReportPackage] = []
    for record in _report_records(company):
        try:
            package = _build_report_package(company, record, agent_type=agent_type)
        except ResearchUniverseError:
            continue
        if include_unready or package.readiness["parsed_ready"]:
            packages.append(package)
    packages.sort(
        key=lambda item: (
            item.research_target.source_report.period_end or "",
            item.research_target.source_report.published_at or "",
            item.report_id,
        ),
        reverse=True,
    )
    return tuple(packages)


def resolve_report_package(
    *,
    market: str,
    company_key: str,
    report_id: str,
    agent_type: str,
    wiki_root: Path | str | None = None,
    expected_identity: Mapping[str, Any] | ResearchIdentity | None = None,
    require_parsed_ready: bool = True,
) -> ResolvedReportPackage:
    agent_type = normalize_agent_type(agent_type)
    market = normalize_market(market)
    if agent_type == "legal" and market != "CN":
        raise ResearchUniverseError("market_not_supported", "Legal research is limited to CN.", 404)
    company = resolve_company(market=market, company_key=company_key, wiki_root=wiki_root)
    safe_report_id = _safe_id(report_id, code="source_report_not_found")
    record = next((item for item in _report_records(company) if item.get("report_id") == safe_report_id), None)
    if record is None:
        raise ResearchUniverseError("source_report_not_found", "The requested source report was not found.", 404)
    package = _build_report_package(company, record, agent_type=agent_type)
    _identity_matches_expected(package.research_identity, expected_identity)
    if require_parsed_ready and not package.readiness["parsed_ready"]:
        raise ResearchUniverseError(
            "source_package_not_ready",
            "The requested source report is not parsed-ready.",
            409,
        )
    return package


def _reject_client_paths(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in CLIENT_PATH_KEYS and item not in (None, ""):
                raise ResearchUniverseError(
                    "unsafe_path_rejected",
                    "Client-provided filesystem paths are not accepted.",
                    400,
                )
            _reject_client_paths(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_client_paths(item)


def resolve_report_package_from_context(
    context: Any,
    *,
    agent_type: str,
    wiki_root: Path | str | None = None,
) -> ResolvedReportPackage:
    raw = agent_runtime_context.context_dict(context)
    _reject_client_paths(raw)
    company = raw.get("company") if isinstance(raw.get("company"), Mapping) else {}
    source_report = raw.get("source_report") if isinstance(raw.get("source_report"), Mapping) else {}
    report = raw.get("report") if isinstance(raw.get("report"), Mapping) else {}
    target = raw.get("research_target") if isinstance(raw.get("research_target"), Mapping) else {}
    target_report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
    target_identity = target.get("research_identity") if isinstance(target.get("research_identity"), Mapping) else {}
    identity = agent_runtime_context.research_identity(raw)
    if not identity and source_report:
        identity = agent_runtime_context.research_identity({"research_identity": source_report})
    if not identity and target_identity:
        identity = agent_runtime_context.research_identity({"research_identity": target_identity})
    market = str(
        raw.get("market")
        or company.get("market")
        or source_report.get("market")
        or identity.get("market")
        or target_identity.get("market")
        or ""
    ).strip()
    company_key = str(raw.get("company_key") or company.get("company_key") or target.get("company_key") or "").strip()
    report_id = str(
        raw.get("report_id")
        or source_report.get("report_id")
        or report.get("report_id")
        or target_report.get("report_id")
        or ""
    ).strip()
    if not market:
        raise ResearchUniverseError("market_not_supported", "The context does not specify a market.", 400)
    if not company_key:
        raise ResearchUniverseError("company_not_found", "The context does not specify a company key.", 400)
    if not report_id:
        raise ResearchUniverseError("source_report_not_found", "The context does not specify a source report.", 400)
    if normalize_market(market) != "CN" and os.getenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "0").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise ResearchUniverseError(
            "source_adapter_unavailable",
            "Multi-market research is disabled.",
            409,
        )
    return resolve_report_package(
        market=market,
        company_key=company_key,
        report_id=report_id,
        agent_type=agent_type,
        wiki_root=wiki_root,
        expected_identity=identity or None,
    )


def _exact_artifact_candidate(
    package: ResolvedReportPackage,
    artifact_type: str,
    sidecar_path: Path,
) -> tuple[AgentArtifactV2, Path, Path] | None:
    output_dir = package.output_dir_for(artifact_type)
    try:
        safe_sidecar = _within(output_dir, sidecar_path, require_exists=True)
    except ResearchUniverseError:
        return None
    payload = _read_json(safe_sidecar)
    if payload is None:
        return None
    try:
        artifact = AgentArtifactV2.from_dict(payload)
    except ContractValidationError:
        return None
    if safe_sidecar.name != f"{artifact.artifact_id}.artifact.json":
        return None
    if artifact.artifact_type != artifact_type or artifact.research_target is None:
        return None
    target = artifact.research_target
    if target.source_report.report_id != package.report_id:
        return None
    if target.company_key != package.company_key:
        return None
    if target.company_wiki_id != package.research_target.company_wiki_id:
        return None
    if target.source_report.source_family != package.research_target.source_report.source_family:
        return None
    if not target.research_identity.matches(package.research_identity):
        return None
    try:
        html_path = _within(output_dir, output_dir / artifact.html_file, require_exists=True)
    except ResearchUniverseError:
        return None
    if not html_path.is_file():
        return None
    return artifact, safe_sidecar, html_path


def _exact_artifact_candidates(
    package: ResolvedReportPackage,
    artifact_type: str,
    *,
    artifact_id: str | None = None,
) -> tuple[tuple[AgentArtifactV2, Path, Path], ...]:
    output_dir = package.output_dir_for(artifact_type)
    if not output_dir.is_dir():
        return ()
    if artifact_id is not None:
        try:
            safe_artifact_id = _safe_id(artifact_id, code="artifact_not_found")
        except ResearchUniverseError:
            return ()
        paths = (output_dir / f"{safe_artifact_id}.artifact.json",)
    else:
        paths = tuple(output_dir.glob("*.artifact.json"))
    candidates = [
        candidate
        for path in paths
        if (candidate := _exact_artifact_candidate(package, artifact_type, path)) is not None
    ]
    candidates.sort(key=lambda item: (item[0].created_at, item[0].artifact_id), reverse=True)
    return tuple(candidates)


def exact_artifact_bindings(
    package: ResolvedReportPackage,
    artifact_type: str,
) -> tuple[tuple[Path, str], ...]:
    """Return identity-validated bindings without reading large HTML bodies."""
    return tuple(
        (
            html_path,
            str(artifact.content_hash or "").removeprefix("sha256:").lower(),
        )
        for artifact, _sidecar, html_path in _exact_artifact_candidates(package, artifact_type)
    )


def has_exact_artifact_metadata(
    package: ResolvedReportPackage,
    artifact_type: str,
    *,
    statuses: frozenset[str] | None = None,
) -> bool:
    """Cheap catalog hint; content integrity is checked before selection or use."""
    output_dir = package.output_dir_for(artifact_type)
    if not output_dir.is_dir():
        return False
    accepted = statuses or frozenset({"completed", "degraded"})
    for sidecar_path in output_dir.glob("*.artifact.json"):
        candidate = _exact_artifact_candidate(package, artifact_type, sidecar_path)
        if candidate is not None and candidate[0].status in accepted:
            return True
    return False


def page_exact_artifact_sidecars(
    package: ResolvedReportPackage,
    artifact_type: str,
    *,
    offset: int = 0,
    limit: int = 20,
    artifact_id: str | None = None,
    verify_content: bool = True,
) -> ExactArtifactPage:
    """Page identity-valid metadata; verify HTML only for consumers that need content."""
    candidates = _exact_artifact_candidates(package, artifact_type, artifact_id=artifact_id)
    start = 0 if artifact_id is not None else max(offset, 0)
    rows: list[tuple[AgentArtifactV2, Path, Path]] = []
    next_offset = min(start, len(candidates))
    for index, candidate in enumerate(candidates[start:], start=start):
        next_offset = index + 1
        artifact, _sidecar, html_path = candidate
        if verify_content:
            expected_hash = str(artifact.content_hash or "").removeprefix("sha256:").lower()
            if expected_hash:
                if _sha256(html_path) != expected_hash:
                    continue
            elif not _readable_file(html_path):
                continue
        rows.append(candidate)
        if len(rows) >= limit:
            break
    return ExactArtifactPage(
        items=tuple(rows),
        next_offset=next_offset,
        has_more=artifact_id is None and next_offset < len(candidates),
    )


def iter_exact_artifact_sidecars(
    package: ResolvedReportPackage,
    artifact_type: str,
) -> tuple[tuple[AgentArtifactV2, Path, Path], ...]:
    candidates = _exact_artifact_candidates(package, artifact_type)
    resolved: list[tuple[AgentArtifactV2, Path, Path]] = []
    for candidate in candidates:
        artifact, _sidecar, html_path = candidate
        expected_hash = str(artifact.content_hash or "").removeprefix("sha256:").lower()
        if expected_hash:
            if _sha256(html_path) != expected_hash:
                continue
        elif not _readable_file(html_path):
            continue
        resolved.append(candidate)
    return tuple(resolved)


def baseline_analysis_artifact_id(
    package: ResolvedReportPackage,
    *,
    verify_content: bool = True,
) -> str | None:
    offset = 0
    while True:
        page = page_exact_artifact_sidecars(
            package,
            "analysis",
            offset=offset,
            limit=1,
            verify_content=verify_content,
        )
        for artifact, _sidecar, _html in page.items:
            if artifact.status in {"completed", "degraded"}:
                return artifact.artifact_id
        if not page.has_more:
            break
        offset = page.next_offset
    return None


__all__ = [
    "FACT_DIRECTORIES",
    "OUTPUT_DIRECTORY_NAMES",
    "ExactArtifactPage",
    "ResolvedCompany",
    "ResolvedReportPackage",
    "baseline_analysis_artifact_id",
    "company_key_for",
    "enumerate_companies",
    "enumerate_report_packages",
    "exact_artifact_bindings",
    "has_exact_artifact_metadata",
    "iter_exact_artifact_sidecars",
    "page_exact_artifact_sidecars",
    "resolve_company",
    "resolve_report_package",
    "resolve_report_package_from_context",
]
