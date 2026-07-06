from __future__ import annotations

from urllib.parse import urlparse

from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.kr.catalog import KrAnnualReportCatalog
from market_report_finder_service.markets.kr.client import DartClient
from market_report_finder_service.markets.kr.public_dart import DartPublicClient
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


class KrReportFinder(MarketReportFinder):
    market = Market.kr

    def __init__(self) -> None:
        self.catalog = KrAnnualReportCatalog()
        self.client = DartClient()
        self.public = DartPublicClient()

    def source_descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="dart",
            source_name="DART public + OpenDART + KRX KIND",
            markets=[Market.kr],
            official_domain="dart.fss.or.kr",
            official_sources=[
                {
                    "source_id": "dart_public",
                    "source_name": "DART public disclosure PDF",
                    "official_domain": "dart.fss.or.kr",
                    "role": "primary_periodic_reports_without_api_key",
                    "notes": "Public DART disclosure search with viewer/download handshake; downloads business-report PDFs without DART_API_KEY.",
                },
                {
                    "source_id": "dart",
                    "source_name": "DART / OpenDART",
                    "official_domain": "opendart.fss.or.kr",
                    "role": "primary_periodic_reports",
                    "notes": "Financial Supervisory Service disclosure system; primary source for annual, semiannual, and quarterly reports.",
                },
                {
                    "source_id": "krx_kind",
                    "source_name": "KRX KIND",
                    "official_domain": "kind.krx.co.kr",
                    "role": "exchange_disclosures",
                    "notes": "Korea Exchange disclosure system; secondary official source for listed-company exchange disclosures and attachments.",
                },
            ],
            supports_targets=[
                ReportTarget.latest_report,
                ReportTarget.annual_report,
                ReportTarget.semiannual_report,
                ReportTarget.quarterly_report,
                ReportTarget.financial_report,
            ],
            supported_forms=["annual", "semiannual", "quarterly", "q1", "q3"],
            notes=(
                "Mainstream Korean annual reports are resolved from public DART web search and downloaded as DART PDFs without an API key. "
                "When DART_API_KEY is configured, OpenDART API ZIP downloads remain available as an enhanced statutory source. "
                "KRX KIND is retained as a secondary official exchange disclosure source."
            ),
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
        catalog_error: Exception | None = None
        try:
            return self.catalog.resolve_company(company_name=company_name, ticker=ticker, company_id=company_id)
        except Exception as exc:
            catalog_error = exc
        try:
            return self.client.resolve_company(company_name=company_name, ticker=ticker, company_id=company_id)
        except Exception:
            if catalog_error:
                raise catalog_error
            raise

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
        del include_amendments
        del include_earnings
        candidates: list[FilingCandidate] = []
        public_error: Exception | None = None
        dart_error: Exception | None = None
        try:
            candidates.extend(self.public.list_filings(company, target=target, forms=forms, report_year=report_year))
        except Exception as exc:
            public_error = exc
        try:
            candidates.extend(
                self.client.list_filings(
                    company,
                    target=target,
                    forms=forms,
                    include_earnings=False,
                    report_year=report_year,
                )
            )
        except Exception as exc:
            dart_error = exc
        if not candidates:
            if public_error:
                raise public_error
            if dart_error and not self._is_missing_dart_key(dart_error):
                raise dart_error
        return self._dedupe_candidates(candidates)

    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        forms: list[str] = []
        for raw in report_types:
            text = raw.strip().lower().replace("_", "-")
            if text in {"annual", "annual-report", "business-report"}:
                forms.append("annual")
            elif text in {"semiannual", "semi-annual", "interim", "half-year", "semiannual-report"}:
                forms.append("semiannual")
            elif text in {"quarterly", "quarterly-report", "financial", "q1", "q2", "q3", "q4"}:
                forms.append("quarterly")
        return list(dict.fromkeys(forms))

    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        catalog_entry = self.catalog.entry_for_url(request.document_url)
        if catalog_entry:
            return self.mark_user_url_candidate(
                self.catalog.filing_candidate(catalog_entry),
                original_url=request.document_url,
                input_kind="direct_download",
            )
        report_type = self._report_type_for_form(request.form)
        report_end = self.fallback_date(request.report_end)
        published_at = self.fallback_date(request.published_at or report_end)
        company_key = request.company_id or request.ticker or "manual"
        source_id, source_name, source_domain, file_format = self._source_for_url(request.document_url, fallback_format="zip")
        candidate = FilingCandidate(
            source_id=source_id,
            source_name=source_name,
            source_domain=source_domain,
            market=Market.kr,
            company_id=str(company_key),
            ticker=request.ticker,
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
            file_format=file_format if source_id in {"dart_public", "krx_kind"} else self.file_format_from_url(request.document_url, file_format),
        )
        return self.mark_user_url_candidate(candidate, original_url=request.document_url, input_kind="direct_download")

    def batch_candidate(
        self,
        item: BatchDownloadItem,
        *,
        default_company_name: str,
    ) -> FilingCandidate:
        catalog_entry = self.catalog.entry_for_url(item.document_url)
        if catalog_entry:
            return self.mark_user_url_candidate(
                self.catalog.filing_candidate(catalog_entry),
                original_url=item.document_url,
                input_kind="batch_download",
            )
        company_name = item.company_name or default_company_name
        report_type = self._report_type_for_form(item.report_type or "annual")
        report_end = self.fallback_date(item.report_end)
        published_at = self.fallback_date(item.published_at or report_end)
        source_id, source_name, source_domain, file_format = self._source_for_url(item.document_url, fallback_format=item.file_format or "zip")
        candidate = FilingCandidate(
            source_id=source_id,
            source_name=source_name,
            source_domain=source_domain,
            market=Market.kr,
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
            file_format=file_format if source_id in {"dart_public", "krx_kind"} else item.file_format or self.file_format_from_url(item.document_url, file_format),
        )
        return self.mark_user_url_candidate(candidate, original_url=item.document_url, input_kind="batch_download")

    @staticmethod
    def owns_url(document_url: str) -> bool:
        return market_owns_url(Market.kr, document_url)

    def curated_annual_reports(self, *, report_year: int | None = None, limit: int = 10) -> list[FilingCandidate]:
        candidates: list[FilingCandidate] = []
        for company in self.catalog.sample_companies(limit=limit):
            try:
                candidates.extend(
                    self.public.list_filings(
                        company,
                        target=ReportTarget.annual_report,
                        forms=["annual"],
                        report_year=report_year,
                    )[:1]
                )
            except Exception:
                continue
            if len(candidates) >= limit:
                break
        return self._dedupe_candidates(candidates)[:limit]

    @staticmethod
    def _report_type_for_form(form: str) -> ReportType:
        normalized = form.strip().upper()
        mapping = {
            "ANNUAL": ReportType.annual,
            "BUSINESS-REPORT": ReportType.annual,
            "SEMIANNUAL": ReportType.semiannual,
            "SEMI-ANNUAL": ReportType.semiannual,
            "INTERIM": ReportType.semiannual,
            "QUARTERLY": ReportType.quarterly,
            "Q1": ReportType.quarterly,
            "Q2": ReportType.quarterly,
            "Q3": ReportType.quarterly,
            "Q4": ReportType.quarterly,
        }
        return mapping.get(normalized, ReportType.annual)

    @staticmethod
    def _source_for_url(document_url: str, *, fallback_format: str) -> tuple[str, str, str, str]:
        host = urlparse(document_url).netloc.lower()
        path = urlparse(document_url).path.lower()
        if "dart.fss.or.kr" in host and "/pdf/download/pdf.do" in path:
            return "dart_public", "DART public disclosure PDF", "dart.fss.or.kr", "pdf"
        if "dart.fss.or.kr" in host and "/pdf/download/main.do" in path:
            return "dart_public", "DART public disclosure PDF", "dart.fss.or.kr", "pdf"
        if "dart.fss.or.kr" in host and "/report/combined.do" in path:
            return "dart_public", "DART public disclosure viewer", "dart.fss.or.kr", "html"
        if "dart.fss.or.kr" in host and "/dsaf001/" in path:
            return "dart_public", "DART public disclosure viewer", "dart.fss.or.kr", "html"
        if "kind.krx.co.kr" in host:
            return "krx_kind", "KRX KIND", "kind.krx.co.kr", "html"
        return "dart", "DART", "opendart.fss.or.kr", fallback_format

    @staticmethod
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_key: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            key = candidate.accession_number or candidate.document_url
            by_key.setdefault(key, candidate)
        return list(by_key.values())

    @staticmethod
    def _is_missing_dart_key(exc: Exception) -> bool:
        return "DART_API_KEY is required" in str(exc)
