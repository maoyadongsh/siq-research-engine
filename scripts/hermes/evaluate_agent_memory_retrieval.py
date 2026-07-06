#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus, agent_memory_service  # noqa: E402


DEFAULT_CASES = [
    {
        "query": "通用问答助手如何做证据定位",
        "profile": "siq_assistant",
        "expected_path_contains": "siq_assistant",
    },
    {
        "query": "一级市场 IC 法务扫描 风险结论",
        "profile": "siq_ic_legal_scanner",
        "expected_path_contains": "siq_ic_legal_scanner",
    },
    {
        "query": "投委会主席如何做最终裁决",
        "profile": "siq_ic_chairman",
        "expected_path_contains": "siq_ic_chairman",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate SIQ agent memory Milvus retrieval quality.")
    parser.add_argument("--cases", default="", help="Optional JSON file with query/profile/expected_path_contains rows.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--embed-url", default=os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL") or "http://127.0.0.1:8013")
    parser.add_argument("--embed-model", default=os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL") or "Qwen3-VL-Embedding-2B")
    return parser.parse_args()


def load_cases(path: str) -> list[dict[str, Any]]:
    if not path:
        return list(DEFAULT_CASES)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("cases file must contain a JSON list")
    return [item for item in payload if isinstance(item, dict)]


async def run_case(case: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    query = str(case.get("query") or "")
    profile = str(case.get("profile") or "siq_assistant")
    expected = str(case.get("expected_path_contains") or profile)
    started = time.perf_counter()
    vector = await agent_memory_service._embed_text(query)
    if not vector:
        return {
            "query": query,
            "profile": profile,
            "status": "failed",
            "reason": "embedding_empty",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    expr = agent_memory_milvus.acl_expr(tenant_id="default", user_id=1, profile=profile)
    hits = agent_memory_milvus.search_records(vector=vector, expr=expr, limit=top_k)
    rank = None
    for index, hit in enumerate(hits, start=1):
        source_path = str(hit.get("source_path") or "")
        if expected in source_path:
            rank = index
            break
    return {
        "query": query,
        "profile": profile,
        "expected_path_contains": expected,
        "status": "passed" if rank is not None else "failed",
        "rank": rank,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "titles": [hit.get("title") for hit in hits],
    }


async def main_async() -> int:
    args = parse_args()
    os.environ["SIQ_AGENT_MEMORY_VECTOR_BACKEND"] = "milvus"
    os.environ["SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL"] = args.embed_url
    os.environ["SIQ_AGENT_MEMORY_EMBEDDING_MODEL"] = args.embed_model
    cases = load_cases(args.cases)
    results = []
    for case in cases:
        results.append(await run_case(case, top_k=args.top_k))
    passed = sum(1 for item in results if item.get("status") == "passed")
    reciprocal_ranks = [1 / item["rank"] for item in results if item.get("rank")]
    summary = {
        "schema_version": "siq_agent_memory_retrieval_eval_v1",
        "case_count": len(results),
        "passed": passed,
        "hit_rate": round(passed / len(results), 4) if results else 0.0,
        "mrr": round(sum(reciprocal_ranks) / len(results), 4) if results else 0.0,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed == len(results) else 1


def main() -> int:
    import anyio

    return anyio.run(main_async)


if __name__ == "__main__":
    raise SystemExit(main())
