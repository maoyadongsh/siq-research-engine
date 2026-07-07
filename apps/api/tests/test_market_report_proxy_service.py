import asyncio
import importlib.util
import sys
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("market_reports", BACKEND_ROOT / "routers" / "market_reports.py")
market_reports = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market_reports)
market_report_proxy = market_reports.market_report_proxy


def test_proxy_request_preserves_query_body_content_type_and_response(monkeypatch):
    seen = {}

    class QueryParams:
        def multi_items(self):
            return [("ticker", "AAPL"), ("ticker", "MSFT"), ("limit", "2")]

    class Request:
        method = "POST"
        query_params = QueryParams()
        headers = {"content-type": "application/json; charset=utf-8"}

        async def body(self):
            return b'{"q":"annual"}'

    class FakeAsyncClient:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, params, content, headers):
            seen.update(
                {
                    "method": method,
                    "url": url,
                    "params": params,
                    "content": content,
                    "headers": headers,
                }
            )
            return type(
                "Upstream",
                (),
                {
                    "content": b'{"ok":true}',
                    "status_code": 207,
                    "headers": {"content-type": "application/vnd.finder+json"},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_request(
            base_url="http://finder",
            upstream_path="/v1/reports/recent",
            request=Request(),
            timeout=1.25,
        )
    )

    assert seen == {
        "timeout": 1.25,
        "method": "POST",
        "url": "http://finder/v1/reports/recent",
        "params": [("ticker", "AAPL"), ("ticker", "MSFT"), ("limit", "2")],
        "content": b'{"q":"annual"}',
        "headers": {"content-type": "application/json; charset=utf-8"},
    }
    assert response.status_code == 207
    assert response.media_type == "application/vnd.finder+json"
    assert response.body == b'{"ok":true}'


def test_proxy_request_forwards_service_token_header(monkeypatch):
    seen = {}

    class QueryParams:
        def multi_items(self):
            return []

    class Request:
        method = "GET"
        query_params = QueryParams()
        headers = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, params, content, headers):
            seen.update(
                {
                    "method": method,
                    "url": url,
                    "params": params,
                    "content": content,
                    "headers": headers,
                }
            )
            return type(
                "Upstream",
                (),
                {
                    "content": b'{"ok":true}',
                    "status_code": 200,
                    "headers": {"content-type": "application/json"},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_request(
            base_url="http://finder",
            upstream_path="/v1/reports/recent",
            request=Request(),
            timeout=1.25,
            service_token="finder-token",
        )
    )

    assert seen == {
        "timeout": 1.25,
        "method": "GET",
        "url": "http://finder/v1/reports/recent",
        "params": [],
        "content": None,
        "headers": {"X-SIQ-Service-Token": "finder-token"},
    }
    assert response.status_code == 200


def test_proxy_request_head_discards_upstream_body(monkeypatch):
    class QueryParams:
        def multi_items(self):
            return []

    class Request:
        method = "HEAD"
        query_params = QueryParams()
        headers = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, params, content, headers):
            assert method == "HEAD"
            assert content is None
            return type(
                "Upstream",
                (),
                {
                    "content": b"should-not-leak",
                    "status_code": 204,
                    "headers": {},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_request(
            base_url="http://finder",
            upstream_path="/v1/ping",
            request=Request(),
            timeout=1.0,
        )
    )

    assert response.status_code == 204
    assert response.media_type == "application/octet-stream"
    assert response.body == b""


def test_proxy_request_maps_request_error_to_502(monkeypatch):
    class QueryParams:
        def multi_items(self):
            return []

    class Request:
        method = "GET"
        query_params = QueryParams()
        headers = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, params, content, headers):
            raise market_report_proxy.httpx.RequestError("offline")

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    try:
        asyncio.run(
            market_report_proxy.proxy_request(
                base_url="http://finder",
                upstream_path="/v1/ping",
                request=Request(),
                timeout=1.0,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "offline" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_finder_assist_handles_empty_and_error_response(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, *, status_code=200, content=b"", text="", payload=None):
            self.status_code = status_code
            self.content = content
            self.text = text
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeAsyncClient:
        responses = [
            FakeResponse(content=b"", payload={"ignored": True}),
            FakeResponse(status_code=503, content=b"fail", text="upstream failed"),
        ]

        def __init__(self, timeout):
            calls.append(("timeout", timeout))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json):
            calls.append(("post", url, json))
            return self.responses.pop(0)

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_report_proxy.finder_assist(
            report_finder_base="http://finder",
            payload={"prompt": "demo"},
            timeout=2.5,
        )
    )
    assert result == {}

    try:
        asyncio.run(
            market_report_proxy.finder_assist(
                report_finder_base="http://finder",
                payload={"prompt": "demo"},
                timeout=2.5,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "upstream failed"
    else:
        raise AssertionError("expected HTTPException")

    assert calls[0] == ("timeout", 2.5)
    assert calls[1] == ("post", "http://finder/v1/reports/assist", {"prompt": "demo"})


def test_finder_assist_maps_malformed_json_to_502(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b"{not-json"
        text = "{not-json"

        def json(self):
            raise ValueError("bad json")

    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 2.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json):
            assert url == "http://finder/v1/reports/assist"
            assert json == {"prompt": "demo"}
            return FakeResponse()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    try:
        asyncio.run(
            market_report_proxy.finder_assist(
                report_finder_base="http://finder",
                payload={"prompt": "demo"},
                timeout=2.5,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert exc.detail == "Market report assist returned invalid JSON"
    else:
        raise AssertionError("expected HTTPException")


def test_finder_assist_returns_object_json(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b'{"answer":"ok"}'
        text = '{"answer":"ok"}'

        def json(self):
            return {"answer": "ok", "sources": ["finder"]}

    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 2.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json):
            assert url == "http://finder/v1/reports/assist"
            assert json == {"prompt": "demo"}
            return FakeResponse()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_report_proxy.finder_assist(
            report_finder_base="http://finder",
            payload={"prompt": "demo"},
            timeout=2.5,
        )
    )

    assert result == {"answer": "ok", "sources": ["finder"]}


def test_finder_assist_returns_empty_for_non_object_json(monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b'["not", "object"]'
        text = '["not", "object"]'

        def json(self):
            return ["not", "object"]

    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 2.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json):
            assert url == "http://finder/v1/reports/assist"
            assert json == {"prompt": "demo"}
            return FakeResponse()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_report_proxy.finder_assist(
            report_finder_base="http://finder",
            payload={"prompt": "demo"},
            timeout=2.5,
        )
    )

    assert result == {}


def test_finder_assist_maps_request_error_to_502(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 2.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json):
            assert url == "http://finder/v1/reports/assist"
            assert json == {"prompt": "demo"}
            raise market_report_proxy.httpx.RequestError("assist offline")

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    try:
        asyncio.run(
            market_report_proxy.finder_assist(
                report_finder_base="http://finder",
                payload={"prompt": "demo"},
                timeout=2.5,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "Market report assist upstream unavailable" in exc.detail
        assert "assist offline" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_proxy_rules_get_preserves_response_contract(monkeypatch):
    seen = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            seen["url"] = url
            return type(
                "Upstream",
                (),
                {
                    "content": b'{"rules":[1]}',
                    "status_code": 206,
                    "headers": {"content-type": "application/vnd.rules+json"},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_rules_get(
            market_rules_base="http://rules",
            upstream_path="/api/rules?market=HK",
            timeout=3.5,
        )
    )

    assert seen == {"timeout": 3.5, "url": "http://rules/api/rules?market=HK"}
    assert response.status_code == 206
    assert response.media_type == "application/vnd.rules+json"
    assert response.body == b'{"rules":[1]}'


def test_proxy_rules_get_forwards_service_token_header(monkeypatch):
    seen = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, *, headers):
            seen["url"] = url
            seen["headers"] = headers
            return type(
                "Upstream",
                (),
                {
                    "content": b'{"rules":[1]}',
                    "status_code": 200,
                    "headers": {"content-type": "application/json"},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_rules_get(
            market_rules_base="http://rules",
            upstream_path="/markets",
            timeout=3.5,
            service_token="rules-token",
        )
    )

    assert seen == {
        "timeout": 3.5,
        "url": "http://rules/markets",
        "headers": {"X-SIQ-Service-Token": "rules-token"},
    }
    assert response.status_code == 200


def test_proxy_rules_get_maps_request_error_to_502(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 3.5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            assert url == "http://rules/api/rules"
            raise market_report_proxy.httpx.RequestError("rules offline")

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    try:
        asyncio.run(
            market_report_proxy.proxy_rules_get(
                market_rules_base="http://rules",
                upstream_path="/api/rules",
                timeout=3.5,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "Market rules service unavailable" in exc.detail
        assert "rules offline" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_market_report_health_tolerates_malformed_finder_json(monkeypatch):
    class FakeResponse:
        def __init__(self, *, status_code, payload=None, json_error=False):
            self.status_code = status_code
            self._payload = payload or {}
            self._json_error = json_error

        def json(self):
            if self._json_error:
                raise ValueError("bad json")
            return self._payload

    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 5.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            if url.endswith("/health"):
                return FakeResponse(status_code=200, json_error=True)
            if url.endswith("/healthz"):
                return FakeResponse(status_code=503)
            raise AssertionError(url)

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_report_proxy.market_report_health(
            report_finder_base="http://finder",
            market_rules_base="http://rules",
        )
    )

    assert result["report_finder"] == {"status": "ok", "code": 200, "config": {}, "markets": {}}
    assert result["market_rules"] == {"status": "error", "code": 503}


def test_market_report_health_keeps_single_side_request_errors_isolated(monkeypatch):
    class FakeResponse:
        def __init__(self, *, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 5.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            if url == "http://finder/health":
                raise market_report_proxy.httpx.RequestError("finder offline")
            if url == "http://rules/healthz":
                return FakeResponse(status_code=200)
            raise AssertionError(url)

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_report_proxy.market_report_health(
            report_finder_base="http://finder",
            market_rules_base="http://rules",
        )
    )

    assert result["report_finder"] == {"status": "error", "error": "finder offline"}
    assert result["market_rules"] == {"status": "ok", "code": 200}


def test_router_proxy_wrappers_use_configured_bases(monkeypatch):
    seen = {}

    async def fake_finder_assist(*, report_finder_base, payload, timeout, service_token):
        seen["assist"] = {
            "report_finder_base": report_finder_base,
            "payload": payload,
            "timeout": timeout,
            "service_token": service_token,
        }
        return {"ok": True}

    async def fake_proxy_rules_get(*, market_rules_base, upstream_path, service_token):
        seen.setdefault("rules", []).append(
            {"market_rules_base": market_rules_base, "upstream_path": upstream_path, "service_token": service_token}
        )
        return {"path": upstream_path}

    async def fake_market_report_health(*, report_finder_base, market_rules_base):
        seen["health"] = {"report_finder_base": report_finder_base, "market_rules_base": market_rules_base}
        return {"ok": True}

    monkeypatch.setattr(market_report_proxy, "finder_assist", fake_finder_assist)
    monkeypatch.setattr(market_report_proxy, "proxy_rules_get", fake_proxy_rules_get)
    monkeypatch.setattr(market_report_proxy, "market_report_health", fake_market_report_health)

    assert asyncio.run(market_reports._finder_assist({"prompt": "demo"})) == {"ok": True}
    assert asyncio.run(market_reports.market_modules()) == {"path": "/markets"}
    assert asyncio.run(market_reports.cn_market_rules()) == {"path": "/markets/cn/rules"}
    assert asyncio.run(market_reports.market_report_health()) == {"ok": True}
    assert seen == {
        "assist": {
            "report_finder_base": market_reports.REPORT_FINDER_BASE,
            "payload": {"prompt": "demo"},
            "timeout": market_reports.MARKET_REPORT_PROXY_TIMEOUT,
            "service_token": None,
        },
        "rules": [
            {"market_rules_base": market_reports.MARKET_RULES_BASE, "upstream_path": "/markets", "service_token": None},
            {
                "market_rules_base": market_reports.MARKET_RULES_BASE,
                "upstream_path": "/markets/cn/rules",
                "service_token": None,
            },
        ],
        "health": {
            "report_finder_base": market_reports.REPORT_FINDER_BASE,
            "market_rules_base": market_reports.MARKET_RULES_BASE,
        },
    }
