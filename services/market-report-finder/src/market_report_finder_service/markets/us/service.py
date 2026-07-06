from __future__ import annotations

from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.us.client import SecClient
from market_report_finder_service.markets.url_ownership import market_owns_url
from market_report_finder_service.models.schemas import (
    BatchDownloadItem,
    CompanyEntity,
    DirectReportDownloadRequest,
    FilingCandidate,
    Market,
    ReportTarget,
    ReportType,
    SourceDescriptor,
)


class UsReportFinder(MarketReportFinder):
    market = Market.us

    def __init__(self) -> None:
        self.client = SecClient()

    def source_descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="sec",
            source_name="SEC EDGAR",
            markets=[Market.us],
            official_domain="sec.gov",
            supports_targets=[
                ReportTarget.latest_report,
                ReportTarget.annual_report,
                ReportTarget.quarterly_report,
                ReportTarget.financial_report,
            ],
            supported_forms=["10-K", "10-Q", "20-F", "6-K"],
            notes="Uses SEC company_tickers.json, data.sec.gov submissions, and SEC Archives primary documents.",
        )

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        return self.client.resolve_company(company_name=company_name, ticker=ticker or company_id, cik=cik or company_id)

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
        del include_earnings, report_year
        return self.client.list_filings(company, target=target, forms=forms, include_amendments=include_amendments)

    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        forms: list[str] = []
        for raw in report_types:
            text = raw.strip().lower().replace("_", "-")
            if text in {"annual", "annual-report"}:
                forms.extend(["10-K", "20-F"])
            elif text in {"quarterly", "quarterly-report", "financial", "q1", "q2", "q3", "q4"}:
                forms.extend(["10-Q", "6-K"])
            elif text.upper() in {"10-K", "10-Q", "20-F", "6-K"}:
                forms.append(text.upper())
        return list(dict.fromkeys(forms))

    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        report_type = self._report_type_for_form(request.form)
        report_end = self.fallback_date(request.report_end)
        published_at = self.fallback_date(request.published_at or report_end)
        company_key = request.company_id or request.cik or request.ticker or "manual"
        candidate = FilingCandidate(
            source_id="sec",
            source_name="SEC EDGAR",
            source_domain="sec.gov",
            market=Market.us,
            company_id=str(company_key),
            ticker=request.ticker,
            cik=request.cik or request.company_id,
            company_name=request.company_name,
            report_type=report_type,
            report_family=self.family_for_report_type(report_type),
            form=request.form,
            title=request.title or f"{request.company_name} {request.form}",
            accession_number="manual",
            primary_document=self.primary_document_from_url(request.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=request.document_url,
            landing_url=request.landing_url or request.document_url,
            file_format=self.file_format_from_url(request.document_url, "html"),
        )
        return self.mark_user_url_candidate(candidate, original_url=request.document_url, input_kind="direct_download")

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
        candidate = FilingCandidate(
            source_id="sec",
            source_name="SEC EDGAR",
            source_domain="sec.gov",
            market=Market.us,
            company_id=item.company_id or item.ticker or "manual",
            ticker=item.ticker,
            company_name=company_name,
            report_type=report_type,
            report_family=self.family_for_report_type(report_type),
            form=item.report_type or report_type.value,
            title=item.title or f"{company_name} {report_type.value}",
            accession_number="manual",
            primary_document=self.primary_document_from_url(item.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=item.document_url,
            landing_url=item.landing_url or item.document_url,
            file_format=item.file_format or self.file_format_from_url(item.document_url, "html"),
        )
        return self.mark_user_url_candidate(candidate, original_url=item.document_url, input_kind="batch_download")

    @staticmethod
    def owns_url(document_url: str) -> bool:
        return market_owns_url(Market.us, document_url)

    @staticmethod
    def _report_type_for_form(form: str) -> ReportType:
        normalized = form.strip().upper()
        mapping = {
            "10-K": ReportType.form_10k,
            "10-Q": ReportType.form_10q,
            "20-F": ReportType.form_20f,
            "6-K": ReportType.form_6k,
            "ANNUAL": ReportType.form_10k,
            "SEMIANNUAL": ReportType.form_6k,
            "SEMI-ANNUAL": ReportType.form_6k,
            "QUARTERLY": ReportType.form_10q,
            "Q1": ReportType.form_10q,
            "Q2": ReportType.form_10q,
            "Q3": ReportType.form_10q,
            "Q4": ReportType.form_10q,
        }
        return mapping.get(normalized, ReportType.form_10k)
