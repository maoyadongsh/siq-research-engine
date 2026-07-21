#!/usr/bin/env python3
"""Build portable, minimal real-company snapshots for the financial QA gate.

The source ``document_full`` files remain runtime data. This tool extracts only
the facts exercised by the committed QA cases, their locators, canonical
identity, source URLs, and upstream hashes into ``datasets/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = REPO_ROOT / "db" / "imports" / "backtests"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_financial_qa_benchmark as benchmark  # noqa: E402

DEFAULT_SUITE_ROOT = REPO_ROOT / "datasets" / "eval" / "financial_qa_benchmark" / "v1"
SNAPSHOT_KIND = "public_disclosure_minimal_fact_snapshot"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(binding: dict[str, Any], source_manifest: dict[str, Any]) -> dict[str, Any]:
    configured = binding.get("case_identity") if isinstance(binding.get("case_identity"), dict) else {}
    return {
        field: source_manifest.get(field) or configured.get(field)
        for field in benchmark.CANONICAL_IDENTITY_FIELDS
        if source_manifest.get(field) or configured.get(field)
    }


def _source_urls(source_manifest: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    values = (
        source_manifest.get("source_url"),
        evidence.get("source_url"),
        evidence.get("url"),
    )
    return sorted({str(value) for value in values if str(value or "").startswith(("http://", "https://"))})


def _snapshot_document(
    *,
    source_document: dict[str, Any],
    identity: dict[str, Any],
    fact: Any,
) -> dict[str, Any]:
    evidence = dict(fact.evidence or {})
    table_index = evidence.get("table_index")
    table = {
        key: value
        for key, value in {
            "table_index": table_index,
            "page_number": evidence.get("page_number") or evidence.get("source_page"),
            "bbox": evidence.get("bbox"),
        }.items()
        if value not in (None, "", [], {})
    }
    task = source_document.get("task") if isinstance(source_document.get("task"), dict) else {}
    item = {
        key: value
        for key, value in {
            "canonical_name": fact.canonical_name,
            "name": fact.name,
            "label": fact.label,
            "concept": fact.concept,
            "unit": fact.unit,
            "currency": fact.currency,
            "fact_currency": fact.fact_currency,
            "scale": fact.scale,
            "values": {fact.period_key: fact.value},
            "raw_values": {fact.period_key: fact.raw_value},
            "sources": {fact.period_key: evidence},
        }.items()
        if value not in (None, "", {}, [])
    }
    return {
        "schema_version": "siq_financial_qa_wiki_snapshot_v1",
        "identity_scope": "real_company",
        "snapshot_kind": SNAPSHOT_KIND,
        "task": {
            key: task.get(key)
            for key in ("task_id", "filename")
            if task.get(key) not in (None, "")
        },
        "financial_data": {
            "market": identity.get("market"),
            "company_id": identity.get("company_id"),
            "filing_id": identity.get("filing_id"),
            "report_id": identity.get("filing_id"),
            "ticker": identity.get("ticker"),
            "period_end": identity.get("period_end"),
            "reporting_currency": fact.reporting_currency,
            "presentation_currency": fact.presentation_currency,
            "statements": [
                {
                    "statement_type": fact.statement_type,
                    "unit": fact.unit,
                    "currency": fact.currency,
                    "scale": fact.scale,
                    "items": [item],
                }
            ],
        },
        "content_list_enhanced": {"tables": [table] if table_index is not None else []},
    }


def build_snapshots(suite_root: Path) -> dict[str, Any]:
    binding_path = suite_root / "wiki_static_artifacts.json"
    binding_payload = read_json(binding_path)
    bindings = binding_payload.get("bindings") if isinstance(binding_payload, dict) else None
    if not isinstance(bindings, list):
        raise ValueError(f"invalid bindings file: {binding_path}")
    cases = {str(case.get("case_id")): case for case in benchmark.load_cases(suite_root)}
    output_bindings: list[dict[str, Any]] = []

    for binding in bindings:
        case_id = str(binding.get("case_id") or "")
        case = cases.get(case_id)
        if case is None:
            raise ValueError(f"case not found: {case_id}")
        source_path = benchmark.repo_path(Path(str(binding.get("document_full_path") or "")))
        source_manifest_path = benchmark.repo_path(Path(str(binding.get("manifest_path") or "")))
        if not source_path.is_file() or not source_manifest_path.is_file():
            raise FileNotFoundError(f"runtime source missing for {case_id}: {source_path}")
        source_sha256 = sha256_file(source_path)
        expected_source_sha256 = str(
            binding.get("upstream_document_sha256") or binding.get("document_sha256") or ""
        )
        if expected_source_sha256 and source_sha256 != expected_source_sha256:
            raise ValueError(
                f"upstream document hash changed for {case_id}: "
                f"expected {expected_source_sha256}, got {source_sha256}"
            )

        source_document = read_json(source_path)
        source_manifest = read_json(source_manifest_path)
        effective_case = benchmark._apply_wiki_binding_overrides(case, binding)
        expected_facts = effective_case.get("expected_facts") or []
        if len(expected_facts) != 1:
            raise ValueError(f"{case_id} must bind exactly one snapshot fact")
        fact = benchmark.find_wiki_fact(
            benchmark.normalize_document_facts(source_document),
            expected_facts[0],
        )
        if fact is None:
            raise ValueError(f"expected fact not found in upstream document: {case_id}")

        identity = _identity(binding, source_manifest)
        snapshot_document = _snapshot_document(
            source_document=source_document,
            identity=identity,
            fact=fact,
        )
        snapshot_dir = suite_root / "wiki_static" / case_id
        snapshot_path = snapshot_dir / "document_full.json"
        snapshot_manifest_path = snapshot_dir / "manifest.json"
        write_json(snapshot_path, snapshot_document)
        snapshot_sha256 = sha256_file(snapshot_path)
        fact_payload = asdict(fact)
        evidence = fact_payload.get("evidence") if isinstance(fact_payload.get("evidence"), dict) else {}
        snapshot_manifest = {
            "schema_version": "siq_financial_qa_wiki_snapshot_manifest_v1",
            "snapshot_kind": SNAPSHOT_KIND,
            **identity,
            "source_urls": _source_urls(source_manifest, evidence),
            "artifact_hashes": {"document_full.json": snapshot_sha256},
            "upstream": {
                "document_full_path": source_path.relative_to(REPO_ROOT).as_posix(),
                "document_sha256": source_sha256,
                "manifest_path": source_manifest_path.relative_to(REPO_ROOT).as_posix(),
                "manifest_sha256": sha256_file(source_manifest_path),
            },
        }
        write_json(snapshot_manifest_path, snapshot_manifest)

        output_binding = dict(binding)
        output_binding.update(
            {
                "snapshot_kind": SNAPSHOT_KIND,
                "document_full_path": snapshot_path.relative_to(REPO_ROOT).as_posix(),
                "manifest_path": snapshot_manifest_path.relative_to(REPO_ROOT).as_posix(),
                "manifest_artifact_key": "document_full.json",
                "document_sha256": snapshot_sha256,
                "upstream_document_sha256": source_sha256,
                "case_identity": identity,
                "document_identity": {
                    **identity,
                    "report_type": None,
                    "report_year": None,
                },
                "manifest_identity": identity,
            }
        )
        output_binding.pop("manifest_artifact_hash_path", None)
        output_binding.pop("provenance_checks", None)
        output_bindings.append(output_binding)

    output = {**binding_payload, "bindings": output_bindings}
    write_json(binding_path, output)
    return {"snapshots": len(output_bindings), "binding_path": str(binding_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, default=DEFAULT_SUITE_ROOT)
    args = parser.parse_args()
    result = build_snapshots(args.suite_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
