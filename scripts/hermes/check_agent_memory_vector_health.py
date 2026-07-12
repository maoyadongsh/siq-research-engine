#!/usr/bin/env python3
# isort: skip_file
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus  # noqa: E402


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _embedding_endpoint_configured() -> bool:
    return bool(
        os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL")
        or os.getenv("SIQ_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
    )


def _milvus_env_summary() -> dict[str, bool]:
    return {
        "host_configured": bool(os.getenv("SIQ_MILVUS_HOST") or os.getenv("MILVUS_HOST")),
        "port_configured": bool(os.getenv("SIQ_MILVUS_PORT") or os.getenv("MILVUS_PORT")),
        "user_configured": bool(os.getenv("SIQ_MILVUS_USER") or os.getenv("MILVUS_USER")),
        "password_configured": bool(os.getenv("SIQ_MILVUS_PASSWORD") or os.getenv("MILVUS_PASSWORD")),
        "token_configured": bool(os.getenv("SIQ_MILVUS_TOKEN") or os.getenv("MILVUS_TOKEN")),
        "db_name_configured": bool(os.getenv("SIQ_MILVUS_DB_NAME") or os.getenv("MILVUS_DB_NAME")),
    }


def _collection_health(client: Any, collection: str) -> dict[str, Any]:
    exists = bool(client.has_collection(collection))
    result: dict[str, Any] = {
        "checked": True,
        "name": collection,
        "exists": exists,
        "required_fields_present": False,
        "missing_required_fields": sorted(agent_memory_milvus.REQUIRED_FIELDS),
        "field_count": 0,
    }
    if not exists:
        return result
    fields = agent_memory_milvus._schema_field_names(client, collection)
    missing = sorted(agent_memory_milvus.REQUIRED_FIELDS - fields)
    result.update(
        {
            "required_fields_present": not missing,
            "missing_required_fields": missing,
            "field_count": len(fields),
        }
    )
    return result


def build_health_report(args: argparse.Namespace) -> dict[str, Any]:
    collection = args.collection or agent_memory_milvus.collection_name()
    report: dict[str, Any] = {
        "schema_version": "siq_agent_memory_vector_health_v1",
        "passed": True,
        "required": {
            "milvus": bool(args.require_milvus),
            "collection": bool(args.require_collection),
        },
        "embedding": {
            "endpoint_configured": _embedding_endpoint_configured(),
            "model_configured": bool(
                os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL")
                or os.getenv("SIQ_EMBEDDING_MODEL")
                or os.getenv("EMBEDDING_MODEL")
            ),
        },
        "milvus": {
            "vector_backend": agent_memory_milvus.vector_backend(),
            "enabled": agent_memory_milvus.milvus_enabled(),
            "pymilvus_available": _module_available("pymilvus"),
            "env": _milvus_env_summary(),
            "connectivity": {
                "attempted": False,
                "passed": False,
                "error_type": "",
            },
            "collection": {
                "checked": False,
                "name": collection,
                "exists": False,
                "required_fields_present": False,
                "missing_required_fields": sorted(agent_memory_milvus.REQUIRED_FIELDS),
                "field_count": 0,
            },
        },
    }
    failures: list[str] = []
    if args.require_milvus and not report["milvus"]["enabled"]:
        failures.append("agent memory vector backend is not milvus")
    if not report["milvus"]["pymilvus_available"]:
        if args.require_milvus or args.require_collection:
            failures.append("pymilvus is not installed")
        report["passed"] = not failures
        report["failures"] = failures
        return report

    report["milvus"]["connectivity"]["attempted"] = True
    try:
        client = agent_memory_milvus._client()
        report["milvus"]["connectivity"]["passed"] = True
        report["milvus"]["collection"] = _collection_health(client, collection)
    except Exception as exc:
        report["milvus"]["connectivity"]["error_type"] = type(exc).__name__
        if args.require_milvus or args.require_collection:
            failures.append("Milvus connection failed")

    collection_health = report["milvus"]["collection"]
    if args.require_collection:
        if not collection_health.get("exists"):
            failures.append("agent memory Milvus collection is missing")
        elif not collection_health.get("required_fields_present"):
            failures.append("agent memory Milvus collection schema is incomplete")

    report["passed"] = not failures
    report["failures"] = failures
    return report


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    milvus = report.get("milvus") or {}
    connectivity = milvus.get("connectivity") or {}
    collection = milvus.get("collection") or {}
    lines = [
        "# SIQ Agent Memory Vector Health",
        "",
        f"- Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        f"- Embedding endpoint configured: `{(report.get('embedding') or {}).get('endpoint_configured')}`",
        f"- pymilvus available: `{milvus.get('pymilvus_available')}`",
        f"- Milvus connectivity: `{connectivity.get('passed')}`",
        f"- Collection: `{collection.get('name')}`",
        f"- Collection exists: `{collection.get('exists')}`",
        f"- Required fields present: `{collection.get('required_fields_present')}`",
    ]
    if connectivity.get("error_type"):
        lines.append(f"- Connectivity error type: `{connectivity.get('error_type')}`")
    failures = report.get("failures") or []
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in failures)
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a redacted SIQ agent-memory vector health report.")
    parser.add_argument("--collection", default="")
    parser.add_argument("--require-milvus", action="store_true")
    parser.add_argument("--require-collection", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("artifacts/eval-runs/release/agent_memory_vector_preflight.json"))
    parser.add_argument("--markdown", type=Path, default=Path("artifacts/eval-runs/release/agent_memory_vector_preflight.md"))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_health_report(args)
    write_json(args.output, report)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{'PASS' if report['passed'] else 'FAIL'} agent memory vector health")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
