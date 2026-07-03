import asyncio
import importlib.util
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("market_reports", BACKEND_ROOT / "routers" / "market_reports.py")
market_reports = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market_reports)


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


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
    assert result["job_id"] == "market-package-build-job-1"
    assert seen["kind"] == "market-package-build"
    assert seen["created_by"]["username"] == "ops"
    assert seen["target_result"]["ok"] is True


def test_eu_parse_queues_background_job_and_forces_market(monkeypatch):
    seen = capture_background_job(monkeypatch)
    monkeypatch.setattr(market_reports, "_run_market_package_build", lambda payload: {"ok": True, "payload": payload})

    result = asyncio.run(
        market_reports.parse_eu_market_report(
            JsonRequest({"market": "HK", "download_relative_path": "EU/NL/ASML/2025/report.html"}),
            wait=False,
            _ops_user=DummyUser(),
        )
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "eu-market-report-parse-job-1"
    assert seen["kind"] == "eu-market-report-parse"
    assert seen["target_result"]["payload"]["market"] == "EU"


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
