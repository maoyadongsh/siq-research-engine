#!/usr/bin/env python3
"""Probe the Milvus boundary from inside one verified SIQ OpenShell sandbox.

The probe never connects to a writable Milvus API successfully and never sends a
mutation payload. It proves that the direct Milvus port is denied, the fixed data
broker exposes the four approved read operations, and mutation-shaped broker paths
do not exist. Result rows and primary keys are used in memory only and are never
written to the receipt.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "siq.openshell.milvus-sandbox-boundary-receipt.v1"
BROKER_SCHEMA_VERSION = "siq.openshell.read-only-data-broker.v2"
PROFILE = "siq_analysis"
BROKER_URL = "http://host.openshell.internal:18793"
DIRECT_HOST = "host.openshell.internal"
DIRECT_PORT = 19_530
COLLECTION = "siq_ic_master_coordinator"
VECTOR_DIMENSIONS = 1_024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
READ_OPERATIONS = ("describe", "get", "query", "search")
EXPECTED_DESCRIPTION = {
    "filter_fields": ["id", "project_tag"],
    "primary_field": "id",
    "readable_fields": ["id", "metadata", "project_tag"],
    "vector_fields": ["vector"],
}
MUTATION_ROUTES = (
    "alter",
    "create",
    "create-index",
    "delete",
    "drop",
    "drop-index",
    "insert",
    "upsert",
)
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
DENIED_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.ETIMEDOUT,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.EHOSTDOWN,
    errno.EADDRNOTAVAIL,
}


class BoundaryProbeError(RuntimeError):
    """Stable probe failure that never contains response or credential content."""


def _broker_transport_error(exc: BaseException) -> BoundaryProbeError:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    error_number = getattr(reason, "errno", None)
    if isinstance(reason, socket.gaierror):
        return BoundaryProbeError("broker_name_resolution_failed")
    if isinstance(reason, (TimeoutError, socket.timeout)) or error_number == errno.ETIMEDOUT:
        return BoundaryProbeError("broker_connection_timed_out")
    if isinstance(reason, PermissionError) or error_number in {errno.EACCES, errno.EPERM}:
        return BoundaryProbeError("broker_policy_denied")
    if isinstance(reason, ConnectionRefusedError) or error_number == errno.ECONNREFUSED:
        return BoundaryProbeError("broker_connection_refused")
    if error_number in {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN}:
        return BoundaryProbeError("broker_route_unreachable")
    return BoundaryProbeError("broker_unreachable")


def _request(path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json", "Connection": "close"}
    data: bytes | None = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        headers["Content-Type"] = "application/json"
        method = "POST"
    identity = str(os.environ.get("SIQ_OPENSHELL_DATA_IDENTITY_TOKEN") or "").strip()
    if identity:
        headers["X-SIQ-OpenShell-Identity"] = identity
    request = urllib.request.Request(f"{BROKER_URL}{path}", data=data, headers=headers, method=method)
    # OpenShell's nested network namespace only permits traffic through the
    # policy proxy injected into HTTP_PROXY/HTTPS_PROXY.
    opener = urllib.request.build_opener()
    try:
        with opener.open(request, timeout=10) as response:
            status = response.status
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        content = exc.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise _broker_transport_error(exc) from exc
    if len(content) > MAX_RESPONSE_BYTES:
        raise BoundaryProbeError("broker_response_too_large")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundaryProbeError("broker_response_invalid") from exc
    if not isinstance(value, dict):
        raise BoundaryProbeError("broker_response_invalid")
    return status, value


def _require_read(path: str, payload: Mapping[str, Any], operation: str) -> dict[str, Any]:
    status, result = _request(path, payload)
    if status != 200 or result.get("ok") is not True or result.get("operation") != operation:
        raise BoundaryProbeError(f"broker_{operation}_failed")
    return result


def _direct_port_denied() -> str:
    try:
        connection = socket.create_connection((DIRECT_HOST, DIRECT_PORT), timeout=2)
    except socket.gaierror as exc:
        if exc.errno not in {
            getattr(socket, "EAI_AGAIN", None),
            getattr(socket, "EAI_FAIL", None),
            getattr(socket, "EAI_NONAME", None),
            getattr(socket, "EAI_NODATA", None),
        }:
            raise BoundaryProbeError("direct_milvus_probe_failed") from exc
        return "name_resolution_denied"
    except (TimeoutError, OSError) as exc:
        error_number = getattr(exc, "errno", None)
        if not isinstance(exc, TimeoutError) and error_number not in DENIED_ERRNOS:
            raise BoundaryProbeError("direct_milvus_probe_failed") from exc
        return "connect_denied"
    else:
        connection.close()
        raise BoundaryProbeError("direct_milvus_allowed")


def run_probe(*, run_id: str, sandbox_id: str, container_id: str, policy_sha256: str) -> dict[str, Any]:
    if (
        not SAFE_ID_RE.fullmatch(run_id)
        or not UUID_RE.fullmatch(sandbox_id)
        or not CONTAINER_ID_RE.fullmatch(container_id)
        or not SHA256_RE.fullmatch(policy_sha256)
    ):
        raise BoundaryProbeError("sandbox_binding_invalid")

    direct_result = _direct_port_denied()
    health_status, health = _request("/healthz")
    if (
        health_status != 200
        or health.get("ok") is not True
        or health.get("schema_version") != BROKER_SCHEMA_VERSION
        or health.get("service") != "siq-read-only-data-broker"
        or health.get("milvus_operations") != list(READ_OPERATIONS)
    ):
        raise BoundaryProbeError("broker_contract_stale")

    description = _require_read(
        "/v1/milvus/describe",
        {"collection": COLLECTION},
        "describe",
    )
    projection = description.get("description")
    if projection != EXPECTED_DESCRIPTION:
        raise BoundaryProbeError("broker_describe_contract_invalid")
    catalog_sha256 = hashlib.sha256(
        json.dumps(projection, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    ).hexdigest()

    query = _require_read(
        "/v1/milvus/query",
        {"collection": COLLECTION, "limit": 1, "output_fields": ["id"]},
        "query",
    )
    rows = query.get("results")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise BoundaryProbeError("broker_query_empty")
    primary_key = rows[0].get("id")
    if isinstance(primary_key, bool) or not isinstance(primary_key, int):
        raise BoundaryProbeError("broker_query_primary_key_invalid")

    get_result = _require_read(
        "/v1/milvus/get",
        {"collection": COLLECTION, "ids": [primary_key], "output_fields": ["id"]},
        "get",
    )
    if not isinstance(get_result.get("results"), list):
        raise BoundaryProbeError("broker_get_contract_invalid")

    search = _require_read(
        "/v1/milvus/search",
        {
            "collection": COLLECTION,
            "vector": [0.0] * VECTOR_DIMENSIONS,
            "vector_field": "vector",
            "output_fields": ["id"],
            "limit": 1,
        },
        "search",
    )
    if not isinstance(search.get("results"), list):
        raise BoundaryProbeError("broker_search_contract_invalid")

    for operation in MUTATION_ROUTES:
        status, result = _request(f"/v1/milvus/{operation}", {"collection": COLLECTION})
        if status != 404 or result.get("ok") is not False or result.get("error_code") != "route_not_found":
            raise BoundaryProbeError("broker_mutation_route_exposed")

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_unix": int(time.time()),
        "profile": PROFILE,
        "run_id": run_id,
        "sandbox_id": sandbox_id,
        "container_id": container_id,
        "policy_sha256": policy_sha256,
        "broker_schema_version": BROKER_SCHEMA_VERSION,
        "milvus_catalog_sha256": catalog_sha256,
        "direct_milvus": {"port": DIRECT_PORT, "result": "denied", "reason_class": direct_result},
        "read_operations": list(READ_OPERATIONS),
        "mutation_routes_denied": list(MUTATION_ROUTES),
        "business_rows_modified": 0,
        "passed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sandbox-id", required=True)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--policy-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_probe(
            run_id=args.run_id,
            sandbox_id=args.sandbox_id,
            container_id=args.container_id,
            policy_sha256=args.policy_sha256,
        )
    except BoundaryProbeError as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
