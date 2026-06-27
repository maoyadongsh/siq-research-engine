from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import urlparse

from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportType


@dataclass(frozen=True)
class KrAnnualReportCatalogEntry:
    industry: str
    company_id: str
    ticker: str
    company_name: str
    aliases: tuple[str, ...] = ()
    report_end: date = date(2025, 12, 31)
    published_at: date | None = None
    title: str | None = None
    document_url: str | None = None
    landing_url: str | None = None
    source_id: str = "dart_public"
    source_name: str = "DART public disclosure viewer"
    source_tier: str = "statutory_public_html"
    file_format: str = "html"
    language: str = "ko"


KR_ANNUAL_REPORT_CATALOG: tuple[KrAnnualReportCatalogEntry, ...] = (
    KrAnnualReportCatalogEntry(
        industry="Semiconductors / Electronics",
        company_id="00126380",
        ticker="005930",
        company_name="Samsung Electronics Co., Ltd.",
        aliases=("Samsung Electronics", "삼성전자", "三星电子", "三星電子"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Semiconductors",
        company_id="00164779",
        ticker="000660",
        company_name="SK hynix Inc.",
        aliases=("SK hynix", "SK Hynix", "SK하이닉스", "海力士"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Internet Services",
        company_id="00266961",
        ticker="035420",
        company_name="NAVER Corporation",
        aliases=("NAVER", "Naver", "네이버", "韩国 NAVER"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Automotive",
        company_id="00164742",
        ticker="005380",
        company_name="Hyundai Motor Company",
        aliases=("Hyundai Motor", "Hyundai", "현대자동차", "现代汽车", "現代自動車"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Airlines",
        company_id="00113526",
        ticker="003490",
        company_name="Korean Air Lines Co., Ltd.",
        aliases=("Korean Air", "대한항공", "大韩航空", "大韓航空"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Steel",
        company_id="00155319",
        ticker="005490",
        company_name="POSCO Holdings Inc.",
        aliases=("POSCO Holdings", "POSCO", "POSCO홀딩스", "浦项", "浦項"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Chemicals / Battery Materials",
        company_id="00356361",
        ticker="051910",
        company_name="LG Chem, Ltd.",
        aliases=("LG Chem", "LG화학", "LG 化学", "LG化學"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Banking",
        company_id="00382199",
        ticker="055550",
        company_name="Shinhan Financial Group Co., Ltd.",
        aliases=("Shinhan Financial Group", "Shinhan", "신한지주", "新韩金融", "新韓金融"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Biopharmaceuticals",
        company_id="00413046",
        ticker="068270",
        company_name="Celltrion, Inc.",
        aliases=("Celltrion", "셀트리온", "赛尔群", "賽爾群"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Telecommunications",
        company_id="00159023",
        ticker="017670",
        company_name="SK Telecom Co., Ltd.",
        aliases=("SK Telecom", "SK텔레콤", "SK 电讯", "SK電訊"),
    ),
)


class KrAnnualReportCatalog:
    @classmethod
    def sample_companies(cls, *, limit: int = 10) -> list[CompanyEntity]:
        return [cls.company_entity(entry) for entry in KR_ANNUAL_REPORT_CATALOG[: max(1, limit)]]

    @classmethod
    def sample_filings(cls, *, limit: int = 10, report_year: int | None = None) -> list[FilingCandidate]:
        candidates = []
        for entry in KR_ANNUAL_REPORT_CATALOG:
            if report_year is not None and entry.report_end.year != report_year:
                continue
            if entry.document_url:
                candidates.append(cls.filing_candidate(entry))
            if len(candidates) >= limit:
                break
        return candidates

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
            raise ValueError(f"KR annual report catalog did not match: {company_id or ticker or company_name or ''}")
        candidates = [cls.company_entity(entry, score=score, reason=reason) for entry, score, reason in matches]
        return candidates[0], candidates

    @classmethod
    def match_entries(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> list[tuple[KrAnnualReportCatalogEntry, float, str]]:
        raw_identifier = str(company_id or ticker or "").strip()
        if ":" in raw_identifier:
            _, raw_identifier = raw_identifier.split(":", 1)
        normalized_identifier = cls._normalize_identifier(raw_identifier)
        normalized_query = cls._normalize(company_name or raw_identifier)
        matches: list[tuple[KrAnnualReportCatalogEntry, float, str]] = []
        for entry in KR_ANNUAL_REPORT_CATALOG:
            score, reason = cls._score(entry, normalized_identifier=normalized_identifier, normalized_query=normalized_query)
            if score >= 0.55:
                matches.append((entry, score, reason))
        return sorted(matches, key=lambda item: (item[1], item[0].company_name), reverse=True)

    @classmethod
    def company_entity(cls, entry: KrAnnualReportCatalogEntry, *, score: float = 0.99, reason: str = "catalog_match") -> CompanyEntity:
        aliases = list(dict.fromkeys([entry.company_name, entry.ticker, entry.company_id, *entry.aliases]))
        return CompanyEntity(
            market=Market.kr,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            exchange="KRX",
            aliases=aliases,
            confidence=score,
            match_reason=reason,
            metadata={
                "corp_code": entry.company_id,
                "stock_code": entry.ticker,
                "industry": entry.industry,
                "source_id": entry.source_id,
                "source_tier": entry.source_tier,
            },
        )

    @classmethod
    def entry_for_company(cls, company: CompanyEntity) -> KrAnnualReportCatalogEntry | None:
        matches = cls.match_entries(company_name=company.company_name, ticker=company.ticker, company_id=company.company_id)
        return matches[0][0] if matches else None

    @classmethod
    def filing_candidate(cls, entry: KrAnnualReportCatalogEntry) -> FilingCandidate:
        document_url = entry.document_url or entry.landing_url or ""
        host = urlparse(document_url).netloc
        published_at = entry.published_at or entry.report_end
        title = entry.title or f"{entry.company_name} Business Report {entry.report_end.year}"
        return FilingCandidate(
            source_id=entry.source_id,
            source_name=entry.source_name,
            source_domain=host,
            market=Market.kr,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form="annual",
            title=title,
            accession_number=entry.company_id,
            primary_document=urlparse(document_url).path.rsplit("/", 1)[-1] or "business-report.html",
            report_end=entry.report_end,
            published_at=published_at,
            document_url=document_url,
            landing_url=entry.landing_url or document_url,
            file_format=entry.file_format,
            language=entry.language,
            metadata={
                "industry": entry.industry,
                "source_tier": entry.source_tier,
                "corp_code": entry.company_id,
                "stock_code": entry.ticker,
                "source_note": "Curated mainstream Korean company seed; DART public search resolves the latest statutory business report without an OpenDART API key.",
            },
        )

    @staticmethod
    def entry_for_url(document_url: str) -> KrAnnualReportCatalogEntry | None:
        normalized = str(document_url or "").strip()
        for entry in KR_ANNUAL_REPORT_CATALOG:
            if normalized and (entry.document_url == normalized or entry.landing_url == normalized):
                return entry
        return None

    @classmethod
    def source_hosts(cls) -> set[str]:
        hosts = {"dart.fss.or.kr", "opendart.fss.or.kr", "englishdart.fss.or.kr", "kind.krx.co.kr"}
        for entry in KR_ANNUAL_REPORT_CATALOG:
            for url in (entry.document_url, entry.landing_url):
                host = urlparse(url or "").netloc.lower()
                if host:
                    hosts.add(host)
        return hosts

    @classmethod
    def _score(
        cls,
        entry: KrAnnualReportCatalogEntry,
        *,
        normalized_identifier: str,
        normalized_query: str,
    ) -> tuple[float, str]:
        aliases = [entry.company_id, entry.ticker, entry.company_name, *entry.aliases]
        alias_keys = [cls._normalize(alias) for alias in aliases if cls._normalize(alias)]
        identifier_keys = {cls._normalize_identifier(entry.company_id), cls._normalize_identifier(entry.ticker)}
        if normalized_identifier:
            padded_identifier = normalized_identifier.zfill(6) if normalized_identifier.isdigit() and len(normalized_identifier) <= 6 else normalized_identifier
            if padded_identifier in identifier_keys or normalized_identifier in identifier_keys:
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
        text = re.sub(r"[^0-9A-Z]+", "", str(value or "").upper())
        return text.zfill(6) if text.isdigit() and len(text) <= 6 else text

    @staticmethod
    def _normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9가-힣\u4e00-\u9fff]+", "", str(value or "").lower())
