from __future__ import annotations

from urllib.parse import urlparse

from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.jp.client import EdinetClient
from market_report_finder_service.markets.jp.catalog import JpAnnualReportCatalog
from market_report_finder_service.markets.jp.tdnet import TdnetClient
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


class JpReportFinder(MarketReportFinder):
    market = Market.jp

    def __init__(self) -> None:
        self.catalog = JpAnnualReportCatalog()
        self.client = EdinetClient()
        self.tdnet = TdnetClient()

    def source_descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="edinet",
            source_name="EDINET + Issuer IR + TDnet",
            markets=[Market.jp],
            official_domain="api.edinet-fsa.go.jp / issuer websites / jpx.co.jp",
            official_sources=[
                {
                    "source_id": "edinet",
                    "source_name": "EDINET",
                    "official_domain": "api.edinet-fsa.go.jp",
                    "role": "primary_statutory_reports",
                    "notes": "Japan FSA statutory disclosure system; primary source for securities reports and semiannual reports.",
                },
                {
                    "source_id": "issuer_annual_report",
                    "source_name": "Issuer Annual Securities Report / IR",
                    "official_domain": "issuer websites",
                    "role": "statutory_mirror_or_auxiliary_ir",
                    "notes": "Issuer-hosted Annual Securities Report mirrors are primary-compatible; Integrated Reports are auxiliary only and require explicit selection.",
                },
                {
                    "source_id": "jpx_listed_company_search",
                    "source_name": "JPX Listed Company Search",
                    "official_domain": "jpx.co.jp / www2.jpx.co.jp",
                    "role": "listed_company_index_and_filing_pointer",
                    "notes": "JPX provides company, timely disclosure, and filing-information search for listed companies; statutory annual-report downloads still resolve through EDINET or issuer statutory mirrors.",
                },
                {
                    "source_id": "tdnet",
                    "source_name": "TDnet",
                    "official_domain": "jpx.co.jp",
                    "role": "exchange_disclosures",
                    "notes": "Tokyo Stock Exchange timely disclosure network; free official public listing for recent earnings releases and quarterly financial summaries.",
                },
            ],
            supports_targets=[
                ReportTarget.latest_report,
                ReportTarget.annual_report,
                ReportTarget.semiannual_report,
                ReportTarget.quarterly_report,
                ReportTarget.financial_report,
            ],
            supported_forms=["annual", "integrated-report", "yuho", "semiannual", "quarterly", "q1", "q2", "q3"],
            notes=(
                "Primary annual-report search uses Japan FSA EDINET API v2 Annual Securities Report/YUHO metadata and PDF downloads when EDINET_API_KEY is configured. "
                "Issuer catalog entries are limited to statutory Annual Securities Report mirrors unless integrated-report is explicitly requested. "
                "TDnet public listings are used as a free secondary official source for recent exchange timely disclosures and quarterly earnings summaries."
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
        candidates: list[FilingCandidate] = []
        edinet_error: Exception | None = None
        edinet_forms = self._edinet_forms(forms)
        if not forms or edinet_forms:
            try:
                candidates.extend(
                    self.client.list_filings(
                        company,
                        target=target,
                        forms=edinet_forms,
                        include_earnings=include_earnings,
                        report_year=report_year,
                    )
                )
            except Exception as exc:
                edinet_error = exc
        if self._allows_annual_reports(target=target, forms=forms):
            candidates.extend(
                self.catalog.filings_for_company(
                    company,
                    report_year=report_year,
                    include_auxiliary=self._allows_integrated_reports(forms),
                )
            )
        candidates.extend(self.tdnet.list_filings(company, target=target, forms=forms, report_year=report_year))
        if not candidates and edinet_error and not self._is_missing_edinet_key(edinet_error):
            raise edinet_error
        return self._dedupe_candidates(candidates)

    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        forms: list[str] = []
        for raw in report_types:
            text = raw.strip().lower().replace("_", "-")
            if text in {"annual", "annual-report", "yuho", "securities-report"}:
                forms.append("yuho")
            elif text in {"integrated-report", "integrated", "ir-report"}:
                forms.append("integrated-report")
            elif text in {"semiannual", "semi-annual", "interim", "half-year", "semiannual-report"}:
                forms.append("semiannual")
            elif text in {"quarterly", "quarterly-report", "financial", "q1", "q2", "q3", "q4"}:
                forms.append("quarterly")
        return list(dict.fromkeys(forms))

    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        entry = self.catalog.entry_for_url(request.document_url)
        if entry:
            return self.catalog.filing_candidate(entry)
        report_type = self._report_type_for_form(request.form)
        report_end = self.fallback_date(request.report_end)
        published_at = self.fallback_date(request.published_at or report_end)
        company_key = request.company_id or request.ticker or "manual"
        return FilingCandidate(
            source_id="edinet",
            source_name="EDINET",
            source_domain="api.edinet-fsa.go.jp",
            market=Market.jp,
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
            file_format=self.file_format_from_url(request.document_url, "pdf"),
        )

    def batch_candidate(
        self,
        item: BatchDownloadItem,
        *,
        default_company_name: str,
    ) -> FilingCandidate:
        entry = self.catalog.entry_for_url(item.document_url)
        if entry:
            return self.catalog.filing_candidate(entry)
        company_name = item.company_name or default_company_name
        report_type = self._report_type_for_form(item.report_type or "annual")
        report_end = self.fallback_date(item.report_end)
        published_at = self.fallback_date(item.published_at or report_end)
        return FilingCandidate(
            source_id="edinet",
            source_name="EDINET",
            source_domain="api.edinet-fsa.go.jp",
            market=Market.jp,
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
            file_format=item.file_format or self.file_format_from_url(item.document_url, "pdf"),
        )

    @staticmethod
    def owns_url(document_url: str) -> bool:
        host = urlparse(document_url).netloc.lower()
        return (
            "edinet-fsa.go.jp" in host
            or "release.tdnet.info" in host
            or host in JpAnnualReportCatalog.source_hosts()
        )

    def curated_annual_reports(self, *, report_year: int | None = None, limit: int = 10) -> list[FilingCandidate]:
        return self.catalog.sample_filings(limit=limit, report_year=report_year)

    @staticmethod
    def _report_type_for_form(form: str) -> ReportType:
        normalized = form.strip().upper()
        mapping = {
            "ANNUAL": ReportType.annual,
            "YUHO": ReportType.annual,
            "SECURITIES-REPORT": ReportType.annual,
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
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_url: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            by_url.setdefault(candidate.document_url, candidate)
        return list(by_url.values())

    @staticmethod
    def _is_missing_edinet_key(exc: Exception) -> bool:
        return "EDINET_API_KEY is required" in str(exc)

    @staticmethod
    def _allows_annual_reports(*, target: ReportTarget, forms: list[str]) -> bool:
        if target in {ReportTarget.latest_report, ReportTarget.annual_report}:
            return True
        if not forms:
            return False
        return any(form.strip().lower().replace("_", "-") in {"annual", "annual-report", "yuho", "securities-report", "integrated-report"} for form in forms)

    @staticmethod
    def _allows_integrated_reports(forms: list[str]) -> bool:
        return any(form.strip().lower().replace("_", "-") in {"integrated-report", "integrated", "ir-report"} for form in forms)

    @staticmethod
    def _edinet_forms(forms: list[str]) -> list[str]:
        integrated_forms = {"integrated-report", "integrated", "ir-report"}
        return [form for form in forms if form.strip().lower().replace("_", "-") not in integrated_forms]
