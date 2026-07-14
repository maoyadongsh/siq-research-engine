#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = REPO_ROOT / "db" / "imports" / "backtests"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

from document_fact_normalizer import fact_content_hash  # noqa: E402
from market_document_full_postgres_backtest import (  # noqa: E402
    NormalizedFact,
    decimal_equal,
    document_identity,
    has_reviewable_evidence,
    normalize_document_facts,
    read_json,
    value_within_tolerance,
)

DEFAULT_CASE_ROOT = REPO_ROOT / "datasets" / "eval" / "financial_qa_benchmark" / "v1"
DEFAULT_SUITE_FILE = DEFAULT_CASE_ROOT / "suite.json"
DEFAULT_TRACE_LOG = DEFAULT_CASE_ROOT / "traces" / "p0_golden_traces.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "financial_qa_benchmark.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "financial_qa_benchmark.md"
P0_REQUIRED_RATE = 1.0
IMPLEMENTED_MODES = ("trace-offline", "wiki-static", "fixture-contract")
RESERVED_MODES = ("postgres-fallback",)
VALID_MODES = IMPLEMENTED_MODES + RESERVED_MODES
FIELD_ALIASES = {
    "quote_text": ("quote_text", "quote", "source_quote"),
    "quote": ("quote", "quote_text", "source_quote"),
    "source_page": ("source_page", "page", "page_number"),
    "page": ("page", "source_page", "page_number", "pdf_page", "pdf_page_number"),
    "page_number": ("page_number", "source_page", "page", "pdf_page", "pdf_page_number"),
    "pdf_page": ("pdf_page", "pdf_page_number", "source_page", "page", "page_number"),
}
NUMERIC_EQUIVALENT_FIELDS = {
    "column_index",
    "md_line",
    "page",
    "page_number",
    "pdf_page",
    "pdf_page_number",
    "row_index",
    "source_page",
    "table_index",
}
REQUIRED_CASE_FIELDS = ("case_id", "market", "question", "source_policy")
ANSWER_AUDIT_TRACE_SCHEMA_VERSION = "siq_answer_audit_trace_v1"
REQUIRED_TRACE_OBJECT_FIELDS = ("resolved_company", "resolved_period", "query_plan", "guardrail_result")
REQUIRED_TRACE_LIST_FIELDS = ("wiki_facts", "postgres_facts", "calculator_runs", "citations")
FULLTEXT_SOURCE_TYPES = frozenset({"wiki_report_fulltext", "wiki_document_full"})
FULLTEXT_FALLBACK_REASONS = frozenset({"wiki_missing", "wiki_evidence_missing"})
WIKI_CANONICAL_ALIASES = {
    "revenue": frozenset({"revenue", "operating_revenue"}),
}
CANONICAL_IDENTITY_FIELDS = ("market", "company_id", "filing_id", "ticker", "period_end")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _portable_report_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return "[external]"


def redact_report_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_report_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_report_paths(item) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        return _portable_report_path(value)
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(item)
    return rows


def load_cases(case_root: Path) -> list[dict[str, Any]]:
    case_root = repo_path(case_root)
    suite_path = case_root / "suite.json"
    suite = read_json(suite_path) if suite_path.exists() else {}
    case_file_defaults = suite.get("case_file_defaults") if isinstance(suite, dict) else {}
    if not isinstance(case_file_defaults, dict):
        case_file_defaults = {}
    cases: list[dict[str, Any]] = []
    jsonl_path = case_root / "cases.jsonl"
    if jsonl_path.exists():
        defaults = case_file_defaults.get(jsonl_path.name)
        defaults = defaults if isinstance(defaults, dict) else {}
        for item in load_jsonl(jsonl_path):
            cases.append({**defaults, **item, "_case_file": str(jsonl_path)})
    for path in sorted(case_root.glob("*_cases.json")):
        payload = read_json(path)
        raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(raw_cases, list):
            continue
        defaults = case_file_defaults.get(path.name)
        defaults = defaults if isinstance(defaults, dict) else {}
        for item in raw_cases:
            if isinstance(item, dict):
                cases.append({**defaults, **item, "_case_file": str(path)})
    return cases


def suite_path_setting(case_root: Path, field: str) -> Path | None:
    root = repo_path(case_root)
    suite_path = root / "suite.json"
    if not suite_path.exists():
        return None
    suite = read_json(suite_path)
    raw_path = suite.get(field) if isinstance(suite, dict) else None
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else (suite_path.parent / path).resolve()


def load_wiki_static_contract_cases(path: Path) -> list[dict[str, Any]]:
    """Translate the authoritative synthetic document contract into QA checks."""

    contract_path = repo_path(path)
    payload = read_json(contract_path)
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list):
        raise ValueError("wiki-static contract must contain a cases array")
    cases: list[dict[str, Any]] = []
    for source_case in raw_cases:
        if not isinstance(source_case, dict):
            continue
        assertions = source_case.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            continue
        expected_facts: list[dict[str, Any]] = []
        required_evidence: list[dict[str, Any]] = []
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            evidence = assertion.get("evidence") if isinstance(assertion.get("evidence"), dict) else {}
            fact = {
                key: assertion[key]
                for key in (
                    "statement_type",
                    "canonical_name",
                    "name",
                    "label",
                    "concept",
                    "raw_value",
                    "unit",
                    "currency",
                    "fact_currency",
                    "reporting_currency",
                    "presentation_currency",
                    "scale",
                    "tolerance_ratio",
                )
                if key in assertion
            }
            fact.update(
                {
                    "period": source_case.get("period_key"),
                    "value": assertion.get("expected_value"),
                    "required_evidence": list(evidence),
                    "evidence": evidence,
                }
            )
            expected_facts.append(fact)
            required_evidence.append(evidence)
        expected_identity = source_case.get("expected_identity")
        expected_identity = expected_identity if isinstance(expected_identity, dict) else {}
        cases.append(
            {
                "schema_version": "siq_financial_qa_benchmark_case_v1",
                "case_id": f"synthetic-contract-{source_case.get('case_id')}",
                "source_contract_case_id": source_case.get("case_id"),
                "identity_scope": "synthetic_fixture",
                "tier": "P0",
                "modes": ["fixture-contract"],
                "market": source_case.get("market"),
                "company_id": source_case.get("company_id"),
                "filing_id": expected_identity.get("filing_id"),
                "period": source_case.get("period_key"),
                "question": f"Validate synthetic document contract {source_case.get('case_id')}",
                "source_policy": {
                    "primary": "synthetic_document_contract",
                    "forbid_semantic_numeric_source": True,
                },
                "document_full_path": source_case.get("document_full_path"),
                "expected_content_hash": source_case.get("expected_content_hash"),
                "expected_facts": expected_facts,
                "required_evidence": required_evidence,
                "expected_calculations": [],
                "expected_guardrail": {"should_answer": True},
                "_case_file": str(contract_path),
            }
        )
    return cases


def load_wiki_static_artifact_cases(
    case_root: Path,
    binding_path: Path,
) -> list[dict[str, Any]]:
    payload = read_json(binding_path)
    raw_bindings = payload.get("bindings") if isinstance(payload, dict) else None
    if not isinstance(raw_bindings, list):
        raise ValueError("wiki-static artifact binding must contain a bindings array")
    bindings = {
        str(item.get("case_id") or ""): item
        for item in raw_bindings
        if isinstance(item, dict) and item.get("case_id")
    }
    cases: list[dict[str, Any]] = []
    for case in load_cases(case_root):
        if "wiki-static" not in case_modes(case):
            continue
        binding = bindings.get(str(case.get("case_id") or ""))
        if binding is None:
            cases.append({**case, "_artifact_binding_error": "wiki-static artifact binding missing"})
            continue
        document_path = Path(str(binding.get("document_full_path") or ""))
        manifest_path = Path(str(binding.get("manifest_path") or ""))
        cases.append(
            _apply_wiki_binding_overrides(
                {
                **case,
                "document_full_path": str(repo_path(document_path)),
                "_artifact_binding": {
                    **binding,
                    "document_full_path": str(repo_path(document_path)),
                    "manifest_path": str(repo_path(manifest_path)) if str(manifest_path) else "",
                },
                "_artifact_binding_file": str(binding_path),
                },
                binding,
            )
        )
    return cases


def _apply_wiki_binding_overrides(
    case: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    result = dict(case)
    identity = binding.get("case_identity")
    if not isinstance(identity, dict):
        identity = binding.get("manifest_identity")
    if isinstance(identity, dict):
        for field in ("market", "company_id", "filing_id", "ticker"):
            if field in identity:
                result[field] = identity[field]
        if "period_end" in identity:
            result["period"] = identity["period_end"]
    fact_overrides = binding.get("expected_fact_overrides")
    if isinstance(fact_overrides, list):
        facts = [dict(item) for item in result.get("expected_facts") or [] if isinstance(item, dict)]
        for index, override in enumerate(fact_overrides):
            if index < len(facts) and isinstance(override, dict):
                facts[index].update(override)
        result["expected_facts"] = facts
    required_evidence = binding.get("required_evidence")
    if isinstance(required_evidence, list):
        result["required_evidence"] = required_evidence
    return result


def load_trace_map(trace_log: Path) -> dict[str, dict[str, Any]]:
    traces = load_jsonl(repo_path(trace_log))
    return {str(item.get("question_id") or ""): item for item in traces if item.get("question_id")}


def case_modes(case: dict[str, Any]) -> tuple[str, ...]:
    """Return the implemented benchmark modes a case should run in.

    Missing ``modes`` means the case is part of every currently implemented
    deterministic mode. Reserved future modes must be declared only after their
    evaluator is implemented, otherwise PR gates could silently skip coverage.
    """
    raw = case.get("modes")
    if raw in (None, "", [], {}):
        return IMPLEMENTED_MODES
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_guardrail = case.get("expected_guardrail") if isinstance(case.get("expected_guardrail"), dict) else {}
    should_answer = expected_guardrail.get("should_answer", True)
    modes = case_modes(case)
    if not modes:
        errors.append("case.modes must be a string or non-empty array")
    unknown_modes = [mode for mode in modes if mode not in IMPLEMENTED_MODES]
    if unknown_modes:
        errors.append(f"case.modes contains unsupported modes: {unknown_modes!r}")
    identity_scope = str(case.get("identity_scope") or "").strip()
    company_id = str(case.get("company_id") or "")
    if identity_scope == "synthetic_fixture":
        if modes != ("fixture-contract",):
            errors.append("synthetic fixture cases must run only in fixture-contract mode")
        if ":FIXTURE:" not in company_id:
            errors.append("synthetic fixture company_id must contain ':FIXTURE:'")
    elif identity_scope == "real_company":
        if "fixture-contract" in modes:
            errors.append("real-company QA cases cannot run in fixture-contract mode")
        if ":FIXTURE:" in company_id:
            errors.append("real-company QA company_id cannot contain ':FIXTURE:'")
    for field in REQUIRED_CASE_FIELDS:
        if case.get(field) in (None, "", [], {}):
            errors.append(f"case.{field} missing")
    if "source_policy" in case and not isinstance(case.get("source_policy"), dict):
        errors.append("case.source_policy must be an object")
    expected_facts = case.get("expected_facts")
    if expected_facts in (None, ""):
        if should_answer:
            errors.append("case.expected_facts missing")
    elif not isinstance(expected_facts, list):
        errors.append("case.expected_facts must be an array")
    elif isinstance(expected_facts, list):
        if not expected_facts and should_answer:
            errors.append("case.expected_facts missing")
        for index, fact in enumerate(expected_facts, start=1):
            if not isinstance(fact, dict):
                errors.append(f"case.expected_facts[{index}] must be an object")
                continue
            if not any(fact.get(key) not in (None, "") for key in ("canonical_name", "metric_name", "name", "label", "concept")):
                errors.append(f"case.expected_facts[{index}] missing metric identifier")
            if fact.get("value") in (None, ""):
                errors.append(f"case.expected_facts[{index}].value missing")
    expected_calculations = case.get("expected_calculations")
    if expected_calculations is not None and not isinstance(expected_calculations, list):
        errors.append("case.expected_calculations must be an array")
    elif isinstance(expected_calculations, list):
        for index, calculation in enumerate(expected_calculations, start=1):
            if not isinstance(calculation, dict):
                errors.append(f"case.expected_calculations[{index}] must be an object")
                continue
            if calculation.get("operation") in (None, ""):
                errors.append(f"case.expected_calculations[{index}].operation missing")
            if not any(calculation.get(key) not in (None, "") for key in ("result", "value", "output")):
                errors.append(f"case.expected_calculations[{index}].result missing")
    expected_violations = expected_guardrail.get("claim_violations")
    if expected_violations is not None and not isinstance(expected_violations, list):
        errors.append("case.expected_guardrail.claim_violations must be an array")
    elif isinstance(expected_violations, list):
        for index, violation in enumerate(expected_violations, start=1):
            if not isinstance(violation, dict):
                errors.append(f"case.expected_guardrail.claim_violations[{index}] must be an object")
            elif not violation.get("reason"):
                errors.append(f"case.expected_guardrail.claim_violations[{index}].reason missing")
    return errors


def invalid_case_result(case: dict[str, Any], mode: str, errors: list[str]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": mode,
        "passed": False,
        "facts": [],
        "errors": errors,
    }


def resolve_case_document_path(case: dict[str, Any]) -> Path:
    raw = Path(str(case.get("document_full_path") or ""))
    if raw.is_absolute():
        return raw
    case_file = Path(str(case.get("_case_file") or ""))
    base = case_file.parent if case_file else DEFAULT_CASE_ROOT
    return (base / raw).resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nested_json_value(payload: Any, path: list[Any]) -> Any:
    value = payload
    for segment in path:
        if isinstance(value, dict):
            value = value.get(str(segment))
        elif isinstance(value, list) and isinstance(segment, int) and 0 <= segment < len(value):
            value = value[segment]
        else:
            return None
    return value


def _binding_case_identity(binding: dict[str, Any]) -> dict[str, Any] | None:
    value = binding.get("case_identity")
    if isinstance(value, dict):
        return value
    value = binding.get("manifest_identity")
    return value if isinstance(value, dict) else None


def _case_identity_value(case: dict[str, Any], field: str) -> Any:
    return case.get("period") if field == "period_end" else case.get(field)


def _validate_provenance_checks(binding: dict[str, Any]) -> list[str]:
    checks = binding.get("provenance_checks")
    if checks is None:
        return []
    if not isinstance(checks, list) or not checks:
        return ["artifact binding provenance_checks must be a non-empty array"]
    errors: list[str] = []
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            errors.append(f"provenance check[{index}] must be an object")
            continue
        path = repo_path(Path(str(check.get("path") or "")))
        expected_sha256 = str(check.get("sha256") or "")
        if not path.is_file():
            errors.append(f"provenance check[{index}] file not found: {path}")
            continue
        if not expected_sha256:
            errors.append(f"provenance check[{index}].sha256 missing")
        else:
            observed_sha256 = _sha256_file(path)
            if observed_sha256 != expected_sha256:
                errors.append(
                    f"provenance check[{index}] sha256 expected {expected_sha256!r}, "
                    f"got {observed_sha256!r}"
                )
        expectations = check.get("json_expectations")
        if expectations is None:
            continue
        if not isinstance(expectations, list):
            errors.append(f"provenance check[{index}].json_expectations must be an array")
            continue
        try:
            payload = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"provenance check[{index}] JSON unreadable: {exc}")
            continue
        for expectation_index, expectation in enumerate(expectations, start=1):
            if not isinstance(expectation, dict) or not isinstance(expectation.get("path"), list):
                errors.append(
                    f"provenance check[{index}].json_expectations[{expectation_index}] invalid"
                )
                continue
            observed = _nested_json_value(payload, expectation["path"])
            expected = expectation.get("value")
            if observed != expected:
                errors.append(
                    f"provenance check[{index}] JSON path {expectation['path']!r} "
                    f"expected {expected!r}, got {observed!r}"
                )
    return errors


def validate_wiki_artifact_binding(case: dict[str, Any], document_path: Path) -> list[str]:
    error = case.get("_artifact_binding_error")
    if error:
        return [str(error)]
    binding = case.get("_artifact_binding")
    if not isinstance(binding, dict):
        return []
    status = str(binding.get("status") or "ready")
    if status != "ready":
        return [f"artifact binding blocked: {binding.get('reason') or status}"]
    errors: list[str] = []
    expected_sha256 = str(binding.get("document_sha256") or "")
    if not expected_sha256:
        errors.append("artifact binding document_sha256 missing")
    if document_path.is_file() and expected_sha256:
        observed_sha256 = _sha256_file(document_path)
        if observed_sha256 != expected_sha256:
            errors.append(
                f"artifact sha256 expected {expected_sha256!r}, got {observed_sha256!r}"
            )
    manifest_path = Path(str(binding.get("manifest_path") or ""))
    if not manifest_path.is_file():
        errors.append(f"authoritative manifest not found: {manifest_path}")
        return errors
    manifest = read_json(manifest_path)
    expected_manifest_identity = binding.get("manifest_identity")
    if not isinstance(expected_manifest_identity, dict):
        errors.append("artifact binding manifest_identity missing")
        return errors
    for field, expected in expected_manifest_identity.items():
        if manifest.get(field) != expected:
            errors.append(
                f"manifest.{field} expected {expected!r}, got {manifest.get(field)!r}"
            )
    case_identity = _binding_case_identity(binding)
    if not isinstance(case_identity, dict):
        errors.append("artifact binding case_identity missing")
    else:
        for field in CANONICAL_IDENTITY_FIELDS:
            if field not in case_identity:
                continue
            observed = _case_identity_value(case, field)
            if observed != case_identity[field]:
                errors.append(
                    f"case identity {field} expected {case_identity[field]!r}, got {observed!r}"
                )
            manifest_value = manifest.get(field)
            if manifest_value not in (None, "") and manifest_value != case_identity[field]:
                errors.append(
                    f"manifest.{field} does not preserve case identity: "
                    f"expected {case_identity[field]!r}, got {manifest_value!r}"
                )
    artifact_key = str(binding.get("manifest_artifact_key") or "")
    manifest_hashes = manifest.get("artifact_hashes") if isinstance(manifest.get("artifact_hashes"), dict) else {}
    if artifact_key and manifest_hashes.get(artifact_key) != expected_sha256:
        errors.append(
            f"manifest artifact hash {artifact_key!r} expected {expected_sha256!r}, "
            f"got {manifest_hashes.get(artifact_key)!r}"
        )
    artifact_hash_path = binding.get("manifest_artifact_hash_path")
    if artifact_hash_path is not None:
        if not isinstance(artifact_hash_path, list) or not artifact_hash_path:
            errors.append("manifest_artifact_hash_path must be a non-empty array")
        else:
            observed_manifest_hash = _nested_json_value(manifest, artifact_hash_path)
            if observed_manifest_hash != expected_sha256:
                errors.append(
                    f"manifest artifact hash path {artifact_hash_path!r} expected "
                    f"{expected_sha256!r}, got {observed_manifest_hash!r}"
                )
    errors.extend(_validate_provenance_checks(binding))
    return errors


def fact_key(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "statement_type": expected.get("statement_type"),
        "period": expected.get("period") or expected.get("period_key"),
        "canonical_name": expected.get("canonical_name"),
        "name": expected.get("name"),
        "label": expected.get("label"),
        "concept": expected.get("concept"),
    }


def trace_fact_matches(fact: dict[str, Any], expected: dict[str, Any]) -> bool:
    key = fact_key(expected)
    for field, value in key.items():
        if value in (None, ""):
            continue
        if field == "period":
            observed = fact.get("period") or fact.get("period_key")
        else:
            observed = fact.get(field)
        if observed != value:
            return False
    return True


def wiki_fact_matches(fact: NormalizedFact, expected: dict[str, Any]) -> bool:
    key = fact_key(expected)
    fields = {
        "statement_type": fact.statement_type,
        "period": fact.period_key,
        "canonical_name": fact.canonical_name,
        "name": fact.name,
        "label": fact.label,
        "concept": fact.concept,
    }
    for field, value in key.items():
        if value in (None, ""):
            continue
        observed = fields[field]
        if field == "canonical_name" and value in WIKI_CANONICAL_ALIASES:
            if observed not in WIKI_CANONICAL_ALIASES[value]:
                return False
        elif observed != value:
            return False
    return True


def find_trace_fact(trace: dict[str, Any], expected: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    facts = trace.get("wiki_facts") if isinstance(trace.get("wiki_facts"), list) else []
    for fact in facts:
        if isinstance(fact, dict) and trace_fact_matches(fact, expected):
            return fact, "wiki_facts"
    facts = trace.get("postgres_facts") if isinstance(trace.get("postgres_facts"), list) else []
    for fact in facts:
        if isinstance(fact, dict) and trace_fact_matches(fact, expected):
            return fact, "postgres_facts"
    return None, ""


def find_wiki_fact(facts: list[NormalizedFact], expected: dict[str, Any]) -> NormalizedFact | None:
    candidates = [fact for fact in facts if wiki_fact_matches(fact, expected)]
    expected_value = expected.get("value")
    if expected_value not in (None, ""):
        tolerance = expected.get("tolerance_ratio")
        value_matches = [
            fact
            for fact in candidates
            if value_within_tolerance(fact.value, expected_value, tolerance)
            if tolerance is not None
        ] if tolerance is not None else [
            fact for fact in candidates if decimal_equal(fact.value, expected_value)
        ]
        if value_matches:
            candidates = value_matches
    expected_evidence = expected.get("evidence")
    if isinstance(expected_evidence, dict) and expected_evidence:
        evidence_matches = [
            fact
            for fact in candidates
            if not check_expected_fields(fact.evidence or {}, expected_evidence, tuple(expected_evidence))
        ]
        if evidence_matches:
            candidates = evidence_matches
    return candidates[0] if candidates else None


def check_value(observed: Any, expected: dict[str, Any]) -> tuple[bool, str]:
    expected_value = expected.get("value")
    tolerance_ratio = expected.get("tolerance_ratio")
    if expected_value in (None, ""):
        return True, ""
    if tolerance_ratio is not None:
        passed = value_within_tolerance(observed, expected_value, tolerance_ratio)
        return passed, "" if passed else f"value expected {expected_value!r} within {tolerance_ratio!r}, got {observed!r}"
    passed = decimal_equal(observed, expected_value)
    return passed, "" if passed else f"value expected {expected_value!r}, got {observed!r}"


def observed_field(observed: dict[str, Any], field: str) -> Any:
    for candidate in FIELD_ALIASES.get(field, (field,)):
        value = observed.get(candidate)
        if value not in (None, "", [], {}):
            return value
    return observed.get(field)


def check_expected_fields(observed: dict[str, Any], expected: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for field in fields:
        value = observed_field(observed, field)
        if field == "unit" and field in expected and _normalized_unit(value) == _normalized_unit(expected[field]):
            continue
        if field in expected and field in NUMERIC_EQUIVALENT_FIELDS | {"scale"} and decimal_equal(value, expected[field]):
            continue
        if field in expected and value != expected[field]:
            errors.append(f"{field} expected {expected[field]!r}, got {value!r}")
    return errors


def _normalized_unit(value: Any) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if text.startswith("ISO4217:"):
        text = text.removeprefix("ISO4217:")
    aliases = {
        "RMB IN MILLIONS": "RMB MILLION",
        "RMB MILLIONS": "RMB MILLION",
        "CNY IN MILLIONS": "CNY MILLION",
        "CNY MILLIONS": "CNY MILLION",
        "JPY IN MILLIONS": "JPY MILLION",
        "JPY MILLIONS": "JPY MILLION",
        "KRW IN MILLIONS": "KRW MILLION",
        "KRW MILLIONS": "KRW MILLION",
        "EUR IN MILLIONS": "EUR MILLION",
        "EUR MILLIONS": "EUR MILLION",
    }
    return aliases.get(text, text)


def evidence_fields_present(observed: dict[str, Any], required: list[Any]) -> list[str]:
    errors: list[str] = []
    for field in required:
        if not isinstance(field, str):
            continue
        if observed_field(observed, field) in (None, "", [], {}):
            errors.append(f"evidence.{field} missing")
    return errors


def expected_required_evidence(expected: dict[str, Any]) -> list[str]:
    value = expected.get("required_evidence") or []
    return value if isinstance(value, list) else []


def expected_evidence_for_fact(case: dict[str, Any], expected_fact: dict[str, Any], index: int) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    case_evidence = case.get("required_evidence")
    if isinstance(case_evidence, list) and case_evidence:
        if index - 1 < len(case_evidence) and isinstance(case_evidence[index - 1], dict):
            expected.update(case_evidence[index - 1])
        elif len(case_evidence) == 1 and isinstance(case_evidence[0], dict):
            expected.update(case_evidence[0])
    if isinstance(expected_fact.get("evidence"), dict):
        expected.update(expected_fact["evidence"])
    return expected


def expected_trace_value(case: dict[str, Any], field: str) -> Any:
    expected_trace = case.get("expected_trace") if isinstance(case.get("expected_trace"), dict) else {}
    return expected_trace.get(field)


def expected_trace_has(case: dict[str, Any], field: str) -> bool:
    expected_trace = case.get("expected_trace") if isinstance(case.get("expected_trace"), dict) else {}
    return field in expected_trace


def policy_allows_postgres_fallback(policy: dict[str, Any]) -> bool:
    return policy.get("allow_postgres_fallback", True) is not False


def policy_allows_fulltext_fallback(policy: dict[str, Any], fallback_reason: Any) -> bool:
    if policy.get("allow_fulltext_fallback", True) is False:
        return False
    allowed_reasons = set(policy.get("allowed_fallback_reasons") or [])
    reason = str(fallback_reason or "")
    return reason in FULLTEXT_FALLBACK_REASONS and (not allowed_reasons or reason in allowed_reasons)


def validate_trace_structure(case: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    """Validate the canonical audit envelope before checking its semantics."""
    errors: list[str] = []
    if trace.get("schema_version") != ANSWER_AUDIT_TRACE_SCHEMA_VERSION:
        errors.append(
            f"answer_audit_trace.schema_version expected {ANSWER_AUDIT_TRACE_SCHEMA_VERSION!r}, "
            f"got {trace.get('schema_version')!r}"
        )
    if trace.get("question_id") != case.get("case_id"):
        errors.append(
            f"answer_audit_trace.question_id expected {case.get('case_id')!r}, got {trace.get('question_id')!r}"
        )
    for field in REQUIRED_TRACE_OBJECT_FIELDS:
        if not isinstance(trace.get(field), dict):
            errors.append(f"answer_audit_trace.{field} must be an object")
    for field in REQUIRED_TRACE_LIST_FIELDS:
        if not isinstance(trace.get(field), list):
            errors.append(f"answer_audit_trace.{field} must be an array")
    if "fallback_reason" not in trace:
        errors.append("answer_audit_trace.fallback_reason is required")
    guardrail = trace.get("guardrail_result")
    if isinstance(guardrail, dict) and not isinstance(guardrail.get("blocked"), bool):
        errors.append("answer_audit_trace.guardrail_result.blocked must be a boolean")
    return errors


def check_trace_identity(case: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    company = trace.get("resolved_company") if isinstance(trace.get("resolved_company"), dict) else {}
    period = trace.get("resolved_period") if isinstance(trace.get("resolved_period"), dict) else {}
    if case.get("market") and company.get("market") != case.get("market"):
        errors.append(f"resolved_company.market expected {case.get('market')!r}, got {company.get('market')!r}")
    company_id = company.get("id") or company.get("company_id")
    if case.get("company_id") and company_id != case.get("company_id"):
        errors.append(f"resolved_company.id expected {case.get('company_id')!r}, got {company_id!r}")
    if case.get("filing_id") and period.get("filing_id") != case.get("filing_id"):
        errors.append(f"resolved_period.filing_id expected {case.get('filing_id')!r}, got {period.get('filing_id')!r}")
    if case.get("report_id") and period.get("report_id") != case.get("report_id"):
        errors.append(f"resolved_period.report_id expected {case.get('report_id')!r}, got {period.get('report_id')!r}")
    period_value = period.get("period") or period.get("period_end")
    if case.get("period") and period_value != case.get("period"):
        errors.append(f"resolved_period.period expected {case.get('period')!r}, got {period_value!r}")
    return errors


def check_trace_claim_verifier(
    case: dict[str, Any],
    trace: dict[str, Any],
    *,
    should_answer: bool,
) -> list[str]:
    """Reject runtime traces whose delivered financial answer failed verification.

    Early v1 golden fixtures predate ``claim_verifier_result`` and do not carry
    ``created_at``. Runtime audit traces always carry both fields, so missing
    verifier state is fail-closed for runtime evidence without invalidating the
    versioned legacy fixtures used by the deterministic benchmark.
    """
    if not should_answer or not case.get("expected_facts"):
        return []
    verifier = trace.get("delivered_claim_verifier_result")
    if not isinstance(verifier, dict):
        verifier = trace.get("claim_verifier_result")
    if not isinstance(verifier, dict):
        return ["answer_audit_trace.claim_verifier_result must be an object"] if trace.get("created_at") else []

    errors: list[str] = []
    if verifier.get("allowed") is not True:
        errors.append("claim_verifier_result.allowed must be true for an answer case")
    violations = verifier.get("violations")
    if not isinstance(violations, list):
        errors.append("claim_verifier_result.violations must be an array")
        violations = []
    violation_count = verifier.get("violation_count")
    if violations or (isinstance(violation_count, int) and violation_count != 0):
        errors.append("claim_verifier_result contains violations for an answer case")
    return errors


def check_all_trace_fact_identities(case: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    """Validate every structured financial fact, not only the first expected match."""
    resolved_company = trace.get("resolved_company") if isinstance(trace.get("resolved_company"), dict) else {}
    resolved_period = trace.get("resolved_period") if isinstance(trace.get("resolved_period"), dict) else {}
    expected = {
        "market": case.get("market") or resolved_company.get("market"),
        "company_id": case.get("company_id") or resolved_company.get("id") or resolved_company.get("company_id"),
        "filing_id": case.get("filing_id") or resolved_period.get("filing_id"),
        "parse_run_id": case.get("parse_run_id") or resolved_period.get("parse_run_id"),
    }
    errors: list[str] = []
    for bucket in ("wiki_facts", "postgres_facts"):
        facts = trace.get(bucket) if isinstance(trace.get(bucket), list) else []
        for index, fact in enumerate(facts, start=1):
            if not isinstance(fact, dict):
                errors.append(f"{bucket}[{index}] must be an object")
                continue
            for field, expected_value in expected.items():
                observed_value = fact.get(field)
                if observed_value in (None, "") or expected_value in (None, ""):
                    continue
                if str(observed_value) != str(expected_value):
                    errors.append(
                        f"{bucket}[{index}].{field} expected {expected_value!r}, got {observed_value!r}"
                    )
    return errors


def check_evidence_expectations(
    observed: dict[str, Any],
    expected_fact: dict[str, Any],
    case: dict[str, Any],
    index: int,
) -> list[str]:
    errors = evidence_fields_present(observed, expected_required_evidence(expected_fact))
    expected_evidence = expected_evidence_for_fact(case, expected_fact, index)
    for field, expected_value in expected_evidence.items():
        if field == "page_number_required":
            if expected_value and observed_field(observed, "page_number") in (None, "", [], {}):
                errors.append("evidence.page_number missing")
            continue
        if field == "bbox_required":
            if expected_value and observed_field(observed, "bbox") in (None, "", [], {}):
                errors.append("evidence.bbox missing")
            continue
        if expected_value in (None, "") or isinstance(expected_value, bool):
            continue
        observed_value = observed_field(observed, field)
        if field in NUMERIC_EQUIVALENT_FIELDS and decimal_equal(observed_value, expected_value):
            continue
        if observed_value != expected_value:
            errors.append(f"evidence.{field} expected {expected_value!r}, got {observed_value!r}")
    return errors


def calculation_value(expected: dict[str, Any]) -> Any:
    for key in ("result", "value", "output"):
        if expected.get(key) not in (None, ""):
            return expected.get(key)
    return None


def calculation_matches(run: dict[str, Any], expected: dict[str, Any]) -> bool:
    if expected.get("operation") and run.get("operation") != expected.get("operation"):
        return False
    expected_result = calculation_value(expected)
    if expected_result not in (None, ""):
        observed = run.get("result") if run.get("result") not in (None, "") else run.get("value") or run.get("output")
        tolerance_ratio = expected.get("tolerance_ratio")
        if tolerance_ratio is not None:
            if not value_within_tolerance(observed, expected_result, tolerance_ratio):
                return False
        elif not decimal_equal(observed, expected_result):
            return False
    for field in ("numerator", "denominator", "unit", "currency", "formula"):
        if field in expected and str(run.get(field) or "") != str(expected[field]):
            return False
    return True


def evaluate_expected_calculations(case: dict[str, Any], trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    expected_calculations = case.get("expected_calculations") or []
    if not isinstance(expected_calculations, list) or not expected_calculations:
        return [], []
    runs = trace.get("calculator_runs") if isinstance(trace.get("calculator_runs"), list) else []
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, expected in enumerate(expected_calculations, start=1):
        matched = None
        if isinstance(expected, dict):
            for run in runs:
                if isinstance(run, dict) and calculation_matches(run, expected):
                    matched = run
                    break
        if matched is None:
            operation = expected.get("operation") if isinstance(expected, dict) else None
            expected_result = calculation_value(expected) if isinstance(expected, dict) else None
            message = f"missing calculator_run[{index}] operation={operation!r} result={expected_result!r}"
            errors.append(message)
            results.append({"index": index, "passed": False, "operation": operation, "errors": [message]})
        else:
            results.append(
                {
                    "index": index,
                    "passed": True,
                    "operation": matched.get("operation"),
                    "result": matched.get("result") if matched.get("result") not in (None, "") else matched.get("value"),
                    "errors": [],
                }
            )
    return results, errors


def evaluate_trace_case(case: dict[str, Any], trace: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    fact_results: list[dict[str, Any]] = []
    if trace is None:
        return {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "tier": case.get("tier", "P0"),
            "mode": "trace-offline",
            "passed": False,
            "facts": [],
            "errors": ["missing answer_audit_trace"],
        }

    errors.extend(validate_trace_structure(case, trace))
    policy = case.get("source_policy") if isinstance(case.get("source_policy"), dict) else {}
    fallback_reason = trace.get("fallback_reason")
    errors.extend(check_trace_identity(case, trace))
    postgres_facts = trace.get("postgres_facts") if isinstance(trace.get("postgres_facts"), list) else []
    wiki_facts = trace.get("wiki_facts") if isinstance(trace.get("wiki_facts"), list) else []
    has_fulltext_fact = any(
        isinstance(fact, dict) and str(fact.get("source_type") or "") in FULLTEXT_SOURCE_TYPES
        for fact in wiki_facts
    )
    postgres_fallback_allowed = policy_allows_postgres_fallback(policy)
    if postgres_facts and not postgres_fallback_allowed:
        errors.append("postgres_facts present but source_policy.allow_postgres_fallback is false")
    if postgres_facts and not fallback_reason:
        errors.append("postgres_facts present without fallback_reason")
    fulltext_fallback_allowed = policy_allows_fulltext_fallback(policy, fallback_reason)
    if expected_trace_has(case, "fallback_reason") and fallback_reason != expected_trace_value(case, "fallback_reason"):
        if not (has_fulltext_fact and fulltext_fallback_allowed and expected_trace_value(case, "fallback_reason") in (None, "")):
            errors.append(
                f"fallback_reason expected {expected_trace_value(case, 'fallback_reason')!r}, got {fallback_reason!r}"
            )
    if has_fulltext_fact and not fulltext_fallback_allowed:
        errors.append(
            "fulltext wiki fact requires allowed wiki_missing or wiki_evidence_missing fallback_reason"
        )
    allowed_reasons = set(policy.get("allowed_fallback_reasons") or [])
    if fallback_reason and allowed_reasons and fallback_reason not in allowed_reasons:
        errors.append(f"fallback_reason {fallback_reason!r} is not allowed")

    guardrail = trace.get("guardrail_result") if isinstance(trace.get("guardrail_result"), dict) else {}
    expected_guardrail = case.get("expected_guardrail") if isinstance(case.get("expected_guardrail"), dict) else {}
    should_answer = expected_guardrail.get("should_answer", True)
    if should_answer and guardrail.get("blocked") is True:
        errors.append("guardrail blocked an answer that should answer")
    if not should_answer and guardrail.get("blocked") is not True:
        errors.append("guardrail should block this answer")
    expected_guardrail_reason = expected_guardrail.get("reason")
    if expected_guardrail_reason and guardrail.get("reason") != expected_guardrail_reason:
        errors.append(
            f"guardrail reason expected {expected_guardrail_reason!r}, got {guardrail.get('reason')!r}"
        )
    errors.extend(check_trace_claim_verifier(case, trace, should_answer=bool(should_answer)))
    errors.extend(check_all_trace_fact_identities(case, trace))
    expected_claim_violations = expected_guardrail.get("claim_violations")
    if isinstance(expected_claim_violations, list):
        verifier = trace.get("claim_verifier_result") if isinstance(trace.get("claim_verifier_result"), dict) else {}
        actual_claim_violations = verifier.get("violations") if isinstance(verifier.get("violations"), list) else []
        for index, expected_violation in enumerate(expected_claim_violations, start=1):
            matched = False
            if isinstance(expected_violation, dict):
                for actual_violation in actual_claim_violations:
                    if not isinstance(actual_violation, dict):
                        continue
                    fields_match = True
                    for field, expected_value in expected_violation.items():
                        actual_value = actual_violation.get(field)
                        if field in {"claimed_value", "evidence_value"}:
                            if not decimal_equal(actual_value, expected_value):
                                fields_match = False
                                break
                        elif actual_value != expected_value:
                            fields_match = False
                            break
                    if fields_match:
                        matched = True
                        break
            if not matched:
                errors.append(f"missing claim_verifier violation[{index}]: {expected_violation!r}")
    if expected_trace_value(case, "must_have_wiki_facts") and not wiki_facts:
        errors.append("expected wiki_facts in answer_audit_trace")

    for index, expected in enumerate(case.get("expected_facts") or [], start=1):
        fact, bucket = find_trace_fact(trace, expected)
        fact_errors: list[str] = []
        if fact is None:
            fact_results.append(
                {
                    "index": index,
                    "passed": False,
                    "source_bucket": None,
                    "key_fact_passed": False,
                    "period_passed": False,
                    "unit_currency_passed": False,
                    "evidence_passed": False,
                    "source_policy_passed": False,
                    "calculator_input_ready": False,
                    "errors": [f"missing trace fact: {fact_key(expected)}"],
                }
            )
            continue
        source_type = str(fact.get("source_type") or "")
        source_policy_passed = True
        if bucket == "wiki_facts":
            allowed = set(expected.get("required_source_types") or [])
            if source_type in FULLTEXT_SOURCE_TYPES and fulltext_fallback_allowed:
                allowed.update(FULLTEXT_SOURCE_TYPES)
            if allowed and source_type not in allowed:
                source_policy_passed = False
                fact_errors.append(f"source_type {source_type!r} is not in required_source_types")
        elif bucket == "postgres_facts":
            if not postgres_fallback_allowed:
                source_policy_passed = False
                fact_errors.append("postgres fallback is forbidden by source_policy.allow_postgres_fallback")
            allowed = set(expected.get("fallback_source_types") or [])
            if allowed and source_type not in allowed:
                source_policy_passed = False
                fact_errors.append(f"source_type {source_type!r} is not in fallback_source_types")
            if not fallback_reason:
                source_policy_passed = False
                fact_errors.append("postgres fallback fact has no fallback_reason")
        if policy.get("forbid_semantic_numeric_source") and source_type.startswith("semantic"):
            source_policy_passed = False
            fact_errors.append("semantic source is not allowed for numeric fact")

        value_passed, value_error = check_value(fact.get("value"), expected)
        if value_error:
            fact_errors.append(value_error)
        raw_errors = check_expected_fields(fact, expected, ("raw_value",))
        unit_currency_errors = check_expected_fields(
            fact,
            expected,
            ("unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"),
        )
        fact_errors.extend(raw_errors)
        fact_errors.extend(unit_currency_errors)
        period = fact.get("period") or fact.get("period_key")
        expected_period = expected.get("period") or expected.get("period_key")
        period_passed = expected_period in (None, "") or period == expected_period
        if not period_passed:
            fact_errors.append(f"period expected {expected_period!r}, got {period!r}")
        evidence_errors = check_evidence_expectations(fact, expected, case, index)
        evidence_passed = not evidence_errors
        fact_errors.extend(evidence_errors)
        unit_currency_passed = not unit_currency_errors
        key_fact_passed = value_passed and not raw_errors
        calculator_input_ready = key_fact_passed and period_passed and unit_currency_passed and evidence_passed
        fact_results.append(
            {
                "index": index,
                "passed": calculator_input_ready and source_policy_passed,
                "source_bucket": bucket,
                "source_type": source_type,
                "key_fact_passed": key_fact_passed,
                "period_passed": period_passed,
                "unit_currency_passed": unit_currency_passed,
                "evidence_passed": evidence_passed,
                "source_policy_passed": source_policy_passed,
                "calculator_input_ready": calculator_input_ready,
                "errors": fact_errors,
            }
        )

    calculation_results, calculation_errors = evaluate_expected_calculations(case, trace)
    errors.extend(error for fact in fact_results for error in fact.get("errors") or [])
    errors.extend(calculation_errors)
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": "trace-offline",
        "passed": not errors and (bool(fact_results) or not should_answer),
        "facts": fact_results,
        "calculations": calculation_results,
        "fallback_reason": fallback_reason,
        "guardrail_blocked": guardrail.get("blocked") is True,
        "errors": errors,
    }


def evaluate_wiki_static_case(case: dict[str, Any], *, mode: str = "wiki-static") -> dict[str, Any]:
    errors: list[str] = []
    fact_results: list[dict[str, Any]] = []
    document_path = resolve_case_document_path(case)
    errors.extend(validate_wiki_artifact_binding(case, document_path))
    if not document_path.exists():
        return {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "tier": case.get("tier", "P0"),
            "mode": mode,
            "passed": False,
            "document_full_path": str(document_path),
            "facts": [],
            "errors": [*errors, f"document_full_path not found: {document_path}"],
        }
    document_full = read_json(document_path)
    observed_document_identity = document_identity(document_full, fallback_market=case.get("market"))
    binding = case.get("_artifact_binding") if isinstance(case.get("_artifact_binding"), dict) else None
    expected_document_identity = binding.get("document_identity") if binding else None
    if isinstance(expected_document_identity, dict):
        for field, expected in expected_document_identity.items():
            observed = observed_document_identity.get(field)
            if observed != expected:
                errors.append(
                    f"document identity {field} expected {expected!r}, got {observed!r}"
                )
    else:
        for field in ("market", "company_id", "filing_id"):
            expected = _case_identity_value(case, field)
            observed = observed_document_identity.get(field)
            if expected not in (None, "") and observed not in (None, "") and observed != expected:
                errors.append(f"identity.{field} expected {expected!r}, got {observed!r}")

    canonical_identity = (
        {
            field: _case_identity_value(case, field)
            for field in CANONICAL_IDENTITY_FIELDS
            if _case_identity_value(case, field) not in (None, "")
        }
        if binding
        else observed_document_identity
    )

    facts = normalize_document_facts(document_full)
    expected_content_hash = case.get("expected_content_hash")
    observed_content_hash = fact_content_hash(facts)
    if expected_content_hash and observed_content_hash != expected_content_hash:
        errors.append(
            f"content hash expected {expected_content_hash!r}, got {observed_content_hash!r}"
        )
    for index, expected in enumerate(case.get("expected_facts") or [], start=1):
        fact = find_wiki_fact(facts, expected)
        fact_errors: list[str] = []
        if fact is None:
            fact_results.append(
                {
                    "index": index,
                    "passed": False,
                    "key_fact_passed": False,
                    "period_passed": False,
                    "unit_currency_passed": False,
                    "evidence_passed": False,
                    "source_policy_passed": True,
                    "calculator_input_ready": False,
                    "errors": [f"missing document_full fact: {fact_key(expected)}"],
                }
            )
            continue
        value_passed, value_error = check_value(fact.value, expected)
        if value_error:
            fact_errors.append(value_error)
        observed = {
            "raw_value": fact.raw_value,
            "unit": fact.unit,
            "currency": fact.currency,
            "fact_currency": fact.fact_currency,
            "reporting_currency": fact.reporting_currency,
            "presentation_currency": fact.presentation_currency,
            "scale": fact.scale,
        }
        raw_errors = check_expected_fields(observed, expected, ("raw_value",))
        unit_currency_errors = check_expected_fields(
            observed,
            expected,
            ("unit", "currency", "fact_currency", "reporting_currency", "presentation_currency", "scale"),
        )
        fact_errors.extend(raw_errors)
        fact_errors.extend(unit_currency_errors)
        expected_period = expected.get("period") or expected.get("period_key")
        period_passed = expected_period in (None, "") or fact.period_key == expected_period
        if not period_passed:
            fact_errors.append(f"period expected {expected_period!r}, got {fact.period_key!r}")
        evidence = fact.evidence or {}
        evidence_errors = check_evidence_expectations(evidence, expected, case, index)
        if not evidence_errors and not has_reviewable_evidence(evidence):
            evidence_errors.append(f"expected reviewable evidence, got {evidence!r}")
        fact_errors.extend(evidence_errors)
        key_fact_passed = value_passed and not raw_errors
        unit_currency_passed = not unit_currency_errors
        evidence_passed = not evidence_errors
        calculator_input_ready = key_fact_passed and period_passed and unit_currency_passed and evidence_passed
        fact_results.append(
            {
                "index": index,
                "passed": calculator_input_ready,
                "key_fact_passed": key_fact_passed,
                "period_passed": period_passed,
                "unit_currency_passed": unit_currency_passed,
                "evidence_passed": evidence_passed,
                "source_policy_passed": True,
                "calculator_input_ready": calculator_input_ready,
                "errors": fact_errors,
            }
        )
    errors.extend(error for fact in fact_results for error in fact.get("errors") or [])
    return {
        "case_id": case.get("case_id"),
        "market": case.get("market"),
        "tier": case.get("tier", "P0"),
        "mode": mode,
        "passed": not errors and bool(fact_results),
        "document_full_path": str(document_path),
        "identity": canonical_identity,
        "document_identity": observed_document_identity,
        "facts": fact_results,
        "errors": errors,
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    fact_results = [fact for result in results for fact in result.get("facts") or []]
    calculation_results = [calculation for result in results for calculation in result.get("calculations") or []]
    total = len(fact_results)
    total_calculations = len(calculation_results)
    summary = {
        "cases": len(results),
        "passed_cases": sum(1 for result in results if result.get("passed")),
        "facts": total,
        "calculations": total_calculations,
        "key_fact_accuracy": _rate(sum(1 for fact in fact_results if fact.get("key_fact_passed")), total),
        "period_unit_currency_accuracy": _rate(
            sum(1 for fact in fact_results if fact.get("period_passed") and fact.get("unit_currency_passed")),
            total,
        ),
        "evidence_coverage_rate": _rate(sum(1 for fact in fact_results if fact.get("evidence_passed")), total),
        "source_policy_pass_rate": _rate(sum(1 for fact in fact_results if fact.get("source_policy_passed")), total),
        "calculator_input_ready_rate": _rate(sum(1 for fact in fact_results if fact.get("calculator_input_ready")), total),
        "calculator_run_accuracy": _rate(
            sum(1 for calculation in calculation_results if calculation.get("passed")),
            total_calculations,
        )
        if total_calculations
        else 1.0,
        "guardrail_block_count": sum(1 for result in results if result.get("guardrail_blocked") is True),
    }
    summary["case_pass_rate"] = _rate(summary["passed_cases"], summary["cases"])
    summary["p0_gate_passed"] = summary["passed_cases"] == summary["cases"] and all(
        summary[key] >= P0_REQUIRED_RATE
        for key in (
            "key_fact_accuracy",
            "period_unit_currency_accuracy",
            "evidence_coverage_rate",
            "source_policy_pass_rate",
            "calculator_input_ready_rate",
            "calculator_run_accuracy",
        )
    )
    return summary


def run_benchmark(
    *,
    case_root: Path = DEFAULT_CASE_ROOT,
    trace_log: Path = DEFAULT_TRACE_LOG,
    mode: str = "trace-offline",
    wiki_static_contract: Path | None = None,
) -> dict[str, Any]:
    if mode == "postgres-fallback":
        raise ValueError("postgres-fallback benchmark mode is reserved for the offline PostgreSQL release gate")
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    contract_path = None
    binding_path = None
    if mode == "fixture-contract":
        contract_path = (
            repo_path(wiki_static_contract)
            if wiki_static_contract
            else suite_path_setting(case_root, "fixture_contract")
        )
    elif mode == "wiki-static":
        binding_path = suite_path_setting(case_root, "wiki_static_artifacts")
    if contract_path is not None:
        cases = load_wiki_static_contract_cases(contract_path)
    elif binding_path is not None:
        cases = load_wiki_static_artifact_cases(case_root, binding_path)
    else:
        cases = [case for case in load_cases(case_root) if mode in case_modes(case)]
    traces = load_trace_map(trace_log) if mode == "trace-offline" else {}
    validation_errors = {id(case): validate_case(case) for case in cases}
    if mode == "trace-offline":
        results = [
            invalid_case_result(case, mode, validation_errors[id(case)])
            if validation_errors[id(case)]
            else evaluate_trace_case(case, traces.get(str(case.get("case_id"))))
            for case in cases
        ]
    else:
        results = [
            invalid_case_result(case, mode, validation_errors[id(case)])
            if validation_errors[id(case)]
            else evaluate_wiki_static_case(case, mode=mode)
            for case in cases
        ]
    summary = summarize(results)
    return redact_report_paths({
        "schema_version": "siq_financial_qa_benchmark_report_v1",
        "created_at": now_iso(),
        "mode": mode,
        "case_root": str(repo_path(case_root)),
        "trace_log": str(repo_path(trace_log)) if mode == "trace-offline" else None,
        "wiki_static_contract": str(contract_path) if contract_path is not None else None,
        "wiki_static_artifacts": str(binding_path) if binding_path is not None else None,
        "passed": bool(cases) and summary["p0_gate_passed"] and all(result.get("passed") for result in results),
        "summary": summary,
        "results": results,
    })


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Financial QA Benchmark",
        "",
        f"Mode: `{report.get('mode')}`",
        f"Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Cases: {summary.get('passed_cases', 0)}/{summary.get('cases', 0)}",
        f"- Facts: {summary.get('facts', 0)}",
        f"- Key fact accuracy: {summary.get('key_fact_accuracy', 0):.3f}",
        f"- Period/unit/currency accuracy: {summary.get('period_unit_currency_accuracy', 0):.3f}",
        f"- Evidence coverage rate: {summary.get('evidence_coverage_rate', 0):.3f}",
        f"- Source policy pass rate: {summary.get('source_policy_pass_rate', 0):.3f}",
        f"- Calculator input ready rate: {summary.get('calculator_input_ready_rate', 0):.3f}",
        f"- Calculator run accuracy: {summary.get('calculator_run_accuracy', 0):.3f}",
        "",
        "| Case | Market | Status | Facts |",
        "| --- | --- | --- | ---: |",
    ]
    for result in report.get("results") or []:
        status = "PASS" if result.get("passed") else "FAIL"
        lines.append(f"| {result.get('case_id')} | {result.get('market')} | {status} | {len(result.get('facts') or [])} |")
        for error in result.get("errors") or []:
            lines.append(f"| {result.get('case_id')} error | {result.get('market')} | `{error}` |  |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic SIQ financial QA benchmark.")
    parser.add_argument("--mode", choices=IMPLEMENTED_MODES, default="trace-offline")
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--trace-log", type=Path, default=DEFAULT_TRACE_LOG)
    parser.add_argument("--wiki-static-contract", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_benchmark(
        case_root=args.case_root,
        trace_log=args.trace_log,
        mode=args.mode,
        wiki_static_contract=args.wiki_static_contract,
    )
    output = repo_path(args.output)
    markdown = repo_path(args.markdown)
    write_json(output, report)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if report.get('passed') else 'FAIL'} financial QA benchmark mode={args.mode}")
        print(f"JSON: {output}")
        print(f"Markdown: {markdown}")
        print(f"Key fact accuracy: {report['summary'].get('key_fact_accuracy', 0):.3f}")
        print(f"Evidence coverage rate: {report['summary'].get('evidence_coverage_rate', 0):.3f}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
