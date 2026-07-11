import importlib.util
import json
import time
from enum import Enum
from itertools import count
from pathlib import Path

import pytest

from services import observability


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job_service = _load_module("temp_job_service", "job_service.py")


class DemoRole(Enum):
    ADMIN = "admin"


@pytest.fixture(autouse=True)
def reset_observability_metrics():
    observability.reset_observability_metrics_for_tests()
    yield
    observability.reset_observability_metrics_for_tests()


def wait_for_terminal(service, job_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.get(job_id)
        if snapshot and snapshot.get("status") in {"succeeded", "failed"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def wait_for_metric(path: tuple[str, str], timeout: float = 2.0):
    section, key = path
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = observability.metrics_snapshot().get(section, {}).get(key)
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"metric did not appear: {section}.{key}")


def test_file_backed_job_service_tracks_success_and_failure(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    ok_job = service.start("demo", lambda: {"ok": True, "value": 1})
    bad_job = service.start("demo", lambda: {"ok": False, "value": 2})

    assert ok_job["status"] in {"queued", "running", "succeeded"}
    assert bad_job["status"] in {"queued", "running", "failed"}

    ok_snapshot = wait_for_terminal(service, ok_job["job_id"])
    bad_snapshot = wait_for_terminal(service, bad_job["job_id"])
    assert ok_snapshot["job_id"] == ok_job["job_id"]
    assert ok_snapshot["status"] == "succeeded"
    assert ok_snapshot["started_at"]
    assert ok_snapshot["finished_at"]
    assert ok_snapshot["result"] == {"ok": True, "value": 1}
    assert "target" not in ok_snapshot
    assert bad_snapshot["job_id"] == bad_job["job_id"]
    assert bad_snapshot["status"] == "failed"
    assert bad_snapshot["result"] == {"ok": False, "value": 2}
    assert bad_snapshot["error"] is None
    assert "target" not in bad_snapshot
    assert store_path.is_file()
    assert "target" not in store_path.read_text(encoding="utf-8")

    reloaded = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)
    reloaded_snapshot = reloaded.get(ok_job["job_id"])
    assert reloaded_snapshot is not None
    assert reloaded_snapshot["job_id"] == ok_job["job_id"]
    assert reloaded_snapshot["status"] == "succeeded"


def test_file_backed_job_service_start_returns_serializable_snapshot(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    started = service.start(
        "market-package-build",
        lambda: {"ok": True, "path": Path("/tmp/package")},
        created_by={"role": DemoRole.ADMIN, "home": Path("/tmp/operator")},
    )

    terminal = wait_for_terminal(service, started["job_id"])
    assert started["job_id"].startswith("market-package-build-")
    assert started["kind"] == "market-package-build"
    assert started["status"] in {"queued", "running", "succeeded"}
    assert started["created_at"]
    assert started["started_at"] in (None, terminal["started_at"])
    assert started["finished_at"] in (None, terminal["finished_at"])
    assert started["created_by"] == {"role": "DemoRole.ADMIN", "home": "/tmp/operator"}
    assert started["result"] in (None, {"ok": True, "path": "/tmp/package"})
    assert started["error"] is None
    assert "target" not in started
    assert terminal["result"] == {"ok": True, "path": "/tmp/package"}

    persisted = json.loads(store_path.read_text(encoding="utf-8"))
    assert persisted["jobs"][0]["created_by"] == {"role": "DemoRole.ADMIN", "home": "/tmp/operator"}
    assert persisted["jobs"][0]["result"] == {"ok": True, "path": "/tmp/package"}
    assert "target" not in persisted["jobs"][0]


def test_file_backed_job_service_serializes_top_level_enum_values(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    started = service.start("demo", lambda: DemoRole.ADMIN, created_by=DemoRole.ADMIN)
    terminal = wait_for_terminal(service, started["job_id"])

    assert started["created_by"] == "admin"
    assert terminal["result"] == "admin"


def test_file_backed_job_service_records_exception_failure(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=3)

    first = service.start("demo", lambda: {"ok": True, "value": "first"})

    def boom():
        raise RuntimeError("boom")

    second = service.start("demo", boom)
    third = service.start("demo", lambda: {"ok": True, "value": "third"})

    wait_for_terminal(service, first["job_id"])
    failed = wait_for_terminal(service, second["job_id"])
    latest = wait_for_terminal(service, third["job_id"])

    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert latest["status"] == "succeeded"


def test_file_backed_job_service_records_terminal_observability_metrics(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=3)

    def boom():
        raise RuntimeError("boom")

    succeeded = service.start("market-document-full-import", lambda: {"ok": True})
    business_failed = service.start("market-document-full-import", lambda: {"ok": False})
    exception_failed = service.start("market-vector-ingest", boom)

    wait_for_terminal(service, succeeded["job_id"])
    wait_for_terminal(service, business_failed["job_id"])
    wait_for_terminal(service, exception_failed["job_id"])

    assert wait_for_metric(
        ("background_job_final_state_counts", "market-document-full-import|succeeded")
    ) == 1
    assert wait_for_metric(("background_job_final_state_counts", "market-document-full-import|failed")) == 1
    assert wait_for_metric(("background_job_final_state_counts", "market-vector-ingest|failed")) == 1

    metrics = observability.metrics_snapshot()
    assert metrics["background_job_duration_seconds"]["market-document-full-import|succeeded"]["count"] == 1
    assert metrics["background_job_duration_seconds"]["market-document-full-import|failed"]["count"] == 1
    assert metrics["background_job_duration_seconds"]["market-vector-ingest|failed"]["count"] == 1


def test_file_backed_job_service_ignores_observability_recorder_failure(tmp_path, monkeypatch):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    def fail_recorder(**_kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(job_service.observability, "record_background_job_final_state", fail_recorder)

    started = service.start("market-document-full-import", lambda: {"ok": True})
    terminal = wait_for_terminal(service, started["job_id"])

    assert terminal["status"] == "succeeded"
    assert terminal["result"] == {"ok": True}


def test_file_backed_job_service_trims_oldest_jobs_by_created_at(tmp_path, monkeypatch):
    ticks = count()
    monkeypatch.setattr(job_service, "_now_iso", lambda: f"2026-07-03T10:{next(ticks):02d}:00Z")
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    first = service.start("demo", lambda: {"ok": True, "value": "first"})
    second = service.start("demo", lambda: {"ok": True, "value": "second"})
    third = service.start("demo", lambda: {"ok": True, "value": "third"})

    for job in (first, second, third):
        snapshot = service.get(job["job_id"])
        if snapshot is not None:
            wait_for_terminal(service, job["job_id"])

    assert service.get(first["job_id"]) is None
    assert service.get(second["job_id"]) is not None
    assert service.get(third["job_id"]) is not None

    reloaded = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)
    assert reloaded.get(first["job_id"]) is None
    assert reloaded.get(second["job_id"]) is not None
    assert reloaded.get(third["job_id"]) is not None


def test_file_backed_job_service_reloads_legacy_list_payload(tmp_path):
    store_path = tmp_path / "jobs.json"
    store_path.write_text(
        json.dumps(
            [
                {"job_id": "legacy-later", "kind": "demo", "status": "succeeded", "created_at": "2026-07-03T10:01:00Z"},
                {"job_id": "legacy-earlier", "kind": "demo", "status": "failed", "created_at": "2026-07-03T10:00:00Z"},
                {"job_id": "", "kind": "ignored"},
            ]
        ),
        encoding="utf-8",
    )

    service = job_service.FileBackedJobService(store_path=store_path)

    assert service.get("legacy-earlier")["status"] == "failed"
    assert service.get("legacy-later")["status"] == "succeeded"
    assert service.get("") is None


def test_file_backed_job_service_get_returns_snapshot_copy(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path)
    started = service.start("demo", lambda: {"ok": True})
    wait_for_terminal(service, started["job_id"])

    snapshot = service.get(started["job_id"])
    snapshot["status"] = "failed"
    snapshot["result"] = {"ok": False}

    fresh = service.get(started["job_id"])
    assert fresh["status"] == "succeeded"
    assert fresh["result"] == {"ok": True}


def test_file_backed_job_service_returns_none_for_missing_job(tmp_path):
    service = job_service.FileBackedJobService(store_path=tmp_path / "jobs.json")

    assert service.get("missing") is None


def test_file_backed_job_service_ignores_malformed_store_payloads(tmp_path):
    store_path = tmp_path / "jobs.json"

    store_path.write_text("{not-json", encoding="utf-8")
    service = job_service.FileBackedJobService(store_path=store_path)
    assert service.get("anything") is None

    store_path.write_text(json.dumps({"jobs": {"job_id": "not-a-list"}}), encoding="utf-8")
    service = job_service.FileBackedJobService(store_path=store_path)
    assert service.get("not-a-list") is None


def test_file_backed_job_service_persist_failure_does_not_block_runtime_snapshot(tmp_path):
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    store_path = blocked_parent / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path)

    started = service.start("demo", lambda: {"ok": True, "value": "runtime-only"})
    terminal = wait_for_terminal(service, started["job_id"])

    assert terminal["status"] == "succeeded"
    assert terminal["result"] == {"ok": True, "value": "runtime-only"}
