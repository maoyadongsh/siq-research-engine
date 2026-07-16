from __future__ import annotations

import importlib.util
import io
import sqlite3
import sys
import urllib.error
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from contracts import ParseConfig, ParseOutput, SourceFile  # noqa: E402
from providers import simple  # noqa: E402
from task_store import TaskStore  # noqa: E402

IDENTITY_SCOPE = {
    "owner_id": "alice",
    "tenant_id": "tenant-a",
    "market_scope": "CN",
    "user_role": "analyst",
}


def _load_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SIQ_ENV", "local")
    monkeypatch.setenv("SIQ_PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setenv("SIQ_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_RESULTS_ROOT", str(tmp_path / "results"))
    monkeypatch.setenv("SIQ_DOCUMENT_OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("SIQ_DOCUMENT_TASK_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_WORKER_AUTOSTART", "false")
    monkeypatch.setenv("SIQ_PDF2MD_DATA_DIR", str(tmp_path / "pdf-parser"))
    spec = importlib.util.spec_from_file_location(
        f"document_parser_identity_test_{uuid.uuid4().hex}",
        BASE / "app.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.WORKER_AUTOSTART = False
    return module


def _source(tmp_path: Path, name: str = "scope.pdf") -> SourceFile:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4\n%identity-scope\n%%EOF\n")
    return SourceFile(
        path=path,
        filename=name,
        mime_type="application/pdf",
        extension=".pdf",
        file_size=path.stat().st_size,
        sha256="a" * 64,
        source_type="upload",
    )


def _task_payload(task_id: str, *, status: str = "queued", upstream_task_id: str = "") -> dict:
    return {
        "task_id": task_id,
        "filename": "scope.pdf",
        **IDENTITY_SCOPE,
        "parse_config_hash": "b" * 64,
        "document_kind": "pdf",
        "source_type": "upload",
        "source_url": "",
        "status": status,
        "stage": status,
        "progress_percent": 10,
        "file_size": 32,
        "file_sha256": "a" * 64,
        "mime_type": "application/pdf",
        "upstream_task_id": upstream_task_id,
        "upstream_status": "processing" if upstream_task_id else "",
        "config": {},
    }


def test_pdf_parser_headers_are_built_only_from_persisted_scope(monkeypatch):
    monkeypatch.setenv("SIQ_PDF2MD_ACCESS_TOKEN", "internal-pdf-token")

    headers = simple._pdf_parser_headers(
        {"Accept": "application/json", "X-SIQ-User-Id": "untrusted"},
        identity_scope=IDENTITY_SCOPE,
    )

    assert headers == {
        "Accept": "application/json",
        "X-PDF2MD-Token": "internal-pdf-token",
        "X-SIQ-User-Id": "alice",
        "X-SIQ-Tenant-Id": "tenant-a",
        "X-SIQ-Market-Scope": "CN",
        "X-SIQ-User-Role": "analyst",
    }

    poisoned = simple._pdf_parser_headers(
        identity_scope={
            "owner_id": "alice\r\nX-Evil: yes",
            "tenant_id": "tenant/a",
            "market_scope": "UNKNOWN",
            "user_role": "analyst\nadmin",
        }
    )
    assert "\r" not in poisoned["X-SIQ-User-Id"]
    assert "\n" not in poisoned["X-SIQ-User-Role"]
    assert "X-SIQ-Market-Scope" not in poisoned


def test_document_request_scope_is_persisted_with_user_role(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    module.APP_ACCESS_TOKEN = "document-token"

    response = module.app.test_client().post(
        "/api/tasks",
        data={"market": "CN", "files": (io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "owned.pdf")},
        headers={
            "X-Document-Parser-Token": "document-token",
            "X-SIQ-User-Id": "alice",
            "X-SIQ-Tenant-Id": "tenant-a",
            "X-SIQ-Market-Scope": "CN",
            "X-SIQ-User-Role": "analyst",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    task_id = response.get_json()["tasks"][0]["task_id"]
    task = module.store.get_task(task_id)
    assert task is not None
    assert module._task_identity_scope(task) == IDENTITY_SCOPE


def test_task_store_migrates_user_role_without_losing_legacy_identity(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                owner_id TEXT NOT NULL DEFAULT 'system',
                tenant_id TEXT NOT NULL DEFAULT 'unknown',
                market_scope TEXT NOT NULL DEFAULT 'unknown',
                parse_config_hash TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL,
                config_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, filename, owner_id, tenant_id, market_scope,
                parse_config_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-scope",
                "legacy.pdf",
                "alice",
                "tenant-a",
                "CN",
                "hash",
                "failed",
                "2026-07-16T00:00:00Z",
                "2026-07-16T00:00:00Z",
            ),
        )

    store = TaskStore(db_path)
    task = store.get_task("legacy-scope")

    assert task is not None
    assert task["owner_id"] == "alice"
    assert task["tenant_id"] == "tenant-a"
    assert task["market_scope"] == "CN"
    assert task["user_role"] == ""
    with store.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "user_role" in columns


def test_process_task_passes_persisted_scope_to_parse_and_cleanup(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    module.store.create_task(_task_payload("normal-scope"))
    seen: dict[str, dict] = {}

    def fake_parse_source(task_id, source_file, config, document_kind, on_status=None, identity_scope=None):
        seen["parse"] = dict(identity_scope or {})
        return ParseOutput(
            markdown="# Scoped\n",
            blocks=[],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            upstream_task_id="doc-normal-scope",
        )

    def fake_cleanup(upstream_task_id, *, identity_scope=None, **_kwargs):
        assert upstream_task_id == "doc-normal-scope"
        assert module.store.get_task("normal-scope")["status"] == "completed"
        seen["cleanup"] = dict(identity_scope or {})
        return {"state": "deleted", "cleaned": True, "staged_cleaned": False}

    monkeypatch.setattr(module, "parse_source", fake_parse_source)
    monkeypatch.setattr(module, "cleanup_pdf_parser_bridge_resources", fake_cleanup)
    monkeypatch.setattr(module, "build_artifacts", lambda **_kwargs: {"quality_status": "pass", "parser_provider": "pdf_parser_bridge"})
    monkeypatch.setattr(module, "artifact_summary", lambda *_args, **_kwargs: {})

    result = module._process_task("normal-scope", source, ParseConfig(), document_kind="pdf")

    assert result["status"] == "completed"
    assert seen == {"parse": IDENTITY_SCOPE, "cleanup": IDENTITY_SCOPE}


def test_json_requests_do_not_follow_redirects_with_bridge_credentials(monkeypatch):
    seen: dict[str, object] = {}

    class FakeOpener:
        def open(self, request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = {name.lower(): value for name, value in request.header_items()}
            seen["timeout"] = timeout
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://untrusted.example/collect"},
                io.BytesIO(b"redirect rejected"),
            )

    def fake_build_opener(*handlers):
        seen["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(simple.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        simple.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("credentialed JSON requests must use no-redirect opener"),
    )

    response = simple._json_request(
        "https://pdf-parser.internal/api/status/doc-1",
        headers={"X-PDF2MD-Token": "secret", "X-SIQ-Tenant-Id": "tenant-a"},
    )

    assert response["_error"] is True
    assert response["status"] == 302
    assert seen["headers"]["x-pdf2md-token"] == "secret"
    assert seen["headers"]["x-siq-tenant-id"] == "tenant-a"
    assert any(isinstance(handler, simple._NoRedirectHandler) for handler in seen["handlers"])


def test_recovery_status_uses_persisted_scope_headers(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    module.store.create_task(
        _task_payload(
            "recover-scope",
            status="failed",
            upstream_task_id="doc-recover-scope",
        )
    )
    task = module.store.get_task("recover-scope")
    seen: dict[str, object] = {}

    def fake_status(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return {"status": "processing", "stage": "processing", "processed_pages": 1, "total_pages": 3}

    monkeypatch.setattr(module, "pdf_parser_json_request", fake_status)

    result = module._recover_pdf_bridge_task(task)

    assert result["status"] == "running"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["X-SIQ-User-Id"] == "alice"
    assert headers["X-SIQ-Tenant-Id"] == "tenant-a"
    assert headers["X-SIQ-Market-Scope"] == "CN"
    assert headers["X-SIQ-User-Role"] == "analyst"


def test_restarted_worker_resumes_existing_upstream_without_reupload(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    module.store.create_task(
        _task_payload(
            "restart-scope",
            status="running",
            upstream_task_id="doc-restart-scope",
        )
    )

    restarted_store = TaskStore(module.store.db_path)
    assert restarted_store.requeue_interrupted_tasks() == 1
    claimed = restarted_store.claim_next_queued_task()
    assert claimed is not None
    assert claimed["upstream_task_id"] == "doc-restart-scope"
    assert module._task_identity_scope(claimed) == IDENTITY_SCOPE
    module.store = restarted_store
    module.worker_stop_event.clear()
    seen: dict[str, dict] = {}

    def fake_recover(task, *, identity_scope=None):
        seen["scope"] = dict(identity_scope or {})
        result = dict(task)
        result["status"] = "completed"
        result["stage"] = "completed"
        return result

    monkeypatch.setattr(module, "_recover_pdf_bridge_task", fake_recover)
    monkeypatch.setattr(module, "_source_file_from_task", lambda _task: pytest.fail("resume must not read a new upload"))
    monkeypatch.setattr(module, "_process_task", lambda *_args, **_kwargs: pytest.fail("resume must not start a new parse"))
    monkeypatch.setattr(simple, "_stream_multipart_post", lambda *_args, **_kwargs: pytest.fail("resume must not re-upload"))

    result = module._process_claimed_task(claimed)

    assert result["status"] == "completed"
    assert seen["scope"] == IDENTITY_SCOPE


def test_submit_status_result_and_delete_share_one_identity_scope(tmp_path, monkeypatch):
    results_root = tmp_path / "pdf-results"
    upstream_task_id = "doc-lifecycle-scope"
    result_dir = results_root / upstream_task_id
    result_dir.mkdir(parents=True)
    (result_dir / "document_full.json").write_text("{}", encoding="utf-8")
    (result_dir / "result.md").write_text("# Scoped lifecycle\n", encoding="utf-8")
    source = _source(tmp_path, "lifecycle.pdf")
    monkeypatch.setenv("SIQ_PDF_RESULTS_ROOT", str(results_root))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT", "shared_fs")
    monkeypatch.setenv("SIQ_PDF2MD_ACCESS_TOKEN", "internal-pdf-token")
    seen: list[tuple[str, str, dict[str, str]]] = []

    def fake_submit(url, **kwargs):
        seen.append(("submit", url, dict(kwargs.get("headers") or {})))
        return {"task_id": upstream_task_id}

    def fake_json(url, method="GET", headers=None, **_kwargs):
        operation = "delete" if method == "DELETE" else "status" if "/api/status/" in url else "result"
        seen.append((operation, url, dict(headers or {})))
        if operation == "status":
            return {"status": "completed", "stage": "completed"}
        if operation == "result":
            return {
                "artifacts": {
                    "document_full.json": {
                        "exists": True,
                        "path": str(result_dir / "document_full.json"),
                    },
                    "result.md": {
                        "exists": True,
                        "path": str(result_dir / "result.md"),
                    },
                }
            }
        return {"success": True}

    monkeypatch.setattr(simple, "_stream_multipart_post", fake_submit)
    monkeypatch.setattr(simple, "_json_request", fake_json)

    output = simple._parse_pdf_via_pdf_parser(
        "lifecycle-scope",
        source,
        ParseConfig(),
        identity_scope=IDENTITY_SCOPE,
    )
    cleanup = simple.cleanup_pdf_parser_bridge_output(
        output,
        identity_scope=IDENTITY_SCOPE,
    )

    assert cleanup and "已删除" in cleanup
    assert [operation for operation, _url, _headers in seen] == [
        "submit",
        "status",
        "result",
        "delete",
    ]
    for _operation, _url, headers in seen:
        assert headers["X-PDF2MD-Token"] == "internal-pdf-token"
        assert headers["X-SIQ-User-Id"] == "alice"
        assert headers["X-SIQ-Tenant-Id"] == "tenant-a"
        assert headers["X-SIQ-Market-Scope"] == "CN"
        assert headers["X-SIQ-User-Role"] == "analyst"


def test_api_artifact_staging_receives_persisted_scope_headers(tmp_path, monkeypatch):
    source = _source(tmp_path, "artifact-scope.pdf")
    staged_dir = tmp_path / "staged" / "doc-artifact-scope"
    staged_dir.mkdir(parents=True)
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT", "api")
    monkeypatch.setenv("SIQ_PDF2MD_ACCESS_TOKEN", "internal-pdf-token")
    seen: dict[str, object] = {}

    def fake_stage(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(result_dir=staged_dir)

    monkeypatch.setattr(simple, "stage_pdf_parser_artifacts", fake_stage)

    result = simple._materialize_pdf_parser_result(
        "artifact-scope",
        "doc-artifact-scope",
        source,
        result_payload={"artifacts": {}},
        identity_scope=IDENTITY_SCOPE,
    )

    assert result == staged_dir
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["X-PDF2MD-Token"] == "internal-pdf-token"
    assert headers["X-SIQ-User-Id"] == "alice"
    assert headers["X-SIQ-Tenant-Id"] == "tenant-a"
    assert headers["X-SIQ-Market-Scope"] == "CN"
    assert headers["X-SIQ-User-Role"] == "analyst"


def test_wrong_scope_404_fails_closed_without_retry_or_materialize(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    module.store.create_task(
        _task_payload(
            "wrong-scope",
            status="running",
            upstream_task_id="doc-wrong-scope",
        )
    )
    task = module.store.get_task("wrong-scope")
    calls: list[dict[str, str]] = []

    def fake_status(_url, headers=None, **_kwargs):
        calls.append(dict(headers or {}))
        return {
            "_error": True,
            "status": 404,
            "detail": "foreign tenant secret must not be persisted",
        }

    monkeypatch.setattr(module, "pdf_parser_json_request", fake_status)
    monkeypatch.setattr(
        module,
        "_finalize_from_pdf_bridge_result",
        lambda *_args, **_kwargs: pytest.fail("wrong scope must not materialize artifacts"),
    )

    result = module._resume_pdf_bridge_task(
        task,
        identity_scope=module._task_identity_scope(task),
    )

    assert result["status"] == "failed"
    assert len(calls) == 1
    assert calls[0]["X-SIQ-User-Id"] == "alice"
    assert calls[0]["X-SIQ-Tenant-Id"] == "tenant-a"
    assert "foreign tenant secret" not in str(result.get("error") or "")
