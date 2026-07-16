"""Versioned, path-free research target contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

RESEARCH_TARGET_SCHEMA_VERSION = "siq_research_target_v1"
RESEARCH_MARKETS = frozenset({"CN", "HK", "US", "EU", "KR", "JP"})


class ContractValidationError(ValueError):
    """Raised when a shared market contract is incomplete or inconsistent."""


class SourceFamily(str, Enum):
    PDF_MARKET = "pdf_market"
    SEC_IXBRL = "sec_ixbrl"
    ESEF_IXBRL = "esef_ixbrl"


class DocumentFormat(str, Enum):
    PDF = "pdf"
    HTML = "html"
    IXBRL_HTML = "ixbrl_html"
    MARKDOWN = "markdown"
    JSON = "json"
    UNKNOWN = "unknown"


class QualityStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    UNKNOWN = "unknown"


def _text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    text = str(value or "").strip()
    if required and not text:
        raise ContractValidationError(f"{field} is required")
    return text or None


def _enum_value(enum_type: type[Enum], value: Any, field: str) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "").strip().lower()
    try:
        return str(enum_type(raw).value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise ContractValidationError(f"{field} must be one of: {allowed}") from exc


def _iso_date(value: Any, field: str) -> str | None:
    text = _text(value, field, required=False)
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ContractValidationError(f"{field} must be an ISO date") from exc
    return text


@dataclass(frozen=True)
class ResearchIdentity:
    market: str
    company_id: str
    filing_id: str
    parse_run_id: str

    def __post_init__(self) -> None:
        market = str(self.market or "").strip().upper().replace("-", "_")
        if market == "US_SEC":
            market = "US"
        if market not in RESEARCH_MARKETS:
            raise ContractValidationError("research_identity.market is not supported")
        object.__setattr__(self, "market", market)
        for field in ("company_id", "filing_id", "parse_run_id"):
            value = _text(getattr(self, field), f"research_identity.{field}")
            upper = value.upper()
            if upper.startswith("US_SEC:") or upper.startswith("US-SEC:"):
                value = f"US:{value.split(':', 1)[1]}"
            object.__setattr__(self, field, value)

        for field in ("company_id", "filing_id"):
            prefix = str(getattr(self, field)).split(":", 1)[0].upper()
            if prefix in RESEARCH_MARKETS and prefix != market:
                raise ContractValidationError(f"research_identity.{field} conflicts with market")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchIdentity":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("research_identity must be an object")
        return cls(
            market=payload.get("market", ""),
            company_id=payload.get("company_id", ""),
            filing_id=payload.get("filing_id", ""),
            parse_run_id=payload.get("parse_run_id", ""),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def matches(self, other: "ResearchIdentity") -> bool:
        return self.to_dict() == other.to_dict()


@dataclass(frozen=True)
class SourceReportV1:
    report_id: str
    source_family: str
    document_format: str
    report_type: str
    form_type: str | None = None
    fiscal_year: int | None = None
    period_end: str | None = None
    published_at: str | None = None
    accounting_standard: str | None = None
    reporting_currency: str | None = None
    quality_status: str = QualityStatus.UNKNOWN.value

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _text(self.report_id, "source_report.report_id"))
        object.__setattr__(self, "source_family", _enum_value(SourceFamily, self.source_family, "source_report.source_family"))
        object.__setattr__(self, "document_format", _enum_value(DocumentFormat, self.document_format, "source_report.document_format"))
        object.__setattr__(self, "report_type", _text(self.report_type, "source_report.report_type"))
        object.__setattr__(self, "form_type", _text(self.form_type, "source_report.form_type", required=False))
        if self.fiscal_year is not None:
            if isinstance(self.fiscal_year, bool) or not 1900 <= int(self.fiscal_year) <= 2200:
                raise ContractValidationError("source_report.fiscal_year is invalid")
            object.__setattr__(self, "fiscal_year", int(self.fiscal_year))
        object.__setattr__(self, "period_end", _iso_date(self.period_end, "source_report.period_end"))
        object.__setattr__(self, "published_at", _iso_date(self.published_at, "source_report.published_at"))
        object.__setattr__(
            self,
            "accounting_standard",
            _text(self.accounting_standard, "source_report.accounting_standard", required=False),
        )
        currency = _text(self.reporting_currency, "source_report.reporting_currency", required=False)
        if currency is not None:
            currency = currency.upper()
            if len(currency) != 3 or not currency.isalpha():
                raise ContractValidationError("source_report.reporting_currency must be a three-letter code")
        object.__setattr__(self, "reporting_currency", currency)
        object.__setattr__(self, "quality_status", _enum_value(QualityStatus, self.quality_status, "source_report.quality_status"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceReportV1":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("source_report must be an object")
        return cls(
            report_id=payload.get("report_id", ""),
            source_family=payload.get("source_family", ""),
            document_format=payload.get("document_format", "unknown"),
            report_type=payload.get("report_type", "unknown"),
            form_type=payload.get("form_type"),
            fiscal_year=payload.get("fiscal_year"),
            period_end=payload.get("period_end"),
            published_at=payload.get("published_at"),
            accounting_standard=payload.get("accounting_standard"),
            reporting_currency=payload.get("reporting_currency"),
            quality_status=payload.get("quality_status") or QualityStatus.UNKNOWN.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchTargetV1:
    company_key: str
    company_wiki_id: str
    display_code: str
    display_name: str
    research_identity: ResearchIdentity
    source_report: SourceReportV1
    schema_version: str = RESEARCH_TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RESEARCH_TARGET_SCHEMA_VERSION:
            raise ContractValidationError(f"unsupported research target schema: {self.schema_version}")
        for field in ("company_key", "company_wiki_id", "display_code", "display_name"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if not isinstance(self.research_identity, ResearchIdentity):
            object.__setattr__(self, "research_identity", ResearchIdentity.from_dict(self.research_identity))
        if not isinstance(self.source_report, SourceReportV1):
            object.__setattr__(self, "source_report", SourceReportV1.from_dict(self.source_report))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchTargetV1":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("research target must be an object")
        return cls(
            schema_version=payload.get("schema_version", RESEARCH_TARGET_SCHEMA_VERSION),
            company_key=payload.get("company_key", ""),
            company_wiki_id=payload.get("company_wiki_id", ""),
            display_code=payload.get("display_code", ""),
            display_name=payload.get("display_name", ""),
            research_identity=ResearchIdentity.from_dict(payload.get("research_identity") or {}),
            source_report=SourceReportV1.from_dict(payload.get("source_report") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "company_key": self.company_key,
            "company_wiki_id": self.company_wiki_id,
            "display_code": self.display_code,
            "display_name": self.display_name,
            "research_identity": self.research_identity.to_dict(),
            "source_report": self.source_report.to_dict(),
        }


__all__ = [
    "ContractValidationError",
    "DocumentFormat",
    "QualityStatus",
    "RESEARCH_MARKETS",
    "RESEARCH_TARGET_SCHEMA_VERSION",
    "ResearchIdentity",
    "ResearchTargetV1",
    "SourceFamily",
    "SourceReportV1",
]
