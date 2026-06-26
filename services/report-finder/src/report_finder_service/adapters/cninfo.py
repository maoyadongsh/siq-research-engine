import re
import time
from datetime import date, datetime, timezone

import httpx

from report_finder_service.adapters.base import SourceAdapter
from report_finder_service.core.config import settings
from report_finder_service.models.schemas import (
    CompanyEntity,
    Market,
    ReportCandidate,
    ReportTarget,
    ReportType,
    SourceDescriptor,
)


class CninfoAdapter(SourceAdapter):
    TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
    ANNOUNCEMENT_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    PDF_BASE_URL = "https://static.cninfo.com.cn/"
    DETAIL_URL = "https://www.cninfo.com.cn/new/disclosure/detail"
    CATEGORY_BY_REPORT_TYPE = {
        ReportType.annual: "category_ndbg_szsh",
        ReportType.semiannual: "category_bndbg_szsh",
        ReportType.q1: "category_yjdbg_szsh",
        ReportType.q3: "category_sjdbg_szsh",
    }

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="cninfo",
            source_name="巨潮资讯",
            markets=[Market.cn],
            official_domain="www.cninfo.com.cn",
            notes="A 股官方信息披露平台适配器；使用巨潮公告搜索接口。",
            supports_targets=[
                ReportTarget.annual_report,
                ReportTarget.financial_report,
                ReportTarget.latest_report,
            ],
            data_scope=["public_financial_reports", "public_annual_reports"],
            implementation_status="live",
        )

    def search(
        self,
        company: CompanyEntity,
        target: ReportTarget = ReportTarget.latest_report,
    ) -> list[ReportCandidate]:
        if company.market != Market.cn:
            return []

        stock_entry = self._resolve_stock_entry(company)
        candidates: list[ReportCandidate] = []
        seen_ids: set[str] = set()
        plate = self._plate_for_company(company)
        for report_type, category in self._categories_for_target(target):
            payload = self._query_announcements(
                stock=stock_entry["stock"],
                category=category,
                plate=plate,
            )
            for announcement in payload.get("announcements", []):
                candidate = self._build_candidate(company, stock_entry, announcement, report_type)
                if candidate is None:
                    continue
                announcement_id = announcement.get("announcementId", "")
                if announcement_id in seen_ids:
                    continue
                seen_ids.add(announcement_id)
                candidates.append(candidate)
        return candidates

    def _categories_for_target(self, target: ReportTarget) -> list[tuple[ReportType, str]]:
        if target == ReportTarget.annual_report:
            return [(ReportType.annual, self.CATEGORY_BY_REPORT_TYPE[ReportType.annual])]
        return list(self.CATEGORY_BY_REPORT_TYPE.items())

    def _resolve_stock_entry(self, company: CompanyEntity) -> dict[str, str]:
        payload = {
            "keyWord": company.ticker,
            "maxNum": 10,
            "plate": "szsh",
        }
        rows = self._post_with_retry(self.TOP_SEARCH_URL, data=payload)

        for row in rows:
            if row.get("code") == company.ticker:
                org_id = row.get("orgId")
                if not org_id:
                    continue
                return {
                    "code": row["code"],
                    "orgId": org_id,
                    "stock": f'{row["code"]},{org_id}',
                    "name": row.get("zwjc") or company.display_name,
                }

        raise ValueError(f"巨潮未找到 {company.display_name}({company.ticker}) 的 orgId")

    def _query_announcements(self, stock: str, category: str, plate: str) -> dict:
        payload = {
            "pageNum": 1,
            "pageSize": 10,
            "column": "szse",
            "tabName": "fulltext",
            "plate": plate,
            "stock": stock,
            "searchkey": "",
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": "",
            "sortName": "time",
            "sortType": "desc",
            "isHLtitle": "true",
        }
        return self._post_with_retry(self.ANNOUNCEMENT_QUERY_URL, data=payload, expect_json=True)

    def _build_candidate(
        self,
        company: CompanyEntity,
        stock_entry: dict[str, str],
        announcement: dict,
        report_type: ReportType,
    ) -> ReportCandidate | None:
        title = announcement.get("announcementTitle", "")
        if not title:
            return None
        if any(keyword in title for keyword in ("摘要", "英文版")):
            return None
        adjunct_url = announcement.get("adjunctUrl", "")
        if not adjunct_url:
            return None
        announcement_time = announcement.get("announcementTime")
        if announcement_time is None:
            return None

        published_at = datetime.fromtimestamp(announcement_time / 1000, tz=timezone.utc).date()
        report_end = self._infer_report_end(title, report_type, published_at)
        announcement_id = announcement.get("announcementId", "")
        org_id = announcement.get("orgId") or stock_entry["orgId"]
        sec_code = announcement.get("secCode") or company.ticker
        sec_name = announcement.get("secName") or stock_entry["name"]
        announcement_date = published_at.isoformat()

        return ReportCandidate(
            source_id="cninfo",
            source_name="巨潮资讯",
            source_domain="www.cninfo.com.cn",
            company_name=sec_name,
            ticker=sec_code,
            market=Market.cn,
            report_type=report_type,
            title=title,
            report_end=report_end,
            published_at=published_at,
            language="zh-CN",
            document_url=self.PDF_BASE_URL + adjunct_url.lstrip("/"),
            landing_url=(
                f"{self.DETAIL_URL}?stockCode={sec_code}"
                f"&announcementId={announcement_id}"
                f"&orgId={org_id}"
                f"&announcementTime={announcement_date}"
            ),
            file_format=(announcement.get("adjunctType") or "pdf").lower(),
        )

    def _client(self) -> httpx.Client:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    def _post_with_retry(
        self,
        url: str,
        data: dict | None = None,
        expect_json: bool = False,
        max_retries: int = 3,
        backoff: float = 2.0,
    ) -> dict | list:
        """带指数退避重试的POST请求，遇到5xx或超时时自动重试"""
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with self._client() as client:
                    response = client.post(url, data=data)
                    response.raise_for_status()
                    if expect_json:
                        return response.json()
                    # topSearch 返回的是 JSON 数组
                    return response.json()
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                status = getattr(exc, "response", None)
                status_code = status.status_code if status else 0
                is_retryable = status_code >= 500 or status_code == 0
                if not is_retryable or attempt >= max_retries:
                    raise
                wait = backoff * (2 ** attempt)
                time.sleep(wait)
        raise last_error

    @staticmethod
    def _plate_for_company(company: CompanyEntity) -> str:
        ticker = company.ticker
        exchange = company.exchange.upper()
        if exchange == "SSE":
            if ticker.startswith("688"):
                return "shkcp"
            return "sh"
        if exchange == "SZSE":
            if ticker.startswith("300"):
                return "szcy"
            return "sz"
        if exchange == "BSE":
            return "bj"
        return "szse"

    @staticmethod
    def _infer_report_end(title: str, report_type: ReportType, published_at: date) -> date:
        match = re.search(r"(20\d{2})年", title)
        year = int(match.group(1)) if match else published_at.year
        if report_type == ReportType.annual:
            return date(year, 12, 31)
        if report_type == ReportType.semiannual:
            return date(year, 6, 30)
        if report_type == ReportType.q1:
            return date(year, 3, 31)
        if report_type == ReportType.q3:
            return date(year, 9, 30)
        return published_at
