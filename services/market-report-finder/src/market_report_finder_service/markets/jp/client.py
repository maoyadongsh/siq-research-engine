from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import (
    CompanyEntity,
    FilingCandidate,
    Market,
    ReportFamily,
    ReportTarget,
    ReportType,
)


class EdinetClient:
    DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
    DOCUMENT_DOWNLOAD_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

    ANNUAL_FORM_CODES = {"030000"}
    SEMIANNUAL_FORM_CODES = {"050000"}
    QUARTERLY_FORM_CODES = {"043000", "044000"}
    COMMON_COMPANIES: tuple[dict[str, Any], ...] = (
        {
            "ticker": "72030",
            "company_id": "E02144",
            "company_name": "トヨタ自動車株式会社",
            "aliases": ("丰田", "丰田汽车", "toyota", "toyota motor"),
        },
        {
            "ticker": "67580",
            "company_id": "E01777",
            "company_name": "ソニーグループ株式会社",
            "aliases": ("索尼", "sony", "sony group"),
        },
        {
            "ticker": "79740",
            "company_id": "7974",
            "company_name": "任天堂株式会社",
            "aliases": ("任天堂", "nintendo"),
        },
        {
            "ticker": "285A0",
            "company_id": "285A",
            "company_name": "キオクシアホールディングス株式会社",
            "aliases": ("铠侠", "鎧俠", "铠侠控股", "kioxia", "kioxia holdings"),
        },
        {
            "ticker": "63020",
            "company_id": "6302",
            "company_name": "住友重機械工業株式会社",
            "aliases": ("住友重工", "住友重机械", "住友重機械", "sumitomo heavy", "sumitomo heavy industries"),
        },
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._document_rows_cache: dict[str, list[dict]] = {}

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        query = company_name or ticker or company_id or ""
        fallback = self._offline_company(company_name=company_name, ticker=ticker, company_id=company_id)
        if fallback:
            return fallback, [fallback]
        rows = self._scan_recent_rows(days=370)
        candidates = self._company_candidates(rows, company_name=company_name, ticker=ticker, company_id=company_id)
        if not candidates:
            raise ValueError(f"EDINET document catalog did not match: {query}")
        return candidates[0], candidates

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget = ReportTarget.financial_report,
        forms: list[str] | None = None,
        include_earnings: bool = False,
        report_year: int | None = None,
    ) -> list[FilingCandidate]:
        del include_earnings
        allowed = self._allowed_types(target=target, forms=forms or [])
        if report_year and allowed == {ReportType.annual}:
            seen_dates: set[date] = set()
            for start, end in self._company_filing_windows(company, allowed=allowed, report_year=report_year):
                candidates = self._scan_window_for_first_match(
                    company,
                    start,
                    end,
                    allowed=allowed,
                    seen_dates=seen_dates,
                )
                if candidates:
                    return self._dedupe_candidates(candidates)
            return []
        rows = (
            self._scan_company_year_window(company, allowed=allowed, report_year=report_year)
            if report_year
            else self._scan_recent_rows(days=460)
        )
        return self._candidates_from_rows(company, rows, allowed)

    def _candidates_from_rows(
        self,
        company: CompanyEntity,
        rows: list[dict],
        allowed: set[ReportType],
    ) -> list[FilingCandidate]:
        candidates: list[FilingCandidate] = []
        for row in rows:
            if not self._row_matches_company(row, company):
                continue
            report_type, family = self._infer_report_type(row)
            if report_type not in allowed:
                continue
            assert report_type is not None
            candidate = self._build_candidate(company, row, report_type, family)
            if candidate:
                candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def _build_candidate(
        self,
        company: CompanyEntity,
        row: dict,
        report_type: ReportType,
        family: ReportFamily,
    ) -> FilingCandidate | None:
        doc_id = str(row.get("docID") or "").strip()
        title = str(row.get("docDescription") or "").strip()
        submit_date_raw = str(row.get("submitDateTime") or row.get("submitDate") or "").strip()
        if not doc_id or not title or not submit_date_raw:
            return None
        published_at = self._parse_submit_date(submit_date_raw)
        report_end = self._parse_period_end(row) or self._infer_report_end(title=title, report_type=report_type, published_at=published_at)
        return FilingCandidate(
            source_id="edinet",
            source_name="EDINET",
            source_domain="api.edinet-fsa.go.jp",
            market=Market.jp,
            company_id=company.company_id,
            ticker=company.ticker,
            company_name=company.company_name,
            report_type=report_type,
            report_family=family,
            form="yuho" if report_type == ReportType.annual else report_type.value,
            title=title,
            accession_number=doc_id,
            primary_document=f"{doc_id}.pdf",
            report_end=report_end,
            published_at=published_at,
            document_url=self._download_url(doc_id, "2"),
            landing_url=f"https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID={doc_id}",
            file_format="pdf",
            language="ja",
            metadata={
                "doc_id": doc_id,
                "edinet_code": row.get("edinetCode"),
                "sec_code": row.get("secCode"),
                "ordinance_code": row.get("ordinanceCode"),
                "form_code": row.get("formCode"),
                "xbrl_download_url": self._download_url(doc_id, "1"),
            },
        )

    def _scan_recent_rows(self, *, days: int) -> list[dict]:
        today = date.today()
        rows: list[dict] = []
        for offset in range(days):
            target_date = today - timedelta(days=offset)
            rows.extend(self._document_rows(target_date))
        return rows

    def _scan_company_year_window(self, company: CompanyEntity, *, allowed: set[ReportType], report_year: int) -> list[dict]:
        rows: list[dict] = []
        seen_dates: set[date] = set()
        for start, end in self._company_filing_windows(company, allowed=allowed, report_year=report_year):
            for row in self._scan_date_range(start, end, seen_dates=seen_dates):
                rows.append(row)
        return rows

    def _scan_date_range(self, start: date, end: date, *, seen_dates: set[date] | None = None) -> list[dict]:
        end = min(end, date.today())
        if end < start:
            return []
        rows: list[dict] = []
        days = (end - start).days + 1
        for offset in range(days):
            target_date = start + timedelta(days=offset)
            if seen_dates is not None:
                if target_date in seen_dates:
                    continue
                seen_dates.add(target_date)
            rows.extend(self._document_rows(target_date))
        return rows

    def _scan_window_for_first_match(
        self,
        company: CompanyEntity,
        start: date,
        end: date,
        *,
        allowed: set[ReportType],
        seen_dates: set[date] | None = None,
    ) -> list[FilingCandidate]:
        """Search likely filing dates first and stop once the issuer is found.

        EDINET exposes a date-based document catalog, so scanning a full filing
        window before filtering turns a normal lookup into a multi-minute
        request. Annual securities reports are concentrated around the middle
        of the filing window; center-out ordering preserves the full fallback
        range while making common issuers fast.
        """
        end = min(end, date.today())
        if end < start:
            return []
        dates = self._center_out_dates(start, end)
        for target_date in dates:
            if seen_dates is not None:
                if target_date in seen_dates:
                    continue
                seen_dates.add(target_date)
            candidates = self._candidates_from_rows(
                company,
                self._document_rows(target_date),
                allowed,
            )
            if candidates:
                return candidates
        return []

    @staticmethod
    def _center_out_dates(start: date, end: date) -> list[date]:
        days = (end - start).days + 1
        # Tie-break toward the earlier day for even-sized ranges. This keeps
        # the result deterministic and avoids duplicate dates.
        offsets = sorted(range(days), key=lambda offset: (abs(2 * offset - (days - 1)), offset))
        return [start + timedelta(days=offset) for offset in offsets]

    def _company_filing_windows(
        self,
        company: CompanyEntity,
        *,
        allowed: set[ReportType],
        report_year: int,
    ) -> list[tuple[date, date]]:
        catalog_report_end = self._parse_iso_date(company.metadata.get("catalog_report_end"))
        if catalog_report_end and catalog_report_end.year == report_year:
            if allowed == {ReportType.annual}:
                windows: list[tuple[date, date]] = []
                catalog_published_at = self._parse_iso_date(company.metadata.get("catalog_published_at"))
                if catalog_published_at:
                    days_after_period_end = (catalog_published_at - catalog_report_end).days
                    if 45 <= days_after_period_end <= 160:
                        windows.append((catalog_published_at - timedelta(days=14), catalog_published_at + timedelta(days=14)))
                windows.append((catalog_report_end + timedelta(days=45), catalog_report_end + timedelta(days=130)))
                return windows
            return [(catalog_report_end + timedelta(days=1), catalog_report_end + timedelta(days=210))]

        if allowed == {ReportType.annual}:
            return [
                (date(report_year, 4, 1), date(report_year, 8, 31)),
                (date(report_year, 8, 1), date(report_year + 1, 1, 31)),
                (date(report_year + 1, 2, 1), date(report_year + 1, 7, 31)),
            ]

        return [
            (date(report_year, 1, 1), date(report_year, 12, 31)),
            (date(report_year + 1, 1, 1), date(report_year + 1, 7, 31)),
        ]

    def _document_rows(self, target_date: date) -> list[dict]:
        cache_key = target_date.isoformat()
        if cache_key in self._document_rows_cache:
            return self._document_rows_cache[cache_key]
        if not settings.edinet_api_key:
            raise ValueError("EDINET_API_KEY is required for Japanese market report search")
        params = {"date": target_date.isoformat(), "type": "2"}
        params["Subscription-Key"] = settings.edinet_api_key
        payload = self._get_json(self.DOCUMENTS_URL, params=params)
        rows = payload.get("results") or []
        parsed_rows = [row for row in rows if isinstance(row, dict)]
        self._document_rows_cache[cache_key] = parsed_rows
        return parsed_rows

    def _offline_company(
        self,
        *,
        company_name: str | None,
        ticker: str | None,
        company_id: str | None,
    ) -> CompanyEntity | None:
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_company_id = self._normalize_edinet_code(company_id)
        normalized_company_id_ticker = self._normalize_ticker_from_identifier(company_id)
        normalized_name = self._normalize_name(company_name or "")
        for item in self.COMMON_COMPANIES:
            aliases = tuple(str(alias) for alias in item.get("aliases", ()))
            alias_names = [self._normalize_name(alias) for alias in aliases]
            company_normalized = self._normalize_name(str(item["company_name"]))
            item_ticker = str(item["ticker"]).upper()
            item_company_id = str(item["company_id"]).upper()
            if normalized_ticker and normalized_ticker != item_ticker:
                continue
            if normalized_company_id and normalized_company_id != item_company_id:
                continue
            if normalized_company_id_ticker and normalized_company_id_ticker != item_ticker:
                continue
            if normalized_name and normalized_name not in company_normalized and company_normalized not in normalized_name:
                if not any(alias and (alias in normalized_name or normalized_name in alias) for alias in alias_names):
                    continue
            return CompanyEntity(
                market=Market.jp,
                company_id=str(item["company_id"]),
                ticker=str(item["ticker"]),
                company_name=str(item["company_name"]),
                exchange="JPX",
                aliases=[company_name] if company_name else list(aliases),
                confidence=0.93,
                match_reason="offline_common_company",
                metadata={
                    "edinet_code": item["company_id"] if str(item["company_id"]).startswith("E") else None,
                    "sec_code": item["ticker"],
                    "offline_catalog": True,
                },
            )
        return None

    def _company_candidates(
        self,
        rows: list[dict],
        *,
        company_name: str | None,
        ticker: str | None,
        company_id: str | None,
    ) -> list[CompanyEntity]:
        normalized_query = self._normalize_name(company_name or "")
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_company_id = self._normalize_edinet_code(company_id)
        normalized_company_id_ticker = self._normalize_ticker_from_identifier(company_id)
        best_by_key: dict[str, CompanyEntity] = {}
        for row in rows:
            edinet_code = str(row.get("edinetCode") or "").strip().upper()
            raw_sec_code = str(row.get("secCode") or "").strip()
            sec_code = self._normalize_ticker(raw_sec_code) or raw_sec_code
            filer_name = str(row.get("filerName") or "").strip()
            if not edinet_code or not filer_name:
                continue
            score, reason = self._score_company_row(
                edinet_code=edinet_code,
                sec_code=sec_code,
                filer_name=filer_name,
                normalized_company_id=normalized_company_id,
                normalized_company_id_ticker=normalized_company_id_ticker,
                normalized_ticker=normalized_ticker,
                normalized_query=normalized_query,
            )
            if score < 0.55:
                continue
            entity = CompanyEntity(
                market=Market.jp,
                company_id=edinet_code,
                ticker=sec_code or None,
                company_name=filer_name,
                exchange="JPX" if sec_code else None,
                aliases=[company_name] if company_name else [],
                confidence=score,
                match_reason=reason,
                metadata={
                    "edinet_code": edinet_code,
                    "sec_code": sec_code or None,
                    "fund_code": row.get("fundCode"),
                    "ordinance_code": row.get("ordinanceCode"),
                    "form_code": row.get("formCode"),
                },
            )
            existing = best_by_key.get(edinet_code)
            if existing is None or entity.confidence > existing.confidence:
                best_by_key[edinet_code] = entity
        return sorted(best_by_key.values(), key=lambda item: (item.confidence, item.ticker or ""), reverse=True)[:10]

    @classmethod
    def _allowed_types(cls, *, target: ReportTarget, forms: list[str]) -> set[ReportType]:
        explicit = {cls._form_to_report_type(form) for form in forms if cls._form_to_report_type(form)}
        if explicit:
            return explicit
        if target == ReportTarget.annual_report:
            return {ReportType.annual}
        if target == ReportTarget.semiannual_report:
            return {ReportType.semiannual}
        if target == ReportTarget.quarterly_report:
            return {ReportType.quarterly}
        return {ReportType.annual, ReportType.semiannual, ReportType.quarterly}

    @staticmethod
    def _form_to_report_type(form: str) -> ReportType | None:
        normalized = form.strip().lower().replace("_", "-")
        mapping = {
            "annual": ReportType.annual,
            "annual-report": ReportType.annual,
            "yuho": ReportType.annual,
            "securities-report": ReportType.annual,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "half-year": ReportType.semiannual,
            "interim": ReportType.semiannual,
            "quarterly": ReportType.quarterly,
            "quarterly-report": ReportType.quarterly,
            "q1": ReportType.quarterly,
            "q2": ReportType.quarterly,
            "q3": ReportType.quarterly,
        }
        return mapping.get(normalized)

    @classmethod
    def _infer_report_type(cls, row: dict) -> tuple[ReportType | None, ReportFamily]:
        form_code = str(row.get("formCode") or "")
        title = str(row.get("docDescription") or "").lower()
        if form_code in cls.ANNUAL_FORM_CODES or "有価証券報告書" in title:
            return ReportType.annual, ReportFamily.annual
        if form_code in cls.QUARTERLY_FORM_CODES or "四半期報告書" in title:
            return ReportType.quarterly, ReportFamily.quarterly
        if form_code in cls.SEMIANNUAL_FORM_CODES or "半期報告書" in title:
            return ReportType.semiannual, ReportFamily.semiannual
        return None, ReportFamily.current

    @staticmethod
    def _score_company_row(
        *,
        edinet_code: str,
        sec_code: str,
        filer_name: str,
        normalized_company_id: str | None,
        normalized_company_id_ticker: str | None,
        normalized_ticker: str | None,
        normalized_query: str,
    ) -> tuple[float, str]:
        if normalized_company_id:
            return (0.99, "edinet_code_exact") if edinet_code == normalized_company_id else (-1.0, "company_id_mismatch")
        if normalized_company_id_ticker:
            return (0.99, "edinet_sec_code_from_company_id") if sec_code == normalized_company_id_ticker else (-1.0, "company_id_ticker_mismatch")
        if normalized_ticker:
            return (0.99, "edinet_sec_code_exact") if sec_code == normalized_ticker else (-1.0, "ticker_mismatch")
        if not normalized_query:
            return -1.0, "empty_query"
        row_normalized = EdinetClient._normalize_name(filer_name)
        if row_normalized == normalized_query:
            return 0.96, "edinet_company_exact"
        if normalized_query in row_normalized:
            return 0.88, "edinet_company_contains_query"
        if row_normalized in normalized_query:
            return 0.84, "edinet_query_contains_company"
        ratio = SequenceMatcher(None, normalized_query, row_normalized).ratio()
        if ratio >= 0.72:
            return 0.70 + (ratio - 0.72) * 0.3, "edinet_company_fuzzy"
        return -1.0, "company_mismatch"

    @classmethod
    def _row_matches_company(cls, row: dict, company: CompanyEntity) -> bool:
        edinet_code = str(row.get("edinetCode") or "").strip().upper()
        sec_code = cls._normalize_ticker(str(row.get("secCode") or "").strip())
        company_ids = {
            str(company.company_id or "").strip().upper(),
            str(company.metadata.get("edinet_code") or "").strip().upper(),
        }
        company_ids.discard("")
        if edinet_code and edinet_code in company_ids:
            return True
        normalized_tickers = {
            cls._normalize_ticker(company.ticker),
            cls._normalize_ticker(company.company_id),
            cls._normalize_ticker(company.metadata.get("sec_code")),
        }
        normalized_tickers.discard(None)
        return bool(sec_code and sec_code in normalized_tickers)

    @staticmethod
    def _parse_submit_date(raw: str) -> date:
        text = raw.strip()
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text[: len(fmt)], fmt).date()
            except ValueError:
                continue
        return date.fromisoformat(text[:10])

    @staticmethod
    def _parse_period_end(row: dict) -> date | None:
        for key in ("periodEnd", "currentPeriodEndDate"):
            raw = str(row.get(key) or "").strip()
            if not raw:
                continue
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_iso_date(value: object) -> date | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    @staticmethod
    def _infer_report_end(title: str, report_type: ReportType, published_at: date) -> date:
        date_matches = re.findall(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", title)
        if date_matches:
            year, month, day = date_matches[-1]
            try:
                return date(int(year), int(month), int(day))
            except ValueError:
                pass
        year_match = re.search(r"20\d{2}", title)
        year = int(year_match.group(0)) if year_match else published_at.year
        if report_type == ReportType.annual:
            return date(year, 3, 31)
        if report_type == ReportType.semiannual:
            return date(year, 9, 30)
        if "第3四半期" in title or "third" in title.lower() or "q3" in title.lower():
            return date(year, 12, 31)
        if "第2四半期" in title or "second" in title.lower() or "q2" in title.lower():
            return date(year, 9, 30)
        if "第1四半期" in title or "first" in title.lower() or "q1" in title.lower():
            return date(year, 6, 30)
        return published_at

    @staticmethod
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_doc_id: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            key = candidate.accession_number or candidate.document_url
            by_doc_id.setdefault(key, candidate)
        return list(by_doc_id.values())

    def _download_url(self, doc_id: str, file_type: str) -> str:
        url = self.DOCUMENT_DOWNLOAD_URL.format(doc_id=doc_id)
        params = [f"type={file_type}"]
        return f"{url}?{'&'.join(params)}"

    def _get_json(self, url: str, *, params: dict[str, str]) -> dict:
        last_rate_limited = False
        for attempt in range(4):
            self._wait_for_slot()
            with self._client() as client:
                response = client.get(url, params=params)
            if response.status_code == 429:
                last_rate_limited = True
                time.sleep(self._retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                preview = response.text.replace("\n", " ")[:160]
                raise ValueError(f"EDINET API returned non-JSON response. Check EDINET_API_KEY/subscription. Preview: {preview}")
            return response.json()
        if last_rate_limited:
            raise ValueError("EDINET API rate limit reached (HTTP 429). Please retry after a longer interval.")
        raise ValueError("EDINET API request failed")

    @staticmethod
    def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 1.0), 120.0)
            except ValueError:
                pass
        return min(60.0, 2.0 * (attempt + 1) ** 2)

    @staticmethod
    def _client() -> httpx.Client:
        headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.edinet_max_requests_per_second), 0.1)
        min_interval = 1.0 / max_rps
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + min_interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _normalize_ticker(ticker: str | None) -> str | None:
        if not ticker:
            return None
        text = str(ticker).strip().upper()
        if ":" in text:
            _, text = text.split(":", 1)
        code = re.sub(r"[^0-9A-Z]+", "", text)
        if code.startswith("JP") and len(code) > 2:
            code = code[2:]
        if re.fullmatch(r"E\d{5}", code):
            return None
        if re.fullmatch(r"\d{4}0|\d{3}[A-Z]0", code):
            return code
        if re.fullmatch(r"\d{4}|\d{3}[A-Z]", code):
            return f"{code}0"
        if re.fullmatch(r"\d{4}[0-9A-Z]+|\d{3}[A-Z][0-9A-Z]+", code):
            return f"{code[:4]}0"
        return code or None

    @staticmethod
    def _normalize_edinet_code(value: str | None) -> str | None:
        text = str(value or "").strip().upper()
        if ":" in text:
            _, text = text.split(":", 1)
        code = re.sub(r"[^0-9A-Z]+", "", text)
        return code if re.fullmatch(r"E\d{5}", code) else None

    @classmethod
    def _normalize_ticker_from_identifier(cls, value: str | None) -> str | None:
        if cls._normalize_edinet_code(value):
            return None
        return cls._normalize_ticker(value)

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9ぁ-んァ-ン一-龥\u4e00-\u9fff]+", "", text.lower())
