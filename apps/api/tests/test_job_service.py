import importlib.util
from pathlib import Path


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job_service = _load_module("temp_job_service", "job_service.py")


def test_file_backed_job_service_tracks_success_and_failure(tmp_path):
    store_path = tmp_path / "jobs.json"
    service = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)

    ok_job = service.start("demo", lambda: {"ok": True, "value": 1})
    bad_job = service.start("demo", lambda: {"ok": False, "value": 2})

    assert ok_job["status"] in {"queued", "running", "succeeded"}
    assert bad_job["status"] in {"queued", "running", "failed"}

    ok_snapshot = service.get(ok_job["job_id"])
    bad_snapshot = service.get(bad_job["job_id"])

    assert ok_snapshot is not None
    assert bad_snapshot is not None
    assert ok_snapshot["job_id"] == ok_job["job_id"]
    assert bad_snapshot["job_id"] == bad_job["job_id"]
    assert store_path.is_file()
    assert "jobs" in store_path.read_text(encoding="utf-8")

    reloaded = job_service.FileBackedJobService(store_path=store_path, max_jobs=2)
    reloaded_snapshot = reloaded.get(ok_job["job_id"])
    assert reloaded_snapshot is not None
    assert reloaded_snapshot["job_id"] == ok_job["job_id"]


def test_file_backed_job_service_returns_none_for_missing_job(tmp_path):
    service = job_service.FileBackedJobService(store_path=tmp_path / "jobs.json")

    assert service.get("missing") is None
