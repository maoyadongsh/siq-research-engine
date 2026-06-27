from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import urlparse

from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportType


@dataclass(frozen=True)
class EuAnnualReportCatalogEntry:
    country: str
    company_id: str
    ticker: str
    company_name: str
    document_url: str
    landing_url: str
    report_end: date
    published_at: date
    title: str
    source_id: str = "issuer_annual_report"
    source_name: str = "Issuer annual report download"
    source_tier: str = "official_direct"
    file_format: str = "pdf"
    language: str = "en"
    aliases: tuple[str, ...] = ()


EU_ANNUAL_REPORT_CATALOG: tuple[EuAnnualReportCatalogEntry, ...] = (
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:AZN",
        ticker="AZN",
        company_name="AstraZeneca PLC",
        document_url="https://www.astrazeneca.com/content/dam/az/Investor_Relations/annual-report-2025/pdf/AstraZeneca_AR_2025.pdf",
        landing_url="https://www.astrazeneca.com/investor-relations/annual-reports/annual-report-2025.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="AstraZeneca Annual Report 2025",
        aliases=("AstraZeneca", "AstraZeneca PLC", "AZN"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:BP",
        ticker="BP",
        company_name="BP p.l.c.",
        document_url="https://www.bp.com/api/files/6cqieuqhq4no/master/33M2iHp8A6d07McKzKqLNP/12b5d4eccb4e02093d1ad9efc0d6a746/bp-annual-report-and-form-20f-2025.pdf",
        landing_url="https://www.bp.com/investors/results-reporting-and-presentations/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 7),
        title="BP Annual Report and Form 20-F 2025",
        aliases=("BP", "BP PLC", "BP p.l.c."),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:BARC",
        ticker="BARC",
        company_name="Barclays PLC",
        document_url="https://home.barclays/content/dam/home-barclays/documents/investor-relations/reports-and-events/annual-reports/2025/Barclays-PLC-Annual-Report-2025.pdf",
        landing_url="https://home.barclays/investor-relations/reports-and-events/annual-reports/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 17),
        title="Barclays PLC Annual Report 2025",
        aliases=("Barclays", "Barclays PLC", "BARC"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:TTE",
        ticker="TTE",
        company_name="TotalEnergies SE",
        document_url="https://totalenergies.com/system/files/documents/totalenergies_universal-registration-document-2025_2026_en.pdf",
        landing_url="https://totalenergies.com/investors/publications-and-regulated-information/regulated-information/universal-registration-document",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 27),
        title="TotalEnergies Universal Registration Document 2025",
        aliases=("TotalEnergies", "TotalEnergies SE", "TTE"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:SAN",
        ticker="SAN",
        company_name="Sanofi",
        document_url="https://www.sanofi.com/assets/dotcom/content-app/publications/annual-report-on-form-20-f/2025-01-01-form-20-f-2025-en.pdf",
        landing_url="https://www.sanofi.com/en/investors/financial-reports-and-regulated-information",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 20),
        title="Sanofi Annual Report on Form 20-F 2025",
        aliases=("Sanofi", "Sanofi SA", "SAN"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:AI",
        ticker="AI",
        company_name="Air Liquide S.A.",
        document_url="https://www.airliquide.com/sites/airliquide.com/files/2026-03/air-liquide-2025-universal-registration-document-interactive.pdf",
        landing_url="https://www.airliquide.com/group/publications",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 6),
        title="Air Liquide Universal Registration Document 2025",
        aliases=("Air Liquide", "Air Liquide S.A.", "AI"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:SIE",
        ticker="SIE",
        company_name="Siemens AG",
        document_url="https://assets.new.siemens.com/siemens/assets/api/uuid:428ea18a-e7ab-4f93-a160-33908f1c3540/Siemens-Annual-Report-2025.pdf",
        landing_url="https://www.siemens.com/global/en/company/investor-relations/annual-reports.html",
        report_end=date(2025, 9, 30),
        published_at=date(2025, 12, 11),
        title="Siemens Annual Report 2025",
        aliases=("Siemens", "Siemens AG", "SIE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:SAP",
        ticker="SAP",
        company_name="SAP SE",
        document_url="https://www.sap.com/docs/download/investors/2025/sap-2025-annual-report-form-20f.pdf",
        landing_url="https://www.sap.com/investors/en/financial-documents-and-events.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="SAP Annual Report on Form 20-F 2025",
        aliases=("SAP", "SAP SE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:DTE",
        ticker="DTE",
        company_name="Deutsche Telekom AG",
        document_url="https://report.telekom.com/annual-report-2025/_assets/downloads/entire-dtag-ar25.pdf",
        landing_url="https://report.telekom.com/annual-report-2025/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="Deutsche Telekom Annual Report 2025",
        aliases=("Deutsche Telekom", "Deutsche Telekom AG", "DTE"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:ASML",
        ticker="ASML",
        company_name="ASML Holding N.V.",
        document_url="https://ourbrand.asml.com/m/71076aaad607de4d/original/asml-2025-annual-report-based-on-us-gaap.pdf",
        landing_url="https://www.asml.com/en/investors/annual-report/2025/downloads",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 25),
        title="ASML Annual Report 2025 based on US GAAP",
        aliases=("ASML", "ASML Holding", "ASML Holding N.V."),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:PHIA",
        ticker="PHIA",
        company_name="Koninklijke Philips N.V.",
        document_url="https://www.results.philips.com/app/uploads/2026/04/PhilipsFullAnnualReport2025-English.pdf",
        landing_url="https://www.results.philips.com/ar25",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 19),
        title="Philips Annual Report 2025",
        aliases=("Philips", "Koninklijke Philips", "Koninklijke Philips N.V.", "PHIA"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:HEIA",
        ticker="HEIA",
        company_name="Heineken N.V.",
        document_url="https://www.theheinekencompany.com/sites/heineken-corp/files/2026-02/2025_Heineken_NV_Annual_Report_Interactive_100226_FINAL.pdf",
        landing_url="https://www.theheinekencompany.com/investors/results-reports-webcasts-presentations",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 12),
        title="Heineken N.V. Annual Report 2025",
        aliases=("Heineken", "Heineken N.V.", "HEIA"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:NESN",
        ticker="NESN",
        company_name="Nestle S.A.",
        document_url="https://www.nestle.com/sites/default/files/2026-02/annual-review-2025-en.pdf",
        landing_url="https://www.nestle.com/investors/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 13),
        title="Nestle Annual Review 2025",
        aliases=("Nestle", "Nestle S.A.", "NESN"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:NOVN",
        ticker="NOVN",
        company_name="Novartis AG",
        document_url="https://www.novartis.com/sites/novartis_com/files/novartis-annual-report-2025.pdf",
        landing_url="https://www.novartis.com/news/media-library/novartis-annual-report-2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 1, 30),
        title="Novartis Annual Report 2025",
        aliases=("Novartis", "Novartis AG", "NOVN"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:ROG",
        ticker="ROG",
        company_name="Roche Holding AG",
        document_url="https://assets.roche.com/f/176343/x/fa3c863601/ar25e.pdf",
        landing_url="https://www.roche.com/investors/annualreport25",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 1, 29),
        title="Roche Annual Report 2025",
        aliases=("Roche", "Roche Holding", "Roche Holding AG", "ROG"),
    ),
)


class EuAnnualReportCatalog:
    COUNTRY_ALIASES = {
        "UK": "GB",
        "GB": "GB",
        "UNITED KINGDOM": "GB",
        "FR": "FR",
        "FRANCE": "FR",
        "DE": "DE",
        "GERMANY": "DE",
        "NL": "NL",
        "NETHERLANDS": "NL",
        "CH": "CH",
        "SWITZERLAND": "CH",
    }

    @classmethod
    def normalize_country(cls, value: object) -> str | None:
        text = str(value or "").strip().upper()
        if not text:
            return None
        return cls.COUNTRY_ALIASES.get(text, text if text in {"GB", "FR", "DE", "NL", "CH"} else None)

    @classmethod
    def resolve_company(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        country: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        matches = cls.match_entries(company_name=company_name, ticker=ticker, company_id=company_id, country=country)
        if not matches:
            raise ValueError(f"EU annual report catalog did not match: {company_id or ticker or company_name or ''}")
        candidates = [cls.company_entity(entry, score=score, reason=reason) for entry, score, reason in matches]
        return candidates[0], candidates

    @classmethod
    def match_entries(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        country: str | None = None,
    ) -> list[tuple[EuAnnualReportCatalogEntry, float, str]]:
        target_country = cls.normalize_country(country)
        raw_identifier = str(company_id or ticker or "").strip()
        if ":" in raw_identifier:
            prefix, suffix = raw_identifier.split(":", 1)
            target_country = target_country or cls.normalize_country(prefix)
            raw_identifier = suffix.strip()
        normalized_identifier = cls._normalize(raw_identifier)
        normalized_query = cls._normalize(company_name or raw_identifier)
        matches: list[tuple[EuAnnualReportCatalogEntry, float, str]] = []
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if target_country and entry.country != target_country:
                continue
            score, reason = cls._score(entry, normalized_identifier=normalized_identifier, normalized_query=normalized_query)
            if score >= 0.55:
                matches.append((entry, score, reason))
        return sorted(matches, key=lambda item: (item[1], item[0].published_at, item[0].company_name), reverse=True)

    @classmethod
    def company_entity(cls, entry: EuAnnualReportCatalogEntry, *, score: float = 0.99, reason: str = "catalog_match") -> CompanyEntity:
        aliases = list(dict.fromkeys([entry.company_name, entry.ticker, entry.company_id, *entry.aliases]))
        return CompanyEntity(
            market=Market.eu,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            exchange=entry.country,
            aliases=aliases,
            confidence=score,
            match_reason=reason,
            metadata={
                "country": entry.country,
                "country_label": cls.country_label(entry.country),
                "source_id": entry.source_id,
                "source_tier": entry.source_tier,
            },
        )

    @classmethod
    def filings_for_company(cls, company: CompanyEntity, report_year: int | None = None) -> list[FilingCandidate]:
        country = cls.normalize_country(company.metadata.get("country") or company.exchange)
        query_keys = {
            cls._normalize(company.company_id),
            cls._normalize(company.ticker),
            cls._normalize(company.company_name),
        }
        candidates = []
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if country and entry.country != country:
                continue
            entry_keys = {
                cls._normalize(entry.company_id),
                cls._normalize(entry.ticker),
                cls._normalize(entry.company_name),
                *(cls._normalize(alias) for alias in entry.aliases),
            }
            if not (query_keys & entry_keys):
                continue
            if report_year is not None and entry.report_end.year != report_year:
                continue
            candidates.append(cls.filing_candidate(entry))
        return sorted(candidates, key=lambda item: (item.report_end, item.published_at), reverse=True)

    @classmethod
    def filing_candidate(cls, entry: EuAnnualReportCatalogEntry) -> FilingCandidate:
        host = urlparse(entry.document_url).netloc
        return FilingCandidate(
            source_id=entry.source_id,
            source_name=entry.source_name,
            source_domain=host,
            market=Market.eu,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form="annual",
            title=entry.title,
            accession_number=entry.company_id,
            primary_document=urlparse(entry.document_url).path.rsplit("/", 1)[-1] or "annual-report.pdf",
            report_end=entry.report_end,
            published_at=entry.published_at,
            document_url=entry.document_url,
            landing_url=entry.landing_url,
            file_format=entry.file_format,
            language=entry.language,
            metadata={
                "country": entry.country,
                "country_label": cls.country_label(entry.country),
                "source_tier": entry.source_tier,
                "source_note": "Curated issuer/mainstream annual-report download used to provide current-year European annual reports.",
            },
        )

    @staticmethod
    def entry_for_url(document_url: str) -> EuAnnualReportCatalogEntry | None:
        normalized = str(document_url or "").strip()
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if entry.document_url == normalized or entry.landing_url == normalized:
                return entry
        return None

    @staticmethod
    def country_label(country: str | None) -> str:
        return {
            "GB": "UK",
            "FR": "France",
            "DE": "Germany",
            "NL": "Netherlands",
            "CH": "Switzerland",
        }.get(country or "", country or "")

    @classmethod
    def _score(
        cls,
        entry: EuAnnualReportCatalogEntry,
        *,
        normalized_identifier: str,
        normalized_query: str,
    ) -> tuple[float, str]:
        aliases = [entry.company_id, entry.ticker, entry.company_name, *entry.aliases]
        alias_keys = [cls._normalize(alias) for alias in aliases if cls._normalize(alias)]
        if normalized_identifier:
            if normalized_identifier in {cls._normalize(entry.company_id), cls._normalize(entry.ticker)}:
                return 0.99, "identifier_exact"
            for key in alias_keys:
                if normalized_identifier == key:
                    return 0.95, "alias_exact"
        if not normalized_query:
            return -1.0, "empty_query"
        best = 0.0
        reason = "query_mismatch"
        for key in alias_keys:
            if normalized_query == key:
                return 0.96, "company_exact"
            if normalized_query in key:
                best = max(best, 0.88)
                reason = "company_contains_query"
            elif key in normalized_query:
                best = max(best, 0.82)
                reason = "query_contains_company"
            else:
                ratio = SequenceMatcher(None, normalized_query, key).ratio()
                if ratio >= 0.72 and ratio > best:
                    best = 0.70 + (ratio - 0.72) * 0.3
                    reason = "company_fuzzy"
        return best, reason

    @staticmethod
    def _normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
