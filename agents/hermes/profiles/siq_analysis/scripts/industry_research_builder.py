#!/usr/bin/env python3
"""Build an external industry research snapshot for SIQ reports.

The snapshot is intentionally evidence-first. It calls Tavily for broad web
search and EXA for neural/search-oriented discovery when credentials are
available, then emits compact result metadata for downstream report sections.
Missing providers are reported as warnings instead of silently inventing
industry context.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILES = [
    PROFILE_DIR / ".env",
    Path.home() / ".hermes" / "profiles" / "siq_tracking" / ".env",
    Path.home() / ".openclaw" / ".env",
    Path.home() / ".openclaw" / "env",
    Path.home() / ".openclaw" / "gateway.systemd.env",
    Path.home() / ".env",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip(chr(34)).strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def clean_text(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 25) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def search_tavily(query: str, limit: int) -> dict[str, Any]:
    api_key = os.environ.get("TAVILY_API_KEY") or os.environ.get("TAVILY_KEY")
    if not api_key:
        return {"ok": False, "provider": "tavily", "query": query, "error": "missing_tavily_api_key", "results": []}
    try:
        payload = post_json(
            "https://api.tavily.com/search",
            {
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "include_answer": True,
                "include_raw_content": False,
                "max_results": limit,
            },
        )
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        return {"ok": False, "provider": "tavily", "query": query, "error": clean_text(exc, 240), "results": []}
    results = []
    for item in payload.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": clean_text(item.get("title"), 140),
            "url": item.get("url"),
            "snippet": clean_text(item.get("content") or item.get("snippet")),
            "published_date": item.get("published_date"),
            "score": item.get("score"),
        })
    return {
        "ok": bool(results),
        "provider": "tavily",
        "query": query,
        "answer": clean_text(payload.get("answer"), 600),
        "results": results,
    }


def search_exa(query: str, limit: int) -> dict[str, Any]:
    api_key = os.environ.get("EXA_API_KEY") or os.environ.get("EXA_KEY")
    if not api_key:
        return {"ok": False, "provider": "exa", "query": query, "error": "missing_exa_api_key", "results": []}
    try:
        payload = post_json(
            "https://api.exa.ai/search",
            {
                "query": query,
                "numResults": limit,
                "type": "auto",
                "contents": {"text": {"maxCharacters": 800}},
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        return {"ok": False, "provider": "exa", "query": query, "error": clean_text(exc, 240), "results": []}
    results = []
    for item in payload.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": clean_text(item.get("title"), 140),
            "url": item.get("url"),
            "snippet": clean_text(item.get("text") or item.get("summary")),
            "published_date": item.get("publishedDate") or item.get("published_date"),
            "score": item.get("score"),
        })
    return {"ok": bool(results), "provider": "exa", "query": query, "results": results}


def company_industry(company: dict[str, Any], fallback_text: str = "") -> str:
    for key in ["industry_sw3", "industry_sw2", "industry_sw1", "industry", "sector"]:
        value = str(company.get(key) or "").strip()
        if value:
            return value
    text = str(fallback_text or "")
    if "汽车" in text:
        if any(term in text for term in ["制造", "整车", "主机厂"]):
            return "汽车制造业"
        return "汽车行业"
    prompt_industry_patterns = [
        (r"([\u4e00-\u9fa5A-Za-z0-9]+(?:制造业|行业|产业|板块|赛道))", 1),
        (r"所属行业[:：\s]*([\u4e00-\u9fa5A-Za-z0-9]+)", 1),
    ]
    for pattern, group in prompt_industry_patterns:
        match = re.search(pattern, text)
        if match:
            candidate = str(match.group(group)).strip("，。、；; ")
            if candidate and candidate not in {"行业", "产业", "板块", "赛道"}:
                return candidate
    return "A股上市公司所属行业"


def unique_queries(values: list[str], limit: int = 10) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = clean_text(value, 180)
        if not query or query in seen:
            continue
        seen.add(query)
        queries.append(query)
        if len(queries) >= limit:
            break
    return queries


def read_prompt_file(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def is_technology_or_manufacturing(company: dict[str, Any]) -> bool:
    text = " ".join(str(company.get(key) or "") for key in [
        "industry_sw3",
        "industry_sw2",
        "industry_sw1",
        "industry",
        "sector",
        "company_short_name",
        "company_full_name",
    ])
    keywords = [
        "制造",
        "汽车",
        "电子",
        "半导体",
        "软件",
        "通信",
        "计算机",
        "设备",
        "机械",
        "新能源",
        "科技",
        "医药",
        "材料",
        "电池",
        "智能",
    ]
    return any(keyword in text for keyword in keywords)


def prompt_indicates_technology_or_manufacturing(text: str) -> bool:
    keywords = ["制造", "汽车", "电子", "半导体", "软件", "通信", "计算机", "设备", "机械", "新能源", "科技", "医药", "材料", "电池", "智能"]
    return any(keyword in text for keyword in keywords)


def build_queries(
    company: dict[str, Any],
    year: int,
    research_prompt: str = "",
    benchmark_hints: list[str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    short_name = str(company.get("company_short_name") or company.get("company_id") or "目标公司")
    stock_code = str(company.get("stock_code") or "").strip()
    prompt_context = " ".join([research_prompt, *(benchmark_hints or [])])
    industry = company_industry(company, prompt_context)
    code_part = f" {stock_code}" if stock_code else ""
    base_queries = [
        f"{industry} 行业 {year} 竞争格局 价格战 需求 出口 政策",
        f"{short_name}{code_part} {industry} {year} 行业位置 同业竞争 毛利率 现金流",
        f"{industry} 行业 {year} 风险 供需 价格 成本 技术趋势",
    ]
    technology_queries: list[str] = []
    if is_technology_or_manufacturing(company) or prompt_indicates_technology_or_manufacturing(prompt_context):
        technology_queries = [
            f"{short_name}{code_part} {year} 研发投入 专利 核心技术 量产 产品结构 毛利率",
            f"{industry} {year} 研发强度 技术路线 专利 量产 产业链 竞争格局",
        ]
    prompt_queries: list[str] = []
    prompt_text = clean_text(research_prompt, 180)
    if prompt_text:
        prompt_queries.append(f"{short_name}{code_part} {year} {prompt_text}")
    hint_queries: list[str] = []
    for hint in benchmark_hints or []:
        hint_text = clean_text(hint, 120)
        if hint_text:
            hint_queries.append(f"{hint_text} {industry} {year} 可比公司 研发 毛利率 现金流 技术路线")
    query_sources = {
        "base": base_queries,
        "technology_or_manufacturing": technology_queries,
        "research_prompt": prompt_queries,
        "benchmark_hints": hint_queries,
    }
    return unique_queries(base_queries + technology_queries + prompt_queries + hint_queries), query_sources


def flatten_results(searches: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    seen: set[str] = set()
    flattened: list[dict[str, Any]] = []
    for search in searches:
        provider = search.get("provider")
        for result in search.get("results", []) or []:
            url = str(result.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            flattened.append({"provider": provider, **result})
            if len(flattened) >= limit:
                return flattened
    return flattened


def build_interpretation(flattened: list[dict[str, Any]], searches: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for search in searches:
        answer = str(search.get("answer") or "").strip()
        if answer:
            lines.append(f"Tavily 综合摘要：{clean_text(answer, 260)}")
            break
    for item in flattened[:6]:
        title = item.get("title") or item.get("url") or "未命名来源"
        snippet = item.get("snippet") or "未返回摘要"
        provider = item.get("provider") or "external"
        lines.append(f"{provider}: {title} - {clean_text(snippet, 220)}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--offline", action="store_true", help="Do not call external providers; emit planned queries only")
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--research-prompt", default="", help="Task prompt used to derive additional Tavily/EXA queries.")
    parser.add_argument("--research-prompt-file", type=Path, help="Read additional task prompt text from a file.")
    parser.add_argument("--benchmark-hint", action="append", default=[], help="Prompt-derived benchmark hint; may be repeated.")
    args = parser.parse_args()

    load_env_files([*DEFAULT_ENV_FILES, *args.env_file])
    company = load_json_if_exists(args.company_dir / "company.json")
    prompt_parts = [args.research_prompt.strip(), read_prompt_file(args.research_prompt_file)]
    research_prompt = "\n\n".join(part for part in prompt_parts if part)
    prompt_context = " ".join([research_prompt, *args.benchmark_hint])
    industry = company_industry(company, prompt_context)
    queries, query_sources = build_queries(company, args.year, research_prompt, args.benchmark_hint)

    searches: list[dict[str, Any]] = []
    if not args.offline:
        for query in queries:
            searches.append(search_tavily(query, args.limit))
            searches.append(search_exa(query, args.limit))

    flattened = flatten_results(searches)
    provider_status = {
        provider: {
            "ok": any(item.get("provider") == provider and item.get("ok") for item in searches),
            "result_count": sum(len(item.get("results", []) or []) for item in searches if item.get("provider") == provider),
            "errors": [item.get("error") for item in searches if item.get("provider") == provider and item.get("error")],
        }
        for provider in ["tavily", "exa"]
    }
    warnings = []
    for provider, status in provider_status.items():
        if not status["ok"]:
            warnings.append(f"{provider}_external_research_unavailable")

    result = {
        "schema_version": 1,
        "generated_by": "industry_research_builder.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": company.get("company_id") or args.company_dir.name,
        "stock_code": company.get("stock_code"),
        "company_short_name": company.get("company_short_name"),
        "report_year": args.year,
        "industry": industry,
        "queries": queries,
        "query_sources": query_sources,
        "provider_status": provider_status,
        "strict_ok": bool(flattened) and all(status["ok"] for status in provider_status.values()),
        "external_result_count": len(flattened),
        "results": flattened,
        "interpretation": build_interpretation(flattened, searches),
        "warnings": warnings,
        "notes": [
            "外部行业资料只用于补充行业趋势、竞争格局和风险触发器；公司财务数字仍以本地 wiki/年报证据为准。",
            "Tavily 用于广域搜索，EXA 用于语义/神经搜索补充；任一缺失时报告必须披露证据缺口。",
        ],
    }

    output = args.output or args.company_dir / "analysis" / ".work" / f"{result['company_id']}-industry_research.json"
    dump_json(output, result)
    print(json.dumps({
        "ok": bool(flattened),
        "strict_ok": result["strict_ok"],
        "output": str(output),
        "external_result_count": len(flattened),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
