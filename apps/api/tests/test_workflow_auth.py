from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import eval_e2e, workflow
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.usage_service import UserArtifact
from sqlmodel import Session, SQLModel, create_engine


def _user(user_id: int, role: UserRole = UserRole.ANALYST) -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"User {user_id}",
        role=role,
        approval_status="approved",
        is_active=True,
    )


def _seed_artifact(
    session: Session,
    *,
    user_id: int,
    artifact_type: str,
    task_id: str,
) -> None:
    session.add(
        UserArtifact(
            user_id=user_id,
            artifact_type=artifact_type,
            artifact_key=task_id,
            title=f"{task_id}.pdf",
            path=f"/api/workflow/task/{task_id}/status",
            source="test",
            global_artifact_id=task_id,
        )
    )
    session.commit()


def _workflow_client(tmp_path: Path, current_user: User) -> tuple[TestClient, dict[str, object]]:
    db_path = tmp_path / "workflow-auth.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    state: dict[str, object] = {"user": current_user, "engine": engine}

    async def override_current_user() -> User:
        return state["user"]  # type: ignore[return-value]

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[workflow.get_session] = override_session
    app.include_router(workflow.router, prefix="/api")
    return TestClient(app), state


def test_workflow_http_read_and_write_are_bound_to_parse_task_owner(monkeypatch, tmp_path):
    owner = _user(1, UserRole.ANALYST)
    other = _user(2, UserRole.ANALYST)
    client, state = _workflow_client(tmp_path, owner)
    engine = state["engine"]
    assert engine is not None
    with Session(engine) as session:
        _seed_artifact(session, user_id=1, artifact_type="parse", task_id="owner-task")

    status_calls: list[str] = []
    wiki_import_calls: list[str] = []
    semantic_job_calls: list[str] = []

    monkeypatch.setattr(
        workflow,
        "_workflow_status_payload",
        lambda task_id: status_calls.append(task_id) or {"ok": True, "taskId": task_id},
    )
    monkeypatch.setattr(
        workflow,
        "_import_task_to_wiki",
        lambda task_id: wiki_import_calls.append(task_id) or {"ok": True, "taskId": task_id},
    )
    monkeypatch.setattr(
        workflow,
        "_start_workflow_step_job",
        lambda task_id, step, runner, *, metadata=None: (
            semantic_job_calls.append(f"{step}:{task_id}") or {"jobId": "job-1", "taskId": task_id}
        ),
    )

    owner_status = client.get("/api/workflow/task/owner-task/status")
    assert owner_status.status_code == 200
    assert owner_status.json()["taskId"] == "owner-task"
    assert status_calls == ["owner-task"]

    state["user"] = other
    other_status = client.get("/api/workflow/task/owner-task/status")
    assert other_status.status_code == 403
    assert status_calls == ["owner-task"]

    other_wiki = client.post("/api/workflow/task/owner-task/wiki-import")
    assert other_wiki.status_code == 403
    assert wiki_import_calls == []

    other_semantic = client.post("/api/workflow/task/owner-task/semantic")
    assert other_semantic.status_code == 403
    assert semantic_job_calls == []

    client.close()


def test_workflow_viewer_can_read_owned_task_but_cannot_start_write_steps(monkeypatch, tmp_path):
    viewer = _user(3, UserRole.VIEWER)
    client, state = _workflow_client(tmp_path, viewer)
    engine = state["engine"]
    assert engine is not None
    with Session(engine) as session:
        _seed_artifact(session, user_id=3, artifact_type="parse", task_id="viewer-task")

    status_calls: list[str] = []
    write_calls: list[str] = []
    monkeypatch.setattr(
        workflow,
        "_workflow_status_payload",
        lambda task_id: status_calls.append(task_id) or {"ok": True, "taskId": task_id},
    )
    monkeypatch.setattr(
        workflow,
        "_import_task_to_wiki",
        lambda task_id: write_calls.append(task_id) or {"ok": True, "taskId": task_id},
    )

    read_response = client.get("/api/workflow/task/viewer-task/status")
    assert read_response.status_code == 200
    assert status_calls == ["viewer-task"]

    write_response = client.post("/api/workflow/task/viewer-task/wiki-import")
    assert write_response.status_code == 403
    assert "report.create" in str(write_response.json()["detail"])
    assert write_calls == []

    client.close()


def test_workflow_db_and_run_remaining_require_system_config(monkeypatch, tmp_path):
    analyst = _user(4, UserRole.ANALYST)
    client, state = _workflow_client(tmp_path, analyst)
    engine = state["engine"]
    assert engine is not None
    with Session(engine) as session:
        _seed_artifact(session, user_id=4, artifact_type="parse", task_id="analyst-task")

    monkeypatch.setattr(workflow, "_workflow_jobs", {})
    monkeypatch.setattr(workflow, "_workflow_preflight", lambda task_id: {"ok": True})

    db_response = client.post("/api/workflow/task/analyst-task/db-import")
    assert db_response.status_code == 403
    assert "system.config" in str(db_response.json()["detail"])

    run_remaining_response = client.post("/api/workflow/task/analyst-task/run-remaining")
    assert run_remaining_response.status_code == 403
    assert "system.config" in str(run_remaining_response.json()["detail"])
    assert workflow._workflow_jobs == {}

    client.close()


def test_document_workflow_http_is_bound_to_document_parse_owner(monkeypatch, tmp_path):
    owner = _user(5, UserRole.ANALYST)
    other = _user(6, UserRole.ANALYST)
    client, state = _workflow_client(tmp_path, owner)
    engine = state["engine"]
    assert engine is not None
    with Session(engine) as session:
        _seed_artifact(session, user_id=5, artifact_type="document_parse", task_id="doc-task")

    status_calls: list[str] = []
    semantic_calls: list[str] = []
    monkeypatch.setattr(
        workflow,
        "_document_workflow_status_payload",
        lambda task_id, collection=None: status_calls.append(task_id) or {"ok": True, "taskId": task_id},
    )
    monkeypatch.setattr(
        workflow.document_workflow_service,
        "document_semantic_plan",
        lambda **kwargs: semantic_calls.append(str(kwargs["package_dir"])) or {"args": ["python", "chunks.py"], "timeout": 1, "semantic_mode": "contract"},
    )
    monkeypatch.setattr(workflow, "_document_wiki_status", lambda task_id, collection=None: {"status": "ready", "path": str(tmp_path)})
    monkeypatch.setattr(workflow, "_run_command", lambda *args, **kwargs: {"returnCode": 0, "stdout": "", "stderr": ""})
    monkeypatch.setattr(workflow, "_document_milvus_status", lambda task_id, collection=None: {"status": "skipped"})
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", tmp_path / "chunks.py")
    (tmp_path / "chunks.py").write_text("print('ok')\n", encoding="utf-8")

    owner_status = client.get("/api/workflow/document/doc-task/status")
    assert owner_status.status_code == 200
    assert status_calls == ["doc-task"]

    state["user"] = other
    other_status = client.get("/api/workflow/document/doc-task/status")
    assert other_status.status_code == 403
    assert status_calls == ["doc-task"]

    other_semantic = client.post("/api/workflow/document/doc-task/semantic")
    assert other_semantic.status_code == 403
    assert semantic_calls == []

    client.close()


def test_eval_e2e_router_is_system_config_only(monkeypatch):
    state: dict[str, User] = {"user": _user(7, UserRole.ANALYST)}

    async def override_current_user() -> User:
        return state["user"]

    app = FastAPI()
    app.dependency_overrides[get_current_user] = override_current_user
    app.include_router(eval_e2e.router, prefix="/api")
    monkeypatch.setattr(eval_e2e, "_iter_wiki_companies", lambda: [])
    client = TestClient(app)

    analyst_response = client.get("/api/eval/e2e/health")
    assert analyst_response.status_code == 403
    assert "system.config" in str(analyst_response.json()["detail"])

    state["user"] = _user(8, UserRole.ADMIN)
    admin_response = client.get("/api/eval/e2e/health")
    assert admin_response.status_code == 200
    assert admin_response.json()["status"] == "ok"

    client.close()
