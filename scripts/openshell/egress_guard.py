#!/usr/bin/env python3
"""Explicit SIQ outbound request broker; never exposes a transparent proxy or CONNECT."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (
    bridge_endpoint,
    broker_request_identity,
    egress_decision,
    security_audit,
)

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 18792
DEFAULT_BRIDGE_ALIAS = bridge_endpoint.HOST_ALIAS
DEFAULT_RESPONSE_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_RESPONSE_HEADER_BYTES = 32 * 1024
DEFAULT_REQUEST_HEADER_BYTES = 16 * 1024
DEFAULT_TOTAL_TIMEOUT_SECONDS = 30.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_QUEUE_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_CONCURRENCY = 16
DEFAULT_MAX_REDIRECTS = 8
MAX_REQUEST_ENVELOPE_BYTES = 256 * 1024
MAX_URL_BYTES = 8 * 1024
MAX_HEADER_COUNT = 128
MAX_DNS_RESULTS = 16
MAX_MIHOMO_RESPONSE_BYTES = 64 * 1024
DEFAULT_MIHOMO_TIMEOUT_SECONDS = 3.0
MIHOMO_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
MIHOMO_COMPAT_ENV = "SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT"
MIHOMO_SOCKET_ENV = "SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET"
MIHOMO_RANGE_ENV = "SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BROKER_METHODS = {"GET", "HEAD", "POST"}
PROVIDER_DIRECT_CATEGORIES = {"model", "search"}
RUNTIME_SOURCE_MODULES = (
    ("broker_request_identity", broker_request_identity),
    ("egress_decision", egress_decision),
    ("egress_guard", sys.modules[__name__]),
    ("security_audit", security_audit),
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
UNKNOWN_STRIPPED_HEADERS = {"authorization", "cookie"}
RESPONSE_STRIPPED_HEADERS = HOP_BY_HOP_HEADERS | {"set-cookie"}
HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
ENVELOPE_KEYS = {"method", "url", "json_body", "headers"}
REQUIRED_ENVELOPE_KEYS = {"method", "url"}
_MISSING = object()


class BrokerError(RuntimeError):
    """Stable broker failure whose message never includes a request value."""

    def __init__(self, code: str, *, http_status: int) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class BrokerInputError(BrokerError):
    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code, http_status=http_status)


class BrokerDenied(BrokerError):
    def __init__(self, decision: egress_decision.EgressDecision) -> None:
        super().__init__(decision.rule_id, http_status=403)
        self.decision = decision


class BrokerUpstreamError(BrokerError):
    def __init__(self, code: str, *, http_status: int = 502) -> None:
        super().__init__(code, http_status=http_status)


class BrokerBusyError(BrokerError):
    def __init__(self) -> None:
        super().__init__("broker_concurrency_limit", http_status=429)


def _canonical_digest(value: Any) -> str:
    content = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return hashlib.sha256(content).hexdigest()


def allowlist_contract_sha256(allowlist: egress_decision.Allowlist) -> str:
    """Digest the exact normalized allowlist object used by this process."""

    return _canonical_digest(
        {
            "unknown_json_post_max_bytes": allowlist.unknown_json_post_max_bytes,
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "category": rule.category,
                    "host_patterns": sorted(rule.host_patterns),
                    "schemes": sorted(rule.schemes),
                    "ports": sorted(rule.ports),
                    "methods": sorted(rule.methods),
                    "content_types": sorted(rule.content_types),
                    "max_body_bytes": rule.max_body_bytes,
                }
                for rule in allowlist.rules
            ],
        }
    )


def runtime_source_bundle_sha256() -> str:
    """Digest the broker modules loaded by the running interpreter."""

    digest = hashlib.sha256()
    for label, module in RUNTIME_SOURCE_MODULES:
        raw_path = getattr(module, "__file__", None)
        if not isinstance(raw_path, str):
            raise BrokerInputError("runtime_source_binding_invalid", http_status=503)
        path = Path(raw_path)
        descriptor = -1
        try:
            info = path.lstat()
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            content_digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                content_digest.update(chunk)
                size += len(chunk)
        except OSError as exc:
            raise BrokerInputError("runtime_source_binding_invalid", http_status=503) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size, size)
            != (info.st_dev, info.st_ino, info.st_size, info.st_size)
        ):
            raise BrokerInputError("runtime_source_binding_invalid", http_status=503)
        digest.update(label.encode("ascii") + b"\0" + content_digest.digest() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class BrokerLimits:
    request_body_bytes: int
    request_header_bytes: int = DEFAULT_REQUEST_HEADER_BYTES
    response_body_bytes: int = DEFAULT_RESPONSE_BODY_BYTES
    response_header_bytes: int = DEFAULT_RESPONSE_HEADER_BYTES
    header_count: int = MAX_HEADER_COUNT
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    queue_timeout_seconds: float = DEFAULT_QUEUE_TIMEOUT_SECONDS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    def validate(self) -> None:
        integer_limits = (
            self.request_body_bytes,
            self.request_header_bytes,
            self.response_body_bytes,
            self.response_header_bytes,
            self.header_count,
            self.max_redirects,
            self.max_concurrency,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in integer_limits):
            raise BrokerInputError("broker_limits_invalid")
        if self.request_body_bytes > 1024 * 1024 or self.response_body_bytes > 64 * 1024 * 1024:
            raise BrokerInputError("broker_limits_invalid")
        if self.header_count > 512 or self.max_redirects > egress_decision.MAX_REDIRECT_HOPS:
            raise BrokerInputError("broker_limits_invalid")
        for value in (
            self.total_timeout_seconds,
            self.connect_timeout_seconds,
            self.queue_timeout_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 300:
                raise BrokerInputError("broker_limits_invalid")


@dataclass(frozen=True)
class ParsedTarget:
    url: str
    scheme: str
    host: str
    port: int

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port


@dataclass(frozen=True)
class BrokerRequest:
    method: str
    target: ParsedTarget
    body: bytes | None
    content_type: str | None
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    peer_ip: str


@dataclass(frozen=True)
class BrokerResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    decision: egress_decision.EgressDecision
    redirect_hops: int

    def as_api_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "status": self.status,
            "headers": [[name, value] for name, value in self.headers],
            "body_base64": base64.b64encode(self.body).decode("ascii"),
            "body_bytes": len(self.body),
            "egress": self.decision.as_dict(),
            "redirect_hops": self.redirect_hops,
        }


@dataclass(frozen=True)
class DNSResolution:
    addresses: tuple[str, ...]
    provenance_rule_id: str = ""


class DNSResolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...] | DNSResolution: ...


class OutboundTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        target: ParsedTarget,
        headers: Sequence[tuple[str, str]],
        body: bytes | None,
        resolved_ips: Sequence[str],
        limits: BrokerLimits,
    ) -> TransportResponse: ...


class AuditSink(Protocol):
    async def record(
        self,
        decision: egress_decision.EgressDecision,
        *,
        error_code: str,
        duration_ms: int,
    ) -> None: ...


class SystemDNSResolver:
    """Resolve once per hop; the returned addresses are later pinned by the transport."""

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return (literal.compressed,)
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except (OSError, UnicodeError) as exc:
            raise BrokerUpstreamError("dns_resolution_failed") from exc
        addresses: list[str] = []
        for record in records:
            try:
                address = ipaddress.ip_address(record[4][0]).compressed
            except (ValueError, IndexError, TypeError) as exc:
                raise BrokerUpstreamError("dns_result_invalid") from exc
            if address not in addresses:
                addresses.append(address)
        if not addresses or len(addresses) > MAX_DNS_RESULTS:
            raise BrokerUpstreamError("dns_result_invalid")
        return tuple(addresses)


def parse_mihomo_fake_ip_network(value: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise BrokerInputError("mihomo_fake_ip_range_invalid") from exc
    if (
        not isinstance(network, ipaddress.IPv4Network)
        or not network.subnet_of(MIHOMO_BENCHMARK_NETWORK)
        or network.prefixlen < 16
    ):
        raise BrokerInputError("mihomo_fake_ip_range_invalid")
    return network


def _validate_mihomo_socket(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise BrokerUpstreamError("mihomo_control_socket_unsafe")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise BrokerUpstreamError("mihomo_control_socket_unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
        if current == path and not stat.S_ISSOCK(info.st_mode):
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
        allowed_owner = info.st_uid in {0, os.getuid()}
        allowed_group = info.st_gid in {os.getgid(), *os.getgroups()}
        world_writable_sticky_root = (
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        )
        if not allowed_owner:
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
        if stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_IWOTH and not world_writable_sticky_root:
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
        if (
            stat.S_ISDIR(info.st_mode)
            and info.st_mode & stat.S_IWGRP
            and not world_writable_sticky_root
            and not (allowed_group or info.st_uid == os.getuid())
        ):
            raise BrokerUpstreamError("mihomo_control_socket_unsafe")
    return path


class MihomoFakeIPDNSResolver:
    """Replace only verified Mihomo fake-IP answers with bounded real DNS answers."""

    mode = "mihomo_fake_ip_verified"

    def __init__(
        self,
        *,
        system_resolver: DNSResolver,
        control_socket: Path,
        fake_ip_network: ipaddress.IPv4Network,
        timeout_seconds: float = DEFAULT_MIHOMO_TIMEOUT_SECONDS,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise BrokerInputError("mihomo_timeout_invalid")
        self._system_resolver = system_resolver
        self._control_socket = control_socket
        self._fake_ip_network = fake_ip_network
        self._timeout_seconds = timeout_seconds

    async def resolve(self, host: str, port: int) -> tuple[str, ...] | DNSResolution:
        initial = await self._system_resolver.resolve(host, port)
        initial_addresses = initial.addresses if isinstance(initial, DNSResolution) else tuple(initial)
        try:
            parsed = tuple(ipaddress.ip_address(item) for item in initial_addresses)
        except ValueError as exc:
            raise BrokerUpstreamError("dns_result_invalid") from exc
        fake_flags = tuple(
            isinstance(address, ipaddress.IPv4Address) and address in self._fake_ip_network for address in parsed
        )
        if not any(fake_flags):
            return initial_addresses
        if not all(fake_flags):
            raise BrokerUpstreamError("mihomo_mixed_fake_ip_projection")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise BrokerUpstreamError("mihomo_fake_ip_literal_denied")

        socket_path = _validate_mihomo_socket(self._control_socket)
        addresses = await self._query_control(socket_path, host)
        if not addresses or len(addresses) > MAX_DNS_RESULTS:
            raise BrokerUpstreamError("mihomo_dns_result_invalid")
        return DNSResolution(addresses=addresses, provenance_rule_id="mihomo_fake_ip_compat_resolved")

    async def _query_control(self, socket_path: Path, host: str) -> tuple[str, ...]:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        connector = aiohttp.UnixConnector(path=str(socket_path), force_close=True, limit=1)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                timeout=timeout,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
            ) as session:
                version = await self._get_json(session, "/version")
                if (
                    not isinstance(version, dict)
                    or version.get("meta") is not True
                    or not isinstance(version.get("version"), str)
                    or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version["version"]) is None
                ):
                    raise BrokerUpstreamError("mihomo_identity_invalid")
                addresses: list[str] = []
                for record_type, numeric_type in (("A", 1), ("AAAA", 28)):
                    payload = await self._get_json(
                        session,
                        "/dns/query",
                        params={"name": host, "type": record_type},
                    )
                    for address in _parse_mihomo_dns_response(payload, host, numeric_type):
                        if address not in addresses:
                            addresses.append(address)
                return tuple(addresses)
        except BrokerError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, UnicodeError) as exc:
            raise BrokerUpstreamError("mihomo_control_query_failed") from exc

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        async with session.get(f"http://localhost{path}", params=params, allow_redirects=False) as response:
            if response.status != 200:
                raise BrokerUpstreamError("mihomo_control_query_failed")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_MIHOMO_RESPONSE_BYTES:
                        raise BrokerUpstreamError("mihomo_control_response_too_large")
                except ValueError as exc:
                    raise BrokerUpstreamError("mihomo_control_response_invalid") from exc
            body = await response.content.read(MAX_MIHOMO_RESPONSE_BYTES + 1)
            if len(body) > MAX_MIHOMO_RESPONSE_BYTES:
                raise BrokerUpstreamError("mihomo_control_response_too_large")
            try:
                return json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BrokerUpstreamError("mihomo_control_response_invalid") from exc


def _parse_mihomo_dns_response(payload: Any, host: str, numeric_type: int) -> tuple[str, ...]:
    expected_name = f"{host.rstrip('.').lower()}."
    if (
        not isinstance(payload, dict)
        or payload.get("Status") != 0
        or payload.get("TC") is not False
        or payload.get("Question") != [{"Name": expected_name, "Qtype": numeric_type, "Qclass": 1}]
    ):
        raise BrokerUpstreamError("mihomo_dns_response_invalid")
    answers = payload.get("Answer", [])
    if not isinstance(answers, list) or len(answers) > 64:
        raise BrokerUpstreamError("mihomo_dns_response_invalid")

    def normalized_name(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 255:
            raise BrokerUpstreamError("mihomo_dns_response_invalid")
        candidate = value.rstrip(".").lower()
        try:
            candidate = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise BrokerUpstreamError("mihomo_dns_response_invalid") from exc
        labels = candidate.split(".")
        if not labels or any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
            raise BrokerUpstreamError("mihomo_dns_response_invalid")
        return f"{candidate}."

    cname_targets: dict[str, str] = {}
    for answer in answers:
        if not isinstance(answer, dict):
            raise BrokerUpstreamError("mihomo_dns_response_invalid")
        name = normalized_name(answer.get("name"))
        if answer.get("type") == 5:
            target = normalized_name(answer.get("data"))
            existing = cname_targets.setdefault(name, target)
            if existing != target:
                raise BrokerUpstreamError("mihomo_dns_response_invalid")
    owned_names = {expected_name}
    for _ in range(len(cname_targets) + 1):
        additions = {target for name, target in cname_targets.items() if name in owned_names}
        if additions <= owned_names:
            break
        owned_names.update(additions)

    addresses: list[str] = []
    for answer in answers:
        if answer.get("type") != numeric_type:
            continue
        if normalized_name(answer.get("name")) not in owned_names:
            raise BrokerUpstreamError("mihomo_dns_response_invalid")
        value = answer.get("data")
        try:
            address = ipaddress.ip_address(value).compressed if isinstance(value, str) else ""
        except ValueError as exc:
            raise BrokerUpstreamError("mihomo_dns_response_invalid") from exc
        if not address or ipaddress.ip_address(address).version != (4 if numeric_type == 1 else 6):
            raise BrokerUpstreamError("mihomo_dns_response_invalid")
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, expected_host: str, addresses: Sequence[str]) -> None:
        self._expected_host = expected_host
        self._addresses = tuple(addresses)

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[aiohttp.abc.ResolveResult]:
        if _normalize_host(host) != self._expected_host:
            raise OSError("pinned_resolver_host_mismatch")
        results: list[aiohttp.abc.ResolveResult] = []
        for address in self._addresses:
            parsed = ipaddress.ip_address(address)
            address_family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            results.append(
                {
                    "hostname": host,
                    "host": parsed.compressed,
                    "port": port,
                    "family": address_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not results:
            raise OSError("pinned_resolver_no_compatible_address")
        return results

    async def close(self) -> None:
        return None


class _PeerCapturingConnector(aiohttp.TCPConnector):
    """Capture the connected peer before aiohttp detaches short responses."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.peer_ip: str | None = None

    async def _create_connection(self, req: Any, traces: list[Any], timeout: Any) -> Any:
        protocol = await super()._create_connection(req, traces, timeout)
        transport = protocol.transport
        peer = transport.get_extra_info("peername") if transport is not None else None
        if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
            raise BrokerUpstreamError("upstream_peer_unverified")
        try:
            self.peer_ip = ipaddress.ip_address(peer[0]).compressed
        except ValueError as exc:
            raise BrokerUpstreamError("upstream_peer_unverified") from exc
        return protocol


class AiohttpPinnedTransport:
    """HTTP transport with proxy discovery off, redirects off, and DNS answers pinned."""

    async def request(
        self,
        *,
        method: str,
        target: ParsedTarget,
        headers: Sequence[tuple[str, str]],
        body: bytes | None,
        resolved_ips: Sequence[str],
        limits: BrokerLimits,
    ) -> TransportResponse:
        resolver = _PinnedResolver(target.host, resolved_ips)
        connector = _PeerCapturingConnector(
            resolver=resolver,
            use_dns_cache=False,
            ttl_dns_cache=0,
            force_close=True,
            limit=1,
        )
        timeout = aiohttp.ClientTimeout(
            total=limits.total_timeout_seconds,
            connect=limits.connect_timeout_seconds,
            sock_connect=limits.connect_timeout_seconds,
            sock_read=limits.total_timeout_seconds,
        )
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                timeout=timeout,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                skip_auto_headers={"Accept-Encoding", "User-Agent"},
                max_line_size=limits.response_header_bytes,
                max_field_size=limits.response_header_bytes,
            ) as session:
                async with session.request(
                    method,
                    target.url,
                    headers=list(headers),
                    data=body,
                    allow_redirects=False,
                ) as response:
                    peer_ip = connector.peer_ip
                    if peer_ip is None:
                        raise BrokerUpstreamError("upstream_peer_unverified")
                    raw_headers = tuple(
                        (name.decode("ascii", "strict"), value.decode("latin-1", "strict"))
                        for name, value in response.raw_headers
                    )
                    _validate_response_headers(raw_headers, limits)
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared_size = int(content_length)
                        except ValueError as exc:
                            raise BrokerUpstreamError("response_content_length_invalid") from exc
                        if declared_size < 0 or declared_size > limits.response_body_bytes:
                            raise BrokerUpstreamError("response_body_too_large", http_status=502)
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        content.extend(chunk)
                        if len(content) > limits.response_body_bytes:
                            raise BrokerUpstreamError("response_body_too_large", http_status=502)
                    return TransportResponse(
                        status=response.status,
                        headers=raw_headers,
                        body=bytes(content),
                        peer_ip=peer_ip,
                    )
        except BrokerError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, UnicodeError) as exc:
            raise BrokerUpstreamError("upstream_request_failed") from exc


class SecurityAuditSink:
    """Append only a hashed host projection and stable decision metadata."""

    def __init__(
        self,
        *,
        project_root: Path,
        context: security_audit.SecurityRunContext,
        sync: bool = True,
    ) -> None:
        context.validate()
        self._project_root = project_root
        self._context = context
        self._sync = sync

    async def record(
        self,
        decision: egress_decision.EgressDecision,
        *,
        error_code: str,
        duration_ms: int,
    ) -> None:
        host = decision.host
        target = security_audit.project_target(
            kind="host",
            scope=f"egress.{decision.rule_id}",
            value=f"{host['scheme']}:{host['hostname']}:{host['port']}",
        )
        identity = broker_request_identity.current_request_identity()
        context = (
            self._context
            if identity is None
            else security_audit.SecurityRunContext(
                profile=identity.profile,
                sandbox_id=identity.sandbox_id,
                run_id=identity.run_id,
                session_id=identity.session_id,
                policy_digest=identity.policy_digest,
            )
        )
        record = security_audit.build_record(
            context=context,
            operation_class="network.request",
            target=target,
            decision=decision.decision,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        await asyncio.to_thread(
            security_audit.append_record,
            project_root=self._project_root,
            record=record,
            sync=self._sync,
        )


class EgressGuard:
    def __init__(
        self,
        *,
        allowlist: egress_decision.Allowlist,
        resolver: DNSResolver,
        transport: OutboundTransport,
        audit_sink: AuditSink,
        limits: BrokerLimits | None = None,
    ) -> None:
        self.allowlist = allowlist
        self.limits = limits or BrokerLimits(request_body_bytes=allowlist.unknown_json_post_max_bytes)
        self.limits.validate()
        if self.limits.request_body_bytes > allowlist.unknown_json_post_max_bytes:
            raise BrokerInputError("broker_body_limit_exceeds_policy")
        self._resolver = resolver
        self._transport = transport
        self._audit_sink = audit_sink
        self._semaphore = asyncio.Semaphore(self.limits.max_concurrency)
        self._rules_by_id = {rule.rule_id: rule for rule in allowlist.rules}

    async def fetch(self, envelope: Any) -> BrokerResponse:
        request = parse_envelope(envelope, limits=self.limits)
        try:
            async with asyncio.timeout(self.limits.queue_timeout_seconds):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            raise BrokerBusyError() from exc
        try:
            try:
                async with asyncio.timeout(self.limits.total_timeout_seconds):
                    return await self._fetch_locked(request)
            except TimeoutError as exc:
                raise BrokerUpstreamError("broker_request_timeout", http_status=504) from exc
        finally:
            self._semaphore.release()

    async def record_input_denial(
        self,
        envelope: Any,
        *,
        error_code: str,
        started: float,
    ) -> None:
        """Audit a verified request rejected before DNS or transport."""

        target: ParsedTarget | None = None
        if isinstance(envelope, dict):
            try:
                target = parse_target_url(envelope.get("url"))
            except BrokerInputError:
                target = None
        if target is None:
            target = ParsedTarget(
                url="https://unavailable.invalid:443/",
                scheme="https",
                host="unavailable.invalid",
                port=443,
            )
        decision = _manual_decision(target, error_code, "deny")
        await self._record_or_fail(decision, started=started, error_code=error_code)

    async def _fetch_locked(self, initial: BrokerRequest) -> BrokerResponse:
        current = initial
        first_origin = initial.target.origin
        seen_urls = {initial.target.url}
        effective_decision: egress_decision.EgressDecision | None = None

        for hop in range(self.limits.max_redirects + 1):
            started = time.monotonic()
            hostname_denial = _hostname_only_denial(current.target)
            if hostname_denial is not None:
                await self._record_or_fail(
                    hostname_denial,
                    started=started,
                    error_code=hostname_denial.rule_id,
                )
                raise BrokerDenied(hostname_denial)
            try:
                resolution = await self._resolver.resolve(current.target.host, current.target.port)
            except BrokerError as exc:
                decision = _manual_decision(current.target, exc.code, "deny")
                await self._record_or_fail(decision, started=started, error_code=exc.code)
                raise BrokerDenied(decision) from exc
            if isinstance(resolution, DNSResolution):
                addresses = resolution.addresses
                if resolution.provenance_rule_id:
                    provenance = _manual_decision(
                        current.target,
                        resolution.provenance_rule_id,
                        "audit_only",
                    )
                    await self._record_or_fail(provenance, started=started, error_code="")
            else:
                addresses = resolution
            try:
                projection = self._project(current, addresses)
            except BrokerError as exc:
                decision = _manual_decision(current.target, exc.code, "deny")
                await self._record_or_fail(decision, started=started, error_code=exc.code)
                raise BrokerDenied(decision) from exc
            decision = self._broker_decision(projection)
            await self._record_or_fail(
                decision,
                started=started,
                error_code=decision.rule_id if decision.decision == "deny" else "",
            )
            if decision.decision == "deny":
                raise BrokerDenied(decision)
            if effective_decision is None or decision.decision == "audit_only":
                effective_decision = decision

            rule = self._rules_by_id.get(decision.rule_id)
            unknown_destination = rule is None
            strip_credentials = unknown_destination or current.target.origin != first_origin
            outbound_headers = _sanitize_request_headers(
                current.headers,
                limits=self.limits,
                strip_credentials=strip_credentials,
            )
            try:
                response = await self._transport.request(
                    method=current.method,
                    target=current.target,
                    headers=outbound_headers,
                    body=current.body,
                    resolved_ips=tuple(address.compressed for address in projection.resolved_ips),
                    limits=self.limits,
                )
                self._validate_transport_response(response, projection)
                response_headers = _sanitize_response_headers(response.headers, self.limits)
            except BrokerError as exc:
                denied = _manual_decision(current.target, exc.code, "deny")
                await self._record_or_fail(denied, started=time.monotonic(), error_code=exc.code)
                raise
            if response.status not in REDIRECT_STATUSES:
                if effective_decision is None:
                    raise AssertionError("forwarded request has no decision")
                return BrokerResponse(
                    status=response.status,
                    headers=response_headers,
                    body=response.body,
                    decision=effective_decision,
                    redirect_hops=hop,
                )

            try:
                location = _single_header(response.headers, "location")
            except BrokerError as exc:
                denied = _manual_decision(current.target, exc.code, "deny")
                await self._record_or_fail(denied, started=time.monotonic(), error_code=exc.code)
                raise BrokerDenied(denied) from exc
            if location is None:
                if effective_decision is None:
                    raise AssertionError("forwarded request has no decision")
                return BrokerResponse(
                    status=response.status,
                    headers=response_headers,
                    body=response.body,
                    decision=effective_decision,
                    redirect_hops=hop,
                )
            if hop >= self.limits.max_redirects:
                denied = _manual_decision(current.target, "redirect_limit_exceeded", "deny")
                await self._record_or_fail(denied, started=time.monotonic(), error_code=denied.rule_id)
                raise BrokerDenied(denied)
            try:
                next_target = _parse_redirect_target(current.target.url, location)
            except BrokerError as exc:
                denied = _manual_decision(current.target, exc.code, "deny")
                await self._record_or_fail(denied, started=time.monotonic(), error_code=exc.code)
                raise BrokerDenied(denied) from exc
            if next_target.url in seen_urls:
                denied = _manual_decision(next_target, "redirect_loop_denied", "deny")
                await self._record_or_fail(denied, started=time.monotonic(), error_code=denied.rule_id)
                raise BrokerDenied(denied)
            seen_urls.add(next_target.url)
            current = _redirect_request(current, next_target, response.status)

        raise AssertionError("redirect loop exited without a result")

    def _project(
        self,
        request: BrokerRequest,
        addresses: Sequence[str],
    ) -> egress_decision.RequestProjection:
        try:
            return egress_decision.project_request(
                {
                    "scheme": request.target.scheme,
                    "host": request.target.host,
                    "port": request.target.port,
                    "method": request.method,
                    "content_type": request.content_type,
                    "body_bytes": len(request.body) if request.body is not None else 0,
                    "resolved_ips": list(addresses),
                    "client": "siq_fetch",
                }
            )
        except egress_decision.EgressConfigurationError as exc:
            raise BrokerUpstreamError("request_projection_invalid") from exc

    def _broker_decision(
        self,
        request: egress_decision.RequestProjection,
    ) -> egress_decision.EgressDecision:
        decision = egress_decision.decide(request, self.allowlist)
        if request.content_type == "multipart/form-data":
            return egress_decision.EgressDecision(
                rule_id="broker_multipart_denied",
                decision="deny",
                host=request.host_projection,
            )
        if request.content_type == "application/octet-stream":
            return egress_decision.EgressDecision(
                rule_id="broker_octet_stream_denied",
                decision="deny",
                host=request.host_projection,
            )
        if request.method not in BROKER_METHODS:
            return egress_decision.EgressDecision(
                rule_id="broker_method_denied",
                decision="deny",
                host=request.host_projection,
            )
        rule = self._rules_by_id.get(decision.rule_id)
        if decision.decision == "allow" and rule is not None and rule.category in PROVIDER_DIRECT_CATEGORIES:
            return egress_decision.EgressDecision(
                rule_id="provider_direct_required",
                decision="deny",
                host=request.host_projection,
            )
        return decision

    async def _record_or_fail(
        self,
        decision: egress_decision.EgressDecision,
        *,
        started: float,
        error_code: str,
    ) -> None:
        duration_ms = max(0, min(int((time.monotonic() - started) * 1000), 86_400_000))
        try:
            await self._audit_sink.record(decision, error_code=error_code, duration_ms=duration_ms)
        except Exception as exc:
            raise BrokerUpstreamError("security_audit_failed", http_status=503) from exc

    def _validate_transport_response(
        self,
        response: TransportResponse,
        projection: egress_decision.RequestProjection,
    ) -> None:
        if (
            isinstance(response.status, bool)
            or not isinstance(response.status, int)
            or not 100 <= response.status <= 599
        ):
            raise BrokerUpstreamError("upstream_status_invalid")
        if len(response.body) > self.limits.response_body_bytes:
            raise BrokerUpstreamError("response_body_too_large")
        try:
            peer = ipaddress.ip_address(response.peer_ip)
        except ValueError as exc:
            raise BrokerUpstreamError("upstream_peer_unverified") from exc
        allowed = set(projection.resolved_ips)
        if peer not in allowed:
            raise BrokerUpstreamError("upstream_peer_mismatch")


def _normalize_host(value: str) -> str:
    if not value or value != value.strip() or any(character in value for character in "/\\@?#\x00"):
        raise BrokerInputError("target_host_invalid")
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    candidate = candidate.rstrip(".").lower()
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass
    if ":" in candidate:
        raise BrokerInputError("target_host_invalid")
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BrokerInputError("target_host_invalid") from exc
    labels = candidate.split(".")
    if len(candidate) > 253 or len(labels) < 2 or any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise BrokerInputError("target_host_invalid")
    return candidate


def parse_target_url(value: Any) -> ParsedTarget:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_URL_BYTES
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise BrokerInputError("target_url_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise BrokerInputError("target_url_invalid") from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BrokerInputError("target_url_invalid")
    if parsed.fragment:
        raise BrokerInputError("target_url_fragment_forbidden")
    host = _normalize_host(parsed.hostname or "")
    port = port or (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise BrokerInputError("target_url_invalid")
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}"
    path = parsed.path or "/"
    normalized = urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))
    return ParsedTarget(url=normalized, scheme=scheme, host=host, port=port)


def _normalize_headers(value: Any, *, limits: BrokerLimits) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > limits.header_count:
        raise BrokerInputError("request_headers_invalid")
    result: list[tuple[str, str]] = []
    names: set[str] = set()
    size = 0
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not HEADER_NAME_RE.fullmatch(raw_name) or not isinstance(raw_value, str):
            raise BrokerInputError("request_headers_invalid")
        name = raw_name.lower()
        if name in names or any(
            ord(character) < 32 and character != "\t" or ord(character) == 127 for character in raw_value
        ):
            raise BrokerInputError("request_headers_invalid")
        if len(raw_value) > limits.request_header_bytes:
            raise BrokerInputError("request_headers_invalid")
        size += len(name.encode("ascii")) + len(raw_value.encode("utf-8")) + 4
        if size > limits.request_header_bytes:
            raise BrokerInputError("request_headers_too_large", http_status=413)
        names.add(name)
        result.append((name, raw_value))
    return tuple(result)


def parse_envelope(value: Any, *, limits: BrokerLimits) -> BrokerRequest:
    if (
        not isinstance(value, dict)
        or not REQUIRED_ENVELOPE_KEYS.issubset(value)
        or not set(value).issubset(ENVELOPE_KEYS)
    ):
        raise BrokerInputError("request_envelope_invalid")
    method = value.get("method")
    if not isinstance(method, str) or method != method.upper() or not re.fullmatch(r"[A-Z]{3,16}", method):
        raise BrokerInputError("request_method_invalid")
    target = parse_target_url(value.get("url"))
    headers = _normalize_headers(value.get("headers"), limits=limits)
    header_map = dict(headers)
    json_body = value.get("json_body", _MISSING)
    if method in {"GET", "HEAD"} and json_body is not _MISSING:
        raise BrokerInputError("read_method_body_forbidden")
    if method == "POST" and json_body is _MISSING:
        raise BrokerInputError("json_body_required")
    body: bytes | None = None
    content_type = header_map.get("content-type")
    if json_body is not _MISSING:
        try:
            body = json.dumps(
                json_body,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise BrokerInputError("json_body_invalid") from exc
        if len(body) > limits.request_body_bytes:
            raise BrokerInputError("json_body_too_large", http_status=413)
        if content_type is None:
            content_type = "application/json"
            headers = (*headers, ("content-type", content_type))
    return BrokerRequest(
        method=method,
        target=target,
        body=body,
        content_type=content_type,
        headers=headers,
    )


def _sanitize_request_headers(
    headers: Sequence[tuple[str, str]],
    *,
    limits: BrokerLimits,
    strip_credentials: bool,
) -> tuple[tuple[str, str], ...]:
    connection_tokens: set[str] = set()
    for name, value in headers:
        if name.lower() == "connection":
            connection_tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    stripped = HOP_BY_HOP_HEADERS | {"host", "content-length"} | connection_tokens
    if strip_credentials:
        stripped |= UNKNOWN_STRIPPED_HEADERS
    result = tuple((name, value) for name, value in headers if name.lower() not in stripped)
    if len(result) > limits.header_count:
        raise BrokerInputError("request_headers_invalid")
    return result


def _validate_response_headers(headers: Sequence[tuple[str, str]], limits: BrokerLimits) -> None:
    if len(headers) > limits.header_count:
        raise BrokerUpstreamError("response_headers_too_large")
    total = 0
    for name, value in headers:
        if not HEADER_NAME_RE.fullmatch(name) or any(
            ord(character) < 32 and character != "\t" or ord(character) == 127 for character in value
        ):
            raise BrokerUpstreamError("response_headers_invalid")
        try:
            encoded_value = value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise BrokerUpstreamError("response_headers_invalid") from exc
        total += len(name.encode("ascii")) + len(encoded_value) + 4
        if total > limits.response_header_bytes:
            raise BrokerUpstreamError("response_headers_too_large")


def _sanitize_response_headers(
    headers: Sequence[tuple[str, str]],
    limits: BrokerLimits,
) -> tuple[tuple[str, str], ...]:
    _validate_response_headers(headers, limits)
    connection_tokens: set[str] = set()
    for name, value in headers:
        if name.lower() == "connection":
            connection_tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    stripped = RESPONSE_STRIPPED_HEADERS | connection_tokens
    return tuple((name, value) for name, value in headers if name.lower() not in stripped)


def _single_header(headers: Sequence[tuple[str, str]], requested: str) -> str | None:
    values = [value for name, value in headers if name.lower() == requested]
    if not values:
        return None
    if len(values) != 1 or not values[0] or len(values[0].encode("utf-8")) > MAX_URL_BYTES:
        raise BrokerUpstreamError("redirect_location_invalid")
    return values[0]


def _parse_redirect_target(base_url: str, location: str) -> ParsedTarget:
    try:
        return parse_target_url(urljoin(base_url, location))
    except BrokerInputError as exc:
        raise BrokerUpstreamError("redirect_target_invalid") from exc


def _redirect_request(current: BrokerRequest, target: ParsedTarget, status: int) -> BrokerRequest:
    method = current.method
    body = current.body
    content_type = current.content_type
    headers = current.headers
    if status == 303 and method != "HEAD" or status in {301, 302} and method == "POST":
        method = "GET"
        body = None
        content_type = None
        headers = tuple((name, value) for name, value in headers if name.lower() != "content-type")
    return BrokerRequest(method=method, target=target, body=body, content_type=content_type, headers=headers)


def _manual_decision(target: ParsedTarget, rule_id: str, decision: str) -> egress_decision.EgressDecision:
    return egress_decision.EgressDecision(
        rule_id=rule_id,
        decision=decision,
        host={"scheme": target.scheme, "hostname": target.host, "port": target.port},
    )


def _hostname_only_denial(target: ParsedTarget) -> egress_decision.EgressDecision | None:
    if target.host in egress_decision.METADATA_HOSTS:
        return _manual_decision(target, "ssrf_metadata_host", "deny")
    if target.host == "localhost" or target.host.endswith(egress_decision.INTERNAL_HOST_SUFFIXES):
        return _manual_decision(target, "ssrf_internal_hostname", "deny")
    return None


def _response_peer_ip(response: aiohttp.ClientResponse) -> str:
    connection = response.connection
    transport = connection.transport if connection is not None else None
    if transport is None:
        protocol = getattr(response, "_protocol", None)
        transport = getattr(protocol, "transport", None)
    peer = transport.get_extra_info("peername") if transport is not None else None
    if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
        raise BrokerUpstreamError("upstream_peer_unverified")
    try:
        return ipaddress.ip_address(peer[0]).compressed
    except ValueError as exc:
        raise BrokerUpstreamError("upstream_peer_unverified") from exc


def validate_binding(
    bind_host: str,
    bridge: bridge_endpoint.BridgeEndpoint | None = None,
) -> tuple[str, frozenset[str]]:
    normalized = bind_host.strip().lower().rstrip(".")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise BrokerInputError("bind_host_invalid") from exc
    if address.is_unspecified or address.is_multicast or address.is_link_local or address.is_global:
        raise BrokerInputError("bind_host_invalid")
    if address.is_loopback:
        hosts = {address.compressed, "localhost"}
        return address.compressed, frozenset(hosts)
    if bridge is None:
        raise BrokerInputError("private_bind_requires_verified_bridge")
    try:
        bridge.validate()
    except bridge_endpoint.BridgeEndpointError as exc:
        raise BrokerInputError("verified_bridge_invalid") from exc
    if address.compressed != bridge.gateway_ip:
        raise BrokerInputError("bind_host_bridge_mismatch")
    return address.compressed, frozenset({address.compressed, bridge.host_alias})


def _request_host(request: web.Request) -> str | None:
    raw = request.headers.get("Host")
    if not raw or any(character in raw for character in "\r\n\x00/\\@?#"):
        return None
    try:
        return (urlsplit(f"//{raw}").hostname or "").lower().rstrip(".")
    except ValueError:
        return None


def create_app(
    guard: EgressGuard,
    *,
    allowed_ingress_hosts: frozenset[str],
    identity_key: bytes | None = None,
    require_identity: bool = False,
    runtime_binding: Mapping[str, str] | None = None,
) -> web.Application:
    if require_identity and identity_key is None:
        raise BrokerInputError("broker_identity_key_missing", http_status=503)
    binding = dict(runtime_binding or {})
    if set(binding) != {"allowlist_contract_sha256", "source_bundle_sha256"} or any(
        not re.fullmatch(r"[0-9a-f]{64}", value) for value in binding.values()
    ):
        raise BrokerInputError("runtime_source_binding_invalid", http_status=503)

    @web.middleware
    async def validate_host(request: web.Request, handler: Any) -> web.StreamResponse:
        if _request_host(request) not in allowed_ingress_hosts:
            return web.json_response({"ok": False, "error_code": "ingress_host_denied"}, status=403)
        return await handler(request)

    app = web.Application(middlewares=[validate_host], client_max_size=MAX_REQUEST_ENVELOPE_BYTES)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "service": "siq-egress-guard",
                "dns_resolver_mode": getattr(guard._resolver, "mode", "system"),
                **binding,
            }
        )

    async def broker_request(request: web.Request) -> web.Response:
        identity: broker_request_identity.RequestIdentity | None = None
        if identity_key is not None:
            try:
                values = request.headers.getall(broker_request_identity.HEADER_NAME, [])
                identity = broker_request_identity.verify_header_values(
                    values,
                    identity_key,
                    expected_audience=broker_request_identity.EGRESS_AUDIENCE,
                )
            except broker_request_identity.IdentityError as exc:
                status = 401 if str(exc) == "broker_identity_header_required" else 403
                code = "broker_identity_required" if status == 401 else "broker_identity_invalid"
                return web.json_response({"ok": False, "error_code": code}, status=status)
        elif require_identity:
            return web.json_response({"ok": False, "error_code": "broker_identity_required"}, status=401)
        if request.content_type != "application/json":
            return web.json_response({"ok": False, "error_code": "content_type_invalid"}, status=415)
        started = time.monotonic()
        payload: Any = None
        try:
            payload = await request.json(loads=json.loads)
            if identity is None:
                result = await guard.fetch(payload)
            else:
                with broker_request_identity.request_identity_context(identity):
                    result = await guard.fetch(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"ok": False, "error_code": "request_json_invalid"}, status=400)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"ok": False, "error_code": "request_too_large"}, status=413)
        except BrokerInputError as exc:
            try:
                if identity is None:
                    await guard.record_input_denial(payload, error_code=exc.code, started=started)
                else:
                    with broker_request_identity.request_identity_context(identity):
                        await guard.record_input_denial(payload, error_code=exc.code, started=started)
            except BrokerError as audit_exc:
                return web.json_response(
                    {"ok": False, "error_code": audit_exc.code},
                    status=audit_exc.http_status,
                )
            return web.json_response({"ok": False, "error_code": exc.code}, status=exc.http_status)
        except BrokerDenied as exc:
            return web.json_response(
                {"ok": False, "error_code": exc.code, "egress": exc.decision.as_dict()},
                status=exc.http_status,
            )
        except BrokerError as exc:
            return web.json_response({"ok": False, "error_code": exc.code}, status=exc.http_status)
        return web.json_response(result.as_api_dict())

    app.router.add_get("/health", health)
    app.router.add_post("/v1/request", broker_request)
    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-host", default=DEFAULT_BIND_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--bridge-bind",
        action="store_true",
        help=f"Bind to the verified {bridge_endpoint.NETWORK_NAME} Docker gateway",
    )
    parser.add_argument("--allowlist", type=Path, default=egress_decision.DEFAULT_ALLOWLIST)
    parser.add_argument("--project-root", type=Path, default=egress_decision.REPO_ROOT)
    parser.add_argument("--profile", default=os.environ.get("SIQ_OPENSHELL_AUDIT_PROFILE", "siq_analysis"))
    parser.add_argument(
        "--sandbox-id",
        default=os.environ.get("SIQ_OPENSHELL_AUDIT_SANDBOX_ID", "host-egress-broker"),
    )
    parser.add_argument("--run-id", default="host-egress-broker")
    parser.add_argument(
        "--session-id",
        default=os.environ.get("SIQ_OPENSHELL_AUDIT_SESSION_ID", "egress-broker"),
    )
    parser.add_argument("--policy-digest", default=os.environ.get("SIQ_OPENSHELL_AUDIT_POLICY_DIGEST", ""))
    parser.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not 1 <= args.port <= 65535:
            raise BrokerInputError("bind_port_invalid")
        if args.bridge_bind:
            if args.bind_host != DEFAULT_BIND_HOST:
                raise BrokerInputError("bind_mode_conflict")
            verified_bridge = bridge_endpoint.discover_bridge_endpoint()
            bind_host, ingress_hosts = validate_binding(verified_bridge.gateway_ip, verified_bridge)
        else:
            bind_host, ingress_hosts = validate_binding(args.bind_host)
        allowlist = egress_decision.load_allowlist(args.allowlist)
        policy_digest = args.policy_digest or hashlib.sha256(egress_decision.SCHEMA_VERSION.encode()).hexdigest()
        limits = BrokerLimits(
            request_body_bytes=allowlist.unknown_json_post_max_bytes,
            max_concurrency=args.max_concurrency,
        )
        context = security_audit.SecurityRunContext(
            profile=args.profile,
            sandbox_id=args.sandbox_id,
            run_id=args.run_id,
            session_id=args.session_id,
            policy_digest=policy_digest,
        )
        compat_value = str(os.environ.get(MIHOMO_COMPAT_ENV) or "").strip().lower()
        if compat_value not in {"", "0", "1", "false", "no", "off", "on", "true", "yes"}:
            raise BrokerInputError("mihomo_fake_ip_compat_invalid")
        resolver: DNSResolver = SystemDNSResolver()
        if compat_value in {"1", "on", "true", "yes"}:
            socket_value = str(os.environ.get(MIHOMO_SOCKET_ENV) or "").strip()
            range_value = str(os.environ.get(MIHOMO_RANGE_ENV) or "").strip()
            if not socket_value or not range_value:
                raise BrokerInputError("mihomo_fake_ip_config_missing")
            resolver = MihomoFakeIPDNSResolver(
                system_resolver=resolver,
                control_socket=Path(socket_value),
                fake_ip_network=parse_mihomo_fake_ip_network(range_value),
            )
        guard = EgressGuard(
            allowlist=allowlist,
            resolver=resolver,
            transport=AiohttpPinnedTransport(),
            audit_sink=SecurityAuditSink(project_root=args.project_root, context=context),
            limits=limits,
        )
        require_identity = str(os.environ.get("SIQ_OPENSHELL_REQUIRE_REQUEST_IDENTITY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        identity_key: bytes | None = None
        identity_key_path = str(os.environ.get("SIQ_OPENSHELL_BROKER_IDENTITY_KEY_FILE") or "").strip()
        if require_identity:
            if not identity_key_path:
                identity_key_path = str(args.project_root / "var/openshell/secrets/broker-request-identity.key")
            identity_key = broker_request_identity.read_key_file(Path(identity_key_path))
        web.run_app(
            create_app(
                guard,
                allowed_ingress_hosts=ingress_hosts,
                identity_key=identity_key,
                require_identity=require_identity,
                runtime_binding={
                    "allowlist_contract_sha256": allowlist_contract_sha256(allowlist),
                    "source_bundle_sha256": runtime_source_bundle_sha256(),
                },
            ),
            host=bind_host,
            port=args.port,
            access_log=None,
            print=None,
        )
        return 0
    except (
        BrokerError,
        bridge_endpoint.BridgeEndpointError,
        egress_decision.EgressConfigurationError,
        broker_request_identity.IdentityError,
        security_audit.SecurityAuditError,
    ) as exc:
        print(f"egress guard failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
