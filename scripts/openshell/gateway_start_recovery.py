#!/usr/bin/env python3
"""Journal, attest, adopt, or reap an interrupted SIQ gateway start."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gateway_runtime_identity import (  # noqa: E402
    GATEWAY_NAME,
    GATEWAY_PORT,
    HEALTH_PORT,
    RECORD_KEYS,
    STARTING_SCHEMA,
    GatewayRuntimeError,
    _activation_sha,
    _atomic_write,
    _listening_sockets,
    _paths,
    _pidfd_open,
    _pidfd_send_signal,
    _private_regular,
    _read_environ,
    _read_pid_file,
    _sha256_file,
    _verify_health,
    _verify_listener_ownership,
    collect_process_identity,
    collect_runtime_identity,
    load_process_identity,
    terminate_attested_gateway,
    verify_runtime_identity,
    verify_starting_identity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INTENT_SCHEMA = "siq.openshell.gateway_start_intent.v1"
INTENT_KEYS = {
    "schema",
    "executable",
    "binary_sha256",
    "config_path",
    "config_sha256",
    "activation_sha256",
    "db_path",
    "created_at",
}
INTENT_PROCESS_KEYS = {
    "executable",
    "binary_sha256",
    "config_path",
    "config_sha256",
    "activation_sha256",
    "db_path",
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _safe_unlink(path: Path, *, label: str) -> None:
    if not _exists(path):
        return
    _private_regular(path, label=label)
    path.unlink()
    _fsync_directory(path.parent)


def _atomic_write_pid(path: Path, pid: int) -> None:
    if isinstance(pid, bool) or pid <= 0:
        raise GatewayRuntimeError("gateway PID is invalid")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(f"{pid}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def build_start_intent(project_root: Path) -> dict[str, Any]:
    paths = _paths(project_root)
    binary = paths["binary"].resolve(strict=True)
    config = paths["config"].resolve(strict=True)
    _private_regular(binary, label="gateway binary")
    _private_regular(config, label="gateway configuration")
    return {
        "schema": INTENT_SCHEMA,
        "executable": str(binary),
        "binary_sha256": _sha256_file(binary),
        "config_path": str(config),
        "config_sha256": _sha256_file(config),
        "activation_sha256": _activation_sha(paths["activation"]),
        "db_path": str(paths["database"]),
        "created_at": _utc_now(),
    }


def load_start_intent(path: Path, *, project_root: Path) -> dict[str, Any]:
    _private_regular(path, label="gateway start intent")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayRuntimeError("gateway start intent is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != INTENT_KEYS:
        raise GatewayRuntimeError("gateway start intent has an unexpected schema")
    if payload.get("schema") != INTENT_SCHEMA:
        raise GatewayRuntimeError("gateway start intent schema version is invalid")
    for key in INTENT_KEYS - {"schema"}:
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise GatewayRuntimeError(f"gateway start intent field is invalid: {key}")
    current = build_start_intent(project_root)
    mismatched = [key for key in INTENT_PROCESS_KEYS if payload.get(key) != current.get(key)]
    if mismatched:
        raise GatewayRuntimeError("gateway start intent drifted: " + ", ".join(sorted(mismatched)))
    return payload


def _assert_process_matches_intent(process: Mapping[str, Any], intent: Mapping[str, Any]) -> None:
    mismatched = [key for key in INTENT_PROCESS_KEYS if process.get(key) != intent.get(key)]
    if mismatched:
        raise GatewayRuntimeError("gateway process does not match start intent: " + ", ".join(sorted(mismatched)))


def _candidate_shape_matches(pid: int, intent: Mapping[str, Any]) -> bool:
    proc = Path(f"/proc/{pid}")
    try:
        if proc.stat().st_uid != os.getuid():
            return False
        executable = (proc / "exe").resolve(strict=True)
        cmdline = (proc / "cmdline").read_bytes()
        environment = _read_environ(pid)
    except (FileNotFoundError, PermissionError, ProcessLookupError, GatewayRuntimeError):
        return False
    expected_executable = Path(str(intent["executable"]))
    expected_environment = {
        b"OPENSHELL_GATEWAY_CONFIG": os.fsencode(str(intent["config_path"])),
        b"OPENSHELL_DB_URL": os.fsencode(f"sqlite:{intent['db_path']}"),
        b"OPENSHELL_TELEMETRY_ENABLED": b"false",
        b"OPENSHELL_GATEWAY": GATEWAY_NAME.encode("ascii"),
    }
    return (
        executable == expected_executable
        and cmdline == os.fsencode(expected_executable) + b"\0"
        and all(environment.get(key) == value for key, value in expected_environment.items())
    )


def _find_intent_candidates(project_root: Path, intent: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if not _candidate_shape_matches(pid, intent):
            continue
        process = collect_process_identity(project_root, pid=pid, require_pid_file=False)
        _assert_process_matches_intent(process, intent)
        candidates.append(process)
    return candidates


def _starting_record_matches_process(starting: Mapping[str, Any], process: Mapping[str, Any]) -> None:
    for key in RECORD_KEYS - {"created_at"}:
        if starting.get(key) != process.get(key):
            raise GatewayRuntimeError(f"gateway provisional identity drifted: {key}")


def _terminate_provisional(project_root: Path, process: Mapping[str, Any], intent: Mapping[str, Any]) -> None:
    descriptor = _pidfd_open(int(process["pid"]))
    try:
        current = collect_process_identity(project_root, pid=int(process["pid"]), require_pid_file=False)
        _starting_record_matches_process(process, current)
        _assert_process_matches_intent(current, intent)
        _pidfd_send_signal(descriptor, signal.SIGTERM)
    finally:
        os.close(descriptor)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            current = collect_process_identity(
                project_root,
                pid=int(process["pid"]),
                require_pid_file=False,
            )
        except (FileNotFoundError, ProcessLookupError, GatewayRuntimeError):
            return
        if current["start_ticks"] != process["start_ticks"]:
            return
        time.sleep(0.1)
    raise GatewayRuntimeError("provisional gateway did not stop after pidfd signal")


def _clear_start_evidence(paths: Mapping[str, Path], *, committed: bool) -> None:
    if committed:
        _safe_unlink(paths["start_intent"], label="gateway start intent")
        _safe_unlink(paths["starting"], label="gateway provisional identity")
    else:
        _safe_unlink(paths["starting"], label="gateway provisional identity")
        _safe_unlink(paths["pid_file"], label="gateway PID file")
        _safe_unlink(paths["start_intent"], label="gateway start intent")


def prepare_start(project_root: Path) -> None:
    paths = _paths(project_root)
    for key in ("start_intent", "starting", "pid_file", "runtime"):
        if _exists(paths[key]):
            raise GatewayRuntimeError(f"gateway start evidence already exists: {paths[key]}")
    if _listening_sockets({GATEWAY_PORT, HEALTH_PORT}):
        raise GatewayRuntimeError("gateway ports are occupied before start intent creation")
    _atomic_write(paths["start_intent"], build_start_intent(project_root))


def _transitional_candidate_exists(intent: Mapping[str, Any]) -> bool:
    expected_binary = os.fsencode(str(intent["executable"]))
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            arguments = [item for item in (entry / "cmdline").read_bytes().split(b"\0") if item]
            environment = _read_environ(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError, GatewayRuntimeError):
            continue
        if expected_binary not in arguments:
            continue
        if (
            environment.get(b"OPENSHELL_GATEWAY_CONFIG") == os.fsencode(str(intent["config_path"]))
            and environment.get(b"OPENSHELL_DB_URL") == os.fsencode(f"sqlite:{intent['db_path']}")
            and environment.get(b"OPENSHELL_GATEWAY") == GATEWAY_NAME.encode("ascii")
        ):
            return True
    return False


def attach_start(project_root: Path, pid: int) -> int:
    paths = _paths(project_root)
    intent = load_start_intent(paths["start_intent"], project_root=project_root)
    if _exists(paths["starting"]) or _exists(paths["runtime"]):
        raise GatewayRuntimeError("gateway start identity already exists")
    deadline = time.monotonic() + 5.0
    process: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        candidates = _find_intent_candidates(project_root, intent)
        if len(candidates) > 1:
            raise GatewayRuntimeError("multiple gateway processes match one start intent")
        if candidates:
            process = candidates[0]
            break
        try:
            if not Path(f"/proc/{pid}").exists() and not _transitional_candidate_exists(intent):
                break
        except OSError:
            pass
        time.sleep(0.02)
    if process is None:
        raise GatewayRuntimeError("gateway process did not reach its strict provisional identity")
    _atomic_write_pid(paths["pid_file"], int(process["pid"]))
    _assert_process_matches_intent(process, intent)
    _atomic_write(paths["starting"], process)
    verify_starting_identity(project_root)
    return int(process["pid"])


def commit_start(project_root: Path, pid: int) -> None:
    paths = _paths(project_root)
    intent = load_start_intent(paths["start_intent"], project_root=project_root)
    starting = verify_starting_identity(project_root)
    if starting["pid"] != pid or _exists(paths["runtime"]):
        raise GatewayRuntimeError("gateway commit identity is inconsistent")
    runtime = collect_runtime_identity(project_root, pid=pid)
    _assert_process_matches_intent(runtime, intent)
    comparable_runtime = dict(runtime)
    comparable_runtime["schema"] = STARTING_SCHEMA
    _starting_record_matches_process(starting, comparable_runtime)
    _atomic_write(paths["runtime"], runtime)
    verify_runtime_identity(project_root)
    _clear_start_evidence(paths, committed=True)


def _normalize_pid_file(paths: Mapping[str, Path], pid: int) -> None:
    if _exists(paths["pid_file"]):
        if _read_pid_file(paths["pid_file"]) != pid:
            raise GatewayRuntimeError("gateway PID file conflicts with the strict orphan candidate")
        return
    _atomic_write_pid(paths["pid_file"], pid)


def _remove_stopped_runtime_evidence(paths: Mapping[str, Path]) -> None:
    _safe_unlink(paths["runtime"], label="gateway runtime identity")
    _safe_unlink(paths["starting"], label="gateway provisional identity")
    _safe_unlink(paths["pid_file"], label="gateway PID file")
    _safe_unlink(paths["start_intent"], label="gateway start intent")


def _ensure_reap_evidence(
    project_root: Path,
    paths: Mapping[str, Path],
    runtime: Mapping[str, Any],
) -> None:
    intent = build_start_intent(project_root)
    _assert_process_matches_intent(runtime, intent)
    if _exists(paths["start_intent"]):
        load_start_intent(paths["start_intent"], project_root=project_root)
    else:
        _atomic_write(paths["start_intent"], intent)
    provisional = dict(runtime)
    provisional["schema"] = STARTING_SCHEMA
    if _exists(paths["starting"]):
        starting = load_process_identity(
            paths["starting"],
            expected_schema=STARTING_SCHEMA,
            label="gateway provisional identity",
        )
        _starting_record_matches_process(starting, provisional)
    else:
        _atomic_write(paths["starting"], provisional)


def recover_start(project_root: Path, *, reap: bool = False) -> str:
    paths = _paths(project_root)
    if _exists(paths["runtime"]):
        try:
            runtime = verify_runtime_identity(project_root)
        except GatewayRuntimeError:
            if not (_exists(paths["start_intent"]) and _exists(paths["starting"])):
                raise
            _safe_unlink(paths["runtime"], label="gateway runtime identity")
        else:
            if reap:
                _ensure_reap_evidence(project_root, paths, runtime)
                terminate_attested_gateway(project_root)
                _wait_runtime_process_gone(project_root, runtime)
                _remove_stopped_runtime_evidence(paths)
                return "reaped"
            _clear_start_evidence(paths, committed=True)
            return "running"

    if not _exists(paths["start_intent"]):
        if _exists(paths["starting"]) or _exists(paths["pid_file"]):
            raise GatewayRuntimeError("partial gateway evidence exists without a start intent")
        if _listening_sockets({GATEWAY_PORT, HEALTH_PORT}):
            raise GatewayRuntimeError("gateway listeners exist without attested runtime or start intent")
        return "stopped"

    intent = load_start_intent(paths["start_intent"], project_root=project_root)
    candidates = _find_intent_candidates(project_root, intent)
    if len(candidates) > 1:
        raise GatewayRuntimeError("multiple gateway processes match one start intent")
    if not candidates:
        transition_deadline = time.monotonic() + 5.0
        while _transitional_candidate_exists(intent) and time.monotonic() < transition_deadline:
            time.sleep(0.05)
            candidates = _find_intent_candidates(project_root, intent)
            if candidates:
                break
        if len(candidates) > 1:
            raise GatewayRuntimeError("multiple gateway processes match one start intent")
    if not candidates:
        if _transitional_candidate_exists(intent):
            raise GatewayRuntimeError("gateway launcher remained transitional; recovery evidence was preserved")
        if _listening_sockets({GATEWAY_PORT, HEALTH_PORT}):
            raise GatewayRuntimeError("gateway listeners exist without the strict start-intent process")
        _clear_start_evidence(paths, committed=False)
        return "stopped"

    process = candidates[0]
    if _exists(paths["starting"]):
        starting = load_process_identity(
            paths["starting"],
            expected_schema=STARTING_SCHEMA,
            label="gateway provisional identity",
        )
        _starting_record_matches_process(starting, process)
    _normalize_pid_file(paths, int(process["pid"]))

    ready = True
    try:
        _verify_listener_ownership(int(process["pid"]))
        _verify_health()
    except GatewayRuntimeError:
        ready = False
    if reap or not ready:
        _terminate_provisional(project_root, process, intent)
        _remove_stopped_runtime_evidence(paths)
        if _listening_sockets({GATEWAY_PORT, HEALTH_PORT}):
            raise GatewayRuntimeError("gateway listeners remain after provisional process cleanup")
        return "reaped"

    runtime = collect_runtime_identity(project_root, pid=int(process["pid"]))
    _assert_process_matches_intent(runtime, intent)
    _atomic_write(paths["runtime"], runtime)
    verify_runtime_identity(project_root)
    _clear_start_evidence(paths, committed=True)
    return "adopted"


def _wait_runtime_process_gone(project_root: Path, runtime: Mapping[str, Any]) -> None:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            current = collect_process_identity(
                project_root,
                pid=int(runtime["pid"]),
                require_pid_file=False,
            )
        except (FileNotFoundError, ProcessLookupError, GatewayRuntimeError):
            return
        if current["start_ticks"] != runtime["start_ticks"]:
            return
        time.sleep(0.1)
    raise GatewayRuntimeError("runtime gateway did not stop after pidfd signal")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    attach = subparsers.add_parser("attach")
    attach.add_argument("--pid", type=int, required=True)
    commit = subparsers.add_parser("commit")
    commit.add_argument("--pid", type=int, required=True)
    recover = subparsers.add_parser("recover")
    recover.add_argument("--reap", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        if args.command == "prepare":
            prepare_start(root)
            print("gateway start intent prepared")
        elif args.command == "attach":
            pid = attach_start(root, args.pid)
            print(pid)
        elif args.command == "commit":
            commit_start(root, args.pid)
            print(f"gateway runtime identity committed: pid={args.pid}")
        else:
            result = recover_start(root, reap=args.reap)
            print(f"gateway start recovery: {result}")
        return 0
    except (OSError, ValueError, GatewayRuntimeError) as exc:
        print(f"gateway start recovery error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
