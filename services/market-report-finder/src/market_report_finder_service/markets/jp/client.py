from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class EdinetClient:
    DOCUMENTS_URL = "https://disclosure2.edinet-fsa.go.jp/api/v2/documents.json"
    DOCUMENT_DOWNLOAD_URL = "https://disclosure2.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

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
        rows = self._scan_year_window(report_year) if report_year else self._scan_recent_rows(days=2920)
        candidates: list[FilingCandidate] = []
        for row in rows:
            if not self._row_matches_company(row, company):
                continue
            report_type, family = self._infer_report_type(row)
            if report_type not in allowed:
                continue
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
            source_domain="disclosure2.edinet-fsa.go.jp",
            market=Market.jp,
            company_id=company.company_id,
            ticker=company.ticker,
            company_name=company.company_name,
            report_type=report_type,
            report_family=family,
            form=report_type.value,
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

    def _scan_year_window(self, report_year: int) -> list[dict]:
        start = date(report_year, 1, 1)
        end = min(date.today(), date(report_year + 1, 12, 31))
        rows: list[dict] = []
        days = (end - start).days + 1
        for offset in range(days):
            rows.extend(self._document_rows(start + timedelta(days=offset)))
        return rows

    def _document_rows(self, target_date: date) -> list[dict]:
        if not settings.edinet_api_key:
            raise ValueError("EDINET_API_KEY is required for Japanese market report search")
        params = {"date": target_date.isoformat(), "type": "2"}
        params["Subscription-Key"] = settings.edinet_api_key
        payload = self._get_json(self.DOCUMENTS_URL, params=params)
        rows = payload.get("results") or []
        return [row for row in rows if isinstance(row, dict)]

    def _offline_company(
        self,
        *,
        company_name: str | None,
        ticker: str | None,
        company_id: str | None,
    ) -> CompanyEntity | None:
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_company_id = (company_id or "").strip().upper()
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
        normalized_company_id = (company_id or "").strip().upper()
        best_by_key: dict[str, CompanyEntity] = {}
        for row in rows:
            edinet_code = str(row.get("edinetCode") or "").strip().upper()
            sec_code = str(row.get("secCode") or "").strip()
            filer_name = str(row.get("filerName") or "").strip()
            if not edinet_code or not filer_name:
                continue
            score, reason = self._score_company_row(
                edinet_code=edinet_code,
                sec_code=sec_code,
                filer_name=filer_name,
                normalized_company_id=normalized_company_id,
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
    def _infer_report_type(cls, row: dict) -> tuple[ReportType, ReportFamily]:
        form_code = str(row.get("formCode") or "")
        title = str(row.get("docDescription") or "").lower()
        if form_code in cls.ANNUAL_FORM_CODES or "有価証券報告書" in title:
            return ReportType.annual, ReportFamily.annual
        if form_code in cls.QUARTERLY_FORM_CODES or "四半期報告書" in title:
            return ReportType.quarterly, ReportFamily.quarterly
        if form_code in cls.SEMIANNUAL_FORM_CODES or "半期報告書" in title:
            return ReportType.semiannual, ReportFamily.semiannual
        return ReportType.annual, ReportFamily.annual

    @staticmethod
    def _score_company_row(
        *,
        edinet_code: str,
        sec_code: str,
        filer_name: str,
        normalized_company_id: str,
        normalized_ticker: str | None,
        normalized_query: str,
    ) -> tuple[float, str]:
        if normalized_company_id:
            return (0.99, "edinet_code_exact") if edinet_code == normalized_company_id else (-1.0, "company_id_mismatch")
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
        sec_code = str(row.get("secCode") or "").strip()
        if edinet_code and edinet_code == company.company_id.upper():
            return True
        return bool(company.ticker and sec_code and sec_code == company.ticker)

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
    def _infer_report_end(title: str, report_type: ReportType, published_at: date) -> date:
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
        self._wait_for_slot()
        with self._client() as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                preview = response.text.replace("\n", " ")[:160]
                raise ValueError(f"EDINET API returned non-JSON response. Check EDINET_API_KEY/subscription. Preview: {preview}")
            return response.json()

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
        code = re.sub(r"[^0-9A-Z]+", "", ticker.upper())
        if len(code) >= 4:
            return code[:4] + "0"
        return code or None

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9ぁ-んァ-ン一-龥\u4e00-\u9fff]+", "", text.lower())
