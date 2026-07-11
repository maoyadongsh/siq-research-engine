from __future__ import annotations

import json
import re
from typing import Any

from services.hermes_model_control import infer_model_mode


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def compact_assist_candidates(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = request_payload.get("candidates") or []
    return [
        {
            "document_url": item.get("document_url"),
            "title": item.get("title"),
            "report_type": item.get("report_type"),
            "report_end": item.get("report_end"),
            "published_at": item.get("published_at"),
        }
        for item in candidates[:30]
        if isinstance(item, dict)
    ]


def assist_system_prompt() -> str:
    return (
        "你是财报下载助手。只能解释用户给定的官方候选列表，不要生成或修改下载 URL。"
        "请输出严格 JSON：{\"intent\":{...},\"candidate_explanations\":[...] }。"
        "candidate_explanations 每项必须包含 document_url、title_zh、report_type_zh、period_zh、recommendation、recommended、warnings。"
        "韩语和日语标题要翻译成中文；推荐项必须与年份、报告类型和官方候选匹配。"
        "如果候选像修订版、摘要、非完整报告或标题/报告期不匹配，请写入 warnings。"
    )


def assist_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt": request_payload.get("prompt"),
        "request": {
            key: request_payload.get(key)
            for key in ("market", "company_name", "ticker", "company_id", "report_year", "report_types")
        },
        "base_assist": base_assist,
        "official_candidates": compact_assist_candidates(request_payload),
    }


def assist_retry_user_payload(request_payload: dict[str, Any], base_assist: dict[str, Any]) -> dict[str, Any]:
    payload = assist_user_payload(request_payload, base_assist)
    payload["retry_hint"] = (
        "上一次增强没有得到可用 JSON。请优先补全 intent，"
        "尤其是把中文境外公司名映射为当地上市主体官方名称与代码；"
        "若没有候选列表，也只返回 intent。"
    )
    return payload


def hermes_mode_for_provider(provider: dict[str, Any]) -> str | None:
    return infer_model_mode(
        provider_name=str(provider.get("providerName") or ""),
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
        base_url=str(provider.get("baseUrl") or ""),
    )


def merge_assist(base_assist: dict[str, Any], llm_assist: dict[str, Any] | None) -> dict[str, Any]:
    if not llm_assist:
        base_assist.setdefault("assistant_mode", "rules")
        return base_assist
    merged = dict(base_assist)
    if isinstance(llm_assist.get("intent"), dict):
        base_intent = dict(merged.get("intent") or {})
        base_intent.update({k: v for k, v in llm_assist["intent"].items() if v not in (None, "", [])})
        merged["intent"] = base_intent
    by_url = {
        item.get("document_url"): item
        for item in merged.get("candidate_explanations", [])
        if isinstance(item, dict) and item.get("document_url")
    }
    for item in llm_assist.get("candidate_explanations") or []:
        if not isinstance(item, dict) or not item.get("document_url"):
            continue
        original = by_url.get(item["document_url"], {})
        original.update({k: v for k, v in item.items() if k != "document_url" and v not in (None, "", [])})
        original["document_url"] = item["document_url"]
        by_url[item["document_url"]] = original
    if by_url:
        ordered_urls = [
            item.get("document_url")
            for item in merged.get("candidate_explanations", [])
            if isinstance(item, dict)
        ]
        merged["candidate_explanations"] = [by_url[url] for url in ordered_urls if url in by_url]
    merged["assistant_mode"] = llm_assist.get("assistant_mode") or "llm"
    return merged
