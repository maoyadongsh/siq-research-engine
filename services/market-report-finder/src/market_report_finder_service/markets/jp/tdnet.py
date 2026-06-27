from __future__ import annotations

import html
import re
import threading
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class _TdnetListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._cell_class = ""
        self._cell_text: list[str] = []
        self._title_href = ""
        self.next_pages: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "tr":
            self._row = {}
        elif tag == "td" and self._row is not None:
            self._cell_class = attr.get("class", "")
            self._cell_text = []
            self._title_href = ""
        elif tag == "a" and self._row is not None and "kjTitle" in self._cell_class:
            self._title_href = attr.get("href", "")
        elif tag == "div":
            onclick = attr.get("onclick", "")
            match = re.search(r"pager(?:Link)?\('([^']+)'\)", onclick)
            if match and match.group(1):
                self.next_pages.add(match.group(1))

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._cell_class:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._cell_class:
            text = re.sub(r"\s+", " ", "".join(self._cell_text)).strip()
            if "kjTime" in self._cell_class:
                self._row["time"] = text
            elif "kjCode" in self._cell_class:
                self._row["code"] = text
            elif "kjName" in self._cell_class:
                self._row["company_name"] = text
            elif "kjTitle" in self._cell_class:
                self._row["title"] = text
                self._row["pdf_href"] = self._title_href
            elif "kjXbrl" in self._cell_class:
                self._row["xbrl"] = text
            elif "kjPlace" in self._cell_class:
                self._row["exchange"] = text
            self._cell_class = ""
            self._cell_text = []
            self._title_href = ""
        elif tag == "tr" and self._row is not None:
            if self._row.get("code") and self._row.get("title") and self._row.get("pdf_href"):
                self.rows.append(self._row)
            self._row = None


class TdnetClient:
    BASE_URL = "https://www.release.tdnet.info/inbs/"
    LIST_PAGE_TEMPLATE = "I_list_{page:03d}_{date}.html"

    EARNINGS_KEYWORDS = ("決算短信", "四半期決算", "業績予想", "決算説明資料", "financial results")
    ANNUAL_KEYWORDS = ("通期決算", "期末決算", "年次", "annual")
    QUARTERLY_KEYWORDS = ("第1四半期", "第2四半期", "第3四半期", "四半期", "quarter")
    SEMIANNUAL_KEYWORDS = ("中間決算", "第2四半期", "半期", "interim", "half")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget = ReportTarget.financial_report,
        forms: list[str] | None = None,
        report_year: int | None = None,
    ) -> list[FilingCandidate]:
        allowed = self._allowed_types(target=target, forms=forms or [])
        rows = self._scan_window(report_year=report_year)
        candidates: list[FilingCandidate] = []
        for row in rows:
            if not self._row_matches_company(row, company):
                continue
            report_type, family = self._infer_report_type(row.get("title", ""))
            if report_type not in allowed:
                continue
            candidate = self._build_candidate(company, row, report_type, family, report_year=report_year)
            if candidate:
                candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def _scan_window(self, *, report_year: int | None) -> list[dict[str, str]]:
        today = date.today()
        if report_year and report_year < today.year - 1:
            return []
        if report_year and report_year > today.year:
            return []
        days = max(1, int(settings.tdnet_recent_days))
        if report_year == today.year - 1:
            start = max(date(report_year, 1, 1), today - timedelta(days=days))
            end = today
        elif report_year == today.year:
            start = max(date(report_year, 1, 1), today - timedelta(days=days))
            end = today
        else:
            start = today - timedelta(days=days)
            end = today
        rows: list[dict[str, str]] = []
        cursor = end
        while cursor >= start:
            rows.extend(self._list_rows_for_day(cursor))
            cursor -= timedelta(days=1)
        return rows

    def _list_rows_for_day(self, target_date: date) -> list[dict[str, str]]:
        date_text = target_date.strftime("%Y%m%d")
        rows: list[dict[str, str]] = []
        for page in range(1, max(1, int(settings.tdnet_max_pages_per_day)) + 1):
            page_name = self.LIST_PAGE_TEMPLATE.format(page=page, date=date_text)
            text = self._get_text(urljoin(self.BASE_URL, page_name))
            if not text:
                break
            parser = _TdnetListParser()
            parser.feed(text)
            if not parser.rows:
                break
            for row in parser.rows:
                row["published_at"] = target_date.isoformat()
                row["list_page"] = page_name
            rows.extend(parser.rows)
            next_page = self.LIST_PAGE_TEMPLATE.format(page=page + 1, date=date_text)
            if next_page not in parser.next_pages:
                break
        return rows

    def _build_candidate(
        self,
        company: CompanyEntity,
        row: dict[str, str],
        report_type: ReportType,
        family: ReportFamily,
        *,
        report_year: int | None,
    ) -> FilingCandidate | None:
        href = row.get("pdf_href", "").strip()
        title = html.unescape(row.get("title", "")).strip()
        if not href or not title or not href.lower().endswith(".pdf"):
            return None
        published_at = date.fromisoformat(row.get("published_at") or date.today().isoformat())
        return FilingCandidate(
            source_id="tdnet",
            source_name="TDnet",
            source_domain="release.tdnet.info",
            market=Market.jp,
            company_id=company.company_id,
            ticker=company.ticker,
            company_name=company.company_name,
            report_type=report_type,
            report_family=family,
            form=report_type.value,
            title=title,
            accession_number=href.rsplit(".", 1)[0],
            primary_document=href,
            report_end=self._infer_report_end(title=title, published_at=published_at, report_year=report_year),
            published_at=published_at,
            document_url=urljoin(self.BASE_URL, href),
            landing_url=urljoin(self.BASE_URL, row.get("list_page") or ""),
            file_format="pdf",
            language="ja",
            metadata={
                "tdnet_code": row.get("code"),
                "tdnet_company_name": row.get("company_name"),
                "exchange": row.get("exchange"),
                "disclosure_time": row.get("time"),
                "secondary_official_source": True,
            },
        )

    @classmethod
    def _allowed_types(cls, *, target: ReportTarget, forms: list[str]) -> set[ReportType]:
        explicit = {cls._form_to_report_type(form) for form in forms if cls._form_to_report_type(form)}
        if explicit:
            return explicit
        if target == ReportTarget.annual_report:
            return {ReportType.annual, ReportType.earnings}
        if target == ReportTarget.semiannual_report:
            return {ReportType.semiannual, ReportType.earnings}
        if target == ReportTarget.quarterly_report:
            return {ReportType.quarterly, ReportType.earnings}
        return {ReportType.annual, ReportType.semiannual, ReportType.quarterly, ReportType.earnings}

    @staticmethod
    def _form_to_report_type(form: str) -> ReportType | None:
        normalized = form.strip().lower().replace("_", "-")
        mapping = {
            "annual": ReportType.annual,
            "annual-report": ReportType.annual,
            "earnings": ReportType.earnings,
            "earnings-release": ReportType.earnings,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "quarterly": ReportType.quarterly,
            "quarterly-report": ReportType.quarterly,
            "q1": ReportType.quarterly,
            "q2": ReportType.quarterly,
            "q3": ReportType.quarterly,
        }
        return mapping.get(normalized)

    @classmethod
    def _infer_report_type(cls, title: str) -> tuple[ReportType, ReportFamily]:
        lowered = title.lower()
        if any(keyword.lower() in lowered for keyword in cls.QUARTERLY_KEYWORDS):
            return ReportType.quarterly, ReportFamily.quarterly
        if any(keyword.lower() in lowered for keyword in cls.SEMIANNUAL_KEYWORDS):
            return ReportType.semiannual, ReportFamily.semiannual
        if any(keyword.lower() in lowered for keyword in cls.ANNUAL_KEYWORDS):
            return ReportType.annual, ReportFamily.annual
        if any(keyword.lower() in lowered for keyword in cls.EARNINGS_KEYWORDS):
            return ReportType.earnings, ReportFamily.current
        return ReportType.earnings, ReportFamily.current

    @staticmethod
    def _infer_report_end(*, title: str, published_at: date, report_year: int | None) -> date:
        year_match = re.search(r"20\d{2}年", title)
        year = int(year_match.group(0)[:4]) if year_match else report_year or published_at.year
        if "第3四半期" in title:
            return date(year, 12, 31)
        if "第2四半期" in title or "中間" in title or "半期" in title:
            return date(year, 9, 30)
        if "第1四半期" in title:
            return date(year, 6, 30)
        if "3月期" in title or "通期" in title or "期末" in title:
            return date(year, 3, 31)
        return published_at

    @classmethod
    def _row_matches_company(cls, row: dict[str, str], company: CompanyEntity) -> bool:
        row_code = cls._normalize_ticker(row.get("code"))
        company_code = cls._normalize_ticker(company.ticker)
        if row_code and company_code and row_code == company_code:
            return True
        row_name = cls._normalize_name(row.get("company_name") or "")
        company_name = cls._normalize_name(company.company_name)
        return bool(row_name and company_name and (row_name in company_name or company_name in row_name))

    @staticmethod
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_doc: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            by_doc.setdefault(candidate.document_url, candidate)
        return list(by_doc.values())

    def _get_text(self, url: str) -> str:
        self._wait_for_slot()
        try:
            with self._client() as client:
                response = client.get(url)
                if response.status_code == 404:
                    return ""
                response.raise_for_status()
                return response.text
        except httpx.HTTPError:
            return ""

    @staticmethod
    def _client() -> httpx.Client:
        headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.tdnet_max_requests_per_second), 0.1)
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
        code = re.sub(r"[^0-9A-Z]+", "", ticker.upper())
        if len(code) >= 4:
            return code[:4] + "0"
        return code or None

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9ぁ-んァ-ン一-龥\u4e00-\u9fff]+", "", text.lower())
