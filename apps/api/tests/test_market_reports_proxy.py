import asyncio
import hashlib
import importlib.util
import io
import json
import sys
import threading
import types
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("market_reports", BACKEND_ROOT / "routers" / "market_reports.py")
market_reports = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market_reports)
market_report_proxy = market_reports.market_report_proxy
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole


class DummyRequest:
    method = "POST"
    query_params = {}
    headers = {"content-type": "application/json"}

    async def body(self):
        return b'{"company_name":"Demo"}'


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class UploadRouteRequest:
    pass


class _AsyncUpload:
    content_type = "application/pdf"

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content
        self._offset = 0

    async def read(self, size: int) -> bytes:
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _write_market_package(root: Path, *parts: str) -> Path:
    package_dir = root.joinpath(*parts)
    package_dir.mkdir(parents=True)
    (package_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return package_dir


def _write_us_company_wiki_package(root: Path) -> Path:
    package_dir = root / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K-0000320193-25-000079"
    (package_dir / "metrics").mkdir(parents=True)
    (package_dir / "qa").mkdir()
    (package_dir / "sections").mkdir()
    (package_dir / "tables").mkdir()
    (package_dir / "xbrl").mkdir()
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "market_evidence_package_v1",
                "market": "US",
                "filing_id": "US:AAPL:2025:10-K",
                "company_id": "US:AAPL",
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "form": "10-K",
                "report_type": "annual",
                "fiscal_year": 2025,
                "fiscal_period": "FY",
                "period_end": "2025-09-27",
                "published_at": "2025-10-31",
                "quality_status": "pass",
                "source_url": "https://www.sec.gov/Archives/edgar/data/320193/demo.htm",
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "metrics" / "normalized_metrics.json").write_text(
        json.dumps({"metrics": [{"metric_id": "m1", "canonical_name": "revenue"}]}),
        encoding="utf-8",
    )
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "evidence_id": "e1",
                        "source_type": "xbrl_fact",
                        "local_path": "sections/financials.md",
                        "line": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "sections" / "financials.md").write_text("# Financials\n", encoding="utf-8")
    (package_dir / "tables" / "table_index.json").write_text(json.dumps({"tables": []}), encoding="utf-8")
    (package_dir / "xbrl" / "facts_raw.json").write_text(json.dumps({"facts": []}), encoding="utf-8")
    return package_dir


def _force_audit_payload(**payload):
    return {
        **payload,
        "legacy_package_import": payload.get("legacy_package_import", True),
        "force": True,
        "force_reason": "reviewed soft quality gate",
        "force_operator": "ops",
        "force_ticket": "CHG-123",
        "force_one_shot": True,
    }


def _allow_market_quality_gate(monkeypatch):
    monkeypatch.setattr(
        market_reports,
        "_quality_gates_for_package",
        lambda _package_dir: {
            "import_blocked": False,
            "vector_ingest_blocked": False,
            "force_allowed": True,
            "hard_gate_rule_ids": [],
            "soft_gate_rule_ids": [],
        },
    )


def _write_load_plan(
    package_dir: Path,
    *,
    can_import: bool,
    can_vector_ingest: bool,
    canonical_decision: str = "allow",
    retrieval_decision: str = "allow",
) -> None:
    (package_dir / "metrics").mkdir(exist_ok=True)
    payload = {
        "can_import": can_import,
        "can_vector_ingest": can_vector_ingest,
        "blocked_reasons": [
            f"canonical:{canonical_decision}:load-plan-test",
            f"retrieval:{retrieval_decision}:load-plan-test",
        ],
        "promotion_decisions": {
            "canonical": {
                "decision": canonical_decision,
                "severity": "soft" if canonical_decision == "review" else "hard" if canonical_decision == "block" else "observe",
                "review_rule_ids": ["load-plan-test"] if canonical_decision == "review" else [],
                "blocking_rule_ids": ["load-plan-test"] if canonical_decision == "block" else [],
            },
            "retrieval": {
                "decision": retrieval_decision,
                "severity": "soft" if retrieval_decision == "review" else "hard" if retrieval_decision == "block" else "observe",
                "review_rule_ids": ["load-plan-test"] if retrieval_decision == "review" else [],
                "blocking_rule_ids": ["load-plan-test"] if retrieval_decision == "block" else [],
            },
        },
        "rows": [{"table": "financial_data_artifacts"}],
        "quarantine_rows": [{"table": "financial_facts"}],
    }
    (package_dir / "metrics" / "load_plan.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_hk_v2_package(root: Path, *parts: str) -> Path:
    package_dir = root.joinpath(*parts)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text("# HK package\n", encoding="utf-8")
    (package_dir / "raw" / "report.pdf").write_bytes(b"%PDF-1.4 hk")
    (package_dir / "sections" / "report.md").write_text("# Report\n", encoding="utf-8")
    (package_dir / "sections" / "report_complete.md").write_text("# Report Complete\n", encoding="utf-8")
    (package_dir / "tables" / "table_index.json").write_text(json.dumps({"tables": [{"table_index": 1}]}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "metrics" / "financial_data.json").write_text(json.dumps({"statements": []}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "metrics" / "financial_checks.json").write_text(json.dumps({"overall_status": "warning"}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "metrics" / "normalized_metrics.json").write_text(json.dumps({"metrics": [{"metric_id": "m1"}]}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "quality_report.json").write_text(json.dumps({"overall_status": "warning", "section_count": 2, "table_count": 3, "raw_fact_count": 4, "normalized_metric_count": 5}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"evidence_id": "e1", "page_number": 1, "table_index": 1, "row_index": 1, "column_index": 1},
                    {"evidence_id": "e2", "page_number": 1, "table_index": 1, "row_index": 2, "column_index": 1},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (package_dir / "parser" / "document_full.json").write_text(json.dumps({"content_list_enhanced": {"footnotes": {"references": [{"id": "fn1"}]}, "toc": {"headings": [{"title": "Overview"}]}}}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "parser" / "content_list_enhanced.json").write_text(json.dumps({"footnotes": {"references": [{"id": "fn1"}]}, "toc": {"headings": [{"title": "Overview"}]}, "financial_note_links": {"links": [{"note": "1"}]}, "tables": [{"table_index": 1, "relations": [{"type": "footnote", "target": "fn1"}]}], "quality_signals": {"tables": [{"table_index": 1, "score": 0.95}]}, "pages": [{"page_number": 1}]}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "parser" / "table_relations.json").write_text(json.dumps({"schema_version": "hk_table_relations_v1", "relations": [{"type": "footnote", "target": "fn1"}]}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "footnotes.json").write_text(json.dumps({"schema_version": "hk_footnotes_v1", "payload": {"references": [{"id": "fn1"}]}}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "toc.json").write_text(json.dumps({"schema_version": "hk_toc_v1", "payload": {"headings": [{"title": "Overview"}]}}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "financial_note_links.json").write_text(json.dumps({"schema_version": "hk_financial_note_links_v1", "payload": {"links": [{"note": "1"}]}}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "qa" / "table_quality_signals.json").write_text(json.dumps({"schema_version": "hk_table_quality_signals_v1", "payload": {"tables": [{"table_index": 1, "score": 0.95}]}}, ensure_ascii=False), encoding="utf-8")
    (package_dir / "manifest.json").write_text(json.dumps({"market": "HK", "filing_id": "HK:00700:12100024", "ticker": "00700", "company_name": "TENCENT", "form": "annual", "report_type": "annual", "fiscal_year": 2025, "fiscal_period": "FY", "period_end": "2025-12-31", "published_at": "2026-04-09", "quality_status": "warning", "parse_run_id": "run-1"}, ensure_ascii=False), encoding="utf-8")
    return package_dir


class DummyUser:
    id = 42
    username = "ops"
    email = "ops@example.test"
    full_name = "Ops User"
    role = "admin"


def market_reports_client() -> TestClient:
    app = FastAPI()
    app.include_router(market_reports.router, prefix="/api")

    async def current_user() -> User:
        return User(
            id=42,
            username="ops",
            email="ops@example.test",
            hashed_password="x",
            full_name="Ops User",
            role=UserRole.ADMIN,
        )

    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app)


def capture_background_job(monkeypatch):
    seen = {}

    def fake_start(kind, target, *, created_by=None):
        seen["kind"] = kind
        seen["created_by"] = created_by
        seen["target_result"] = target()
        return {"job_id": f"{kind}-job-1", "status": "queued", "created_by": created_by}

    monkeypatch.setattr(market_reports.market_report_job_service, "start", fake_start)
    return seen


def test_market_report_route_order_keeps_static_routes_before_catchalls():
    paths = [route.path for route in market_reports.router.routes]

    assert paths.index("/v1/reports/assist") < paths.index("/v1/{upstream_path:path}")
    assert paths.index("/market-reports/package-file") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/packages/build") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/packages/import") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/packages/vector-ingest") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/document-full/status") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/document-full/import/status") < paths.index("/market-reports/packages/{filing_id}")
    assert paths.index("/market-reports/document-full/import") < paths.index("/market-reports/packages/{filing_id}")


def test_v1_proxy_preserves_finder_path(monkeypatch):
    seen = {}

    async def fake_proxy_request(
        *,
        base_url,
        upstream_path,
        request,
        timeout=market_reports.MARKET_REPORT_PROXY_TIMEOUT,
        service_token=None,
    ):
        seen.update(
            {
                "base_url": base_url,
                "upstream_path": upstream_path,
                "timeout": timeout,
                "service_token": service_token,
            }
        )
        return "ok"

    monkeypatch.delenv("SIQ_MARKET_REPORT_FINDER_TOKEN", raising=False)
    monkeypatch.setattr(market_reports, "_proxy_request", fake_proxy_request)

    result = asyncio.run(market_reports.proxy_market_report_finder("reports/recent", DummyRequest()))

    assert result == "ok"
    assert seen["base_url"] == market_reports.REPORT_FINDER_BASE
    assert seen["upstream_path"] == "/v1/reports/recent"
    assert seen["service_token"] is None


def test_v1_proxy_rejects_disallowed_path_without_upstream(monkeypatch):
    async def fake_proxy_request(**kwargs):
        raise AssertionError("disallowed finder path should not call upstream")

    monkeypatch.setattr(market_reports, "_proxy_request", fake_proxy_request)

    try:
        asyncio.run(market_reports.proxy_market_report_finder("reports/search", DummyRequest()))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException")


def test_v1_proxy_rejects_disallowed_method_without_upstream(monkeypatch):
    class GetRequest(DummyRequest):
        method = "GET"

    async def fake_proxy_request(**kwargs):
        raise AssertionError("disallowed finder method should not call upstream")

    monkeypatch.setattr(market_reports, "_proxy_request", fake_proxy_request)

    try:
        asyncio.run(market_reports.proxy_market_report_finder("reports/recent", GetRequest()))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException")


def test_v1_proxy_uses_finder_service_token_env(monkeypatch):
    seen = {}

    async def fake_proxy_request(
        *,
        base_url,
        upstream_path,
        request,
        timeout=market_reports.MARKET_REPORT_PROXY_TIMEOUT,
        service_token=None,
    ):
        seen.update(
            {
                "base_url": base_url,
                "upstream_path": upstream_path,
                "timeout": timeout,
                "service_token": service_token,
            }
        )
        return "ok"

    monkeypatch.setenv("SIQ_MARKET_REPORT_FINDER_TOKEN", "finder-token")
    monkeypatch.setattr(market_reports, "_proxy_request", fake_proxy_request)

    result = asyncio.run(market_reports.proxy_market_report_finder("reports/latest", DummyRequest()))

    assert result == "ok"
    assert seen == {
        "base_url": market_reports.REPORT_FINDER_BASE,
        "upstream_path": "/v1/reports/latest",
        "timeout": market_reports.MARKET_REPORT_PROXY_TIMEOUT,
        "service_token": "finder-token",
    }


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
        market_reports._proxy_request(
            base_url="http://finder",
            upstream_path="/v1/reports/recent",
            request=Request(),
            timeout=1.25,
        )
    )

    assert seen["method"] == "POST"
    assert seen["url"] == "http://finder/v1/reports/recent"
    assert seen["params"] == [("ticker", "AAPL"), ("ticker", "MSFT"), ("limit", "2")]
    assert seen["content"] == b'{"q":"annual"}'
    assert seen["headers"] == {"content-type": "application/json; charset=utf-8"}
    assert seen["timeout"] == 1.25
    assert response.status_code == 207
    assert response.media_type == "application/vnd.finder+json"
    assert response.body == b'{"ok":true}'


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
        market_reports._proxy_request(
            base_url="http://finder",
            upstream_path="/v1/ping",
            request=Request(),
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


def test_proxy_rules_get_preserves_status_body_and_media_type(monkeypatch):
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
                    "content": b'{"rules":[]}',
                    "status_code": 206,
                    "headers": {"content-type": "application/vnd.rules+json"},
                },
            )()

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        market_report_proxy.proxy_rules_get(
            market_rules_base="http://rules",
            upstream_path="/markets/cn/rules",
            timeout=3.0,
        )
    )

    assert seen == {"timeout": 3.0, "url": "http://rules/markets/cn/rules"}
    assert response.status_code == 206
    assert response.media_type == "application/vnd.rules+json"
    assert response.body == b'{"rules":[]}'


def test_proxy_rules_get_maps_request_error_to_502(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            raise market_report_proxy.httpx.RequestError("rules offline")

    monkeypatch.setattr(market_report_proxy.httpx, "AsyncClient", FakeAsyncClient)

    try:
        asyncio.run(
            market_report_proxy.proxy_rules_get(
                market_rules_base="http://rules",
                upstream_path="/markets",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "rules offline" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_finder_assist_wrapper_uses_router_settings(monkeypatch):
    seen = {}

    async def fake_finder_assist(*, report_finder_base, payload, timeout, service_token):
        seen.update(
            {
                "report_finder_base": report_finder_base,
                "payload": payload,
                "timeout": timeout,
                "service_token": service_token,
            }
        )
        return {"ok": True}

    monkeypatch.setenv("SIQ_MARKET_REPORT_FINDER_TOKEN", "finder-token")
    monkeypatch.setattr(market_report_proxy, "finder_assist", fake_finder_assist)

    result = asyncio.run(market_reports._finder_assist({"prompt": "demo"}))

    assert result == {"ok": True}
    assert seen == {
        "report_finder_base": market_reports.REPORT_FINDER_BASE,
        "payload": {"prompt": "demo"},
        "timeout": market_reports.MARKET_REPORT_PROXY_TIMEOUT,
        "service_token": "finder-token",
    }


def test_market_rules_route_wrappers_use_rules_base(monkeypatch):
    calls = []

    async def fake_proxy_rules_get(*, market_rules_base, upstream_path, service_token):
        calls.append({"market_rules_base": market_rules_base, "upstream_path": upstream_path, "service_token": service_token})
        return {"path": upstream_path}

    monkeypatch.setenv("SIQ_MARKET_REPORT_RULES_TOKEN", "rules-token")
    monkeypatch.setattr(market_report_proxy, "proxy_rules_get", fake_proxy_rules_get)

    modules = asyncio.run(market_reports.market_modules())
    cn_rules = asyncio.run(market_reports.cn_market_rules())

    assert modules == {"path": "/markets"}
    assert cn_rules == {"path": "/markets/cn/rules"}
    assert calls == [
        {"market_rules_base": market_reports.MARKET_RULES_BASE, "upstream_path": "/markets", "service_token": "rules-token"},
        {
            "market_rules_base": market_reports.MARKET_RULES_BASE,
            "upstream_path": "/markets/cn/rules",
            "service_token": "rules-token",
        },
    ]


def test_market_report_health_wrapper_uses_router_bases(monkeypatch):
    seen = {}

    async def fake_market_report_health(*, report_finder_base, market_rules_base):
        seen.update({"report_finder_base": report_finder_base, "market_rules_base": market_rules_base})
        return {"ok": True}

    monkeypatch.setattr(market_report_proxy, "market_report_health", fake_market_report_health)

    result = asyncio.run(market_reports.market_report_health())

    assert result == {"ok": True}
    assert seen == {
        "report_finder_base": market_reports.REPORT_FINDER_BASE,
        "market_rules_base": market_reports.MARKET_RULES_BASE,
    }


def test_assist_merge_prefers_llm_explanations():
    base = {
        "intent": {"market": "KR", "report_types": ["annual"]},
        "candidate_explanations": [
            {
                "document_url": "https://dart.example/doc",
                "title_zh": "年度报告",
                "report_type_zh": "年度报告",
                "period_zh": "2025-12-31",
                "recommendation": "规则推荐",
                "recommended": True,
                "warnings": [],
            }
        ],
        "assistant_mode": "rules",
    }
    llm = {
        "intent": {"company_query": "三星电子"},
        "candidate_explanations": [
            {
                "document_url": "https://dart.example/doc",
                "title_zh": "三星电子年度报告",
                "recommendation": "模型解释：报告期匹配",
            }
        ],
        "assistant_mode": "llm:local:test",
    }

    merged = market_reports._merge_assist(base, llm)

    assert merged["intent"]["company_query"] == "三星电子"
    assert merged["candidate_explanations"][0]["title_zh"] == "三星电子年度报告"
    assert merged["candidate_explanations"][0]["recommended"] is True
    assert merged["assistant_mode"] == "llm:local:test"


def test_merge_assist_preserves_foreign_market_identifier_enrichment():
    base = {
        "intent": {"market": "US", "company_query": "苹果", "report_types": ["annual"]},
        "candidate_explanations": [],
        "assistant_mode": "rules",
    }
    llm = {
        "intent": {
            "company_query": "Apple Inc.",
            "ticker": "AAPL",
            "company_id": "0000320193",
        },
        "assistant_mode": "llm:cloud:test",
    }

    merged = market_reports._merge_assist(base, llm)

    assert merged["intent"]["market"] == "US"
    assert merged["intent"]["company_query"] == "Apple Inc."
    assert merged["intent"]["ticker"] == "AAPL"
    assert merged["intent"]["company_id"] == "0000320193"


def test_openai_assist_retries_after_transient_failure(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": {
                                        "market": "US",
                                        "company_query": "Apple Inc.",
                                        "ticker": "AAPL",
                                        "company_id": "0000320193",
                                    },
                                    "candidate_explanations": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            calls.append(("timeout", timeout))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers, json):
            calls.append(("post", url, json))
            if len([item for item in calls if item[0] == "post"]) == 1:
                raise market_reports.httpx.RequestError("temporary upstream reset")
            return FakeResponse()

    monkeypatch.setattr(market_reports.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        market_reports._openai_compatible_enhance_assist(
            active="cloud",
            provider={
                "baseUrl": "https://llm.example/v1",
                "model": "demo-model",
                "apiKey": "secret",
                "temperature": 0.2,
                "maxTokens": 1024,
            },
            request_payload={
                "prompt": "下载苹果 2025 年年报",
                "market": "US",
                "report_year": 2025,
                "report_types": ["annual"],
            },
            base_assist={"intent": {"market": "US", "company_query": "苹果"}, "candidate_explanations": []},
        )
    )

    assert result
    assert result["intent"]["ticker"] == "AAPL"
    assert result["assistant_mode"] == "llm:cloud:demo-model"
    assert len([item for item in calls if item[0] == "post"]) == 2


def test_active_llm_provider_respects_active_setting(monkeypatch):
    monkeypatch.setattr(
        market_reports,
        "load_llm_settings",
        lambda include_secrets=False: {
            "activeProvider": "local",
            "providers": {
                "local": {
                    "enabled": True,
                    "providerName": "本地 vLLM / Qwen3.6",
                    "baseUrl": "http://127.0.0.1:8004/v1",
                    "model": "Qwen3.6-35B-A3B-FP8",
                },
                "cloud": {
                    "enabled": True,
                    "providerName": "StepFun / Step-3.7 Flash",
                    "baseUrl": "https://api.stepfun.com/v1",
                    "model": "step-3.7-flash",
                },
            },
        },
    )

    active, provider = market_reports._active_llm_provider()

    assert active == "local"
    assert provider["baseUrl"] == "http://127.0.0.1:8004/v1"


def test_active_llm_provider_falls_back_when_active_disabled(monkeypatch):
    monkeypatch.setattr(
        market_reports,
        "load_llm_settings",
        lambda include_secrets=False: {
            "activeProvider": "local",
            "providers": {
                "local": {
                    "enabled": False,
                    "baseUrl": "http://127.0.0.1:8004/v1",
                    "model": "local-disabled",
                },
                "cloud": {
                    "enabled": True,
                    "baseUrl": "https://api.stepfun.com/v1",
                    "model": "step-3.7-flash",
                },
            },
        },
    )

    active, provider = market_reports._active_llm_provider()

    assert active == "cloud"
    assert provider["model"] == "step-3.7-flash"


def test_market_llm_semantic_env_maps_active_provider(monkeypatch):
    monkeypatch.setattr(
        market_reports,
        "load_llm_settings",
        lambda include_secrets=True: {
            "activeProvider": "local",
            "providers": {
                "local": {
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "model": "qwen-local",
                    "apiKey": "secret",
                    "timeoutSeconds": 77,
                    "maxTokens": 2048,
                    "temperature": 0.15,
                    "chatTemplateKwargs": {"enable_thinking": False},
                },
                "cloud": {
                    "enabled": True,
                    "baseUrl": "https://api.stepfun.com/v1",
                    "model": "step-3.7-flash",
                },
            },
        },
    )

    env = market_reports._llm_semantic_env()

    assert env["SIQ_LLM_SEMANTIC_PROVIDER"] == "local"
    assert env["FINSIGHT_LLM_SEMANTIC_PROVIDER"] == "local"
    assert env["SIQ_LLM_SEMANTIC_PROVIDER_BASE_URL"] == "http://llm.local/v1"
    assert env["FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"] == "http://llm.local/v1"
    assert env["SIQ_LLM_SEMANTIC_MODEL"] == "qwen-local"
    assert env["FINSIGHT_LLM_SEMANTIC_MODEL"] == "qwen-local"
    assert env["SIQ_LOCAL_LLM_MODEL"] == "qwen-local"
    assert env["FINSIGHT_LOCAL_LLM_MODEL"] == "qwen-local"
    assert env["SIQ_LLM_SEMANTIC_API_KEY"] == "secret"
    assert env["FINSIGHT_LLM_SEMANTIC_API_KEY"] == "secret"
    assert env["SIQ_LLM_SEMANTIC_TIMEOUT"] == "77"
    assert env["FINSIGHT_LLM_SEMANTIC_MAX_TOKENS"] == "2048"
    assert env["SIQ_LLM_SEMANTIC_TEMPERATURE"] == "0.15"
    assert env["FINSIGHT_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] == '{"enable_thinking": false}'


def test_hermes_assist_uses_runs_api(monkeypatch):
    seen = {}

    async def fake_create_run(input, conversation_history, *, profile="siq_assistant", session_id=None):
        seen["input"] = input
        seen["conversation_history"] = conversation_history
        seen["profile"] = profile
        seen["session_id"] = session_id
        return "run_123"

    async def fake_collect_run_result(run_id, *, profile="siq_assistant", timeout=None):
        seen["run_id"] = run_id
        seen["collect_profile"] = profile
        seen["timeout"] = timeout
        return """
        {
          "intent": {"company_query": "三星电子"},
          "candidate_explanations": [
            {
              "document_url": "https://dart.example/doc",
              "title_zh": "三星电子年度报告",
              "report_type_zh": "年度报告",
              "period_zh": "2025 全年",
              "recommendation": "模型解释：报告期和年报类型匹配",
              "recommended": true,
              "warnings": []
            }
          ]
        }
        """

    monkeypatch.setattr(market_reports, "create_run", fake_create_run)
    monkeypatch.setattr(market_reports, "collect_run_result", fake_collect_run_result)
    monkeypatch.setattr(market_reports, "set_all_profile_model_modes", lambda mode: {"mode": mode})

    result = asyncio.run(
        market_reports._hermes_enhance_assist(
            active="cloud",
            provider={
                "providerName": "Hermes / Minimax",
                "baseUrl": "hermes://minimax-cn",
                "model": "MiniMax-M3",
                "temperature": 0.2,
                "maxTokens": 4096,
            },
            request_payload={
                "prompt": "下载三星电子 2025 年年报",
                "market": "KR",
                "report_year": 2025,
                "report_types": ["annual"],
                "candidates": [
                    {
                        "document_url": "https://dart.example/doc",
                        "title": "사업보고서",
                        "report_type": "annual",
                        "report_end": "2025-12-31",
                        "published_at": "2026-03-15",
                    }
                ],
            },
            base_assist={"intent": {"market": "KR"}, "candidate_explanations": []},
        )
    )

    assert result
    assert result["candidate_explanations"][0]["title_zh"] == "三星电子年度报告"
    assert result["assistant_mode"] == "llm:cloud:hermes:minimax"
    assert seen["profile"] == "siq_assistant"
    assert seen["conversation_history"] == []
    assert seen["run_id"] == "run_123"
    assert "不要生成或修改下载 URL" in seen["input"]
    assert "https://dart.example/doc" in seen["input"]


def test_us_sec_upload_records_workspace_artifact_async(monkeypatch):
    calls = []

    def fake_persist_us_sec_upload_batch(items, **kwargs):
        calls.append({"filenames": [item.filename for item in items], "kwargs": kwargs})
        return [
            {
                "file_name": item.filename,
                "relative_path": f"us-sec/uploads/{item.filename}",
            }
            for item in items
        ]

    async def fake_record_user_artifact_async(async_session, **kwargs):
        calls.append({"async_session": async_session, "artifact": kwargs})

    monkeypatch.setattr(market_reports, "_persist_us_sec_upload_batch", fake_persist_us_sec_upload_batch)
    monkeypatch.setattr(market_reports, "record_user_artifact_async", fake_record_user_artifact_async)

    result = asyncio.run(
        market_reports.us_sec_upload_files(
            UploadRouteRequest(),
            files=[_AsyncUpload("aapl-10k.pdf", b"10-k")],
            ticker="aapl",
            company_name="Apple Inc.",
            report_type="10-K",
            fiscal_year="2025",
            period_end="2025-09-27",
            filing_date="2025-10-31",
            current_user=type("User", (), {"id": 7})(),
            async_session=object(),
        )
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert calls[0]["kwargs"]["ticker"] == "AAPL"
    assert calls[1]["artifact"]["user_id"] == 7
    assert calls[1]["artifact"]["artifact_type"] == "download"
    assert calls[1]["artifact"]["artifact_key"] == "us-sec/uploads/aapl-10k.pdf"
    assert calls[1]["artifact"]["source"] == "us-sec-upload"


def test_us_sec_upload_swallow_workspace_artifact_error(monkeypatch):
    def fake_persist_us_sec_upload_batch(items, **kwargs):
        return [
            {
                "file_name": item.filename,
                "relative_path": f"us-sec/uploads/{item.filename}",
            }
            for item in items
        ]

    async def fake_record_user_artifact_async(*args, **kwargs):
        raise RuntimeError("workspace unavailable")

    monkeypatch.setattr(market_reports, "_persist_us_sec_upload_batch", fake_persist_us_sec_upload_batch)
    monkeypatch.setattr(market_reports, "record_user_artifact_async", fake_record_user_artifact_async)

    result = asyncio.run(
        market_reports.us_sec_upload_files(
            UploadRouteRequest(),
            files=[_AsyncUpload("aapl-10k.pdf", b"10-k")],
            ticker="",
            company_name="",
            report_type="",
            fiscal_year="",
            period_end="",
            filing_date="",
            current_user=type("User", (), {"id": 7})(),
            async_session=object(),
        )
    )

    assert result["ok"] is True
    assert result["files"][0]["relative_path"] == "us-sec/uploads/aapl-10k.pdf"


def test_us_sec_upload_persist_batch_writes_build_compatible_metadata(monkeypatch, tmp_path):
    content = b"<html><body>10-K</body></html>"
    digest = hashlib.sha256(content).hexdigest()

    class FixedDateTime(market_reports.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 3, 12, 34, 56, tzinfo=tz)

    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(market_reports, "datetime", FixedDateTime)
    upload = market_reports.BufferedUpload(
        filename="apple-10k.htm",
        content_type="text/html",
        size_bytes=len(content),
        sha256=digest,
        file=io.BytesIO(content),
    )

    results = market_reports._persist_us_sec_upload_batch(
        [upload],
        ticker="AAPL",
        company_name="Apple Inc.",
        report_type="10-K",
        fiscal_year=2025,
        period_end="2025-09-27",
        filing_date="2025-10-31",
    )
    result = results[0]

    saved_path = Path(result["saved_path"])
    metadata_path = Path(result["metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert saved_path.is_file()
    assert saved_path.read_bytes() == content
    assert result["relative_path"].startswith("US/Apple-Inc/2025/年报/")
    assert result["relative_path"].endswith(f"_20260703T123456Z_{digest[:10]}.html")
    assert metadata_path == saved_path.with_suffix(saved_path.suffix + ".metadata.json")
    assert metadata["candidate"]["market"] == "US"
    assert metadata["candidate"]["ticker"] == "AAPL"
    assert metadata["candidate"]["company_name"] == "Apple Inc."
    assert metadata["candidate"]["report_type"] == "annual"
    assert metadata["candidate"]["report_family"] == "annual"
    assert metadata["candidate"]["form"] == "10-K"
    assert metadata["candidate"]["report_end"] == "2025-09-27"
    assert metadata["candidate"]["published_at"] == "2025-10-31"
    assert metadata["candidate"]["metadata"] == {"uploaded_filename": "apple-10k.htm", "content_type": "text/html"}
    assert metadata["downloaded_file"]["saved_path"] == str(saved_path)
    assert metadata["downloaded_file"]["content_sha256"] == digest
    assert metadata["downloaded_file"]["size_bytes"] == len(content)
    assert metadata["downloaded_file"]["content_type"] == "text/html"


def test_us_sec_upload_rejects_unsupported_suffix_before_reading():
    bad_suffix = _AsyncUpload("notes.exe", b"x")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            market_reports.us_sec_upload_files(
                UploadRouteRequest(),
                files=[_AsyncUpload("valid.pdf", b"valid"), bad_suffix],
                ticker="AAPL",
                company_name="Apple Inc.",
                report_type="10-K",
                fiscal_year="2025",
                period_end="2025-09-27",
                filing_date="2025-10-31",
                current_user=type("User", (), {"id": 7})(),
                async_session=object(),
            )
        )

    assert exc.value.status_code == 400
    assert "Only PDF" in exc.value.detail
    assert bad_suffix._offset == 0


def test_us_sec_upload_rejects_six_files_before_reading():
    files = [_AsyncUpload(f"report-{index}.pdf", b"x") for index in range(6)]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            market_reports.us_sec_upload_files(
                UploadRouteRequest(),
                files=files,
                ticker="",
                company_name="",
                report_type="",
                fiscal_year="",
                period_end="",
                filing_date="",
                current_user=type("User", (), {"id": 7})(),
                async_session=object(),
            )
        )

    assert exc.value.status_code == 413
    assert exc.value.detail["error"] == "too_many_upload_files"
    assert all(item._offset == 0 for item in files)


def test_us_sec_upload_rejects_empty_file_without_publishing(monkeypatch, tmp_path):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            market_reports.us_sec_upload_files(
                UploadRouteRequest(),
                files=[_AsyncUpload("first.pdf", b"valid"), _AsyncUpload("empty.pdf", b"")],
                ticker="AAPL",
                company_name="Apple Inc.",
                report_type="10-K",
                fiscal_year="2025",
                period_end="2025-09-27",
                filing_date="2025-10-31",
                current_user=type("User", (), {"id": 7})(),
                async_session=object(),
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Uploaded file is empty"
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


@pytest.mark.parametrize(
    ("files", "max_file_bytes", "max_batch_bytes", "expected_scope"),
    [
        ([_AsyncUpload("large.pdf", b"12345")], 4, 20, "file"),
        ([_AsyncUpload("first.pdf", b"1234"), _AsyncUpload("second.pdf", b"5678")], 10, 7, "batch"),
    ],
)
def test_us_sec_upload_enforces_single_file_and_batch_size_limits(
    monkeypatch,
    tmp_path,
    files,
    max_file_bytes,
    max_batch_bytes,
    expected_scope,
):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(market_reports, "US_SEC_UPLOAD_MAX_FILE_BYTES", max_file_bytes)
    monkeypatch.setattr(market_reports, "US_SEC_UPLOAD_MAX_BATCH_BYTES", max_batch_bytes)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            market_reports.us_sec_upload_files(
                UploadRouteRequest(),
                files=files,
                ticker="",
                company_name="",
                report_type="",
                fiscal_year="",
                period_end="",
                filing_date="",
                current_user=type("User", (), {"id": 7})(),
                async_session=object(),
            )
        )

    assert exc.value.status_code == 413
    assert exc.value.detail["error"] == "upload_too_large"
    assert exc.value.detail["scope"] == expected_scope
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_us_sec_upload_persists_batch_in_threadpool_and_records_after_publish(monkeypatch):
    route_thread = threading.get_ident()
    persist_threads = []
    calls = []

    def fake_persist_batch(items, **kwargs):
        persist_threads.append(threading.get_ident())
        return [
            {"file_name": item.filename, "relative_path": f"US/manual/{item.filename}"}
            for item in items
        ]

    async def fake_record(*args, **kwargs):
        calls.append(kwargs["artifact_key"])

    monkeypatch.setattr(market_reports, "_persist_us_sec_upload_batch", fake_persist_batch)
    monkeypatch.setattr(market_reports, "record_user_artifact_async", fake_record)

    result = asyncio.run(
        market_reports.us_sec_upload_files(
            UploadRouteRequest(),
            files=[_AsyncUpload("first.pdf", b"first"), _AsyncUpload("second.htm", b"second")],
            ticker="",
            company_name="",
            report_type="",
            fiscal_year="",
            period_end="",
            filing_date="",
            current_user=type("User", (), {"id": 7})(),
            async_session=object(),
        )
    )

    assert result["count"] == 2
    assert persist_threads and persist_threads[0] != route_thread
    assert calls == ["US/manual/first.pdf", "US/manual/second.htm"]


def test_us_sec_upload_batch_failure_on_second_file_publishes_nothing_and_cleans_temps(monkeypatch, tmp_path):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    real_copy = market_reports._copy_buffered_upload_to_path
    calls = 0

    def fail_second_copy(item, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-file write failure")
        return real_copy(item, destination)

    monkeypatch.setattr(market_reports, "_copy_buffered_upload_to_path", fail_second_copy)
    uploads = [
        market_reports.BufferedUpload(
            filename="first.pdf",
            content_type="application/pdf",
            size_bytes=5,
            sha256=hashlib.sha256(b"first").hexdigest(),
            file=io.BytesIO(b"first"),
        ),
        market_reports.BufferedUpload(
            filename="second.pdf",
            content_type="application/pdf",
            size_bytes=6,
            sha256=hashlib.sha256(b"second").hexdigest(),
            file=io.BytesIO(b"second"),
        ),
    ]

    with pytest.raises(OSError, match="second-file"):
        market_reports._persist_us_sec_upload_batch(
            uploads,
            ticker="AAPL",
            company_name="Apple Inc.",
            report_type="10-K",
            fiscal_year=2025,
            period_end="2025-09-27",
            filing_date="2025-10-31",
        )

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_us_sec_upload_batch_publish_failure_rolls_back_data_and_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    real_link = market_reports.os.link
    link_calls = 0

    def fail_during_second_file_publish(source, destination):
        nonlocal link_calls
        link_calls += 1
        if link_calls == 3:
            raise OSError("injected publish failure")
        return real_link(source, destination)

    monkeypatch.setattr(market_reports.os, "link", fail_during_second_file_publish)
    uploads = [
        market_reports.BufferedUpload(
            filename=f"report-{index}.pdf",
            content_type="application/pdf",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            file=io.BytesIO(content),
        )
        for index, content in enumerate((b"first", b"second"), start=1)
    ]

    with pytest.raises(OSError, match="publish failure"):
        market_reports._persist_us_sec_upload_batch(
            uploads,
            ticker="AAPL",
            company_name="Apple Inc.",
            report_type="10-K",
            fiscal_year=2025,
            period_end="2025-09-27",
            filing_date="2025-10-31",
        )

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_us_sec_upload_batch_success_leaves_no_temporary_files(monkeypatch, tmp_path):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    content_by_name = {"first.pdf": b"first", "second.htm": b"second"}
    uploads = [
        market_reports.BufferedUpload(
            filename=name,
            content_type="" if name.endswith(".pdf") else "text/html",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            file=io.BytesIO(content),
        )
        for name, content in content_by_name.items()
    ]

    results = market_reports._persist_us_sec_upload_batch(
        uploads,
        ticker="AAPL",
        company_name="Apple Inc.",
        report_type="10-K",
        fiscal_year=2025,
        period_end="2025-09-27",
        filing_date="2025-10-31",
    )

    assert len(results) == 2
    assert all(Path(item["saved_path"]).is_file() for item in results)
    assert all(Path(item["metadata_path"]).is_file() for item in results)
    assert results[0]["content_type"] == "application/octet-stream"
    assert not list(tmp_path.rglob(".siq-upload-*"))


def test_market_package_summary_reads_us_package(tmp_path):
    package_dir = _write_us_company_wiki_package(tmp_path / "wiki" / "us")
    summary = market_reports._read_market_package_summary(package_dir)

    assert summary["market"] == "US"
    assert summary["ticker"] == "AAPL"
    assert summary["quality_status"] in {"pass", "warning"}
    assert summary["counts"]["metrics"] >= 1
    assert summary["counts"]["evidence"] >= 1
    assert summary["paths"]["source_map"].endswith("qa/source_map.json")


def test_market_packages_route_lists_hk_company_wiki_layout(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "market_evidence_package_v1",
                "market": "HK",
                "filing_id": "HK:00700:12100024",
                "company_id": "HK:00700",
                "ticker": "00700",
                "company_name": "TENCENT",
                "form": "annual",
                "report_type": "annual",
                "fiscal_year": 2025,
                "period_end": "2025-12-31",
                "quality_status": "warning",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    response = market_reports_client().get("/api/market-reports/packages?market=HK&limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["packages"][0]["package_path"].endswith(
        "wiki/hk/companies/00700-TENCENT/reports/2025-annual-12100024"
    )
    assert payload["packages"][0]["filing_id"] == "HK:00700:12100024"
    assert payload["packages"][0]["quality_gates"]["overall_status"] == "warning"
    assert payload["packages"][0]["quality_gates"]["import_blocked"] is True


def test_market_package_detail_returns_hk_v2_paths(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_hk_v2_package(wiki_root, "00700", "2025", "annual_12100024")
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    by_path = asyncio.run(market_reports.market_package_detail_by_path("HK", str(package_dir)))
    by_filing_id = asyncio.run(market_reports.market_package_detail_by_filing_id("HK:00700:12100024", "HK"))

    for payload in (by_path, by_filing_id):
        assert payload["paths"]["document_full"] == "parser/document_full.json"
        assert payload["paths"]["content_list_enhanced"] == "parser/content_list_enhanced.json"
        assert payload["paths"]["report_complete"] == "sections/report_complete.md"
        assert payload["paths"]["footnotes"] == "qa/footnotes.json"
        assert payload["paths"]["toc"] == "qa/toc.json"
        assert payload["paths"]["financial_note_links"] == "qa/financial_note_links.json"
        assert payload["paths"]["table_quality_signals"] == "qa/table_quality_signals.json"
        assert payload["quality_gates"]["overall_status"] == "warning"
        assert payload["quality_gates"]["vector_ingest_blocked"] is True


def test_market_package_quality_routes_keep_response_contract(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    (package_dir / "manifest.json").write_text(json.dumps({"filing_id": "AAPL-10K"}), encoding="utf-8")
    (package_dir / "qa").mkdir()
    (package_dir / "qa" / "quality_report.json").write_text(json.dumps({"overall_status": "pass"}), encoding="utf-8")
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"evidence_id": "e1", "source_url": "https://www.sec.gov/Archives/demo.htm", "html_anchor": "fact-1"},
                    {"evidence_id": "e2", "source_url": "https://www.sec.gov/Archives/demo.htm", "html_anchor": "fact-2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "metrics").mkdir()
    (package_dir / "metrics" / "financial_checks.json").write_text(json.dumps({"status": "warning"}), encoding="utf-8")
    _write_load_plan(
        package_dir,
        can_import=False,
        can_vector_ingest=False,
        canonical_decision="review",
        retrieval_decision="review",
    )
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "US", wiki_root)

    by_path = asyncio.run(market_reports.market_package_quality_by_path("US", str(package_dir)))
    by_filing_id = asyncio.run(market_reports.market_package_quality_by_filing_id("AAPL-10K", "US"))

    assert by_path["manifest"] == {"filing_id": "AAPL-10K"}
    assert by_path["quality"] == {"overall_status": "pass"}
    assert by_path["financial_checks"] == {"status": "warning"}
    assert by_path["load_plan"]["can_import"] is False
    assert by_path["load_plan"]["quarantine_row_count"] == 1
    assert by_path["quality_gates"]["load_plan"]["can_vector_ingest"] is False
    assert by_path["source_map_summary"] == {"evidence": 2}
    assert by_filing_id["package_path"] == str(package_dir)
    assert by_filing_id["load_plan"]["can_import"] is False
    assert "source_map_summary" not in by_filing_id


def test_market_package_file_serves_market_files_and_controls_inline_header(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_demo")
    sections_dir = package_dir / "sections"
    sections_dir.mkdir()
    report_path = sections_dir / "report.md"
    report_path.write_text("# Tencent annual report", encoding="utf-8")
    manifest_path = package_dir / "manifest.json"
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    inline_response = asyncio.run(
        market_reports.market_package_file("hk", str(package_dir), "sections/report.md", inline=True)
    )
    attachment_response = asyncio.run(
        market_reports.market_package_file("HK", str(package_dir), "manifest.json", inline=False)
    )

    assert Path(inline_response.path) == report_path
    assert inline_response.media_type == "text/markdown; charset=utf-8"
    assert inline_response.headers["content-disposition"] == "inline"
    assert Path(attachment_response.path) == manifest_path
    assert attachment_response.media_type == "application/json; charset=utf-8"
    assert "content-disposition" not in attachment_response.headers


def test_market_package_file_rejects_file_and_package_path_escape(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_demo")
    outside_package = _write_market_package(tmp_path / "outside", "00700", "2025", "annual_demo")
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    for file_path in ("../manifest.json", "/etc/passwd", "sections/../../secret.txt"):
        try:
            asyncio.run(market_reports.market_package_file("HK", str(package_dir), file_path))
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "Invalid file path"
        else:
            raise AssertionError("expected HTTPException")

    try:
        asyncio.run(market_reports.market_package_file("HK", str(outside_package), "manifest.json"))
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "outside the allowed evidence package root" in exc.detail
    else:
        raise AssertionError("expected HTTPException")


def test_market_package_file_returns_404_for_missing_file(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_demo")
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    try:
        asyncio.run(market_reports.market_package_file("HK", str(package_dir), "sections/missing.md"))
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Package file not found"
    else:
        raise AssertionError("expected HTTPException")


def test_us_sec_package_file_uses_us_root_and_rejects_escape(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    raw_dir = package_dir / "raw"
    raw_dir.mkdir()
    filing_path = raw_dir / "filing.htm"
    filing_path.write_text("<html><body>10-K</body></html>", encoding="utf-8")
    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)

    response = asyncio.run(market_reports.us_sec_package_file(str(package_dir), "raw/filing.htm", inline=True))

    assert Path(response.path) == filing_path
    assert response.media_type == "text/html; charset=utf-8"
    assert response.headers["content-disposition"] == "inline"

    try:
        asyncio.run(market_reports.us_sec_package_file(str(package_dir), "../manifest.json"))
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Invalid file path"
    else:
        raise AssertionError("expected HTTPException")


def test_us_sec_package_detail_by_path_returns_real_us_contract_and_keeps_ticker_route(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us"
    package_dir = _write_us_company_wiki_package(wiki_root)
    (package_dir / "raw").mkdir()
    (package_dir / "raw" / "filing.htm").write_text("<html><body>10-K</body></html>", encoding="utf-8")
    (package_dir / "sections.json").write_text(
        json.dumps({"sections": [{"file": "financials.md", "title": "Financial Statements"}]}),
        encoding="utf-8",
    )
    (package_dir / "sections" / "report_complete.md").write_text("# Clean annual report", encoding="utf-8")
    (package_dir / "metrics" / "normalized_metrics.json").write_text(
        json.dumps(
            {
                "metrics": [
                    {"metric_id": "revenue", "value": 100},
                    {"metric_id": "revenue_us", "value": 60, "dimensions": {"region": "US"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "metrics" / "financial_checks.json").write_text(
        json.dumps(
            {
                "overall_status": "pass",
                "checks": [
                    {"rule_id": "bs.assets_equals_liabilities_plus_equity", "status": "pass"},
                    {"rule_id": "note.unrelated", "status": "warning"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "qa" / "quality_report.json").write_text(
        json.dumps({"overall_status": "pass"}),
        encoding="utf-8",
    )
    (package_dir / "xbrl" / "facts_raw.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "fact-segment",
                        "concept": "us-gaap:Revenue",
                        "value_numeric": "60",
                        "unit": "USD",
                        "context_ref": "c-segment",
                        "dimensions": {"srt:ProductOrServiceAxis": "aapl:IPhoneMember"},
                        "html_anchor": "f-segment",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "evidence_id": "e-segment",
                        "source_type": "sec_xbrl_fact",
                        "html_anchor": "f-segment",
                        "target": "https://www.sec.gov/filing.htm#f-segment",
                        "raw": {"fact_id": "fact-segment"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(
        market_reports,
        "_latest_case_item_for_ticker",
        lambda ticker: {"ticker": ticker, "package_path": str(package_dir)},
    )

    client = market_reports_client()
    by_path = client.get("/api/us-sec/package", params={"package_path": str(package_dir)})
    by_ticker = client.get("/api/us-sec/packages/AAPL")

    assert by_path.status_code == 200
    payload = by_path.json()
    assert payload["sections"] == [{"file": "financials.md", "title": "Financial Statements"}]
    assert payload["bridge_checks"]["checks"] == [
        {"rule_id": "bs.assets_equals_liabilities_plus_equity", "status": "pass"}
    ]
    assert payload["dimension_metrics"] == [
        {"metric_id": "revenue_us", "value": 60, "dimensions": {"region": "US"}}
    ]
    assert payload["counts"]["dimension_facts"] == 1
    assert payload["dimension_facts"][0]["fact_id"] == "fact-segment"
    assert payload["dimension_facts"][0]["evidence"]["evidence_id"] == "e-segment"
    assert payload["preview"] == {
        "raw_html": "raw/filing.htm",
        "default_markdown": "sections/report_complete.md",
    }
    assert by_ticker.status_code == 200
    assert by_ticker.json() == payload


@pytest.mark.parametrize(
    ("package_path_kind", "expected_status", "expected_detail"),
    [
        ("empty", 400, "package_path is required"),
        ("outside", 400, "Path is outside the allowed evidence package root"),
        ("missing", 404, "US SEC package not found"),
    ],
)
def test_us_sec_package_detail_by_path_validates_package_path(
    monkeypatch,
    tmp_path,
    package_path_kind,
    expected_status,
    expected_detail,
):
    wiki_root = tmp_path / "wiki" / "us"
    wiki_root.mkdir(parents=True)
    outside_package = _write_market_package(tmp_path / "outside", "AAPL", "2025", "10-K")
    package_paths = {
        "empty": "",
        "outside": str(outside_package),
        "missing": str(wiki_root / "companies" / "AAPL" / "reports" / "missing"),
    }
    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)

    response = market_reports_client().get(
        "/api/us-sec/package",
        params={"package_path": package_paths[package_path_kind]},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


def test_us_sec_package_file_returns_404_for_missing_file(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)

    try:
        asyncio.run(market_reports.us_sec_package_file(str(package_dir), "raw/missing.htm"))
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Package file not found"
    else:
        raise AssertionError("expected HTTPException")


def test_find_market_evidence_returns_package_and_entry(tmp_path):
    package_dir = _write_us_company_wiki_package(tmp_path / "wiki" / "us")
    source_map = market_reports._read_json_file(package_dir / "qa" / "source_map.json", {})
    evidence_id = source_map["entries"][0]["evidence_id"]

    market, found_package, entry = market_reports._find_market_evidence(
        evidence_id,
        market="US",
        package_dir=package_dir,
    )

    assert market == "US"
    assert found_package == package_dir
    assert entry["evidence_id"] == evidence_id
    assert entry["local_path"].startswith("sections/")


def test_eu_package_build_routes_pdf_and_esef_sources():
    pdf_script = market_reports._market_build_script("EU", Path("/tmp/report.pdf"))
    esef_script = market_reports._market_build_script("EU", Path("/tmp/report.xhtml"))
    zip_script = market_reports._market_build_script("EU", Path("/tmp/report.zip"))

    assert pdf_script.name == "build_eu_pdf_evidence_package.py"
    assert esef_script.name == "build_eu_esef_evidence_package.py"
    assert zip_script.name == "build_eu_esef_evidence_package.py"
    assert market_reports._market_build_requires_parser_result("EU", Path("/tmp/report.pdf")) is True
    assert market_reports._market_build_requires_parser_result("EU", Path("/tmp/report.xhtml")) is False


def test_eu_package_build_accepts_download_relative_path(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    source_path = downloads_root / "EU" / "NL" / "ASML" / "2025" / "年报" / "report.xhtml"
    metadata_path = source_path.with_suffix(source_path.suffix + ".metadata.json")
    source_path.parent.mkdir(parents=True)
    source_path.write_text("<html xmlns:ix=\"http://www.xbrl.org/2013/inlineXBRL\"></html>", encoding="utf-8")
    metadata_path.write_text('{"company_name":"ASML Holding N.V."}', encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "/tmp/package\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", downloads_root)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    monkeypatch.setattr(market_reports, "_read_market_package_detail", lambda package_dir: {"package_path": str(package_dir)})

    result = market_reports._run_market_package_build({
        "market": "EU",
        "download_relative_path": "EU/NL/ASML/2025/年报/report.xhtml",
        "force": True,
    })

    assert result["ok"] is True
    assert seen["args"][1].endswith("build_eu_esef_evidence_package.py")
    assert seen["args"][2] == str(source_path)
    metadata_index = seen["args"].index("--metadata")
    assert seen["args"][metadata_index + 1] == str(metadata_path)
    assert "--parser-result" not in seen["args"]
    assert seen["args"][-1] == "--force"


def test_us_package_build_accepts_download_relative_path_and_returns_sec_detail(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    source_path = downloads_root / "US" / "Apple" / "2025" / "年报" / "apple_10k.html"
    metadata_path = source_path.with_suffix(source_path.suffix + ".metadata.json")
    source_path.parent.mkdir(parents=True)
    source_path.write_text("<html><body>10-K</body></html>", encoding="utf-8")
    metadata_path.write_text('{"candidate":{"ticker":"AAPL"}}', encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "/tmp/us-package\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", downloads_root)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    monkeypatch.setattr(market_reports, "_read_package_detail", lambda package_dir: {"package_path": str(package_dir), "preview": {"raw_html": "raw/filing.htm"}})
    monkeypatch.setattr(market_reports, "_read_market_package_detail", lambda package_dir: {"unexpected": str(package_dir)})

    result = market_reports._run_market_package_build({
        "market": "US",
        "download_relative_path": "US/Apple/2025/年报/apple_10k.html",
        "force": True,
    })

    assert result["ok"] is True
    assert result["package"]["preview"]["raw_html"] == "raw/filing.htm"
    assert seen["args"][1].endswith("build_sec_evidence_package.py")
    assert seen["args"][2] == str(source_path)
    metadata_index = seen["args"].index("--metadata")
    assert seen["args"][metadata_index + 1] == str(metadata_path)
    assert seen["args"][-1] == "--force"


def test_market_package_build_rejects_invalid_download_path_before_command(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", downloads_root)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setattr(market_reports, "run_command", fail_run)

    try:
        market_reports._run_market_package_build({
            "market": "US",
            "download_relative_path": "../secret.html",
        })
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Invalid download_relative_path"
    else:
        raise AssertionError("expected HTTPException")


def test_market_package_build_missing_parser_result_returns_404_before_command(monkeypatch, tmp_path):
    source_path = tmp_path / "downloads" / "HK" / "00700" / "2025" / "annual.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("pdf", encoding="utf-8")
    build_script = tmp_path / "scripts" / "build_hk.py"
    build_script.parent.mkdir(parents=True)
    build_script.write_text("print('build')\n", encoding="utf-8")
    monkeypatch.setattr(market_reports, "MARKET_BUILD_SCRIPTS", {**market_reports.MARKET_BUILD_SCRIPTS, "HK": build_script})

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setattr(market_reports, "run_command", fail_run)

    try:
        market_reports._run_market_package_build({
            "market": "HK",
            "source_path": source_path,
            "parser_result": "missing-parser-result",
        })
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "parser_result not found"
    else:
        raise AssertionError("expected HTTPException")


def test_market_ingestion_eval_missing_script_does_not_run_command(monkeypatch, tmp_path):
    missing_script = tmp_path / "scripts" / "run_market_ingestion_eval.py"

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setattr(market_reports, "MARKET_INGESTION_EVAL_SCRIPT", missing_script)
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    try:
        market_reports._run_market_ingestion_eval({})
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == f"Missing eval script: {missing_script}"
    else:
        raise AssertionError("expected HTTPException")


def test_eu_parse_endpoint_wraps_market_package_build(monkeypatch, tmp_path):
    downloads_root = tmp_path / "downloads"
    source_path = downloads_root / "EU" / "NL" / "ASML" / "2025" / "年报" / "report.html"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("<!doctype html><html><body><table><tr><td>Revenue</td><td>1</td></tr></table></body></html>", encoding="utf-8")
    seen = {}

    def fake_run_market_package_build(payload):
        seen.update(payload)
        return {"ok": True, "package": {"package_path": "/tmp/package"}}

    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", downloads_root)
    monkeypatch.setattr(market_reports, "_run_market_package_build", fake_run_market_package_build)

    result = asyncio.run(
        market_reports.parse_eu_market_report(
            JsonRequest({"market": "HK", "download_relative_path": "EU/NL/ASML/2025/年报/report.html"}),
            wait=True,
            _ops_user=None,
        )
    )

    assert result["ok"] is True
    assert seen["market"] == "EU"
    assert seen["download_relative_path"] == "EU/NL/ASML/2025/年报/report.html"


def test_market_import_command_uses_us_package_flag(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us"
    package_dir = _write_us_company_wiki_package(wiki_root)
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-1\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(market_reports, "run_command", fake_run)
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "US", wiki_root)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_package_import(
        _force_audit_payload(
            market="US",
            package_path=str(package_dir),
            ddl=True,
        )
    )

    assert result["ok"] is True
    package_index = seen["args"].index("--package")
    assert seen["args"][package_index + 1] == str(package_dir)
    assert seen["args"][-1] == "--ddl"


def test_hk_market_package_import_uses_configured_hk_database_env(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    captured = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-hk\n"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_package_import(
        _force_audit_payload(
            market="HK",
            package_path=str(package_dir),
            ddl=True,
        )
    )

    assert result["ok"] is True
    assert "import_hk_evidence_package_to_postgres.py" in " ".join(captured["args"])
    assert "--ddl" in captured["args"]
    assert captured["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"


def test_market_package_import_requires_explicit_legacy_flag(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_package_import({"market": "HK", "package_path": str(package_dir)})

    assert exc.value.status_code == 422
    assert "legacy_package_import=true" in exc.value.detail


def test_market_package_import_blocks_warning_quality_without_force(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_package_import({
            "market": "HK",
            "package_path": str(package_dir),
            "ddl": True,
            "legacy_package_import": True,
        })

    assert exc.value.status_code == 409
    assert exc.value.detail["quality_gates"]["import_blocked"] is True
    assert "hard blocks" in exc.value.detail["message"]
    assert "force=true" not in exc.value.detail["message"]


def test_market_package_import_rejects_force_without_audit_fields(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setattr(
        market_reports,
        "_quality_gates_for_package",
        lambda _package_dir: {
            "import_blocked": True,
            "vector_ingest_blocked": False,
            "force_allowed": True,
            "hard_gate_rule_ids": [],
            "soft_gate_rule_ids": ["package.quality_status.warning"],
        },
    )
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_package_import(
            {"market": "HK", "package_path": str(package_dir), "force": True, "legacy_package_import": True}
        )

    assert exc.value.status_code == 400
    assert {"reason", "operator", "ticket_or_change_id", "expires_at_or_one_shot"} <= set(
        exc.value.detail["missing_fields"]
    )


def test_market_package_import_blocks_hard_gate_even_with_force(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setattr(
        market_reports,
        "_quality_gates_for_package",
        lambda _package_dir: {
            "import_blocked": True,
            "vector_ingest_blocked": True,
            "force_allowed": False,
            "hard_gate_rule_ids": ["package.artifact_hashes.mismatch"],
            "soft_gate_rule_ids": [],
        },
    )
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_package_import(
            _force_audit_payload(market="HK", package_path=str(package_dir), ddl=True)
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["quality_gates"]["force_allowed"] is False
    assert exc.value.detail["quality_gates"]["import_blocked"] is True
    assert "review/quarantine" in exc.value.detail["message"]


def test_market_package_import_blocks_load_plan_can_import_false(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    _write_load_plan(
        package_dir,
        can_import=False,
        can_vector_ingest=True,
        canonical_decision="block",
        retrieval_decision="allow",
    )
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    _allow_market_quality_gate(monkeypatch)
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_package_import({"market": "HK", "package_path": str(package_dir), "legacy_package_import": True})

    assert exc.value.status_code == 409
    assert exc.value.detail["quality_gates"]["import_blocked"] is True
    assert exc.value.detail["quality_gates"]["load_plan"]["can_import"] is False
    assert "load_plan.canonical.block" in exc.value.detail["quality_gates"]["hard_gate_rule_ids"]


def test_market_package_import_allows_audited_load_plan_review_force(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    _write_load_plan(
        package_dir,
        can_import=False,
        can_vector_ingest=False,
        canonical_decision="review",
        retrieval_decision="review",
    )
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = "parse-run-hk\n"
        stderr = ""

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    _allow_market_quality_gate(monkeypatch)
    monkeypatch.setattr(market_reports, "run_command", lambda *_args, **_kwargs: Completed())

    result = market_reports._run_market_package_import(
        _force_audit_payload(market="HK", package_path=str(package_dir))
    )

    assert result["ok"] is True
    assert result["parse_run_id"] == "parse-run-hk"


def test_market_vector_ingest_blocks_hard_gate_even_with_force(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    ingest_script = tmp_path / "scripts" / "ingest_market_package.py"
    ingest_script.parent.mkdir(parents=True)
    ingest_script.write_text("# ingest", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setattr(market_reports, "MARKET_VECTOR_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(
        market_reports,
        "_quality_gates_for_package",
        lambda _package_dir: {
            "import_blocked": True,
            "vector_ingest_blocked": True,
            "force_allowed": False,
            "hard_gate_rule_ids": ["package.evidence.unresolvable"],
            "soft_gate_rule_ids": [],
        },
    )
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_vector_ingest(
            _force_audit_payload(market="HK", package_path=str(package_dir), dry_run=False)
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["action"] == "vector_ingest"
    assert exc.value.detail["quality_gates"]["vector_ingest_blocked"] is True
    assert exc.value.detail["quality_gates"]["force_allowed"] is False


def test_market_vector_ingest_blocks_load_plan_can_vector_false(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    _write_load_plan(
        package_dir,
        can_import=True,
        can_vector_ingest=False,
        canonical_decision="allow",
        retrieval_decision="block",
    )
    ingest_script = tmp_path / "scripts" / "ingest_market_package.py"
    ingest_script.parent.mkdir(parents=True)
    ingest_script.write_text("# ingest", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setattr(market_reports, "MARKET_VECTOR_INGEST_SCRIPT", ingest_script)
    _allow_market_quality_gate(monkeypatch)
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_vector_ingest({"market": "HK", "package_path": str(package_dir), "dry_run": False})

    assert exc.value.status_code == 409
    assert exc.value.detail["quality_gates"]["vector_ingest_blocked"] is True
    assert exc.value.detail["quality_gates"]["load_plan"]["can_vector_ingest"] is False
    assert "load_plan.retrieval.block" in exc.value.detail["quality_gates"]["hard_gate_rule_ids"]


def test_market_package_import_allows_audited_soft_gate_force_and_logs(monkeypatch, tmp_path, caplog):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-hk\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setattr(
        market_reports,
        "_quality_gates_for_package",
        lambda _package_dir: {
            "import_blocked": True,
            "vector_ingest_blocked": True,
            "force_allowed": True,
            "hard_gate_rule_ids": [],
            "soft_gate_rule_ids": ["package.quality_status.warning"],
        },
    )
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    caplog.set_level("INFO", logger=market_reports.__name__)

    result = market_reports._run_market_package_import(
        _force_audit_payload(
            market="HK",
            package_path=str(package_dir),
            database_url="postgres://secret",
        )
    )

    assert result["ok"] is True
    assert seen["args"][:3] == [market_reports.sys.executable, str(import_script), str(package_dir)]
    audit_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "CHG-123" in audit_logs
    assert "reviewed soft quality gate" in audit_logs
    assert "postgres://secret" not in audit_logs


def test_market_import_command_uses_positional_package_for_non_us(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_abc123")
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-hk\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setenv("DATABASE_URL", "postgres://inherited")
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_package_import(
        _force_audit_payload(
            market="HK",
            package_path=str(package_dir),
            database_url="postgres://secret",
            run_ddl=True,
        )
    )

    assert result["ok"] is True
    assert result["parse_run_id"] == "parse-run-hk"
    assert seen["args"][:3] == [market_reports.sys.executable, str(import_script), str(package_dir)]
    assert "--package" not in seen["args"]
    assert seen["args"][-1] == "--ddl"
    assert "--database-url" not in seen["args"]
    assert "postgres://secret" not in seen["args"]
    assert seen["kwargs"]["cwd"] == market_reports.REPO_ROOT
    assert seen["kwargs"]["timeout"] == 900
    assert seen["kwargs"]["env"]["DATABASE_URL"] == "postgres://secret"
    assert "postgres://secret" not in result["command"]
    assert "--database-url" not in result["command"]


def test_market_import_command_hk_default_env_sanitizes_inherited_database_url(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_abc123")
    import_script = tmp_path / "scripts" / "import_hk.py"
    import_script.parent.mkdir(parents=True)
    import_script.write_text("# import", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-hk\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db/siq")
    monkeypatch.delenv("SIQ_HK_PGDATABASE", raising=False)
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_package_import(
        _force_audit_payload(
            market="HK",
            package_path=str(package_dir),
        )
    )

    assert result["ok"] is True
    assert "--database-url" not in seen["args"]
    assert seen["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert "DATABASE_URL" not in seen["kwargs"]["env"]
    assert "postgresql://postgres:secret@db/siq" not in result["command"]


def test_market_document_full_import_command_uses_market_script_path_and_env(monkeypatch, tmp_path):
    market_reports.observability.reset_observability_metrics_for_tests()
    document_root = tmp_path / "parser-results" / "hk"
    document_full = document_root / "task-1" / "document_full.json"
    import_script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "log\nparse-run-doc-full\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db/siq")
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_market_document_full_import(
        {
            "market": "HK",
            "document_full_path": "task-1/document_full.json",
            "ddl": True,
        }
    )

    assert result["ok"] is True
    assert result["parse_run_id"] == "parse-run-doc-full"
    assert seen["args"] == [
        market_reports.sys.executable,
        str(import_script),
        str(document_full),
        "--market",
        "HK",
        "--ddl",
    ]
    assert seen["kwargs"]["cwd"] == market_reports.REPO_ROOT
    assert seen["kwargs"]["timeout"] == 900
    assert seen["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert "DATABASE_URL" not in seen["kwargs"]["env"]
    assert "postgresql://postgres:secret@db/siq" not in result["command"]
    assert result["selector"] == {
        "market": "HK",
        "document_full_path": str(document_full),
    }
    assert result["identity"]["market"] == "HK"
    assert result["identity"]["document_full_path"] == str(document_full)
    assert str(document_full.resolve()) in result["identity"]["path_keys"]
    assert "task-1/document_full.json" in result["identity"]["path_keys"]
    metrics = market_reports.observability.metrics_snapshot()
    assert metrics["ingestion_duration_seconds"]["HK|postgres_import|success"]["count"] == 1
    assert metrics["frontend_pipeline_job_failure_counts"] == {}


def test_market_document_full_import_task_id_returns_identity_selector(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    document_full = document_root / "task-identity" / "document_full.json"
    import_script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = "parse-task-identity\n"
        stderr = ""

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(market_reports, "run_command", lambda _args, **_kwargs: Completed())

    result = market_reports._run_market_document_full_import({"market": "HK", "task_id": "task-identity"})

    assert result["ok"] is True
    assert result["selector"] == {
        "market": "HK",
        "document_full_path": str(document_full),
        "task_id": "task-identity",
    }
    assert result["identity"]["task_id"] == "task-identity"
    assert result["identity"]["document_full_path"] == str(document_full)
    assert result["identity"]["path_keys"][0] == "task-identity"
    assert "task-identity/document_full.json" in result["identity"]["path_keys"]


def test_market_document_full_import_uses_explicit_database_url_without_command_leak(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "eu"
    document_full = document_root / "task-2" / "document_full.json"
    import_script = tmp_path / "imports" / "import_eu_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-run-eu\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "EU", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "EU", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "EU", "siq_eu")
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_market_document_full_import(
        {
            "market": "EU",
            "document_full_path": str(document_full),
            "database_url": "postgresql://postgres:explicit@db/siq_eu_private",
        }
    )

    assert result["ok"] is True
    assert "--database-url" not in seen["args"]
    assert "postgresql://postgres:explicit@db/siq_eu_private" not in seen["args"]
    assert seen["kwargs"]["env"]["DATABASE_URL"] == "postgresql://postgres:explicit@db/siq_eu_private"
    assert seen["kwargs"]["env"]["SIQ_EU_PGDATABASE"] == "siq_eu"
    assert "postgresql://postgres:explicit@db/siq_eu_private" not in result["command"]


def test_market_document_full_import_failure_records_pipeline_metric(monkeypatch, tmp_path):
    market_reports.observability.reset_observability_metrics_for_tests()
    document_root = tmp_path / "parser-results" / "hk"
    document_full = document_root / "task-failed" / "document_full.json"
    import_script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    class Completed:
        returncode = 2
        stdout = "partial\n"
        stderr = "failed"

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(market_reports, "run_command", lambda _args, **_kwargs: Completed())

    result = market_reports._run_market_document_full_import(
        {
            "market": "HK",
            "document_full_path": "task-failed/document_full.json",
        }
    )

    assert result["ok"] is False
    metrics = market_reports.observability.metrics_snapshot()
    assert metrics["ingestion_duration_seconds"]["HK|postgres_import|failure"]["count"] == 1
    assert metrics["frontend_pipeline_job_failure_counts"]["HK|postgres|returncode_2"] == 1


def test_market_document_full_import_accepts_us_sec_market_alias(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "us-sec"
    document_full = document_root / "filing-1" / "document_full.json"
    import_script = tmp_path / "imports" / "import_us_sec_document_full_to_postgres.py"
    for path in (document_full, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "parse-us\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "US", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "US", import_script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "US", "siq_us")
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_market_document_full_import(
        {"market": "US_SEC", "document_full_path": "filing-1/document_full.json"}
    )

    assert result["ok"] is True
    assert seen["args"] == [
        market_reports.sys.executable,
        str(import_script),
        str(document_full),
        "--market",
        "US",
    ]
    assert seen["kwargs"]["env"]["SIQ_US_PGDATABASE"] == "siq_us"
    assert result["selector"] == {
        "market": "US",
        "document_full_path": str(document_full),
    }
    assert result["identity"]["market"] == "US"
    assert result["identity"]["document_full_path"] == str(document_full)
    assert "filing-1/document_full.json" in result["identity"]["path_keys"]


def test_market_document_full_import_rejects_outside_root_before_command(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "jp"
    outside = tmp_path / "outside" / "document_full.json"
    import_script = tmp_path / "imports" / "import_jp_document_full_to_postgres.py"
    for path in (outside, import_script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_command should not be called")

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "JP", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "JP", import_script)
    monkeypatch.setattr(market_reports, "run_command", fail_run)

    with pytest.raises(HTTPException) as exc:
        market_reports._run_market_document_full_import(
            {"market": "JP", "document_full_path": str(outside)}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "document_full_path is outside the allowed market root"


def test_market_document_full_import_status_reports_configured_paths(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "kr"
    script = tmp_path / "imports" / "import_kr_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")

    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "KR", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "KR", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "KR", "siq_kr")

    result = asyncio.run(market_reports.market_document_full_import_status(market="KR"))

    assert result["ok"] is True
    assert result["markets"]["KR"]["document_full_root"] == str(document_root)
    assert result["markets"]["KR"]["document_full_root_exists"] is True
    assert result["markets"]["KR"]["script"] == str(script)
    assert result["markets"]["KR"]["script_exists"] is True
    assert result["markets"]["KR"]["database"] == "siq_kr"
    assert result["markets"]["KR"]["schema"] == "dart_kr"


def test_market_document_full_import_status_queries_postgres_counts(monkeypatch, tmp_path):
    market_reports.observability.reset_observability_metrics_for_tests()
    document_root = tmp_path / "parser-results" / "hk"
    script = tmp_path / "imports" / "import_kr_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.financial_facts": 0,
                    "pdf2md_hk.xbrl_facts_raw": 0,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.html_tables": 0,
                    "pdf2md_hk.pdf_tables": 0,
                    "pdf2md_hk.document_chunks": 3,
                    "pdf2md_hk.retrieval_chunks": 0,
                    "pdf2md_hk.evidence_citations": 2,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    fake_psycopg = types.SimpleNamespace(connect=lambda _url: FakeConn())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")

    result = asyncio.run(market_reports.market_document_full_import_status(market="HK", parse_run_id="parse-1"))

    postgres = result["markets"]["HK"]["postgres"]
    assert postgres["status"] == "postgres_ready"
    assert postgres["parse_runs"] == 1
    assert postgres["facts"] == 2
    assert postgres["tables"] == 1
    assert postgres["chunks"] == 3
    assert postgres["evidence"] == 2
    metrics = market_reports.observability.metrics_snapshot()
    assert metrics["ingestion_fact_counts"]["HK|parse_runs"] == 1
    assert metrics["ingestion_fact_counts"]["HK|facts"] == 2
    assert metrics["ingestion_fact_counts"]["HK|tables"] == 1
    assert metrics["ingestion_fact_counts"]["HK|chunks"] == 3
    assert metrics["ingestion_fact_counts"]["HK|evidence"] == 2


def test_market_document_full_import_status_matches_relative_document_full_path(monkeypatch, tmp_path):
    document_root = tmp_path / "data" / "pdf-parser" / "results"
    document_full = document_root / "hk-task" / "document_full.json"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_full, script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    root_relative_path = document_full.relative_to(tmp_path)
    market_relative_path = document_full.relative_to(document_root)
    seen_lookup_params = []

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if "from pdf2md_hk.parse_runs" in text and "raw->>'document_full_path'" in text:
                seen_lookup_params.extend(params or ())
                assert str(document_full.resolve()) in seen_lookup_params
                assert str(root_relative_path) in seen_lookup_params
                assert str(market_relative_path) in seen_lookup_params
                return FakeCursor(("parse-hk-relative", "HK:f1"))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.document_chunks": 3,
                    "pdf2md_hk.evidence_citations": 1,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setattr(market_reports, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")

    result = asyncio.run(
        market_reports.market_document_full_import_status(market="HK", document_full_path=str(market_relative_path))
    )

    postgres = result["markets"]["HK"]["postgres"]
    assert postgres["status"] == "postgres_ready"
    assert postgres["parse_run_id"] == "parse-hk-relative"
    assert postgres["selectors"]["document_full_path"] == str(document_full.resolve())
    assert seen_lookup_params


def test_market_document_full_import_status_rejects_non_document_full_path(monkeypatch, tmp_path):
    document_root = tmp_path / "data" / "pdf-parser" / "results"
    other_json = document_root / "hk-task" / "financial_data.json"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (other_json, script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(market_reports, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(market_reports.market_document_full_import_status(market="HK", document_full_path=str(other_json)))

    assert exc.value.status_code == 400
    assert exc.value.detail == "document_full_path must resolve to document_full.json"


def test_market_document_full_status_requires_evidence_for_ready(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "eu"
    script = tmp_path / "imports" / "import_eu_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "eu_ifrs.parse_runs": 1,
                    "eu_ifrs.financial_statement_items": 4,
                    "eu_ifrs.document_tables": 2,
                    "eu_ifrs.document_chunks": 5,
                    "eu_ifrs.evidence_citations": 0,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "EU", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "EU", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "EU", "siq_eu")

    result = asyncio.run(market_reports.market_document_full_import_status(market="EU", parse_run_id="parse-eu"))

    postgres = result["markets"]["EU"]["postgres"]
    assert postgres["status"] == "warning"
    assert postgres["facts"] == 4
    assert postgres["tables"] == 2
    assert postgres["chunks"] == 5
    assert postgres["evidence"] == 0
    assert postgres["missing_counts"] == ["evidence"]


def test_market_document_full_import_status_requires_market_for_document_full_path(tmp_path):
    document_full = tmp_path / "document_full.json"
    document_full.write_text("{}", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(market_reports.market_document_full_import_status(document_full_path=str(document_full)))

    assert exc.value.status_code == 400
    assert exc.value.detail == "market is required when document_full_path is provided"


@pytest.mark.parametrize(
    ("params", "selector_names"),
    [
        (
            {"market": "HK", "parse_run_id": "parse-hk-1", "filing_id": "HK:00700:2025-annual"},
            "parse_run_id, filing_id",
        ),
        (
            {
                "market": "US",
                "parse_run_id": "parse-us-1",
                "document_full_path": "filing-1/document_full.json",
            },
            "parse_run_id, document_full_path",
        ),
    ],
)
def test_market_document_full_status_http_rejects_ambiguous_identity_selectors(params, selector_names):
    with market_reports_client() as client:
        response = client.get("/api/market-reports/document-full/status", params=params)

    assert response.status_code == 400
    assert response.json()["detail"] == f"document_full status selectors are mutually exclusive: {selector_names}"


def test_market_document_full_status_http_rejects_filing_market_conflict():
    with market_reports_client() as client:
        response = client.get(
            "/api/market-reports/document-full/status",
            params={"market": "US", "filing_id": "HK:00700:2025-annual"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "market US conflicts with filing_id market HK"


def test_market_document_full_status_http_rejects_path_outside_selected_market(monkeypatch, tmp_path):
    hk_root = tmp_path / "parser-results" / "hk"
    us_root = tmp_path / "parser-results" / "us-sec"
    hk_document_full = hk_root / "task-hk" / "document_full.json"
    hk_document_full.parent.mkdir(parents=True)
    hk_document_full.write_text("{}", encoding="utf-8")
    us_root.mkdir(parents=True)
    monkeypatch.setattr(market_reports, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", hk_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "US", us_root)

    with market_reports_client() as client:
        response = client.get(
            "/api/market-reports/document-full/status",
            params={"market": "US", "document_full_path": str(hk_document_full)},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "document_full_path is outside the allowed market root"


@pytest.mark.parametrize("market", ("HK", "JP", "KR", "EU", "US"))
def test_market_document_full_status_http_keeps_non_cn_market_unscoped_without_identity(market):
    with market_reports_client() as client:
        response = client.get(
            "/api/market-reports/document-full/status",
            params={"market": market},
        )

    assert response.status_code == 200
    market_payload = response.json()["markets"][market]
    assert market_payload["schema"] == market_reports.MARKET_DOCUMENT_FULL_SCHEMAS[market]
    assert "postgres" not in market_payload


def test_market_document_full_status_requires_tables_for_us(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "us-sec"
    script = tmp_path / "imports" / "import_us_sec_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "sec_us.parse_runs": 1,
                    "sec_us.financial_statement_items": 4,
                    "sec_us.document_tables": 0,
                    "sec_us.document_chunks": 5,
                    "sec_us.evidence_citations": 8,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "US", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "US", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "US", "siq_us")

    result = asyncio.run(market_reports.market_document_full_import_status(market="US_SEC", parse_run_id="parse-us"))

    postgres = result["markets"]["US"]["postgres"]
    assert postgres["status"] == "warning"
    assert postgres["facts"] == 4
    assert postgres["tables"] == 0
    assert postgres["chunks"] == 5
    assert postgres["evidence"] == 8


def test_market_document_full_import_status_path_lookup_uses_parse_run_timestamps(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")
    document_full = document_root / "task-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text("{}", encoding="utf-8")

    seen_sql = []

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            seen_sql.append(text)
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if "artifact_hashes->>'document_full.json'" in text and "from pdf2md_hk.parse_runs" in text:
                return FakeCursor(("parse-1", "filing-1", None))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.retrieval_chunks": 3,
                    "pdf2md_hk.evidence_citations": 2,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    fake_psycopg = types.SimpleNamespace(connect=lambda _url: FakeConn())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")

    result = asyncio.run(
        market_reports.market_document_full_import_status(market="HK", document_full_path=str(document_full))
    )

    postgres = result["markets"]["HK"]["postgres"]
    assert postgres["status"] == "postgres_ready"
    assert postgres["parse_run_id"] == "parse-1"
    lookup_sql = next(
        sql
        for sql in seen_sql
        if "artifact_hashes->>'document_full.json'" in sql and "from pdf2md_hk.parse_runs" in sql
    )
    assert "created_at" not in lookup_sql
    assert "completed_at desc nulls last, started_at desc nulls last, parse_run_id desc" in lookup_sql


def test_market_document_full_import_status_matches_task_id_alias(monkeypatch, tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    for path in (document_root, script):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_root.mkdir(parents=True, exist_ok=True)
    script.write_text("# import", encoding="utf-8")
    seen_lookup_params = []

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if "raw->'task'->>'task_id'" in text:
                seen_lookup_params.extend(params or ())
                return FakeCursor(("parse-task-1", "filing-task-1"))
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.document_chunks": 3,
                    "pdf2md_hk.evidence_citations": 2,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_ROOTS, "HK", document_root)
    monkeypatch.setitem(market_reports.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", script)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")

    result = asyncio.run(market_reports.market_document_full_import_status(market="HK", task_id="task-1"))

    postgres = result["markets"]["HK"]["postgres"]
    assert postgres["status"] == "postgres_ready"
    assert postgres["selectors"] == {"task_id": "task-1"}
    assert postgres["parse_run_id"] == "parse-task-1"
    assert seen_lookup_params == ["task-1", "%/task-1/document_full.json"]


def test_hk_market_package_import_uses_hk_database_env(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk"
    package_dir = _write_market_package(wiki_root, "companies", "00700-TENCENT", "reports", "2025-annual-12100024")
    seen = {}

    class Completed:
        returncode = 0
        stdout = "run-hk\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_package_import(
        _force_audit_payload(market="HK", package_path=str(package_dir), ddl=True)
    )

    assert result["ok"] is True
    assert "import_hk_evidence_package_to_postgres.py" in " ".join(seen["args"])
    assert "--ddl" in seen["args"]
    assert seen["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"


def test_market_vector_ingest_command_contract_and_summary(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "hk_reports"
    package_dir = _write_market_package(wiki_root, "00700", "2025", "annual_abc123")
    ingest_script = tmp_path / "scripts" / "ingest_market_package.py"
    ingest_script.parent.mkdir(parents=True)
    ingest_script.write_text("# ingest", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = 'log line\n{"inserted": 3, "collection": "siq_market"}\n'
        stderr = "warn\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setattr(market_reports, "MARKET_VECTOR_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_vector_ingest(
        _force_audit_payload(
            market="HK",
            package_path=str(package_dir),
            collection="siq_market",
            embed_url="http://embed.local",
            embed_model="text-embedding-3-small",
            vector_dim=1536,
        )
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["summary"] == {"inserted": 3, "collection": "siq_market"}
    assert seen["args"] == [
        market_reports.sys.executable,
        str(ingest_script),
        "--package",
        str(package_dir),
        "--batch-tag",
        "market-evidence",
        "--collection",
        "siq_market",
        "--embed-url",
        "http://embed.local",
        "--embed-model",
        "text-embedding-3-small",
        "--vector-dim",
        "1536",
        "--dry-run",
    ]
    assert seen["kwargs"] == {"cwd": market_reports.REPO_ROOT, "timeout": 1800}


def test_market_vector_ingest_can_disable_dry_run(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    ingest_script = tmp_path / "scripts" / "ingest_market_package.py"
    ingest_script.parent.mkdir(parents=True)
    ingest_script.write_text("# ingest", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        return Completed()

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "US", wiki_root)
    monkeypatch.setattr(market_reports, "MARKET_VECTOR_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    _allow_market_quality_gate(monkeypatch)

    result = market_reports._run_market_vector_ingest(
        _force_audit_payload(
            market="US",
            package_path=str(package_dir),
            batch_tag="prod-load",
            dry_run=False,
        )
    )

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["summary"] is None
    assert "--dry-run" not in seen["args"]
    assert seen["args"][seen["args"].index("--batch-tag") + 1] == "prod-load"


def test_market_ingestion_eval_run_reads_requested_output(monkeypatch, tmp_path):
    eval_script = tmp_path / "scripts" / "run_market_ingestion_eval.py"
    eval_script.parent.mkdir(parents=True)
    eval_script.write_text("# eval", encoding="utf-8")
    output_path = tmp_path / "reports" / "eval.json"
    markdown_path = tmp_path / "reports" / "eval.md"
    seen = {}

    class Completed:
        returncode = 0
        stdout = "eval ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        output_path.parent.mkdir(parents=True)
        output_path.write_text(json.dumps({"score": 0.98, "cases": 4}), encoding="utf-8")
        markdown_path.write_text("# Eval", encoding="utf-8")
        return Completed()

    monkeypatch.setattr(market_reports, "MARKET_INGESTION_EVAL_SCRIPT", eval_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_market_ingestion_eval(
        {
            "output": str(output_path),
            "markdown": str(markdown_path),
        }
    )

    assert result["ok"] is True
    assert result["report"] == {"score": 0.98, "cases": 4}
    assert result["markdown_path"] == str(markdown_path)
    assert seen["args"] == [
        market_reports.sys.executable,
        str(eval_script),
        "--output",
        str(output_path),
        "--markdown",
        str(markdown_path),
    ]
    assert seen["kwargs"] == {"cwd": market_reports.REPO_ROOT, "timeout": 900}


def test_market_ingestion_eval_report_reads_files_and_optional_markdown(monkeypatch, tmp_path):
    report_path = tmp_path / "market_eval.json"
    markdown_path = tmp_path / "market_eval.md"
    report_path.write_text(json.dumps({"summary": {"passed": 2}}), encoding="utf-8")
    markdown_path.write_text("# Market Eval", encoding="utf-8")

    monkeypatch.setattr(market_reports, "MARKET_INGESTION_EVAL_REPORT_PATH", report_path)
    monkeypatch.setattr(market_reports, "MARKET_INGESTION_EVAL_MARKDOWN_PATH", markdown_path)

    result = asyncio.run(market_reports.market_ingestion_eval_report(include_markdown=True))

    assert result["ok"] is True
    assert result["report_path"] == str(report_path)
    assert result["markdown_path"] == str(markdown_path)
    assert result["report"] == {"summary": {"passed": 2}}
    assert result["markdown"] == "# Market Eval"

    without_markdown = asyncio.run(market_reports.market_ingestion_eval_report(include_markdown=False))
    assert "markdown" not in without_markdown


def test_us_sec_case_set_status_reads_files_and_keeps_response_shape(monkeypatch, tmp_path):
    case_set_path = tmp_path / "case_set.json"
    ingest_report_path = tmp_path / "ingest_report.json"
    case_set_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "ticker": "AAPL",
                        "company_name": "Apple Inc.",
                        "fiscal_year": 2025,
                        "period_end": "2025-09-27",
                        "filing_date": "2025-10-31",
                        "quality_status": "pass",
                        "quality_summary": {
                            "xbrl_fact_count": 10,
                            "normalized_metric_count": 4,
                            "section_count": 2,
                            "table_count": 3,
                        },
                        "package_path": "data/wiki/us_sec/AAPL/package",
                    },
                    {
                        "ticker": "MSFT",
                        "quality_status": "warning",
                        "quality_summary": {"xbrl_fact_count": 5, "section_count": 1},
                        "package_path": "data/wiki/us_sec/MSFT/package",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    ingest_report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-03T00:00:00Z",
                "summary": {"inserted": 7},
                "package_count": 2,
                "collection": "siq_documents",
                "batch_tag": "market-evidence",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)
    monkeypatch.setattr(market_reports, "US_SEC_INGEST_REPORT_PATH", ingest_report_path)

    result = asyncio.run(market_reports.us_sec_case_set_status())

    assert result["case_set_path"] == str(case_set_path)
    assert result["ingest_report_path"] == str(ingest_report_path)
    assert result["company_count"] == 2
    assert result["quality"] == {"pass": 1, "warning": 1}
    assert result["counts"] == {
        "xbrl_fact_count": 15,
        "normalized_metric_count": 4,
        "section_count": 3,
        "table_count": 3,
    }
    assert result["items"][0]["ticker"] == "AAPL"
    assert result["items"][1]["company_name"] is None
    assert result["ingest_report"] == {
        "generated_at": "2026-07-03T00:00:00Z",
        "summary": {"inserted": 7},
        "package_count": 2,
        "collection": "siq_documents",
        "batch_tag": "market-evidence",
    }


def test_us_sec_package_selector_preserves_error_mapping_and_package_path_priority(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    case_set_path = tmp_path / "case_set.json"
    case_set_path.write_text(
        json.dumps({"items": [{"ticker": "MSFT", "package_path": str(wiki_root / "MSFT" / "missing")}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)

    assert market_reports._package_from_selector({
        "package_path": str(package_dir),
        "ticker": "MSFT",
    }) == package_dir

    for payload, status_code, detail in (
        ({}, 400, "ticker or package_path is required"),
        ({"ticker": "TSLA"}, 404, "No package for ticker TSLA"),
    ):
        try:
            market_reports._package_from_selector(payload)
        except HTTPException as exc:
            assert exc.status_code == status_code
            assert exc.detail == detail
        else:
            raise AssertionError("expected HTTPException")


def test_market_package_build_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_package_build", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.build_market_package(
            JsonRequest({"market": "US", "download_relative_path": "US/demo/report.html"}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert seen["kind"] == "market-package-build"
    assert seen["created_by"]["username"] == "ops"
    assert seen["target_result"]["ok"] is True


def test_eu_parse_queues_background_job_and_forces_market(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_package_build", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.parse_eu_market_report(
            JsonRequest({"market": "HK", "download_relative_path": "EU/NL/ASML/2025/年报/report.html"}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "eu-market-report-parse-job-1"
    assert seen["kind"] == "eu-market-report-parse"
    assert seen["target_result"]["payload"]["market"] == "EU"
    assert seen["target_result"]["payload"]["download_relative_path"] == "EU/NL/ASML/2025/年报/report.html"


def test_market_package_import_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_package_import", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.import_market_package(
            JsonRequest({"market": "US", "package_path": "data/wiki/us_sec/AAPL/package", "ddl": True, "legacy_package_import": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "market-package-import-job-1"
    assert seen["kind"] == "market-package-import"
    assert seen["target_result"]["payload"]["ddl"] is True


def test_market_package_import_queue_requires_explicit_legacy_flag(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_package_import", lambda payload: {"ok": True, "payload": payload})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            market_reports.import_market_package(
                JsonRequest({"market": "US", "package_path": "data/wiki/us_sec/AAPL/package", "ddl": True}),
                wait=False,
                _ops_user=DummyUser(),
            )
        )

    assert exc.value.status_code == 422
    assert seen == {}


def test_market_document_full_import_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_document_full_import", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.import_market_document_full(
            JsonRequest({"market": "HK", "document_full_path": "task-1/document_full.json", "ddl": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "market-document-full-import-job-1"
    assert seen["kind"] == "market-document-full-import"
    assert seen["created_by"]["email"] == "ops@example.test"
    assert seen["target_result"]["payload"]["document_full_path"] == "task-1/document_full.json"
    assert seen["target_result"]["payload"]["ddl"] is True


def test_market_vector_ingest_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_vector_ingest", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.vector_ingest_market_package(
            JsonRequest({"market": "US", "package_path": "data/wiki/us_sec/AAPL/package", "dry_run": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "market-vector-ingest-job-1"
    assert seen["kind"] == "market-vector-ingest"
    assert seen["created_by"]["email"] == "ops@example.test"
    assert seen["target_result"]["payload"]["dry_run"] is True


def test_market_ingestion_eval_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_ingestion_eval", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.run_market_ingestion_eval(
            JsonRequest({"output": "tmp/eval.json"}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "market-ingestion-eval-job-1"
    assert seen["kind"] == "market-ingestion-eval"
    assert seen["target_result"]["payload"] == {"output": "tmp/eval.json"}
    for internal_key in ("schema_version", "id", "subject", "steps", "logs", "attempts", "source_schema", "legacy_payload"):
        assert internal_key not in result


def test_market_ingestion_eval_wait_runs_inline(monkeypatch):
    seen = {}

    def fake_run(payload):
        seen["payload"] = payload
        return {"ok": True, "payload": payload}

    monkeypatch.setattr(market_reports, "_run_market_ingestion_eval", fake_run)

    result = asyncio.run(
        market_reports.run_market_ingestion_eval(
            JsonRequest({"output": "tmp/eval.json", "markdown": "tmp/eval.md"}),
            wait=True,
            _ops_user=DummyUser(),
        )
    )

    assert result == {"ok": True, "payload": {"output": "tmp/eval.json", "markdown": "tmp/eval.md"}}
    assert seen["payload"] == {"output": "tmp/eval.json", "markdown": "tmp/eval.md"}


def test_market_ingestion_eval_rejects_non_object_payload(monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("_run_market_ingestion_eval should not be called")

    monkeypatch.setattr(market_reports, "_run_market_ingestion_eval", fail_run)

    try:
        asyncio.run(
            market_reports.run_market_ingestion_eval(
                JsonRequest(["tmp/eval.json"]),
                wait=True,
                _ops_user=DummyUser(),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "JSON object payload is required"
    else:
        raise AssertionError("expected HTTPException")


def test_us_sec_safe_ingest_args_validates_filters(monkeypatch, tmp_path):
    ingest_script = tmp_path / "scripts" / "ingest_sec_case_set.py"
    ingest_script.parent.mkdir(parents=True)
    ingest_script.write_text("# ingest", encoding="utf-8")
    case_set_path = tmp_path / "case_set.json"
    report_path = tmp_path / "ingest_report.json"
    monkeypatch.setattr(market_reports, "US_SEC_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)
    monkeypatch.setattr(market_reports, "US_SEC_INGEST_REPORT_PATH", report_path)

    args = market_reports._safe_ingest_args(
        {
            "tickers": " aapl,msft ",
            "batch_tag": "market-evidence:2026",
            "postgres": True,
            "dry_run": False,
        }
    )

    assert args == [
        market_reports.sys.executable,
        str(ingest_script),
        "--case-set",
        str(case_set_path),
        "--report",
        str(report_path),
        "--postgres",
        "--tickers",
        "AAPL,MSFT",
        "--batch-tag",
        "market-evidence:2026",
    ]

    for payload, detail in (
        ({"tickers": "../AAPL"}, "Invalid tickers"),
        ({"batch_tag": "bad tag"}, "Invalid batch_tag"),
    ):
        try:
            market_reports._safe_ingest_args(payload)
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == detail
        else:
            raise AssertionError("expected HTTPException")


def test_us_sec_semantic_flag_runs_llm_wiki_semantics(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    ingest_script = scripts_dir / "ingest_sec_case_set.py"
    rule_script = scripts_dir / "run_market_rule_semantics.py"
    llm_script = scripts_dir / "run_market_llm_semantics.py"
    for script in (ingest_script, rule_script, llm_script):
        script.write_text("# script\n", encoding="utf-8")
    case_set_path = tmp_path / "case_set.json"
    report_path = tmp_path / "ingest_report.json"
    report_path.write_text('{"package_count": 1}', encoding="utf-8")
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Completed()

    monkeypatch.setattr(market_reports, "US_SEC_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)
    monkeypatch.setattr(market_reports, "US_SEC_INGEST_REPORT_PATH", report_path)
    monkeypatch.setattr(market_reports, "MARKET_RULE_SEMANTIC_SCRIPT", rule_script)
    monkeypatch.setattr(market_reports, "MARKET_LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(market_reports, "_us_sec_company_dirs_from_payload", lambda payload: ["AAPL-Apple-Inc"])
    monkeypatch.setattr(market_reports, "_llm_semantic_env", lambda: {"SIQ_LLM_SEMANTIC_PROVIDER": "local"})
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_us_sec_case_set_ingest({
        "dry_run": False,
        "postgres": False,
        "semantic": True,
        "tickers": "AAPL",
        "batch_tag": "us-sec-case-set-50",
    })

    assert result["ok"] is True
    assert result["semantic_only"] is True
    assert result["semantic_prestep"][0]["companyDir"] == "AAPL-Apple-Inc"
    assert len(calls) == 2
    assert calls[0]["args"] == [
        market_reports.sys.executable,
        str(rule_script),
        "--market",
        "US",
        "--company",
        "AAPL-Apple-Inc",
        "--skip-existing",
    ]
    assert calls[1]["args"] == [
        market_reports.sys.executable,
        str(llm_script),
        "--market",
        "US",
        "--company",
        "AAPL-Apple-Inc",
        "--skip-existing",
        "--allow-failures",
    ]
    assert calls[1]["kwargs"]["env"] == {"SIQ_LLM_SEMANTIC_PROVIDER": "local"}


def test_us_sec_ingest_ignores_milvus_flag_for_financial_pipeline(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    ingest_script = scripts_dir / "ingest_sec_case_set.py"
    rule_script = scripts_dir / "run_market_rule_semantics.py"
    llm_script = scripts_dir / "run_market_llm_semantics.py"
    for script in (ingest_script, rule_script, llm_script):
        script.write_text("# script\n", encoding="utf-8")
    case_set_path = tmp_path / "case_set.json"
    report_path = tmp_path / "ingest_report.json"
    report_path.write_text('{"package_count": 1}', encoding="utf-8")
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Completed()

    monkeypatch.setattr(market_reports, "US_SEC_INGEST_SCRIPT", ingest_script)
    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)
    monkeypatch.setattr(market_reports, "US_SEC_INGEST_REPORT_PATH", report_path)
    monkeypatch.setattr(market_reports, "MARKET_RULE_SEMANTIC_SCRIPT", rule_script)
    monkeypatch.setattr(market_reports, "MARKET_LLM_SEMANTIC_SCRIPT", llm_script)
    monkeypatch.setattr(market_reports, "_us_sec_company_dirs_from_payload", lambda payload: ["AAPL-Apple-Inc"])
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_us_sec_case_set_ingest({
        "dry_run": False,
        "postgres": False,
        "milvus": True,
        "tickers": "AAPL",
        "batch_tag": "us-sec-case-set-50",
    })

    assert result["ok"] is True
    assert result["semantic_prestep"] == []
    assert len(calls) == 1
    ingest_args = calls[0]["args"]
    assert ingest_args[:2] == [market_reports.sys.executable, str(ingest_script)]
    assert "--milvus" not in ingest_args
    assert "--postgres" not in ingest_args


def test_us_sec_rebuild_package_command_contract(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = wiki_root / "AAPL" / "2025" / "10-K_demo"
    raw_dir = package_dir / "raw"
    raw_dir.mkdir(parents=True)
    source_path = raw_dir / "filing.htm"
    source_path.write_text("<html><body>10-K</body></html>", encoding="utf-8")
    metadata_path = raw_dir / "filing.metadata.json"
    metadata_path.write_text('{"ticker":"AAPL"}', encoding="utf-8")
    (package_dir / "manifest.json").write_text(
        json.dumps({"local_source_path": "raw/filing.htm"}),
        encoding="utf-8",
    )
    case_set_path = tmp_path / "case_set.json"
    case_set_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "ticker": "AAPL",
                        "filing_date": "2025-10-31",
                        "period_end": "2025-09-27",
                        "package_path": str(package_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    build_script = tmp_path / "scripts" / "build_sec_evidence_package.py"
    build_script.parent.mkdir(parents=True)
    build_script.write_text("# build", encoding="utf-8")
    seen = {}

    class Completed:
        returncode = 0
        stdout = f"{package_dir}\n"
        stderr = "warn\n"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        tmp_source = Path(args[2])
        tmp_metadata = Path(args[args.index("--metadata") + 1])
        assert tmp_source.name == "filing.htm"
        assert tmp_source.read_text(encoding="utf-8") == "<html><body>10-K</body></html>"
        assert tmp_metadata.name == "filing.metadata.json"
        assert tmp_metadata.read_text(encoding="utf-8") == '{"ticker":"AAPL"}'
        return Completed()

    monkeypatch.setattr(market_reports, "US_SEC_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(market_reports, "US_SEC_CASE_SET_PATH", case_set_path)
    monkeypatch.setattr(market_reports, "US_SEC_PACKAGE_BUILD_SCRIPT", build_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)
    monkeypatch.setattr(market_reports, "_read_package_detail", lambda package: {"package_path": str(package)})

    result = market_reports._run_us_sec_rebuild_package("aapl", {"force": True})

    assert result["ok"] is True
    assert result["ticker"] == "AAPL"
    assert result["package"] == {"package_path": str(package_dir)}
    assert result["stdout"] == f"{package_dir}\n"
    assert result["stderr"] == "warn\n"
    assert seen["args"][:2] == [market_reports.sys.executable, str(build_script)]
    assert seen["args"][3] == "--force"
    assert seen["args"][seen["args"].index("--output-root") + 1] == str(wiki_root)
    assert seen["kwargs"] == {"cwd": market_reports.REPO_ROOT, "timeout": 900}


def test_us_sec_ingest_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_us_sec_case_set_ingest", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.us_sec_case_set_ingest(
            JsonRequest({"tickers": "AAPL,MSFT", "dry_run": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "us-sec-ingest-job-1"
    assert seen["kind"] == "us-sec-ingest"
    assert seen["target_result"]["payload"]["tickers"] == "AAPL,MSFT"


def test_us_sec_rebuild_queues_background_job(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(
        market_reports,
        "_run_us_sec_rebuild_package",
        lambda ticker, payload: {"ok": True, "ticker": ticker, "payload": payload},
    )

    result = asyncio.run(
        market_reports.us_sec_rebuild_package(
            "aapl",
            JsonRequest({"force": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "us-sec-rebuild-job-1"
    assert seen["kind"] == "us-sec-rebuild"
    assert seen["target_result"] == {"ok": True, "ticker": "aapl", "payload": {"force": True}}


def test_market_report_job_status_uses_service(monkeypatch):
    snapshot = {
        "job_id": "job-123",
        "kind": "market-vector-ingest",
        "status": "running",
        "created_at": "2026-07-03T10:00:00Z",
        "started_at": "2026-07-03T10:00:01Z",
        "finished_at": None,
        "created_by": {"id": 42, "username": "ops"},
        "result": None,
        "error": None,
    }
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: snapshot)

    result = asyncio.run(market_reports.market_report_job_status("job-123", _ops_user=None))

    assert result == snapshot
    assert "target" not in result


def canonical_market_eval_job_snapshot() -> dict:
    return {
        "schema_version": "siq_job_envelope_v1",
        "id": "market-ingestion-eval-job-1",
        "kind": "market-ingestion-eval",
        "subject": {"output": "tmp/eval.json"},
        "status": "succeeded",
        "created_at": "2026-07-04T12:00:00Z",
        "started_at": "2026-07-04T12:01:00Z",
        "finished_at": "2026-07-04T12:02:00Z",
        "updated_at": "2026-07-04T12:02:00Z",
        "created_by": {"id": 42, "username": "ops"},
        "result": {"ok": True, "report": "tmp/eval.json"},
        "error": None,
        "steps": [],
        "logs": [{"message": "internal only"}],
        "attempts": 1,
        "source_schema": "market_file_backed_job_v1",
        "legacy_payload": {
            "job_id": "market-ingestion-eval-job-1",
            "kind": "market-ingestion-eval",
            "status": "succeeded",
            "created_at": "2026-07-04T12:00:00Z",
            "started_at": "2026-07-04T12:01:00Z",
            "finished_at": "2026-07-04T12:02:00Z",
            "updated_at": "2026-07-04T12:02:00Z",
            "created_by": {"id": 42, "username": "ops"},
            "result": {"ok": True, "report": "tmp/eval.json"},
            "error": None,
            "target": lambda: None,
        },
        "target": lambda: None,
    }


def test_market_report_job_status_projects_market_eval_canonical_payload(monkeypatch):
    canonical_snapshot = canonical_market_eval_job_snapshot()
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: canonical_snapshot)

    result = asyncio.run(market_reports.market_report_job_status("market-ingestion-eval-job-1", _ops_user=None))

    assert result == {
        "job_id": "market-ingestion-eval-job-1",
        "kind": "market-ingestion-eval",
        "status": "succeeded",
        "created_at": "2026-07-04T12:00:00Z",
        "started_at": "2026-07-04T12:01:00Z",
        "finished_at": "2026-07-04T12:02:00Z",
        "updated_at": "2026-07-04T12:02:00Z",
        "created_by": {"id": 42, "username": "ops"},
        "result": {"ok": True, "report": "tmp/eval.json"},
        "error": None,
    }
    for internal_key in (
        "schema_version",
        "id",
        "subject",
        "steps",
        "logs",
        "attempts",
        "source_schema",
        "legacy_payload",
        "target",
        "jobId",
        "createdAt",
        "updatedAt",
        "finishedAt",
    ):
        assert internal_key not in result


def test_market_report_job_status_http_route_projects_public_payload(monkeypatch):
    canonical_snapshot = canonical_market_eval_job_snapshot()
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: canonical_snapshot)

    client = market_reports_client()
    try:
        response = client.get("/api/jobs/market-ingestion-eval-job-1")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "market-ingestion-eval-job-1",
        "kind": "market-ingestion-eval",
        "status": "succeeded",
        "created_at": "2026-07-04T12:00:00Z",
        "started_at": "2026-07-04T12:01:00Z",
        "finished_at": "2026-07-04T12:02:00Z",
        "updated_at": "2026-07-04T12:02:00Z",
        "created_by": {"id": 42, "username": "ops"},
        "result": {"ok": True, "report": "tmp/eval.json"},
        "error": None,
    }


def test_market_report_job_status_http_route_returns_404_json(monkeypatch):
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: None)

    client = market_reports_client()
    try:
        response = client.get("/api/jobs/missing-job")
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_market_report_job_status_returns_404_for_missing_job(monkeypatch):
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: None)

    try:
        asyncio.run(market_reports.market_report_job_status("missing-job", _ops_user=None))
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Job not found"
    else:
        raise AssertionError("expected HTTPException")
