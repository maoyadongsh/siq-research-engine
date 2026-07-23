import importlib.util
import json
import multiprocessing
import subprocess
import sys
from pathlib import Path

import pytest


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
        "command": "python script.py",
        "timeoutSeconds": 123,
        "timedOut": False,
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


def test_workflow_job_persistence_merges_concurrent_process_snapshots(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    context = multiprocessing.get_context("fork")
    start = context.Event()

    def persist(index: int) -> None:
        start.wait()
        workflow_jobs.persist_workflow_jobs(
            store_path,
            {
                f"job-{index}": {
                    "jobId": f"job-{index}",
                    "createdAt": f"2026-01-01T00:00:{index:02d}Z",
                    "updatedAt": f"2026-01-01T00:00:{index:02d}Z",
                }
            },
        )

    processes = [context.Process(target=persist, args=(index,)) for index in range(8)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    persisted = workflow_jobs.load_workflow_jobs(store_path)
    assert set(persisted) == {f"job-{index}" for index in range(8)}
    assert workflow_jobs.workflow_job_store_revision(store_path) == 8


def test_workflow_job_persistence_merges_same_job_fields_and_steps(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {
            "job-1": {
                "jobId": "job-1",
                "updatedAt": "2026-01-01T00:00:00Z",
                "result": {"wiki": "ready"},
                "steps": [{"step": "wiki", "status": "succeeded"}],
            }
        },
    )
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {
            "job-1": {
                "jobId": "job-1",
                "updatedAt": "2026-01-01T00:00:00Z",
                "status": "running",
                "steps": [{"step": "semantic", "status": "running"}],
            }
        },
    )

    job = workflow_jobs.load_workflow_jobs(store_path)["job-1"]
    assert job["result"] == {"wiki": "ready"}
    assert job["status"] == "running"
    assert job["steps"] == [
        {"step": "wiki", "status": "succeeded"},
        {"step": "semantic", "status": "running"},
    ]


def test_workflow_job_persistence_rejects_stale_revision_without_overwrite(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {"job-1": {"jobId": "job-1", "updatedAt": "v1"}},
    )
    stale_revision = workflow_jobs.workflow_job_store_revision(store_path)
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {"job-2": {"jobId": "job-2", "updatedAt": "v2"}},
        expected_revision=stale_revision,
    )
    with pytest.raises(workflow_jobs.WorkflowJobStoreConflictError, match="expected revision 1, found 2"):
        workflow_jobs.persist_workflow_jobs(
            store_path,
            {"job-3": {"jobId": "job-3", "updatedAt": "v3"}},
            expected_revision=stale_revision,
        )
    assert set(workflow_jobs.load_workflow_jobs(store_path)) == {"job-1", "job-2"}


def test_workflow_job_persistence_failure_keeps_previous_store(tmp_path, monkeypatch):
    store_path = tmp_path / "workflow-jobs.json"
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {"job-1": {"jobId": "job-1", "updatedAt": "v1"}},
    )
    previous = store_path.read_text(encoding="utf-8")
    monkeypatch.setattr(workflow_jobs.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("replace failed")))

    with pytest.raises(OSError, match="replace failed"):
        workflow_jobs.persist_workflow_jobs(
            store_path,
            {"job-2": {"jobId": "job-2", "updatedAt": "v2"}},
        )
    assert store_path.read_text(encoding="utf-8") == previous
    assert set(workflow_jobs.load_workflow_jobs(store_path)) == {"job-1"}


def test_workflow_job_recovery_marks_only_expired_leases_recoverable():
    jobs = {
        "stale": {
            "jobId": "stale",
            "status": "running",
            "currentStep": "semantic",
            "steps": [{"step": "semantic", "status": "running"}],
            "leaseExpiresAt": "2026-07-12T09:00:00Z",
        },
        "fresh": {
            "jobId": "fresh",
            "status": "running",
            "leaseExpiresAt": "2026-07-12T11:00:00Z",
        },
        "done": {"jobId": "done", "status": "succeeded"},
    }

    recovered = workflow_jobs.recover_stale_workflow_jobs(jobs, now="2026-07-12T10:00:00Z")

    assert recovered == ["stale"]
    assert jobs["stale"]["status"] == "interrupted"
    assert jobs["stale"]["recoverable"] is False
    assert jobs["stale"]["recoveryReason"] == "process_restart_unrecoverable_target"
    assert jobs["stale"]["failedStep"] == "semantic"
    assert jobs["stale"]["steps"][0]["status"] == "failed"
    assert jobs["fresh"]["status"] == "running"
    assert jobs["done"]["status"] == "succeeded"


def test_workflow_job_store_recovers_legacy_running_job_after_restart(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    workflow_jobs.persist_workflow_jobs(
        store_path,
        {
            "legacy": {
                "jobId": "legacy",
                "status": "running",
                "updatedAt": "2026-07-12T09:00:00Z",
            }
        },
    )

    jobs, recovered = workflow_jobs.recover_workflow_job_store(
        store_path,
        now="2026-07-12T10:00:00Z",
        legacy_stale_seconds=300,
    )

    assert recovered == ["legacy"]
    assert jobs["legacy"]["status"] == "interrupted"
    assert jobs["legacy"]["recoverable"] is False
    assert workflow_jobs.load_workflow_jobs(store_path)["legacy"]["status"] == "interrupted"


def test_workflow_job_claim_reuses_active_idempotency_key(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    jobs = {}
    key = workflow_jobs.workflow_job_idempotency_key(
        task_id="task-1",
        retry_scope="semantic",
        metadata={"created_by": {"id": 7}},
    )
    first = workflow_jobs.create_workflow_job(
        {},
        job_id="job-1",
        task_id="task-1",
        now=lambda: "2026-07-12T10:00:00Z",
        retry_scope="semantic",
        idempotency_key=key,
        owner_id="worker-a",
    )
    selected, reused = workflow_jobs.claim_workflow_job(
        store_path,
        jobs,
        first,
        now="2026-07-12T10:00:00Z",
    )
    assert reused is False
    assert selected["jobId"] == "job-1"

    duplicate = workflow_jobs.create_workflow_job(
        {},
        job_id="job-2",
        task_id="task-1",
        now=lambda: "2026-07-12T10:00:01Z",
        retry_scope="semantic",
        idempotency_key=key,
        owner_id="worker-b",
    )
    selected, reused = workflow_jobs.claim_workflow_job(
        store_path,
        jobs,
        duplicate,
        now="2026-07-12T10:00:01Z",
    )
    assert reused is True
    assert selected["jobId"] == "job-1"
    assert set(jobs) == {"job-1"}


def test_workflow_job_claim_converges_same_key_across_processes(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    key = workflow_jobs.workflow_job_idempotency_key(task_id="task-1", retry_scope="semantic")

    def claim(index: int) -> None:
        candidate = workflow_jobs.create_workflow_job(
            {},
            job_id=f"job-{index}",
            task_id="task-1",
            now=lambda: "2026-07-12T10:00:00Z",
            retry_scope="semantic",
            idempotency_key=key,
            owner_id=f"worker-{index}",
        )
        start.wait()
        workflow_jobs.claim_workflow_job(
            store_path,
            {},
            candidate,
            now="2026-07-12T10:00:00Z",
        )

    processes = [context.Process(target=claim, args=(index,)) for index in range(6)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    jobs = workflow_jobs.load_workflow_jobs(store_path)
    assert len(jobs) == 1
    assert next(iter(jobs.values()))["idempotencyKey"] == key


def test_workflow_job_claim_allows_retry_after_stale_lease(tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    key = workflow_jobs.workflow_job_idempotency_key(task_id="task-1", retry_scope="semantic")
    stale = workflow_jobs.create_workflow_job(
        {},
        job_id="job-stale",
        task_id="task-1",
        now=lambda: "2026-07-12T09:00:00Z",
        retry_scope="semantic",
        idempotency_key=key,
        owner_id="old-worker",
        lease_seconds=60,
    )
    workflow_jobs.claim_workflow_job(
        store_path,
        {},
        stale,
        now="2026-07-12T09:00:00Z",
    )
    retry = workflow_jobs.create_workflow_job(
        {},
        job_id="job-retry",
        task_id="task-1",
        now=lambda: "2026-07-12T10:00:00Z",
        retry_scope="semantic",
        idempotency_key=key,
        owner_id="new-worker",
    )

    jobs = {}
    selected, reused = workflow_jobs.claim_workflow_job(
        store_path,
        jobs,
        retry,
        now="2026-07-12T10:00:00Z",
    )

    assert reused is False
    assert selected["jobId"] == "job-retry"
    assert jobs["job-stale"]["status"] == "interrupted"
    assert jobs["job-stale"]["recoverable"] is False
    assert jobs["job-retry"]["status"] == "queued"


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
    assert job["jobId"] == "job-1"
    assert job["taskId"] == "task-1"
    assert job["status"] == "queued"
    assert job["steps"] == []
    assert job["currentStep"] is None
    assert job["retryScope"] == "workflow"
    assert job["createdAt"] == "2026-07-03T10:00:00Z"
    assert job["updatedAt"] == "2026-07-03T10:00:00Z"

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
    assert jobs["job-1"]["currentStep"] == "wiki-import"
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


def test_workflow_job_step_contract_extracts_command_output_and_retry_scope():
    timestamps = iter([
        "2026-07-04T12:00:00Z",
        "2026-07-04T12:01:00Z",
        "2026-07-04T12:02:00Z",
        "2026-07-04T12:03:00Z",
    ])
    jobs = {}
    workflow_jobs.create_workflow_job(
        jobs,
        job_id="job-1",
        task_id="task-1",
        retry_scope="semantic-generic",
        now=lambda: next(timestamps),
    )

    assert workflow_jobs.record_workflow_job_step(
        jobs,
        "job-1",
        "semantic-generic",
        "failed",
        now=lambda: next(timestamps),
        result={
            "ok": False,
            "result": {
                "rule": {
                    "returnCode": 0,
                    "stdout": "rule ok\n",
                    "stderr": "",
                    "command": "python semantic.py",
                    "timeoutSeconds": 180,
                },
                "llm": {
                    "returnCode": 124,
                    "stdout": "",
                    "stderr": "timed out\n",
                    "command": "python llm.py",
                    "timeoutSeconds": 900,
                },
            },
        },
        error="llm timeout",
    )

    step = jobs["job-1"]["steps"][0]
    assert jobs["job-1"]["retryScope"] == "semantic-generic"
    assert jobs["job-1"]["currentStep"] == "semantic-generic"
    assert jobs["job-1"]["failedStep"] == "semantic-generic"
    assert step["status"] == "failed"
    assert step["finishedAt"] == "2026-07-04T12:02:00Z"
    assert step["stdoutTail"] == "rule ok\n"
    assert step["stderrTail"] == "timed out\n"
    assert step["timeoutSeconds"] == 180
    assert step["commandResults"] == [
        {
            "stage": "rule",
            "returnCode": 0,
            "stdoutTail": "rule ok\n",
            "stderrTail": "",
            "timeoutSeconds": 180,
            "command": "python semantic.py",
        },
        {
            "stage": "llm",
            "returnCode": 124,
            "stdoutTail": "",
            "stderrTail": "timed out\n",
            "timeoutSeconds": 900,
            "command": "python llm.py",
        },
    ]


def test_workflow_run_command_returns_timeout_contract(monkeypatch):
    def fake_run_command(args, *, timeout=None, env=None):
        raise subprocess.TimeoutExpired(args, timeout=timeout, output="partial stdout", stderr="partial stderr")

    monkeypatch.setattr(workflow, "run_subprocess_command", fake_run_command)

    result = workflow._run_command(["python", "slow.py"], timeout=5)

    assert result == {
        "returnCode": 124,
        "stdout": "partial stdout",
        "stderr": "partial stderr",
        "command": "python slow.py",
        "timeoutSeconds": 5,
        "timedOut": True,
    }


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

    assert result["jobId"] == "job-abc123"
    assert result["taskId"] == "task-queued"
    assert result["status"] == "queued"
    assert result["steps"] == []
    assert result["currentStep"] is None
    assert result["retryScope"] == "remaining"
    assert result["updatedAt"] == result["createdAt"]
    assert result["ownerId"] == workflow.WORKFLOW_JOB_OWNER_ID
    assert result["heartbeatAt"] == result["createdAt"]
    assert result["leaseExpiresAt"]
    assert result["idempotencyKey"]
    assert started["target"] == workflow._run_job_with_heartbeat
    assert started["args"][0] == "job-abc123"
    assert callable(started["args"][1])
    assert started["daemon"] is True
    assert started["called"] is True
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["jobs"] == [result]

    workflow._job_update("job-abc123", status="running")
    workflow._job_step("job-abc123", "wiki-import", "skipped", message="Wiki 已是最新")

    reloaded = workflow_jobs.load_workflow_jobs(store_path)
    assert reloaded["job-abc123"]["status"] == "running"
    assert reloaded["job-abc123"]["steps"][0]["step"] == "wiki-import"
    assert reloaded["job-abc123"]["steps"][0]["status"] == "skipped"
    assert reloaded["job-abc123"]["steps"][0]["message"] == "Wiki 已是最新"


def test_workflow_router_duplicate_active_submission_reuses_job(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    identifiers = iter(["job-first", "job-duplicate"])
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    monkeypatch.setattr(
        workflow.uuid,
        "uuid4",
        lambda: type("Uuid", (), {"hex": next(identifiers)})(),
    )
    monkeypatch.setattr(workflow, "_workflow_preflight", lambda task_id: {"ok": True})
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(workflow.threading, "Thread", FakeThread)

    first = workflow.run_remaining_workflow("task-duplicate")
    duplicate = workflow.run_remaining_workflow("task-duplicate")

    assert first["jobId"] == "job-first"
    assert duplicate["jobId"] == "job-first"
    assert len(started) == 1
    assert set(workflow._workflow_jobs) == {"job-first"}


def test_workflow_router_postgres_backend_enqueues_without_api_thread(monkeypatch):
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_BACKEND", "postgres")
    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    monkeypatch.setattr(workflow.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "job-pg"})())
    monkeypatch.setattr(workflow, "_workflow_preflight", lambda task_id: {"ok": True})
    calls = []

    class FakeQueue:
        def enqueue(self, *, snapshot, max_attempts):
            calls.append((snapshot, max_attempts))
            return {**snapshot, "durabilityStatus": "durable"}, False

    monkeypatch.setattr(workflow, "_workflow_queue", FakeQueue())

    class UnexpectedThread:
        def __init__(self, *args, **kwargs):
            raise AssertionError("PostgreSQL workflow submission must not start an API thread")

    monkeypatch.setattr(workflow.threading, "Thread", UnexpectedThread)

    result = workflow.run_remaining_workflow("task-pg")

    assert result["jobId"] == "job-pg"
    assert result["taskId"] == "task-pg"
    assert result["status"] == "queued"
    assert result["durabilityStatus"] == "durable"
    assert len(calls) == 1
    assert calls[0][0]["jobId"] == "job-pg"
    assert calls[0][0]["retryScope"] == "remaining"
    assert calls[0][1] == workflow.WORKFLOW_JOB_MAX_ATTEMPTS


def test_workflow_router_postgres_status_reads_authoritative_queue(monkeypatch):
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_BACKEND", "postgres")
    expected = {
        "jobId": "job-status",
        "taskId": "task-status",
        "status": "running",
        "steps": [],
    }

    class FakeQueue:
        def get(self, job_id):
            assert job_id == "job-status"
            return expected

    monkeypatch.setattr(workflow, "_workflow_queue", FakeQueue())

    assert workflow.workflow_job_status("job-status") == expected


def test_workflow_job_heartbeat_renews_active_lease(monkeypatch, tmp_path):
    store_path = tmp_path / "workflow-jobs.json"
    jobs = {}
    workflow_jobs.create_workflow_job(
        jobs,
        job_id="job-heartbeat",
        task_id="task-heartbeat",
        now=lambda: "2026-07-12T10:00:00Z",
        owner_id="old-owner",
    )
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_STORE", store_path)
    monkeypatch.setattr(workflow, "_workflow_jobs", jobs)
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_OWNER_ID", "current-owner")
    monkeypatch.setattr(workflow, "WORKFLOW_JOB_LEASE_SECONDS", 120)
    monkeypatch.setattr(workflow, "_now_iso", lambda: "2026-07-12T10:01:00Z")

    assert workflow._job_heartbeat("job-heartbeat") is True

    job = workflow_jobs.load_workflow_jobs(store_path)["job-heartbeat"]
    assert job["ownerId"] == "current-owner"
    assert job["heartbeatAt"] == "2026-07-12T10:01:00Z"
    assert job["leaseExpiresAt"] == "2026-07-12T10:03:00Z"


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
        ("semantic", "failed"),
    ]
    assert job["failedStep"] == "semantic"
