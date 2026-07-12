"""Trusted financial-tool receipt extraction for the Hermes runtime.

The model's Markdown answer is a display surface.  This module reads only the
current Hermes session transcript and accepts a receipt when a single,
allowlisted Python financial script invocation produced exactly one complete
JSON object.  It deliberately rejects shell pipelines, chained commands and
truncated or mixed stdout so a hand-written marker cannot satisfy the guard.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.agent_runtime_financial_claim_verifier import TRACE_SCHEMAS

_SCRIPT_NAMES = frozenset({"financial_calculator.py", "financial_reconciliation_validator.py"})
_PYTHON_NAMES = frozenset({"python", "python3", "python3.11", "python3.12"})
_SHELL_META = (";", "&&", "||", "|", ">", "<", "$(", "`", "\x00")
_SCRIPT_OPERATIONS = {
    "financial_calculator.py": frozenset({"yoy", "yoy_growth", "ratio", "cagr", "per_capita"}),
    "financial_reconciliation_validator.py": frozenset(
        {"goodwill_reconciliation", "gross_allowance_net_reconciliation"}
    ),
}
_CURRENT_TRUSTED_RUNS: ContextVar[tuple[Mapping[str, Any], ...]] = ContextVar(
    "siq_current_trusted_financial_runs",
    default=(),
)


def set_current_trusted_runs(runs: Sequence[Mapping[str, Any]]):
    return _CURRENT_TRUSTED_RUNS.set(tuple(runs))


def reset_current_trusted_runs(token: Any) -> None:
    _CURRENT_TRUSTED_RUNS.reset(token)


def current_trusted_runs() -> tuple[Mapping[str, Any], ...]:
    return _CURRENT_TRUSTED_RUNS.get()


def _json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor:].strip() == "":
            break
        try:
            payload, consumed = decoder.raw_decode(text[cursor:].lstrip())
        except json.JSONDecodeError:
            return []
        cursor += len(text[cursor:]) - len(text[cursor:].lstrip()) + consumed
        if not isinstance(payload, dict):
            return []
        objects.append(payload)
    return objects


def _session_path(profile_dir: Path, hermes_session_id: str) -> Path | None:
    normalized = str(hermes_session_id or "").strip()
    if not normalized or "/" in normalized or "\\" in normalized or ".." in normalized:
        return None
    sessions_dir = (profile_dir / "sessions").resolve()
    candidate = (sessions_dir / f"session_{normalized}.json").resolve()
    if candidate.parent != sessions_dir:
        return None
    return candidate


def _allowed_script_path(token: str, allowed_script_paths: Mapping[str, Path | Sequence[Path]]) -> str | None:
    try:
        candidate = Path(token).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for name, expected_paths in allowed_script_paths.items():
        candidates = expected_paths if isinstance(expected_paths, Sequence) and not isinstance(expected_paths, (str, bytes)) else (expected_paths,)
        for expected in candidates:
            try:
                if candidate == Path(expected).expanduser().resolve():
                    return name
            except (OSError, RuntimeError):
                continue
    return None


def _command_script(command: str, allowed_script_paths: Mapping[str, Path | Sequence[Path]]) -> str | None:
    if not command or any(marker in command for marker in _SHELL_META) or "\n" in command:
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) < 3:
        return None
    if Path(tokens[0]).name not in _PYTHON_NAMES:
        return None
    script_index = next((index for index, token in enumerate(tokens[1:], start=1) if Path(token).name in _SCRIPT_NAMES), None)
    if script_index is None or script_index != 1:
        return None
    script_name = _allowed_script_path(tokens[script_index], allowed_script_paths)
    if script_name is None:
        return None
    if any(token in {"2>&1", "1>&2", "--format=markdown"} for token in tokens):
        return None
    format_seen = False
    for token in tokens[2:]:
        if token == "--format":
            format_seen = True
            continue
        if format_seen:
            if token != "json":
                return None
            format_seen = False
    if format_seen or "--format" not in tokens and "--format=json" not in tokens:
        return None
    if "--format=json" in tokens and any(token == "--format" for token in tokens):
        return None
    return script_name


def _tool_payload(content: Any) -> tuple[str, int | None, Any] | None:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None
    if not isinstance(content, Mapping):
        return None
    output = content.get("output")
    exit_code = content.get("exit_code")
    return str(output or ""), int(exit_code) if isinstance(exit_code, int) else None, content.get("error")


def extract_runtime_financial_receipts(
    *,
    profile_dir: Path,
    hermes_session_id: str,
    allowed_script_paths: Mapping[str, Path | Sequence[Path]],
) -> tuple[Mapping[str, Any], ...]:
    """Extract current-turn financial tool payloads from one exact session file."""

    path = _session_path(profile_dir, hermes_session_id)
    if path is None or not path.is_file():
        return ()
    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            return ()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if not isinstance(messages, Sequence):
        return ()
    last_user = max((index for index, item in enumerate(messages) if isinstance(item, Mapping) and item.get("role") == "user"), default=-1)
    pending: dict[str, str] = {}
    receipts: list[Mapping[str, Any]] = []
    for item in messages[last_user + 1 :]:
        if not isinstance(item, Mapping):
            continue
        if item.get("role") == "assistant":
            for call in item.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if not isinstance(function, Mapping) or function.get("name") != "terminal":
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(arguments, Mapping):
                    continue
                script_name = _command_script(str(arguments.get("command") or ""), allowed_script_paths)
                call_id = str(call.get("id") or call.get("call_id") or "").strip()
                if script_name and call_id:
                    pending[call_id] = script_name
            continue
        if item.get("role") != "tool":
            continue
        call_id = str(item.get("tool_call_id") or "").strip()
        script_name = pending.pop(call_id, None)
        if not script_name:
            continue
        output_payload = _tool_payload(item.get("content"))
        if output_payload is None:
            continue
        output, exit_code, error = output_payload
        if exit_code != 0 or error not in (None, ""):
            continue
        objects = _json_objects(output.strip())
        if len(objects) != 1:
            continue
        result = objects[0]
        if str(result.get("status") or "").lower() not in {"ok", "pass", "passed"}:
            continue
        operation = str(result.get("operation") or "").strip().lower()
        if operation not in _SCRIPT_OPERATIONS[script_name]:
            continue
        if str(result.get("schema_version") or "") in TRACE_SCHEMAS and str(result.get("tool") or "") != script_name:
            continue
        receipt = dict(result)
        receipt["receipt_source"] = "hermes_session_tool"
        receipt["receipt_tool"] = script_name
        receipt["receipt_tool_call_id"] = call_id
        receipt["receipt_session_id"] = hermes_session_id
        receipt["receipt_payload_hash"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
        receipts.append(receipt)
    return tuple(receipts)
