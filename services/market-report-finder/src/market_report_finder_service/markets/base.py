from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from market_report_finder_service.markets.url_ownership import (
    MANUAL_UNVERIFIED_SOURCE_ID,
    MANUAL_UNVERIFIED_SOURCE_NAME,
    MANUAL_UNVERIFIED_STATUS,
    OFFICIAL_VERIFIED_STATUS,
    market_owns_url,
    validate_http_url,
)
from market_report_finder_service.models.schemas import (
    BatchDownloadItem,
    CompanyEntity,
    DirectReportDownloadRequest,
    FilingCandidate,
    Market,
    ReportFamily,
    ReportTarget,
    ReportType,
    SourceDescriptor,
)


class MarketReportFinder(ABC):
    market: Market

    @abstractmethod
    def source_descriptor(self) -> SourceDescriptor:
        raise NotImplementedError

    @abstractmethod
    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        raise NotImplementedError

    @abstractmethod
    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget,
        forms: list[str],
        include_amendments: bool,
        include_earnings: bool,
        report_year: int | None = None,
    ) -> list[FilingCandidate]:
        raise NotImplementedError

    @abstractmethod
    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        raise NotImplementedError

    @abstractmethod
    def batch_candidate(
        self,
        item: BatchDownloadItem,
        *,
        default_company_name: str,
    ) -> FilingCandidate:
        raise NotImplementedError

    def mark_user_url_candidate(
        self,
        candidate: FilingCandidate,
        *,
        original_url: str,
        input_kind: str,
    ) -> FilingCandidate:
        host = validate_http_url(original_url)
        metadata = dict(candidate.metadata)
        metadata.setdefault("original_url", original_url)
        metadata["source_verification_input"] = input_kind
        if market_owns_url(candidate.market, original_url):
            metadata.setdefault("source_verification_status", OFFICIAL_VERIFIED_STATUS)
            return candidate.model_copy(update={"metadata": metadata})

        metadata["source_verification_status"] = MANUAL_UNVERIFIED_STATUS
        metadata.setdefault("original_source_id", candidate.source_id)
        metadata.setdefault("original_source_name", candidate.source_name)
        metadata.setdefault("original_source_domain", candidate.source_domain)
        metadata["source_tier"] = MANUAL_UNVERIFIED_STATUS
        return candidate.model_copy(
            update={
                "source_id": MANUAL_UNVERIFIED_SOURCE_ID,
                "source_name": MANUAL_UNVERIFIED_SOURCE_NAME,
                "source_domain": host,
                "metadata": metadata,
            }
        )

    @staticmethod
    def target_for_forms_or_types(forms: list[str], report_types: list[str]) -> ReportTarget:
        joined = {item.strip().lower().replace("_", "-") for item in [*forms, *report_types]}
        if joined and joined <= {"10-k", "20-f", "annual", "annual-report"}:
            return ReportTarget.annual_report
        if joined and joined <= {"semiannual", "semi-annual", "interim", "half-year", "semiannual-report"}:
            return ReportTarget.semiannual_report
        if joined and joined <= {"10-q", "6-k", "quarterly", "quarterly-report", "q1", "q2", "q3", "q4"}:
            return ReportTarget.quarterly_report
        return ReportTarget.financial_report

    @staticmethod
    def quarter_months_from_report_types(report_types: list[str]) -> set[int]:
        mapping = {
            "q1": 3,
            "q2": 6,
            "q3": 9,
            "q4": 12,
        }
        return {mapping[key] for raw in report_types if (key := raw.strip().lower()) in mapping}

    @staticmethod
    def family_for_report_type(report_type: ReportType) -> ReportFamily:
        if report_type in {ReportType.form_10k, ReportType.form_20f, ReportType.annual}:
            return ReportFamily.annual
        if report_type == ReportType.semiannual:
            return ReportFamily.semiannual
        if report_type in {ReportType.form_10q, ReportType.form_6k, ReportType.quarterly, ReportType.q1, ReportType.q3}:
            return ReportFamily.quarterly
        return ReportFamily.current

    @staticmethod
    def fallback_date(value):
        return value or datetime.now(timezone.utc).date()

    @staticmethod
    def primary_document_from_url(document_url: str) -> str:
        return Path(urlparse(document_url).path).name or "manual"

    @staticmethod
    def file_format_from_url(document_url: str, default: str) -> str:
        suffix = Path(urlparse(document_url).path).suffix.lstrip(".")
        if suffix == "htm":
            return "html"
        return suffix or default
