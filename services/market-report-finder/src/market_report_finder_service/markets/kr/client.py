from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from datetime import date, datetime
from difflib import SequenceMatcher
from xml.etree import ElementTree

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class DartClient:
    CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"
    DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
    VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"

    ANNUAL_REPORT_NAMES = ("사업보고서", "annual report")
    SEMIANNUAL_REPORT_NAMES = ("반기보고서", "half-year", "semiannual", "semi-annual")
    QUARTERLY_REPORT_NAMES = ("분기보고서", "quarterly report")
    COMMON_COMPANIES = {
        "005930": ("00126380", "삼성전자"),
        "005380": ("00164742", "현대자동차"),
        "000660": ("00164779", "SK하이닉스"),
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._corp_codes: list[dict[str, str]] | None = None

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
        candidates = self._company_candidates(
            self.corp_codes(),
            company_name=company_name,
            ticker=ticker,
            company_id=company_id,
        )
        if not candidates:
            raise ValueError(f"DART company catalog did not match: {query}")
        return candidates[0], candidates

    def _offline_company(
        self,
        *,
        company_name: str | None,
        ticker: str | None,
        company_id: str | None,
    ) -> CompanyEntity | None:
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_company_id = re.sub(r"\D+", "", company_id or "")
        normalized_name = self._normalize_name(company_name or "")
        for stock_code, (corp_code, corp_name) in self.COMMON_COMPANIES.items():
            if normalized_ticker and normalized_ticker != stock_code:
                continue
            if normalized_company_id and normalized_company_id != corp_code:
                continue
            if normalized_name and normalized_name not in self._normalize_name(corp_name) and self._normalize_name(corp_name) not in normalized_name:
                if stock_code not in normalized_name and corp_code not in normalized_name:
                    continue
            return CompanyEntity(
                market=Market.kr,
                company_id=corp_code,
                ticker=stock_code,
                company_name=corp_name,
                exchange="KRX",
                aliases=[company_name] if company_name else [],
                confidence=0.93,
                match_reason="offline_common_company",
                metadata={"corp_code": corp_code, "stock_code": stock_code, "offline_catalog": True},
            )
        return None

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
        candidates: list[FilingCandidate] = []
        for report_type in sorted(allowed, key=lambda item: item.value):
            payload = self._query_filings(company.company_id, report_type, report_year=report_year)
            for row in payload.get("list") or []:
                candidate = self._build_candidate(company, row, report_type)
                if candidate:
                    candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def corp_codes(self) -> list[dict[str, str]]:
        if self._corp_codes is not None:
            return self._corp_codes
        api_key = self._api_key()
        content = self._get_bytes(self.CORP_CODE_URL, params={"crtfc_key": api_key})
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml_name = next((name for name in archive.namelist() if name.lower().endswith(".xml")), archive.namelist()[0])
            root = ElementTree.fromstring(archive.read(xml_name))
        rows: list[dict[str, str]] = []
        for item in root.findall(".//list"):
            row = {child.tag: (child.text or "").strip() for child in item}
            if row.get("corp_code") and row.get("corp_name"):
                rows.append(row)
        self._corp_codes = rows
        return rows

    def _query_filings(self, corp_code: str, report_type: ReportType, *, report_year: int | None = None) -> dict:
        begin_date = f"{report_year + 1}0101" if report_year else "20190101"
        end_date = f"{report_year + 1}1231" if report_year else "20301231"
        params = {
            "crtfc_key": self._api_key(),
            "corp_code": corp_code,
            "bgn_de": begin_date,
            "end_de": end_date,
            "last_reprt_at": "Y",
            "pblntf_ty": "A",
            "pblntf_detail_ty": self._detail_type(report_type),
            "page_no": "1",
            "page_count": "100",
        }
        return self._get_json(self.LIST_URL, params=params)

    def _build_candidate(
        self,
        company: CompanyEntity,
        row: dict,
        expected_report_type: ReportType,
    ) -> FilingCandidate | None:
        title = str(row.get("report_nm") or "").strip()
        receipt_no = str(row.get("rcept_no") or "").strip()
        published_raw = str(row.get("rcept_dt") or "").strip()
        if not title or not receipt_no or not published_raw:
            return None
        report_type, family = self._infer_report_type(title, expected_report_type)
        published_at = self._parse_yyyymmdd(published_raw)
        report_end = self._infer_report_end(title=title, report_type=report_type, published_at=published_at)
        return FilingCandidate(
            source_id="dart",
            source_name="DART",
            source_domain="opendart.fss.or.kr",
            market=Market.kr,
            company_id=company.company_id,
            ticker=company.ticker,
            company_name=company.company_name,
            report_type=report_type,
            report_family=family,
            form=report_type.value,
            title=title,
            accession_number=receipt_no,
            primary_document=f"{receipt_no}.zip",
            report_end=report_end,
            published_at=published_at,
            document_url=f"{self.DOCUMENT_URL}?rcept_no={receipt_no}",
            landing_url=f"{self.VIEWER_URL}?rcpNo={receipt_no}",
            file_format="zip",
            language="ko",
            metadata={
                "corp_code": company.company_id,
                "corp_cls": row.get("corp_cls"),
                "stock_code": row.get("stock_code"),
                "flr_nm": row.get("flr_nm"),
                "rm": row.get("rm"),
                "document_format": "dart_xml_zip",
            },
        )

    def _company_candidates(
        self,
        rows: list[dict[str, str]],
        *,
        company_name: str | None,
        ticker: str | None,
        company_id: str | None,
    ) -> list[CompanyEntity]:
        normalized_query = self._normalize_name(company_name or "")
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_corp_code = re.sub(r"\D+", "", company_id or "")
        candidates: list[CompanyEntity] = []
        for row in rows:
            corp_code = row.get("corp_code") or ""
            stock_code = row.get("stock_code") or ""
            corp_name = row.get("corp_name") or ""
            if not corp_code or not corp_name:
                continue
            score, reason = self._score_company_row(
                corp_code=corp_code,
                stock_code=stock_code,
                corp_name=corp_name,
                normalized_corp_code=normalized_corp_code,
                normalized_ticker=normalized_ticker,
                normalized_query=normalized_query,
            )
            if score < 0.55:
                continue
            candidates.append(
                CompanyEntity(
                    market=Market.kr,
                    company_id=corp_code,
                    ticker=stock_code or None,
                    company_name=corp_name,
                    exchange="KRX" if stock_code else None,
                    aliases=[company_name] if company_name else [],
                    confidence=score,
                    match_reason=reason,
                    metadata={
                        "corp_code": corp_code,
                        "stock_code": stock_code or None,
                        "modify_date": row.get("modify_date"),
                    },
                )
            )
        return sorted(candidates, key=lambda item: (item.confidence, item.ticker or ""), reverse=True)[:10]

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
            "business-report": ReportType.annual,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "half-year": ReportType.semiannual,
            "interim": ReportType.semiannual,
            "quarterly": ReportType.quarterly,
            "quarterly-report": ReportType.quarterly,
            "q1": ReportType.quarterly,
            "q3": ReportType.quarterly,
        }
        return mapping.get(normalized)

    @staticmethod
    def _detail_type(report_type: ReportType) -> str:
        mapping = {
            ReportType.annual: "a001",
            ReportType.semiannual: "a002",
            ReportType.quarterly: "a003",
            ReportType.q1: "a003",
            ReportType.q3: "a003",
        }
        return mapping.get(report_type, "a001")

    @classmethod
    def _infer_report_type(cls, title: str, fallback: ReportType) -> tuple[ReportType, ReportFamily]:
        normalized = title.lower()
        if any(token in normalized for token in cls.ANNUAL_REPORT_NAMES):
            return ReportType.annual, ReportFamily.annual
        if any(token in normalized for token in cls.SEMIANNUAL_REPORT_NAMES):
            return ReportType.semiannual, ReportFamily.semiannual
        if any(token in normalized for token in cls.QUARTERLY_REPORT_NAMES):
            return ReportType.quarterly, ReportFamily.quarterly
        family = ReportFamily.annual if fallback == ReportType.annual else ReportFamily.quarterly
        if fallback == ReportType.semiannual:
            family = ReportFamily.semiannual
        return fallback, family

    @staticmethod
    def _infer_report_end(title: str, report_type: ReportType, published_at: date) -> date:
        year_month_match = re.search(r"(20\d{2})[.\-/年]\s*(0?[1-9]|1[0-2])", title)
        if year_month_match:
            year = int(year_month_match.group(1))
            month = int(year_month_match.group(2))
            if report_type == ReportType.annual:
                return date(year, 12, 31)
            if report_type == ReportType.semiannual:
                return date(year, 6, 30)
            if month <= 3:
                return date(year, 3, 31)
            if month <= 6:
                return date(year, 6, 30)
            if month <= 9:
                return date(year, 9, 30)
            return date(year, 12, 31)
        year_match = re.search(r"20\d{2}", title)
        year = int(year_match.group(0)) if year_match else published_at.year
        if report_type == ReportType.annual:
            return date(year, 12, 31)
        if report_type == ReportType.semiannual:
            return date(year, 6, 30)
        if "3분기" in title or "third" in title.lower() or "q3" in title.lower():
            return date(year, 9, 30)
        if "1분기" in title or "first" in title.lower() or "q1" in title.lower():
            return date(year, 3, 31)
        return published_at

    @staticmethod
    def _score_company_row(
        *,
        corp_code: str,
        stock_code: str,
        corp_name: str,
        normalized_corp_code: str,
        normalized_ticker: str | None,
        normalized_query: str,
    ) -> tuple[float, str]:
        if normalized_corp_code:
            return (0.99, "dart_corp_code_exact") if corp_code == normalized_corp_code else (-1.0, "corp_code_mismatch")
        if normalized_ticker:
            return (0.99, "dart_stock_code_exact") if stock_code == normalized_ticker else (-1.0, "ticker_mismatch")
        if not normalized_query:
            return -1.0, "empty_query"
        row_normalized = DartClient._normalize_name(corp_name)
        if row_normalized == normalized_query:
            return 0.96, "dart_company_exact"
        if normalized_query in row_normalized:
            return 0.88, "dart_company_contains_query"
        if row_normalized in normalized_query:
            return 0.84, "dart_query_contains_company"
        ratio = SequenceMatcher(None, normalized_query, row_normalized).ratio()
        if ratio >= 0.72:
            return 0.70 + (ratio - 0.72) * 0.3, "dart_company_fuzzy"
        return -1.0, "company_mismatch"

    @staticmethod
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_receipt: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            key = candidate.accession_number or candidate.document_url
            by_receipt.setdefault(key, candidate)
        return list(by_receipt.values())

    def _get_json(self, url: str, *, params: dict[str, str]) -> dict:
        self._wait_for_slot()
        with self._client() as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        status = str(payload.get("status") or "")
        if status and status not in {"000", "013"}:
            raise ValueError(f"DART API error {status}: {payload.get('message')}")
        return payload

    def _get_bytes(self, url: str, *, params: dict[str, str]) -> bytes:
        self._wait_for_slot()
        with self._client() as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.content

    @staticmethod
    def _client() -> httpx.Client:
        headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    def _wait_for_slot(self) -> None:
        max_rps = max(float(settings.dart_max_requests_per_second), 0.1)
        min_interval = 1.0 / max_rps
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + min_interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _parse_yyyymmdd(raw: str) -> date:
        return datetime.strptime(raw, "%Y%m%d").date()

    @staticmethod
    def _normalize_ticker(ticker: str | None) -> str | None:
        if not ticker:
            return None
        digits = re.sub(r"\D+", "", ticker)
        return digits.zfill(6) if digits else None

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9가-힣\u4e00-\u9fff]+", "", text.lower())

    @staticmethod
    def _api_key() -> str:
        if not settings.dart_api_key:
            raise ValueError("DART_API_KEY is required for Korean market report search")
        return settings.dart_api_key
