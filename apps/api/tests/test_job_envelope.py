import importlib.util
from pathlib import Path


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job_envelope = _load_module("temp_job_envelope", "job_envelope.py")


def test_market_job_to_canonical_preserves_public_contract_fields():
    market_job = {
        "job_id": "market-ingestion-eval-1",
        "kind": "market-ingestion-eval",
        "status": "succeeded",
        "created_at": "2026-07-04T10:00:00Z",
        "started_at": "2026-07-04T10:01:00Z",
        "finished_at": "2026-07-04T10:02:00Z",
        "updated_at": "2026-07-04T10:02:00Z",
        "created_by": {"id": 7, "username": "ops"},
        "result": {"ok": True, "report": "done"},
        "error": None,
    }

    canonical = job_envelope.market_job_to_canonical(market_job)

    assert canonical == {
        "schema_version": "siq_job_envelope_v1",
        "id": "market-ingestion-eval-1",
        "kind": "market-ingestion-eval",
        "subject": None,
        "status": "succeeded",
        "created_at": "2026-07-04T10:00:00Z",
        "started_at": "2026-07-04T10:01:00Z",
        "finished_at": "2026-07-04T10:02:00Z",
        "updated_at": "2026-07-04T10:02:00Z",
        "created_by": {"id": 7, "username": "ops"},
        "result": {"ok": True, "report": "done"},
        "error": None,
        "steps": [],
        "logs": [],
        "attempts": 1,
        "source_schema": "market_file_backed_job_v1",
        "legacy_payload": market_job,
    }


def test_workflow_job_to_canonical_maps_steps_without_changing_legacy_payload():
    workflow_job = {
        "jobId": "workflow-job-1",
        "taskId": "task-1",
        "status": "failed",
        "steps": [
            {
                "step": "wiki-import",
                "status": "succeeded",
                "startedAt": "2026-07-04T10:01:00Z",
                "finishedAt": "2026-07-04T10:02:00Z",
                "result": {"ok": True},
            },
            {
                "step": "semantic",
                "status": "running",
                "startedAt": "2026-07-04T10:03:00Z",
            },
        ],
        "createdAt": "2026-07-04T10:00:00Z",
        "updatedAt": "2026-07-04T10:04:00Z",
        "error": "semantic failed",
    }

    canonical = job_envelope.workflow_job_to_canonical(workflow_job)

    assert canonical["schema_version"] == "siq_job_envelope_v1"
    assert canonical["id"] == "workflow-job-1"
    assert canonical["kind"] == "workflow-run-remaining"
    assert canonical["subject"] == {"task_id": "task-1"}
    assert canonical["status"] == "failed"
    assert canonical["created_at"] == "2026-07-04T10:00:00Z"
    assert canonical["updated_at"] == "2026-07-04T10:04:00Z"
    assert canonical["error"] == "semantic failed"
    assert canonical["source_schema"] == "workflow_job_v1"
    assert canonical["legacy_payload"] == workflow_job
    assert canonical["steps"][0] == {
        "name": "wiki-import",
        "status": "succeeded",
        "started_at": "2026-07-04T10:01:00Z",
        "finished_at": "2026-07-04T10:02:00Z",
        "result": {"ok": True},
        "message": None,
        "legacy_payload": workflow_job["steps"][0],
    }
    assert canonical["steps"][1]["status"] == "running"
    assert canonical["steps"][1]["finished_at"] is None


def test_canonical_to_market_public_keeps_snake_case_payload_shape():
    canonical = job_envelope.market_job_to_canonical(
        {
            "job_id": "market-job-1",
            "kind": "market-package-build",
            "status": "running",
            "created_at": "2026-07-04T10:00:00Z",
            "started_at": "2026-07-04T10:01:00Z",
            "finished_at": None,
            "created_by": {"username": "ops"},
            "result": None,
            "error": None,
        }
    )
    canonical["logs"] = [{"message": "internal only"}]

    public = job_envelope.canonical_to_market_public(canonical)

    assert public == {
        "job_id": "market-job-1",
        "kind": "market-package-build",
        "status": "running",
        "created_at": "2026-07-04T10:00:00Z",
        "started_at": "2026-07-04T10:01:00Z",
        "finished_at": None,
        "created_by": {"username": "ops"},
        "result": None,
        "error": None,
    }
    assert "logs" not in public


def test_canonical_to_workflow_public_keeps_camel_case_payload_shape():
    workflow_job = {
        "jobId": "workflow-job-1",
        "taskId": "task-1",
        "status": "failed",
        "steps": [{"step": "semantic", "status": "running", "startedAt": "2026-07-04T10:03:00Z"}],
        "createdAt": "2026-07-04T10:00:00Z",
        "updatedAt": "2026-07-04T10:04:00Z",
        "error": "semantic failed",
    }
    canonical = job_envelope.workflow_job_to_canonical(workflow_job)
    canonical["logs"] = [{"message": "internal only"}]

    public = job_envelope.canonical_to_workflow_public(canonical)

    assert public == workflow_job
    assert "logs" not in public


def test_market_job_to_canonical_does_not_keep_runtime_target_callable():
    market_job = {
        "job_id": "market-job-1",
        "kind": "market-package-build",
        "status": "queued",
        "created_at": "2026-07-04T10:00:00Z",
        "target": lambda: {"ok": True},
    }

    canonical = job_envelope.market_job_to_canonical(market_job)

    assert "target" not in canonical["legacy_payload"]
    assert "target" not in job_envelope.canonical_to_market_public(canonical)


def test_load_canonical_compatible_jobs_accepts_legacy_list_and_jobs_payload():
    market_jobs = [
        {"job_id": "market-1", "kind": "market-package-build", "status": "succeeded"},
        {"job_id": "", "kind": "ignored"},
        "not-a-job",
    ]
    workflow_payload = {
        "jobs": [
            {"jobId": "workflow-1", "taskId": "task-1", "status": "queued", "steps": []},
            {"jobId": "", "status": "ignored"},
            {"createdAt": "missing-id"},
        ]
    }

    market = job_envelope.load_canonical_compatible_jobs(market_jobs, source="market")
    workflow = job_envelope.load_canonical_compatible_jobs(workflow_payload, source="workflow")

    assert [job["id"] for job in market] == ["market-1"]
    assert market[0]["source_schema"] == "market_file_backed_job_v1"
    assert [job["id"] for job in workflow] == ["workflow-1"]
    assert workflow[0]["source_schema"] == "workflow_job_v1"


def test_load_canonical_compatible_jobs_ignores_malformed_payload_and_rejects_unknown_source():
    assert job_envelope.load_canonical_compatible_jobs({"jobs": {"job_id": "not-a-list"}}, source="market") == []
    assert job_envelope.load_canonical_compatible_jobs({"jobs": []}, source="workflow") == []

    try:
        job_envelope.load_canonical_compatible_jobs([], source="deal")
    except ValueError as exc:
        assert str(exc) == "source must be market or workflow"
    else:
        raise AssertionError("expected ValueError")
