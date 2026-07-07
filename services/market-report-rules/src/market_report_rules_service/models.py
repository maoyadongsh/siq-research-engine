from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Market(StrEnum):
    CN = "CN"
    US = "US"
    HK = "HK"
    JP = "JP"
    KR = "KR"
    EU = "EU"


class AccountingStandard(StrEnum):
    US_GAAP = "US_GAAP"
    IFRS = "IFRS"
    HKFRS = "HKFRS"
    CASBE = "CASBE"
    JGAAP = "JGAAP"
    KIFRS = "KIFRS"
    UNKNOWN = "UNKNOWN"


class StatementType(StrEnum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW_STATEMENT = "cash_flow_statement"
    KEY_METRICS = "key_metrics"
    OPERATING_METRICS = "operating_metrics"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIPPED = "skipped"


class RuleProfile(BaseModel):
    market: Market
    profile_id: str
    rule_version: str
    accounting_standards: list[AccountingStandard]
    report_forms: list[str]
    preferred_artifacts: list[str]
    notes: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    source_type: str
    source_id: str | None = None
    page_number: int | None = None
    section: str | None = None
    anchor: str | None = None
    xpath: str | None = None
    html_snippet: str | None = None
    rendered_page_number: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    bbox: list[float] | None = None
    xbrl_tag: str | None = None
    accession_number: str | None = None
    url: str | None = None
    path: str | None = None
    quote_text: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ParsedFact(BaseModel):
    concept: str
    value: Decimal
    unit: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    duration_days: int | None = None
    filed_at: date | None = None
    form: str | None = None
    frame: str | None = None
    context_id: str | None = None
    accession_number: str | None = None
    decimals: int | None = None
    label: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("concept", mode="before")
    @classmethod
    def normalize_concept(cls, value: Any) -> str:
        return str(value or "").strip()


class ParsedTable(BaseModel):
    table_id: str
    title: str | None = None
    rows: list[list[Any]]
    page_number: int | None = None
    table_index: int | None = None
    unit: str | None = None
    currency: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ParsedArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_id: str
    market: Market
    company_id: str
    ticker: str
    company_name: str | None = None
    report_id: str | None = None
    report_type: str | None = None
    report_form: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    period_end: date | None = None
    accounting_standard: AccountingStandard = AccountingStandard.UNKNOWN
    industry_profile: str = "general"
    company_overrides: dict[str, Any] = Field(default_factory=dict)
    currency: str | None = None
    unit: str | None = None
    source_url: str | None = None
    source_files: dict[str, Any] = Field(default_factory=dict)
    facts: list[ParsedFact] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    document_full: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedFact(BaseModel):
    canonical_name: str
    local_name: str
    label: str | None = None
    statement_type: StatementType
    value: Decimal
    raw_value: str | None = None
    unit: str | None = None
    currency: str | None = None
    period_key: str
    period_start: date | None = None
    period_end: date | None = None
    duration_days: int | None = None
    frame: str | None = None
    qtd_ytd_type: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    scale: Decimal = Decimal("1")
    market: Market
    accounting_standard: AccountingStandard
    taxonomy: str | None = None
    is_extension: bool = False
    gaap_status: str = "reported_gaap"
    source_accession: str | None = None
    confidence: Decimal = Decimal("0.80")
    evidence: EvidenceRef
    raw: dict[str, Any] = Field(default_factory=dict)


class FinancialStatement(BaseModel):
    statement_id: str
    statement_type: StatementType
    statement_name: str
    scope: str = "consolidated"
    scope_name: str | None = None
    title: str | None = None
    unit: str | None = None
    scale: Decimal = Decimal("1")
    currency: str | None = None
    table_indexes: list[int] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    items: list[ExtractedFact] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    schema_version: int = 1
    rule_version: str
    profile_id: str
    artifact_id: str
    market: Market
    accounting_standard: AccountingStandard
    industry_profile: str = "general"
    company_overrides: dict[str, Any] = Field(default_factory=dict)
    company_id: str
    ticker: str
    company_name: str | None = None
    report_id: str | None = None
    report_type: str | None = None
    report_form: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    period_end: date | None = None
    statements: list[FinancialStatement]
    key_metrics: list[ExtractedFact] = Field(default_factory=list)
    operating_metrics: list[ExtractedFact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ValidationCheck(BaseModel):
    rule_id: str
    rule_name: str
    statement_type: StatementType | Literal["document", "cross"]
    scope: str = "consolidated"
    period: str | None = None
    status: CheckStatus
    diff: Decimal | None = None
    tolerance: Decimal | None = None
    inputs: list[str] = Field(default_factory=list)
    left: dict[str, Any] = Field(default_factory=dict)
    right: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    schema_version: int = 1
    rule_version: str
    profile_id: str
    artifact_id: str
    market: Market
    industry_profile: str = "general"
    overall_status: CheckStatus
    summary: dict[str, int]
    checks: list[ValidationCheck]
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LoadPlanRow(BaseModel):
    table: str
    operation: Literal["insert", "upsert", "delete_then_insert"] = "insert"
    row: dict[str, Any]


class PromotionDecision(BaseModel):
    target: str
    promotion_target: str
    decision: Literal["allow", "review", "block"] = "allow"
    severity: Literal["observe", "soft", "hard"] = "observe"
    rule_ids: list[str] = Field(default_factory=list)
    review_rule_ids: list[str] = Field(default_factory=list)
    blocking_rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class DbLoadPlan(BaseModel):
    schema_version: int = 1
    target_database: str
    target_schema: str = "market_rules"
    wiki_namespace: str
    file_layout: dict[str, Any] = Field(default_factory=dict)
    agent_policy: str = "market_specific_agents_only"
    compatible_pdf2md_tables: list[str] = Field(default_factory=list)
    artifact_id: str
    market: Market
    company_id: str
    ticker: str
    report_id: str | None = None
    parse_run_id: str
    filing_id: str
    can_import: bool = True
    can_vector_ingest: bool = True
    promotion_decisions: dict[str, PromotionDecision] = Field(default_factory=dict)
    blocked_reasons: list[str] = Field(default_factory=list)
    rows: list[LoadPlanRow]
    quarantine_rows: list[LoadPlanRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProcessRequest(BaseModel):
    artifact: ParsedArtifact
    build_load_plan: bool = True
    package_dir: str | None = None


class ProcessResult(BaseModel):
    extraction: ExtractionResult
    validation: ValidationResult
    load_plan: DbLoadPlan | None = None
