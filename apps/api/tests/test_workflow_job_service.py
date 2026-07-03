import importlib.util
import json
import sys
from pathlib import Path


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow_jobs = _load_module("temp_workflow_job_service", "workflow_job_service.py")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

WORKFLOW_SPEC = importlib.util.spec_from_file_location("workflow_router_for_job_tests", BACKEND_ROOT / "routers" / "workflow.py")
assert WORKFLOW_SPEC and WORKFLOW_SPEC.loader
workflow = importlib.util.module_from_spec(WORKFLOW_SPEC)
WORKFLOW_SPEC.loader.exec_module(workflow)


def test_workflow_run_command_uses_shared_runner_and_preserves_contract(monkeypatch):
    seen = {}

    class Completed:
        returncode = 7
        stdout = "x" * 6100
        stderr = "y" * 6101

    def fake_run_command(args, *, timeout=None, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return Completed()

    monkeypatch.setattr(workflow, "run_subprocess_command", fake_run_command)

    result = workflow._run_command(["python", "script.py"], timeout=123, env={"DATABASE_URL": "secret"})

    assert seen == {
        "args": ["python", "script.py"],
        "timeout": 123,
        "env": {"DATABASE_URL": "secret"},
    }
    assert result == {
        "returnCode": 7,
        "stdout": "x" * 6000,
        "stderr": "y" * 6000,
    }


def test_workflow_job_store_loads_trims_and_persists(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    store_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {"jobId": "job-b", "createdAt": "2026-01-02T00:00:00Z"},
                    {"jobId": "", "createdAt": "bad"},
                    {"createdAt": "missing"},
                    {"jobId": "job-a", "createdAt": "2026-01-01T00:00:00Z"},
                ]
            }
        ),
        encoding="utf-8",
    )

    jobs = workflow_jobs.load_workflow_jobs(store_path)

    assert list(jobs) == ["job-a", "job-b"]
    workflow_jobs.persist_workflow_jobs(store_path, jobs, max_jobs=1)
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload == {"jobs": [{"jobId": "job-b", "createdAt": "2026-01-02T00:00:00Z"}]}


def test_workflow_job_mutators_keep_existing_contract():
    timestamps = iter(
        [
            "2026-07-03T10:00:00Z",
            "2026-07-03T10:01:00Z",
            "2026-07-03T10:02:00Z",
            "2026-07-03T10:03:00Z",
            "2026-07-03T10:04:00Z",
            "2026-07-03T10:05:00Z",
        ]
    )
    jobs = {}

    job = workflow_jobs.create_workflow_job(jobs, job_id="job-1", task_id="task-1", now=lambda: next(timestamps))
    assert job == {
        "jobId": "job-1",
        "taskId": "task-1",
        "status": "queued",
        "steps": [],
        "createdAt": "2026-07-03T10:00:00Z",
        "updatedAt": "2026-07-03T10:00:00Z",
    }

    assert workflow_jobs.update_workflow_job(jobs, "job-1", now=lambda: next(timestamps), status="running")
    assert jobs["job-1"]["status"] == "running"
    assert jobs["job-1"]["updatedAt"] == "2026-07-03T10:01:00Z"

    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "wiki-import",
        "running",
        now=lambda: next(timestamps),
    )
    assert jobs["job-1"]["steps"] == [
        {"step": "wiki-import", "startedAt": "2026-07-03T10:02:00Z", "status": "running"}
    ]
    assert jobs["job-1"]["updatedAt"] == "2026-07-03T10:03:00Z"

    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "wiki-import",
        "succeeded",
        now=lambda: next(timestamps),
        result={"ok": True},
    )
    assert jobs["job-1"]["steps"] == [
        {
            "step": "wiki-import",
                "startedAt": "2026-07-03T10:02:00Z",
                "status": "succeeded",
                "result": {"ok": True},
                "finishedAt": "2026-07-03T10:04:00Z",
            }
        ]
    assert jobs["job-1"]["updatedAt"] == "2026-07-03T10:05:00Z"

    assert workflow_jobs.update_workflow_job(jobs, "missing", now=lambda: next(timestamps), status="running") is False
    assert (
        workflow_jobs.record_workflow_job_step(jobs, "missing", "wiki-import", "running", now=lambda: next(timestamps))
        is False
    )


def test_workflow_job_step_update_reuses_existing_step_and_preserves_terminal_finished_at():
    timestamps = iter(
        [
            "2026-07-04T10:00:00Z",
            "2026-07-04T10:01:00Z",
            "2026-07-04T10:02:00Z",
            "2026-07-04T10:03:00Z",
            "2026-07-04T10:04:00Z",
            "2026-07-04T10:05:00Z",
            "2026-07-04T10:06:00Z",
        ]
    )
    jobs = {}
    workflow_jobs.create_workflow_job(jobs, job_id="job-1", task_id="task-1", now=lambda: next(timestamps))

    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "semantic",
        "running",
        now=lambda: next(timestamps),
        detail={"phase": "rule"},
    )
    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "semantic",
        "succeeded",
        now=lambda: next(timestamps),
        detail={"phase": "llm"},
    )
    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "semantic",
        "succeeded",
        now=lambda: next(timestamps),
        result={"ok": True},
    )

    assert jobs["job-1"]["steps"] == [
        {
            "step": "semantic",
            "startedAt": "2026-07-04T10:01:00Z",
            "status": "succeeded",
            "detail": {"phase": "llm"},
            "finishedAt": "2026-07-04T10:03:00Z",
            "result": {"ok": True},
        }
    ]
    assert jobs["job-1"]["updatedAt"] == "2026-07-04T10:06:00Z"


def test_update_workflow_job_custom_fields_refresh_updated_at_only_for_existing_jobs():
    timestamps = iter(["2026-07-04T11:00:00Z", "2026-07-04T11:01:00Z"])
    jobs = {}
    workflow_jobs.create_workflow_job(jobs, job_id="job-1", task_id="task-1", now=lambda: next(timestamps))

    assert workflow_jobs.update_workflow_job(
        jobs,
        "job-1",
        now=lambda: next(timestamps),
        status="running",
        error=None,
        result={"stage": "wiki"},
    )
    assert jobs["job-1"]["status"] == "running"
    assert jobs["job-1"]["error"] is None
    assert jobs["job-1"]["result"] == {"stage": "wiki"}
    assert jobs["job-1"]["updatedAt"] == "2026-07-04T11:01:00Z"

    before = dict(jobs["job-1"])
    assert workflow_jobs.update_workflow_job(
        jobs,
        "missing",
        now=lambda: "should-not-be-used",
        status="failed",
    ) is False
    assert jobs["job-1"] == before


def test_workflow_router_uses_job_service_contract(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    monkeypatch.setattr(workflow.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "job-abc123"})())
    monkeypatch.setattr(workflow, "_workflow_preflight", lambda task_id: {"ok": True})

    started = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["called"] = True

    monkeypatch.setattr(workflow.threading, "Thread", FakeThread)

    result = workflow.run_remaining_workflow("task-queued")

    assert result == {
        "jobId": "job-abc123",
        "taskId": "task-queued",
        "status": "queued",
        "steps": [],
        "createdAt": result["createdAt"],
        "updatedAt": result["createdAt"],
    }
    assert started == {
        "target": workflow._run_remaining_pipeline,
        "args": ("job-abc123", "task-queued"),
        "daemon": True,
        "called": True,
    }
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["jobs"] == [result]

    workflow._job_update("job-abc123", status="running")
    workflow._job_step("job-abc123", "wiki-import", "skipped", message="Wiki 已是最新")

    reloaded = workflow_jobs.load_workflow_jobs(store_path)
    assert reloaded["job-abc123"]["status"] == "running"
    assert reloaded["job-abc123"]["steps"][0]["step"] == "wiki-import"
    assert reloaded["job-abc123"]["steps"][0]["status"] == "skipped"
    assert reloaded["job-abc123"]["steps"][0]["message"] == "Wiki 已是最新"


def test_run_remaining_pipeline_fails_when_artifact_bundle_not_ready(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    workflow.create_workflow_job(
        workflow._workflow_jobs,
        job_id="job-missing-artifacts",
        task_id="task-missing-artifacts",
        now=lambda: "2026-07-03T11:00:00Z",
    )
    monkeypatch.setattr(
        workflow,
        "_workflow_status_payload",
        lambda task_id: {"artifactBundle": {"ready": False}},
    )

    workflow._run_remaining_pipeline("job-missing-artifacts", "task-missing-artifacts")

    job = workflow._workflow_jobs["job-missing-artifacts"]
    assert job["status"] == "failed"
    assert job["error"] == "解析产物包不完整"
    assert job["steps"] == []
    persisted = workflow_jobs.load_workflow_jobs(store_path)
    assert persisted["job-missing-artifacts"]["status"] == "failed"


def test_run_remaining_pipeline_records_step_order_and_final_status(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    workflow.create_workflow_job(
        workflow._workflow_jobs,
        job_id="job-success",
        task_id="task-success",
        now=lambda: "2026-07-03T12:00:00Z",
    )
    status_sequence = iter(
        [
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "missing"},
                "semantic": {"status": "missing"},
                "obsidian": {"status": "ready"},
                "database": {"status": "missing"},
            },
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "ready", "companyDir": "/tmp/company"},
                "semantic": {"status": "missing", "companyDir": "/tmp/company"},
                "obsidian": {"status": "ready"},
                "database": {"status": "missing"},
            },
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "ready", "companyDir": "/tmp/company"},
                "semantic": {"status": "ready", "companyDir": "/tmp/company"},
                "obsidian": {"status": "ready"},
                "database": {"status": "missing"},
            },
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "ready", "companyDir": "/tmp/company"},
                "semantic": {"status": "ready", "companyDir": "/tmp/company"},
                "obsidian": {"status": "ready"},
                "database": {"status": "missing"},
            },
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "ready", "companyDir": "/tmp/company"},
                "semantic": {"status": "ready", "companyDir": "/tmp/company"},
                "obsidian": {"status": "ready"},
                "database": {"status": "ready"},
            },
        ]
    )
    calls = []
    monkeypatch.setattr(workflow, "_workflow_status_payload", lambda task_id: next(status_sequence))
    monkeypatch.setattr(workflow, "_import_task_to_wiki", lambda task_id: calls.append(("wiki", task_id)) or {"ok": True})
    monkeypatch.setattr(
        workflow,
        "extract_semantic_for_task",
        lambda task_id: calls.append(("semantic", task_id)) or {"ok": True},
    )
    monkeypatch.setattr(
        workflow,
        "_generate_obsidian_for_company",
        lambda company_dir: calls.append(("obsidian", company_dir)) or {"ok": True},
    )
    monkeypatch.setattr(
        workflow,
        "import_task_to_database",
        lambda task_id: calls.append(("database", task_id)) or {"ok": True},
    )

    workflow._run_remaining_pipeline("job-success", "task-success")

    job = workflow._workflow_jobs["job-success"]
    assert job["status"] == "succeeded"
    assert calls == [("wiki", "task-success"), ("semantic", "task-success"), ("database", "task-success")]
    assert [(step["step"], step["status"]) for step in job["steps"]] == [
        ("wiki-import", "succeeded"),
        ("semantic", "succeeded"),
        ("obsidian", "skipped"),
        ("db-import", "succeeded"),
    ]
    assert job["result"]["database"]["status"] == "ready"


def test_run_remaining_pipeline_keeps_completed_steps_when_later_step_fails(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    workflow.create_workflow_job(
        workflow._workflow_jobs,
        job_id="job-failed",
        task_id="task-failed",
        now=lambda: "2026-07-03T13:00:00Z",
    )
    status_sequence = iter(
        [
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "missing"},
                "semantic": {"status": "missing"},
                "obsidian": {"status": "ready"},
                "database": {"status": "ready"},
            },
            {
                "artifactBundle": {"ready": True},
                "wiki": {"status": "ready"},
                "semantic": {"status": "missing"},
                "obsidian": {"status": "ready"},
                "database": {"status": "ready"},
            },
        ]
    )
    monkeypatch.setattr(workflow, "_workflow_status_payload", lambda task_id: next(status_sequence))
    monkeypatch.setattr(workflow, "_import_task_to_wiki", lambda task_id: {"ok": True})

    def fail_semantic(task_id):
        raise RuntimeError("semantic failed")

    monkeypatch.setattr(workflow, "extract_semantic_for_task", fail_semantic)

    workflow._run_remaining_pipeline("job-failed", "task-failed")

    job = workflow._workflow_jobs["job-failed"]
    assert job["status"] == "failed"
    assert job["error"] == "semantic failed"
    assert [(step["step"], step["status"]) for step in job["steps"]] == [
        ("wiki-import", "succeeded"),
        ("semantic", "running"),
    ]
