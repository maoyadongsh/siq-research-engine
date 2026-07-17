#!/usr/bin/env python3
"""Build a strict receipt for a completed normal A/B run and its still-active sandbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    check_sanitized_artifacts,
    check_siq_analysis_ab_prerequisites as ab_prerequisites,
    formal_business_route_evidence as evidence_contract,
    formal_runtime_contract,
    gateway_runtime_identity,
    prepare_siq_analysis_ab_eval as ab_prepare,
    run_formal_filesystem_boundary as formal_filesystem,
    run_siq_analysis_ab_eval as ab_eval,
)
from scripts.openshell.run_siq_analysis_fallback_drill import _api_runtime_receipt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = Path("artifacts/openshell/v0.6/formal-business-route.sanitized.json")
DEFAULT_MARKDOWN = Path("artifacts/openshell/v0.6/formal-business-route.sanitized.md")
MAX_INPUT_BYTES = 64 * 1024 * 1024
FORMAL_RUN_RE = re.compile(r"formal-[0-9a-f]{12}\Z")
FORBIDDEN_LIVE_ID_RE = re.compile(r"(?:synthetic|fixture|fake|test)", re.IGNORECASE)


class BusinessRouteReceiptError(RuntimeError):
    """Stable failure that never includes private input, paths, or credentials."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "business_route_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass(frozen=True)
class NormalEvaluation:
    evaluation_id: str
    dataset: ab_eval.EvaluationDataset
    dataset_sha256: str
    raw: Mapping[str, Any]
    raw_sha256: str
    summary: Mapping[str, Any]
    summary_sha256: str
    prerequisites_sha256: str
    provenance: Mapping[str, Any]
    provenance_sha256: str
    openshell_records: tuple[Mapping[str, Any], ...]


def _sha256(content: bytes | str) -> str:
    return hashlib.sha256(content.encode("utf-8") if isinstance(content, str) else content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = child
    return value


def _stable_file(path: Path, *, private: bool, maximum: int = MAX_INPUT_BYTES) -> bytes:
    descriptor = -1
    try:
        expected = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(expected.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (private and stat.S_IMODE(opened.st_mode) != 0o600)
            or not 0 < opened.st_size <= maximum
            or (expected.st_dev, expected.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise BusinessRouteReceiptError("business_route_input_invalid")
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, maximum + 1 - len(content))):
            content.extend(chunk)
            if len(content) > maximum:
                raise BusinessRouteReceiptError("business_route_input_invalid")
        finished = os.fstat(descriptor)
        final = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if identity != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
        ) or identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise BusinessRouteReceiptError("business_route_input_changed")
        return bytes(content)
    except BusinessRouteReceiptError:
        raise
    except OSError as exc:
        raise BusinessRouteReceiptError("business_route_input_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_file(path: Path, *, private: bool, maximum: int = MAX_INPUT_BYTES) -> tuple[Mapping[str, Any], bytes]:
    content = _stable_file(path, private=private, maximum=maximum)
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BusinessRouteReceiptError("business_route_input_json_invalid") from exc
    if not isinstance(payload, dict):
        raise BusinessRouteReceiptError("business_route_input_json_invalid")
    return payload, content


def _validate_schedule(dataset: ab_eval.EvaluationDataset, raw: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    records = raw.get("results")
    schedule = ab_eval.interleaved_schedule(dataset)
    if not isinstance(records, list) or len(records) != len(schedule):
        raise BusinessRouteReceiptError("business_route_raw_schedule_invalid")
    case_ids = {case.case_id for case in dataset.cases}
    for sequence, (record, expected) in enumerate(zip(records, schedule, strict=True), start=1):
        repetition, arm, case = expected
        payload = ab_eval.build_run_payload(
            dataset,
            case,
            evaluation_id=str(raw.get("evaluation_id") or ""),
            repetition=repetition,
        )
        if (
            not isinstance(record, dict)
            or record.get("sequence") != sequence
            or record.get("repetition") != repetition
            or record.get("arm") != arm
            or record.get("case_id") != case.case_id
            or record.get("case_id") not in case_ids
            or record.get("case_hash") != case.case_hash
            or record.get("payload_sha256") != _sha256(_canonical_json(payload))
        ):
            raise BusinessRouteReceiptError("business_route_raw_schedule_invalid")
    return tuple(records)


def load_normal_evaluation(
    *,
    evaluation_id: str,
    dataset_path: Path,
    raw_path: Path,
    summary_path: Path,
    prerequisites_path: Path,
    provenance_path: Path,
) -> NormalEvaluation:
    if not ab_eval.SAFE_ID_RE.fullmatch(evaluation_id) or FORBIDDEN_LIVE_ID_RE.search(evaluation_id):
        raise BusinessRouteReceiptError("business_route_evaluation_id_invalid")
    dataset_payload, dataset_content = _json_file(dataset_path, private=True, maximum=ab_eval.MAX_DATASET_BYTES)
    raw, raw_content = _json_file(raw_path, private=True)
    summary, summary_content = _json_file(summary_path, private=True)
    prerequisites, prerequisites_content = _json_file(prerequisites_path, private=True, maximum=1024 * 1024)
    provenance, provenance_content = _json_file(provenance_path, private=True, maximum=1024 * 1024)
    try:
        dataset = ab_eval.parse_dataset(dataset_payload, sha256=_sha256(dataset_content))
    except ab_eval.EvaluationConfigurationError as exc:
        raise BusinessRouteReceiptError("business_route_dataset_invalid") from exc
    prerequisites_sha = _sha256(prerequisites_content)
    prerequisite_provenance = prerequisites.get("provenance")
    attestation = provenance.get("runtime_attestation")
    sources = provenance.get("sources")
    primary_provider = attestation.get("primary_provider") if isinstance(attestation, dict) else None
    primary_model = attestation.get("primary_model") if isinstance(attestation, dict) else None
    if (
        dataset.profile != "siq_analysis"
        or any(case.expectations.fallback_expected is not None for case in dataset.cases)
        or any(case.expectations.policy_denial_expected for case in dataset.cases)
        or prerequisites.get("schema_version") != ab_prerequisites.SCHEMA_VERSION
        or prerequisites.get("decision") != "GO"
        or prerequisites.get("evaluation_id") != evaluation_id
        or prerequisites.get("network_probe_performed") is not True
        or not isinstance(prerequisites.get("dataset"), dict)
        or prerequisites["dataset"].get("sha256") != dataset.sha256
        or not isinstance(prerequisite_provenance, dict)
        or prerequisite_provenance.get("schema_version") != ab_prepare.PROVENANCE_SCHEMA
        or prerequisite_provenance.get("sha256") != _sha256(provenance_content)
        or prerequisite_provenance.get("host_runtime_verified") is not True
        or prerequisite_provenance.get("host_candidate_source_match") is not True
        or provenance.get("schema_version") != ab_prepare.PROVENANCE_SCHEMA
        or provenance.get("evaluation_id") != evaluation_id
        or provenance.get("dataset_sha256") != dataset.sha256
        or not isinstance(primary_provider, str)
        or not isinstance(primary_model, str)
        or primary_model != dataset.model
        or not isinstance(sources, dict)
        or set(sources) != ab_prepare.PROVENANCE_SOURCE_NAMES
    ):
        raise BusinessRouteReceiptError("business_route_provenance_invalid")
    try:
        for binding in sources.values():
            ab_prepare.recapture_source_binding(binding, maximum=16 * 1024 * 1024)
    except ab_prepare.PreparationError as exc:
        raise BusinessRouteReceiptError("business_route_provenance_drift") from exc
    expected_configuration = {
        "profile": dataset.profile,
        "model": dataset.model,
        "temperature": dataset.temperature,
        "repetitions": dataset.repetitions,
        "run_timeout_seconds": dataset.run_timeout_seconds,
        "interleaving": "alternating_case_and_repetition",
    }
    expected_prerequisites_path = f"var/openshell/eval/{evaluation_id}/prerequisites.json"
    if (
        raw.get("schema_version") != ab_eval.RAW_SCHEMA_VERSION
        or raw.get("evaluation_id") != evaluation_id
        or raw.get("dataset_sha256") != dataset.sha256
        or raw.get("dataset_schema_version") != ab_eval.DATASET_SCHEMA_VERSION
        or raw.get("prerequisites_path") != expected_prerequisites_path
        or raw.get("prerequisites_sha256") != prerequisites_sha
        or raw.get("configuration") != expected_configuration
        or raw.get("cutover_performed") is not False
    ):
        raise BusinessRouteReceiptError("business_route_raw_invalid")
    records = _validate_schedule(dataset, raw)
    host_records = tuple(record for record in records if record.get("arm") == "host")
    openshell_records = tuple(record for record in records if record.get("arm") == "openshell")
    try:
        host_summary = ab_eval.summarize_arm(
            host_records,
            expected_primary_provider=primary_provider,
            expected_primary_model=primary_model,
        )
        openshell_summary = ab_eval.summarize_arm(
            openshell_records,
            expected_primary_provider=primary_provider,
            expected_primary_model=primary_model,
        )
        comparison, reasons = ab_eval.quality_comparison(
            host_summary,
            openshell_summary,
            case_count=len(dataset.cases),
            repetitions=dataset.repetitions,
            require_fallback=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessRouteReceiptError("business_route_raw_invalid") from exc
    expected_summary_fields = {
        "schema_version": ab_eval.SUMMARY_SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "prerequisites_path": expected_prerequisites_path,
        "prerequisites_sha256": prerequisites_sha,
        "dataset_sha256": dataset.sha256,
        "dataset_schema_version": ab_eval.DATASET_SCHEMA_VERSION,
        "profile": dataset.profile,
        "model": dataset.model,
        "temperature": dataset.temperature,
        "case_count": len(dataset.cases),
        "repetitions": dataset.repetitions,
        "execution_count": len(records),
        "interleaving": "alternating_case_and_repetition",
    }
    if (
        any(summary.get(field) != value for field, value in expected_summary_fields.items())
        or summary.get("arms") != {"host": host_summary, "openshell": openshell_summary}
        or summary.get("comparison") != comparison
        or not isinstance(summary.get("quality_gate"), dict)
        or summary["quality_gate"].get("passed") is not True
        or summary["quality_gate"].get("failure_reasons") != []
        or reasons
    ):
        raise BusinessRouteReceiptError("business_route_summary_raw_mismatch")
    return NormalEvaluation(
        evaluation_id=evaluation_id,
        dataset=dataset,
        dataset_sha256=dataset.sha256,
        raw=raw,
        raw_sha256=_sha256(raw_content),
        summary=summary,
        summary_sha256=_sha256(summary_content),
        prerequisites_sha256=prerequisites_sha,
        provenance=provenance,
        provenance_sha256=_sha256(provenance_content),
        openshell_records=openshell_records,
    )


def _workflow_projection(
    records: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
    required_tool: str | None,
) -> dict[str, Any]:
    selected = [record for record in records if record.get("case_id") == case_id]
    task_success_count = sum(record.get("scores", {}).get("task_success") is True for record in selected)
    completed_count = sum(record.get("status") == "completed" for record in selected)
    policy_denials = sum(record.get("policy_denied") is True for record in selected)
    if (
        len(selected) != ab_eval.MIN_EVALUATION_REPETITIONS
        or task_success_count != len(selected)
        or completed_count != len(selected)
        or policy_denials
    ):
        raise BusinessRouteReceiptError("business_route_workflow_failed")
    if required_tool is not None and any(
        required_tool not in record.get("successful_tools", [])
        or record.get("scores", {}).get("tools") != {"matched": 1, "expected": 1}
        for record in selected
    ):
        raise BusinessRouteReceiptError("business_route_workflow_tool_failed")
    return {
        "case_id": case_id,
        "execution_count": len(selected),
        "task_success_count": task_success_count,
        "terminal_completed_count": completed_count,
        "policy_denial_count": policy_denials,
    }


def _route_projection(inputs: NormalEvaluation) -> dict[str, Any]:
    attestation = inputs.provenance["runtime_attestation"]
    primary_provider = str(attestation["primary_provider"])
    primary_model = str(attestation["primary_model"])
    records = inputs.openshell_records
    if any(
        record.get("status") != "completed"
        or record.get("scores", {}).get("task_success") is not True
        or record.get("policy_denied") is not False
        or record.get("runtime")
        != {
            "fallback_activated": False,
            "requested_model": primary_model,
            "configured_provider": primary_provider,
            "configured_model": primary_model,
            "effective_provider": primary_provider,
            "effective_model": primary_model,
        }
        for record in records
    ):
        raise BusinessRouteReceiptError("business_route_primary_route_failed")
    source_config = Path(str(inputs.provenance["sources"]["host_profile_config"]["path"]))
    try:
        config = yaml.safe_load(_stable_file(source_config, private=False, maximum=1024 * 1024))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise BusinessRouteReceiptError("business_route_search_config_invalid") from exc
    if not isinstance(config, dict) or not isinstance(config.get("web"), dict) or config["web"].get("search_backend") != "tavily":
        raise BusinessRouteReceiptError("business_route_search_config_invalid")
    analysis = _workflow_projection(records, case_id="workflow_analysis_roundtrip", required_tool="terminal")
    tavily = _workflow_projection(records, case_id="workflow_tavily_search", required_tool="web_search")
    download = _workflow_projection(records, case_id="workflow_public_download_parse", required_tool="terminal")
    session = _workflow_projection(records, case_id="workflow_session_continuity", required_tool=None)
    return {
        "model_runtime": {
            "execution_count": len(records),
            "terminal_completed_count": len(records),
            "task_success_count": len(records),
            "policy_denial_count": 0,
            "fallback_activated_count": 0,
            "configured_provider": primary_provider,
            "configured_model": primary_model,
            "effective_provider": primary_provider,
            "effective_model": primary_model,
        },
        "analysis_crud": analysis,
        "tavily_search": {
            **tavily,
            "required_tool": "web_search",
            "search_backend": "tavily",
            "contract": "approved_search_success_nonempty_https",
        },
        "public_download_parse": {
            **download,
            "required_tool": "terminal",
            "contract": "siq-fetch_https_get_json_parse",
        },
        "session_continuity": session,
    }


def build_receipt(*, project_root: Path, run_id: str, inputs: NormalEvaluation) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    if root != REPO_ROOT or not FORMAL_RUN_RE.fullmatch(run_id):
        raise BusinessRouteReceiptError("business_route_run_invalid")
    first = formal_filesystem.capture_active_binding(project_root=root, run_id=run_id)
    gateway_before = gateway_runtime_identity.verify_runtime_identity(root)
    api_before = _api_runtime_receipt(root)
    routes = _route_projection(inputs)
    second = formal_filesystem.capture_active_binding(project_root=root, run_id=run_id)
    gateway_after = gateway_runtime_identity.verify_runtime_identity(root)
    api_after = _api_runtime_receipt(root)
    if first.binding != second.binding or gateway_before != gateway_after or api_before != api_after:
        raise BusinessRouteReceiptError("business_route_runtime_changed")
    openshell = inputs.provenance.get("arms", {}).get("openshell")
    binding = first.binding
    image_id = f"sha256:{binding.image_sha256}"
    if (
        not isinstance(openshell, dict)
        or image_id != openshell.get("image_id")
        or binding.policy_sha256 != openshell.get("policy_sha256")
        or binding.mount_plan_sha256 != openshell.get("mount_plan_sha256")
        or binding.mount_contract_sha256 != openshell.get("mount_contract_sha256")
        or binding.runtime_config_sha256 != openshell.get("runtime_config_sha256")
    ):
        raise BusinessRouteReceiptError("business_route_transaction_provenance_mismatch")
    receipt = {
        "schema_version": evidence_contract.SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "decision": "PASS",
        "profile": "siq_analysis",
        "evaluation_id": inputs.evaluation_id,
        "dataset_sha256": inputs.dataset_sha256,
        "normal_raw_results_sha256": inputs.raw_sha256,
        "normal_summary_sha256": inputs.summary_sha256,
        "prerequisites_sha256": inputs.prerequisites_sha256,
        "provenance_sha256": inputs.provenance_sha256,
        "transaction": {
            "transaction_receipt_sha256": binding.transaction_receipt_sha256,
            "transaction_generation": binding.transaction_generation,
            "run_id_sha256": binding.run_id_sha256,
            "sandbox_id_sha256": binding.sandbox_id_sha256,
            "container_id_sha256": binding.container_id_sha256,
            "host_receipt_sha256": binding.host_receipt_sha256,
            "gateway_receipt_sha256": formal_runtime_contract.canonical_sha256(gateway_before),
            "api_runtime_receipt_sha256": formal_runtime_contract.canonical_sha256(api_before),
            "image_id": image_id,
            "policy_sha256": binding.policy_sha256,
            "mount_plan_sha256": binding.mount_plan_sha256,
            "mount_contract_sha256": binding.mount_contract_sha256,
            "runtime_config_sha256": binding.runtime_config_sha256,
        },
        "routes": routes,
        "provenance": {
            "evidence_schema_sha256": evidence_contract.source_sha256(root, evidence_contract.SCHEMA_RELATIVE),
            "producer_sha256": evidence_contract.source_sha256(root, evidence_contract.PRODUCER_RELATIVE),
            "validator_sha256": evidence_contract.source_sha256(root, evidence_contract.VALIDATOR_RELATIVE),
            "evaluator_sha256": evidence_contract.source_sha256(root, evidence_contract.EVALUATOR_RELATIVE),
            "preparer_sha256": evidence_contract.source_sha256(root, evidence_contract.PREPARER_RELATIVE),
            "lifecycle_sha256": evidence_contract.source_sha256(root, evidence_contract.LIFECYCLE_RELATIVE),
            "runtime_contract_sha256": evidence_contract.source_sha256(
                root, evidence_contract.RUNTIME_CONTRACT_RELATIVE
            ),
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "exporter_ready": True,
        },
    }
    evidence_contract.validate_bindings(
        receipt,
        root=root,
        summary=inputs.summary,
        summary_sha256=inputs.summary_sha256,
        raw_sha256=inputs.raw_sha256,
        prerequisites_sha256=inputs.prerequisites_sha256,
        provenance_report=inputs.provenance,
        provenance_sha256=inputs.provenance_sha256,
    )
    return receipt


def _output_path(root: Path, value: Path, *, suffix: str) -> Path:
    path = value if value.is_absolute() else root / value
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BusinessRouteReceiptError("business_route_output_invalid") from exc
    if relative.parent != DEFAULT_JSON.parent or not path.name.endswith(suffix):
        raise BusinessRouteReceiptError("business_route_output_invalid")
    return path


def _exclusive_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def publish_receipt(root: Path, receipt: Mapping[str, Any], json_value: Path, markdown_value: Path) -> tuple[Path, Path]:
    json_path = _output_path(root, json_value, suffix=".sanitized.json")
    markdown_path = _output_path(root, markdown_value, suffix=".sanitized.md")
    if json_path.stem.removesuffix(".sanitized") != markdown_path.stem.removesuffix(".sanitized"):
        raise BusinessRouteReceiptError("business_route_output_pair_invalid")
    json_content = json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    routes = receipt["routes"]
    markdown = (
        "# Formal Business Route Evidence\n\n"
        f"- Decision: {receipt['decision']}\n"
        f"- Evaluation ID: `{receipt['evaluation_id']}`\n"
        f"- OpenShell executions on primary route: {routes['model_runtime']['execution_count']}\n"
        f"- Analysis CRUD executions: {routes['analysis_crud']['execution_count']}\n"
        f"- Tavily search executions: {routes['tavily_search']['execution_count']}\n"
        f"- Public download/parse executions: {routes['public_download_parse']['execution_count']}\n"
        f"- Session continuity executions: {routes['session_continuity']['execution_count']}\n"
        "- Raw prompts, outputs, headers, credentials, and local paths: excluded\n"
    ).encode("ascii")
    written: list[Path] = []
    try:
        _exclusive_write(json_path, json_content)
        written.append(json_path)
        _exclusive_write(markdown_path, markdown)
        written.append(markdown_path)
        if check_sanitized_artifacts.scan_paths(written):
            raise BusinessRouteReceiptError("business_route_output_not_sanitized")
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--normal-raw-results", type=Path, required=True)
    parser.add_argument("--normal-summary", type=Path, required=True)
    parser.add_argument("--prerequisites", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--confirm-live-capture", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.confirm_live_capture:
            raise BusinessRouteReceiptError("business_route_live_capture_not_confirmed")
        root = args.project_root.resolve(strict=True)
        inputs = load_normal_evaluation(
            evaluation_id=args.evaluation_id,
            dataset_path=args.dataset,
            raw_path=args.normal_raw_results,
            summary_path=args.normal_summary,
            prerequisites_path=args.prerequisites,
            provenance_path=args.provenance,
        )
        receipt = build_receipt(project_root=root, run_id=args.run_id, inputs=inputs)
        json_path, markdown_path = publish_receipt(root, receipt, args.output_json, args.output_markdown)
        print(
            json.dumps(
                {
                    "ok": True,
                    "decision": "PASS",
                    "json": json_path.relative_to(root).as_posix(),
                    "markdown": markdown_path.relative_to(root).as_posix(),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "business_route_failed")
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
