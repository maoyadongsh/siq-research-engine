#!/usr/bin/env python3
"""Controlled host-side, read-only PostgreSQL and Milvus broker for SIQ sandboxes."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.hermes.profiles.shared.scripts import pg_query  # noqa: E402
from scripts.openshell import (  # noqa: E402
    bridge_endpoint,
    broker_request_identity,
    security_audit,
)

SCHEMA_VERSION = "siq.openshell.read-only-data-broker.v2"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 18793
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SQL_BYTES = 128 * 1024
MAX_VECTOR_DIMENSIONS = 16_384
MAX_MILVUS_SEARCH_LIMIT = 50
MAX_MILVUS_QUERY_LIMIT = 200
MAX_MILVUS_GET_IDS = 50
POSTGRES_SCHEMA_DATABASES = {
    "pdf2md": "siq",
    "pdf2md_hk": "siq_hk",
    "sec_us": "siq_us",
    "edinet_jp": "siq_jp",
    "dart_kr": "siq_kr",
    "eu_ifrs": "siq_eu",
}
FORBIDDEN_POSTGRES_USERS = {
    "admin",
    "app",
    "postgres",
    "rds_superuser",
    "root",
    "siq_app",
}
FORBIDDEN_MILVUS_USERS = {"admin", "root"}
IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}\Z")
JSON_STRING_PATTERN = r'"(?:\\["\\/bfnrt]|\\u[0-9A-Fa-f]{4}|[^"\\])*"'
JSON_NUMBER_PATTERN = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
SCALAR_EXPR_RE = re.compile(
    rf"(?P<field>[a-z_][a-z0-9_]*)\s*(?P<operator>==|!=|>=|<=|>|<)\s*"
    rf"(?P<value>{JSON_STRING_PATTERN}|{JSON_NUMBER_PATTERN}|true|false)\Z"
)
LIST_EXPR_RE = re.compile(r"(?P<field>[a-z_][a-z0-9_]*)\s+(?P<operator>in|not\s+in)\s+(?P<value>\[.*\])\Z")
FORBIDDEN_SQL_FUNCTION_RE = re.compile(
    r"\b(?:"
    r"dblink|dblink_exec|lo_export|lo_import|nextval|pg_advisory_lock|"
    r"pg_advisory_xact_lock|pg_read_binary_file|pg_read_file|pg_reload_conf|"
    r"pg_rotate_logfile|pg_sleep|pg_terminate_backend|set_config|setval"
    r")\s*\(",
    re.IGNORECASE,
)
SELECT_INTO_RE = re.compile(r"\bselect\b[\s\S]*?\binto\b", re.IGNORECASE)
LOCKING_CLAUSE_RE = re.compile(
    r"\bfor\s+(?:no\s+key\s+update|key\s+share|update|share)\b",
    re.IGNORECASE,
)


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.status = int(status)


class PostgresConnector(Protocol):
    def __call__(
        self,
        config: "PostgresReadOnlyConfig",
        database: str,
        timeout_ms: int,
    ) -> tuple[str, Any]: ...


class PostgresAdapter(Protocol):
    def query(
        self,
        *,
        sql: str,
        schema: str,
        database: str,
        timeout_ms: int,
    ) -> list[dict[str, Any]]: ...


class MilvusAdapter(Protocol):
    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        vector_field: str,
        output_fields: list[str],
        limit: int,
        expr: str,
    ) -> Any: ...

    def query(
        self,
        *,
        collection: str,
        output_fields: list[str],
        limit: int,
        expr: str,
    ) -> Any: ...

    def get(
        self,
        *,
        collection: str,
        ids: list[int | str],
        output_fields: list[str],
    ) -> Any: ...

    def describe(self, *, collection: str) -> Any: ...


class AuditSink(Protocol):
    def record(
        self,
        *,
        scope: str,
        target: str,
        decision: str,
        error_code: str,
        duration_ms: int,
    ) -> None: ...


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise BrokerError(
            "broker_credentials_missing",
            f"Required dedicated broker setting is missing: {name}.",
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return value


def _validated_port(value: str, *, error_code: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise BrokerError(error_code, "Configured service port is invalid.") from exc
    if not 1 <= port <= 65_535:
        raise BrokerError(error_code, "Configured service port is invalid.")
    return port


def _validate_host(value: str, *, error_code: str) -> str:
    if not HOST_RE.fullmatch(value) or any(token in value for token in ("/", "@", "?", "#")):
        raise BrokerError(error_code, "Configured service host is invalid.")
    return value


@dataclass(frozen=True)
class PostgresReadOnlyConfig:
    host: str
    port: int
    user: str
    password: str
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PostgresReadOnlyConfig":
        source = os.environ if env is None else env
        host = _validate_host(
            _required_env(source, "SIQ_OPENSHELL_PG_RO_HOST"),
            error_code="postgresql_host_invalid",
        )
        port = _validated_port(
            _required_env(source, "SIQ_OPENSHELL_PG_RO_PORT"),
            error_code="postgresql_port_invalid",
        )
        user = _required_env(source, "SIQ_OPENSHELL_PG_RO_USER")
        normalized_user = user.lower()
        if (
            not IDENTIFIER_RE.fullmatch(normalized_user)
            or normalized_user in FORBIDDEN_POSTGRES_USERS
            or "superuser" in normalized_user
        ):
            raise BrokerError(
                "postgresql_role_not_allowed",
                "The configured broker role is not an approved dedicated read-only role.",
            )
        password = _required_env(source, "SIQ_OPENSHELL_PG_RO_PASSWORD")
        if len(password) > 4_096 or "\x00" in password:
            raise BrokerError("postgresql_password_invalid", "The configured broker password is invalid.")
        sslmode = str(source.get("SIQ_OPENSHELL_PG_RO_SSLMODE") or "prefer").strip().lower()
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise BrokerError("postgresql_sslmode_invalid", "The configured PostgreSQL SSL mode is invalid.")
        return cls(
            host=host,
            port=port,
            user=user,
            password=password,
            sslmode=sslmode,
        )


def _default_postgres_connector(
    config: PostgresReadOnlyConfig,
    database: str,
    timeout_ms: int,
) -> tuple[str, Any]:
    if database not in POSTGRES_SCHEMA_DATABASES.values():
        raise RuntimeError("postgresql_database_route_invalid")
    connect_timeout = max(1, math.ceil(timeout_ms / 1_000))
    kwargs = {
        "host": config.host,
        "port": config.port,
        "dbname": database,
        "user": config.user,
        "password": config.password,
        "sslmode": config.sslmode,
        "connect_timeout": connect_timeout,
        "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
    }
    if pg_query.psycopg2:
        return "psycopg2", pg_query.psycopg2.connect(**kwargs)
    if pg_query.psycopg:
        return "psycopg3", pg_query.psycopg.connect(row_factory=pg_query.dict_row, **kwargs)
    raise RuntimeError("postgresql_driver_unavailable")


def _transaction_read_only(row: Any) -> bool:
    if isinstance(row, Mapping):
        value = row.get("transaction_read_only")
    elif isinstance(row, (list, tuple)) and row:
        value = row[0]
    else:
        value = None
    return str(value or "").strip().lower() in {"on", "true", "1"}


def _rows_as_dicts(cursor: Any, rows: list[Any]) -> list[dict[str, Any]]:
    if not rows:
        return []
    if all(isinstance(row, Mapping) for row in rows):
        return [dict(row) for row in rows]
    columns = [str(item[0]) for item in (cursor.description or [])]
    return [dict(zip(columns, row, strict=False)) for row in rows]


class PostgreSQLReadOnlyAdapter:
    def __init__(
        self,
        config: PostgresReadOnlyConfig,
        *,
        connector: PostgresConnector = _default_postgres_connector,
    ) -> None:
        self._config = config
        self._connector = connector

    def query(
        self,
        *,
        sql: str,
        schema: str,
        database: str,
        timeout_ms: int,
    ) -> list[dict[str, Any]]:
        if POSTGRES_SCHEMA_DATABASES.get(schema) != database:
            raise RuntimeError("postgresql_schema_database_route_invalid")
        driver, connection = self._connector(self._config, database, timeout_ms)
        try:
            if driver == "psycopg2":
                connection.set_session(readonly=True, autocommit=False)
                cursor_kwargs = {"cursor_factory": pg_query.psycopg2.extras.RealDictCursor}
            elif driver == "psycopg3":
                connection.read_only = True
                connection.autocommit = False
                cursor_kwargs = {}
            else:
                raise RuntimeError("postgresql_driver_invalid")
            with connection.cursor(**cursor_kwargs) as cursor:
                cursor.execute("SHOW transaction_read_only")
                if not _transaction_read_only(cursor.fetchone()):
                    raise RuntimeError("postgresql_read_only_not_enforced")
                cursor.execute(f'SET LOCAL search_path TO "{schema}", pg_catalog')
                cursor.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                cursor.execute(sql)
                rows = cursor.fetchall() if cursor.description else []
                return _rows_as_dicts(cursor, rows)
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()


@dataclass(frozen=True)
class MilvusReadOnlyConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    token: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MilvusReadOnlyConfig":
        source = os.environ if env is None else env
        host = _validate_host(
            str(source.get("SIQ_OPENSHELL_MILVUS_RO_HOST") or "127.0.0.1").strip(),
            error_code="milvus_host_invalid",
        )
        port = _validated_port(
            str(source.get("SIQ_OPENSHELL_MILVUS_RO_PORT") or "19530").strip(),
            error_code="milvus_port_invalid",
        )
        database = str(source.get("SIQ_OPENSHELL_MILVUS_RO_DATABASE") or "default").strip()
        if not IDENTIFIER_RE.fullmatch(database.lower()):
            raise BrokerError("milvus_database_invalid", "The configured Milvus database is invalid.")
        user = str(source.get("SIQ_OPENSHELL_MILVUS_RO_USER") or "").strip()
        if user and (not IDENTIFIER_RE.fullmatch(user.lower()) or user.lower() in FORBIDDEN_MILVUS_USERS):
            raise BrokerError(
                "milvus_role_not_allowed",
                "The configured Milvus role is not an approved read-only role.",
            )
        password = str(source.get("SIQ_OPENSHELL_MILVUS_RO_PASSWORD") or "")
        token = str(source.get("SIQ_OPENSHELL_MILVUS_RO_TOKEN") or "")
        if any("\x00" in value or len(value) > 4_096 for value in (password, token)):
            raise BrokerError("milvus_credentials_invalid", "The configured Milvus credentials are invalid.")
        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            token=token,
        )


class MilvusReadOnlyAdapter:
    """Narrow adapter that intentionally has no mutation or raw-client method."""

    def __init__(self, config: MilvusReadOnlyConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client_instance = client

    def _client(self) -> Any:
        if self._client_instance is None:
            from pymilvus import MilvusClient  # type: ignore[import-not-found]

            self._client_instance = MilvusClient(
                uri=f"http://{self._config.host}:{self._config.port}",
                user=self._config.user,
                password=self._config.password,
                token=self._config.token,
                db_name=self._config.database,
            )
        return self._client_instance

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        vector_field: str,
        output_fields: list[str],
        limit: int,
        expr: str,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "data": [vector],
            "anns_field": vector_field,
            "output_fields": output_fields,
            "limit": limit,
        }
        if expr:
            kwargs["filter"] = expr
        return self._client().search(**kwargs)

    def query(
        self,
        *,
        collection: str,
        output_fields: list[str],
        limit: int,
        expr: str,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "output_fields": output_fields,
            "limit": limit,
        }
        if expr:
            kwargs["filter"] = expr
        return self._client().query(**kwargs)

    def get(
        self,
        *,
        collection: str,
        ids: list[int | str],
        output_fields: list[str],
    ) -> Any:
        return self._client().get(
            collection_name=collection,
            ids=ids,
            output_fields=output_fields,
        )

    def describe(self, *, collection: str) -> Any:
        return self._client().describe_collection(collection_name=collection)


@dataclass(frozen=True)
class CollectionPolicy:
    physical_name: str
    primary_field: str
    primary_type: str
    vector_fields: frozenset[str]
    output_fields: frozenset[str]
    default_output_fields: tuple[str, ...]
    filter_fields: Mapping[str, str]


def _knowledge_policy(physical_name: str) -> CollectionPolicy:
    return CollectionPolicy(
        physical_name=physical_name,
        primary_field="id",
        primary_type="integer",
        vector_fields=frozenset({"vector"}),
        output_fields=frozenset({"id", "metadata", "project_tag"}),
        default_output_fields=("id", "metadata", "project_tag"),
        filter_fields={"id": "integer", "project_tag": "string"},
    )


def _market_policy(collection: str) -> CollectionPolicy:
    return CollectionPolicy(
        physical_name=collection,
        primary_field="chunk_uid",
        primary_type="string",
        vector_fields=frozenset({"vector"}),
        output_fields=frozenset({"batch_tag", "chunk_uid", "metadata"}),
        default_output_fields=("chunk_uid", "batch_tag", "metadata"),
        filter_fields={"batch_tag": "string", "chunk_uid": "string"},
    )


COLLECTION_POLICIES: dict[str, CollectionPolicy] = {
    collection: _market_policy(collection)
    for collection in (
        "siq_eu_reports",
        "siq_hk_reports",
        "siq_jp_reports",
        "siq_kr_reports",
        "siq_us_sec_filings",
    )
}
for logical_name, physical_name in {
    "siq_deal_shared": "ic_collaboration_shared",
    "siq_ic_chairman": "ic_chairman",
    "siq_ic_finance_auditor": "ic_finance_auditor",
    "siq_ic_legal_scanner": "ic_legal_scanner",
    "siq_ic_master_coordinator": "ic_master_coordinator",
    "siq_ic_risk_controller": "ic_risk_controller",
    "siq_ic_sector_expert": "ic_sector_expert",
    "siq_ic_strategist": "ic_strategist",
}.items():
    policy = _knowledge_policy(physical_name)
    COLLECTION_POLICIES[logical_name] = policy
    COLLECTION_POLICIES[physical_name] = policy


def _validate_loopback_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return host
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise BrokerError("bind_host_not_loopback", "The data broker must bind to loopback only.") from exc
    if not address.is_loopback:
        raise BrokerError("bind_host_not_loopback", "The data broker must bind to loopback only.")
    return address.compressed


def resolve_broker_binding(
    host: str,
    *,
    bridge_bind: bool,
    discoverer: Callable[[], bridge_endpoint.BridgeEndpoint] = bridge_endpoint.discover_bridge_endpoint,
) -> tuple[str, frozenset[str]]:
    if not bridge_bind:
        loopback = _validate_loopback_host(host)
        return loopback, frozenset({loopback, "localhost"})
    if host != DEFAULT_BIND_HOST:
        raise BrokerError(
            "bind_mode_conflict",
            "Bridge binding cannot be combined with a host override.",
        )
    try:
        endpoint = discoverer()
        endpoint.validate()
    except bridge_endpoint.BridgeEndpointError as exc:
        raise BrokerError(
            "verified_bridge_unavailable",
            "The fixed OpenShell Docker bridge could not be verified.",
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        ) from exc
    return endpoint.gateway_ip, frozenset({endpoint.gateway_ip, endpoint.host_alias})


def _ingress_host_allowed(raw_host: str | None, allowed_hosts: frozenset[str]) -> bool:
    if not raw_host or any(character in raw_host for character in "\r\n\x00/\\@?#"):
        return False
    try:
        host = (urlsplit(f"//{raw_host}").hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in allowed_hosts


def _validate_payload(
    payload: Any,
    *,
    allowed: set[str],
    required: set[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BrokerError("request_object_required", "The request body must be a JSON object.")
    unknown = set(payload) - allowed
    if unknown:
        raise BrokerError(
            "request_fields_not_allowed",
            "The request contains fields outside the fixed broker contract.",
        )
    missing = required - set(payload)
    if missing:
        raise BrokerError("request_fields_missing", "The request is missing required fields.")
    return payload


def _validate_postgres_sql(sql: Any, *, schema: str, row_limit: int) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise BrokerError("sql_required", "A non-empty SQL statement is required.")
    if len(sql.encode("utf-8")) > MAX_SQL_BYTES:
        raise BrokerError("sql_too_large", "The SQL statement exceeds the broker size limit.")
    try:
        normalized = pg_query.normalize_sql(sql, schema=schema)
    except pg_query.QueryPolicyError as exc:
        raise BrokerError(exc.code, str(exc)) from exc
    cleaned = pg_query.strip_sql_comments_and_literals(normalized)
    if SELECT_INTO_RE.search(cleaned):
        raise BrokerError("select_into_blocked", "SELECT INTO is not allowed by the broker.")
    if LOCKING_CLAUSE_RE.search(cleaned):
        raise BrokerError("locking_clause_blocked", "Row-locking clauses are not allowed by the broker.")
    if FORBIDDEN_SQL_FUNCTION_RE.search(cleaned):
        raise BrokerError("sql_function_blocked", "The query calls a function blocked by the broker.")
    first = pg_query.first_sql_keyword(normalized)
    if first in {"select", "with"}:
        return f"SELECT * FROM ({normalized}) AS siq_broker_result LIMIT {row_limit}"
    return normalized


def _validate_limit(value: Any, *, default: int, maximum: int) -> int:
    resolved = default if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, int) or not 1 <= resolved <= maximum:
        raise BrokerError("limit_out_of_range", f"Limit must be between 1 and {maximum}.")
    return resolved


def _validate_timeout(value: Any) -> int:
    resolved = pg_query.DEFAULT_TIMEOUT_MS if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, int):
        raise BrokerError("timeout_out_of_range", "Statement timeout is invalid.")
    try:
        _, timeout_ms = pg_query.validate_query_limits(row_limit=1, timeout_ms=resolved)
    except pg_query.QueryPolicyError as exc:
        raise BrokerError(exc.code, str(exc)) from exc
    return timeout_ms


def _collection_policy(value: Any) -> tuple[str, CollectionPolicy]:
    if not isinstance(value, str) or value not in COLLECTION_POLICIES:
        raise BrokerError("milvus_collection_not_allowed", "The Milvus collection is not allowlisted.")
    return value, COLLECTION_POLICIES[value]


def _validate_output_fields(value: Any, *, policy: CollectionPolicy) -> list[str]:
    if value is None:
        return list(policy.default_output_fields)
    if not isinstance(value, list) or not value or len(value) > len(policy.output_fields):
        raise BrokerError("milvus_output_fields_invalid", "Milvus output fields are invalid.")
    fields: list[str] = []
    for field in value:
        if not isinstance(field, str) or field not in policy.output_fields or field in fields:
            raise BrokerError("milvus_output_field_not_allowed", "A Milvus output field is not allowlisted.")
        fields.append(field)
    return fields


def _validate_vector(value: Any) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_VECTOR_DIMENSIONS:
        raise BrokerError("milvus_vector_invalid", "The search vector is invalid.")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise BrokerError("milvus_vector_invalid", "The search vector is invalid.")
        number = float(item)
        if not math.isfinite(number) or abs(number) > 1_000_000:
            raise BrokerError("milvus_vector_invalid", "The search vector is invalid.")
        vector.append(number)
    return vector


def _validate_ids(value: Any, *, policy: CollectionPolicy) -> list[int | str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MILVUS_GET_IDS:
        raise BrokerError("milvus_ids_invalid", "Milvus primary keys are invalid.")
    ids: list[int | str] = []
    for item in value:
        _validate_expr_value(item, field_type=policy.primary_type)
        if item in ids:
            raise BrokerError("milvus_ids_invalid", "Milvus primary keys are invalid.")
        ids.append(item)
    return ids


def _describe_projection(value: Any, *, policy: CollectionPolicy) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("fields"), list):
        raise BrokerError(
            "milvus_backend_contract_invalid",
            "The Milvus collection description is invalid.",
            status=HTTPStatus.BAD_GATEWAY,
        )
    observed_names = {
        field.get("name")
        for field in value["fields"]
        if isinstance(field, dict) and isinstance(field.get("name"), str)
    }
    expected_names = {
        policy.primary_field,
        *policy.vector_fields,
        *policy.output_fields,
        *policy.filter_fields,
    }
    if not expected_names.issubset(observed_names):
        raise BrokerError(
            "milvus_backend_contract_invalid",
            "The Milvus collection description is invalid.",
            status=HTTPStatus.BAD_GATEWAY,
        )
    return {
        "primary_field": policy.primary_field,
        "readable_fields": sorted(policy.output_fields),
        "filter_fields": sorted(policy.filter_fields),
        "vector_fields": sorted(policy.vector_fields),
    }


def _validate_expr_value(value: Any, *, field_type: str, multiple: bool = False) -> Any:
    items = value if multiple else [value]
    if multiple and (not isinstance(items, list) or not 1 <= len(items) <= 20):
        raise BrokerError("milvus_expr_invalid", "The Milvus expression is outside the fixed grammar.")
    for item in items:
        if field_type == "string":
            if not isinstance(item, str) or not 1 <= len(item) <= 256 or any(ord(character) < 32 for character in item):
                raise BrokerError("milvus_expr_invalid", "The Milvus expression value is invalid.")
        elif field_type == "integer":
            if isinstance(item, bool) or not isinstance(item, int) or abs(item) > 2**63 - 1:
                raise BrokerError("milvus_expr_invalid", "The Milvus expression value is invalid.")
        else:
            raise BrokerError("milvus_expr_invalid", "The Milvus expression field type is invalid.")
    return value


def _validate_expr(value: Any, *, policy: CollectionPolicy) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 1_024 or "\x00" in value:
        raise BrokerError("milvus_expr_invalid", "The Milvus expression is outside the fixed grammar.")
    scalar = SCALAR_EXPR_RE.fullmatch(value.strip())
    if scalar:
        field = scalar.group("field")
        field_type = policy.filter_fields.get(field)
        if not field_type:
            raise BrokerError("milvus_expr_field_not_allowed", "The Milvus filter field is not allowlisted.")
        parsed = json.loads(scalar.group("value"))
        _validate_expr_value(parsed, field_type=field_type)
        if field_type == "string" and scalar.group("operator") not in {"==", "!="}:
            raise BrokerError("milvus_expr_operator_not_allowed", "The Milvus filter operator is not allowlisted.")
        return f"{field} {scalar.group('operator')} {json.dumps(parsed, ensure_ascii=True)}"
    listed = LIST_EXPR_RE.fullmatch(value.strip())
    if listed:
        field = listed.group("field")
        field_type = policy.filter_fields.get(field)
        if not field_type:
            raise BrokerError("milvus_expr_field_not_allowed", "The Milvus filter field is not allowlisted.")
        try:
            parsed = json.loads(listed.group("value"))
        except json.JSONDecodeError as exc:
            raise BrokerError("milvus_expr_invalid", "The Milvus expression is outside the fixed grammar.") from exc
        _validate_expr_value(parsed, field_type=field_type, multiple=True)
        operator = " ".join(listed.group("operator").split())
        return f"{field} {operator} {json.dumps(parsed, ensure_ascii=True, separators=(',', ':'))}"
    raise BrokerError("milvus_expr_invalid", "The Milvus expression is outside the fixed grammar.")


class SecurityAuditSink:
    def __init__(self, *, project_root: Path, env: Mapping[str, str] | None = None) -> None:
        source = os.environ if env is None else env
        self._project_root = project_root.resolve(strict=True)
        self._profile = str(source.get("SIQ_OPENSHELL_AUDIT_PROFILE") or "siq_analysis")
        self._sandbox_id = str(source.get("SIQ_OPENSHELL_AUDIT_SANDBOX_ID") or "host-data-broker")
        self._session_id = str(source.get("SIQ_OPENSHELL_AUDIT_SESSION_ID") or "data-broker")
        self._policy_digest = str(source.get("SIQ_OPENSHELL_AUDIT_POLICY_DIGEST") or "")
        if not self._policy_digest:
            self._policy_digest = hashlib.sha256(SCHEMA_VERSION.encode()).hexdigest()

    def record(
        self,
        *,
        scope: str,
        target: str,
        decision: str,
        error_code: str,
        duration_ms: int,
    ) -> None:
        identity = broker_request_identity.current_request_identity()
        if identity is None:
            context = security_audit.SecurityRunContext(
                profile=self._profile,
                sandbox_id=self._sandbox_id,
                run_id=f"broker-{uuid.uuid4().hex}",
                session_id=self._session_id,
                policy_digest=self._policy_digest,
            )
        else:
            context = security_audit.SecurityRunContext(
                profile=identity.profile,
                sandbox_id=identity.sandbox_id,
                run_id=identity.run_id,
                session_id=identity.session_id,
                policy_digest=identity.policy_digest,
            )
        record = security_audit.build_record(
            context=context,
            operation_class="database.query",
            target=security_audit.project_target(kind="service", scope=scope, value=target),
            decision=decision,
            error_code=error_code,
            duration_ms=max(0, min(int(duration_ms), 86_400_000)),
        )
        security_audit.append_record(project_root=self._project_root, record=record)


class ReadOnlyDataBroker:
    def __init__(
        self,
        *,
        postgres: PostgresAdapter,
        milvus: MilvusAdapter,
        audit: AuditSink,
    ) -> None:
        self._postgres = postgres
        self._milvus = milvus
        self._audit_sink = audit

    def _audit(
        self,
        *,
        scope: str,
        target: str,
        decision: str,
        error_code: str,
        started: float,
    ) -> None:
        duration_ms = max(0, round((time.monotonic() - started) * 1_000))
        try:
            self._audit_sink.record(
                scope=scope,
                target=target,
                decision=decision,
                error_code=error_code,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            raise BrokerError(
                "security_audit_failed",
                "The broker could not persist its minimal security audit record.",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc

    def postgresql_query(self, payload: Any) -> dict[str, Any]:
        started = time.monotonic()
        scope = "postgresql.schema"
        target = "invalid"
        try:
            request = _validate_payload(
                payload,
                allowed={"limit", "schema", "sql", "timeout_ms"},
                required={"sql"},
            )
            try:
                schema = pg_query.validate_schema(str(request.get("schema") or pg_query.DEFAULTS["schema"]))
                row_limit, _ = pg_query.validate_query_limits(
                    row_limit=_validate_limit(
                        request.get("limit"),
                        default=pg_query.DEFAULT_ROW_LIMIT,
                        maximum=pg_query.MAX_ROW_LIMIT,
                    ),
                    timeout_ms=pg_query.DEFAULT_TIMEOUT_MS,
                )
            except pg_query.QueryPolicyError as exc:
                raise BrokerError(exc.code, str(exc)) from exc
            target = schema
            database = POSTGRES_SCHEMA_DATABASES[schema]
            timeout_ms = _validate_timeout(request.get("timeout_ms"))
            sql = _validate_postgres_sql(request["sql"], schema=schema, row_limit=row_limit)
            try:
                rows = self._postgres.query(
                    sql=sql,
                    schema=schema,
                    database=database,
                    timeout_ms=timeout_ms,
                )
            except Exception as exc:
                raise BrokerError(
                    "postgresql_backend_failed",
                    "The read-only PostgreSQL operation failed.",
                    status=HTTPStatus.BAD_GATEWAY,
                ) from exc
            response = {
                "ok": True,
                "schema": schema,
                "row_limit": row_limit,
                "row_count": len(rows),
                "rows": rows,
            }
            self._audit(scope=scope, target=target, decision="allow", error_code="", started=started)
            return response
        except BrokerError as exc:
            self._audit(
                scope=scope,
                target=target,
                decision="deny",
                error_code=exc.code,
                started=started,
            )
            raise

    def milvus_search(self, payload: Any) -> dict[str, Any]:
        return self._milvus_operation(payload, operation="search")

    def milvus_query(self, payload: Any) -> dict[str, Any]:
        return self._milvus_operation(payload, operation="query")

    def milvus_get(self, payload: Any) -> dict[str, Any]:
        started = time.monotonic()
        scope = "milvus.collection"
        target = "invalid"
        try:
            request = _validate_payload(
                payload,
                allowed={"collection", "ids", "output_fields"},
                required={"collection", "ids"},
            )
            target, policy = _collection_policy(request["collection"])
            fields = _validate_output_fields(request.get("output_fields"), policy=policy)
            ids = _validate_ids(request["ids"], policy=policy)
            try:
                results = self._milvus.get(
                    collection=policy.physical_name,
                    ids=ids,
                    output_fields=fields,
                )
            except Exception as exc:
                raise BrokerError(
                    "milvus_backend_failed",
                    "The read-only Milvus operation failed.",
                    status=HTTPStatus.BAD_GATEWAY,
                ) from exc
            response = {
                "ok": True,
                "operation": "get",
                "collection": target,
                "requested_id_count": len(ids),
                "results": results,
            }
            self._audit(scope=scope, target=target, decision="allow", error_code="", started=started)
            return response
        except BrokerError as exc:
            self._audit(
                scope=scope,
                target=target,
                decision="deny",
                error_code=exc.code,
                started=started,
            )
            raise

    def milvus_describe(self, payload: Any) -> dict[str, Any]:
        started = time.monotonic()
        scope = "milvus.collection"
        target = "invalid"
        try:
            request = _validate_payload(payload, allowed={"collection"}, required={"collection"})
            target, policy = _collection_policy(request["collection"])
            try:
                description = self._milvus.describe(collection=policy.physical_name)
            except Exception as exc:
                raise BrokerError(
                    "milvus_backend_failed",
                    "The read-only Milvus operation failed.",
                    status=HTTPStatus.BAD_GATEWAY,
                ) from exc
            response = {
                "ok": True,
                "operation": "describe",
                "collection": target,
                "description": _describe_projection(description, policy=policy),
            }
            self._audit(scope=scope, target=target, decision="allow", error_code="", started=started)
            return response
        except BrokerError as exc:
            self._audit(
                scope=scope,
                target=target,
                decision="deny",
                error_code=exc.code,
                started=started,
            )
            raise

    def _milvus_operation(self, payload: Any, *, operation: str) -> dict[str, Any]:
        started = time.monotonic()
        scope = "milvus.collection"
        target = "invalid"
        allowed = {"collection", "expr", "limit", "output_fields"}
        required = {"collection"}
        if operation == "search":
            allowed |= {"vector", "vector_field"}
            required |= {"vector"}
        try:
            request = _validate_payload(payload, allowed=allowed, required=required)
            target, policy = _collection_policy(request["collection"])
            fields = _validate_output_fields(request.get("output_fields"), policy=policy)
            expr = _validate_expr(request.get("expr"), policy=policy)
            if operation == "search":
                limit = _validate_limit(
                    request.get("limit"),
                    default=10,
                    maximum=MAX_MILVUS_SEARCH_LIMIT,
                )
                vector_field = str(request.get("vector_field") or "vector")
                if vector_field not in policy.vector_fields:
                    raise BrokerError(
                        "milvus_vector_field_not_allowed",
                        "The Milvus vector field is not allowlisted.",
                    )
                vector = _validate_vector(request["vector"])

                def backend_call() -> Any:
                    return self._milvus.search(
                        collection=policy.physical_name,
                        vector=vector,
                        vector_field=vector_field,
                        output_fields=fields,
                        limit=limit,
                        expr=expr,
                    )

            else:
                limit = _validate_limit(
                    request.get("limit"),
                    default=50,
                    maximum=MAX_MILVUS_QUERY_LIMIT,
                )

                def backend_call() -> Any:
                    return self._milvus.query(
                        collection=policy.physical_name,
                        output_fields=fields,
                        limit=limit,
                        expr=expr,
                    )

            try:
                results = backend_call()
            except Exception as exc:
                raise BrokerError(
                    "milvus_backend_failed",
                    "The read-only Milvus operation failed.",
                    status=HTTPStatus.BAD_GATEWAY,
                ) from exc
            response = {
                "ok": True,
                "operation": operation,
                "collection": target,
                "limit": limit,
                "results": results,
            }
            self._audit(scope=scope, target=target, decision="allow", error_code="", started=started)
            return response
        except BrokerError as exc:
            self._audit(
                scope=scope,
                target=target,
                decision="deny",
                error_code=exc.code,
                started=started,
            )
            raise

    def dispatch(self, path: str, payload: Any) -> dict[str, Any]:
        routes: dict[str, Callable[[Any], dict[str, Any]]] = {
            "/v1/milvus/describe": self.milvus_describe,
            "/v1/milvus/get": self.milvus_get,
            "/v1/milvus/query": self.milvus_query,
            "/v1/milvus/search": self.milvus_search,
            "/v1/postgresql/query": self.postgresql_query,
        }
        handler = routes.get(path)
        if handler is None:
            raise BrokerError("route_not_found", "The broker route does not exist.", status=HTTPStatus.NOT_FOUND)
        return handler(payload)


class BrokerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        broker: ReadOnlyDataBroker,
        *,
        allowed_ingress_hosts: frozenset[str] | None = None,
        identity_key: bytes | None = None,
        require_identity: bool = False,
    ):
        self.broker = broker
        self.allowed_ingress_hosts = allowed_ingress_hosts or frozenset({"127.0.0.1", "localhost"})
        if require_identity and identity_key is None:
            raise BrokerError(
                "broker_identity_key_missing",
                "The broker identity key is unavailable.",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        self.identity_key = identity_key
        self.require_identity = require_identity
        super().__init__(address, BrokerRequestHandler)


class BrokerRequestHandler(BaseHTTPRequestHandler):
    server: BrokerHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        # Request and response bodies are intentionally never sent to access logs.
        return

    def _write_json(self, status: int, payload: Mapping[str, Any]) -> None:
        try:
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=pg_query.json_default,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BrokerError(
                "response_serialization_failed",
                "The broker response could not be serialized.",
                status=HTTPStatus.BAD_GATEWAY,
            ) from exc
        if len(content) > MAX_RESPONSE_BYTES:
            raise BrokerError(
                "response_too_large",
                "The broker response exceeds the fixed size limit.",
                status=HTTPStatus.BAD_GATEWAY,
            )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _write_error(self, error: BrokerError) -> None:
        payload = {"ok": False, "error_code": error.code, "error": str(error)}
        try:
            self._write_json(error.status, payload)
        except BrokerError:
            content = b'{"ok":false,"error_code":"response_failed","error":"Broker response failed."}'
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    def _require_allowed_host(self) -> None:
        if not _ingress_host_allowed(self.headers.get("Host"), self.server.allowed_ingress_hosts):
            raise BrokerError(
                "ingress_host_denied",
                "The request Host is outside the broker bind contract.",
                status=HTTPStatus.FORBIDDEN,
            )

    def _request_identity(self) -> broker_request_identity.RequestIdentity | None:
        if self.server.identity_key is None:
            if self.server.require_identity:
                raise BrokerError(
                    "broker_identity_required",
                    "A signed broker identity is required.",
                    status=HTTPStatus.UNAUTHORIZED,
                )
            return None
        try:
            values = self.headers.get_all(broker_request_identity.HEADER_NAME, [])
            return broker_request_identity.verify_header_values(
                values,
                self.server.identity_key,
                expected_audience=broker_request_identity.DATA_AUDIENCE,
            )
        except broker_request_identity.IdentityError as exc:
            code = str(exc)
            if code == "broker_identity_header_required":
                raise BrokerError(
                    "broker_identity_required",
                    "A signed broker identity is required.",
                    status=HTTPStatus.UNAUTHORIZED,
                ) from exc
            raise BrokerError(
                "broker_identity_invalid",
                "The signed broker identity is invalid.",
                status=HTTPStatus.FORBIDDEN,
            ) from exc

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_allowed_host()
            if self.path != "/healthz":
                raise BrokerError("route_not_found", "The broker route does not exist.", status=404)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "schema_version": SCHEMA_VERSION,
                    "service": "siq-read-only-data-broker",
                    "milvus_operations": ["describe", "get", "query", "search"],
                },
            )
        except BrokerError as exc:
            self._write_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_allowed_host()
            identity = self._request_identity()
            media_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise BrokerError("json_content_type_required", "Content-Type must be application/json.")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise BrokerError("content_length_required", "Content-Length is required.", status=411)
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise BrokerError("content_length_invalid", "Content-Length is invalid.") from exc
            if not 1 <= length <= MAX_REQUEST_BYTES:
                raise BrokerError("request_size_invalid", "The request body exceeds the fixed size limit.", status=413)
            content = self.rfile.read(length)
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BrokerError("invalid_json", "The request body is not valid JSON.") from exc
            if identity is None:
                response = self.server.broker.dispatch(self.path, payload)
            else:
                with broker_request_identity.request_identity_context(identity):
                    response = self.server.broker.dispatch(self.path, payload)
            self._write_json(HTTPStatus.OK, response)
        except BrokerError as exc:
            self._write_error(exc)


def build_broker(*, project_root: Path, env: Mapping[str, str] | None = None) -> ReadOnlyDataBroker:
    return ReadOnlyDataBroker(
        postgres=PostgreSQLReadOnlyAdapter(PostgresReadOnlyConfig.from_env(env)),
        milvus=MilvusReadOnlyAdapter(MilvusReadOnlyConfig.from_env(env)),
        audit=SecurityAuditSink(project_root=project_root, env=env),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("SIQ_OPENSHELL_DATA_BROKER_HOST", DEFAULT_BIND_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("SIQ_OPENSHELL_DATA_BROKER_PORT", str(DEFAULT_PORT)),
    )
    parser.add_argument(
        "--bridge-bind",
        action="store_true",
        help=f"Bind to the verified {bridge_endpoint.NETWORK_NAME} Docker gateway",
    )
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        host, ingress_hosts = resolve_broker_binding(args.host, bridge_bind=args.bridge_bind)
        if not 1 <= args.port <= 65_535:
            raise BrokerError("bind_port_invalid", "The data broker port is invalid.")
        broker = build_broker(project_root=args.project_root, env=os.environ)
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
        server = BrokerHTTPServer(
            (host, args.port),
            broker,
            allowed_ingress_hosts=ingress_hosts,
            identity_key=identity_key,
            require_identity=require_identity,
        )
    except (BrokerError, broker_request_identity.IdentityError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, BrokerError) else "broker_start_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "service": "siq-read-only-data-broker",
                "listen": f"{host}:{args.port}",
                "schema_version": SCHEMA_VERSION,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
