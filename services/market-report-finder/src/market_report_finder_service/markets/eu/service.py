from __future__ import annotations

from urllib.parse import urlparse

from market_report_finder_service.data.foreign_aliases import foreign_alias_entry
from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.eu.catalog import EuAnnualReportCatalog
from market_report_finder_service.markets.eu.client import EsefIndexClient
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


class EuReportFinder(MarketReportFinder):
    market = Market.eu
    URL_HOST_SUFFIXES = (
        "filings.xbrl.org",
        "annualreports.ai",
        "financialreports.eu",
        "financialfilings.com",
        "fca.org.uk",
        "amf-france.org",
        "info-financiere.fr",
        "unternehmensregister.de",
        "bundesanzeiger.de",
        "afm.nl",
        "six-group.com",
        "ser-ag.com",
        "astrazeneca.com",
        "bp.com",
        "barclays",
        "totalenergies.com",
        "sanofi.com",
        "airliquide.com",
        "siemens.com",
        "sap.com",
        "telekom.com",
        "asml.com",
        "philips.com",
        "heinekencompany.com",
        "theheinekencompany.com",
        "nestle.com",
        "novartis.com",
        "roche.com",
        "hsbc.com",
        "shell.com",
        "londonstockexchange.com",
        "investegate.co.uk",
        "unilever.com",
        "diageo.com",
        "cdn-rio.dataweavers.io",
        "riotinto.com",
        "glencore.com",
        "lseg.com",
        "lvmh-com.cdn.prismic.io",
        "www-axa-com.cdn.prismic.io",
        "lvmh.com",
        "loreal-finance.com",
        "schneider-electric.com",
        "se.com",
        "bnpparibas",
        "airbus.com",
        "vinci.com",
        "allianz.com",
        "eqs-news.com",
        "bmwgroup.com",
        "vw-mms.de",
        "volkswagen-group.com",
        "basf.com",
        "infineon.com",
        "munichre.com",
        "ing.com",
        "prosus.com",
        "adyen.com",
        "aholddelhaize.com",
        "dsm-firmenich.com",
        "ubs.com",
        "zurich.com",
        "edge.sitecorecloud.io",
        "abb.com",
        "richemont.com",
        "swissre.com",
        "sika.com",
        "holcim.com",
    )

    def __init__(self) -> None:
        self.client = EsefIndexClient()

    def source_descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="xbrl_filings_esef",
            source_name="European ESEF / official OAM filings",
            markets=[Market.eu],
            official_domain="filings.xbrl.org",
            official_sources=[
                {
                    "source_id": "xbrl_filings_esef",
                    "source_name": "filings.xbrl.org ESEF index",
                    "official_domain": "filings.xbrl.org",
                    "role": "official_oam_esef_mirror",
                    "notes": "Public ESEF package index for annual financial reports filed via European OAMs. Used for UK, France, Germany, and Netherlands search/download.",
                },
                {
                    "source_id": "six_direct",
                    "source_name": "SIX / issuer official URL direct download",
                    "official_domain": "six-group.com",
                    "role": "switzerland_direct_download",
                    "notes": "Swiss listed-company search is not yet exposed through the ESEF index. Direct official URL downloads are supported and archived under EU/CH.",
                },
                {
                    "source_id": "issuer_annual_report",
                    "source_name": "Issuer / mainstream annual report downloads",
                    "official_domain": "issuer websites",
                    "role": "current_year_annual_report_fallback",
                    "notes": "Curated issuer PDF/HTML download links for major UK, French, German, Dutch, and Swiss companies. Used when ESEF search is unavailable, stale, or not PDF-friendly.",
                },
            ],
            supports_targets=[
                ReportTarget.latest_report,
                ReportTarget.annual_report,
                ReportTarget.financial_report,
            ],
            supported_forms=["annual", "ESEF", "AFR"],
            notes=(
                "Initial European coverage is scoped to UK, France, Germany, Netherlands, and Switzerland. "
                "Searchable ESEF annual financial reports are available for UK/FR/DE/NL via the public XBRL filings index; "
                "current-year major-company annual reports are supplemented with issuer/mainstream download links; "
                "CH is supported through issuer direct-download URLs."
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
        alias = foreign_alias_entry(Market.eu.value, company_name) if company_name and not (ticker or company_id) else None
        if alias:
            ticker = str(alias.get("ticker") or "") or ticker
            company_id = str(alias.get("company_id") or "") or company_id
            company_name = str(alias.get("canonical_name") or "") or company_name
        country, clean_company_id = self._split_country_identifier(company_id)
        catalog_error: Exception | None = None
        esef_error: Exception | None = None
        catalog_resolved: CompanyEntity | None = None
        catalog_candidates: list[CompanyEntity] = []
        esef_resolved: CompanyEntity | None = None
        esef_candidates: list[CompanyEntity] = []
        try:
            catalog_resolved, catalog_candidates = EuAnnualReportCatalog.resolve_company(
                company_name=company_name,
                ticker=ticker,
                company_id=clean_company_id,
                country=country,
            )
        except Exception as exc:
            catalog_error = exc
        try:
            esef_resolved, esef_candidates = self.client.resolve_company(
                company_name=company_name,
                ticker=ticker,
                company_id=clean_company_id,
                country=country,
            )
        except Exception as exc:
            esef_error = exc

        merged = self._merge_companies([*catalog_candidates, *esef_candidates])
        if catalog_resolved:
            return catalog_resolved, merged
        if esef_resolved:
            return esef_resolved, merged
        raise ValueError(str(catalog_error or esef_error or "EU company not found"))

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
        if self._allows_annual_reports(target=target, forms=forms):
            candidates.extend(EuAnnualReportCatalog.filings_for_company(company, report_year=report_year))
        try:
            candidates.extend(
                self.client.list_filings(
                    company,
                    target=target,
                    forms=forms,
                    include_earnings=include_earnings,
                    report_year=report_year,
                )
            )
        except Exception:
            if not candidates:
                raise
        return self._merge_filings(candidates)

    def forms_for_report_types(self, report_types: list[str]) -> list[str]:
        forms: list[str] = []
        for raw in report_types:
            text = raw.strip().lower().replace("_", "-")
            if text in {"annual", "annual-report", "financial", "esef", "afr"}:
                forms.append("annual")
        return list(dict.fromkeys(forms))

    def curated_annual_reports(
        self,
        *,
        report_year: int | None = None,
        limit: int = 10,
        country: str | None = None,
    ) -> list[FilingCandidate]:
        return EuAnnualReportCatalog.sample_filings(limit=limit, report_year=report_year, country=country)

    def direct_candidate(self, request: DirectReportDownloadRequest) -> FilingCandidate:
        catalog_entry = EuAnnualReportCatalog.entry_for_url(request.document_url)
        if catalog_entry is not None:
            return EuAnnualReportCatalog.filing_candidate(catalog_entry)
        report_end = self.fallback_date(request.report_end)
        published_at = self.fallback_date(request.published_at or report_end)
        country = self.client.normalize_country(request.company_id) or self._country_from_url(request.document_url) or "GB"
        source_id = "six_direct" if country == "CH" else "eu_direct"
        company_key = request.cik or request.ticker or request.company_id or "manual"
        return FilingCandidate(
            source_id=source_id,
            source_name="Official European filing direct download",
            source_domain=urlparse(request.document_url).netloc or "manual",
            market=Market.eu,
            company_id=str(company_key),
            ticker=request.ticker,
            company_name=request.company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form=request.form or "annual",
            title=request.title or f"{request.company_name} annual financial report",
            accession_number="manual",
            primary_document=self.primary_document_from_url(request.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=request.document_url,
            landing_url=request.landing_url or request.document_url,
            file_format=self.file_format_from_url(request.document_url, "pdf"),
            metadata={
                "country": country,
                "country_label": self.client.country_label(country),
                "source_tier": "official_direct",
            },
        )

    def batch_candidate(
        self,
        item: BatchDownloadItem,
        *,
        default_company_name: str,
    ) -> FilingCandidate:
        catalog_entry = EuAnnualReportCatalog.entry_for_url(item.document_url)
        if catalog_entry is not None:
            return EuAnnualReportCatalog.filing_candidate(catalog_entry)
        company_name = item.company_name or default_company_name
        report_end = self.fallback_date(item.report_end)
        published_at = self.fallback_date(item.published_at or report_end)
        country, clean_company_id = self._split_country_identifier(item.company_id)
        country = country or self._country_from_url(item.document_url) or "GB"
        host = urlparse(item.document_url).netloc.lower()
        is_esef_index = "filings.xbrl.org" in host
        source_id = "xbrl_filings_esef" if is_esef_index else "six_direct" if country == "CH" else "eu_direct"
        source_tier = "official_mirror" if is_esef_index else "official_direct"
        return FilingCandidate(
            source_id=source_id,
            source_name="filings.xbrl.org ESEF index" if is_esef_index else "Official European filing direct download",
            source_domain=host or "manual",
            market=Market.eu,
            company_id=clean_company_id or item.ticker or "manual",
            ticker=item.ticker,
            company_name=company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form=item.report_type or "annual",
            title=item.title or f"{company_name} annual financial report",
            accession_number="manual",
            primary_document=self.primary_document_from_url(item.document_url),
            report_end=report_end,
            published_at=published_at,
            document_url=item.document_url,
            landing_url=item.landing_url or item.document_url,
            file_format=item.file_format or self.file_format_from_url(item.document_url, "pdf"),
            metadata={
                "country": country,
                "country_label": self.client.country_label(country),
                "source_tier": source_tier,
            },
        )

    @staticmethod
    def owns_url(document_url: str) -> bool:
        host = urlparse(document_url).hostname or ""
        return any(EuReportFinder._host_matches(host, suffix) for suffix in EuReportFinder.URL_HOST_SUFFIXES)

    @staticmethod
    def _host_matches(host: str, suffix: str) -> bool:
        normalized_host = host.rstrip(".").lower()
        normalized_suffix = suffix.rstrip(".").lower()
        return normalized_host == normalized_suffix or normalized_host.endswith(f".{normalized_suffix}")

    @staticmethod
    def _allows_annual_reports(*, target: ReportTarget, forms: list[str]) -> bool:
        normalized = {str(form or "").strip().lower().replace("_", "-") for form in forms if str(form or "").strip()}
        if normalized and not normalized & {"annual", "annual-report", "esef", "afr"}:
            return False
        return target in {ReportTarget.latest_report, ReportTarget.annual_report, ReportTarget.financial_report}

    @staticmethod
    def _merge_companies(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        merged: dict[str, CompanyEntity] = {}
        for candidate in candidates:
            key = "|".join(
                [
                    candidate.market.value,
                    str(candidate.metadata.get("country") or candidate.exchange or ""),
                    candidate.company_id.upper(),
                ]
            )
            current = merged.get(key)
            if current is None or candidate.confidence > current.confidence:
                merged[key] = candidate
        return sorted(merged.values(), key=lambda item: (item.confidence, item.company_name), reverse=True)

    @staticmethod
    def _merge_filings(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        merged: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            key = candidate.document_url
            current = merged.get(key)
            if current is None or candidate.published_at > current.published_at:
                merged[key] = candidate
        return sorted(
            merged.values(),
            key=lambda item: (item.report_end, item.published_at, item.source_id != "xbrl_filings_esef"),
            reverse=True,
        )

    @staticmethod
    def _split_country_identifier(company_id: str | None) -> tuple[str | None, str | None]:
        if not company_id:
            return None, None
        raw = str(company_id).strip()
        if ":" not in raw:
            return None, raw
        country, value = raw.split(":", 1)
        return EsefIndexClient.normalize_country(country), value.strip() or None

    @staticmethod
    def _country_from_url(document_url: str) -> str | None:
        host = urlparse(document_url).netloc.lower()
        if host.endswith(".ch") or "six-group.com" in host or "ser-ag.com" in host:
            return "CH"
        if host.endswith(".de") or "bundesanzeiger.de" in host or "unternehmensregister.de" in host:
            return "DE"
        if host.endswith(".fr") or "info-financiere.fr" in host or "amf-france.org" in host:
            return "FR"
        if host.endswith(".nl") or "afm.nl" in host:
            return "NL"
        if host.endswith(".uk") or "fca.org.uk" in host:
            return "GB"
        return None
