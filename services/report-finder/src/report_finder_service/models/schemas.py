from datetime import date, datetime, timezone
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class ReportTarget(str, Enum):
    latest_report = "latest_report"
    annual_report = "annual_report"
    financial_report = "financial_report"


class Market(str, Enum):
    cn = "CN"
    hk = "HK"
    us = "US"


class ReportType(str, Enum):
    annual = "annual"
    semiannual = "semiannual"
    q1 = "q1"
    q3 = "q3"
    form_10k = "10-K"
    form_20f = "20-F"
    form_10q = "10-Q"
    form_6k = "6-K"
    earnings = "earnings_release"


class CompanyEntity(BaseModel):
    canonical_name: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    market: Market
    exchange: str
    ticker: str
    cik: str | None = None
    confidence: float
    match_reason: str


class ReportCandidate(BaseModel):
    source_id: str
    source_name: str
    source_domain: str
    company_name: str
    ticker: str
    market: Market
    report_type: ReportType
    title: str
    report_end: date
    published_at: date
    language: str = "zh-CN"
    document_url: str
    landing_url: str
    file_format: str = "pdf"
    selection_reason: str | None = None


class ReportCandidateSnapshot(BaseModel):
    title: str
    report_type: ReportType
    report_end: date
    published_at: date
    document_url: str
    landing_url: str


class RecentReportsRequest(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    exchange_hint: str | None = Field(default=None, max_length=32)
    target: ReportTarget = ReportTarget.financial_report
    report_year: int | None = Field(default=None, ge=1900, le=2100)
    include_earnings: bool = False
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_company_or_ticker(self):
        if not self.company_name and not self.ticker:
            raise ValueError("company_name 或 ticker 至少提供一个")
        return self


class RecentReportsResponse(BaseModel):
    query: str
    target: ReportTarget
    resolved: CompanyEntity
    report_year: int | None = None
    candidates_total: int
    reports: list[ReportCandidate] = Field(default_factory=list)
    checked_at: datetime
    ranking_rule: str


class SelectionEvidence(BaseModel):
    checked_at: datetime
    target_scope: ReportTarget
    ranking_rule: str
    filtered_candidates_count: int
    top_candidates: list[ReportCandidateSnapshot] = Field(default_factory=list)
    selected_is_latest_by_report_end: bool
    selected_is_latest_by_published_at: bool


class ResolveCompanyRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    exchange_hint: str | None = Field(default=None, max_length=32)


class ResolveCompanyResponse(BaseModel):
    query: str
    resolved: CompanyEntity
    candidates: list[CompanyEntity] = Field(default_factory=list)
    candidate_count: int = 0


class LatestReportRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    exchange_hint: str | None = Field(default=None, max_length=32)
    target: ReportTarget = ReportTarget.annual_report


class LatestReportResponse(BaseModel):
    query: str
    target: ReportTarget
    resolved: CompanyEntity
    selected: ReportCandidate
    candidates_considered: int
    selection_evidence: SelectionEvidence


class DownloadedReportFile(BaseModel):
    file_name: str
    saved_path: str
    size_bytes: int
    content_type: str | None = None
    downloaded_at: datetime
    cache_hit: bool = False
    deduplicated: bool = False
    content_sha256: str | None = None


class LatestReportDownloadRequest(LatestReportRequest):
    pass


class LatestReportDownloadResponse(LatestReportResponse):
    downloaded_file: DownloadedReportFile


class DirectReportDownloadRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    document_url: str = Field(min_length=1, max_length=2000)
    landing_url: str | None = Field(default=None, max_length=2000)
    source_name: str = Field(default="manual_official", max_length=128)
    report_type: ReportType = ReportType.annual
    report_end: date | None = None
    published_at: date | None = None


class DirectReportDownloadResponse(BaseModel):
    company_name: str
    source_name: str
    source_domain: str
    document_url: str
    landing_url: str | None = None
    report_type: ReportType
    report_end: date
    published_at: date
    downloaded_file: DownloadedReportFile


class SingleDownloadRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    document_url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=500)


class BatchDownloadItem(BaseModel):
    document_url: str = Field(min_length=1, max_length=2000)
    company_name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=500)
    ticker: str | None = Field(default=None, max_length=32)
    report_type: ReportType | None = None
    report_end: date | None = None
    published_at: date | None = None


class BatchDownloadRequest(BaseModel):
    items: list[BatchDownloadItem] = Field(min_length=1, max_length=20)
    default_company_name: str = Field(default="unknown", max_length=200)


class BatchDownloadResultItem(BaseModel):
    document_url: str
    company_name: str
    file_name: str
    size_bytes: int
    success: bool
    error: str | None = None


class BatchDownloadResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BatchDownloadResultItem]
    zip_file_name: str
    checked_at: datetime


class SelectiveDownloadRequest(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    ticker: str | None = Field(default=None, max_length=32)
    exchange_hint: str | None = Field(default=None, max_length=32)
    report_types: list[str] = Field(default_factory=list)
    reports: list[ReportCandidate] = Field(default_factory=list, max_length=20)
    report_year: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def require_company_or_ticker(self):
        if not self.company_name and not self.ticker:
            raise ValueError("company_name 或 ticker 至少提供一个")
        return self

    @model_validator(mode="after")
    def validate_report_types(self):
        valid = {"annual", "semiannual", "q1", "q3"}
        for rt in self.report_types:
            if rt not in valid:
                raise ValueError(f"report_types 只支持: {valid}")
        return self


class SelectiveDownloadResultItem(BaseModel):
    title: str
    report_type: str
    report_end: date
    published_at: date
    document_url: str
    file_name: str
    saved_path: str
    size_bytes: int
    cache_hit: bool


class SelectiveDownloadResponse(BaseModel):
    company_name: str
    ticker: str
    total: int
    succeeded: int
    failed: int
    files: list[SelectiveDownloadResultItem]
    download_dir: str
    checked_at: datetime


class SourceDescriptor(BaseModel):
    source_id: str
    source_name: str
    markets: list[Market]
    official_domain: str
    notes: str
    supports_targets: list[ReportTarget] = Field(default_factory=list)
    data_scope: list[str] = Field(default_factory=list)
    implementation_status: str = "prototype"


class SourceCatalogResponse(BaseModel):
    sources: list[SourceDescriptor]


class HealthResponse(BaseModel):
    status: str
    service: str
