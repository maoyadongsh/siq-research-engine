from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from scripts.openshell import siq_fetch as module


def test_default_endpoint_is_loopback_guard_only() -> None:
    assert module.validate_guard_endpoint(module.DEFAULT_GUARD_ENDPOINT) == module.DEFAULT_GUARD_ENDPOINT
    assert module.DEFAULT_GUARD_ENDPOINT == "http://127.0.0.1:18792/v1/request"


def test_broker_identity_header_is_optional_bounded_and_local_only() -> None:
    assert module.broker_identity_headers({}) == {}
    token = ".".join(("v1", "cGF5bG9hZA", "c2lnbmF0dXJl"))
    assert module.broker_identity_headers({module.IDENTITY_TOKEN_ENV: token}) == {
        module.egress_guard.broker_request_identity.HEADER_NAME: token
    }

    with pytest.raises(module.FetchClientError, match="broker_identity_token_invalid"):
        module.broker_identity_headers({module.IDENTITY_TOKEN_ENV: "Bearer secret"})
    assert module.broker_identity_headers({module.LEGACY_IDENTITY_TOKEN_ENV: token}) == {
        module.egress_guard.broker_request_identity.HEADER_NAME: token
    }
    assert module.broker_identity_headers(
        {module.IDENTITY_TOKEN_ENV: token, module.LEGACY_IDENTITY_TOKEN_ENV: "invalid"}
    ) == {module.egress_guard.broker_request_identity.HEADER_NAME: token}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:18792/v1/request",
        "http://127.0.0.1:18792/other",
        "http://127.0.0.1:18792/v1/request?next=evil",
        "http://user:pass@127.0.0.1:18792/v1/request",
        "http://public.example:18792/v1/request",
        "http://10.0.0.8:18792/v1/request",
        "http://0.0.0.0:18792/v1/request",
    ],
)
def test_guard_endpoint_rejects_non_guard_scheme_path_and_hosts(endpoint: str) -> None:
    with pytest.raises(module.FetchClientError):
        module.validate_guard_endpoint(endpoint)


def test_controlled_bridge_alias_must_be_explicit_and_internal() -> None:
    endpoint = "http://host.openshell.internal:18792/v1/request"

    assert (
        module.validate_guard_endpoint(
            endpoint,
            bridge_alias="host.openshell.internal",
        )
        == endpoint
    )
    with pytest.raises(module.FetchClientError):
        module.validate_guard_endpoint(endpoint)
    with pytest.raises(module.FetchClientError):
        module.validate_guard_endpoint(endpoint, bridge_alias="public.example")
    with pytest.raises(module.FetchClientError):
        module.validate_guard_endpoint(
            "http://other.internal:18792/v1/request",
            bridge_alias="other.internal",
        )


def test_guard_transport_honors_openshell_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    encoded = json.dumps(
        {"ok": True, "status": 200, "body_base64": "", "body_bytes": 0}
    ).encode("utf-8")

    class _Content:
        async def iter_chunked(self, _size: int):
            yield encoded

    class _Response:
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Session:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr(module.aiohttp, "TCPConnector", lambda **_kwargs: object())
    monkeypatch.setattr(module.aiohttp, "ClientSession", _Session)

    result = asyncio.run(
        module.post_to_guard(
            "http://host.openshell.internal:18792/v1/request",
            {"method": "GET", "url": "https://example.com/"},
        )
    )

    assert result["ok"] is True
    assert captured["trust_env"] is True


def test_get_posts_only_structured_metadata_to_guard_and_returns_decoded_body() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((endpoint, payload))
        return {
            "ok": True,
            "status": 200,
            "body_base64": base64.b64encode(b"report-content").decode(),
            "body_bytes": len(b"report-content"),
        }

    result = asyncio.run(
        module.run_fetch(
            endpoint=module.DEFAULT_GUARD_ENDPOINT,
            method="GET",
            target_url="https://public.example/report?q=value",
            post=fake_post,
        )
    )

    assert result.status == 200
    assert result.body == b"report-content"
    assert calls == [
        (
            module.DEFAULT_GUARD_ENDPOINT,
            {"method": "GET", "url": "https://public.example/report?q=value"},
        )
    ]


def test_post_accepts_inline_json_object_without_any_file_argument() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_post(_endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"ok": True, "status": 204, "body_base64": "", "body_bytes": 0}

    result = asyncio.run(
        module.run_fetch(
            endpoint=module.DEFAULT_GUARD_ENDPOINT,
            method="POST",
            target_url="https://public.example/events",
            json_body={"event": "completed"},
            post=fake_post,
        )
    )

    assert result.status == 204
    assert calls == [
        {
            "method": "POST",
            "url": "https://public.example/events",
            "json_body": {"event": "completed"},
        }
    ]


@pytest.mark.parametrize("target", ["/etc/passwd", "file:///etc/passwd", "@/tmp/upload", "scp://host/path"])
def test_target_cannot_be_a_local_file_or_transfer_protocol(target: str) -> None:
    called = False

    async def fake_post(_endpoint: str, _payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        raise AssertionError("guard must not be called")

    with pytest.raises(module.FetchClientError):
        asyncio.run(
            module.run_fetch(
                endpoint=module.DEFAULT_GUARD_ENDPOINT,
                method="GET",
                target_url=target,
                post=fake_post,
            )
        )
    assert called is False


def test_read_methods_reject_json_and_post_requires_json() -> None:
    async def unused(_endpoint: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not called")

    with pytest.raises(module.FetchClientError, match="read_method_body_forbidden"):
        asyncio.run(
            module.run_fetch(
                endpoint=module.DEFAULT_GUARD_ENDPOINT,
                method="GET",
                target_url="https://public.example/read",
                json_body={},
                post=unused,
            )
        )
    with pytest.raises(module.FetchClientError, match="json_body_required"):
        asyncio.run(
            module.run_fetch(
                endpoint=module.DEFAULT_GUARD_ENDPOINT,
                method="POST",
                target_url="https://public.example/events",
                post=unused,
            )
        )


def test_guard_denial_is_returned_as_stable_error_without_body() -> None:
    async def fake_post(_endpoint: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": "ssrf_non_public_ip",
            "egress": {
                "rule_id": "ssrf_non_public_ip",
                "decision": "deny",
                "host": {"scheme": "http", "hostname": "127.0.0.1", "port": 80},
            },
        }

    with pytest.raises(module.FetchClientError, match="ssrf_non_public_ip") as raised:
        asyncio.run(
            module.run_fetch(
                endpoint=module.DEFAULT_GUARD_ENDPOINT,
                method="GET",
                target_url="http://127.0.0.1/private",
                post=fake_post,
            )
        )
    assert str(raised.value) == "ssrf_non_public_ip"


@pytest.mark.parametrize(
    "response",
    [
        {"ok": True, "status": 200, "body_base64": "%%%", "body_bytes": 1},
        {"ok": True, "status": 200, "body_base64": "b2s=", "body_bytes": 3},
        {"ok": True, "status": "200", "body_base64": "", "body_bytes": 0},
        {"ok": False},
    ],
)
def test_malformed_guard_response_fails_closed(response: dict[str, Any]) -> None:
    async def fake_post(_endpoint: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return response

    with pytest.raises(module.FetchClientError, match="guard_response_invalid"):
        asyncio.run(
            module.run_fetch(
                endpoint=module.DEFAULT_GUARD_ENDPOINT,
                method="GET",
                target_url="https://public.example/read",
                post=fake_post,
            )
        )


def test_cli_has_no_local_input_or_output_file_options() -> None:
    parser = module._parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}

    assert "--input" not in option_strings
    assert "--file" not in option_strings
    assert "--upload" not in option_strings
    assert "--output" not in option_strings
