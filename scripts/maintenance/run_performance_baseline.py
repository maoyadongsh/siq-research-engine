#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import resource
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "ci" / "performance_baseline.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "ci" / "performance_baseline.md"
DEFAULT_MARKET_INGESTION_CASE_ROOT = REPO_ROOT / "eval_datasets" / "market_ingestion_contract" / "cases"
DEFAULT_MARKET_INGESTION_WIKI_ROOT = REPO_ROOT / "eval_datasets" / "market_ingestion_contract" / "wiki"
DEFAULT_DOCUMENT_FULL_CASES = REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "cases.json"
DEFAULT_PRODUCTION_SAMPLE_MANIFEST = (
    REPO_ROOT / "eval_datasets" / "market_document_full_postgres" / "production_sample_manifest.json"
)
DEFAULT_MARKET_PACKAGE = (
    REPO_ROOT
    / "eval_datasets"
    / "market_ingestion_contract"
    / "wiki"
    / "hk"
    / "companies"
    / "00700-SYNTHETIC"
    / "reports"
    / "2025-annual"
)
DEFAULT_REPEAT = 5
DEFAULT_MAX_BENCHMARK_SECONDS = 30.0
PRODUCTION_SAMPLE_ROOT_ENV = "SIQ_MARKET_POSTGRES_SAMPLE_ROOT"
EMBEDDING_BASE_URL_ENVS = (
    "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
    "SIQ_EMBEDDING_BASE_URL",
    "EMBEDDING_BASE_URL",
)
EMBEDDING_MODEL_ENVS = (
    "SIQ_AGENT_MEMORY_EMBEDDING_MODEL",
    "SIQ_EMBEDDING_MODEL",
    "EMBEDDING_MODEL",
)
DEFAULT_AGENT_MEMORY_EMBEDDING_MODEL = "Qwen3-VL-Embedding-2B"
DEFAULT_AGENT_MEMORY_COLLECTION = "siq_agent_memory"

BenchmarkCallable = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class BenchmarkSpec:
    name: str
    fn: BenchmarkCallable
    required: bool = True


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _load_module(name: str, path: Path, *, path_prepend: Path | None = None) -> Any:
    if path_prepend is not None and str(path_prepend) not in sys.path:
        sys.path.insert(0, str(path_prepend))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


@contextmanager
def _temporary_env(updates: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _elapsed_stats(elapsed_ms: list[float]) -> dict[str, float]:
    if not elapsed_ms:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "min": round(min(elapsed_ms), 3),
        "p50": round(statistics.median(elapsed_ms), 3),
        "p95": round(_percentile(elapsed_ms, 0.95), 3),
        "p99": round(_percentile(elapsed_ms, 0.99), 3),
        "max": round(max(elapsed_ms), 3),
    }


def _rss_kb() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _benchmark(
    spec: BenchmarkSpec,
    *,
    repeat: int,
    max_benchmark_seconds: float,
) -> dict[str, Any]:
    elapsed_ms: list[float] = []
    domain: dict[str, Any] = {}
    errors: list[str] = []
    rss_before = _rss_kb()
    for _index in range(repeat):
        started = time.perf_counter()
        try:
            domain = spec.fn()
        except Exception as exc:  # pragma: no cover - exercised through integration-style failures
            domain = {}
            errors.append(f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started
        elapsed_ms.append(elapsed * 1000)
        if elapsed > max_benchmark_seconds:
            errors.append(f"iteration exceeded {max_benchmark_seconds:g}s ceiling: {elapsed:.3f}s")
        if errors:
            break
    rss_after = _rss_kb()
    domain_passed = bool(domain.get("passed")) if domain else False
    skipped = bool(domain.get("skipped")) if domain else False
    return {
        "name": spec.name,
        "required": spec.required,
        "iterations": len(elapsed_ms),
        "elapsed_ms": _elapsed_stats(elapsed_ms),
        "rss_delta_kb": max(0, rss_after - rss_before),
        "skipped": skipped,
        "passed": (domain_passed or (skipped and not spec.required)) and not errors,
        "errors": errors,
        "domain": domain,
    }


def _market_ingestion_contract_benchmark(
    *,
    case_root: Path,
    wiki_root: Path,
) -> dict[str, Any]:
    module = _load_module(
        "siq_run_market_ingestion_eval_for_perf",
        REPO_ROOT / "scripts" / "maintenance" / "run_market_ingestion_eval.py",
    )
    cases = module.load_cases(case_root, legacy_case_root=case_root)
    items = [
        module.evaluate_case(case, wiki_roots=module.wiki_roots_from_base(wiki_root))
        for case in cases
    ]
    summary = module.summarize_items(items)
    strict_reasons = module.strict_failure_reasons(summary)
    return {
        "passed": bool(cases) and not strict_reasons,
        "cases": len(cases),
        "strict_failure_reasons": strict_reasons,
        "summary": {
            "pass": summary.get("pass"),
            "fail": summary.get("fail"),
            "missing_package": summary.get("missing_package"),
            "eval_gate_status": summary.get("eval_gate_status"),
        },
    }


def _document_full_contract_benchmark(
    *,
    cases_path: Path,
    production_sample_manifest_path: Path,
) -> dict[str, Any]:
    module = _load_module(
        "siq_market_document_full_postgres_backtest_for_perf",
        REPO_ROOT / "db" / "imports" / "backtests" / "market_document_full_postgres_backtest.py",
        path_prepend=REPO_ROOT / "db" / "imports" / "backtests",
    )
    summary = module.run_cases(
        cases_path,
        verify_db=False,
        production_sample_manifest_path=production_sample_manifest_path,
        require_production_sample_files=False,
    )
    return {
        "passed": bool(summary.get("passed")) and int(summary.get("case_count") or 0) > 0,
        "cases": summary.get("case_count"),
        "passed_count": summary.get("passed_count"),
        "acceptance_requirements": summary.get("acceptance_requirements"),
        "summary": {
            "market_counts": summary.get("summary", {}).get("market_counts"),
            "fixture_contract": summary.get("acceptance_requirements", {}).get("fixture_contract"),
        },
    }


def _market_chunk_builder_benchmark(*, package_dir: Path) -> dict[str, Any]:
    module = _load_module(
        "siq_ingest_market_evidence_chunks_for_perf",
        REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_market_evidence_chunks.py",
    )
    chunks = module.iter_chunks(package_dir)
    collections = sorted(
        {
            str((chunk.get("metadata") or {}).get("collection") or "")
            for chunk in chunks
            if isinstance(chunk, dict)
        }
    )
    return {
        "passed": bool(chunks),
        "chunks": len(chunks),
        "collections": collections,
        "first_chunk_uid": chunks[0].get("chunk_uid") if chunks else None,
    }


def _production_sample_root(sample_root: Path | None) -> Path | None:
    configured = sample_root or os.environ.get(PRODUCTION_SAMPLE_ROOT_ENV)
    return Path(configured).expanduser().resolve() if configured else None


def _production_sample_manifest_benchmark(
    *,
    production_sample_manifest_path: Path,
    production_sample_root: Path | None,
) -> dict[str, Any]:
    sample_root = _production_sample_root(production_sample_root)
    if sample_root is None:
        return {
            "passed": False,
            "skipped": True,
            "reason": f"{PRODUCTION_SAMPLE_ROOT_ENV} is not configured",
        }
    module = _load_module(
        "siq_production_sample_gate_for_perf",
        REPO_ROOT / "db" / "imports" / "backtests" / "production_sample_gate.py",
    )
    backtest = _load_module(
        "siq_market_document_full_postgres_backtest_for_perf_manifest",
        REPO_ROOT / "db" / "imports" / "backtests" / "market_document_full_postgres_backtest.py",
        path_prepend=REPO_ROOT / "db" / "imports" / "backtests",
    )
    result = module.validate_production_sample_manifest(
        production_sample_manifest_path,
        repo_root=REPO_ROOT,
        market_databases=backtest.MARKET_DATABASES,
        require_existing=True,
        production_sample_root=sample_root,
    )
    sizes = []
    for sample in result.get("samples") or []:
        if isinstance(sample, dict) and sample.get("exists") and sample.get("resolved_path"):
            sizes.append(Path(str(sample["resolved_path"])).stat().st_size)
    return {
        "passed": bool(result.get("passed")) and bool(sizes),
        "sample_root": str(sample_root),
        "sample_goal_per_market": result.get("sample_goal_per_market"),
        "market_counts": result.get("market_counts"),
        "existing_counts": result.get("existing_counts"),
        "missing_count": sum(len(items) for items in (result.get("missing") or {}).values()),
        "samples": len(result.get("samples") or []),
        "existing_samples": len(sizes),
        "total_bytes": sum(sizes),
        "largest_bytes": max(sizes) if sizes else 0,
    }


def _parser_document_full_load_benchmark(
    *,
    production_sample_manifest_path: Path,
    production_sample_root: Path | None,
    max_sample_files: int,
) -> dict[str, Any]:
    sample_root = _production_sample_root(production_sample_root)
    if sample_root is None:
        return {
            "passed": False,
            "skipped": True,
            "reason": f"{PRODUCTION_SAMPLE_ROOT_ENV} is not configured",
        }
    manifest = _production_sample_manifest_benchmark(
        production_sample_manifest_path=production_sample_manifest_path,
        production_sample_root=sample_root,
    )
    if not manifest.get("passed"):
        return {
            "passed": False,
            "skipped": bool(manifest.get("skipped")),
            "reason": manifest.get("reason") or "production sample manifest did not pass",
            "manifest": manifest,
        }
    module = _load_module(
        "siq_production_sample_gate_for_perf_parser",
        REPO_ROOT / "db" / "imports" / "backtests" / "production_sample_gate.py",
    )
    backtest = _load_module(
        "siq_market_document_full_postgres_backtest_for_perf_parser",
        REPO_ROOT / "db" / "imports" / "backtests" / "market_document_full_postgres_backtest.py",
        path_prepend=REPO_ROOT / "db" / "imports" / "backtests",
    )
    result = module.validate_production_sample_manifest(
        production_sample_manifest_path,
        repo_root=REPO_ROOT,
        market_databases=backtest.MARKET_DATABASES,
        require_existing=True,
        production_sample_root=sample_root,
    )
    loaded = 0
    total_bytes = 0
    markets: dict[str, int] = {}
    for sample in result.get("samples") or []:
        if loaded >= max_sample_files:
            break
        if not isinstance(sample, dict) or not sample.get("exists") or not sample.get("resolved_path"):
            continue
        path = Path(str(sample["resolved_path"]))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"passed": False, "reason": f"document_full is not a JSON object: {path}"}
        loaded += 1
        total_bytes += path.stat().st_size
        market = str(sample.get("market") or "UNKNOWN")
        markets[market] = markets.get(market, 0) + 1
    return {
        "passed": loaded > 0,
        "loaded_documents": loaded,
        "max_sample_files": max_sample_files,
        "markets": markets,
        "total_bytes": total_bytes,
    }


def _postgres_agent_view_query_benchmark(
    *,
    database_url: str | None,
) -> dict[str, Any]:
    try:
        import psycopg
    except Exception as exc:
        return {
            "passed": False,
            "skipped": True,
            "reason": f"psycopg unavailable: {exc}",
        }
    backtest = _load_module(
        "siq_market_document_full_postgres_backtest_for_perf_query",
        REPO_ROOT / "db" / "imports" / "backtests" / "market_document_full_postgres_backtest.py",
        path_prepend=REPO_ROOT / "db" / "imports" / "backtests",
    )
    markets: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for market, schema in backtest.MARKET_SCHEMAS.items():
        url = backtest.database_url_for_market(market, database_url)
        query_started = time.perf_counter()
        try:
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"select count(*) from {backtest.safe_sql_ident(schema)}.v_agent_financial_facts",
                    )
                    row = cur.fetchone()
            elapsed_ms = (time.perf_counter() - query_started) * 1000
            row_count = int(row[0] if row else 0)
            markets[market] = {
                "schema": schema,
                "row_count": row_count,
                "elapsed_ms": round(elapsed_ms, 3),
            }
            if row_count <= 0:
                errors.append(f"{market} v_agent_financial_facts returned no rows")
        except Exception as exc:
            errors.append(f"{market}: {type(exc).__name__}: {exc}")
    return {
        "passed": not errors and bool(markets),
        "skipped": bool(errors) and not markets,
        "reason": "PostgreSQL market databases are not reachable" if errors and not markets else "",
        "markets": markets,
        "queries": len(markets),
        "errors": errors,
    }


def _configured_embedding_base_url(value: str | None) -> str | None:
    configured = value
    if configured is None:
        configured = next((os.environ.get(name) for name in EMBEDDING_BASE_URL_ENVS if os.environ.get(name)), None)
    normalized = str(configured or "").strip()
    return normalized or None


def _configured_embedding_model(value: str | None) -> str:
    configured = value
    if configured is None:
        configured = next((os.environ.get(name) for name in EMBEDDING_MODEL_ENVS if os.environ.get(name)), None)
    normalized = str(configured or "").strip()
    return normalized or DEFAULT_AGENT_MEMORY_EMBEDDING_MODEL


def _optional_external_probe_result(*, required: bool, reason: str) -> dict[str, Any]:
    return {
        "passed": False,
        "skipped": not required,
        "reason": reason,
    }


def _agent_memory_eval_module() -> Any:
    return _load_module(
        "siq_agent_memory_retrieval_eval_for_perf",
        REPO_ROOT / "scripts" / "hermes" / "evaluate_agent_memory_retrieval.py",
    )


def _agent_memory_ingest_module() -> Any:
    return _load_module(
        "siq_agent_memory_milvus_ingest_for_perf",
        REPO_ROOT / "scripts" / "hermes" / "ingest_agent_memory_to_milvus.py",
    )


def _load_agent_memory_cases(cases_path: Path | None, *, max_cases: int) -> list[dict[str, Any]]:
    module = _agent_memory_eval_module()
    raw_cases = module.load_cases(str(cases_path) if cases_path else "")
    cases = [item for item in raw_cases if isinstance(item, dict)]
    return cases[: max(1, int(max_cases))]


def _agent_memory_probe_texts(cases_path: Path | None, *, max_texts: int) -> list[str]:
    cases = _load_agent_memory_cases(cases_path, max_cases=max_texts)
    texts = [str(case.get("query") or "").strip() for case in cases if str(case.get("query") or "").strip()]
    if texts:
        return texts[: max(1, int(max_texts))]
    return [
        "SIQ agent memory retrieval latency probe",
        "投委会主席如何做最终裁决",
        "一级市场 IC 法务扫描 风险结论",
    ][: max(1, int(max_texts))]


def _agent_memory_embedding_throughput_benchmark(
    *,
    embedding_base_url: str | None,
    embedding_model: str | None,
    cases_path: Path | None,
    timeout_seconds: float,
    max_texts: int,
    required: bool,
) -> dict[str, Any]:
    base_url = _configured_embedding_base_url(embedding_base_url)
    if base_url is None:
        return _optional_external_probe_result(
            required=required,
            reason=f"embedding base URL is not configured ({', '.join(EMBEDDING_BASE_URL_ENVS)})",
        )
    try:
        ingest = _agent_memory_ingest_module()
        texts = _agent_memory_probe_texts(cases_path, max_texts=max_texts)
        endpoint = ingest.embedding_endpoint(SimpleNamespace(embed_url=base_url))
        model = _configured_embedding_model(embedding_model)
        started = time.perf_counter()
        vectors = ingest.embed_batch(
            texts,
            endpoint=endpoint,
            model=model,
            timeout=float(timeout_seconds),
        )
        elapsed_seconds = max(time.perf_counter() - started, 0.000001)
    except Exception as exc:
        return _optional_external_probe_result(
            required=required,
            reason=f"embedding throughput probe failed: {type(exc).__name__}",
        )
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        return {
            "passed": False,
            "reason": "embedding response size mismatch",
            "input_count": len(texts),
            "vector_count": len(vectors) if isinstance(vectors, list) else 0,
        }
    vector_dims = [len(vector) for vector in vectors if isinstance(vector, list)]
    if len(vector_dims) != len(texts) or any(dim <= 0 for dim in vector_dims):
        return {
            "passed": False,
            "reason": "embedding response contains empty or invalid vectors",
            "input_count": len(texts),
            "vector_count": len(vectors),
            "vector_dims": vector_dims,
        }
    total_chars = sum(len(text) for text in texts)
    return {
        "passed": True,
        "input_count": len(texts),
        "vector_count": len(vectors),
        "vector_dim": vector_dims[0],
        "total_chars": total_chars,
        "model": model,
        "latency_ms": round(elapsed_seconds * 1000, 3),
        "texts_per_second": round(len(texts) / elapsed_seconds, 3),
        "chars_per_second": round(total_chars / elapsed_seconds, 3),
    }


async def _run_agent_memory_retrieval_cases(
    eval_module: Any,
    cases: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        result = await eval_module.run_case(case, top_k=top_k)
        if isinstance(result, dict):
            results.append(result)
        else:
            results.append({"status": "failed", "reason": "case result is not a JSON object"})
    return results


def _float_values(items: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        raw_value = item.get(key)
        if raw_value is None:
            continue
        try:
            values.append(float(raw_value))
        except (TypeError, ValueError):
            continue
    return values


def _reciprocal_ranks(items: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for item in items:
        raw_rank = item.get("rank")
        if raw_rank is None:
            continue
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            values.append(1 / rank)
    return values


def _agent_memory_milvus_retrieval_benchmark(
    *,
    embedding_base_url: str | None,
    embedding_model: str | None,
    collection: str,
    cases_path: Path | None,
    top_k: int,
    max_cases: int,
    required: bool,
) -> dict[str, Any]:
    base_url = _configured_embedding_base_url(embedding_base_url)
    if base_url is None:
        return _optional_external_probe_result(
            required=required,
            reason=f"embedding base URL is not configured ({', '.join(EMBEDDING_BASE_URL_ENVS)})",
        )
    if not _module_available("pymilvus"):
        return _optional_external_probe_result(required=required, reason="pymilvus is not installed")
    try:
        eval_module = _agent_memory_eval_module()
        cases = _load_agent_memory_cases(cases_path, max_cases=max_cases)
        if not cases:
            return {"passed": False, "reason": "agent memory retrieval loaded no cases", "cases": 0}
        updates = {
            "SIQ_AGENT_MEMORY_VECTOR_BACKEND": "milvus",
            "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL": base_url,
            "SIQ_AGENT_MEMORY_EMBEDDING_MODEL": _configured_embedding_model(embedding_model),
            "SIQ_AGENT_MEMORY_MILVUS_COLLECTION": collection.strip() or DEFAULT_AGENT_MEMORY_COLLECTION,
        }
        with _temporary_env(updates):
            results = asyncio.run(
                _run_agent_memory_retrieval_cases(
                    eval_module,
                    cases,
                    top_k=max(1, int(top_k)),
                )
            )
    except Exception as exc:
        return _optional_external_probe_result(
            required=required,
            reason=f"Milvus retrieval probe failed: {type(exc).__name__}",
        )
    passed_cases = sum(1 for item in results if item.get("status") == "passed")
    latencies = _float_values(results, "latency_ms")
    reciprocal_ranks = _reciprocal_ranks(results)
    failures = [
        {
            "profile": item.get("profile"),
            "reason": item.get("reason") or "expected path was not found",
        }
        for item in results
        if item.get("status") != "passed"
    ]
    case_count = len(results)
    return {
        "passed": case_count > 0 and passed_cases == case_count,
        "cases": case_count,
        "passed_cases": passed_cases,
        "hit_rate": round(passed_cases / case_count, 4) if case_count else 0.0,
        "mrr": round(sum(reciprocal_ranks) / case_count, 4) if case_count else 0.0,
        "top_k": max(1, int(top_k)),
        "collection": collection.strip() or DEFAULT_AGENT_MEMORY_COLLECTION,
        "latency_ms": _elapsed_stats(latencies),
        "failures": failures[:10],
    }


def build_benchmark_specs(args: argparse.Namespace) -> list[BenchmarkSpec]:
    case_root = repo_path(args.market_ingestion_case_root)
    wiki_root = repo_path(args.market_ingestion_wiki_root)
    document_full_cases = repo_path(args.document_full_cases)
    production_sample_manifest = repo_path(args.production_sample_manifest)
    market_package = repo_path(args.market_package)
    specs = [
        BenchmarkSpec(
            name="market_ingestion_contract",
            fn=lambda: _market_ingestion_contract_benchmark(case_root=case_root, wiki_root=wiki_root),
        ),
        BenchmarkSpec(
            name="market_document_full_contract",
            fn=lambda: _document_full_contract_benchmark(
                cases_path=document_full_cases,
                production_sample_manifest_path=production_sample_manifest,
            ),
        ),
        BenchmarkSpec(
            name="market_evidence_chunk_builder",
            fn=lambda: _market_chunk_builder_benchmark(package_dir=market_package),
        ),
    ]
    if args.mode == "nightly":
        production_sample_root = repo_path(args.production_sample_root) if args.production_sample_root else None
        nightly_required = bool(args.require_nightly_inputs)
        specs.extend(
            [
                BenchmarkSpec(
                    name="production_sample_manifest_files",
                    fn=lambda: _production_sample_manifest_benchmark(
                        production_sample_manifest_path=production_sample_manifest,
                        production_sample_root=production_sample_root,
                    ),
                    required=nightly_required,
                ),
                BenchmarkSpec(
                    name="parser_document_full_load",
                    fn=lambda: _parser_document_full_load_benchmark(
                        production_sample_manifest_path=production_sample_manifest,
                        production_sample_root=production_sample_root,
                        max_sample_files=max(1, int(args.max_nightly_sample_files)),
                    ),
                    required=nightly_required,
                ),
            ]
        )
        if not args.skip_postgres_nightly:
            specs.append(
                BenchmarkSpec(
                    name="postgres_agent_view_query_latency",
                    fn=lambda: _postgres_agent_view_query_benchmark(database_url=args.database_url),
                    required=nightly_required,
                )
            )
        vector_required = bool(args.require_agent_memory_vector_probes)
        if not args.skip_agent_memory_vector_probes:
            retrieval_cases = repo_path(args.agent_memory_retrieval_cases) if args.agent_memory_retrieval_cases else None
            specs.extend(
                [
                    BenchmarkSpec(
                        name="agent_memory_embedding_throughput",
                        fn=lambda: _agent_memory_embedding_throughput_benchmark(
                            embedding_base_url=args.agent_memory_embedding_base_url,
                            embedding_model=args.agent_memory_embedding_model,
                            cases_path=retrieval_cases,
                            timeout_seconds=float(args.agent_memory_embedding_timeout),
                            max_texts=max(1, int(args.agent_memory_embedding_probe_texts)),
                            required=vector_required,
                        ),
                        required=vector_required,
                    ),
                    BenchmarkSpec(
                        name="agent_memory_milvus_retrieval_latency",
                        fn=lambda: _agent_memory_milvus_retrieval_benchmark(
                            embedding_base_url=args.agent_memory_embedding_base_url,
                            embedding_model=args.agent_memory_embedding_model,
                            collection=args.agent_memory_vector_collection,
                            cases_path=retrieval_cases,
                            top_k=max(1, int(args.agent_memory_retrieval_top_k)),
                            max_cases=max(1, int(args.agent_memory_retrieval_max_cases)),
                            required=vector_required,
                        ),
                        required=vector_required,
                    ),
                ]
            )
    return specs


def run_performance_baseline(
    args: argparse.Namespace,
) -> dict[str, Any]:
    repeat = max(1, int(args.repeat))
    benchmarks = [
        _benchmark(
            spec,
            repeat=repeat,
            max_benchmark_seconds=float(args.max_benchmark_seconds),
        )
        for spec in build_benchmark_specs(args)
    ]
    return {
        "schema_version": "siq_performance_baseline_v1",
        "mode": args.mode,
        "passed": all(item.get("passed") for item in benchmarks),
        "generated_at": now_iso(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": args.cpu_count_override or os.cpu_count(),
        },
        "settings": {
            "repeat": repeat,
            "max_benchmark_seconds": float(args.max_benchmark_seconds),
        },
        "benchmarks": benchmarks,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SIQ Performance Baseline",
        "",
        f"- Mode: `{report.get('mode')}`",
        f"- Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        f"- Generated: `{report.get('generated_at')}`",
        "",
        "| Benchmark | Iterations | P50 ms | P95 ms | P99 ms | Max ms | RSS Δ KB | Domain |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report.get("benchmarks") or []:
        elapsed = item.get("elapsed_ms") or {}
        domain = item.get("domain") or {}
        domain_summary = ", ".join(
            f"{key}={domain.get(key)}"
            for key in (
                "cases",
                "chunks",
                "passed_count",
                "samples",
                "queries",
                "input_count",
                "vector_count",
                "reason",
            )
            if domain.get(key) is not None
        )
        status = "SKIP" if item.get("skipped") else "PASS" if item.get("passed") else "FAIL"
        lines.append(
            f"| {item.get('name')} ({status}) | {item.get('iterations')} | "
            f"{elapsed.get('p50')} | {elapsed.get('p95')} | {elapsed.get('p99')} | {elapsed.get('max')} | "
            f"{item.get('rss_delta_kb')} | {domain_summary or '-'} |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SIQ contract and nightly performance baselines.")
    parser.add_argument("--mode", choices=("contract", "nightly"), default="contract")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--max-benchmark-seconds", type=float, default=DEFAULT_MAX_BENCHMARK_SECONDS)
    parser.add_argument("--market-ingestion-case-root", type=Path, default=DEFAULT_MARKET_INGESTION_CASE_ROOT)
    parser.add_argument("--market-ingestion-wiki-root", type=Path, default=DEFAULT_MARKET_INGESTION_WIKI_ROOT)
    parser.add_argument("--document-full-cases", type=Path, default=DEFAULT_DOCUMENT_FULL_CASES)
    parser.add_argument("--production-sample-manifest", type=Path, default=DEFAULT_PRODUCTION_SAMPLE_MANIFEST)
    parser.add_argument("--production-sample-root", type=Path, default=None)
    parser.add_argument("--max-nightly-sample-files", type=int, default=15)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--require-nightly-inputs", action="store_true")
    parser.add_argument("--skip-postgres-nightly", action="store_true")
    parser.add_argument("--skip-agent-memory-vector-probes", action="store_true")
    parser.add_argument("--require-agent-memory-vector-probes", action="store_true")
    parser.add_argument("--agent-memory-embedding-base-url", default=None)
    parser.add_argument("--agent-memory-embedding-model", default=None)
    parser.add_argument("--agent-memory-embedding-timeout", type=float, default=30.0)
    parser.add_argument("--agent-memory-embedding-probe-texts", type=int, default=3)
    parser.add_argument("--agent-memory-vector-collection", default=DEFAULT_AGENT_MEMORY_COLLECTION)
    parser.add_argument("--agent-memory-retrieval-cases", type=Path, default=None)
    parser.add_argument("--agent-memory-retrieval-top-k", type=int, default=5)
    parser.add_argument("--agent-memory-retrieval-max-cases", type=int, default=3)
    parser.add_argument("--market-package", type=Path, default=DEFAULT_MARKET_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--cpu-count-override", type=int, default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.skip_agent_memory_vector_probes and args.require_agent_memory_vector_probes:
        parser.error("--require-agent-memory-vector-probes cannot be combined with --skip-agent-memory-vector-probes")
    report = run_performance_baseline(args)
    output = repo_path(args.output)
    markdown = repo_path(args.markdown)
    write_json(output, report)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{'PASS' if report['passed'] else 'FAIL'} {len(report['benchmarks'])} {args.mode} performance benchmarks")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
