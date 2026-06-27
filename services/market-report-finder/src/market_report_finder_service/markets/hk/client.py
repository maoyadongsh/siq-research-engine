from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from html import unescape

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


HK_COMPANY_ALIASES = {
    "美团": "03690",
    "美团w": "03690",
    "美团点评": "03690",
    "meituan": "03690",
    "meituanw": "03690",
}


class HkexClient:
    ACTIVE_STOCK_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
    ACTIVE_STOCK_ZH_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_c.json"
    INACTIVE_STOCK_URL = "https://www1.hkexnews.hk/ncms/script/eds/inactivestock_sehk_e.json"
    INACTIVE_STOCK_ZH_URL = "https://www1.hkexnews.hk/ncms/script/eds/inactivestock_sehk_c.json"
    TITLE_SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        normalized_query = self._normalize_name(company_name or "")
        alias_ticker = HK_COMPANY_ALIASES.get(normalized_query)
        needs_localized_names = bool(company_name and not (ticker or company_id or alias_ticker))
        active_rows = self._stock_rows(
            self.ACTIVE_STOCK_URL,
            status="active",
            localized_rows=self._stock_rows(self.ACTIVE_STOCK_ZH_URL, status="active", language="zh") if needs_localized_names else None,
        )
        inactive_rows = self._stock_rows(
            self.INACTIVE_STOCK_URL,
            status="inactive",
            localized_rows=self._stock_rows(self.INACTIVE_STOCK_ZH_URL, status="inactive", language="zh") if needs_localized_names else None,
        )
        rows = [*active_rows, *inactive_rows]
        candidates = self._company_candidates_from_rows(
            rows,
            company_name=company_name,
            ticker=ticker or company_id,
        )
        if not candidates:
            query = ticker or company_id or company_name or ""
            raise ValueError(f"HKEX stock catalog did not match: {query}")
        return candidates[0], candidates

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget = ReportTarget.financial_report,
        forms: list[str] | None = None,
        include_earnings: bool = False,
    ) -> list[FilingCandidate]:
        if not company.hkex_stock_id:
            raise ValueError(f"{company.company_name} missing HKEX stock id")
        payload = self._query_title_search(company.hkex_stock_id, company.ticker or company.company_id)
        candidates = self._build_candidates(company, payload)
        allowed_types = self._allowed_types(target=target, forms=forms or [], include_earnings=include_earnings)
        return [candidate for candidate in candidates if candidate.report_type in allowed_types]

    def _stock_rows(
        self,
        url: str,
        status: str = "unknown",
        *,
        language: str = "en",
        localized_rows: list[dict] | None = None,
    ) -> list[dict]:
        self._wait_for_slot()
        with self._client() as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        rows = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["_status"] = status
            item["_language"] = language
            if localized_rows:
                item.update(self._localized_names_for_row(row, localized_rows))
            rows.append(item)
        return rows

    @staticmethod
    def _localized_names_for_row(row: dict, localized_rows: list[dict]) -> dict[str, object]:
        code = str(row.get("c") or "").zfill(5)
        if not code:
            return {}
        for localized in localized_rows:
            if str(localized.get("c") or "").zfill(5) != code:
                continue
            name = HkexClient._clean_text(str(localized.get("n") or "").strip())
            if not name:
                return {}
            return {"_localized_names": [name]}
        return {}

    def _query_title_search(self, stock_id: str, stock_code: str) -> dict:
        params = {
            "lang": "EN",
            "stockId": stock_id,
            "stockCode": stock_code,
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "documentType": "",
            "fromDate": "20190101",
            "toDate": "20301231",
            "title": "",
            "searchType": "1",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": "200",
        }
        self._wait_for_slot()
        with self._client() as client:
            response = client.get(self.TITLE_SEARCH_URL, params=params)
            response.raise_for_status()
            return response.json()

    def _company_candidates_from_rows(
        self,
        rows: list[dict],
        *,
        company_name: str | None = None,
        ticker: str | None = None,
    ) -> list[CompanyEntity]:
        normalized_query = self._normalize_name(company_name or "")
        alias_ticker = HK_COMPANY_ALIASES.get(normalized_query)
        normalized_ticker = self._normalize_hk_ticker(ticker or alias_ticker)
        candidates: list[CompanyEntity] = []
        for row in rows:
            code = str(row.get("c") or "").zfill(5)
            stock_id = row.get("i") or row.get("s")
            legacy_stock_id = row.get("s")
            name = self._clean_text(str(row.get("n") or "").strip())
            localized_names = [
                self._clean_text(str(item).strip())
                for item in row.get("_localized_names", [])
                if self._clean_text(str(item).strip())
            ]
            if not code or stock_id is None or not name:
                continue
            score, reason = self._score_stock_row(
                code=code,
                name=name,
                aliases=localized_names,
                normalized_ticker=normalized_ticker,
                normalized_query=normalized_query,
            )
            if score < 0.55:
                continue
            status = str(row.get("_status") or "unknown")
            if status == "inactive":
                score -= 0.05
            candidates.append(
                CompanyEntity(
                    market=Market.hk,
                    company_id=code,
                    ticker=code,
                    company_name=name,
                    exchange="HKEX",
                    aliases=list(dict.fromkeys([*localized_names, *([company_name] if company_name else [])])),
                    confidence=score,
                    match_reason=reason,
                    hkex_stock_id=str(stock_id),
                    metadata={
                        "stock_id": str(stock_id),
                        "legacy_stock_id": str(legacy_stock_id) if legacy_stock_id is not None else None,
                        "status": status,
                        "localized_names": localized_names,
                    },
                )
            )
        return sorted(candidates, key=lambda item: (item.confidence, item.ticker or ""), reverse=True)[:10]

    def _build_candidates(self, company: CompanyEntity, payload: dict) -> list[FilingCandidate]:
        raw_result = payload.get("result") or "[]"
        try:
            rows = json.loads(raw_result)
        except json.JSONDecodeError:
            rows = []
        candidates: list[FilingCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            report_type, family = self._infer_report_type(row)
            if report_type is None:
                continue
            published_at = self._parse_hkex_datetime(str(row.get("DATE_TIME") or ""))
            title = self._clean_text(str(row.get("TITLE") or "").strip())
            file_link = str(row.get("FILE_LINK") or "").strip()
            if published_at is None or not title or not file_link:
                continue
            document_url = file_link if file_link.startswith("http") else f"https://www1.hkexnews.hk{file_link}"
            report_end = self._infer_report_end(title=title, report_family=family, published_at=published_at)
            candidates.append(
                FilingCandidate(
                    source_id="hkex",
                    source_name="HKEXnews",
                    source_domain="www1.hkexnews.hk",
                    market=Market.hk,
                    company_id=company.company_id,
                    ticker=company.ticker,
                    company_name=company.company_name,
                    report_type=report_type,
                    report_family=family,
                    form=report_type.value,
                    title=title,
                    accession_number=str(row.get("NEWS_ID") or ""),
                    primary_document=file_link.rsplit("/", 1)[-1],
                    report_end=report_end,
                    published_at=published_at,
                    document_url=document_url,
                    landing_url=document_url,
                    file_format=str(row.get("FILE_TYPE") or "pdf").lower(),
                    language="zh-CN" if re.search(r"[\u4e00-\u9fff]", title) else "en",
                    metadata={
                        "news_id": row.get("NEWS_ID"),
                        "stock_name": self._clean_text(str(row.get("STOCK_NAME") or "")),
                        "short_text": self._clean_text(str(row.get("SHORT_TEXT") or "")),
                        "long_text": self._clean_text(str(row.get("LONG_TEXT") or "")),
                        "file_info": row.get("FILE_INFO"),
                    },
                )
            )
        return candidates

    @classmethod
    def _allowed_types(
        cls,
        *,
        target: ReportTarget,
        forms: list[str],
        include_earnings: bool,
    ) -> set[ReportType]:
        explicit = {cls._form_to_report_type(form) for form in forms if cls._form_to_report_type(form)}
        if explicit:
            return explicit
        if target == ReportTarget.annual_report:
            return {ReportType.annual}
        if target == ReportTarget.semiannual_report:
            return {ReportType.semiannual}
        if target == ReportTarget.quarterly_report:
            return {ReportType.quarterly}
        allowed = {ReportType.annual, ReportType.semiannual, ReportType.quarterly}
        if include_earnings:
            allowed.add(ReportType.earnings)
        return allowed

    @staticmethod
    def _infer_report_type(row: dict) -> tuple[ReportType | None, ReportFamily]:
        short_text = HkexClient._clean_text(str(row.get("SHORT_TEXT") or ""))
        long_text = HkexClient._clean_text(str(row.get("LONG_TEXT") or ""))
        title = HkexClient._clean_text(str(row.get("TITLE") or ""))
        category_text = f"{short_text} {long_text}".lower()
        joined = f"{short_text} {long_text} {title}".lower()

        if HkexClient._is_hkex_non_report_notice(category_text=category_text, joined=joined):
            return None, ReportFamily.current
        if HkexClient._is_hkex_report_category(category_text, "annual report") or "annual report" in joined:
            return ReportType.annual, ReportFamily.annual
        if (
            HkexClient._is_hkex_report_category(category_text, "interim")
            or HkexClient._is_hkex_report_category(category_text, "half-year")
            or "interim" in joined
            or "half-year" in joined
            or "half year" in joined
        ):
            return ReportType.semiannual, ReportFamily.semiannual
        if (
            HkexClient._is_hkex_report_category(category_text, "quarterly")
            or "quarterly report" in joined
            or "quarterly results" in joined
            or re.search(r"\bq[1-4]\b", joined)
        ):
            return ReportType.quarterly, ReportFamily.quarterly
        if "results announcement" in joined or "final results" in joined or "financial results" in joined:
            return ReportType.earnings, ReportFamily.current
        return None, ReportFamily.current

    @staticmethod
    def _is_hkex_report_category(category_text: str, report_marker: str) -> bool:
        return "financial statements" in category_text and report_marker in category_text

    @staticmethod
    def _is_hkex_non_report_notice(*, category_text: str, joined: str) -> bool:
        if HkexClient._is_hkex_report_category(category_text, "annual report"):
            return False
        if HkexClient._is_hkex_report_category(category_text, "interim"):
            return False
        if HkexClient._is_hkex_report_category(category_text, "half-year"):
            return False
        if HkexClient._is_hkex_report_category(category_text, "quarterly"):
            return False

        notice_markers = (
            "notification letter",
            "notice of publication",
            "reply form",
            "request form",
            "non-registered shareholder",
            "registered shareholder",
            "proxy form",
            "circulars - [other]",
        )
        if any(marker in joined for marker in notice_markers):
            return True
        return "circulars" in category_text and "annual report" in joined

    @staticmethod
    def _infer_report_end(title: str, report_family: ReportFamily, published_at: date) -> date:
        explicit_end = HkexClient._parse_report_end_from_title(title)
        if explicit_end is not None:
            return explicit_end
        fiscal_annual_end = HkexClient._parse_short_fiscal_annual_end(title, report_family)
        if fiscal_annual_end is not None:
            return fiscal_annual_end
        year_match = re.search(r"(20\d{2})", title)
        year = int(year_match.group(1)) if year_match else published_at.year
        title_lower = title.lower()
        if report_family == ReportFamily.annual:
            return date(year, 12, 31)
        if report_family == ReportFamily.semiannual:
            return date(year, 6, 30)
        if report_family == ReportFamily.quarterly:
            if "third quarter" in title_lower or "q3" in title_lower:
                return date(year, 9, 30)
            if "second quarter" in title_lower or "q2" in title_lower:
                return date(year, 6, 30)
            if "first quarter" in title_lower or "q1" in title_lower:
                return date(year, 3, 31)
            if "fourth quarter" in title_lower or "q4" in title_lower:
                return date(year, 12, 31)
        return published_at

    @staticmethod
    def _parse_short_fiscal_annual_end(title: str, report_family: ReportFamily) -> date | None:
        if report_family != ReportFamily.annual or "annual report" not in title.lower():
            return None
        match = re.search(r"\b(20\d{2})/(\d{2})\s+annual report\b", title, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"\bannual report\s+(20\d{2})/(\d{2})\b", title, flags=re.IGNORECASE)
        if not match:
            return None
        start_year = int(match.group(1))
        short_end_year = int(match.group(2))
        end_year = (start_year // 100) * 100 + short_end_year
        if end_year < start_year:
            end_year += 100
        return date(end_year, 6, 30)

    @staticmethod
    def _parse_report_end_from_title(title: str) -> date | None:
        month_names = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "sept": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }
        patterns = [
            r"(?:ended|ending|as at|as of)\s+(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})",
            r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})",
        ]
        for pattern in patterns:
            matches = list(re.finditer(pattern, title, flags=re.IGNORECASE))
            if not matches:
                continue
            day_raw, month_raw, year_raw = matches[-1].groups()
            month = month_names.get(month_raw.lower())
            if not month:
                continue
            try:
                return date(int(year_raw), month, int(day_raw))
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_hkex_datetime(raw: str) -> date | None:
        try:
            return datetime.strptime(raw.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc).date()
        except ValueError:
            return None

    @staticmethod
    def _clean_text(text: str) -> str:
        text = unescape(str(text or ""))
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _score_stock_row(
        *,
        code: str,
        name: str,
        aliases: list[str],
        normalized_ticker: str | None,
        normalized_query: str,
    ) -> tuple[float, str]:
        if normalized_ticker:
            return (0.99, "hkex_ticker_exact") if code == normalized_ticker else (-1.0, "ticker_mismatch")
        if not normalized_query:
            return -1.0, "empty_query"
        for index, candidate_name in enumerate([name, *aliases]):
            row_normalized = HkexClient._normalize_name(candidate_name)
            reason_prefix = "hkex_name" if index == 0 else "hkex_alias"
            if row_normalized == normalized_query:
                return 0.96, f"{reason_prefix}_exact"
            if normalized_query in row_normalized:
                return 0.88, f"{reason_prefix}_contains_query"
            if row_normalized in normalized_query:
                return 0.84, f"hkex_query_contains_{'name' if index == 0 else 'alias'}"
            ratio = SequenceMatcher(None, normalized_query, row_normalized).ratio()
            if ratio >= 0.72:
                return 0.70 + (ratio - 0.72) * 0.3, f"{reason_prefix}_fuzzy"
        return -1.0, "name_mismatch"

    @staticmethod
    def _normalize_hk_ticker(ticker: str | None) -> str | None:
        if not ticker:
            return None
        compact = ticker.strip().upper()
        compact = re.sub(r"^(HK|HKG|HKEX)[:.\s-]+", "", compact)
        compact = compact.replace(".HK", "")
        digits = re.sub(r"\D+", "", compact)
        if not digits:
            return None
        return digits.zfill(5)

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())

    @staticmethod
    def _form_to_report_type(form: str) -> ReportType | None:
        normalized = str(form or "").strip().lower().replace("_", "-")
        mapping = {
            "annual": ReportType.annual,
            "annual-report": ReportType.annual,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "interim": ReportType.semiannual,
            "half-year": ReportType.semiannual,
            "quarterly": ReportType.quarterly,
            "quarterly-report": ReportType.quarterly,
            "earnings": ReportType.earnings,
            "results": ReportType.earnings,
        }
        return mapping.get(normalized)

    def _client(self) -> httpx.Client:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=EN",
            "X-Requested-With": "XMLHttpRequest",
        }
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.hkex_max_requests_per_second), 0.1)
        min_interval = 1.0 / max_rps
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + min_interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()
