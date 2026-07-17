#!/usr/bin/env python3
"""Validate that Hermes auth state contains only reviewed OpenShell placeholders."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

MAX_AUTH_BYTES = 32 * 1024
EXPECTED_POOL = (
    {
        "id": "minimax_cn_primary_0",
        "label": "MINIMAX_CN_API_KEY_PRIMARY",
        "priority": 0,
        "access_token": "openshell:resolve:env:SIQ_MINIMAX_CN_PRIMARY",
    },
    {
        "id": "minimax_cn_backup_10",
        "label": "MINIMAX_CN_API_KEY_BACKUP",
        "priority": 10,
        "access_token": "openshell:resolve:env:SIQ_MINIMAX_CN_BACKUP",
    },
)
FIXED_ENTRY_FIELDS = {
    "auth_type",
    "base_url",
    "id",
    "label",
    "priority",
    "source",
    "access_token",
}
MUTABLE_ENTRY_FIELDS = {
    "last_error_code",
    "last_error_message",
    "last_error_reason",
    "last_error_reset_at",
    "last_status",
    "last_status_at",
    "request_count",
}
ALLOWED_ENTRY_FIELDS = FIXED_ENTRY_FIELDS | MUTABLE_ENTRY_FIELDS


class PlaceholderAuthError(RuntimeError):
    pass


def _require_private_regular_file(path: Path, *, expected_uid: int, label: str) -> bytes:
    if path.is_symlink():
        raise PlaceholderAuthError(f"{label}_symlink")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise PlaceholderAuthError(f"{label}_missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PlaceholderAuthError(f"{label}_not_private_regular_file")
    if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) & 0o077:
        raise PlaceholderAuthError(f"{label}_not_private_regular_file")
    if info.st_size > MAX_AUTH_BYTES:
        raise PlaceholderAuthError(f"{label}_too_large")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise PlaceholderAuthError(f"{label}_changed")
        content = os.read(descriptor, MAX_AUTH_BYTES + 1)
    finally:
        os.close(descriptor)
    return content


def _validate_mutable_fields(entry: Mapping[str, Any]) -> None:
    request_count = entry.get("request_count")
    if isinstance(request_count, bool) or not isinstance(request_count, int) or request_count < 0:
        raise PlaceholderAuthError("auth_mutable_state_invalid")
    if entry.get("last_status") not in {None, "ok", "exhausted"}:
        raise PlaceholderAuthError("auth_mutable_state_invalid")
    for name in MUTABLE_ENTRY_FIELDS - {"request_count", "last_status"}:
        value = entry.get(name)
        if value is not None and not isinstance(value, (str, int, float)):
            raise PlaceholderAuthError("auth_mutable_state_invalid")


def validate_placeholder_auth(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) - {"version", "providers", "credential_pool", "updated_at"}:
        raise PlaceholderAuthError("auth_shape_invalid")
    if payload.get("version") != 1 or payload.get("providers") != {}:
        raise PlaceholderAuthError("auth_shape_invalid")
    if "updated_at" in payload and not isinstance(payload["updated_at"], str):
        raise PlaceholderAuthError("auth_shape_invalid")
    credential_pool = payload.get("credential_pool")
    if not isinstance(credential_pool, dict) or set(credential_pool) != {"minimax-cn"}:
        raise PlaceholderAuthError("auth_pool_invalid")
    entries = credential_pool["minimax-cn"]
    if not isinstance(entries, list) or len(entries) != len(EXPECTED_POOL):
        raise PlaceholderAuthError("auth_pool_invalid")
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
    if len(by_id) != len(EXPECTED_POOL):
        raise PlaceholderAuthError("auth_pool_invalid")
    for expected in EXPECTED_POOL:
        entry = by_id.get(expected["id"])
        if not isinstance(entry, dict) or set(entry) != ALLOWED_ENTRY_FIELDS:
            raise PlaceholderAuthError("auth_entry_invalid")
        if any(entry.get(field) != value for field, value in expected.items()):
            raise PlaceholderAuthError("auth_placeholder_invalid")
        if entry.get("auth_type") != "api_key" or entry.get("source") != "openshell:provider":
            raise PlaceholderAuthError("auth_entry_invalid")
        if entry.get("base_url") != "https://api.minimax.chat/v1":
            raise PlaceholderAuthError("auth_entry_invalid")
        _validate_mutable_fields(entry)


def validate_files(*, auth_file: Path, lock_file: Path, expected_user: str = "sandbox") -> None:
    try:
        expected_uid = pwd.getpwnam(expected_user).pw_uid
    except KeyError as exc:
        raise PlaceholderAuthError("sandbox_user_missing") from exc
    content = _require_private_regular_file(auth_file, expected_uid=expected_uid, label="auth_file")
    _require_private_regular_file(lock_file, expected_uid=expected_uid, label="auth_lock")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlaceholderAuthError("auth_json_invalid") from exc
    validate_placeholder_auth(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--expected-user", default="sandbox")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        validate_files(auth_file=args.auth_file, lock_file=args.lock_file, expected_user=args.expected_user)
        print("OpenShell placeholder auth: PASS")
        return 0
    except (OSError, PlaceholderAuthError) as exc:
        print(f"OpenShell placeholder auth failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
