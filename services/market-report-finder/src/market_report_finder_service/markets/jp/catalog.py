from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import urlparse

from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportType


@dataclass(frozen=True)
class JpAnnualReportCatalogEntry:
    industry: str
    company_id: str
    ticker: str
    company_name: str
    document_url: str
    landing_url: str
    report_end: date
    published_at: date
    title: str
    source_id: str = "issuer_annual_report"
    source_name: str = "Issuer annual report / IR"
    source_tier: str = "issuer_official_direct"
    file_format: str = "pdf"
    language: str = "en"
    aliases: tuple[str, ...] = ()


JP_ANNUAL_REPORT_CATALOG: tuple[JpAnnualReportCatalogEntry, ...] = (
    JpAnnualReportCatalogEntry(
        industry="Automotive",
        company_id="JP:7203",
        ticker="7203",
        company_name="Toyota Motor Corporation",
        document_url="https://global.toyota/pages/global_toyota/ir/library/annual/2025_001_integrated_en.pdf",
        landing_url="https://global.toyota/en/ir/library/annual/",
        report_end=date(2025, 3, 31),
        published_at=date(2026, 4, 3),
        title="Toyota Integrated Report 2025",
        aliases=("Toyota", "Toyota Motor", "トヨタ自動車", "丰田", "豐田"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Banking",
        company_id="JP:8306",
        ticker="8306",
        company_name="Mitsubishi UFJ Financial Group, Inc.",
        document_url="https://www.mufg.jp/dam/ir/report/annual_report/pdf/ir2025_all_en.pdf",
        landing_url="https://www.mufg.jp/english/ir/report/annual_report/",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 8, 26),
        title="MUFG Report 2025",
        aliases=("MUFG", "Mitsubishi UFJ", "三菱UFJ", "三菱 UFJ 金融集团", "三菱UFJフィナンシャル・グループ"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Gaming",
        company_id="JP:7974",
        ticker="7974",
        company_name="Nintendo Co., Ltd.",
        document_url="https://www.nintendo.co.jp/ir/pdf/2025/annual2503e.pdf",
        landing_url="https://www.nintendo.co.jp/ir/en/library/annual/index.html",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 7, 7),
        title="Nintendo Annual Report 2025",
        aliases=("Nintendo", "任天堂", "ニンテンドー"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Retail",
        company_id="JP:9983",
        ticker="9983",
        company_name="Fast Retailing Co., Ltd.",
        document_url="https://www.fastretailing.com/eng/ir/library/pdf/ar2025_en.pdf",
        landing_url="https://www.fastretailing.com/eng/ir/library/annual.html",
        report_end=date(2025, 8, 31),
        published_at=date(2026, 6, 9),
        title="Fast Retailing Annual Report 2025",
        aliases=("Fast Retailing", "Uniqlo", "UNIQLO", "ファーストリテイリング", "迅销", "优衣库"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Industrials / IT",
        company_id="JP:6501",
        ticker="6501",
        company_name="Hitachi, Ltd.",
        document_url="https://www.hitachi.com/content/dam/hitachi/global/en/ir/media/library/integrated/2025/ar2025e.pdf",
        landing_url="https://www.hitachi.com/en/ir/library/integrated/",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 9, 29),
        title="Hitachi Integrated Report 2025",
        aliases=("Hitachi", "日立", "日立製作所"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Telecommunications",
        company_id="JP:9432",
        ticker="9432",
        company_name="Nippon Telegraph and Telephone Corporation",
        document_url="https://group.ntt/en/ir/library/annual/pdf/integrated_report_25e.pdf",
        landing_url="https://group.ntt/en/ir/library/annual/",
        report_end=date(2025, 3, 31),
        published_at=date(2026, 1, 6),
        title="NTT Integrated Report 2025",
        aliases=("NTT", "Nippon Telegraph", "日本电信电话", "日本電信電話"),
    ),
    JpAnnualReportCatalogEntry(
        industry="HVAC / Machinery",
        company_id="JP:6367",
        ticker="6367",
        company_name="Daikin Industries, Ltd.",
        document_url="https://www.daikin.com/-/media/Project/Daikin/daikin_com/investor/library/annual/2025/2025_E-pdf.pdf?rev=600b9ff254db49b2bfcf1fe8f9aca15e",
        landing_url="https://www.daikin.com/investor/library/annual",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 9, 26),
        title="Daikin Integrated Report 2025",
        aliases=("Daikin", "Daikin Industries", "ダイキン", "大金工业", "大金工業"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Information Technology",
        company_id="JP:6702",
        ticker="6702",
        company_name="Fujitsu Limited",
        document_url="https://global.fujitsu/-/media/Project/Fujitsu/Fujitsu-HQ/about/integrated-report/2025/integrated-report-2025-en.pdf?rev=889a9b84954c46e3ab772b11facb539d&hash=51362E00F41F124C9B5C6C9D6D4B8B80",
        landing_url="https://global.fujitsu/en-global/about/ir/library/integratedreport/",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 11, 4),
        title="Fujitsu Integrated Report 2025",
        aliases=("Fujitsu", "富士通"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Trading Company",
        company_id="JP:8001",
        ticker="8001",
        company_name="ITOCHU Corporation",
        document_url="https://www.itochu.co.jp/en/files/ar2025E.pdf",
        landing_url="https://www.itochu.co.jp/en/ir/doc/annual_report/",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 9, 4),
        title="ITOCHU Integrated Report 2025",
        aliases=("ITOCHU", "Itochu Corporation", "伊藤忠", "伊藤忠商事"),
    ),
    JpAnnualReportCatalogEntry(
        industry="Consumer Goods / Cosmetics",
        company_id="JP:4911",
        ticker="4911",
        company_name="Shiseido Company, Limited",
        document_url="https://corp.shiseido.com/en/ir/library/annual/pdf/2025report_en.pdf",
        landing_url="https://corp.shiseido.com/en/ir/library/annual/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 4, 24),
        title="Shiseido Integrated Report 2025",
        aliases=("Shiseido", "資生堂", "资生堂"),
    ),
)


class JpAnnualReportCatalog:
    @classmethod
    def sample_filings(cls, *, limit: int = 10, report_year: int | None = None) -> list[FilingCandidate]:
        entries = [entry for entry in JP_ANNUAL_REPORT_CATALOG if report_year is None or entry.report_end.year == report_year]
        return [cls.filing_candidate(entry) for entry in entries[: max(1, limit)]]

    @classmethod
    def resolve_company(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        matches = cls.match_entries(company_name=company_name, ticker=ticker, company_id=company_id)
        if not matches:
            raise ValueError(f"JP annual report catalog did not match: {company_id or ticker or company_name or ''}")
        candidates = [cls.company_entity(entry, score=score, reason=reason) for entry, score, reason in matches]
        return candidates[0], candidates

    @classmethod
    def match_entries(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> list[tuple[JpAnnualReportCatalogEntry, float, str]]:
        raw_identifier = str(company_id or ticker or "").strip()
        if ":" in raw_identifier:
            _, raw_identifier = raw_identifier.split(":", 1)
        normalized_identifier = cls._normalize_identifier(raw_identifier)
        normalized_query = cls._normalize(company_name or raw_identifier)
        matches: list[tuple[JpAnnualReportCatalogEntry, float, str]] = []
        for entry in JP_ANNUAL_REPORT_CATALOG:
            score, reason = cls._score(entry, normalized_identifier=normalized_identifier, normalized_query=normalized_query)
            if score >= 0.55:
                matches.append((entry, score, reason))
        return sorted(matches, key=lambda item: (item[1], item[0].published_at, item[0].company_name), reverse=True)

    @classmethod
    def company_entity(cls, entry: JpAnnualReportCatalogEntry, *, score: float = 0.99, reason: str = "catalog_match") -> CompanyEntity:
        aliases = list(dict.fromkeys([entry.company_name, entry.ticker, entry.company_id, *entry.aliases]))
        return CompanyEntity(
            market=Market.jp,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            exchange="JPX",
            aliases=aliases,
            confidence=score,
            match_reason=reason,
            metadata={
                "industry": entry.industry,
                "source_id": entry.source_id,
                "source_tier": entry.source_tier,
            },
        )

    @classmethod
    def filings_for_company(cls, company: CompanyEntity, report_year: int | None = None) -> list[FilingCandidate]:
        query_keys = {
            cls._normalize_identifier(company.company_id),
            cls._normalize_identifier(company.ticker),
            cls._normalize(company.company_name),
        }
        candidates = []
        for entry in JP_ANNUAL_REPORT_CATALOG:
            entry_keys = {
                cls._normalize_identifier(entry.company_id),
                cls._normalize_identifier(entry.ticker),
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
    def filing_candidate(cls, entry: JpAnnualReportCatalogEntry) -> FilingCandidate:
        host = urlparse(entry.document_url).netloc
        primary_document = urlparse(entry.document_url).path.rsplit("/", 1)[-1] or "annual-report.pdf"
        return FilingCandidate(
            source_id=entry.source_id,
            source_name=entry.source_name,
            source_domain=host,
            market=Market.jp,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form="annual",
            title=entry.title,
            accession_number=entry.company_id,
            primary_document=primary_document,
            report_end=entry.report_end,
            published_at=entry.published_at,
            document_url=entry.document_url,
            landing_url=entry.landing_url,
            file_format=entry.file_format,
            language=entry.language,
            metadata={
                "industry": entry.industry,
                "source_tier": entry.source_tier,
                "source_note": "Curated mainstream Japanese issuer IR annual-report download; EDINET/TDnet remain available for live statutory and exchange disclosures.",
            },
        )

    @staticmethod
    def entry_for_url(document_url: str) -> JpAnnualReportCatalogEntry | None:
        normalized = str(document_url or "").strip()
        for entry in JP_ANNUAL_REPORT_CATALOG:
            if entry.document_url == normalized or entry.landing_url == normalized:
                return entry
        return None

    @classmethod
    def source_hosts(cls) -> set[str]:
        hosts = set()
        for entry in JP_ANNUAL_REPORT_CATALOG:
            for url in (entry.document_url, entry.landing_url):
                host = urlparse(url).netloc.lower()
                if host:
                    hosts.add(host)
        return hosts

    @classmethod
    def _score(
        cls,
        entry: JpAnnualReportCatalogEntry,
        *,
        normalized_identifier: str,
        normalized_query: str,
    ) -> tuple[float, str]:
        aliases = [entry.company_id, entry.ticker, entry.company_name, *entry.aliases]
        alias_keys = [cls._normalize(alias) for alias in aliases if cls._normalize(alias)]
        identifier_keys = {cls._normalize_identifier(entry.company_id), cls._normalize_identifier(entry.ticker)}
        if normalized_identifier:
            if normalized_identifier in identifier_keys:
                return 0.99, "identifier_exact"
            if normalized_identifier in alias_keys:
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
    def _normalize_identifier(value: object) -> str:
        text = str(value or "").upper()
        if ":" in text:
            _, text = text.split(":", 1)
        return re.sub(r"[^A-Z0-9]+", "", text)

    @staticmethod
    def _normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9ぁ-んァ-ヶ一-龥가-힣ー]+", "", str(value or "").lower())
