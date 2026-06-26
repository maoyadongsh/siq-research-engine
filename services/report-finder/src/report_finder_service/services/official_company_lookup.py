import httpx

from report_finder_service.core.config import settings
from report_finder_service.models.schemas import CompanyEntity, Market


class OfficialCompanyLookup:
    CNINFO_TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"

    def search(
        self,
        query: str,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> list[CompanyEntity]:
        candidates: list[CompanyEntity] = []
        if self._should_search_cn(exchange_hint, ticker):
            candidates.extend(self._search_cninfo(query=query, ticker=ticker, exchange_hint=exchange_hint))
        return self._dedupe(candidates)

    def _search_cninfo(
        self,
        query: str,
        ticker: str | None,
        exchange_hint: str | None,
    ) -> list[CompanyEntity]:
        payload = {"keyWord": ticker or query, "maxNum": 10, "plate": "szsh"}
        with self._client() as client:
            response = client.post(self.CNINFO_TOP_SEARCH_URL, data=payload)
            response.raise_for_status()
            rows = response.json()

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
            score = self._score_cninfo_row(
                row=row,
                normalized_query=normalized_query,
                normalized_ticker=normalized_ticker,
            )
            if score < 0.6:
                continue
            candidates.append(
                CompanyEntity(
                    canonical_name=row.get("zwjc") or query,
                    display_name=row.get("zwjc") or query,
                    aliases=[query],
                    market=Market.cn,
                    exchange=row_exchange,
                    ticker=row_ticker,
                    confidence=score,
                    match_reason=(
                        f"cninfo_exact_ticker:{row_ticker}"
                        if normalized_ticker and self._normalize_ticker(row_ticker) == normalized_ticker
                        else f"cninfo_search:{row.get('zwjc', row_ticker)}"
                    ),
                )
            )
        return candidates

    @staticmethod
    def _score_cninfo_row(
        row: dict,
        normalized_query: str,
        normalized_ticker: str | None,
    ) -> float:
        row_ticker = OfficialCompanyLookup._normalize_ticker(row.get("code", ""))
        if normalized_ticker:
            return 0.99 if row_ticker == normalized_ticker else -1.0

        normalized_name = OfficialCompanyLookup._normalize_cn_name(row.get("zwjc", ""))
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
        normalized = OfficialCompanyLookup._normalize_text(text)
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
        if compact.isdigit() and len(compact) == 5 and compact.startswith("0"):
            compact = compact.lstrip("0") or "0"
        return compact

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
    def _should_search_cn(exchange_hint: str | None, ticker: str | None) -> bool:
        if exchange_hint and exchange_hint.upper() not in {"SSE", "SZSE", "BSE", "CN"}:
            return False
        normalized_ticker = OfficialCompanyLookup._normalize_ticker(ticker)
        if normalized_ticker and normalized_ticker.isalpha():
            return False
        return True

    @staticmethod
    def _dedupe(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        best_by_key: dict[tuple[str, str], CompanyEntity] = {}
        for candidate in candidates:
            key = (candidate.exchange.upper(), candidate.ticker)
            existing = best_by_key.get(key)
            if existing is None or candidate.confidence > existing.confidence:
                best_by_key[key] = candidate
        return list(best_by_key.values())

    @staticmethod
    def _client() -> httpx.Client:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        }
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)
