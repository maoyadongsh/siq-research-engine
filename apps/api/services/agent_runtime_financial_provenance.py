"""Financial LLM provenance helpers for the agent runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from services.path_config import BACKEND_DATA_ROOT

FINANCIAL_LLM_PROMPT_VERSION = "agent-runtime-financial-v1"
RECENT_FINANCIAL_LLM_PROVENANCE_LIMIT = 200
RECENT_FINANCIAL_LLM_PROVENANCE: list[dict[str, Any]] = []

_EVIDENCE_ID_KEYS = {"evidence_id", "input_evidence_id"}
_EVIDENCE_IDS_KEYS = {"evidence_ids", "input_evidence_ids"}
_HASH_KEY_TERMS = (
    "evidence_hash",
    "source_hash",
    "content_hash",
    "document_hash",
    "file_hash",
    "sha256",
    "sha1",
    "md5",
)
_GENERIC_HASH_KEYS = {"hash"}
_EVIDENCE_ID_RE = re.compile(
    r"(?:\"evidence_id\"|\bevidence_id\b)\s*[:=]\s*[\"'`]?([A-Za-z0-9_.:/#-]{3,})"
)
_EVIDENCE_IDS_RE = re.compile(
    r"(?:\"evidence_ids\"|\bevidence_ids\b)\s*[:=]\s*\[([^\]]+)\]"
)
_TASK_ID_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
_CANARY_RUN_ID_RE = re.compile(r"canary-[0-9a-f]{12}\Z")
_RUNTIME_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}\Z")
_CREDENTIAL_LABEL_RE = re.compile(
    r"(?i)(?:bearer|sk[-_]|ghp_|github_pat_|xox[a-z]-|akia|eyJ[A-Za-z0-9_-]+\."
    r"|.*(?:api[_-]?key|authorization|cookie|password|secret|token)[:=])"
)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_FINANCIAL_PROVENANCE_FIELDS = frozenset(
    {
        "provider",
        "model",
        "prompt_version",
        "input_evidence_ids",
        "input_hash",
        "output_hash",
        "created_at",
        "input_evidence_hash",
        "fact_trust_level",
        "canonical_promotable",
        "stored_output_hash",
        "output_was_guarded",
        "runtime_provenance",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _stable_value(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, set):
        return [_stable_value(item) for item in sorted(value, key=lambda item: repr(item))]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_runtime_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not _RUNTIME_LABEL_RE.fullmatch(normalized)
        or "://" in normalized
        or _CREDENTIAL_LABEL_RE.match(normalized)
    ):
        return None
    return normalized


def _runtime_provenance_snapshot(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return {"runtime_target": "host"}
    if not isinstance(value, Mapping):
        return None
    target = str(value.get("runtime_target") or "").strip().lower()
    if target not in {"host", "openshell"}:
        return None
    snapshot = {"runtime_target": target}
    canary_run_id = str(value.get("canary_run_id") or "").strip()
    if target == "openshell" and _CANARY_RUN_ID_RE.fullmatch(canary_run_id):
        snapshot["canary_run_id"] = canary_run_id
    return snapshot


def _runtime_metadata_label(value: Any, section: str, field: str) -> str | None:
    if isinstance(value, Mapping):
        section_value = value.get(section)
        if not isinstance(section_value, Mapping):
            return None
        return _safe_runtime_label(section_value.get(field))
    return _safe_runtime_label(getattr(value, f"{section}_{field}", None))


def _model_identity_from_runtime(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    provider = _runtime_metadata_label(value, "effective", "provider")
    model = _runtime_metadata_label(value, "effective", "model")
    if provider is None:
        provider = _runtime_metadata_label(value, "configured", "provider")
    if model is None:
        model = _runtime_metadata_label(value, "configured", "model")
    return provider, model


def _safe_created_at(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    normalized = value.strip()
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalized


def _sanitize_financial_provenance_record(record: Mapping[str, Any]) -> dict[str, Any]:
    source = {key: value for key, value in record.items() if key in _FINANCIAL_PROVENANCE_FIELDS}
    raw_evidence_ids = source.get("input_evidence_ids")
    evidence_ids = (
        raw_evidence_ids
        if isinstance(raw_evidence_ids, Sequence)
        and not isinstance(raw_evidence_ids, (str, bytes, bytearray))
        else ()
    )
    payload: dict[str, Any] = {
        "provider": _safe_runtime_label(source.get("provider")) or "unknown",
        "model": _safe_runtime_label(source.get("model")) or "unknown",
        "prompt_version": _safe_runtime_label(source.get("prompt_version")) or FINANCIAL_LLM_PROMPT_VERSION,
        "input_evidence_ids": [
            evidence_id
            for item in evidence_ids
            if (evidence_id := _safe_runtime_label(item)) is not None
        ],
        "input_hash": source.get("input_hash") if _HASH_RE.fullmatch(str(source.get("input_hash") or "")) else "",
        "output_hash": source.get("output_hash") if _HASH_RE.fullmatch(str(source.get("output_hash") or "")) else "",
        "created_at": _safe_created_at(source.get("created_at")) or _utc_now_iso(),
        "input_evidence_hash": (
            source.get("input_evidence_hash")
            if _HASH_RE.fullmatch(str(source.get("input_evidence_hash") or ""))
            else ""
        ),
        "fact_trust_level": (
            source.get("fact_trust_level")
            if source.get("fact_trust_level") in {"evidence_bound_explanation", "candidate_explanation"}
            else "candidate_explanation"
        ),
        "canonical_promotable": source.get("canonical_promotable") is True,
    }
    runtime_snapshot = _runtime_provenance_snapshot(source.get("runtime_provenance"))
    if runtime_snapshot is not None:
        payload["runtime_provenance"] = runtime_snapshot
    if _HASH_RE.fullmatch(str(source.get("stored_output_hash") or "")):
        payload["stored_output_hash"] = source["stored_output_hash"]
    if isinstance(source.get("output_was_guarded"), bool):
        payload["output_was_guarded"] = source["output_was_guarded"]
    return payload


def _dedupe_append(items: list[str], value: Any) -> None:
    text = str(value or "").strip().strip(",.;，；。")
    if not text or text in items:
        return
    items.append(text)


def _string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _source_field(line: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}=([^,，\]\)\n]+)", line or "")
    return match.group(1).strip().strip("。；;") if match else ""


def _extract_text_evidence_ids(text: str, ids: list[str]) -> None:
    for match in _EVIDENCE_ID_RE.finditer(text or ""):
        _dedupe_append(ids, match.group(1))
    for match in _EVIDENCE_IDS_RE.finditer(text or ""):
        for token in re.findall(r"[A-Za-z0-9_.:/#-]{3,}", match.group(1)):
            _dedupe_append(ids, token)
    for line in (text or "").splitlines():
        if "source_type=" not in line or "task_id=" not in line:
            continue
        task_match = _TASK_ID_RE.search(line)
        if not task_match:
            continue
        source_type = _source_field(line, "source_type") or "source"
        task_id = task_match.group(1)
        pdf_page = _source_field(line, "pdf_page") or _source_field(line, "pdf_page_number") or "na"
        table_index = _source_field(line, "table_index") or "na"
        _dedupe_append(ids, f"{source_type}:{task_id}:p{pdf_page}:t{table_index}")


def _is_hash_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _GENERIC_HASH_KEYS or any(term in normalized for term in _HASH_KEY_TERMS)


def _append_hash_material(items: list[dict[str, Any]], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    items.append({key: _stable_value(value)})


def _collect_evidence_material(value: Any, ids: list[str], hash_material: list[dict[str, Any]]) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.lower()
            if normalized_key in _EVIDENCE_ID_KEYS:
                for text in _string_sequence(item):
                    _dedupe_append(ids, text)
            elif normalized_key in _EVIDENCE_IDS_KEYS:
                for text in _string_sequence(item):
                    _dedupe_append(ids, text)
            elif _is_hash_key(normalized_key):
                _append_hash_material(hash_material, key, item)
            _collect_evidence_material(item, ids, hash_material)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _collect_evidence_material(item, ids, hash_material)
        return
    if isinstance(value, str):
        _extract_text_evidence_ids(value, ids)


def financial_evidence_snapshot(*values: Any) -> dict[str, Any]:
    input_evidence_ids: list[str] = []
    hash_material: list[dict[str, Any]] = []
    for value in values:
        _collect_evidence_material(value, input_evidence_ids, hash_material)
    payload = {
        "input_evidence_ids": input_evidence_ids,
        "hash_material": hash_material,
    }
    has_evidence_material = bool(input_evidence_ids or hash_material)
    return {
        "input_evidence_ids": input_evidence_ids,
        "input_evidence_hash": stable_hash(payload) if has_evidence_material else "",
        "has_evidence_material": has_evidence_material,
    }


def financial_llm_cache_key(
    base_key: str,
    *,
    message: str,
    context: Any | None = None,
    attachments: Any | None = None,
) -> str:
    snapshot = financial_evidence_snapshot(message, context, attachments)
    evidence_hash = snapshot["input_evidence_hash"]
    if not evidence_hash:
        return base_key
    return f"{base_key}:e{evidence_hash[:16]}"


def financial_llm_fact_trust_level(input_evidence_ids: Sequence[str]) -> str:
    if input_evidence_ids:
        return "evidence_bound_explanation"
    return "candidate_explanation"


def can_promote_financial_llm_output_to_canonical(_provenance: Mapping[str, Any] | None = None) -> bool:
    return False


def model_identity_for_profile(
    profile: str,
    *,
    profile_dirs: Mapping[str, Path] | None = None,
) -> tuple[str, str]:
    profile_dir = profile_dirs.get(profile) if profile_dirs else None
    config_path = profile_dir / "config.yaml" if profile_dir else None
    if config_path and config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            config = {}
        model_config = config.get("model") if isinstance(config, Mapping) else {}
        if isinstance(model_config, Mapping):
            provider = str(model_config.get("provider") or "hermes")
            model = str(model_config.get("default") or profile)
            return provider, model
        if isinstance(model_config, str) and model_config.strip():
            return "hermes", model_config.strip()
    return "hermes", profile


def build_financial_llm_provenance(
    *,
    provider: str,
    model: str,
    model_input: Any,
    output: str,
    context: Any | None = None,
    attachments: Any | None = None,
    prompt_version: str = FINANCIAL_LLM_PROMPT_VERSION,
    created_at: datetime | str | None = None,
    stored_output: str | None = None,
    runtime_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = financial_evidence_snapshot(model_input, context, attachments)
    runtime_snapshot = _runtime_provenance_snapshot(runtime_provenance)
    safe_provider = _safe_runtime_label(provider) or "unknown"
    safe_model = _safe_runtime_label(model) or "unknown"
    created_at_text = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or _utc_now_iso())
    input_hash = stable_hash(
        {
            "prompt_version": prompt_version,
            "model_input": model_input,
            "context": context,
            "attachments": attachments,
            "input_evidence_hash": snapshot["input_evidence_hash"],
            "runtime_provenance": runtime_snapshot,
            "runtime_model_identity": {
                "provider": safe_provider,
                "model": safe_model,
            },
        }
    )
    input_evidence_ids = snapshot["input_evidence_ids"]
    record: dict[str, Any] = {
        "provider": safe_provider,
        "model": safe_model,
        "prompt_version": prompt_version,
        "input_evidence_ids": input_evidence_ids,
        "input_hash": input_hash,
        "output_hash": stable_hash(output or ""),
        "created_at": created_at_text,
        "input_evidence_hash": snapshot["input_evidence_hash"],
        "fact_trust_level": financial_llm_fact_trust_level(input_evidence_ids),
        "canonical_promotable": can_promote_financial_llm_output_to_canonical(None),
    }
    if runtime_snapshot is not None:
        record["runtime_provenance"] = runtime_snapshot
    if stored_output is not None:
        record["stored_output_hash"] = stable_hash(stored_output or "")
        record["output_was_guarded"] = record["stored_output_hash"] != record["output_hash"]
    return record


def _default_log_path() -> Path:
    raw = os.getenv("SIQ_FINANCIAL_LLM_PROVENANCE_LOG_PATH")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return BACKEND_DATA_ROOT / "audit" / "financial_llm_provenance.jsonl"


def record_financial_llm_provenance(
    record: Mapping[str, Any],
    *,
    log_path: str | Path | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    payload = _sanitize_financial_provenance_record(record)
    RECENT_FINANCIAL_LLM_PROVENANCE.append(payload)
    del RECENT_FINANCIAL_LLM_PROVENANCE[:-RECENT_FINANCIAL_LLM_PROVENANCE_LIMIT]

    path = Path(log_path).expanduser() if log_path is not None else _default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        payload["audit_log_error"] = exc.__class__.__name__
        if raise_on_error:
            raise
    return payload


def record_financial_llm_provenance_if_needed(
    *,
    message: str,
    context: Any | None,
    profile: str,
    model_input: Any,
    raw_output: str,
    stored_output: str,
    attachments: Any | None = None,
    profile_dirs: Mapping[str, Path] | None = None,
    runtime_metadata: Any | None = None,
    runtime_provenance: Mapping[str, Any] | None = None,
    is_runtime_status_reply: Callable[[str], bool],
    needs_financial_evidence_contract: Callable[[str, Any | None], bool],
    record_provenance: Callable[[Mapping[str, Any]], dict[str, Any] | None] = record_financial_llm_provenance,
) -> dict[str, Any] | None:
    """Record provenance for financial LLM output only when the run is evidence-relevant."""
    if not raw_output or is_runtime_status_reply(raw_output):
        return None
    snapshot = financial_evidence_snapshot(model_input, context, attachments)
    if not (snapshot["has_evidence_material"] or needs_financial_evidence_contract(message, context)):
        return None
    runtime_snapshot = _runtime_provenance_snapshot(runtime_provenance)
    if runtime_provenance is not None and runtime_snapshot is None:
        return None
    provider, model = _model_identity_from_runtime(runtime_metadata)
    if provider is None or model is None:
        if runtime_snapshot is not None and runtime_snapshot["runtime_target"] == "openshell":
            provider = provider or "unknown"
            model = model or "unknown"
        else:
            configured_provider, configured_model = model_identity_for_profile(
                profile,
                profile_dirs=profile_dirs,
            )
            provider = provider or configured_provider
            model = model or configured_model
    record = build_financial_llm_provenance(
        provider=provider,
        model=model,
        model_input=model_input,
        output=raw_output,
        stored_output=stored_output,
        context=context,
        attachments=attachments,
        runtime_provenance=runtime_snapshot,
    )
    return record_provenance(record)


__all__ = [
    "FINANCIAL_LLM_PROMPT_VERSION",
    "RECENT_FINANCIAL_LLM_PROVENANCE",
    "build_financial_llm_provenance",
    "can_promote_financial_llm_output_to_canonical",
    "financial_evidence_snapshot",
    "financial_llm_cache_key",
    "financial_llm_fact_trust_level",
    "model_identity_for_profile",
    "record_financial_llm_provenance",
    "record_financial_llm_provenance_if_needed",
    "stable_hash",
]
