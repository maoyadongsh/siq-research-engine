from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.data.foreign_aliases import foreign_alias_entry
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class SecClient:
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik_padded}.json"
    FILING_BASE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_no_zero}/{accession_no_dashes}"

    ANNUAL_FORMS = {"10-K", "20-F"}
    QUARTERLY_FORMS = {"10-Q"}
    FOREIGN_QUARTERLY_FORM = "6-K"
    SUPPORTED_BASE_FORMS = {"10-K", "10-Q", "20-F", "6-K"}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        cik: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        if cik:
            entity = self.company_from_cik(cik, fallback_name=company_name, fallback_ticker=ticker)
            return entity, [entity]

        payload = self.company_tickers()
        candidates = self._company_candidates_from_ticker_payload(
            payload,
            company_name=company_name,
            ticker=ticker,
        )
        if not candidates:
            query = ticker or company_name or ""
            raise ValueError(f"SEC company ticker catalog did not match: {query}")
        return candidates[0], candidates

    def company_tickers(self) -> dict:
        return self._get_json(self.COMPANY_TICKERS_URL, host="www.sec.gov")

    def company_from_cik(
        self,
        cik: str,
        *,
        fallback_name: str | None = None,
        fallback_ticker: str | None = None,
    ) -> CompanyEntity:
        cik_clean = self._normalize_cik(cik)
        payload = self.submissions(cik_clean)
        tickers = payload.get("tickers") or []
        exchanges = payload.get("exchanges") or []
        return CompanyEntity(
            market=Market.us,
            company_id=cik_clean,
            cik=cik_clean,
            cik_padded=cik_clean.zfill(10),
            ticker=(fallback_ticker or (tickers[0] if tickers else None)),
            company_name=payload.get("name") or fallback_name or f"CIK {cik_clean}",
            exchange=exchanges[0] if exchanges else None,
            aliases=[fallback_name] if fallback_name else [],
            confidence=0.99,
            match_reason="sec_submissions_cik",
        )

    def submissions(self, cik: str) -> dict:
        cik_padded = self._normalize_cik(cik).zfill(10)
        url = self.SUBMISSIONS_URL.format(cik_padded=cik_padded)
        return self._get_json(url, host="data.sec.gov")

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget = ReportTarget.financial_report,
        forms: list[str] | None = None,
        include_amendments: bool = False,
    ) -> list[FilingCandidate]:
        payload = self.submissions(company.cik)
        candidates = self._build_candidates_from_submissions(company, payload)
        allowed_forms = self._allowed_forms(target=target, forms=forms or [])
        filtered = [
            candidate
            for candidate in candidates
            if candidate.report_type.value in allowed_forms
            and (include_amendments or not candidate.form.endswith("/A"))
        ]
        if ReportType.form_6k.value in allowed_forms:
            filtered = [
                candidate
                for candidate in filtered
                if candidate.report_type != ReportType.form_6k or self._looks_like_quarterly_6k(candidate)
            ]
        return filtered

    def _build_candidates_from_submissions(
        self,
        company: CompanyEntity,
        payload: dict,
    ) -> list[FilingCandidate]:
        recent = payload.get("filings", {}).get("recent", {})
        return self._build_candidates_from_recent_payload(company, payload.get("name"), recent)

    def _build_candidates_from_recent_payload(
        self,
        company: CompanyEntity,
        payload_company_name: str | None,
        recent: dict,
    ) -> list[FilingCandidate]:
        forms = recent.get("form") or []
        accession_numbers = recent.get("accessionNumber") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        primary_documents = recent.get("primaryDocument") or []
        primary_descriptions = recent.get("primaryDocDescription") or []
        accepted_times = recent.get("acceptanceDateTime") or []
        inline_xbrl_values = recent.get("isInlineXBRL") or []

        candidates: list[FilingCandidate] = []
        for index, raw_form in enumerate(forms):
            report_type = self._map_form_to_type(raw_form)
            if report_type is None:
                continue
            accession_number = self._item_at(accession_numbers, index)
            filing_date_raw = self._item_at(filing_dates, index)
            primary_document = self._item_at(primary_documents, index)
            if not accession_number or not filing_date_raw or not primary_document:
                continue

            filing_date = date.fromisoformat(filing_date_raw)
            report_date_raw = self._item_at(report_dates, index)
            report_end = date.fromisoformat(report_date_raw) if report_date_raw else filing_date
            accepted_at = self._parse_sec_datetime(self._item_at(accepted_times, index))
            description = self._item_at(primary_descriptions, index) or ""
            accession_no_dashes = accession_number.replace("-", "")
            cik_no_zero = str(int(company.cik))
            base_url = self.FILING_BASE_URL.format(
                cik_no_zero=cik_no_zero,
                accession_no_dashes=accession_no_dashes,
            )
            title = description or f"{payload_company_name or company.company_name} {raw_form} filed {filing_date}"
            candidates.append(
                FilingCandidate(
                    source_id="sec",
                    source_name="SEC EDGAR",
                    source_domain="sec.gov",
                    market=Market.us,
                    company_id=company.company_id,
                    cik=company.cik,
                    ticker=company.ticker,
                    company_name=payload_company_name or company.company_name,
                    report_type=report_type,
                    report_family=self._report_family(report_type),
                    form=raw_form,
                    title=title,
                    accession_number=accession_number,
                    primary_document=primary_document,
                    report_end=report_end,
                    published_at=filing_date,
                    accepted_at=accepted_at,
                    document_url=f"{base_url}/{primary_document}",
                    landing_url=f"{base_url}/{accession_number}-index.html",
                    file_format=self._file_format(primary_document),
                    inline_xbrl=self._parse_boolish(self._item_at(inline_xbrl_values, index)),
                    metadata={
                        "sec_submission_name": payload_company_name,
                        "accession_no_dashes": accession_no_dashes,
                    },
                )
            )
        return candidates

    def _company_candidates_from_ticker_payload(
        self,
        payload: dict,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
    ) -> list[CompanyEntity]:
        alias = foreign_alias_entry(Market.us.value, company_name) if company_name and not ticker else None
        if alias and not ticker:
            ticker = str(alias.get("ticker") or "") or None
        normalized_query = self._normalize_company_name(company_name or "")
        normalized_ticker = self._normalize_ticker(ticker)
        candidates: list[CompanyEntity] = []

        rows = payload.values() if isinstance(payload, dict) else payload
        for row in rows:
            row_ticker = str(row.get("ticker") or "").upper()
            row_title = str(row.get("title") or "")
            row_cik = self._normalize_cik(str(row.get("cik_str") or ""))
            if not row_ticker or not row_title or not row_cik:
                continue
            score, reason = self._score_company_row(
                row_ticker=row_ticker,
                row_title=row_title,
                normalized_ticker=normalized_ticker,
                normalized_query=normalized_query,
            )
            if score < 0.55:
                continue
            candidates.append(
                CompanyEntity(
                    market=Market.us,
                    company_id=row_cik,
                    cik=row_cik,
                    cik_padded=row_cik.zfill(10),
                    ticker=row_ticker,
                    company_name=row_title,
                    exchange=None,
                    aliases=[company_name] if company_name else [],
                    confidence=score,
                    match_reason=reason,
                )
            )

        return sorted(candidates, key=lambda item: (item.confidence, item.ticker or ""), reverse=True)[:10]

    def _score_company_row(
        self,
        *,
        row_ticker: str,
        row_title: str,
        normalized_ticker: str | None,
        normalized_query: str,
    ) -> tuple[float, str]:
        if normalized_ticker:
            return (0.99, "sec_ticker_exact") if row_ticker == normalized_ticker else (-1.0, "ticker_mismatch")
        if not normalized_query:
            return -1.0, "empty_query"

        row_normalized = self._normalize_company_name(row_title)
        if row_normalized == normalized_query:
            return 0.96, "sec_company_exact"
        if normalized_query in row_normalized:
            return 0.88, "sec_company_contains_query"
        if row_normalized in normalized_query:
            return 0.84, "sec_query_contains_company"
        ratio = SequenceMatcher(None, normalized_query, row_normalized).ratio()
        if ratio >= 0.72:
            return 0.70 + (ratio - 0.72) * 0.3, "sec_company_fuzzy"
        return -1.0, "company_mismatch"

    @classmethod
    def _allowed_forms(cls, *, target: ReportTarget, forms: list[str]) -> set[str]:
        explicit = {cls._base_form(form) for form in forms if cls._base_form(form)}
        if explicit:
            return explicit
        if target == ReportTarget.annual_report:
            return set(cls.ANNUAL_FORMS)
        if target == ReportTarget.quarterly_report:
            return {*cls.QUARTERLY_FORMS, cls.FOREIGN_QUARTERLY_FORM}
        return {*cls.ANNUAL_FORMS, *cls.QUARTERLY_FORMS, cls.FOREIGN_QUARTERLY_FORM}

    @classmethod
    def _map_form_to_type(cls, form: str) -> ReportType | None:
        base_form = cls._base_form(form)
        mapping = {
            "10-K": ReportType.form_10k,
            "10-Q": ReportType.form_10q,
            "20-F": ReportType.form_20f,
            "6-K": ReportType.form_6k,
        }
        return mapping.get(base_form)

    @classmethod
    def _base_form(cls, form: str) -> str | None:
        normalized = str(form or "").strip().upper()
        if normalized.endswith("/A"):
            normalized = normalized[:-2]
        aliases = {
            "ANNUAL": "10-K",
            "QUARTERLY": "10-Q",
        }
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in cls.SUPPORTED_BASE_FORMS else None

    @staticmethod
    def _report_family(report_type: ReportType) -> ReportFamily:
        if report_type in {ReportType.form_10k, ReportType.form_20f}:
            return ReportFamily.annual
        if report_type in {ReportType.form_10q, ReportType.form_6k}:
            return ReportFamily.quarterly
        return ReportFamily.current

    @staticmethod
    def _looks_like_quarterly_6k(candidate: FilingCandidate) -> bool:
        joined = f"{candidate.title} {candidate.primary_document}".lower()
        keywords = (
            "quarter",
            "quarterly",
            "q1",
            "q2",
            "q3",
            "q4",
            "interim",
            "financial results",
            "results",
            "earnings",
        )
        return any(keyword in joined for keyword in keywords)

    def _get_json(self, url: str, *, host: str) -> dict:
        self._wait_for_slot()
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": host,
        }
        with httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.sec_max_requests_per_second), 0.1)
        min_interval = 1.0 / max_rps
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + min_interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _normalize_company_name(text: str) -> str:
        compact = re.sub(r"[^a-z0-9]+", "", text.lower())
        suffixes = (
            "incorporated",
            "corporation",
            "company",
            "holdings",
            "holding",
            "limited",
            "inc",
            "corp",
            "co",
            "ltd",
            "plc",
        )
        for suffix in suffixes:
            if compact.endswith(suffix):
                compact = compact[: -len(suffix)]
        return compact

    @staticmethod
    def _normalize_ticker(ticker: str | None) -> str | None:
        if not ticker:
            return None
        normalized = ticker.strip().upper().replace("-", ".")
        normalized = re.sub(r"^(NASDAQ|NYSE|AMEX|US)[:.\s]+", "", normalized)
        return normalized or None

    @staticmethod
    def _normalize_cik(cik: str) -> str:
        digits = re.sub(r"\D+", "", str(cik or ""))
        if not digits:
            raise ValueError("CIK must contain digits")
        return str(int(digits))

    @staticmethod
    def _item_at(items: list, index: int):
        return items[index] if index < len(items) else None

    @staticmethod
    def _parse_sec_datetime(raw: str | None) -> datetime | None:
        if not raw:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_boolish(value) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
        return None

    @staticmethod
    def _file_format(primary_document: str) -> str:
        path = urlparse(primary_document).path
        if "." not in path:
            return "html"
        suffix = path.rsplit(".", 1)[-1].lower()
        return "html" if suffix == "htm" else suffix
