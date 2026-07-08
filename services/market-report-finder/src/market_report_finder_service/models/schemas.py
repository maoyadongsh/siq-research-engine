from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Market(str, Enum):
    cn = "CN"
    us = "US"
    hk = "HK"
    eu = "EU"
    kr = "KR"
    jp = "JP"


class ReportTarget(str, Enum):
    latest_report = "latest_report"
    annual_report = "annual_report"
    semiannual_report = "semiannual_report"
    quarterly_report = "quarterly_report"
    financial_report = "financial_report"


class ReportType(str, Enum):
    form_10k = "10-K"
    form_10q = "10-Q"
    form_20f = "20-F"
    form_6k = "6-K"
    annual = "annual"
    semiannual = "semiannual"
    quarterly = "quarterly"
    q1 = "q1"
    q3 = "q3"
    earnings = "earnings_release"


class ReportFamily(str, Enum):
    annual = "annual"
    semiannual = "semiannual"
    quarterly = "quarterly"
    current = "current"


class CompanyEntity(BaseModel):
    market: Market
    company_id: str
    ticker: str | None = None
    company_name: str
    exchange: str | None = None
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    match_reason: str = "exact"
    cik: str | None = None
    cik_padded: str | None = None
    hkex_stock_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FilingCandidate(BaseModel):
    source_id: str
    source_name: str
    source_domain: str
    market: Market
    company_id: str
    ticker: str | None = None
    company_name: str
    report_type: ReportType
    report_family: ReportFamily
    form: str
    title: str
    accession_number: str | None = None
    primary_document: str | None = None
    report_end: date
    published_at: date
    accepted_at: datetime | None = None
    document_url: str
    landing_url: str
    file_format: str = "pdf"
    language: str | None = None
    inline_xbrl: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FilingCandidateSnapshot(BaseModel):
    title: str
    form: str
    report_type: ReportType
    report_family: ReportFamily
    report_end: date
    published_at: date
    document_url: str
    landing_url: str


class SourceDescriptor(BaseModel):
    source_id: str
    source_name: str
    markets: list[Market]
    official_domain: str
    official_sources: list[dict[str, str]] = Field(default_factory=list)
    supports_targets: list[ReportTarget]
    supported_forms: list[str]
    notes: str


class SourceCatalogResponse(BaseModel):
    sources: list[SourceDescriptor]


class ReportAssistIntent(BaseModel):
    market: Market | None = None
    company_query: str | None = None
    ticker: str | None = None
    company_id: str | None = None
    cik: str | None = None
    report_year: int | None = Field(default=None, ge=1900, le=2100)
    report_types: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ReportAssistCandidate(BaseModel):
    document_url: str = Field(min_length=1, max_length=2000)
    title: str
    report_type: str | None = None
    report_family: str | None = None
    form: str | None = None
    report_end: date | None = None
    published_at: date | None = None
    landing_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportAssistCandidateExplanation(BaseModel):
    document_url: str
    title_zh: str
    report_type_zh: str
    period_zh: str
    recommendation: str
    recommended: bool = False
    warnings: list[str] = Field(default_factory=list)


class ReportAssistRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=1000)
    market: Market | None = None
    company_name: str | None = Field(default=None, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)
    report_year: int | None = Field(default=None, ge=1900, le=2100)
    report_types: list[str] = Field(default_factory=list, max_length=16)
    candidates: list[ReportAssistCandidate] = Field(default_factory=list, max_length=100)


class ReportAssistResponse(BaseModel):
    intent: ReportAssistIntent
    candidate_explanations: list[ReportAssistCandidateExplanation] = Field(default_factory=list)
    assistant_mode: str = "rules"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResolveCompanyRequest(BaseModel):
    market: Market | None = None
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def require_any_identifier(self):
        if not self.company_name and not self.ticker and not self.company_id and not self.cik:
            raise ValueError("company_name, ticker, company_id, or cik is required")
        return self


class LegacyResolveCompanyRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    exchange_hint: str | None = Field(default=None, max_length=32)


class ResolveCompanyResponse(BaseModel):
    query: str
    market: Market
    resolved: CompanyEntity
    candidates: list[CompanyEntity] = Field(default_factory=list)
    candidate_count: int
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecentReportsRequest(BaseModel):
    market: Market | None = None
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)
    target: ReportTarget = ReportTarget.financial_report
    report_year: int | None = Field(default=None, ge=1900, le=2100)
    forms: list[str] = Field(default_factory=list, max_length=16)
    include_amendments: bool = False
    include_earnings: bool = False
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_any_identifier(self):
        if not self.company_name and not self.ticker and not self.company_id and not self.cik:
            raise ValueError("company_name, ticker, company_id, or cik is required")
        return self


class RecentReportsResponse(BaseModel):
    query: str
    market: Market
    target: ReportTarget
    resolved: CompanyEntity
    report_year: int | None = None
    candidates_total: int
    reports: list[FilingCandidate] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ranking_rule: str


class LatestReportRequest(BaseModel):
    market: Market | None = None
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)
    target: ReportTarget = ReportTarget.annual_report
    forms: list[str] = Field(default_factory=list, max_length=16)
    include_amendments: bool = False
    include_earnings: bool = False

    @model_validator(mode="after")
    def require_any_identifier(self):
        if not self.company_name and not self.ticker and not self.company_id and not self.cik:
            raise ValueError("company_name, ticker, company_id, or cik is required")
        return self


class LatestReportResponse(BaseModel):
    query: str
    market: Market
    target: ReportTarget
    resolved: CompanyEntity
    selected: FilingCandidate
    candidates_considered: int
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DownloadedReportFile(BaseModel):
    file_name: str
    saved_path: str
    size_bytes: int
    content_type: str | None = None
    downloaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    deduplicated: bool = False
    content_sha256: str | None = None
    metadata_path: str | None = None


class SelectiveDownloadRequest(BaseModel):
    market: Market | None = None
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)
    report_types: list[str] = Field(default_factory=list, max_length=16)
    forms: list[str] = Field(default_factory=list, max_length=16)
    reports: list[FilingCandidate] = Field(default_factory=list, max_length=20)
    report_year: int | None = Field(default=None, ge=1900, le=2100)
    include_amendments: bool = False
    include_earnings: bool = False

    @model_validator(mode="after")
    def require_any_identifier(self):
        if not self.company_name and not self.ticker and not self.company_id and not self.cik:
            raise ValueError("company_name, ticker, company_id, or cik is required")
        return self


class SelectiveDownloadResultItem(BaseModel):
    title: str
    form: str
    report_type: ReportType
    report_family: ReportFamily
    report_end: date
    published_at: date
    accession_number: str | None = None
    document_url: str
    file_name: str
    saved_path: str
    size_bytes: int
    cache_hit: bool
    error: str | None = None


class SelectiveDownloadResponse(BaseModel):
    market: Market
    company_name: str
    ticker: str | None
    company_id: str
    total: int
    succeeded: int
    failed: int
    files: list[SelectiveDownloadResultItem]
    download_dir: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BatchDownloadItem(BaseModel):
    document_url: str = Field(min_length=1, max_length=2000)
    company_name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=500)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    market: Market | None = None
    report_type: str | None = Field(default=None, max_length=64)
    report_end: date | None = None
    published_at: date | None = None
    landing_url: str | None = Field(default=None, max_length=2000)
    file_format: str | None = Field(default=None, max_length=16)


class BatchDownloadRequest(BaseModel):
    items: list[BatchDownloadItem] = Field(min_length=1, max_length=50)
    default_company_name: str = Field(default="unknown", max_length=200)
    market: Market | None = None


class BatchDownloadResultItem(BaseModel):
    document_url: str
    company_name: str
    file_name: str
    size_bytes: int
    success: bool
    saved_path: str = ""
    error: str | None = None


class BatchDownloadResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BatchDownloadResultItem]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DirectReportDownloadRequest(BaseModel):
    market: Market | None = None
    company_name: str = Field(min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    company_id: str | None = Field(default=None, max_length=32)
    cik: str | None = Field(default=None, max_length=16)
    document_url: str = Field(min_length=1, max_length=2000)
    landing_url: str | None = Field(default=None, max_length=2000)
    form: str = "10-K"
    title: str | None = Field(default=None, max_length=500)
    report_end: date | None = None
    published_at: date | None = None


class SingleDownloadRequest(BaseModel):
    market: Market | None = None
    company_name: str = Field(min_length=1, max_length=200)
    document_url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    ticker: str | None = Field(default=None, max_length=32)


class DirectReportDownloadResponse(BaseModel):
    market: Market
    company_name: str
    ticker: str | None
    company_id: str
    document_url: str
    landing_url: str
    form: str
    downloaded_file: DownloadedReportFile
