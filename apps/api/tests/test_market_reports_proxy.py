import sys
import asyncio
import importlib.util
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("market_reports", BACKEND_ROOT / "routers" / "market_reports.py")
market_reports = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market_reports)


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

    monkeypatch.setattr(market_reports.httpx, "AsyncClient", FakeAsyncClient)

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

    monkeypatch.setattr(market_reports.httpx, "AsyncClient", FakeAsyncClient)

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


def test_active_llm_provider_prefers_cloud_minimax(monkeypatch):
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
                    "providerName": "Hermes / Minimax",
                    "baseUrl": "hermes://minimax-cn",
                    "model": "MiniMax-M3",
                },
            },
        },
    )

    active, provider = market_reports._active_llm_provider()

    assert active == "cloud"
    assert provider["baseUrl"] == "hermes://minimax-cn"


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


def test_market_package_build_queues_background_job(monkeypatch):
    seen = {}

    def fake_start(kind, target, *, created_by=None):
        seen["kind"] = kind
        seen["created_by"] = created_by
        seen["target_result"] = target()
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(market_reports.market_report_job_service, "start", fake_start)
    monkeypatch.setattr(market_reports, "_run_market_package_build", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.build_market_package(
            JsonRequest({"market": "US", "download_relative_path": "US/demo/report.html"}),
            wait=False,
            _ops_user=None,
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert seen["kind"] == "market-package-build"
    assert seen["target_result"]["ok"] is True


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
    assert seen["args"][:2] == [market_reports.sys.executable, str(build_script)]
    assert seen["args"][3] == "--force"
    assert seen["args"][seen["args"].index("--output-root") + 1] == str(wiki_root)
    assert seen["kwargs"] == {"cwd": market_reports.REPO_ROOT, "timeout": 900}


def test_market_report_job_status_uses_service(monkeypatch):
    monkeypatch.setattr(market_reports.market_report_job_service, "get", lambda job_id: {"job_id": job_id, "status": "running"})

    result = asyncio.run(market_reports.market_report_job_status("job-123", _ops_user=None))

    assert result["job_id"] == "job-123"
    assert result["status"] == "running"
