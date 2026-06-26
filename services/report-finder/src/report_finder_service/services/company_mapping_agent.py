import json
from typing import Any

import httpx
from pydantic import BaseModel

from report_finder_service.core.config import settings
from report_finder_service.models.schemas import CompanyEntity


class CompanyMappingDecision(BaseModel):
    should_use_candidate: bool
    selected_ticker: str | None = None
    selected_exchange: str | None = None
    selected_market: str | None = None
    confidence: float = 0.0
    reason: str = ""


class CompanyMappingAgent:
    def is_enabled(self) -> bool:
        return (
            settings.enable_company_mapping_agent
            and bool(settings.company_mapping_model)
            and bool(settings.company_mapping_api_key)
        )

    def choose_candidate(
        self,
        company_name: str,
        candidates: list[CompanyEntity],
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> CompanyEntity | None:
        if not self.is_enabled() or not candidates:
            return None

        decision = self._request_decision(
            company_name=company_name,
            candidates=candidates,
            ticker=ticker,
            exchange_hint=exchange_hint,
        )
        if decision is None or not decision.should_use_candidate:
            return None

        for candidate in candidates:
            if (
                candidate.ticker == (decision.selected_ticker or "")
                and candidate.exchange.upper() == (decision.selected_exchange or "").upper()
            ):
                return candidate.model_copy(
                    update={
                        "confidence": max(candidate.confidence, min(decision.confidence, 0.99)),
                        "match_reason": (
                            f"agent_structured:{decision.reason}"
                            if decision.reason
                            else "agent_structured"
                        ),
                    }
                )
        return None

    def _request_decision(
        self,
        company_name: str,
        candidates: list[CompanyEntity],
        ticker: str | None,
        exchange_hint: str | None,
    ) -> CompanyMappingDecision | None:
        headers = {
            "Authorization": f"Bearer {settings.company_mapping_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.company_mapping_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a company mapping agent. "
                        "Pick exactly one candidate only if the match is well supported. "
                        "Return strict JSON with keys: "
                        "should_use_candidate, selected_ticker, selected_exchange, selected_market, confidence, reason. "
                        "Never invent tickers or exchanges outside the candidate list."
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        company_name=company_name,
                        candidates=candidates,
                        ticker=ticker,
                        exchange_hint=exchange_hint,
                    ),
                },
            ],
        }

        try:
            with httpx.Client(
                timeout=settings.http_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = client.post(
                    f"{settings.company_mapping_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
        except Exception:
            return None

        content = self._extract_content(response.json())
        if not content:
            return None

        try:
            return CompanyMappingDecision.model_validate(json.loads(content))
        except Exception:
            return None

    @staticmethod
    def _build_user_prompt(
        company_name: str,
        candidates: list[CompanyEntity],
        ticker: str | None,
        exchange_hint: str | None,
    ) -> str:
        candidate_lines = []
        for index, candidate in enumerate(candidates, start=1):
            candidate_lines.append(
                {
                    "rank": index,
                    "canonical_name": candidate.canonical_name,
                    "display_name": candidate.display_name,
                    "ticker": candidate.ticker,
                    "exchange": candidate.exchange,
                    "market": candidate.market.value,
                    "aliases": candidate.aliases[:6],
                    "confidence": candidate.confidence,
                    "match_reason": candidate.match_reason,
                }
            )
        return json.dumps(
            {
                "query_company_name": company_name,
                "query_ticker": ticker,
                "query_exchange_hint": exchange_hint,
                "candidates": candidate_lines,
                "decision_rule": (
                    "Prefer exact ticker match first, then exact or very close company-name match, "
                    "and use exchange hint only as a disambiguation signal. "
                    "If no candidate is reliable enough, set should_use_candidate to false."
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str | None:
        choices = payload.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return "".join(text_parts) or None
        return None
