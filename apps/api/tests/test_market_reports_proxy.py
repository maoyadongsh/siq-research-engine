import sys
import asyncio
import importlib.util
import hashlib
import io
import json
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


def _write_market_package(root: Path, *parts: str) -> Path:
    package_dir = root.joinpath(*parts)
    package_dir.mkdir(parents=True)
    (package_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return package_dir


class DummyUser:
    id = 42
    username = "ops"
    email = "ops@example.test"
    full_name = "Ops User"
    role = "admin"


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


def test_v1_proxy_preserves_finder_path(monkeypatch):
    seen = {}

    async def fake_proxy_request(*, base_url, upstream_path, request, timeout=market_reports.MARKET_REPORT_PROXY_TIMEOUT):
        seen.update({"base_url": base_url, "upstream_path": upstream_path, "timeout": timeout})
        return "ok"

    monkeypatch.setattr(market_reports, "_proxy_request", fake_proxy_request)

    result = asyncio.run(market_reports.proxy_market_report_finder("reports/recent", DummyRequest()))

    assert result == "ok"
    assert seen["base_url"] == market_reports.REPORT_FINDER_BASE
    assert seen["upstream_path"] == "/v1/reports/recent"


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
            upstream_path="/v1/reports/search",
            request=Request(),
            timeout=1.25,
        )
    )

    assert seen["method"] == "POST"
    assert seen["url"] == "http://finder/v1/reports/search"
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

    async def fake_finder_assist(*, report_finder_base, payload, timeout):
        seen.update({"report_finder_base": report_finder_base, "payload": payload, "timeout": timeout})
        return {"ok": True}

    monkeypatch.setattr(market_report_proxy, "finder_assist", fake_finder_assist)

    result = asyncio.run(market_reports._finder_assist({"prompt": "demo"}))

    assert result == {"ok": True}
    assert seen == {
        "report_finder_base": market_reports.REPORT_FINDER_BASE,
        "payload": {"prompt": "demo"},
        "timeout": market_reports.MARKET_REPORT_PROXY_TIMEOUT,
    }


def test_market_rules_route_wrappers_use_rules_base(monkeypatch):
    calls = []

    async def fake_proxy_rules_get(*, market_rules_base, upstream_path):
        calls.append({"market_rules_base": market_rules_base, "upstream_path": upstream_path})
        return {"path": upstream_path}

    monkeypatch.setattr(market_report_proxy, "proxy_rules_get", fake_proxy_rules_get)

    modules = asyncio.run(market_reports.market_modules())
    cn_rules = asyncio.run(market_reports.cn_market_rules())

    assert modules == {"path": "/markets"}
    assert cn_rules == {"path": "/markets/cn/rules"}
    assert calls == [
        {"market_rules_base": market_reports.MARKET_RULES_BASE, "upstream_path": "/markets"},
        {"market_rules_base": market_reports.MARKET_RULES_BASE, "upstream_path": "/markets/cn/rules"},
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


def test_active_llm_provider_prefers_cloud_stepfun(monkeypatch):
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

    assert active == "cloud"
    assert provider["baseUrl"] == "https://api.stepfun.com/v1"


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

    def fake_persist_us_sec_upload(item, **kwargs):
        calls.append({"filename": item.filename, "kwargs": kwargs})
        return {
            "file_name": item.filename,
            "relative_path": f"us-sec/uploads/{item.filename}",
        }

    async def fake_record_user_artifact_async(async_session, **kwargs):
        calls.append({"async_session": async_session, "artifact": kwargs})

    monkeypatch.setattr(market_reports, "_persist_us_sec_upload", fake_persist_us_sec_upload)
    monkeypatch.setattr(market_reports, "record_user_artifact_async", fake_record_user_artifact_async)

    result = asyncio.run(
        market_reports.us_sec_upload_files(
            UploadRouteRequest(),
            files=[type("Upload", (), {"filename": "aapl-10k.pdf"})()],
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
    def fake_persist_us_sec_upload(item, **kwargs):
        return {
            "file_name": item.filename,
            "relative_path": f"us-sec/uploads/{item.filename}",
        }

    async def fake_record_user_artifact_async(*args, **kwargs):
        raise RuntimeError("workspace unavailable")

    monkeypatch.setattr(market_reports, "_persist_us_sec_upload", fake_persist_us_sec_upload)
    monkeypatch.setattr(market_reports, "record_user_artifact_async", fake_record_user_artifact_async)

    result = asyncio.run(
        market_reports.us_sec_upload_files(
            UploadRouteRequest(),
            files=[type("Upload", (), {"filename": "aapl-10k.pdf"})()],
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


def test_us_sec_upload_persist_writes_build_compatible_metadata(monkeypatch, tmp_path):
    content = b"<html><body>10-K</body></html>"
    digest = hashlib.sha256(content).hexdigest()

    class FixedDateTime(market_reports.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 3, 12, 34, 56, tzinfo=tz)

    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    monkeypatch.setattr(market_reports, "datetime", FixedDateTime)
    upload = type(
        "Upload",
        (),
        {
            "filename": "apple-10k.htm",
            "content_type": "text/html",
            "file": io.BytesIO(content),
        },
    )()

    result = market_reports._persist_us_sec_upload(
        upload,
        ticker="AAPL",
        company_name="Apple Inc.",
        report_type="10-K",
        fiscal_year=2025,
        period_end="2025-09-27",
        filing_date="2025-10-31",
    )

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


def test_us_sec_upload_persist_rejects_unsupported_suffix_and_empty_file(monkeypatch, tmp_path):
    monkeypatch.setattr(market_reports, "REPORT_DOWNLOADS_ROOT", tmp_path / "downloads")
    bad_suffix = type("Upload", (), {"filename": "notes.exe", "content_type": "application/octet-stream", "file": io.BytesIO(b"x")})()
    empty_file = type("Upload", (), {"filename": "empty.pdf", "content_type": "application/pdf", "file": io.BytesIO(b"")})()

    try:
        market_reports._persist_us_sec_upload(
            bad_suffix,
            ticker="AAPL",
            company_name="Apple Inc.",
            report_type="10-K",
            fiscal_year=2025,
            period_end="2025-09-27",
            filing_date="2025-10-31",
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Only PDF" in exc.detail
    else:
        raise AssertionError("expected HTTPException")

    try:
        market_reports._persist_us_sec_upload(
            empty_file,
            ticker="AAPL",
            company_name="Apple Inc.",
            report_type="10-K",
            fiscal_year=2025,
            period_end="2025-09-27",
            filing_date="2025-10-31",
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Uploaded file is empty"
    else:
        raise AssertionError("expected HTTPException")


def test_market_package_summary_reads_us_package():
    package_dir = (
        market_reports.REPO_ROOT
        / "data"
        / "wiki"
        / "us_sec"
        / "AAPL"
        / "2025"
        / "10-K_0000320193-25-000079"
    )
    summary = market_reports._read_market_package_summary(package_dir)

    assert summary["market"] == "US"
    assert summary["ticker"] == "AAPL"
    assert summary["quality_status"] == "pass"
    assert summary["counts"]["metrics"] >= 1
    assert summary["counts"]["evidence"] >= 1
    assert summary["paths"]["source_map"].endswith("qa/source_map.json")


def test_market_package_quality_routes_keep_response_contract(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "us_sec"
    package_dir = _write_market_package(wiki_root, "AAPL", "2025", "10-K_demo")
    (package_dir / "manifest.json").write_text(json.dumps({"filing_id": "AAPL-10K"}), encoding="utf-8")
    (package_dir / "qa").mkdir()
    (package_dir / "qa" / "quality_report.json").write_text(json.dumps({"overall_status": "pass"}), encoding="utf-8")
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps({"entries": [{"evidence_id": "e1"}, {"evidence_id": "e2"}]}),
        encoding="utf-8",
    )
    (package_dir / "metrics").mkdir()
    (package_dir / "metrics" / "financial_checks.json").write_text(json.dumps({"status": "warning"}), encoding="utf-8")
    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "US", wiki_root)

    by_path = asyncio.run(market_reports.market_package_quality_by_path("US", str(package_dir)))
    by_filing_id = asyncio.run(market_reports.market_package_quality_by_filing_id("AAPL-10K", "US"))

    assert by_path["manifest"] == {"filing_id": "AAPL-10K"}
    assert by_path["quality"] == {"overall_status": "pass"}
    assert by_path["financial_checks"] == {"status": "warning"}
    assert by_path["source_map_summary"] == {"evidence": 2}
    assert by_filing_id["package_path"] == str(package_dir)
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


def test_find_market_evidence_returns_package_and_entry():
    package_dir = (
        market_reports.REPO_ROOT
        / "data"
        / "wiki"
        / "us_sec"
        / "AAPL"
        / "2025"
        / "10-K_0000320193-25-000079"
    )
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


def test_market_import_command_uses_us_package_flag(monkeypatch):
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

    result = market_reports._run_market_package_import({
        "market": "US",
        "package_path": "data/wiki/us_sec/AAPL/2025/10-K_0000320193-25-000079",
        "ddl": True,
    })

    assert result["ok"] is True
    package_index = seen["args"].index("--package")
    assert "data/wiki/us_sec/AAPL/2025/10-K_0000320193-25-000079" in seen["args"][package_index + 1]
    assert seen["args"][-1] == "--ddl"


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

    monkeypatch.setitem(market_reports.MARKET_WIKI_ROOTS, "HK", wiki_root)
    monkeypatch.setitem(market_reports.MARKET_IMPORT_SCRIPTS, "HK", import_script)
    monkeypatch.setattr(market_reports, "run_command", fake_run)

    result = market_reports._run_market_package_import(
        {
            "market": "HK",
            "package_path": str(package_dir),
            "database_url": "postgres://secret",
            "run_ddl": True,
        }
    )

    assert result["ok"] is True
    assert result["parse_run_id"] == "parse-run-hk"
    assert seen["args"][:3] == [market_reports.sys.executable, str(import_script), str(package_dir)]
    assert "--package" not in seen["args"]
    assert seen["args"][-3:] == ["--database-url", "postgres://secret", "--ddl"]
    assert seen["kwargs"] == {"cwd": market_reports.REPO_ROOT, "timeout": 900}
    assert "postgres://secret" not in result["command"]
    assert "--database-url ***" in result["command"]


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

    result = market_reports._run_market_vector_ingest(
        {
            "market": "HK",
            "package_path": str(package_dir),
            "collection": "siq_market",
            "embed_url": "http://embed.local",
            "embed_model": "text-embedding-3-small",
            "vector_dim": 1536,
        }
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

    result = market_reports._run_market_vector_ingest(
        {
            "market": "US",
            "package_path": str(package_dir),
            "batch_tag": "prod-load",
            "dry_run": False,
        }
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
            JsonRequest({"market": "US", "package_path": "data/wiki/us_sec/AAPL/package", "ddl": True}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "market-package-import-job-1"
    assert seen["kind"] == "market-package-import"
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


def test_market_report_job_status_returns_404_for_missing_job(monkeypatch):
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: None)

    try:
        asyncio.run(market_reports.market_report_job_status("missing-job", _ops_user=None))
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Job not found"
    else:
        raise AssertionError("expected HTTPException")
