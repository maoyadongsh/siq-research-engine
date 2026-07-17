#!/usr/bin/env python3
"""Pure SIQ egress decision engine; this module does not enforce or proxy network traffic."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "siq.openshell.egress_allowlist.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = REPO_ROOT / "infra/openshell/egress/allowlist.json"
MAX_ALLOWLIST_BYTES = 64 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_APPROVED_BODY_BYTES = 1024 * 1024 * 1024
MAX_REDIRECT_HOPS = 20
DECISIONS = {"allow", "audit_only", "deny"}
RULE_CATEGORIES = {"github", "lark", "model", "search"}
HTTP_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
HTTP_SCHEMES = {"http", "https"}
READ_METHODS = {"GET", "HEAD"}
BLOCKED_TRANSFER_CLIENTS = {
    "ftp",
    "lftp",
    "nc",
    "ncat",
    "netcat",
    "rclone",
    "rsync",
    "scp",
    "sftp",
    "socat",
    "ssh",
    "telnet",
}
BLOCKED_TRANSFER_SCHEMES = {"ftp", "ftps", "rsync", "scp", "sftp", "ssh", "telnet"}
METADATA_HOSTS = {
    "instance-data.ec2.internal",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
}
INTERNAL_HOST_SUFFIXES = (".internal", ".local", ".localhost", ".home.arpa")
PUBLIC_SUFFIX_LIKE = {
    "ac.uk",
    "co.jp",
    "co.uk",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.sg",
    "net.au",
    "org.au",
    "org.uk",
}
PUBLIC_SUFFIX_SECOND_LEVEL_LABELS = {"ac", "co", "com", "edu", "gov", "net", "org"}
RULE_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,63}")
SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]{0,31}")
CONTENT_TYPE_RE = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+")
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
AMBIGUOUS_NUMERIC_HOST_RE = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.IGNORECASE)
ALLOWLIST_KEYS = {"schema_version", "unknown_json_post_max_bytes", "rules"}
RULE_KEYS = {
    "rule_id",
    "category",
    "host_patterns",
    "schemes",
    "ports",
    "methods",
    "content_types",
    "max_body_bytes",
}
REQUEST_KEYS = {
    "scheme",
    "host",
    "port",
    "method",
    "content_type",
    "body_bytes",
    "resolved_ips",
    "client",
}


class EgressConfigurationError(RuntimeError):
    """Stable configuration/input error that never includes supplied values."""


@dataclass(frozen=True)
class AllowRule:
    rule_id: str
    category: str
    host_patterns: tuple[str, ...]
    schemes: frozenset[str]
    ports: frozenset[int]
    methods: frozenset[str]
    content_types: frozenset[str]
    max_body_bytes: int


@dataclass(frozen=True)
class Allowlist:
    unknown_json_post_max_bytes: int
    rules: tuple[AllowRule, ...]


@dataclass(frozen=True)
class RequestProjection:
    scheme: str
    host: str
    port: int
    method: str
    content_type: str | None
    body_bytes: int | None
    resolved_ips: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
    client: str

    @property
    def host_projection(self) -> dict[str, Any]:
        return {"scheme": self.scheme, "hostname": self.host, "port": self.port}


@dataclass(frozen=True)
class EgressDecision:
    rule_id: str
    decision: str
    host: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "decision": self.decision, "host": dict(self.host)}


def _unique_string_list(value: Any, *, code: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise EgressConfigurationError(code)
    if len(value) != len(set(value)):
        raise EgressConfigurationError(code)
    return value


def _unique_int_list(value: Any, *, code: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 65535 for item in value)
        or len(value) != len(set(value))
    ):
        raise EgressConfigurationError(code)
    return value


def _safe_regular_file(path: Path, *, max_bytes: int, code: str) -> Path:
    candidate = path.expanduser()
    if ".." in candidate.parts:
        raise EgressConfigurationError(code)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise EgressConfigurationError(code) from exc
        if stat.S_ISLNK(mode):
            raise EgressConfigurationError(code)
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise EgressConfigurationError(code)
    return candidate


def _normalize_dns_host(value: str, *, code: str) -> str:
    if not value or value != value.strip() or any(character in value for character in "/\\@?#:\x00"):
        raise EgressConfigurationError(code)
    candidate = value.rstrip(".").lower()
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EgressConfigurationError(code) from exc
    labels = candidate.split(".")
    if len(candidate) > 253 or len(labels) < 2 or any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise EgressConfigurationError(code)
    return candidate


def _normalize_host_pattern(value: str) -> str:
    if "*" not in value:
        host = _normalize_dns_host(value, code="allowlist_host_pattern_invalid")
        if host in METADATA_HOSTS or host.endswith(INTERNAL_HOST_SUFFIXES):
            raise EgressConfigurationError("allowlist_internal_host_forbidden")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return host
        raise EgressConfigurationError("allowlist_ip_literal_forbidden")
    if not value.startswith("*.") or value.count("*") != 1:
        raise EgressConfigurationError("allowlist_host_pattern_invalid")
    suffix = _normalize_dns_host(value[2:], code="allowlist_host_pattern_invalid")
    suffix_labels = suffix.split(".")
    country_public_suffix = (
        len(suffix_labels) == 2
        and len(suffix_labels[-1]) == 2
        and suffix_labels[0] in PUBLIC_SUFFIX_SECOND_LEVEL_LABELS
    )
    if (
        suffix.count(".") < 1
        or suffix in PUBLIC_SUFFIX_LIKE
        or country_public_suffix
        or suffix.endswith(INTERNAL_HOST_SUFFIXES)
    ):
        raise EgressConfigurationError("allowlist_tld_wildcard_forbidden")
    return f"*.{suffix}"


def _host_matches(pattern: str, host: str) -> bool:
    if not pattern.startswith("*."):
        return host == pattern
    suffix_labels = pattern[2:].split(".")
    host_labels = host.split(".")
    return len(host_labels) == len(suffix_labels) + 1 and host_labels[1:] == suffix_labels


def _patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith("*.") and not right.startswith("*."):
        return _host_matches(left, right)
    if right.startswith("*.") and not left.startswith("*."):
        return _host_matches(right, left)
    return False


def parse_allowlist(payload: Any) -> Allowlist:
    if not isinstance(payload, dict) or set(payload) != ALLOWLIST_KEYS:
        raise EgressConfigurationError("allowlist_schema_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EgressConfigurationError("allowlist_schema_version_invalid")
    threshold = payload.get("unknown_json_post_max_bytes")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= 1024 * 1024:
        raise EgressConfigurationError("allowlist_unknown_json_threshold_invalid")
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise EgressConfigurationError("allowlist_rules_invalid")

    rules: list[AllowRule] = []
    rule_ids: set[str] = set()
    all_patterns: list[str] = []
    for raw in raw_rules:
        if not isinstance(raw, dict) or set(raw) != RULE_KEYS:
            raise EgressConfigurationError("allowlist_rule_schema_invalid")
        rule_id = raw.get("rule_id")
        category = raw.get("category")
        if not isinstance(rule_id, str) or not RULE_ID_RE.fullmatch(rule_id) or rule_id in rule_ids:
            raise EgressConfigurationError("allowlist_rule_id_invalid")
        if category not in RULE_CATEGORIES:
            raise EgressConfigurationError("allowlist_rule_category_invalid")
        patterns = tuple(
            _normalize_host_pattern(item)
            for item in _unique_string_list(raw.get("host_patterns"), code="allowlist_host_patterns_invalid")
        )
        if len(patterns) != len(set(patterns)):
            raise EgressConfigurationError("allowlist_host_patterns_invalid")
        if any(
            _patterns_overlap(left, right) for index, left in enumerate(patterns) for right in patterns[index + 1 :]
        ):
            raise EgressConfigurationError("allowlist_host_pattern_overlap")
        if any(_patterns_overlap(pattern, existing) for pattern in patterns for existing in all_patterns):
            raise EgressConfigurationError("allowlist_host_pattern_overlap")
        schemes = _unique_string_list(raw.get("schemes"), code="allowlist_schemes_invalid")
        if any(not SCHEME_RE.fullmatch(item) or item != "https" for item in schemes):
            raise EgressConfigurationError("allowlist_schemes_invalid")
        ports = _unique_int_list(raw.get("ports"), code="allowlist_ports_invalid")
        methods = _unique_string_list(raw.get("methods"), code="allowlist_methods_invalid")
        if any(item not in HTTP_METHODS for item in methods):
            raise EgressConfigurationError("allowlist_methods_invalid")
        content_types = _unique_string_list(raw.get("content_types"), code="allowlist_content_types_invalid")
        if any(item != item.lower() or not CONTENT_TYPE_RE.fullmatch(item) for item in content_types):
            raise EgressConfigurationError("allowlist_content_types_invalid")
        max_body_bytes = raw.get("max_body_bytes")
        if (
            isinstance(max_body_bytes, bool)
            or not isinstance(max_body_bytes, int)
            or not 0 <= max_body_bytes <= MAX_APPROVED_BODY_BYTES
        ):
            raise EgressConfigurationError("allowlist_max_body_invalid")
        rules.append(
            AllowRule(
                rule_id=rule_id,
                category=category,
                host_patterns=patterns,
                schemes=frozenset(schemes),
                ports=frozenset(ports),
                methods=frozenset(methods),
                content_types=frozenset(content_types),
                max_body_bytes=max_body_bytes,
            )
        )
        rule_ids.add(rule_id)
        all_patterns.extend(patterns)
    return Allowlist(unknown_json_post_max_bytes=threshold, rules=tuple(rules))


def load_allowlist(path: Path = DEFAULT_ALLOWLIST) -> Allowlist:
    path = _safe_regular_file(path, max_bytes=MAX_ALLOWLIST_BYTES, code="allowlist_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EgressConfigurationError("allowlist_file_invalid") from exc
    return parse_allowlist(payload)


def _normalize_request_host(value: str) -> str:
    if not value or value != value.strip() or any(character in value for character in "/\\@?#\x00"):
        raise EgressConfigurationError("request_host_invalid")
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    candidate = candidate.rstrip(".").lower()
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass
    if ":" in candidate or AMBIGUOUS_NUMERIC_HOST_RE.fullmatch(candidate):
        raise EgressConfigurationError("request_host_invalid")
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EgressConfigurationError("request_host_invalid") from exc
    labels = candidate.split(".")
    if candidate != "localhost" and (len(labels) < 2 or all(label.isdigit() for label in labels)):
        raise EgressConfigurationError("request_host_invalid")
    if len(candidate) > 253 or any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise EgressConfigurationError("request_host_invalid")
    return candidate


def _normalize_content_type(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or any(character in value for character in "\r\n"):
        raise EgressConfigurationError("request_content_type_invalid")
    media_type = value.split(";", 1)[0].strip().lower()
    if not CONTENT_TYPE_RE.fullmatch(media_type):
        raise EgressConfigurationError("request_content_type_invalid")
    return media_type


def project_request(payload: Any) -> RequestProjection:
    if not isinstance(payload, dict) or set(payload) != REQUEST_KEYS:
        raise EgressConfigurationError("request_projection_schema_invalid")
    scheme = payload.get("scheme")
    method = payload.get("method")
    port = payload.get("port")
    body_bytes = payload.get("body_bytes")
    client = payload.get("client")
    if not isinstance(scheme, str) or scheme != scheme.lower() or not SCHEME_RE.fullmatch(scheme):
        raise EgressConfigurationError("request_scheme_invalid")
    if not isinstance(method, str) or method != method.upper() or not re.fullmatch(r"[A-Z]{3,16}", method):
        raise EgressConfigurationError("request_method_invalid")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise EgressConfigurationError("request_port_invalid")
    if body_bytes is not None and (isinstance(body_bytes, bool) or not isinstance(body_bytes, int) or body_bytes < 0):
        raise EgressConfigurationError("request_body_size_invalid")
    if not isinstance(client, str) or not client or client != client.strip() or len(client) > 128 or "\x00" in client:
        raise EgressConfigurationError("request_client_invalid")
    normalized_client = Path(client).name.lower().removesuffix(".exe")
    if not normalized_client:
        raise EgressConfigurationError("request_client_invalid")

    host = _normalize_request_host(payload.get("host") if isinstance(payload.get("host"), str) else "")
    raw_ips = payload.get("resolved_ips")
    if not isinstance(raw_ips, list) or len(raw_ips) > 16 or not all(isinstance(item, str) for item in raw_ips):
        raise EgressConfigurationError("request_resolved_ips_invalid")
    try:
        resolved_ips = tuple(ipaddress.ip_address(item) for item in raw_ips)
    except ValueError as exc:
        raise EgressConfigurationError("request_resolved_ips_invalid") from exc
    if len(resolved_ips) != len(set(resolved_ips)):
        raise EgressConfigurationError("request_resolved_ips_invalid")
    try:
        literal_host = ipaddress.ip_address(host)
    except ValueError:
        literal_host = None
    if literal_host is not None and resolved_ips != (literal_host,):
        raise EgressConfigurationError("request_resolved_ips_invalid")
    return RequestProjection(
        scheme=scheme,
        host=host,
        port=port,
        method=method,
        content_type=_normalize_content_type(payload.get("content_type")),
        body_bytes=body_bytes,
        resolved_ips=resolved_ips,
        client=normalized_client,
    )


def _decision(request: RequestProjection, rule_id: str, decision: str) -> EgressDecision:
    if decision not in DECISIONS:
        raise AssertionError("invalid decision")
    return EgressDecision(rule_id=rule_id, decision=decision, host=request.host_projection)


def _non_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or not address.is_global
    )


def _approved_rule_matches(rule: AllowRule, request: RequestProjection) -> bool:
    if (
        request.scheme not in rule.schemes
        or request.port not in rule.ports
        or request.method not in rule.methods
        or not any(_host_matches(pattern, request.host) for pattern in rule.host_patterns)
    ):
        return False
    if request.method in READ_METHODS:
        return request.body_bytes in (None, 0)
    if request.body_bytes is None or request.body_bytes > rule.max_body_bytes:
        return False
    if request.body_bytes == 0 and request.content_type is None:
        return True
    return request.content_type in rule.content_types


def decide(request: RequestProjection, allowlist: Allowlist) -> EgressDecision:
    if request.host in METADATA_HOSTS:
        return _decision(request, "ssrf_metadata_host", "deny")
    if request.host == "localhost" or request.host.endswith(INTERNAL_HOST_SUFFIXES):
        return _decision(request, "ssrf_internal_hostname", "deny")
    if not request.resolved_ips:
        return _decision(request, "ssrf_dns_projection_missing", "deny")
    if any(_non_public_ip(address) for address in request.resolved_ips):
        return _decision(request, "ssrf_non_public_ip", "deny")
    if request.client in BLOCKED_TRANSFER_CLIENTS:
        return _decision(request, "blocked_transfer_client", "deny")
    if request.scheme in BLOCKED_TRANSFER_SCHEMES:
        return _decision(request, "blocked_transfer_scheme", "deny")
    if request.scheme not in HTTP_SCHEMES:
        return _decision(request, "unsupported_egress_scheme", "deny")

    destination_rules = [
        rule for rule in allowlist.rules if any(_host_matches(pattern, request.host) for pattern in rule.host_patterns)
    ]
    for rule in destination_rules:
        if _approved_rule_matches(rule, request):
            return _decision(request, rule.rule_id, "allow")
    if destination_rules:
        return _decision(request, "approved_destination_rule_mismatch", "deny")

    if request.content_type == "multipart/form-data":
        return _decision(request, "unknown_multipart_upload", "deny")
    if request.content_type == "application/octet-stream":
        return _decision(request, "unknown_octet_stream_upload", "deny")
    if request.method == "PUT":
        return _decision(request, "unknown_put_upload", "deny")
    if request.method in READ_METHODS:
        return _decision(request, "unknown_safe_read", "allow")
    if request.method != "POST":
        return _decision(request, "unknown_method_denied", "deny")
    if request.body_bytes is None:
        return _decision(request, "unknown_body_size", "deny")
    if request.body_bytes > allowlist.unknown_json_post_max_bytes:
        return _decision(request, "unknown_body_too_large", "deny")
    if request.content_type == "application/json" or (
        request.content_type is not None and request.content_type.endswith("+json")
    ):
        return _decision(request, "unknown_json_post_audit", "audit_only")
    return _decision(request, "unknown_non_json_post", "deny")


def evaluate_redirect_chain(
    requests: Sequence[RequestProjection],
    allowlist: Allowlist,
) -> EgressDecision:
    if not requests:
        raise EgressConfigurationError("redirect_chain_empty")
    if len(requests) > MAX_REDIRECT_HOPS:
        raise EgressConfigurationError("redirect_chain_too_long")
    audit_result: EgressDecision | None = None
    last_result: EgressDecision | None = None
    for request in requests:
        result = decide(request, allowlist)
        if result.decision == "deny":
            return result
        if result.decision == "audit_only" and audit_result is None:
            audit_result = result
        last_result = result
    if audit_result is not None:
        return audit_result
    if last_result is None:
        raise AssertionError("non-empty redirect chain produced no decision")
    return last_result


def _read_input(path: str) -> Any:
    try:
        if path == "-":
            content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            input_path = _safe_regular_file(
                Path(path),
                max_bytes=MAX_INPUT_BYTES,
                code="request_input_file_invalid",
            )
            content = input_path.read_bytes()
        if len(content) > MAX_INPUT_BYTES:
            raise EgressConfigurationError("request_input_file_invalid")
        payload = json.loads(content)
    except EgressConfigurationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EgressConfigurationError("request_input_file_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"requests"} or not isinstance(payload["requests"], list):
        raise EgressConfigurationError("request_input_schema_invalid")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--input", default="-", help="Projected request chain JSON file, or '-' for stdin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        allowlist = load_allowlist(args.allowlist)
        payload = _read_input(args.input)
        requests = [project_request(item) for item in payload["requests"]]
        result = evaluate_redirect_chain(requests, allowlist)
        print(json.dumps(result.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 1 if result.decision == "deny" else 0
    except EgressConfigurationError as exc:
        print(f"egress decision failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
