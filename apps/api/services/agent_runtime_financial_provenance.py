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
) -> dict[str, Any]:
    snapshot = financial_evidence_snapshot(model_input, context, attachments)
    created_at_text = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or _utc_now_iso())
    input_hash = stable_hash(
        {
            "prompt_version": prompt_version,
            "model_input": model_input,
            "context": context,
            "attachments": attachments,
            "input_evidence_hash": snapshot["input_evidence_hash"],
        }
    )
    input_evidence_ids = snapshot["input_evidence_ids"]
    record: dict[str, Any] = {
        "provider": provider or "unknown",
        "model": model or "unknown",
        "prompt_version": prompt_version,
        "input_evidence_ids": input_evidence_ids,
        "input_hash": input_hash,
        "output_hash": stable_hash(output or ""),
        "created_at": created_at_text,
        "input_evidence_hash": snapshot["input_evidence_hash"],
        "fact_trust_level": financial_llm_fact_trust_level(input_evidence_ids),
        "canonical_promotable": can_promote_financial_llm_output_to_canonical(None),
    }
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
    payload = dict(record)
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
    provider, model = model_identity_for_profile(profile, profile_dirs=profile_dirs)
    record = build_financial_llm_provenance(
        provider=provider,
        model=model,
        model_input=model_input,
        output=raw_output,
        stored_output=stored_output,
        context=context,
        attachments=attachments,
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
