from services import market_report_queueing as service


class DummyUser:
    id = 42
    username = "ops"
    email = "ops@example.test"
    full_name = "Ops User"
    role = "admin"


def test_job_created_by_snapshots_user_fields_and_handles_none():
    assert service.job_created_by(None) is None
    assert service.job_created_by(DummyUser()) == {
        "id": 42,
        "username": "ops",
        "email": "ops@example.test",
        "full_name": "Ops User",
        "role": "admin",
    }


def test_queue_market_report_job_uses_job_service_and_envelopes_result():
    seen = {}

    class JobService:
        def start(self, kind, target, *, created_by=None):
            seen["kind"] = kind
            seen["created_by"] = created_by
            seen["target_result"] = target()
            return {"job_id": "job-1", "status": "queued"}

    result = service.queue_market_report_job(
        job_service=JobService(),
        kind="market-package-build",
        target=lambda: {"ok": True, "package": "demo"},
        created_by=DummyUser(),
    )

    assert result == {"ok": True, "queued": True, "job_id": "job-1", "status": "queued"}
    assert seen["kind"] == "market-package-build"
    assert seen["created_by"]["username"] == "ops"
    assert seen["target_result"] == {"ok": True, "package": "demo"}


def test_get_market_report_job_delegates_to_job_service():
    class JobService:
        def get(self, job_id):
            if job_id == "job-1":
                return {"job_id": job_id, "status": "running"}
            return None

    assert service.get_market_report_job(job_service=JobService(), job_id="job-1") == {
        "job_id": "job-1",
        "status": "running",
    }
    assert service.get_market_report_job(job_service=JobService(), job_id="missing") is None
