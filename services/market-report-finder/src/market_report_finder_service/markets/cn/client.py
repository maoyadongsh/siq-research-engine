from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from importlib import resources

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


class CninfoClient:
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

    def resolve_company(
        self,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        query = company_name or ticker or company_id or ""
        normalized_exchange = self._normalize_exchange_hint(None)
        ticker_query = ticker or company_id or self._maybe_ticker_from_query(query)

        candidate_pool: list[CompanyEntity] = []
        candidate_pool.extend(self._search_cninfo(query=query, ticker=ticker_query, exchange_hint=normalized_exchange))

        matched_seed = self._match_seed(query)
        if matched_seed is not None:
            for search_term in self._seed_search_terms(matched_seed):
                candidate_pool.extend(
                    self._search_cninfo(
                        query=search_term,
                        ticker=ticker_query or self._maybe_ticker_from_query(search_term),
                        exchange_hint=matched_seed.get("exchange_hint"),
                    )
                )

        ranked = self._rank_candidates(self._dedupe_candidates(candidate_pool))
        if not ranked:
            raise ValueError(f"无法识别 A 股公司: {query}")
        return ranked[0], ranked

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget,
        forms: list[str],
        include_earnings: bool,
    ) -> list[FilingCandidate]:
        del include_earnings
        stock_entry = self._resolve_stock_entry(company)
        candidates: list[FilingCandidate] = []
        seen_ids: set[str] = set()
        plate = self._plate_for_company(company)
        for report_type, category in self._categories_for_target(target=target, forms=forms):
            payload = self._query_announcements(stock=stock_entry["stock"], category=category, plate=plate)
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

    def _search_cninfo(
        self,
        *,
        query: str,
        ticker: str | None,
        exchange_hint: str | None,
    ) -> list[CompanyEntity]:
        payload = {"keyWord": ticker or query, "maxNum": 10, "plate": "szsh"}
        rows = self._post_with_retry(self.TOP_SEARCH_URL, data=payload)

        normalized_query = self._normalize_cn_name(query)
        normalized_ticker = self._normalize_ticker(ticker) if ticker else None
        candidates: list[CompanyEntity] = []
        for row in rows:
            if row.get("category") != "A股" or row.get("delisted") == "true":
                continue
            row_ticker = row.get("code", "")
            row_exchange = self._exchange_for_cn_ticker(row_ticker)
            if not self._exchange_matches(row_exchange, exchange_hint):
                continue
            score = self._score_cninfo_row(row=row, normalized_query=normalized_query, normalized_ticker=normalized_ticker)
            if score < 0.6:
                continue
            candidates.append(
                CompanyEntity(
                    market=Market.cn,
                    company_id=row_ticker,
                    ticker=row_ticker,
                    company_name=row.get("zwjc") or query,
                    exchange=row_exchange,
                    aliases=[query],
                    confidence=score,
                    match_reason=(
                        f"cninfo_exact_ticker:{row_ticker}"
                        if normalized_ticker and self._normalize_ticker(row_ticker) == normalized_ticker
                        else f"cninfo_search:{row.get('zwjc', row_ticker)}"
                    ),
                    metadata={"org_id": row.get("orgId"), "category": row.get("category")},
                )
            )
        return candidates

    def _resolve_stock_entry(self, company: CompanyEntity) -> dict[str, str]:
        payload = {"keyWord": company.ticker or company.company_id, "maxNum": 10, "plate": "szsh"}
        rows = self._post_with_retry(self.TOP_SEARCH_URL, data=payload)

        for row in rows:
            if row.get("code") == (company.ticker or company.company_id):
                org_id = row.get("orgId")
                if not org_id:
                    continue
                return {
                    "code": row["code"],
                    "orgId": org_id,
                    "stock": f'{row["code"]},{org_id}',
                    "name": row.get("zwjc") or company.company_name,
                }

        org_id = company.metadata.get("org_id")
        if org_id and (company.ticker or company.company_id):
            code = company.ticker or company.company_id
            return {"code": code, "orgId": org_id, "stock": f"{code},{org_id}", "name": company.company_name}

        raise ValueError(f"巨潮未找到 {company.company_name}({company.ticker or company.company_id}) 的 orgId")

    def _query_announcements(self, *, stock: str, category: str, plate: str) -> dict:
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
    ) -> FilingCandidate | None:
        title = announcement.get("announcementTitle", "")
        if not title or any(keyword in title for keyword in ("摘要", "英文版")):
            return None
        adjunct_url = announcement.get("adjunctUrl", "")
        announcement_time = announcement.get("announcementTime")
        if not adjunct_url or announcement_time is None:
            return None

        published_at = datetime.fromtimestamp(announcement_time / 1000, tz=timezone.utc).date()
        report_end = self._infer_report_end(title, report_type, published_at)
        announcement_id = announcement.get("announcementId", "")
        org_id = announcement.get("orgId") or stock_entry["orgId"]
        sec_code = announcement.get("secCode") or company.ticker or stock_entry["code"]
        sec_name = announcement.get("secName") or stock_entry["name"]
        announcement_date = published_at.isoformat()
        file_format = (announcement.get("adjunctType") or "pdf").lower()

        return FilingCandidate(
            source_id="cninfo",
            source_name="巨潮资讯",
            source_domain="www.cninfo.com.cn",
            market=Market.cn,
            company_id=sec_code,
            ticker=sec_code,
            company_name=sec_name,
            report_type=report_type,
            report_family=self._family_for_cn_report_type(report_type),
            form=report_type.value,
            title=title,
            accession_number=announcement_id,
            primary_document=adjunct_url.rsplit("/", 1)[-1],
            report_end=report_end,
            published_at=published_at,
            document_url=self.PDF_BASE_URL + adjunct_url.lstrip("/"),
            landing_url=(
                f"{self.DETAIL_URL}?stockCode={sec_code}"
                f"&announcementId={announcement_id}"
                f"&orgId={org_id}"
                f"&announcementTime={announcement_date}"
            ),
            file_format="pdf" if file_format == "pdf" else file_format,
            language="zh-CN",
            metadata={"org_id": org_id, "plate": self._plate_for_company(company)},
        )

    def _categories_for_target(self, *, target: ReportTarget, forms: list[str]) -> list[tuple[ReportType, str]]:
        if forms:
            selected: list[tuple[ReportType, str]] = []
            for form in forms:
                report_type = self._report_type_for_form(form)
                if report_type in self.CATEGORY_BY_REPORT_TYPE:
                    selected.append((report_type, self.CATEGORY_BY_REPORT_TYPE[report_type]))
            return list(dict.fromkeys(selected))
        if target == ReportTarget.annual_report:
            return [(ReportType.annual, self.CATEGORY_BY_REPORT_TYPE[ReportType.annual])]
        if target == ReportTarget.semiannual_report:
            return [(ReportType.semiannual, self.CATEGORY_BY_REPORT_TYPE[ReportType.semiannual])]
        if target == ReportTarget.quarterly_report:
            return [
                (ReportType.q1, self.CATEGORY_BY_REPORT_TYPE[ReportType.q1]),
                (ReportType.q3, self.CATEGORY_BY_REPORT_TYPE[ReportType.q3]),
            ]
        return list(self.CATEGORY_BY_REPORT_TYPE.items())

    def _post_with_retry(
        self,
        url: str,
        data: dict | None = None,
        expect_json: bool = False,
        max_retries: int = 3,
        backoff: float = 2.0,
    ) -> dict | list:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with self._client() as client:
                    response = client.post(url, data=data)
                    response.raise_for_status()
                    return response.json() if expect_json else response.json()
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                status = getattr(exc, "response", None)
                status_code = status.status_code if status else 0
                if not (status_code >= 500 or status_code == 0) or attempt >= max_retries:
                    raise
                time.sleep(backoff * (2**attempt))
        raise last_error

    @staticmethod
    def _client() -> httpx.Client:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)

    @staticmethod
    def _report_type_for_form(form: str) -> ReportType:
        normalized = form.strip().lower().replace("_", "-")
        mapping = {
            "annual": ReportType.annual,
            "annual-report": ReportType.annual,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "interim": ReportType.semiannual,
            "half-year": ReportType.semiannual,
            "q1": ReportType.q1,
            "first-quarter": ReportType.q1,
            "q3": ReportType.q3,
            "third-quarter": ReportType.q3,
            "quarterly": ReportType.quarterly,
        }
        return mapping.get(normalized, ReportType.annual)

    @staticmethod
    def _family_for_cn_report_type(report_type: ReportType) -> ReportFamily:
        if report_type == ReportType.annual:
            return ReportFamily.annual
        if report_type == ReportType.semiannual:
            return ReportFamily.semiannual
        return ReportFamily.quarterly

    @staticmethod
    def _plate_for_company(company: CompanyEntity) -> str:
        ticker = company.ticker or company.company_id
        exchange = (company.exchange or "").upper()
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

    @staticmethod
    def _score_cninfo_row(row: dict, normalized_query: str, normalized_ticker: str | None) -> float:
        row_ticker = CninfoClient._normalize_ticker(row.get("code", ""))
        if normalized_ticker:
            return 0.99 if row_ticker == normalized_ticker else -1.0

        normalized_name = CninfoClient._normalize_cn_name(row.get("zwjc", ""))
        if not normalized_name:
            return -1.0
        if normalized_name == normalized_query:
            return 0.93
        if normalized_query in normalized_name:
            return 0.86
        if normalized_name in normalized_query:
            return 0.82
        return -1.0

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(ch.lower() for ch in text.strip() if not ch.isspace())

    @staticmethod
    def _normalize_cn_name(text: str) -> str:
        normalized = CninfoClient._normalize_text(text)
        for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
            normalized = normalized.removesuffix(suffix)
        normalized = normalized.replace("*", "").replace("股份", "")
        if normalized.startswith("st"):
            normalized = normalized[2:]
        return normalized

    @staticmethod
    def _normalize_ticker(ticker: str | None) -> str:
        if not ticker:
            return ""
        compact = "".join(ch for ch in ticker.strip().upper() if ch.isalnum())
        for prefix in ("SH", "SZ", "BJ"):
            if compact.startswith(prefix) and len(compact) > len(prefix):
                compact = compact[len(prefix) :]
                break
        return compact

    @staticmethod
    def _normalize_exchange_hint(exchange_hint: str | None) -> str | None:
        if not exchange_hint:
            return None
        normalized = exchange_hint.strip().upper()
        aliases = {"SH": "SSE", "SS": "SSE", "SZ": "SZSE", "BJ": "BSE"}
        return aliases.get(normalized, normalized)

    @staticmethod
    def _maybe_ticker_from_query(company_name: str) -> str | None:
        compact = re.sub(r"[\s\-_:./]", "", company_name.strip().upper())
        if compact.startswith(("SH", "SZ", "BJ")) and len(compact) > 2:
            return compact[2:]
        if compact.isdigit() and len(compact) == 6:
            return compact
        return None

    @staticmethod
    def _exchange_for_cn_ticker(ticker: str) -> str:
        if ticker.startswith(("600", "601", "603", "605", "688", "900")):
            return "SSE"
        if ticker.startswith(("000", "001", "002", "003", "300", "301", "200")):
            return "SZSE"
        return "BSE"

    @staticmethod
    def _exchange_matches(exchange: str, exchange_hint: str | None) -> bool:
        if not exchange_hint:
            return True
        exchange_upper = exchange.upper()
        hint = exchange_hint.upper()
        if hint == exchange_upper:
            return True
        if hint == "CN":
            return exchange_upper in {"SSE", "SZSE", "BSE"}
        return False

    @staticmethod
    def _dedupe_candidates(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        best_by_key: dict[tuple[str, str], CompanyEntity] = {}
        for candidate in candidates:
            key = ((candidate.exchange or "").upper(), candidate.ticker or candidate.company_id)
            existing = best_by_key.get(key)
            if existing is None or candidate.confidence > existing.confidence:
                best_by_key[key] = candidate
        return list(best_by_key.values())

    @staticmethod
    def _rank_candidates(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        return sorted(candidates, key=lambda item: (item.confidence, item.exchange or "", item.ticker or ""), reverse=True)

    @staticmethod
    def _match_seed(company_name: str) -> dict | None:
        normalized = CninfoClient._normalize_text(company_name)
        best_seed = None
        best_score = -1
        for seed in CninfoClient._seed_catalog():
            names = [seed["canonical_name"], *seed.get("aliases", []), *seed.get("search_terms", [])]
            for name in names:
                candidate = CninfoClient._normalize_text(name)
                if normalized == candidate:
                    return seed
                if normalized in candidate or candidate in normalized:
                    score = len(candidate)
                    if score > best_score:
                        best_seed = seed
                        best_score = score
        return best_seed

    @staticmethod
    def _seed_search_terms(seed: dict) -> list[str]:
        terms: list[str] = []
        for item in [seed.get("canonical_name"), *(seed.get("search_terms") or []), *(seed.get("aliases") or [])]:
            if item and item not in terms:
                terms.append(item)
        return terms

    @staticmethod
    def _seed_catalog() -> list[dict]:
        data = resources.files("market_report_finder_service.data").joinpath("company_aliases.json").read_text(
            encoding="utf-8"
        )
        payload = json.loads(data)
        return payload["companies"]
