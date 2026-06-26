import json
import re
from importlib import resources

from report_finder_service.models.schemas import CompanyEntity
from report_finder_service.services.company_mapping_agent import CompanyMappingAgent
from report_finder_service.services.official_company_lookup import OfficialCompanyLookup


class CompanyResolver:
    def __init__(self) -> None:
        self.mapping_agent = CompanyMappingAgent()
        self.official_lookup = OfficialCompanyLookup()

    def resolve(
        self,
        company_name: str,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> CompanyEntity:
        resolved, _ = self.resolve_with_candidates(
            company_name=company_name,
            ticker=ticker,
            exchange_hint=exchange_hint,
        )
        return resolved

    def resolve_with_candidates(
        self,
        company_name: str,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        normalized_exchange = self._normalize_exchange_hint(exchange_hint)
        ticker_query = ticker or self._maybe_ticker_from_query(company_name)

        candidate_pool: list[CompanyEntity] = []
        candidate_pool.extend(
            self.official_lookup.search(
                query=company_name,
                ticker=ticker_query,
                exchange_hint=normalized_exchange,
            )
        )

        matched_seed = self._match_seed(company_name)
        if matched_seed is not None:
            for search_term in self._seed_search_terms(matched_seed):
                search_term_ticker = self._maybe_ticker_from_query(search_term)
                candidate_pool.extend(
                    self.official_lookup.search(
                        query=search_term,
                        ticker=ticker_query or search_term_ticker,
                        exchange_hint=normalized_exchange or matched_seed.get("exchange_hint"),
                    )
                )

        candidate_pool = self._dedupe_candidates(candidate_pool)

        agent_choice = self.mapping_agent.choose_candidate(
            company_name=company_name,
            candidates=candidate_pool,
            ticker=ticker_query,
            exchange_hint=normalized_exchange,
        )
        if agent_choice is not None:
            ranked_candidates = self._rank_candidates(candidate_pool)
            return agent_choice, ranked_candidates

        if candidate_pool:
            ranked_candidates = self._rank_candidates(candidate_pool)
            return ranked_candidates[0], ranked_candidates

        raise ValueError(f"无法识别公司名称: {company_name}")

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(ch.lower() for ch in text.strip() if not ch.isspace())

    @classmethod
    def _normalize_cn_equity_name(cls, text: str) -> str:
        normalized = cls._normalize(text)
        for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
            normalized = normalized.removesuffix(suffix)
        normalized = normalized.replace("*", "").replace("股份", "")
        if normalized.startswith("st"):
            normalized = normalized[2:]
        return normalized

    @staticmethod
    def _normalize_exchange_hint(exchange_hint: str | None) -> str | None:
        if not exchange_hint:
            return None
        normalized = exchange_hint.strip().upper()
        aliases = {
            "SH": "SSE",
            "SS": "SSE",
            "SZ": "SZSE",
            "BJ": "BSE",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _maybe_ticker_from_query(company_name: str) -> str | None:
        compact = re.sub(r"[\s\-_:./]", "", company_name.strip().upper())
        if not compact:
            return None
        if compact.startswith(("SH", "SZ", "BJ")) and len(compact) > 2:
            return compact[2:]
        if compact.isdigit() and len(compact) in {4, 5, 6}:
            if len(compact) == 5 and compact.startswith("0"):
                return compact.lstrip("0") or "0"
            return compact
        return None

    def _match_seed(self, company_name: str) -> dict | None:
        normalized = self._normalize(company_name)
        best_seed = None
        best_score = -1
        for seed in self._seed_catalog():
            names = [seed["canonical_name"], *seed.get("aliases", []), *seed.get("search_terms", [])]
            for name in names:
                candidate = self._normalize(name)
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
    def _dedupe_candidates(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        best_by_key: dict[tuple[str, str], CompanyEntity] = {}
        for candidate in candidates:
            key = (candidate.exchange.upper(), candidate.ticker)
            existing = best_by_key.get(key)
            if existing is None or candidate.confidence > existing.confidence:
                best_by_key[key] = candidate
        return list(best_by_key.values())

    @staticmethod
    def _rank_candidates(candidates: list[CompanyEntity]) -> list[CompanyEntity]:
        return sorted(
            candidates,
            key=lambda item: (
                item.confidence,
                item.market.value,
                item.exchange,
                item.ticker,
            ),
            reverse=True,
        )

    @staticmethod
    def _seed_catalog() -> list[dict]:
        data = resources.files("report_finder_service.data").joinpath("company_aliases.json").read_text(encoding="utf-8")
        payload = json.loads(data)
        return payload["companies"]
