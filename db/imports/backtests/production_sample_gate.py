"""Production real-sample manifest helpers for market document_full gates."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION = "market_document_full_production_sample_manifest_v1"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_manifest_path(path: str | Path, *, repo_root: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def validate_production_sample_manifest(
    path: Path | None,
    *,
    repo_root: Path,
    market_databases: dict[str, str],
    require_existing: bool = True,
) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "passed": False,
            "reason": "sample manifest disabled",
            "require_existing": require_existing,
            "sample_goal_per_market": 0,
            "market_counts": {},
            "existing_counts": {},
            "missing": {},
            "samples": [],
        }
    if not path.exists():
        return {
            "path": str(path),
            "passed": False,
            "reason": "sample manifest missing",
            "require_existing": require_existing,
            "sample_goal_per_market": 0,
            "market_counts": {},
            "existing_counts": {},
            "missing": {"__manifest__": [str(path)]},
            "samples": [],
        }
    payload = read_json(path)
    schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
    if schema_version != PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION:
        return {
            "path": str(path),
            "passed": False,
            "reason": f"sample manifest schema_version must be {PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION}",
            "require_existing": require_existing,
            "sample_goal_per_market": 0,
            "market_counts": {},
            "existing_counts": {},
            "missing": {"__manifest__": [f"schema_version={schema_version!r}"]},
            "samples": [],
        }
    markets = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, dict):
        return {
            "path": str(path),
            "passed": False,
            "reason": "sample manifest has no markets object",
            "require_existing": require_existing,
            "sample_goal_per_market": 0,
            "market_counts": {},
            "existing_counts": {},
            "missing": {},
            "samples": [],
        }

    raw_sample_goal = payload.get("sample_goal_per_market") or 3
    try:
        sample_goal = int(raw_sample_goal)
    except (TypeError, ValueError):
        return {
            "path": str(path),
            "passed": False,
            "reason": "sample manifest sample_goal_per_market must be a positive integer",
            "require_existing": require_existing,
            "sample_goal_per_market": 0,
            "market_counts": {},
            "existing_counts": {},
            "missing": {"__manifest__": [f"sample_goal_per_market={raw_sample_goal!r}"]},
            "samples": [],
        }
    if sample_goal < 1:
        return {
            "path": str(path),
            "passed": False,
            "reason": "sample manifest sample_goal_per_market must be a positive integer",
            "require_existing": require_existing,
            "sample_goal_per_market": sample_goal,
            "market_counts": {},
            "existing_counts": {},
            "missing": {"__manifest__": [f"sample_goal_per_market={raw_sample_goal!r}"]},
            "samples": [],
        }
    samples: list[dict[str, Any]] = []
    market_counts: dict[str, int] = {}
    existing_counts: dict[str, int] = {}
    missing: dict[str, list[str]] = {}
    for market in market_databases:
        paths = markets.get(market) or []
        if not isinstance(paths, list):
            missing[market] = [f"manifest markets.{market} is not a list"]
            continue
        unique_paths = list(dict.fromkeys(str(item) for item in paths))
        market_counts[market] = len(unique_paths)
        existing_counts[market] = 0
        for item in unique_paths:
            resolved = resolve_manifest_path(item, repo_root=repo_root)
            exists = resolved.exists() if require_existing else None
            if exists:
                existing_counts[market] += 1
            elif require_existing:
                missing.setdefault(market, []).append(item)
            samples.append(
                {
                    "market": market,
                    "path": item,
                    "resolved_path": str(resolved),
                    "exists": exists,
                    "existence_checked": require_existing,
                }
            )
    counts_for_gate = existing_counts if require_existing else market_counts
    passed = all(counts_for_gate.get(market, 0) >= sample_goal for market in market_databases) and not missing
    return {
        "path": str(path),
        "passed": passed,
        "reason": "" if passed else (
            "each market needs at least sample_goal_per_market existing real samples"
            if require_existing
            else "each market needs at least sample_goal_per_market manifest entries"
        ),
        "require_existing": require_existing,
        "sample_goal_per_market": sample_goal,
        "market_counts": market_counts,
        "existing_counts": existing_counts,
        "missing": missing,
        "samples": samples,
    }


def production_sample_cases_from_manifest(
    sample_manifest_result: dict[str, Any],
    *,
    market_databases: dict[str, str],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    market_indexes: dict[str, int] = {}
    for sample in sample_manifest_result.get("samples") or []:
        if not isinstance(sample, dict) or not sample.get("exists"):
            continue
        market = str(sample.get("market") or "").upper()
        if market not in market_databases:
            continue
        market_indexes[market] = market_indexes.get(market, 0) + 1
        cases.append(
            {
                "case_id": f"production_sample_{market.lower()}_{market_indexes[market]:02d}",
                "market": market,
                "document_full_path": sample.get("resolved_path"),
                "production_sample_path": sample.get("path"),
            }
        )
    return cases


def check_production_sample_db_coexistence(
    production_sample_db_results: list[dict[str, Any]],
    *,
    market_schemas: dict[str, str],
    database_url_for_market: Callable[[str, str | None], str],
    relation_exists: Callable[[Any, str, str], bool],
    safe_sql_ident: Callable[[str], str],
    database_url: str | None = None,
    connect: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for result in production_sample_db_results:
        if result.get("skipped") or not result.get("passed"):
            continue
        market = str(result.get("market") or "").upper()
        parse_run_id = result.get("parse_run_id")
        if market in market_schemas and parse_run_id:
            grouped.setdefault(market, []).append(str(parse_run_id))

    if connect is None:
        try:
            import psycopg
        except Exception as exc:
            return [
                {
                    "market": market,
                    "passed": False,
                    "errors": [f"psycopg unavailable: {exc}"],
                    "expected_parse_run_ids": parse_run_ids,
                    "observed_parse_run_ids": [],
                }
                for market, parse_run_ids in sorted(grouped.items())
            ]
        connect = psycopg.connect

    coexistence_results: list[dict[str, Any]] = []
    for market, parse_run_ids in sorted(grouped.items()):
        schema = market_schemas[market]
        unique_parse_run_ids = list(dict.fromkeys(parse_run_ids))
        errors: list[str] = []
        observed_parse_run_ids: list[str] = []
        if len(unique_parse_run_ids) != len(parse_run_ids):
            errors.append(
                f"duplicate parse_run_id values among production samples: {parse_run_ids!r}"
            )
        try:
            placeholders = ", ".join(["%s"] * len(unique_parse_run_ids))
            with connect(database_url_for_market(market, database_url)) as conn:
                if relation_exists(conn, schema, "parse_runs"):
                    rows = conn.execute(
                        f"""
                        select parse_run_id
                        from {safe_sql_ident(schema)}.parse_runs
                        where parse_run_id in ({placeholders})
                        order by parse_run_id
                        """,
                        tuple(unique_parse_run_ids),
                    ).fetchall()
                    observed_parse_run_ids = [str(row[0]) for row in rows]
                else:
                    errors.append("parse_runs table missing")
        except Exception as exc:
            errors.append(str(exc))
        missing = sorted(set(unique_parse_run_ids) - set(observed_parse_run_ids))
        if missing:
            errors.append(f"missing production sample parse_runs after coexistence import: {missing!r}")
        coexistence_results.append(
            {
                "market": market,
                "passed": not errors,
                "errors": errors,
                "expected_parse_run_ids": unique_parse_run_ids,
                "observed_parse_run_ids": observed_parse_run_ids,
                "expected_count": len(unique_parse_run_ids),
                "observed_count": len(observed_parse_run_ids),
            }
        )
    return coexistence_results


__all__ = [
    "PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION",
    "check_production_sample_db_coexistence",
    "production_sample_cases_from_manifest",
    "read_json",
    "resolve_manifest_path",
    "validate_production_sample_manifest",
]
