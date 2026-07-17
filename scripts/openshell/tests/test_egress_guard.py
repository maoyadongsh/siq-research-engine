from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import pytest
from aiohttp.test_utils import TestClient, TestServer

from scripts.openshell import egress_guard as module, security_audit

PUBLIC_IP = "93.184.216.34"
SECOND_PUBLIC_IP = "1.1.1.1"


def _runtime_binding() -> dict[str, str]:
    return {"allowlist_contract_sha256": "a" * 64, "source_bundle_sha256": "b" * 64}


class FakeResolver:
    def __init__(self, answers: dict[str, tuple[str, ...]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.answers.get(host, (PUBLIC_IP,))


class FakeTransport:
    def __init__(
        self,
        responses: Sequence[module.TransportResponse] | None = None,
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        delay: float = 0,
    ) -> None:
        self.responses = list(
            responses
            or [
                module.TransportResponse(
                    status=200, headers=(("Content-Type", "text/plain"),), body=b"ok", peer_ip=PUBLIC_IP
                )
            ]
        )
        self.calls: list[dict[str, Any]] = []
        self.started = started
        self.release = release
        self.delay = delay

    async def request(self, **kwargs: Any) -> module.TransportResponse:
        self.calls.append(kwargs)
        if self.started is not None:
            self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.release is not None:
            await self.release.wait()
        if not self.responses:
            raise AssertionError("unexpected transport request")
        return self.responses.pop(0)


class FakeAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail = fail

    async def record(
        self,
        decision: Any,
        *,
        error_code: str,
        duration_ms: int,
    ) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(
            {
                "decision": decision.as_dict(),
                "error_code": error_code,
                "duration_ms": duration_ms,
            }
        )


def _guard(
    *,
    resolver: FakeResolver | None = None,
    transport: FakeTransport | None = None,
    audit: FakeAudit | None = None,
    limits: module.BrokerLimits | None = None,
) -> tuple[module.EgressGuard, FakeResolver, FakeTransport, FakeAudit]:
    allowlist = module.egress_decision.load_allowlist()
    resolver = resolver or FakeResolver()
    transport = transport or FakeTransport()
    audit = audit or FakeAudit()
    limits = limits or module.BrokerLimits(request_body_bytes=allowlist.unknown_json_post_max_bytes)
    return (
        module.EgressGuard(
            allowlist=allowlist,
            resolver=resolver,
            transport=transport,
            audit_sink=audit,
            limits=limits,
        ),
        resolver,
        transport,
        audit,
    )


def _run(guard: module.EgressGuard, payload: dict[str, Any]) -> module.BrokerResponse:
    return asyncio.run(guard.fetch(payload))


def _response(
    status: int = 200,
    *,
    headers: Sequence[tuple[str, str]] = (("Content-Type", "text/plain"),),
    body: bytes = b"ok",
    peer_ip: str = PUBLIC_IP,
) -> module.TransportResponse:
    return module.TransportResponse(status=status, headers=tuple(headers), body=body, peer_ip=peer_ip)


def test_unknown_get_is_forwarded_with_dns_pinned_and_minimal_audit() -> None:
    guard, resolver, transport, audit = _guard()

    result = _run(guard, {"method": "GET", "url": "https://public.example/report?q=secret"})

    assert result.status == 200
    assert result.body == b"ok"
    assert result.decision.rule_id == "unknown_safe_read"
    assert resolver.calls == [("public.example", 443)]
    assert transport.calls[0]["resolved_ips"] == (PUBLIC_IP,)
    assert transport.calls[0]["target"].url.endswith("/report?q=secret")
    serialized_audit = json.dumps(audit.records, sort_keys=True)
    assert "report" not in serialized_audit
    assert "secret" not in serialized_audit
    assert "public.example" in serialized_audit


def test_unknown_small_json_post_is_canonicalized_audited_and_forwarded() -> None:
    guard, _, transport, audit = _guard()

    result = _run(
        guard,
        {
            "method": "POST",
            "url": "https://public.example/events",
            "json_body": {"z": 1, "a": "sensitive-value"},
        },
    )

    assert result.decision.rule_id == "unknown_json_post_audit"
    assert result.decision.decision == "audit_only"
    assert transport.calls[0]["body"] == b'{"a":"sensitive-value","z":1}'
    assert ("content-type", "application/json") in transport.calls[0]["headers"]
    assert "sensitive-value" not in json.dumps(audit.records)


@pytest.mark.parametrize(
    ("payload", "rule_id"),
    [
        (
            {
                "method": "POST",
                "url": "https://public.example/upload",
                "json_body": {"value": 1},
                "headers": {"Content-Type": "multipart/form-data"},
            },
            "broker_multipart_denied",
        ),
        (
            {
                "method": "POST",
                "url": "https://uploads.github.com/release",
                "json_body": {"value": 1},
                "headers": {"Content-Type": "application/octet-stream"},
            },
            "broker_octet_stream_denied",
        ),
        (
            {"method": "PUT", "url": "https://api.github.com/repos/x/y", "json_body": {"value": 1}},
            "broker_method_denied",
        ),
    ],
)
def test_explicit_broker_never_forwards_file_upload_shapes_or_put(payload: dict[str, Any], rule_id: str) -> None:
    guard, _, transport, audit = _guard()

    with pytest.raises(module.BrokerDenied) as raised:
        _run(guard, payload)

    assert raised.value.decision.rule_id == rule_id
    assert transport.calls == []
    assert audit.records[-1]["decision"]["decision"] == "deny"


def test_oversize_json_is_rejected_before_dns_audit_or_transport() -> None:
    guard, resolver, transport, audit = _guard()

    with pytest.raises(module.BrokerInputError, match="json_body_too_large") as raised:
        _run(
            guard,
            {
                "method": "POST",
                "url": "https://public.example/events",
                "json_body": {"value": "x" * (128 * 1024)},
            },
        )

    assert raised.value.http_status == 413
    assert resolver.calls == []
    assert transport.calls == []
    assert audit.records == []


@pytest.mark.parametrize("host", ["api.stepfun.com", "api.tavily.com", "api.exa.ai"])
def test_model_and_search_provider_endpoints_must_use_direct_openshell_route(host: str) -> None:
    guard, _, transport, audit = _guard()
    method = "POST" if host != "api.stepfun.com" else "GET"
    payload: dict[str, Any] = {"method": method, "url": f"https://{host}/v1"}
    if method == "POST":
        payload["json_body"] = {"query": "market"}

    with pytest.raises(module.BrokerDenied) as raised:
        _run(guard, payload)

    assert raised.value.decision.rule_id == "provider_direct_required"
    assert transport.calls == []
    assert audit.records[-1]["error_code"] == "provider_direct_required"


def test_unknown_destination_strips_credentials_and_hop_by_hop_headers() -> None:
    guard, _, transport, _ = _guard()

    _run(
        guard,
        {
            "method": "GET",
            "url": "https://public.example/read",
            "headers": {
                "Authorization": "Bearer never-forward",
                "Cookie": "session=never-forward",
                "Connection": "X-Hop",
                "X-Hop": "remove-me",
                "X-Trace": "keep-me",
                "Host": "attacker.example",
                "Content-Length": "999",
            },
        },
    )

    assert dict(transport.calls[0]["headers"]) == {"x-trace": "keep-me"}


def test_approved_github_keeps_auth_on_same_origin_but_strips_it_after_cross_origin_redirect() -> None:
    transport = FakeTransport(
        [
            _response(302, headers=(("Location", "https://public.example/final"),)),
            _response(peer_ip=SECOND_PUBLIC_IP),
        ]
    )
    resolver = FakeResolver({"api.github.com": (PUBLIC_IP,), "public.example": (SECOND_PUBLIC_IP,)})
    guard, _, _, _ = _guard(resolver=resolver, transport=transport)

    _run(
        guard,
        {
            "method": "GET",
            "url": "https://api.github.com/repos/x/y",
            "headers": {"Authorization": "Bearer approved-only", "Cookie": "sid=approved-only"},
        },
    )

    assert dict(transport.calls[0]["headers"])["authorization"] == "Bearer approved-only"
    assert "authorization" not in dict(transport.calls[1]["headers"])
    assert "cookie" not in dict(transport.calls[1]["headers"])


def test_redirect_is_resolved_decided_and_pinned_at_every_hop() -> None:
    resolver = FakeResolver({"first.example": (PUBLIC_IP,), "second.example": (SECOND_PUBLIC_IP,)})
    transport = FakeTransport(
        [
            _response(302, headers=(("Location", "https://second.example/final"),)),
            _response(peer_ip=SECOND_PUBLIC_IP),
        ]
    )
    guard, _, _, audit = _guard(resolver=resolver, transport=transport)

    result = _run(guard, {"method": "GET", "url": "https://first.example/start"})

    assert result.redirect_hops == 1
    assert resolver.calls == [("first.example", 443), ("second.example", 443)]
    assert transport.calls[0]["resolved_ips"] == (PUBLIC_IP,)
    assert transport.calls[1]["resolved_ips"] == (SECOND_PUBLIC_IP,)
    assert [record["decision"]["host"]["hostname"] for record in audit.records] == [
        "first.example",
        "second.example",
    ]


def test_redirect_to_metadata_or_private_ip_is_denied_before_second_transport() -> None:
    for location in ("http://169.254.169.254/latest", "http://metadata.google.internal/latest"):
        resolver = FakeResolver({"public.example": (PUBLIC_IP,), "169.254.169.254": ("169.254.169.254",)})
        transport = FakeTransport([_response(302, headers=(("Location", location),))])
        guard, _, _, audit = _guard(resolver=resolver, transport=transport)

        with pytest.raises(module.BrokerDenied) as raised:
            _run(guard, {"method": "GET", "url": "https://public.example/start"})

        assert raised.value.decision.rule_id in {"ssrf_non_public_ip", "ssrf_metadata_host"}
        assert len(transport.calls) == 1
        assert audit.records[-1]["decision"]["decision"] == "deny"
        if "metadata.google" in location:
            assert all(call[0] != "metadata.google.internal" for call in resolver.calls)


def test_dns_answer_with_any_private_address_is_denied() -> None:
    resolver = FakeResolver({"public.example": (PUBLIC_IP, "10.0.0.8")})
    guard, _, transport, _ = _guard(resolver=resolver)

    with pytest.raises(module.BrokerDenied, match="ssrf_non_public_ip"):
        _run(guard, {"method": "GET", "url": "https://public.example/read"})

    assert transport.calls == []


def test_transport_peer_must_match_the_exact_dns_projection() -> None:
    transport = FakeTransport([_response(peer_ip=SECOND_PUBLIC_IP)])
    guard, _, _, _ = _guard(transport=transport)

    with pytest.raises(module.BrokerUpstreamError, match="upstream_peer_mismatch"):
        _run(guard, {"method": "GET", "url": "https://public.example/read"})


@pytest.mark.parametrize("status", [301, 302, 303])
def test_post_redirect_to_get_drops_body_and_content_type(status: int) -> None:
    transport = FakeTransport(
        [
            _response(status, headers=(("Location", "/next"),)),
            _response(),
        ]
    )
    guard, _, _, _ = _guard(transport=transport)

    _run(
        guard,
        {"method": "POST", "url": "https://public.example/start", "json_body": {"value": 1}},
    )

    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[1]["method"] == "GET"
    assert transport.calls[1]["body"] is None
    assert "content-type" not in dict(transport.calls[1]["headers"])


def test_307_redirect_preserves_structured_json_body() -> None:
    transport = FakeTransport(
        [
            _response(307, headers=(("Location", "/next"),)),
            _response(),
        ]
    )
    guard, _, _, _ = _guard(transport=transport)

    _run(
        guard,
        {"method": "POST", "url": "https://public.example/start", "json_body": {"value": 1}},
    )

    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[1]["body"] == b'{"value":1}'


def test_redirect_loop_is_denied_without_repeating_transport() -> None:
    transport = FakeTransport([_response(302, headers=(("Location", "/start"),))])
    guard, _, _, audit = _guard(transport=transport)

    with pytest.raises(module.BrokerDenied, match="redirect_loop_denied"):
        _run(guard, {"method": "GET", "url": "https://public.example/start"})

    assert len(transport.calls) == 1
    assert audit.records[-1]["error_code"] == "redirect_loop_denied"


def test_fake_transport_cannot_bypass_response_size_or_header_limits() -> None:
    allowlist = module.egress_decision.load_allowlist()
    limits = module.BrokerLimits(
        request_body_bytes=allowlist.unknown_json_post_max_bytes,
        response_body_bytes=4,
        response_header_bytes=32,
    )
    body_transport = FakeTransport([_response(body=b"12345")])
    guard, _, _, _ = _guard(transport=body_transport, limits=limits)
    with pytest.raises(module.BrokerUpstreamError, match="response_body_too_large"):
        _run(guard, {"method": "GET", "url": "https://public.example/read"})

    header_transport = FakeTransport([_response(headers=(("X-Large", "x" * 40),))])
    guard, _, _, _ = _guard(transport=header_transport, limits=limits)
    with pytest.raises(module.BrokerUpstreamError, match="response_headers_too_large"):
        _run(guard, {"method": "GET", "url": "https://public.example/read"})


def test_response_hop_headers_and_set_cookie_are_not_returned_to_sandbox() -> None:
    transport = FakeTransport(
        [
            _response(
                headers=(
                    ("Connection", "X-Hop"),
                    ("X-Hop", "remove"),
                    ("Set-Cookie", "sid=remove"),
                    ("Content-Type", "text/plain"),
                )
            )
        ]
    )
    guard, _, _, _ = _guard(transport=transport)

    result = _run(guard, {"method": "GET", "url": "https://public.example/read"})

    assert result.headers == (("Content-Type", "text/plain"),)


def test_audit_failure_is_fail_closed_before_network() -> None:
    audit = FakeAudit(fail=True)
    guard, _, transport, _ = _guard(audit=audit)

    with pytest.raises(module.BrokerUpstreamError, match="security_audit_failed"):
        _run(guard, {"method": "GET", "url": "https://public.example/read"})

    assert transport.calls == []


def test_security_audit_sink_never_persists_url_path_query_body_or_credentials(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    context = security_audit.SecurityRunContext(
        profile="siq_analysis",
        sandbox_id="sandbox-test",
        run_id="run-test",
        session_id="session-secret",
        policy_digest="a" * 64,
    )
    sink = module.SecurityAuditSink(project_root=project, context=context, sync=False)
    decision = module.egress_decision.EgressDecision(
        rule_id="unknown_json_post_audit",
        decision="audit_only",
        host={"scheme": "https", "hostname": "public.example", "port": 443},
    )

    asyncio.run(sink.record(decision, error_code="", duration_ms=1))

    serialized = next((project / "var/openshell/audit").glob("*.jsonl")).read_text(encoding="utf-8")
    for forbidden in ("public.example", "session-secret", "/reports/private", "authorization", "cookie", "body"):
        assert forbidden not in serialized.lower()


def test_security_audit_sink_uses_verified_request_identity(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    default_context = security_audit.SecurityRunContext(
        profile="siq_analysis",
        sandbox_id="host-egress-broker",
        run_id="host-egress-broker",
        session_id="egress-broker",
        policy_digest="c" * 64,
    )
    key = bytes(range(module.broker_request_identity.KEY_BYTES))
    identity = module.broker_request_identity.verify_identity(
        module.broker_request_identity.sign_identity(
            key,
            profile="siq_analysis",
            run_id="run-signed",
            sandbox_id="siq-analysis-run-signed",
            session_id="session-signed",
            policy_digest="a" * 64,
            run_nonce_digest="b" * 64,
            now=1_000,
            ttl_seconds=60,
        ),
        key,
        now=1_010,
    )
    sink = module.SecurityAuditSink(project_root=project, context=default_context, sync=False)
    decision = module.egress_decision.EgressDecision(
        rule_id="unknown_json_post_audit",
        decision="audit_only",
        host={"scheme": "https", "hostname": "public.example", "port": 443},
    )

    with module.broker_request_identity.request_identity_context(identity):
        asyncio.run(sink.record(decision, error_code="", duration_ms=1))

    record = json.loads(next((project / "var/openshell/audit").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert record["sandbox_id"] == "siq-analysis-run-signed"
    assert record["siq_run_id"] == "run-signed"
    assert record["policy_digest"] == "a" * 64
    assert "session-signed" not in json.dumps(record)


def test_http_app_requires_and_verifies_signed_identity() -> None:
    async def scenario() -> None:
        guard, _, transport, audit = _guard()
        key = bytes(range(module.broker_request_identity.KEY_BYTES))
        app = module.create_app(
            guard,
            allowed_ingress_hosts=frozenset({"127.0.0.1"}),
            identity_key=key,
            require_identity=True,
            runtime_binding=_runtime_binding(),
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            health = await client.get("/health")
            assert health.status == 200
            health_value = await health.json()
            assert health_value["allowlist_contract_sha256"] == "a" * 64
            assert health_value["source_bundle_sha256"] == "b" * 64
            missing = await client.post("/v1/request", json={"method": "GET", "url": "https://public.example/read"})
            assert missing.status == 401
            invalid = await client.post(
                "/v1/request",
                json={"method": "GET", "url": "https://public.example/read"},
                headers={module.broker_request_identity.HEADER_NAME: "invalid"},
            )
            assert invalid.status == 403
            wrong_audience = module.broker_request_identity.sign_identity(
                key,
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
            denied = await client.post(
                "/v1/request",
                json={"method": "GET", "url": "https://public.example/read"},
                headers={module.broker_request_identity.HEADER_NAME: wrong_audience},
            )
            assert denied.status == 403
            token = module.broker_request_identity.sign_identity(
                key,
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
            accepted = await client.post(
                "/v1/request",
                json={"method": "GET", "url": "https://public.example/read"},
                headers={module.broker_request_identity.HEADER_NAME: token},
            )
            assert accepted.status == 200
            assert (await accepted.json())["ok"] is True
            assert len(transport.calls) == 1
            oversized = await client.post(
                "/v1/request",
                json={
                    "method": "POST",
                    "url": "https://public.example/events",
                    "json_body": {"probe": "x" * (128 * 1024)},
                },
                headers={module.broker_request_identity.HEADER_NAME: token},
            )
            assert oversized.status == 413
            assert await oversized.json() == {"ok": False, "error_code": "json_body_too_large"}
            assert len(transport.calls) == 1
            assert audit.records[-1]["decision"]["rule_id"] == "json_body_too_large"
            assert audit.records[-1]["decision"]["decision"] == "deny"
            assert audit.records[-1]["error_code"] == "json_body_too_large"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_http_parse_denial_audit_uses_verified_identity_and_no_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "repo"
        project.mkdir()
        default_context = security_audit.SecurityRunContext(
            profile="siq_analysis",
            sandbox_id="host-egress-broker",
            run_id="host-egress-broker",
            session_id="host-egress-broker",
            policy_digest="c" * 64,
        )
        allowlist = module.egress_decision.load_allowlist()
        resolver = FakeResolver()
        transport = FakeTransport()
        guard = module.EgressGuard(
            allowlist=allowlist,
            resolver=resolver,
            transport=transport,
            audit_sink=module.SecurityAuditSink(project_root=project, context=default_context, sync=False),
        )
        key = bytes(range(module.broker_request_identity.KEY_BYTES))
        token = module.broker_request_identity.sign_identity(
            key,
            audience="siq-egress-guard",
            profile="siq_analysis",
            run_id="run-parse-denial",
            sandbox_id="siq-analysis-run-parse-denial",
            session_id="session-parse-denial",
            policy_digest="a" * 64,
            run_nonce_digest="b" * 64,
            now=int(time.time()),
            ttl_seconds=60,
        )
        app = module.create_app(
            guard,
            allowed_ingress_hosts=frozenset({"127.0.0.1"}),
            identity_key=key,
            require_identity=True,
            runtime_binding=_runtime_binding(),
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                "/v1/request",
                json={
                    "method": "POST",
                    "url": "https://public.example/events",
                    "json_body": {"probe": "x" * (128 * 1024)},
                },
                headers={module.broker_request_identity.HEADER_NAME: token},
            )
            assert response.status == 413
            assert transport.calls == []
        finally:
            await client.close()

        raw = next((project / security_audit.AUDIT_RELATIVE_ROOT).glob("*.jsonl")).read_text(encoding="utf-8")
        record = json.loads(raw)
        assert record["siq_run_id"] == "run-parse-denial"
        assert record["sandbox_id"] == "siq-analysis-run-parse-denial"
        assert record["policy_digest"] == "a" * 64
        assert record["decision"] == "deny"
        assert record["error_code"] == "json_body_too_large"
        assert "session-parse-denial" not in raw
        assert "x" * 128 not in raw
        assert "public.example" not in raw

    asyncio.run(scenario())


def test_total_timeout_and_concurrency_queue_are_bounded() -> None:
    async def scenario() -> None:
        allowlist = module.egress_decision.load_allowlist()
        started = asyncio.Event()
        release = asyncio.Event()
        limits = module.BrokerLimits(
            request_body_bytes=allowlist.unknown_json_post_max_bytes,
            max_concurrency=1,
            queue_timeout_seconds=0.01,
            total_timeout_seconds=1,
        )
        transport = FakeTransport(started=started, release=release)
        guard, _, _, _ = _guard(transport=transport, limits=limits)
        first = asyncio.create_task(guard.fetch({"method": "GET", "url": "https://first.example/read"}))
        await started.wait()
        with pytest.raises(module.BrokerBusyError):
            await guard.fetch({"method": "GET", "url": "https://second.example/read"})
        release.set()
        await first

        timeout_limits = replace(limits, total_timeout_seconds=0.01, queue_timeout_seconds=1)
        slow_guard, _, _, _ = _guard(transport=FakeTransport(delay=0.1), limits=timeout_limits)
        with pytest.raises(module.BrokerUpstreamError, match="broker_request_timeout"):
            await slow_guard.fetch({"method": "GET", "url": "https://public.example/read"})

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("bind_host", "bridge", "expected"),
    [
        ("127.0.0.1", None, "127.0.0.1"),
        ("::1", None, "::1"),
        (
            "172.20.0.1",
            module.bridge_endpoint.BridgeEndpoint(
                network_name="siq-openshell-dev",
                network_id="a" * 64,
                subnet="172.20.0.0/16",
                gateway_ip="172.20.0.1",
            ),
            "172.20.0.1",
        ),
    ],
)
def test_binding_allows_only_loopback_or_explicit_controlled_bridge(
    bind_host: str,
    bridge: module.bridge_endpoint.BridgeEndpoint | None,
    expected: str,
) -> None:
    actual, hosts = module.validate_binding(bind_host, bridge)
    assert actual == expected
    assert expected in hosts
    if bridge is not None:
        assert hosts == frozenset({expected, "host.openshell.internal"})


@pytest.mark.parametrize(
    "bind_host",
    [
        "0.0.0.0",
        "::",
        PUBLIC_IP,
        "172.20.0.1",
        "host.openshell.internal",
        "evil.example",
    ],
)
def test_binding_rejects_wildcard_public_or_uncontrolled_addresses(bind_host: str) -> None:
    with pytest.raises(module.BrokerInputError):
        module.validate_binding(bind_host)


def test_binding_rejects_private_ip_from_other_verified_network_gateway() -> None:
    bridge = module.bridge_endpoint.BridgeEndpoint(
        network_name="siq-openshell-dev",
        network_id="a" * 64,
        subnet="172.20.0.0/16",
        gateway_ip="172.20.0.1",
    )
    with pytest.raises(module.BrokerInputError, match="bridge_mismatch"):
        module.validate_binding("172.20.0.2", bridge)


def test_http_app_has_only_health_and_explicit_request_routes() -> None:
    guard, _, _, _ = _guard()
    app = module.create_app(
        guard,
        allowed_ingress_hosts=frozenset({"127.0.0.1"}),
        runtime_binding=_runtime_binding(),
    )
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}

    assert ("POST", "/v1/request") in routes
    assert ("GET", "/health") in routes
    assert all(method != "CONNECT" for method, _ in routes)
    assert all("proxy" not in path for _, path in routes)


def test_runtime_bindings_cover_loaded_allowlist_and_broker_modules() -> None:
    allowlist = module.egress_decision.load_allowlist()

    assert len(module.allowlist_contract_sha256(allowlist)) == 64
    assert len(module.runtime_source_bundle_sha256()) == 64


def test_mihomo_socket_allows_root_owned_sticky_tmp_parent(tmp_path: Path) -> None:
    path = tmp_path / "mihomo.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        assert module._validate_mihomo_socket(path) == path
    finally:
        listener.close()
        path.unlink(missing_ok=True)


def test_mihomo_socket_rejects_non_socket_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "not-a-socket"
    path.write_text("unsafe\n", encoding="utf-8")

    with pytest.raises(module.BrokerUpstreamError, match="mihomo_control_socket_unsafe"):
        module._validate_mihomo_socket(path)


def test_mihomo_resolver_replaces_only_an_all_fake_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    system = FakeResolver({"public.example": ("198.18.0.42",)})
    resolver = module.MihomoFakeIPDNSResolver(
        system_resolver=system,
        control_socket=Path("/tmp/mihomo.sock"),
        fake_ip_network=module.parse_mihomo_fake_ip_network("198.18.0.0/16"),
    )
    calls: list[tuple[Path, str]] = []

    async def query(path: Path, host: str) -> tuple[str, ...]:
        calls.append((path, host))
        return (PUBLIC_IP,)

    monkeypatch.setattr(module, "_validate_mihomo_socket", lambda path: path)
    monkeypatch.setattr(resolver, "_query_control", query)

    result = asyncio.run(resolver.resolve("public.example", 443))

    assert result == module.DNSResolution(
        addresses=(PUBLIC_IP,),
        provenance_rule_id="mihomo_fake_ip_compat_resolved",
    )
    assert calls == [(Path("/tmp/mihomo.sock"), "public.example")]


def test_mihomo_resolver_does_not_query_control_for_public_system_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = module.MihomoFakeIPDNSResolver(
        system_resolver=FakeResolver({"public.example": (PUBLIC_IP,)}),
        control_socket=Path("/tmp/mihomo.sock"),
        fake_ip_network=module.parse_mihomo_fake_ip_network("198.18.0.0/16"),
    )

    async def unexpected(*_args: Any) -> tuple[str, ...]:
        raise AssertionError("Mihomo control must not be queried")

    monkeypatch.setattr(resolver, "_query_control", unexpected)

    assert asyncio.run(resolver.resolve("public.example", 443)) == (PUBLIC_IP,)


@pytest.mark.parametrize(
    ("host", "answers", "error"),
    [
        ("public.example", ("198.18.0.42", PUBLIC_IP), "mihomo_mixed_fake_ip_projection"),
        ("198.18.0.42", ("198.18.0.42",), "mihomo_fake_ip_literal_denied"),
    ],
)
def test_mihomo_resolver_fails_closed_for_mixed_or_literal_fake_ip(
    host: str,
    answers: tuple[str, ...],
    error: str,
) -> None:
    resolver = module.MihomoFakeIPDNSResolver(
        system_resolver=FakeResolver({host: answers}),
        control_socket=Path("/tmp/mihomo.sock"),
        fake_ip_network=module.parse_mihomo_fake_ip_network("198.18.0.0/16"),
    )

    with pytest.raises(module.BrokerUpstreamError, match=error):
        asyncio.run(resolver.resolve(host, 443))


@pytest.mark.parametrize("value", ["198.18.0.0/15", "198.17.0.0/16", "203.0.113.0/24", "::/64"])
def test_mihomo_fake_ip_range_is_explicit_and_bounded(value: str) -> None:
    with pytest.raises(module.BrokerInputError, match="mihomo_fake_ip_range_invalid"):
        module.parse_mihomo_fake_ip_network(value)


def test_mihomo_dns_response_accepts_owned_cname_and_rejects_unrelated_address() -> None:
    base = {
        "Status": 0,
        "TC": False,
        "Question": [{"Name": "public.example.", "Qtype": 1, "Qclass": 1}],
    }
    owned = {
        **base,
        "Answer": [
            {"name": "public.example.", "type": 5, "data": "edge.example.net."},
            {"name": "edge.example.net.", "type": 1, "data": PUBLIC_IP},
        ],
    }
    unrelated = {
        **base,
        "Answer": [{"name": "unrelated.example.", "type": 1, "data": PUBLIC_IP}],
    }

    assert module._parse_mihomo_dns_response(owned, "public.example", 1) == (PUBLIC_IP,)
    with pytest.raises(module.BrokerUpstreamError, match="mihomo_dns_response_invalid"):
        module._parse_mihomo_dns_response(unrelated, "public.example", 1)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_pinned_transport_captures_peer_before_short_response_detaches(method: str) -> None:
    async def scenario() -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            request = await reader.readuntil(b"\r\n\r\n")
            body = b"" if request.startswith(b"HEAD ") else b"ok"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            result = await module.AiohttpPinnedTransport().request(
                method=method,
                target=module.ParsedTarget(
                    url=f"http://public.example:{port}/",
                    scheme="http",
                    host="public.example",
                    port=port,
                ),
                headers=(),
                body=None,
                resolved_ips=("127.0.0.1",),
                limits=module.BrokerLimits(request_body_bytes=128 * 1024),
            )
            assert result.status == 200
            assert result.peer_ip == "127.0.0.1"
            assert result.body == (b"ok" if method == "GET" else b"")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
