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


def test_market_ingestion_eval_queue_uses_canonical_adapter_without_public_schema_drift():
    seen = {}

    class JobService:
        def start(self, kind, target, *, created_by=None):
            seen["kind"] = kind
            seen["target_result"] = target()
            return {
                "job_id": "market-ingestion-eval-job-1",
                "kind": kind,
                "status": "queued",
                "created_at": "2026-07-04T12:00:00Z",
                "started_at": None,
                "finished_at": None,
                "updated_at": "2026-07-04T12:00:00Z",
                "created_by": created_by,
                "result": None,
                "error": None,
                "target": target,
            }

    result = service.queue_market_report_job(
        job_service=JobService(),
        kind="market-ingestion-eval",
        target=lambda: {"ok": True, "report": "eval.json"},
        created_by=DummyUser(),
    )

    assert result == {
        "ok": True,
        "queued": True,
        "job_id": "market-ingestion-eval-job-1",
        "kind": "market-ingestion-eval",
        "status": "queued",
        "created_at": "2026-07-04T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "updated_at": "2026-07-04T12:00:00Z",
        "created_by": {
            "id": 42,
            "username": "ops",
            "email": "ops@example.test",
            "full_name": "Ops User",
            "role": "admin",
        },
        "result": None,
        "error": None,
    }
    assert seen == {
        "kind": "market-ingestion-eval",
        "target_result": {"ok": True, "report": "eval.json"},
    }
    for internal_key in ("schema_version", "id", "subject", "steps", "logs", "attempts", "source_schema", "legacy_payload", "target"):
        assert internal_key not in result


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


def test_get_market_report_job_projects_canonical_market_eval_to_public_shape():
    class JobService:
        def get(self, job_id):
            assert job_id == "job-1"
            return {
                "schema_version": "siq_job_envelope_v1",
                "id": "job-1",
                "kind": "market-ingestion-eval",
                "subject": {"output": "eval.json"},
                "status": "succeeded",
                "created_at": "2026-07-04T12:00:00Z",
                "started_at": "2026-07-04T12:01:00Z",
                "finished_at": "2026-07-04T12:02:00Z",
                "updated_at": "2026-07-04T12:02:00Z",
                "created_by": {"username": "ops"},
                "result": {"ok": True},
                "error": None,
                "steps": [],
                "logs": [{"message": "internal"}],
                "attempts": 1,
                "source_schema": "market_file_backed_job_v1",
            }

    result = service.get_market_report_job(job_service=JobService(), job_id="job-1")

    assert result == {
        "job_id": "job-1",
        "kind": "market-ingestion-eval",
        "status": "succeeded",
        "created_at": "2026-07-04T12:00:00Z",
        "started_at": "2026-07-04T12:01:00Z",
        "finished_at": "2026-07-04T12:02:00Z",
        "updated_at": "2026-07-04T12:02:00Z",
        "created_by": {"username": "ops"},
        "result": {"ok": True},
        "error": None,
    }


def test_get_market_report_job_projects_legacy_market_eval_snapshot():
    class JobService:
        def get(self, job_id):
            return {
                "job_id": job_id,
                "kind": "market-ingestion-eval",
                "status": "running",
                "created_at": "2026-07-04T12:00:00Z",
                "target": lambda: None,
            }

    result = service.get_market_report_job(job_service=JobService(), job_id="job-1")

    assert result == {
        "job_id": "job-1",
        "kind": "market-ingestion-eval",
        "status": "running",
        "created_at": "2026-07-04T12:00:00Z",
    }


def test_get_market_report_job_preserves_durability_degraded_state():
    class JobService:
        def get(self, job_id):
            return {
                "job_id": job_id,
                "kind": "market-ingestion-eval",
                "status": "running",
                "created_at": "2026-07-12T09:00:00Z",
                "attempt": 2,
                "owner": "job-worker-1",
                "heartbeat_at": "2026-07-12T09:01:00Z",
                "durability_status": "degraded",
                "persistence_error": "job_store_write_failed",
            }

    result = service.get_market_report_job(job_service=JobService(), job_id="job-1")

    assert result["attempt"] == 2
    assert result["owner"] == "job-worker-1"
    assert result["heartbeat_at"] == "2026-07-12T09:01:00Z"
    assert result["durability_status"] == "degraded"
    assert result["persistence_error"] == "job_store_write_failed"


def test_run_or_queue_market_report_job_runs_inline_when_waiting():
    calls = []

    class JobService:
        def start(self, *_args, **_kwargs):
            raise AssertionError("job should not be queued when wait=True")

    result = service.run_or_queue_market_report_job(
        wait=True,
        job_service=JobService(),
        kind="market-package-build",
        target=lambda: calls.append("target") or {"ok": True, "mode": "inline"},
        created_by=DummyUser(),
    )

    assert result == {"ok": True, "mode": "inline"}
    assert calls == ["target"]


def test_run_or_queue_market_report_job_queues_when_not_waiting():
    seen = {}

    class JobService:
        def start(self, kind, target, *, created_by=None):
            seen["kind"] = kind
            seen["created_by"] = created_by
            seen["target_result"] = target()
            return {"job_id": "queued-job", "status": "queued"}

    result = service.run_or_queue_market_report_job(
        wait=False,
        job_service=JobService(),
        kind="market-vector-ingest",
        target=lambda: {"ok": True, "mode": "queued"},
        created_by=DummyUser(),
    )

    assert result == {"ok": True, "queued": True, "job_id": "queued-job", "status": "queued"}
    assert seen["kind"] == "market-vector-ingest"
    assert seen["created_by"]["email"] == "ops@example.test"
    assert seen["target_result"] == {"ok": True, "mode": "queued"}


def test_market_report_job_status_returns_job_or_stable_404():
    class JobService:
        def get(self, job_id):
            if job_id == "job-1":
                return {"job_id": "job-1", "status": "succeeded"}
            return None

    assert service.market_report_job_status(job_service=JobService(), job_id="job-1") == {
        "job_id": "job-1",
        "status": "succeeded",
    }

    try:
        service.market_report_job_status(job_service=JobService(), job_id="missing")
    except service.MarketReportJobError as exc:
        assert exc.status_code == 404
        assert exc.detail == "Job not found"
    else:
        raise AssertionError("expected MarketReportJobError")
