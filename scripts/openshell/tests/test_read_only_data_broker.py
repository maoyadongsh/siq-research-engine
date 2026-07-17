from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.openshell import read_only_data_broker as broker


class FakePostgres:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query(
        self,
        *,
        sql: str,
        schema: str,
        database: str,
        timeout_ms: int,
    ) -> list[dict[str, Any]]:
        self.calls.append({"sql": sql, "schema": schema, "database": database, "timeout_ms": timeout_ms})
        return [{"ticker": "TEST", "value": 42}]


class FakeMilvus:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.describe_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
        self.search_calls.append(kwargs)
        return [[{"id": 1, "metadata": {"title": "fixture"}}]]

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.query_calls.append(kwargs)
        return [{"id": 1, "project_tag": "fixture"}]

    def get(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.get_calls.append(kwargs)
        return [{"id": 1, "project_tag": "fixture"}]

    def describe(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_calls.append(kwargs)
        return {
            "fields": [
                {"name": "id"},
                {"name": "vector"},
                {"name": "project_tag"},
                {"name": "metadata"},
            ]
        }


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _service() -> tuple[broker.ReadOnlyDataBroker, FakePostgres, FakeMilvus, FakeAudit]:
    postgres = FakePostgres()
    milvus = FakeMilvus()
    audit = FakeAudit()
    return (
        broker.ReadOnlyDataBroker(postgres=postgres, milvus=milvus, audit=audit),
        postgres,
        milvus,
        audit,
    )


def _pg_env(**overrides: str) -> dict[str, str]:
    values = {
        "SIQ_OPENSHELL_PG_RO_HOST": "127.0.0.1",
        "SIQ_OPENSHELL_PG_RO_PORT": "15432",
        "SIQ_OPENSHELL_PG_RO_USER": "siq_openshell_reader",
        "SIQ_OPENSHELL_PG_RO_PASSWORD": "test-only-password",
    }
    values.update(overrides)
    return values


def test_postgres_config_uses_only_dedicated_broker_variables() -> None:
    with pytest.raises(broker.BrokerError, match="SIQ_OPENSHELL_PG_RO_HOST") as missing:
        broker.PostgresReadOnlyConfig.from_env(
            {
                "SIQ_APP_DATABASE_URL": "postgresql://app:ignored@db/siq",
                "PGUSER": "postgres",
                "PGPASSWORD": "ignored",
            }
        )
    assert missing.value.code == "broker_credentials_missing"

    config = broker.PostgresReadOnlyConfig.from_env(_pg_env())
    assert config.user == "siq_openshell_reader"

    with pytest.raises(broker.BrokerError) as privileged:
        broker.PostgresReadOnlyConfig.from_env(_pg_env(SIQ_OPENSHELL_PG_RO_USER="postgres"))
    assert privileged.value.code == "postgresql_role_not_allowed"


def test_postgres_service_wraps_query_with_hard_limit_and_minimal_audit() -> None:
    service, postgres, _, audit = _service()
    response = service.postgresql_query(
        {
            "sql": "SELECT ticker, value FROM pdf2md.facts ORDER BY ticker",
            "schema": "pdf2md",
            "limit": 25,
            "timeout_ms": 4_000,
        }
    )

    assert response == {
        "ok": True,
        "schema": "pdf2md",
        "row_limit": 25,
        "row_count": 1,
        "rows": [{"ticker": "TEST", "value": 42}],
    }
    assert postgres.calls == [
        {
            "sql": (
                "SELECT * FROM (SELECT ticker, value FROM pdf2md.facts ORDER BY ticker) AS siq_broker_result LIMIT 25"
            ),
            "schema": "pdf2md",
            "database": "siq",
            "timeout_ms": 4_000,
        }
    ]
    assert audit.events == [
        {
            "scope": "postgresql.schema",
            "target": "pdf2md",
            "decision": "allow",
            "error_code": "",
            "duration_ms": audit.events[0]["duration_ms"],
        }
    ]
    serialized_audit = json.dumps(audit.events)
    assert "SELECT" not in serialized_audit
    assert "TEST" not in serialized_audit


@pytest.mark.parametrize(
    ("schema", "database"),
    [
        ("pdf2md", "siq"),
        ("pdf2md_hk", "siq_hk"),
        ("sec_us", "siq_us"),
        ("edinet_jp", "siq_jp"),
        ("dart_kr", "siq_kr"),
        ("eu_ifrs", "siq_eu"),
    ],
)
def test_postgres_schema_selects_only_fixed_server_side_database(schema: str, database: str) -> None:
    service, postgres, _, _ = _service()

    service.postgresql_query({"sql": f"SELECT 1 FROM {schema}.facts", "schema": schema, "limit": 1})

    assert postgres.calls[0]["schema"] == schema
    assert postgres.calls[0]["database"] == database
    assert broker.POSTGRES_SCHEMA_DATABASES[schema] == database


def test_postgres_config_ignores_database_environment_and_adapter_rechecks_fixed_route() -> None:
    config = broker.PostgresReadOnlyConfig.from_env(_pg_env(SIQ_OPENSHELL_PG_RO_DATABASE="attacker_selected"))
    called = False

    def connector(
        _config: broker.PostgresReadOnlyConfig,
        _database: str,
        _timeout_ms: int,
    ) -> tuple[str, FakeConnection]:
        nonlocal called
        called = True
        return "psycopg3", FakeConnection()

    adapter = broker.PostgreSQLReadOnlyAdapter(config, connector=connector)
    with pytest.raises(RuntimeError, match="schema_database_route_invalid"):
        adapter.query(
            sql="SELECT 1",
            schema="pdf2md",
            database="siq_us",
            timeout_ms=1_000,
        )
    assert called is False


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"sql": "SELECT 1; SELECT 2"}, "multiple_statements_blocked"),
        ({"sql": "SELECT * INTO copied FROM pdf2md.facts"}, "select_into_blocked"),
        ({"sql": "SELECT pg_read_file('/etc/passwd')"}, "sql_function_blocked"),
        ({"sql": "SELECT * FROM sec_us.facts", "schema": "pdf2md"}, "cross_schema_query_blocked"),
        ({"sql": "SELECT 1", "database": "siq_us"}, "request_fields_not_allowed"),
        ({"sql": "SELECT 1", "dsn": "postgresql://example"}, "request_fields_not_allowed"),
        ({"sql": "SELECT 1", "path": "/tmp/result"}, "request_fields_not_allowed"),
    ],
)
def test_postgres_policy_rejects_write_like_or_open_ended_requests(
    payload: dict[str, Any],
    code: str,
) -> None:
    service, postgres, _, audit = _service()

    with pytest.raises(broker.BrokerError) as exc_info:
        service.postgresql_query(payload)

    assert exc_info.value.code == code
    assert postgres.calls == []
    assert audit.events[-1]["decision"] == "deny"
    assert audit.events[-1]["error_code"] == code


class FakeCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.description: list[tuple[str]] | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.statements.append(sql)
        if sql.startswith("SELECT *"):
            self.description = [("value",)]

    def fetchone(self) -> dict[str, str]:
        return {"transaction_read_only": "on"}

    def fetchall(self) -> list[dict[str, int]]:
        return [{"value": 1}]


class FakeConnection:
    def __init__(self) -> None:
        self.read_only = False
        self.autocommit = True
        self.rolled_back = False
        self.closed = False
        self.cursor_instance = FakeCursor()

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_postgres_adapter_verifies_read_only_transaction_and_closes_connection() -> None:
    connection = FakeConnection()
    seen: dict[str, Any] = {}

    def connector(
        config: broker.PostgresReadOnlyConfig,
        database: str,
        timeout_ms: int,
    ) -> tuple[str, FakeConnection]:
        seen.update({"config": config, "database": database, "timeout_ms": timeout_ms})
        return "psycopg3", connection

    adapter = broker.PostgreSQLReadOnlyAdapter(
        broker.PostgresReadOnlyConfig.from_env(_pg_env()),
        connector=connector,
    )
    rows = adapter.query(
        sql="SELECT * FROM (SELECT 1 AS value) AS siq_broker_result LIMIT 1",
        schema="pdf2md",
        database="siq",
        timeout_ms=3_000,
    )

    assert rows == [{"value": 1}]
    assert seen["timeout_ms"] == 3_000
    assert seen["database"] == "siq"
    assert connection.read_only is True
    assert connection.autocommit is False
    assert connection.cursor_instance.statements == [
        "SHOW transaction_read_only",
        'SET LOCAL search_path TO "pdf2md", pg_catalog',
        "SET LOCAL statement_timeout = 3000",
        "SELECT * FROM (SELECT 1 AS value) AS siq_broker_result LIMIT 1",
    ]
    assert connection.rolled_back is True
    assert connection.closed is True


def test_milvus_search_uses_fixed_collection_fields_expr_and_limit() -> None:
    service, _, milvus, audit = _service()
    response = service.milvus_search(
        {
            "collection": "siq_deal_shared",
            "vector": [0.1, 0.2, 0.3],
            "vector_field": "vector",
            "output_fields": ["metadata", "project_tag"],
            "expr": 'project_tag == "deal-fixture"',
            "limit": 5,
        }
    )

    assert response["ok"] is True
    assert response["collection"] == "siq_deal_shared"
    assert milvus.search_calls == [
        {
            "collection": "ic_collaboration_shared",
            "vector": [0.1, 0.2, 0.3],
            "vector_field": "vector",
            "output_fields": ["metadata", "project_tag"],
            "limit": 5,
            "expr": 'project_tag == "deal-fixture"',
        }
    ]
    serialized_audit = json.dumps(audit.events)
    assert "0.1" not in serialized_audit
    assert "fixture" not in serialized_audit


def test_milvus_get_and_describe_use_fixed_read_only_projection() -> None:
    service, _, milvus, audit = _service()

    get_response = service.milvus_get(
        {
            "collection": "siq_ic_master_coordinator",
            "ids": [1, 2],
            "output_fields": ["id", "project_tag"],
        }
    )
    describe_response = service.milvus_describe({"collection": "siq_ic_master_coordinator"})

    assert get_response == {
        "ok": True,
        "operation": "get",
        "collection": "siq_ic_master_coordinator",
        "requested_id_count": 2,
        "results": [{"id": 1, "project_tag": "fixture"}],
    }
    assert milvus.get_calls == [
        {
            "collection": "ic_master_coordinator",
            "ids": [1, 2],
            "output_fields": ["id", "project_tag"],
        }
    ]
    assert describe_response == {
        "ok": True,
        "operation": "describe",
        "collection": "siq_ic_master_coordinator",
        "description": {
            "primary_field": "id",
            "readable_fields": ["id", "metadata", "project_tag"],
            "filter_fields": ["id", "project_tag"],
            "vector_fields": ["vector"],
        },
    }
    assert milvus.describe_calls == [{"collection": "ic_master_coordinator"}]
    assert [event["decision"] for event in audit.events[-2:]] == ["allow", "allow"]


@pytest.mark.parametrize(
    ("method", "payload", "code"),
    [
        (
            "milvus_search",
            {"collection": "unknown", "vector": [0.1]},
            "milvus_collection_not_allowed",
        ),
        (
            "milvus_search",
            {"collection": "siq_deal_shared", "vector": [0.1], "vector_field": "embedding"},
            "milvus_vector_field_not_allowed",
        ),
        (
            "milvus_query",
            {"collection": "siq_deal_shared", "output_fields": ["vector"]},
            "milvus_output_field_not_allowed",
        ),
        (
            "milvus_query",
            {"collection": "siq_deal_shared", "expr": 'metadata["tenant"] == "x"'},
            "milvus_expr_invalid",
        ),
        (
            "milvus_query",
            {"collection": "siq_deal_shared", "expr": 'project_tag == "x" or id == 1'},
            "milvus_expr_invalid",
        ),
        (
            "milvus_query",
            {"collection": "siq_deal_shared", "database": "other"},
            "request_fields_not_allowed",
        ),
        (
            "milvus_query",
            {"collection": "siq_deal_shared", "path": "/tmp/output"},
            "request_fields_not_allowed",
        ),
        (
            "milvus_get",
            {"collection": "siq_deal_shared", "ids": [True]},
            "milvus_expr_invalid",
        ),
        (
            "milvus_get",
            {"collection": "siq_deal_shared", "ids": [1, 1]},
            "milvus_ids_invalid",
        ),
        (
            "milvus_describe",
            {"collection": "siq_deal_shared", "database": "other"},
            "request_fields_not_allowed",
        ),
    ],
)
def test_milvus_policy_rejects_non_allowlisted_surface(
    method: str,
    payload: dict[str, Any],
    code: str,
) -> None:
    service, _, milvus, audit = _service()

    with pytest.raises(broker.BrokerError) as exc_info:
        getattr(service, method)(payload)

    assert exc_info.value.code == code
    assert milvus.search_calls == []
    assert milvus.query_calls == []
    assert milvus.get_calls == []
    assert milvus.describe_calls == []
    assert audit.events[-1]["decision"] == "deny"


class FakeMilvusClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("search", kwargs))
        return []

    def query(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("query", kwargs))
        return []

    def get(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("get", kwargs))
        return []

    def describe_collection(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe", kwargs))
        return {"fields": []}


def test_milvus_adapter_exposes_no_mutation_or_raw_sdk_surface() -> None:
    client = FakeMilvusClient()
    config = broker.MilvusReadOnlyConfig.from_env({})
    adapter = broker.MilvusReadOnlyAdapter(config, client=client)

    adapter.search(
        collection="siq_hk_reports",
        vector=[0.1],
        vector_field="vector",
        output_fields=["metadata"],
        limit=1,
        expr="",
    )
    adapter.query(
        collection="siq_hk_reports",
        output_fields=["metadata"],
        limit=1,
        expr='batch_tag == "fixture"',
    )

    adapter.get(
        collection="siq_hk_reports",
        ids=["chunk-1"],
        output_fields=["metadata"],
    )
    adapter.describe(collection="siq_hk_reports")

    assert [name for name, _ in client.calls] == ["search", "query", "get", "describe"]
    for method in ("insert", "upsert", "delete", "drop_collection", "raw_client"):
        assert not hasattr(adapter, method)


def test_security_audit_sink_records_projection_without_request_or_result_body(tmp_path: Path) -> None:
    sink = broker.SecurityAuditSink(project_root=tmp_path)
    sink.record(
        scope="postgresql.schema",
        target="pdf2md",
        decision="allow",
        error_code="",
        duration_ms=7,
    )

    audit_file = next((tmp_path / "var" / "openshell" / "audit").glob("*.jsonl"))
    record = json.loads(audit_file.read_text(encoding="utf-8"))
    assert record["operation_class"] == "database.query"
    assert record["target"]["scope"] == "postgresql.schema"
    assert record["target"]["projection"] != "pdf2md"
    serialized = json.dumps(record)
    assert "SELECT" not in serialized
    assert "request_body" not in serialized
    assert "vector" not in serialized.lower()
    assert '"rows"' not in serialized.lower()


def test_security_audit_sink_uses_verified_request_identity(tmp_path: Path) -> None:
    identity_key = bytes(range(broker.broker_request_identity.KEY_BYTES))
    identity = broker.broker_request_identity.verify_identity(
        broker.broker_request_identity.sign_identity(
            identity_key,
            profile="siq_analysis",
            run_id="run-signed",
            sandbox_id="siq-analysis-run-signed",
            session_id="session-signed",
            policy_digest="a" * 64,
            run_nonce_digest="b" * 64,
            now=1_000,
            ttl_seconds=60,
        ),
        identity_key,
        now=1_010,
    )
    sink = broker.SecurityAuditSink(project_root=tmp_path)

    with broker.broker_request_identity.request_identity_context(identity):
        sink.record(
            scope="postgresql.schema",
            target="pdf2md",
            decision="allow",
            error_code="",
            duration_ms=7,
        )

    audit_file = next((tmp_path / "var" / "openshell" / "audit").glob("*.jsonl"))
    record = json.loads(audit_file.read_text(encoding="utf-8"))
    assert record["profile"] == "siq_analysis"
    assert record["sandbox_id"] == "siq-analysis-run-signed"
    assert record["siq_run_id"] == "run-signed"
    assert record["policy_digest"] == "a" * 64
    assert "session-signed" not in json.dumps(record)


def test_http_broker_requires_and_verifies_signed_identity() -> None:
    service, postgres, *_ = _service()
    identity_key = bytes(range(broker.broker_request_identity.KEY_BYTES))
    server = broker.BrokerHTTPServer(
        ("127.0.0.1", 0),
        service,
        identity_key=identity_key,
        require_identity=True,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    host, port = server.server_address
    body = json.dumps({"sql": "SELECT ticker FROM pdf2md.metrics", "schema": "pdf2md"}).encode()

    def request(identity: str | None) -> tuple[int, dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Host": f"{host}:{port}",
        }
        if identity is not None:
            headers[broker.broker_request_identity.HEADER_NAME] = identity
        connection = http.client.HTTPConnection(host, port, timeout=2)
        try:
            connection.request("POST", "/v1/postgresql/query", body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        assert request(None)[0] == 401
        assert request("invalid")[0] == 403
        wrong_audience = broker.broker_request_identity.sign_identity(
            identity_key,
            audience="siq-egress-guard",
            profile="siq_analysis",
            run_id="run-http",
            sandbox_id="siq-analysis-run-http",
            session_id="run-http",
            policy_digest="a" * 64,
            run_nonce_digest="b" * 64,
            now=int(time.time()),
            ttl_seconds=60,
        )
        assert request(wrong_audience)[0] == 403
        token = broker.broker_request_identity.sign_identity(
            identity_key,
            audience="siq-read-only-data-broker",
            profile="siq_analysis",
            run_id="run-http",
            sandbox_id="siq-analysis-run-http",
            session_id="run-http",
            policy_digest="a" * 64,
            run_nonce_digest="b" * 64,
            now=int(time.time()),
            ttl_seconds=60,
        )
        status, payload = request(token)
        assert status == 200
        assert payload["ok"] is True
        assert len(postgres.calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_broker_bind_and_route_surface_are_fixed() -> None:
    assert broker.DEFAULT_BIND_HOST == "127.0.0.1"
    assert broker.DEFAULT_PORT == 18_793
    assert broker._validate_loopback_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(broker.BrokerError, match="loopback"):
        broker._validate_loopback_host("0.0.0.0")

    service, *_ = _service()
    with pytest.raises(broker.BrokerError) as missing:
        service.dispatch("/v1/milvus/delete", {"collection": "siq_hk_reports"})
    assert missing.value.code == "route_not_found"


def test_bridge_bind_accepts_only_verified_fixed_network_gateway_and_alias() -> None:
    endpoint = broker.bridge_endpoint.BridgeEndpoint(
        network_name="siq-openshell-dev",
        network_id="a" * 64,
        subnet="172.28.0.0/16",
        gateway_ip="172.28.0.1",
    )

    host, allowed = broker.resolve_broker_binding(
        "127.0.0.1",
        bridge_bind=True,
        discoverer=lambda: endpoint,
    )

    assert host == "172.28.0.1"
    assert allowed == frozenset({"172.28.0.1", "host.openshell.internal"})
    with pytest.raises(broker.BrokerError, match="host override") as conflict:
        broker.resolve_broker_binding(
            "172.28.0.8",
            bridge_bind=True,
            discoverer=lambda: endpoint,
        )
    assert conflict.value.code == "bind_mode_conflict"


def test_bridge_discovery_failure_does_not_fall_back_to_arbitrary_private_bind() -> None:
    def fail() -> broker.bridge_endpoint.BridgeEndpoint:
        raise broker.bridge_endpoint.BridgeEndpointError("docker_inspect_failed")

    with pytest.raises(broker.BrokerError) as raised:
        broker.resolve_broker_binding("127.0.0.1", bridge_bind=True, discoverer=fail)
    assert raised.value.code == "verified_bridge_unavailable"

    with pytest.raises(broker.BrokerError, match="loopback"):
        broker.resolve_broker_binding("172.28.0.1", bridge_bind=False)


@pytest.mark.parametrize(
    ("raw_host", "allowed"),
    [
        ("host.openshell.internal:18793", True),
        ("172.28.0.1:18793", True),
        ("other.internal:18793", False),
        ("127.0.0.1:18793", False),
        ("host.openshell.internal@evil.example", False),
        (None, False),
    ],
)
def test_bridge_http_host_contract_is_exact(raw_host: str | None, allowed: bool) -> None:
    allowed_hosts = frozenset({"172.28.0.1", "host.openshell.internal"})
    assert broker._ingress_host_allowed(raw_host, allowed_hosts) is allowed
