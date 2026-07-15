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
import math
import shlex
import sqlite3
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.agent_runtime_financial_claim_verifier import TRACE_SCHEMAS

_SCRIPT_NAMES = frozenset({"financial_calculator.py", "financial_reconciliation_validator.py"})
_PYTHON_NAMES = frozenset({"python", "python3", "python3.11", "python3.12"})
_SHELL_META = (";", "&&", "||", "|", ">", "<", "$(", "`", "\x00")
_MAX_SESSION_BYTES = 20 * 1024 * 1024
_MAX_CURRENT_TURN_MESSAGES = 2048
_SCRIPT_OPERATIONS = {
    "financial_calculator.py": frozenset(
        {"normalize_amount", "yoy", "yoy_growth", "ratio", "cagr", "per_capita"}
    ),
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


def _state_db_path(profile_dir: Path) -> Path | None:
    try:
        resolved_profile_dir = Path(profile_dir).expanduser().resolve()
        candidate = (resolved_profile_dir / "state.db").resolve()
    except (OSError, RuntimeError):
        return None
    if candidate.parent != resolved_profile_dir:
        return None
    return candidate


def _sqlite_table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    if table not in {"sessions", "messages"}:
        return frozenset()
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return frozenset()
    return frozenset(str(row[1]) for row in rows if len(row) > 1)


def _timestamp_ns(value: Any) -> int:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(timestamp) or timestamp < 0:
        return 0
    return int(timestamp * 1_000_000_000)


def _sqlite_session_messages(
    profile_dir: Path,
    hermes_session_id: str,
) -> tuple[int, tuple[Mapping[str, Any], ...]] | None:
    """Read one exact session's current turn from a Hermes SQLite state store."""

    path = _state_db_path(profile_dir)
    if path is None or not path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=0.1,
        )
    except (OSError, sqlite3.Error):
        return None
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        if not {"id"}.issubset(_sqlite_table_columns(connection, "sessions")):
            return None
        required_message_columns = {
            "id",
            "session_id",
            "role",
            "content",
            "tool_call_id",
            "tool_calls",
            "timestamp",
        }
        if not required_message_columns.issubset(_sqlite_table_columns(connection, "messages")):
            return None
        if connection.execute(
            "SELECT 1 FROM sessions WHERE id = ? LIMIT 1",
            (hermes_session_id,),
        ).fetchone() is None:
            return None
        last_user = connection.execute(
            """
            SELECT id, timestamp
            FROM messages
            WHERE session_id = ? AND role = 'user'
            ORDER BY id DESC
            LIMIT 1
            """,
            (hermes_session_id,),
        ).fetchone()
        if last_user is None:
            return None
        last_user_id = int(last_user[0])
        turn_size = connection.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(COALESCE(length(content), 0) + COALESCE(length(tool_calls), 0)), 0)
            FROM messages
            WHERE session_id = ? AND id >= ?
            """,
            (hermes_session_id, last_user_id),
        ).fetchone()
        if (
            turn_size is None
            or int(turn_size[0] or 0) > _MAX_CURRENT_TURN_MESSAGES
            or int(turn_size[1] or 0) > _MAX_SESSION_BYTES
        ):
            return None
        rows = connection.execute(
            """
            SELECT role, content, tool_call_id, tool_calls, timestamp
            FROM messages
            WHERE session_id = ? AND id >= ?
            ORDER BY id ASC
            """,
            (hermes_session_id, last_user_id),
        ).fetchall()
    except (TypeError, ValueError, sqlite3.Error):
        return None
    finally:
        connection.close()

    messages: list[Mapping[str, Any]] = []
    activity_ns = _timestamp_ns(last_user[1])
    for role, content, tool_call_id, tool_calls, timestamp in rows:
        item: dict[str, Any] = {"role": str(role or "")}
        if content is not None:
            item["content"] = content
        if tool_call_id is not None:
            item["tool_call_id"] = str(tool_call_id)
        if tool_calls is not None:
            if isinstance(tool_calls, str):
                try:
                    tool_calls = json.loads(tool_calls)
                except json.JSONDecodeError:
                    tool_calls = ()
            item["tool_calls"] = tool_calls if isinstance(tool_calls, Sequence) else ()
        messages.append(item)
        activity_ns = max(activity_ns, _timestamp_ns(timestamp))
    return activity_ns, tuple(messages)


def _newest_session_messages(
    profile_dirs: Sequence[Path],
    hermes_session_id: str,
) -> tuple[Mapping[str, Any], ...] | None:
    """Select the newest exact-session transcript across JSON and SQLite stores."""

    candidates: list[tuple[int, int, int, tuple[Mapping[str, Any], ...]]] = []
    seen: set[Path] = set()
    for index, profile_dir in enumerate(profile_dirs):
        try:
            resolved_profile_dir = Path(profile_dir).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved_profile_dir in seen:
            continue
        seen.add(resolved_profile_dir)

        path = _session_path(resolved_profile_dir, hermes_session_id)
        if path is not None:
            try:
                stat = path.stat()
            except OSError:
                pass
            else:
                if path.is_file() and stat.st_size <= _MAX_SESSION_BYTES:
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                    else:
                        messages = payload.get("messages") if isinstance(payload, Mapping) else None
                        if isinstance(messages, Sequence):
                            normalized_messages = tuple(item for item in messages if isinstance(item, Mapping))
                            candidates.append((stat.st_mtime_ns, 0, -index, normalized_messages))

        sqlite_messages = _sqlite_session_messages(resolved_profile_dir, hermes_session_id)
        if sqlite_messages is not None:
            activity_ns, messages = sqlite_messages
            # Prefer the canonical SQLite store for an indistinguishable timestamp tie.
            candidates.append((activity_ns, 1, -index, messages))

    if not candidates:
        return None
    sqlite_candidates = [candidate for candidate in candidates if candidate[1] == 1]
    if sqlite_candidates:
        # SQLite is Hermes' canonical session store.  Legacy JSON mtimes can
        # change during copy/backup and must not replace a current DB turn.
        candidates = sqlite_candidates
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


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
    profile_dir: Path | None = None,
    profile_dirs: Sequence[Path] = (),
    hermes_session_id: str,
    allowed_script_paths: Mapping[str, Path | Sequence[Path]],
) -> tuple[Mapping[str, Any], ...]:
    """Extract receipts from the newest exact session across profile roots."""

    candidate_dirs = ((profile_dir,) if profile_dir is not None else ()) + tuple(profile_dirs)
    messages = _newest_session_messages(candidate_dirs, hermes_session_id)
    if messages is None:
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
