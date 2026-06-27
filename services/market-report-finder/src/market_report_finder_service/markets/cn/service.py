from __future__ import annotations

from urllib.parse import urlparse

from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.cn.client import CninfoClient
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


class CnReportFinder(MarketReportFinder):
    market = Market.cn

    def __init__(self) -> None:
        self.client = CninfoClient()

    def source_descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="cninfo",
            source_name="巨潮资讯",
            markets=[Market.cn],
            official_domain="www.cninfo.com.cn",
            supports_targets=[
                ReportTarget.latest_report,
                ReportTarget.annual_report,
                ReportTarget.semiannual_report,
                ReportTarget.quarterly_report,
                ReportTarget.financial_report,
            ],
            supported_forms=["annual", "semiannual", "q1", "q3"],
            notes="Uses CNINFO topSearch/query and hisAnnouncement/query for A-share official periodic reports.",
        )

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        del cik
        return self.client.resolve_company(company_name=company_name, ticker=ticker, company_id=company_id)

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
        del include_amendments, report_year
        return self.client.list_filings(company, target=target, forms=forms, include_earnings=include_earnings)

    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        forms: list[str] = []
        for raw in report_types:
            text = raw.strip().lower().replace("_", "-")
            if text in {"annual", "annual-report"}:
                forms.append("annual")
            elif text in {"semiannual", "semi-annual", "interim", "half-year", "semiannual-report"}:
                forms.append("semiannual")
            elif text in {"q1", "first-quarter"}:
                forms.append("q1")
            elif text in {"q3", "third-quarter"}:
                forms.append("q3")
            elif text in {"quarterly", "quarterly-report", "financial"}:
                forms.extend(["q1", "q3"])
        return list(dict.fromkeys(forms))

    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        report_type = self._report_type_for_form(request.form)
        report_end = self.fallback_date(request.report_end)
        published_at = self.fallback_date(request.published_at or report_end)
        company_key = request.company_id or request.ticker or "manual"
        return FilingCandidate(
            source_id="cninfo",
            source_name="巨潮资讯",
            source_domain="www.cninfo.com.cn",
            market=Market.cn,
            company_id=str(company_key),
            ticker=request.ticker,
            company_name=request.company_name,
            report_type=report_type,
            report_family=self._family_for_report_type(report_type),
            form=request.form,
            title=request.title or f"{request.company_name} {request.form}",
            accession_number="manual",
            primary_document=self.primary_document_from_url(request.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=request.document_url,
            landing_url=request.landing_url or request.document_url,
            file_format=self.file_format_from_url(request.document_url, "pdf"),
        )

    def batch_candidate(
        self,
        item: BatchDownloadItem,
        *,
        default_company_name: str,
    ) -> FilingCandidate:
        company_name = item.company_name or default_company_name
        report_type = self._report_type_for_form(item.report_type or "annual")
        report_end = self.fallback_date(item.report_end)
        published_at = self.fallback_date(item.published_at or report_end)
        return FilingCandidate(
            source_id="cninfo",
            source_name="巨潮资讯",
            source_domain="www.cninfo.com.cn",
            market=Market.cn,
            company_id=item.company_id or item.ticker or "manual",
            ticker=item.ticker,
            company_name=company_name,
            report_type=report_type,
            report_family=self._family_for_report_type(report_type),
            form=item.report_type or report_type.value,
            title=item.title or f"{company_name} {report_type.value}",
            accession_number="manual",
            primary_document=self.primary_document_from_url(item.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=item.document_url,
            landing_url=item.landing_url or item.document_url,
            file_format=item.file_format or self.file_format_from_url(item.document_url, "pdf"),
        )

    @staticmethod
    def owns_url(document_url: str) -> bool:
        host = urlparse(document_url).netloc.lower()
        return "cninfo.com.cn" in host

    @staticmethod
    def _report_type_for_form(form: str) -> ReportType:
        normalized = form.strip().upper()
        mapping = {
            "ANNUAL": ReportType.annual,
            "SEMIANNUAL": ReportType.semiannual,
            "SEMI-ANNUAL": ReportType.semiannual,
            "INTERIM": ReportType.semiannual,
            "Q1": ReportType.q1,
            "Q3": ReportType.q3,
            "QUARTERLY": ReportType.quarterly,
        }
        return mapping.get(normalized, ReportType.annual)

    @staticmethod
    def _family_for_report_type(report_type: ReportType) -> ReportFamily:
        if report_type == ReportType.annual:
            return ReportFamily.annual
        if report_type == ReportType.semiannual:
            return ReportFamily.semiannual
        return ReportFamily.quarterly
