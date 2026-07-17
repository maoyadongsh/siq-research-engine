from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.openshell import (
    check_sanitized_artifacts,
    check_v06_completion as completion,
    publish_siq_analysis_ab_summary as publisher,
)


def _arm(execution_count: int) -> dict[str, object]:
    return {
        "execution_count": execution_count,
        "task_success_rate": 1.0,
        "answer_citation_rate": 1.0,
        "numeric_accuracy": 1.0,
        "hallucination_block_rate": 1.0,
        "evidence_coverage": 1.0,
        "tool_success_rate": 1.0,
        "tool_error_rate": 0.0,
        "tool_retry_rate": 0.0,
        "tool_recovery_rate": 1.0,
        "tool_unrecovered_failure_rate": 0.0,
        "fallback_success_rate": None,
        "fallback_telemetry_coverage": None,
        "fallback_expected_execution_count": 0,
        "fallback_telemetry_expected_count": 0,
        "report_completeness": 1.0,
        "timeout_rate": 0.0,
        "policy_false_positive_rate": 0.0,
        "sample_counts": {
            "answer_citation_rate": execution_count,
            "numeric_accuracy": execution_count,
            "hallucination_block_rate": execution_count,
            "evidence_coverage": execution_count,
            "tool_success_rate": execution_count,
            "report_completeness": execution_count,
            "policy_false_positive_rate": execution_count,
        },
        "contract_failure_count": 0,
        "unexpected_fallback_count": 0,
        "tool_runtime": {
            "attempt_count": execution_count,
            "success_count": execution_count,
            "failure_count": 0,
            "retry_count": 0,
            "failed_tool_state_count": 0,
            "recovered_tool_state_count": 0,
            "unrecovered_tool_state_count": 0,
        },
        "runtime_telemetry": {
            "expected_primary_provider": "primary-provider",
            "expected_primary_model": "primary-model",
            "telemetry_count": execution_count,
            "requested_model_match_count": execution_count,
            "configured_route_match_count": execution_count,
            "effective_route_match_count": execution_count,
            "fallback_inactive_count": execution_count,
            "configured_routes": [
                {"provider": "primary-provider", "model": "primary-model", "count": execution_count}
            ],
            "effective_routes": [
                {"provider": "primary-provider", "model": "primary-model", "count": execution_count}
            ],
        },
        "latency_ms": {
            "ttft_sample_count": execution_count,
            "ttft_p50": 10.0,
            "ttft_p95": 20.0,
            "total_sample_count": execution_count,
            "total_p50": 100.0,
            "total_p95": 150.0,
        },
    }


def _summary(evaluation_id: str) -> dict[str, object]:
    case_count = 10
    repetitions = 3
    arm = _arm(case_count * repetitions)
    comparison, reasons = completion.ab_eval.quality_comparison(
        arm,
        arm,
        case_count=case_count,
        repetitions=repetitions,
        require_fallback=False,
    )
    return {
        "schema_version": completion.AB_SUMMARY_SCHEMA,
        "evaluation_id": evaluation_id,
        "prerequisites_path": f"var/openshell/eval/{evaluation_id}/prerequisites.json",
        "prerequisites_sha256": "0" * 64,
        "dataset_sha256": "a" * 64,
        "dataset_schema_version": completion.ab_eval.DATASET_SCHEMA_VERSION,
        "profile": "siq_analysis",
        "model": "pinned-model-alias",
        "temperature": 0.1,
        "case_count": case_count,
        "repetitions": repetitions,
        "execution_count": case_count * repetitions * 2,
        "interleaving": "alternating_case_and_repetition",
        "arms": {"host": arm, "openshell": json.loads(json.dumps(arm))},
        "comparison": comparison,
        "quality_gate": {
            "passed": not reasons,
            "failure_reasons": reasons,
            "cutover_performed": False,
            "recommendation": "manual_review_only_no_automatic_cutover",
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "t8_exporter_ready": True,
        },
    }


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "artifacts/openshell/v0.6").mkdir(parents=True)
    return root


def _write_source(root: Path, evaluation_id: str, payload: object) -> Path:
    path = root / "var/openshell/eval" / evaluation_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_publish_valid_summary_preserves_schema_and_is_deterministic(tmp_path: Path) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    source = _write_source(root, evaluation_id, _summary(evaluation_id))

    json_path, markdown_path = publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)

    payload = json.loads(json_path.read_text(encoding="ascii"))
    assert payload == json.loads(source.read_text(encoding="utf-8"))
    assert payload["schema_version"] == completion.AB_SUMMARY_SCHEMA
    assert completion._validate_ab_summary(payload) is True
    assert json_path.read_bytes() == publisher._canonical_json(payload)
    assert markdown_path.read_bytes() == publisher._markdown(payload)
    assert not check_sanitized_artifacts.scan_paths([json_path, markdown_path])
    assert stat.S_IMODE(json_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(markdown_path.stat().st_mode) == 0o600


def test_publish_rejects_invalid_schema(tmp_path: Path) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    payload = _summary(evaluation_id)
    payload["schema_version"] = "siq.openshell.siq-analysis-ab-summary.v2"
    _write_source(root, evaluation_id, payload)

    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_schema_invalid"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)


def test_publish_rejects_source_change_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    source = _write_source(root, evaluation_id, _summary(evaluation_id))
    original = publisher._read_descriptor

    def changing_read(descriptor: int) -> bytes:
        content = original(descriptor)
        source.write_bytes(content + b" ")
        source.chmod(0o600)
        return content

    monkeypatch.setattr(publisher, "_read_descriptor", changing_read)
    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_source_changed"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)


def test_publish_requires_explicit_replace_for_existing_target(tmp_path: Path) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    _write_source(root, evaluation_id, _summary(evaluation_id))
    json_path = root / publisher.OUTPUT_JSON
    json_path.write_text("existing\n", encoding="ascii")

    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_output_exists"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)
    assert json_path.read_text(encoding="ascii") == "existing\n"
    assert not (root / publisher.OUTPUT_MARKDOWN).exists()

    publisher.publish_summary(project_root=root, evaluation_id=evaluation_id, replace=True)
    assert json.loads(json_path.read_text(encoding="ascii"))["evaluation_id"] == evaluation_id


@pytest.mark.parametrize(
    ("field", "value"),
    (("prompt", "private request body"), ("api_key", "real-looking-secret-value")),
)
def test_publish_rejects_sensitive_extra_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    payload = _summary(evaluation_id)
    payload[field] = value
    _write_source(root, evaluation_id, payload)

    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_not_sanitized"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)


def test_publish_rejects_duplicate_keys_and_non_private_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    source = _write_source(root, evaluation_id, _summary(evaluation_id))
    source.write_text('{"schema_version":"one","schema_version":"two"}', encoding="ascii")
    source.chmod(0o600)
    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_duplicate_json_key"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)

    _write_source(root, evaluation_id, _summary(evaluation_id)).chmod(0o644)
    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_source_invalid"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)


def test_publish_rejects_symlink_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    evaluation_id = "eval-live-20260717"
    source = _write_source(root, evaluation_id, _summary(evaluation_id))
    target = source.with_name("real-summary.json")
    source.rename(target)
    source.symlink_to(target.name)

    with pytest.raises(publisher.AbSummaryPublishError, match="ab_summary_path_invalid"):
        publisher.publish_summary(project_root=root, evaluation_id=evaluation_id)
