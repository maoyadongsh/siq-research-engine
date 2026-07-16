"""Currency-neutral financial facts and cross-format evidence references."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit

from .research_target import ContractValidationError, ResearchIdentity

NORMALIZED_FACT_SCHEMA_VERSION = "siq_normalized_fact_v1"
EVIDENCE_REF_SCHEMA_VERSION = "siq_evidence_ref_v1"


class EvidenceKind(str, Enum):
    PDF_PAGE = "pdf_page"
    PDF_TABLE = "pdf_table"
    HTML_SECTION = "html_section"
    HTML_ANCHOR = "html_anchor"
    XBRL_FACT = "xbrl_fact"
    MARKDOWN_LINE = "markdown_line"
    CHUNK = "chunk"
    SOURCE_URL = "source_url"


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _date(value: Any, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} must be an ISO date") from exc
    return text


@dataclass(frozen=True)
class EvidenceRefV1:
    research_identity: ResearchIdentity
    report_id: str
    kind: str
    source_url: str | None = None
    local_source_id: str | None = None
    pdf_task_id: str | None = None
    pdf_page: int | None = None
    table_id: str | None = None
    section_id: str | None = None
    html_anchor: str | None = None
    xpath: str | None = None
    xbrl_fact_id: str | None = None
    xbrl_concept: str | None = None
    xbrl_context: str | None = None
    xbrl_unit: str | None = None
    md_line: int | None = None
    chunk_index: int | None = None
    quote: str | None = None
    schema_version: str = EVIDENCE_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_REF_SCHEMA_VERSION:
            raise ContractValidationError(f"unsupported evidence ref schema: {self.schema_version}")
        if not isinstance(self.research_identity, ResearchIdentity):
            object.__setattr__(self, "research_identity", ResearchIdentity.from_dict(self.research_identity))
        report_id = _optional_text(self.report_id)
        if not report_id:
            raise ContractValidationError("evidence_ref.report_id is required")
        object.__setattr__(self, "report_id", report_id)
        try:
            kind = EvidenceKind(str(self.kind or "").strip().lower()).value
        except ValueError as exc:
            raise ContractValidationError("evidence_ref.kind is not supported") from exc
        object.__setattr__(self, "kind", kind)
        for name in (
            "source_url", "local_source_id", "pdf_task_id", "table_id", "section_id",
            "html_anchor", "xpath", "xbrl_fact_id", "xbrl_concept", "xbrl_context",
            "xbrl_unit", "quote",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name)))
        if self.local_source_id:
            logical_path = self.local_source_id.replace("\\", "/")
            if logical_path.startswith("/") or ".." in logical_path.split("/"):
                raise ContractValidationError("evidence_ref.local_source_id must be a safe logical identifier")
        if self.source_url:
            parsed_url = urlsplit(self.source_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ContractValidationError("evidence_ref.source_url must be an HTTP(S) URL")
        if self.quote and len(self.quote) > 2000:
            raise ContractValidationError("evidence_ref.quote is too long")
        for name in ("pdf_page", "md_line", "chunk_index"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool) or int(value) < 0:
                    raise ContractValidationError(f"evidence_ref.{name} must be non-negative")
                object.__setattr__(self, name, int(value))
        self._validate_locator()

    def _validate_locator(self) -> None:
        kind = EvidenceKind(self.kind)
        valid = {
            EvidenceKind.PDF_PAGE: self.pdf_page is not None and bool(self.pdf_task_id or self.local_source_id),
            EvidenceKind.PDF_TABLE: bool(self.table_id) and bool(self.pdf_task_id or self.local_source_id),
            EvidenceKind.HTML_SECTION: bool(self.section_id) and bool(self.source_url or self.local_source_id),
            EvidenceKind.HTML_ANCHOR: bool(self.html_anchor or self.xpath) and bool(self.source_url or self.local_source_id),
            EvidenceKind.XBRL_FACT: bool(self.xbrl_fact_id or (self.xbrl_concept and self.xbrl_context)),
            EvidenceKind.MARKDOWN_LINE: self.md_line is not None and bool(self.local_source_id),
            EvidenceKind.CHUNK: self.chunk_index is not None and bool(self.local_source_id),
            EvidenceKind.SOURCE_URL: bool(self.source_url),
        }[kind]
        if not valid:
            raise ContractValidationError(f"evidence_ref locator is incomplete for kind={kind.value}")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRefV1":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("evidence_ref must be an object")
        values = {name: payload.get(name) for name in cls.__dataclass_fields__}
        values["schema_version"] = payload.get("schema_version") or EVIDENCE_REF_SCHEMA_VERSION
        values["research_identity"] = ResearchIdentity.from_dict(payload.get("research_identity") or {})
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["research_identity"] = self.research_identity.to_dict()
        return payload


@dataclass(frozen=True)
class NormalizedFactV1:
    metric_key: str
    raw_label: str
    raw_value: Any
    normalized_value: int | float | None
    currency: str | None
    raw_unit: str | None
    scale: int | float
    period_start: str | None
    period_end: str
    accounting_standard: str | None
    research_identity: ResearchIdentity
    evidence_refs: tuple[EvidenceRefV1, ...] = field(default_factory=tuple)
    schema_version: str = NORMALIZED_FACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NORMALIZED_FACT_SCHEMA_VERSION:
            raise ContractValidationError(f"unsupported normalized fact schema: {self.schema_version}")
        for name in ("metric_key", "raw_label"):
            value = _optional_text(getattr(self, name))
            if not value:
                raise ContractValidationError(f"normalized_fact.{name} is required")
            object.__setattr__(self, name, value)
        if isinstance(self.raw_value, (Mapping, list, tuple, set)) or isinstance(self.raw_value, bool):
            raise ContractValidationError("normalized_fact.raw_value must be a scalar or null")
        if isinstance(self.normalized_value, bool) or (
            self.normalized_value is not None and not isinstance(self.normalized_value, (int, float))
        ):
            raise ContractValidationError("normalized_fact.normalized_value must be numeric or null")
        if isinstance(self.scale, bool) or not isinstance(self.scale, (int, float)) or self.scale <= 0:
            raise ContractValidationError("normalized_fact.scale must be positive")
        currency = _optional_text(self.currency)
        if currency is not None:
            currency = currency.upper()
            if len(currency) != 3 or not currency.isalpha():
                raise ContractValidationError("normalized_fact.currency must be a three-letter code")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "raw_unit", _optional_text(self.raw_unit))
        object.__setattr__(self, "period_start", _date(self.period_start, "normalized_fact.period_start"))
        period_end = _date(self.period_end, "normalized_fact.period_end")
        if period_end is None:
            raise ContractValidationError("normalized_fact.period_end is required")
        object.__setattr__(self, "period_end", period_end)
        if self.period_start and self.period_start > period_end:
            raise ContractValidationError("normalized_fact period_start must not follow period_end")
        object.__setattr__(self, "accounting_standard", _optional_text(self.accounting_standard))
        if not isinstance(self.research_identity, ResearchIdentity):
            object.__setattr__(self, "research_identity", ResearchIdentity.from_dict(self.research_identity))
        refs = tuple(
            item if isinstance(item, EvidenceRefV1) else EvidenceRefV1.from_dict(item)
            for item in self.evidence_refs
        )
        if any(not ref.research_identity.matches(self.research_identity) for ref in refs):
            raise ContractValidationError("normalized_fact evidence identity mismatch")
        object.__setattr__(self, "evidence_refs", refs)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NormalizedFactV1":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("normalized_fact must be an object")
        values = {name: payload.get(name) for name in cls.__dataclass_fields__}
        values["schema_version"] = payload.get("schema_version") or NORMALIZED_FACT_SCHEMA_VERSION
        values["research_identity"] = ResearchIdentity.from_dict(payload.get("research_identity") or {})
        values["evidence_refs"] = tuple(
            EvidenceRefV1.from_dict(item) for item in (payload.get("evidence_refs") or [])
        )
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["research_identity"] = self.research_identity.to_dict()
        payload["evidence_refs"] = [item.to_dict() for item in self.evidence_refs]
        return payload


__all__ = [
    "EVIDENCE_REF_SCHEMA_VERSION",
    "NORMALIZED_FACT_SCHEMA_VERSION",
    "EvidenceKind",
    "EvidenceRefV1",
    "NormalizedFactV1",
]
