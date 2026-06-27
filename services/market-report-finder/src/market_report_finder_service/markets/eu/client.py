from __future__ import annotations

import re
import threading
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class EsefIndexClient:
    INDEX_URL = "https://filings.xbrl.org/index.json"
    BASE_URL = "https://filings.xbrl.org"
    SUPPORTED_COUNTRIES = {"GB", "FR", "DE", "NL", "CH"}
    ESEF_COUNTRIES = {"GB", "FR", "DE", "NL"}
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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._index_cache: dict | None = None
        self._index_loaded_at = 0.0

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        country: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        candidates = self._match_companies(
            query=company_name,
            ticker=ticker,
            company_id=company_id,
            country=country,
        )
        if not candidates:
            target_country = self.normalize_country(country)
            if target_country == "CH":
                raise ValueError("Swiss EU search currently supports official direct-download URLs; SIX search provider is not configured yet")
            raise ValueError(f"ESEF filing index did not match: {company_id or ticker or company_name or ''}")
        return candidates[0], candidates

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget,
        forms: list[str],
        include_earnings: bool,
        report_year: int | None = None,
    ) -> list[FilingCandidate]:
        del include_earnings
        country = self.normalize_country(company.metadata.get("country") or company.exchange)
        if country == "CH":
            return []
        index = self.index()
        row = index.get(company.company_id) or index.get(company.company_id.upper()) or index.get(company.company_id.lower())
        if not isinstance(row, dict):
            return []
        candidates = self._filing_candidates_for_row(company, row)
        allowed_families = self._allowed_families(target=target, forms=forms)
        filtered = [
            candidate
            for candidate in candidates
            if candidate.report_family in allowed_families
            and (report_year is None or candidate.report_end.year == report_year)
        ]
        return filtered

    def _match_companies(
        self,
        *,
        query: str | None,
        ticker: str | None,
        company_id: str | None,
        country: str | None,
    ) -> list[CompanyEntity]:
        target_country = self.normalize_country(country)
        raw_identifier = (company_id or ticker or "").strip()
        normalized_identifier = self._normalize_identifier(raw_identifier)
        normalized_query = self._normalize_text(query or raw_identifier)
        index = self.index()
        candidates: list[CompanyEntity] = []
        for lei, row in index.items():
            if not isinstance(row, dict):
                continue
            filings = row.get("filings") if isinstance(row.get("filings"), dict) else {}
            countries = {
                self.normalize_country(filing.get("country"))
                for filing in filings.values()
                if isinstance(filing, dict)
            }
            countries = {item for item in countries if item}
            if not countries & self.ESEF_COUNTRIES:
                continue
            if target_country and target_country not in countries:
                continue
            entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
            name = str(entity.get("name") or entity.get("entityName") or entity.get("label") or "")
            tickers = [
                str(value).strip().upper()
                for key in ("ticker", "tickers", "symbol", "symbols")
                for value in self._as_list(entity.get(key))
                if str(value).strip()
            ]
            aliases = [name, *tickers, str(lei)]
            score, reason = self._score_row(
                lei=str(lei),
                aliases=aliases,
                normalized_identifier=normalized_identifier,
                normalized_query=normalized_query,
            )
            if score < 0.55:
                continue
            primary_country = target_country or sorted(countries)[0]
            candidates.append(
                CompanyEntity(
                    market=Market.eu,
                    company_id=str(lei),
                    ticker=tickers[0] if tickers else ticker,
                    company_name=name or str(lei),
                    exchange=primary_country,
                    aliases=[alias for alias in aliases if alias],
                    confidence=score,
                    match_reason=reason,
                    metadata={
                        "country": primary_country,
                        "countries": sorted(countries),
                        "source_id": "xbrl_filings_esef",
                        "source_tier": "official_mirror",
                    },
                )
            )
        return sorted(candidates, key=lambda item: (item.confidence, item.company_name), reverse=True)[:10]

    def _filing_candidates_for_row(self, company: CompanyEntity, row: dict) -> list[FilingCandidate]:
        filings = row.get("filings") if isinstance(row.get("filings"), dict) else {}
        candidates: list[FilingCandidate] = []
        for filing_key, filing in filings.items():
            if not isinstance(filing, dict):
                continue
            country = self.normalize_country(filing.get("country"))
            if country not in self.ESEF_COUNTRIES:
                continue
            report_package = str(filing.get("report-package") or "").strip()
            report_date = self._parse_date(filing.get("date"))
            if not report_package or not report_date:
                continue
            filing_key_text = str(filing_key).strip("/")
            document_url = self._filing_url(filing_key_text, report_package)
            report_url = self._filing_url(filing_key_text, str(filing.get("report") or "")) if filing.get("report") else ""
            viewer_url = self._filing_url(filing_key_text, str(filing.get("viewer") or "")) if filing.get("viewer") else ""
            langs = [str(lang) for lang in self._as_list(filing.get("langs")) if str(lang)]
            added = self._parse_date(filing.get("added")) or report_date
            title = f"{company.company_name} ESEF annual financial report {report_date.year}"
            candidates.append(
                FilingCandidate(
                    source_id="xbrl_filings_esef",
                    source_name="filings.xbrl.org ESEF index",
                    source_domain="filings.xbrl.org",
                    market=Market.eu,
                    company_id=company.company_id,
                    ticker=company.ticker,
                    company_name=company.company_name,
                    report_type=ReportType.annual,
                    report_family=ReportFamily.annual,
                    form="ESEF",
                    title=title,
                    accession_number=filing_key_text,
                    primary_document=report_package,
                    report_end=report_date,
                    published_at=added,
                    document_url=document_url,
                    landing_url=viewer_url or report_url or document_url,
                    file_format="zip",
                    language=",".join(langs) if langs else None,
                    inline_xbrl=True,
                    metadata={
                        "country": country,
                        "country_label": self.country_label(country),
                        "source_tier": "official_mirror",
                        "source_note": "ESEF package mirrored from European OAM filings in the public XBRL filings index.",
                        "filing_key": filing_key_text,
                        "report_path": filing.get("report"),
                        "viewer_path": filing.get("viewer"),
                        "report_url": report_url,
                        "viewer_url": viewer_url,
                        "xbrl_json": filing.get("xbrl-json"),
                        "sha256sum": filing.get("sha256sum"),
                        "languages": langs,
                    },
                )
            )
        return sorted(candidates, key=lambda item: (item.report_end, item.published_at), reverse=True)

    def index(self) -> dict:
        now = time.monotonic()
        if self._index_cache is not None and now - self._index_loaded_at < 3600:
            return self._index_cache
        self._wait_for_slot()
        headers = {
            "User-Agent": settings.eu_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        with httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True) as client:
            response = client.get(self.INDEX_URL)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ESEF filing index returned an unexpected payload")
        self._index_cache = payload
        self._index_loaded_at = time.monotonic()
        return payload

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.eu_max_requests_per_second), 0.1)
        min_interval = 1.0 / max_rps
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + min_interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    @classmethod
    def normalize_country(cls, value: object) -> str | None:
        text = str(value or "").strip().upper()
        if not text:
            return None
        return cls.COUNTRY_ALIASES.get(text, text if text in cls.SUPPORTED_COUNTRIES else None)

    @staticmethod
    def country_label(country: str | None) -> str:
        return {
            "GB": "UK",
            "FR": "France",
            "DE": "Germany",
            "NL": "Netherlands",
            "CH": "Switzerland",
        }.get(country or "", country or "")

    @staticmethod
    def _allowed_families(*, target: ReportTarget, forms: list[str]) -> set[ReportFamily]:
        normalized = {str(form or "").strip().lower().replace("_", "-") for form in forms}
        if normalized and not normalized & {"annual", "annual-report", "esef", "afr"}:
            return set()
        if target in {ReportTarget.annual_report, ReportTarget.financial_report, ReportTarget.latest_report}:
            return {ReportFamily.annual}
        return set()

    @staticmethod
    def _filing_url(filing_key: str, relative_path: str) -> str:
        parts = [quote(part, safe="") for part in PurePosixPath(filing_key, relative_path).parts if part not in {"", "."}]
        return f"{EsefIndexClient.BASE_URL}/{'/'.join(parts)}"

    @staticmethod
    def _parse_date(value: object) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _score_row(
        *,
        lei: str,
        aliases: list[str],
        normalized_identifier: str,
        normalized_query: str,
    ) -> tuple[float, str]:
        lei_normalized = re.sub(r"[^a-z0-9]+", "", lei.lower())
        if normalized_identifier and normalized_identifier == lei_normalized:
            return 0.99, "lei_exact"
        if normalized_identifier:
            for alias in aliases:
                if normalized_identifier == re.sub(r"[^a-z0-9]+", "", alias.lower()):
                    return 0.94, "identifier_alias_exact"
        if not normalized_query:
            return -1.0, "empty_query"
        best = 0.0
        best_reason = "query_mismatch"
        for alias in aliases:
            alias_normalized = re.sub(r"[^a-z0-9]+", "", alias.lower())
            if not alias_normalized:
                continue
            if alias_normalized == normalized_query:
                return 0.96, "company_exact"
            if normalized_query in alias_normalized:
                best = max(best, 0.88)
                best_reason = "company_contains_query"
            elif alias_normalized in normalized_query:
                best = max(best, 0.82)
                best_reason = "query_contains_company"
            else:
                ratio = SequenceMatcher(None, normalized_query, alias_normalized).ratio()
                if ratio >= 0.72 and ratio > best:
                    best = 0.70 + (ratio - 0.72) * 0.3
                    best_reason = "company_fuzzy"
        return best, best_reason

    @staticmethod
    def _normalize_identifier(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        compact = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
        suffixes = (
            "publiclimitedcompany",
            "aktiengesellschaft",
            "societeeuropeenne",
            "societeanonyme",
            "naamlozevennootschap",
            "holding",
            "holdings",
            "limited",
            "plc",
            "ag",
            "se",
            "sa",
            "nv",
            "bv",
            "ltd",
        )
        for suffix in suffixes:
            if compact.endswith(suffix):
                compact = compact[: -len(suffix)]
        return compact

    @staticmethod
    def _as_list(value: object) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]
