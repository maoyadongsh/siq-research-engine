#!/usr/bin/env python3
"""Sign and verify the task identity carried to SIQ host brokers.

The signing key stays on the host broker/lifecycle side.  A sandbox receives
only a short-lived token whose claims are fixed to one lifecycle transaction.
The token contains no prompt, URL, credential, or request body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = "siq.openshell.broker-request-identity.v1"
AUTH_SCHEME = "hmac-sha256-v1"
HEADER_NAME = "X-SIQ-OpenShell-Identity"
KEY_BYTES = 32
DEFAULT_TTL_SECONDS = 24 * 60 * 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
CLOCK_SKEW_SECONDS = 60
TOKEN_MAX_BYTES = 4096
KEY_FILE_BYTES = KEY_BYTES * 2 + 1
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
TOKEN_RE = re.compile(r"v1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\Z")
EGRESS_AUDIENCE = "siq-egress-guard"
DATA_AUDIENCE = "siq-read-only-data-broker"
EGRESS_TOKEN_ENV = "SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN"
DATA_TOKEN_ENV = "SIQ_OPENSHELL_DATA_IDENTITY_TOKEN"


class IdentityError(RuntimeError):
    """Stable identity failure without exposing token/key material."""


@dataclass(frozen=True)
class RequestIdentity:
    audience: str
    profile: str
    run_id: str
    sandbox_id: str
    session_id: str
    policy_digest: str
    run_nonce_digest: str
    issued_at: int
    expires_at: int
    schema_version: str = SCHEMA_VERSION

    def as_claims(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gateway": "siq-openshell-dev",
            "audience": self.audience,
            "profile": self.profile,
            "run_id": self.run_id,
            "sandbox_id": self.sandbox_id,
            "session_id": self.session_id,
            "policy_sha256": self.policy_digest,
            "run_nonce_sha256": self.run_nonce_digest,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class IssuedBrokerIdentities:
    """Two audience-limited tokens for one sandbox lifecycle transaction."""

    egress_token: str = field(repr=False)
    data_token: str = field(repr=False)
    issued_at: int
    expires_at: int

    def as_environment(self) -> dict[str, str]:
        return {
            EGRESS_TOKEN_ENV: self.egress_token,
            DATA_TOKEN_ENV: self.data_token,
        }

    def secret_values(self) -> tuple[str, str]:
        return (self.egress_token, self.data_token)


_CURRENT_IDENTITY = ContextVar("siq_openshell_broker_request_identity", default=None)


@contextmanager
def request_identity_context(identity: RequestIdentity) -> Iterator[None]:
    if not isinstance(identity, RequestIdentity):
        raise IdentityError("broker_identity_context_invalid")
    marker: Token[RequestIdentity | None] = _CURRENT_IDENTITY.set(identity)
    try:
        yield
    finally:
        _CURRENT_IDENTITY.reset(marker)


def current_request_identity() -> RequestIdentity | None:
    return _CURRENT_IDENTITY.get()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeError) as exc:
        raise IdentityError("broker_identity_token_invalid") from exc
    if _b64encode(decoded) != value:
        raise IdentityError("broker_identity_token_invalid")
    return decoded


def _key_bytes(key: bytes | bytearray | str) -> bytes:
    if isinstance(key, str):
        try:
            key = bytes.fromhex(key.strip())
        except ValueError as exc:
            raise IdentityError("broker_identity_key_invalid") from exc
    try:
        value = bytes(key)
    except (TypeError, ValueError) as exc:
        raise IdentityError("broker_identity_key_invalid") from exc
    if len(value) != KEY_BYTES:
        raise IdentityError("broker_identity_key_invalid")
    return value


def _claim_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise IdentityError("broker_identity_claim_invalid")
    return value


def _claim_digest(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise IdentityError("broker_identity_claim_invalid")
    return value


def _validate_claims(payload: Any, *, now: int, expected_gateway: str) -> RequestIdentity:
    if not isinstance(payload, dict):
        raise IdentityError("broker_identity_token_invalid")
    expected_keys = {
        "schema_version",
        "gateway",
        "audience",
        "profile",
        "run_id",
        "sandbox_id",
        "session_id",
        "policy_sha256",
        "run_nonce_sha256",
        "issued_at",
        "expires_at",
    }
    if set(payload) != expected_keys or payload.get("schema_version") != SCHEMA_VERSION:
        raise IdentityError("broker_identity_claim_invalid")
    if payload.get("gateway") != expected_gateway:
        raise IdentityError("broker_identity_gateway_mismatch")
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if (
        isinstance(issued_at, bool)
        or isinstance(expires_at, bool)
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_TTL_SECONDS
        or issued_at > now + CLOCK_SKEW_SECONDS
        or expires_at < now - CLOCK_SKEW_SECONDS
    ):
        raise IdentityError("broker_identity_token_expired")
    return RequestIdentity(
        audience=_claim_text(payload, "audience"),
        profile=_claim_text(payload, "profile"),
        run_id=_claim_text(payload, "run_id"),
        sandbox_id=_claim_text(payload, "sandbox_id"),
        session_id=_claim_text(payload, "session_id"),
        policy_digest=_claim_digest(payload, "policy_sha256"),
        run_nonce_digest=_claim_digest(payload, "run_nonce_sha256"),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def sign_identity(
    key: bytes | bytearray | str,
    *,
    audience: str = "siq-brokers",
    profile: str,
    run_id: str,
    sandbox_id: str,
    session_id: str,
    policy_digest: str,
    run_nonce_digest: str,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    key_value = _key_bytes(key)
    issued_at = int(time.time()) if now is None else now
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        raise IdentityError("broker_identity_time_invalid")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise IdentityError("broker_identity_ttl_invalid")
    payload = RequestIdentity(
        audience=audience,
        profile=profile,
        run_id=run_id,
        sandbox_id=sandbox_id,
        session_id=session_id,
        policy_digest=policy_digest,
        run_nonce_digest=run_nonce_digest,
        issued_at=issued_at,
        expires_at=issued_at + ttl_seconds,
    )
    claims = payload.as_claims()
    # Validate before signing so a caller cannot issue a token that the broker
    # would reject.  The fixed gateway is intentional and not caller supplied.
    _validate_claims(claims, now=issued_at, expected_gateway="siq-openshell-dev")
    encoded = _b64encode(json.dumps(claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii"))
    signing_input = f"v1.{encoded}".encode("ascii")
    signature = hmac.new(key_value, signing_input, hashlib.sha256).digest()
    token = f"v1.{encoded}.{_b64encode(signature)}"
    if len(token.encode("ascii")) > TOKEN_MAX_BYTES:
        raise IdentityError("broker_identity_token_too_large")
    return token


def issue_broker_identities(
    key: bytes | bytearray | str,
    *,
    profile: str,
    run_id: str,
    sandbox_id: str,
    session_id: str,
    policy_digest: str,
    run_nonce_digest: str,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> IssuedBrokerIdentities:
    """Issue the fixed egress/data token pair used by formal and pilot lifecycles."""

    issued_at = int(time.time()) if now is None else now
    common = {
        "profile": profile,
        "run_id": run_id,
        "sandbox_id": sandbox_id,
        "session_id": session_id,
        "policy_digest": policy_digest,
        "run_nonce_digest": run_nonce_digest,
        "now": issued_at,
        "ttl_seconds": ttl_seconds,
    }
    egress_token = sign_identity(key, audience=EGRESS_AUDIENCE, **common)
    data_token = sign_identity(key, audience=DATA_AUDIENCE, **common)
    return IssuedBrokerIdentities(
        egress_token=egress_token,
        data_token=data_token,
        issued_at=issued_at,
        expires_at=issued_at + ttl_seconds,
    )


def verify_identity(
    token: str,
    key: bytes | bytearray | str,
    *,
    expected_gateway: str = "siq-openshell-dev",
    now: int | None = None,
) -> RequestIdentity:
    if not isinstance(token, str) or len(token.encode("utf-8", errors="ignore")) > TOKEN_MAX_BYTES:
        raise IdentityError("broker_identity_token_invalid")
    match = TOKEN_RE.fullmatch(token)
    if match is None:
        raise IdentityError("broker_identity_token_invalid")
    encoded, encoded_signature = match.groups()
    signature = _b64decode(encoded_signature)
    if len(signature) != hashlib.sha256().digest_size:
        raise IdentityError("broker_identity_token_invalid")
    key_value = _key_bytes(key)
    signing_input = f"v1.{encoded}".encode("ascii")
    expected_signature = hmac.new(key_value, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise IdentityError("broker_identity_signature_invalid")
    payload_bytes = _b64decode(encoded)
    try:
        payload = json.loads(payload_bytes.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityError("broker_identity_token_invalid") from exc
    checked_now = int(time.time()) if now is None else now
    if isinstance(checked_now, bool) or not isinstance(checked_now, int):
        raise IdentityError("broker_identity_time_invalid")
    return _validate_claims(payload, now=checked_now, expected_gateway=expected_gateway)


def verify_header_values(
    values: Sequence[str],
    key: bytes | bytearray | str,
    *,
    expected_profile: str = "siq_analysis",
    expected_audience: str | None = None,
    expected_gateway: str = "siq-openshell-dev",
    now: int | None = None,
) -> RequestIdentity:
    if isinstance(values, (str, bytes, bytearray)):
        raise IdentityError("broker_identity_header_invalid")
    if not values:
        raise IdentityError("broker_identity_header_required")
    if len(values) != 1 or not isinstance(values[0], str):
        raise IdentityError("broker_identity_header_invalid")
    identity = verify_identity(values[0], key, expected_gateway=expected_gateway, now=now)
    if not SAFE_ID_RE.fullmatch(expected_profile) or identity.profile != expected_profile:
        raise IdentityError("broker_identity_profile_mismatch")
    if expected_audience is not None and (
        not SAFE_ID_RE.fullmatch(expected_audience) or identity.audience != expected_audience
    ):
        raise IdentityError("broker_identity_audience_mismatch")
    return identity


def _open_private_parent(path: Path, *, create: bool) -> int:
    if not path.name or path.name in {".", ".."}:
        raise IdentityError("broker_identity_key_file_invalid")
    try:
        if create:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(descriptor)
            raise IdentityError("broker_identity_key_parent_invalid")
        return descriptor
    except IdentityError:
        raise
    except OSError as exc:
        raise IdentityError("broker_identity_key_parent_invalid") from exc


def _read_key_at(parent_descriptor: int, name: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != KEY_FILE_BYTES
        ):
            raise IdentityError("broker_identity_key_file_invalid")
        content = bytearray()
        while len(content) <= KEY_FILE_BYTES:
            chunk = os.read(descriptor, KEY_FILE_BYTES + 1 - len(content))
            if not chunk:
                break
            content.extend(chunk)
    except IdentityError:
        raise
    except OSError as exc:
        raise IdentityError("broker_identity_key_file_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) != KEY_FILE_BYTES:
        raise IdentityError("broker_identity_key_file_invalid")
    try:
        value = bytes(content).decode("ascii")
    except UnicodeDecodeError as exc:
        raise IdentityError("broker_identity_key_file_invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{64}\n", value):
        raise IdentityError("broker_identity_key_file_invalid")
    return _key_bytes(value)


def read_key_file(path: Path) -> bytes:
    parent_descriptor = _open_private_parent(path, create=False)
    try:
        return _read_key_at(parent_descriptor, path.name)
    finally:
        os.close(parent_descriptor)


def _read_key_after_publication(parent_descriptor: int, name: str) -> bytes:
    for attempt in range(50):
        try:
            return _read_key_at(parent_descriptor, name)
        except IdentityError:
            try:
                info = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except OSError:
                raise
            publishing = (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and info.st_nlink == 2
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_size == KEY_FILE_BYTES
            )
            if not publishing or attempt == 49:
                raise
            time.sleep(0.001)
    raise IdentityError("broker_identity_key_file_invalid")


def ensure_key_file(path: Path) -> bytes:
    parent_descriptor = _open_private_parent(path, create=True)
    value = secrets.token_bytes(KEY_BYTES)
    descriptor = -1
    temporary_name: str | None = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return _read_key_after_publication(parent_descriptor, path.name)
        assert temporary_name is not None
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        content = value.hex().encode("ascii") + b"\n"
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short key write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
            temporary_name = None
            return _read_key_after_publication(parent_descriptor, path.name)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = None
        os.fsync(parent_descriptor)
    except IdentityError:
        raise
    except OSError as exc:
        raise IdentityError("broker_identity_key_file_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)
    return read_key_file(path)


def rotate_key_file(path: Path) -> bytes:
    """Atomically replace an existing private key without exposing its value."""

    parent_descriptor = _open_private_parent(path, create=False)
    descriptor = -1
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.rotate"
    value = secrets.token_bytes(KEY_BYTES)
    try:
        _read_key_at(parent_descriptor, path.name)
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        content = value.hex().encode("ascii") + b"\n"
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short key write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = ""
        os.fsync(parent_descriptor)
    except IdentityError:
        raise
    except OSError as exc:
        raise IdentityError("broker_identity_key_file_rotate_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)
    return read_key_file(path)
