#!/usr/bin/env python3
"""Fetch through the explicit SIQ egress guard without accepting local file inputs."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

import aiohttp

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import egress_guard

DEFAULT_GUARD_ENDPOINT = f"http://{egress_guard.DEFAULT_BIND_HOST}:{egress_guard.DEFAULT_PORT}/v1/request"
MAX_GUARD_RESPONSE_BYTES = egress_guard.DEFAULT_RESPONSE_BODY_BYTES * 2
MappingPayload = dict[str, Any]
GuardPost = Callable[[str, MappingPayload], Awaitable[MappingPayload]]
_MISSING = object()
IDENTITY_TOKEN_ENV = egress_guard.broker_request_identity.EGRESS_TOKEN_ENV
LEGACY_IDENTITY_TOKEN_ENV = "SIQ_OPENSHELL_BROKER_IDENTITY_TOKEN"


class FetchClientError(RuntimeError):
    """Stable client error that never includes a target URL or response body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes


def broker_identity_headers(env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    token = str(source.get(IDENTITY_TOKEN_ENV) or source.get(LEGACY_IDENTITY_TOKEN_ENV) or "").strip()
    if not token:
        return {}
    identity = egress_guard.broker_request_identity
    if len(token.encode("ascii", errors="ignore")) > identity.TOKEN_MAX_BYTES or identity.TOKEN_RE.fullmatch(token) is None:
        raise FetchClientError("broker_identity_token_invalid")
    return {identity.HEADER_NAME: token}


def validate_guard_endpoint(value: str, *, bridge_alias: str | None = None) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise FetchClientError("guard_endpoint_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise FetchClientError("guard_endpoint_invalid") from exc
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/v1/request"
        or parsed.query
        or parsed.fragment
    ):
        raise FetchClientError("guard_endpoint_invalid")
    host = (parsed.hostname or "").lower().rstrip(".")
    alias = bridge_alias.lower().rstrip(".") if bridge_alias else None
    if alias is not None and alias != egress_guard.DEFAULT_BRIDGE_ALIAS:
        raise FetchClientError("bridge_alias_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        if host != "localhost" and (alias is None or host != alias):
            raise FetchClientError("guard_endpoint_host_denied") from exc
    else:
        if not address.is_loopback:
            raise FetchClientError("guard_endpoint_host_denied")
        host = address.compressed
    port = port or 80
    if not 1 <= port <= 65535:
        raise FetchClientError("guard_endpoint_invalid")
    rendered_host = f"[{host}]" if ":" in host else host
    return urlunsplit(SplitResult("http", f"{rendered_host}:{port}", "/v1/request", "", ""))


async def post_to_guard(endpoint: str, payload: MappingPayload) -> MappingPayload:
    timeout = aiohttp.ClientTimeout(total=egress_guard.DEFAULT_TOTAL_TIMEOUT_SECONDS + 5)
    connector = aiohttp.TCPConnector(use_dns_cache=False, force_close=True, limit=1)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            timeout=timeout,
            # OpenShell injects its policy proxy into the sandbox environment.
            # The fixed broker endpoint is still validated above before use.
            trust_env=True,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            skip_auto_headers={"Accept-Encoding", "User-Agent"},
        ) as session:
            async with session.post(
                endpoint,
                json=payload,
                headers=broker_identity_headers(),
                allow_redirects=False,
            ) as response:
                content = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_GUARD_RESPONSE_BYTES:
                        raise FetchClientError("guard_response_too_large")
                try:
                    decoded = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise FetchClientError("guard_response_invalid") from exc
                if not isinstance(decoded, dict):
                    raise FetchClientError("guard_response_invalid")
                return decoded
    except FetchClientError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise FetchClientError("guard_unavailable") from exc


async def run_fetch(
    *,
    endpoint: str,
    method: str,
    target_url: str,
    json_body: Any = _MISSING,
    post: GuardPost = post_to_guard,
    bridge_alias: str | None = None,
) -> FetchResult:
    if method not in {"GET", "HEAD", "POST"}:
        raise FetchClientError("method_invalid")
    endpoint = validate_guard_endpoint(endpoint, bridge_alias=bridge_alias)
    try:
        egress_guard.parse_target_url(target_url)
    except egress_guard.BrokerInputError as exc:
        raise FetchClientError(exc.code) from exc
    if method in {"GET", "HEAD"} and json_body is not _MISSING:
        raise FetchClientError("read_method_body_forbidden")
    if method == "POST" and json_body is _MISSING:
        raise FetchClientError("json_body_required")
    payload: MappingPayload = {"method": method, "url": target_url}
    if json_body is not _MISSING:
        payload["json_body"] = json_body
    response = await post(endpoint, payload)
    if response.get("ok") is not True:
        error_code = response.get("error_code")
        if not isinstance(error_code, str) or not error_code:
            raise FetchClientError("guard_response_invalid")
        raise FetchClientError(error_code)
    status = response.get("status")
    encoded_body = response.get("body_base64")
    declared_size = response.get("body_bytes")
    if (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not 100 <= status <= 599
        or not isinstance(encoded_body, str)
        or isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size < 0
    ):
        raise FetchClientError("guard_response_invalid")
    try:
        body = base64.b64decode(encoded_body, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FetchClientError("guard_response_invalid") from exc
    if len(body) != declared_size or len(body) > egress_guard.DEFAULT_RESPONSE_BODY_BYTES:
        raise FetchClientError("guard_response_invalid")
    return FetchResult(status=status, body=body)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public http(s) target URL; local paths and file URLs are rejected")
    parser.add_argument("--method", choices=("GET", "HEAD", "POST"), default="GET")
    parser.add_argument("--json-body", help="Inline JSON only; @file and path options are not supported")
    parser.add_argument("--guard-endpoint", default=DEFAULT_GUARD_ENDPOINT)
    parser.add_argument("--bridge-alias", choices=(egress_guard.DEFAULT_BRIDGE_ALIAS,))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        endpoint = validate_guard_endpoint(args.guard_endpoint, bridge_alias=args.bridge_alias)
        json_body: Any = _MISSING
        if args.json_body is not None:
            try:
                json_body = json.loads(args.json_body)
            except json.JSONDecodeError as exc:
                raise FetchClientError("json_body_invalid") from exc
        result = asyncio.run(
            run_fetch(
                endpoint=endpoint,
                method=args.method,
                target_url=args.url,
                json_body=json_body,
                bridge_alias=args.bridge_alias,
            )
        )
        sys.stdout.buffer.write(result.body)
        sys.stdout.buffer.flush()
        return 0 if 200 <= result.status < 400 else 3
    except (FetchClientError, egress_guard.BrokerError) as exc:
        print(f"siq fetch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
