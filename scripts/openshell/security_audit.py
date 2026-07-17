#!/usr/bin/env python3
"""Write minimal, secret-free SIQ OpenShell runtime audit records."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "siq.openshell.audit.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_RELATIVE_ROOT = Path("var/openshell/audit")
MAX_RECORD_BYTES = 16 * 1024
DECISIONS = {"allow", "deny", "audit_only"}
OPERATION_CLASSES = {
    "database.query",
    "filesystem.delete",
    "filesystem.write",
    "immutable.write",
    "network.request",
    "publisher.index",
    "runtime.route",
    "sandbox.lifecycle",
    "service.preflight",
}
TARGET_KINDS = {"host", "path", "process", "service", "none"}
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
ERROR_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,95}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FORBIDDEN_SERIALIZED_TERMS = (
    "authorization",
    "cookie",
    "database_url",
    "dsn",
    "password",
    "private_key",
    "prompt",
    "request_body",
    "user_input",
)


class SecurityAuditError(RuntimeError):
    """Raised when an audit record could expose data or escape its root."""


@dataclass(frozen=True)
class SecurityRunContext:
    profile: str
    sandbox_id: str
    run_id: str
    session_id: str
    policy_digest: str

    def validate(self) -> None:
        for label, value in (
            ("profile", self.profile),
            ("sandbox_id", self.sandbox_id),
            ("run_id", self.run_id),
        ):
            if not SAFE_ID_RE.fullmatch(value):
                raise SecurityAuditError(f"invalid_{label}")
        if (
            not self.session_id
            or len(self.session_id) > 512
            or any(ord(character) < 32 for character in self.session_id)
        ):
            raise SecurityAuditError("invalid_session_id")
        if not SHA256_RE.fullmatch(self.policy_digest):
            raise SecurityAuditError("invalid_policy_digest")


def _stable_projection(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()[:24]


def project_target(*, kind: str, scope: str, value: str = "") -> dict[str, str]:
    if kind not in TARGET_KINDS:
        raise SecurityAuditError("invalid_target_kind")
    if not SAFE_ID_RE.fullmatch(scope):
        raise SecurityAuditError("invalid_target_scope")
    if kind == "none":
        if value:
            raise SecurityAuditError("none_target_must_not_have_value")
        return {"kind": kind, "scope": scope, "projection": "none"}
    if not value or len(value) > 4096 or "\x00" in value:
        raise SecurityAuditError("invalid_target_value")
    return {
        "kind": kind,
        "scope": scope,
        "projection": _stable_projection(f"target:{kind}:{scope}", value),
    }


def build_record(
    *,
    context: SecurityRunContext,
    operation_class: str,
    target: Mapping[str, str],
    decision: str,
    error_code: str,
    duration_ms: int,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    context.validate()
    if operation_class not in OPERATION_CLASSES:
        raise SecurityAuditError("invalid_operation_class")
    if decision not in DECISIONS:
        raise SecurityAuditError("invalid_decision")
    if error_code and not ERROR_CODE_RE.fullmatch(error_code):
        raise SecurityAuditError("invalid_error_code")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 0 <= duration_ms <= 86_400_000:
        raise SecurityAuditError("invalid_duration_ms")
    expected_target_keys = {"kind", "scope", "projection"}
    if set(target) != expected_target_keys:
        raise SecurityAuditError("invalid_target_projection")
    if target.get("kind") not in TARGET_KINDS or not SAFE_ID_RE.fullmatch(str(target.get("scope") or "")):
        raise SecurityAuditError("invalid_target_projection")
    projection = str(target.get("projection") or "")
    if projection != "none" and not re.fullmatch(r"[0-9a-f]{24}", projection):
        raise SecurityAuditError("invalid_target_projection")

    occurred_at = timestamp or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        raise SecurityAuditError("timestamp_must_be_timezone_aware")
    occurred_at = occurred_at.astimezone(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": occurred_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "profile": context.profile,
        "sandbox_id": context.sandbox_id,
        "siq_run_id": context.run_id,
        "session_projection": _stable_projection("session", context.session_id),
        "operation_class": operation_class,
        "target": dict(target),
        "decision": decision,
        "policy_digest": context.policy_digest,
        "error_code": error_code,
        "duration_ms": duration_ms,
    }


def serialize_record(record: Mapping[str, Any]) -> bytes:
    if set(record) != {
        "schema_version",
        "timestamp",
        "profile",
        "sandbox_id",
        "siq_run_id",
        "session_projection",
        "operation_class",
        "target",
        "decision",
        "policy_digest",
        "error_code",
        "duration_ms",
    }:
        raise SecurityAuditError("invalid_record_fields")
    content = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    lowered = content.lower()
    if any(term.encode() in lowered for term in FORBIDDEN_SERIALIZED_TERMS):
        raise SecurityAuditError("forbidden_audit_field")
    if len(content) > MAX_RECORD_BYTES:
        raise SecurityAuditError("audit_record_too_large")
    return content


def _assert_safe_directory_chain(root: Path, directory: Path) -> None:
    root = root.resolve(strict=True)
    directory.relative_to(root)
    current = root
    for part in directory.relative_to(root).parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise SecurityAuditError("unsafe_audit_directory")


def append_record(*, project_root: Path, record: Mapping[str, Any], sync: bool = True) -> Path:
    root = project_root.resolve(strict=True)
    audit_root = root / AUDIT_RELATIVE_ROOT
    _assert_safe_directory_chain(root, audit_root)
    timestamp = str(record.get("timestamp") or "")
    try:
        date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise SecurityAuditError("invalid_record_timestamp") from exc
    output = audit_root / f"{date}.jsonl"
    if output.is_symlink():
        raise SecurityAuditError("unsafe_audit_file")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(output, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecurityAuditError("unsafe_audit_file")
        os.fchmod(descriptor, 0o600)
        content = serialize_record(record)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            written = os.write(descriptor, content)
            if written != len(content):
                raise SecurityAuditError("short_audit_write")
            if sync:
                os.fsync(descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--sandbox-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--operation-class", choices=sorted(OPERATION_CLASSES), required=True)
    parser.add_argument("--target-kind", choices=sorted(TARGET_KINDS), required=True)
    parser.add_argument("--target-scope", required=True)
    parser.add_argument("--target-value", default="")
    parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    parser.add_argument("--error-code", default="")
    parser.add_argument("--duration-ms", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        context = SecurityRunContext(
            profile=args.profile,
            sandbox_id=args.sandbox_id,
            run_id=args.run_id,
            session_id=args.session_id,
            policy_digest=args.policy_digest,
        )
        record = build_record(
            context=context,
            operation_class=args.operation_class,
            target=project_target(kind=args.target_kind, scope=args.target_scope, value=args.target_value),
            decision=args.decision,
            error_code=args.error_code,
            duration_ms=args.duration_ms,
        )
        append_record(project_root=args.project_root, record=record)
        print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
        return 0
    except (OSError, ValueError, SecurityAuditError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
