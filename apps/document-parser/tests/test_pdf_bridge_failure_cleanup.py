from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import pdf_parser_artifact_transport as artifact_transport  # noqa: E402
from contracts import ParseConfig, ParseOutput, SourceFile  # noqa: E402
from providers import simple  # noqa: E402


def _load_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SIQ_ENV", "local")
    monkeypatch.setenv("SIQ_PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setenv("SIQ_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_RESULTS_ROOT", str(tmp_path / "results"))
    monkeypatch.setenv("SIQ_DOCUMENT_TASK_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_WORKER_AUTOSTART", "false")
    spec = importlib.util.spec_from_file_location(
        f"document_parser_failure_cleanup_{uuid.uuid4().hex}",
        BASE / "app.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.WORKER_AUTOSTART = False
    return module


def _source(tmp_path: Path) -> SourceFile:
    path = tmp_path / "failure.pdf"
    path.write_bytes(b"%PDF-1.4\n%failure-cleanup\n%%EOF\n")
    return SourceFile(
        path=path,
        filename=path.name,
        mime_type="application/pdf",
        extension=".pdf",
        file_size=path.stat().st_size,
        sha256="a" * 64,
    )


def _create_task(module, task_id: str, source: SourceFile, *, upstream_task_id: str = "") -> dict:
    module.store.create_task(
        {
            "task_id": task_id,
            "filename": source.filename,
            "owner_id": "alice",
            "tenant_id": "tenant-a",
            "market_scope": "CN",
            "user_role": "analyst",
            "document_kind": "pdf",
            "source_type": "upload",
            "source_url": "",
            "status": "running",
            "stage": "running",
            "progress_percent": 40,
            "file_size": source.file_size,
            "file_sha256": source.sha256,
            "mime_type": source.mime_type,
            "upstream_task_id": upstream_task_id,
            "upstream_status": "completed" if upstream_task_id else "",
            "config": {},
        }
    )
    task = module.store.get_task(task_id)
    assert task is not None
    return task


def _api_stage(staging_root: Path, upstream_task_id: str) -> Path:
    staging_root = artifact_transport._ensure_staging_root(staging_root)
    stage = staging_root / upstream_task_id
    stage.mkdir(parents=True)
    (stage / "result.md").write_text("# Staged\n", encoding="utf-8")
    budget = artifact_transport._DownloadBudget(
        artifact_transport.ArtifactTransportLimits(
            max_file_bytes=1024,
            max_total_bytes=1024,
            max_files=10,
            max_json_bytes=1024,
        )
    )
    artifact_transport._write_receipt(
        stage,
        task_id=upstream_task_id,
        bundle_sha256="a" * 64,
        budget=budget,
    )
    return stage


def test_process_build_failure_cleans_local_stage_but_retains_upstream(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "normal-build-failure"
    upstream_task_id = f"doc-{task_id}"
    _create_task(module, task_id, source)
    stage = _api_stage(module._task_pdf_staging_root(task_id), upstream_task_id)

    monkeypatch.setattr(
        module,
        "parse_source",
        lambda *_args, **_kwargs: ParseOutput(
            markdown="# Staged\n",
            blocks=[],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            raw_artifacts_dir=str(stage),
            upstream_task_id=upstream_task_id,
        ),
    )
    monkeypatch.setattr(
        module,
        "build_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("normalization failed")),
    )
    monkeypatch.setattr(
        module,
        "cleanup_pdf_parser_bridge_output",
        lambda *_args, **_kwargs: pytest.fail("failed normalization must retain upstream"),
    )

    result = module._process_task(task_id, source, ParseConfig(), document_kind="pdf")

    assert result["status"] == "failed"
    assert result["error"] == "normalization failed"
    assert not stage.exists()
    logs, _cursor = module.store.get_logs(task_id)
    assert any("保留 PDF bridge 上游任务供重试" in item["message"] for item in logs)


def test_cancelled_after_parse_deletes_upstream_and_local_stage_with_persisted_scope(
    tmp_path,
    monkeypatch,
):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "cancel-after-parse"
    upstream_task_id = f"doc-{task_id}"
    _create_task(module, task_id, source)
    stage = _api_stage(module._task_pdf_staging_root(task_id), upstream_task_id)
    calls: list[dict] = []

    def fake_parse(*_args, **_kwargs):
        module.store.update_task(
            task_id,
            status="cancelled",
            stage="cancelled",
        )
        return ParseOutput(
            markdown="# Cancelled\n",
            blocks=[],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            raw_artifacts_dir=str(stage),
            upstream_task_id=upstream_task_id,
        )

    def fake_json(url, method="GET", headers=None, **_kwargs):
        calls.append({"url": url, "method": method, "headers": dict(headers or {})})
        return {"success": True}

    monkeypatch.setattr(module, "parse_source", fake_parse)
    monkeypatch.setattr(simple, "_json_request", fake_json)

    result = module._process_task(task_id, source, ParseConfig(), document_kind="pdf")

    assert result["status"] == "cancelled"
    assert not stage.exists()
    assert len(calls) == 1
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith(f"/api/tasks/{upstream_task_id}")
    assert calls[0]["headers"]["X-SIQ-User-Id"] == "alice"
    assert calls[0]["headers"]["X-SIQ-Tenant-Id"] == "tenant-a"
    assert calls[0]["headers"]["X-SIQ-Market-Scope"] == "CN"
    assert calls[0]["headers"]["X-SIQ-User-Role"] == "analyst"


def test_poll_error_after_cancel_does_not_overwrite_cancelled_with_failed(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "cancel-poll-error"
    _create_task(module, task_id, source)

    def fake_parse(*_args, **_kwargs):
        module.store.update_task(task_id, status="cancelled", stage="cancelled")
        raise RuntimeError("upstream disappeared after cancellation")

    monkeypatch.setattr(module, "parse_source", fake_parse)

    result = module._process_task(task_id, source, ParseConfig(), document_kind="pdf")

    assert result["status"] == "cancelled"
    assert "upstream disappeared" not in str(result.get("error") or "")


@pytest.mark.parametrize(
    ("cancel_response", "expected_state"),
    [
        ({"_error": True, "status": 404, "detail": "foreign scope detail"}, "not_found"),
        ({"_error": True, "status": 503, "detail": "private upstream failure"}, "deferred"),
    ],
)
def test_bridge_cancel_is_idempotent_and_sanitizes_upstream_errors(
    tmp_path,
    monkeypatch,
    cancel_response,
    expected_state,
):
    upstream_task_id = "doc-cancel-contract"
    stage = _api_stage(tmp_path / ".pdf-parser-staging", upstream_task_id)
    calls: list[tuple[str, str]] = []

    def fake_json(url, method="GET", **_kwargs):
        calls.append((method, url))
        return cancel_response

    monkeypatch.setattr(simple, "_json_request", fake_json)

    result = simple.cancel_pdf_parser_bridge_task(
        upstream_task_id,
        raw_artifacts_dir=stage,
        identity_scope={
            "owner_id": "alice",
            "tenant_id": "tenant-a",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        staging_root=stage.parent,
    )

    assert result["state"] == expected_state
    assert not stage.exists()
    assert calls == [("POST", f"{simple._pdf_parser_api_base()}/api/cancel/{upstream_task_id}")]
    assert "detail" not in result


def test_bridge_cancel_stops_then_deletes_temporary_upstream(tmp_path, monkeypatch):
    upstream_task_id = "doc-cancel-success"
    stage = _api_stage(tmp_path / ".pdf-parser-staging", upstream_task_id)
    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_json(url, method="GET", headers=None, **_kwargs):
        calls.append((method, url, dict(headers or {})))
        return {"success": True}

    monkeypatch.setattr(simple, "_json_request", fake_json)

    result = simple.cancel_pdf_parser_bridge_task(
        upstream_task_id,
        raw_artifacts_dir=stage,
        identity_scope={
            "owner_id": "alice",
            "tenant_id": "tenant-a",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        staging_root=stage.parent,
    )

    assert result == {"state": "deleted", "cancelled": True, "staged_cleaned": True}
    assert [method for method, _url, _headers in calls] == ["POST", "DELETE"]
    assert calls[0][1].endswith(f"/api/cancel/{upstream_task_id}")
    assert calls[1][1].endswith(f"/api/tasks/{upstream_task_id}")
    assert all(headers["X-SIQ-User-Id"] == "alice" for _method, _url, headers in calls)


def test_cancel_route_uses_persisted_scope_for_upstream_cleanup(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "cancel-route"
    upstream_task_id = f"doc-{task_id}"
    _create_task(module, task_id, source, upstream_task_id=upstream_task_id)
    module.APP_ACCESS_TOKEN = "document-token"
    seen: dict[str, object] = {}

    def fake_cancel(upstream_id, **kwargs):
        seen["upstream_task_id"] = upstream_id
        seen["identity_scope"] = dict(kwargs.get("identity_scope") or {})
        seen["raw_artifacts_dir"] = kwargs.get("raw_artifacts_dir")
        return {"state": "deleted", "cancelled": True, "staged_cleaned": False}

    monkeypatch.setattr(module, "cancel_pdf_parser_bridge_task", fake_cancel)

    response = module.app.test_client().post(
        f"/api/cancel/{task_id}",
        headers={
            "X-Document-Parser-Token": "document-token",
            "X-SIQ-User-Id": "alice",
            "X-SIQ-Tenant-Id": "tenant-a",
            "X-SIQ-Market-Scope": "CN",
            "X-SIQ-User-Role": "analyst",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["upstream_cancel_state"] == "deleted"
    assert module.store.get_task(task_id)["status"] == "cancelled"
    assert seen["upstream_task_id"] == upstream_task_id
    assert seen["identity_scope"] == {
        "owner_id": "alice",
        "tenant_id": "tenant-a",
        "market_scope": "CN",
        "user_role": "analyst",
    }
    assert Path(seen["raw_artifacts_dir"]).name == upstream_task_id


def test_recovery_build_failure_cleans_local_stage_but_retains_upstream(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "recovery-build-failure"
    upstream_task_id = f"doc-{task_id}"
    task = _create_task(module, task_id, source, upstream_task_id=upstream_task_id)
    stage = _api_stage(module._task_pdf_staging_root(task_id), upstream_task_id)
    output = ParseOutput(
        markdown="# Recovered\n",
        blocks=[],
        provider_name="pdf_parser_bridge",
        document_kind="pdf",
        raw_artifacts_dir=str(stage),
        upstream_task_id=upstream_task_id,
    )

    monkeypatch.setattr(module, "_source_file_from_task", lambda _task: source)
    monkeypatch.setattr(module, "_materialize_pdf_parser_result", lambda *_args, **_kwargs: stage)
    monkeypatch.setattr(module, "parse_mineru_output_dir", lambda *_args, **_kwargs: (source, output))
    monkeypatch.setattr(module, "rewrite_image_paths_to_result", lambda _output: None)
    monkeypatch.setattr(
        module,
        "build_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("recovery normalization failed")),
    )
    monkeypatch.setattr(
        module,
        "cleanup_pdf_parser_bridge_output",
        lambda *_args, **_kwargs: pytest.fail("failed recovery must retain upstream"),
    )

    with pytest.raises(RuntimeError, match="recovery normalization failed"):
        module._finalize_from_pdf_bridge_result(
            task,
            upstream_task_id,
            ParseConfig(),
        )

    assert not stage.exists()
    latest = module.store.get_task(task_id)
    assert latest is not None
    logs, _cursor = module.store.get_logs(task_id)
    assert any("保留 PDF bridge 上游任务供重试" in item["message"] for item in logs)


def test_completed_parse_survives_cleanup_failure_and_worker_retries(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    source = _source(tmp_path)
    task_id = "completed-cleanup-retry"
    upstream_task_id = f"doc-{task_id}"
    _create_task(module, task_id, source)
    stage = _api_stage(module._task_pdf_staging_root(task_id), upstream_task_id)
    cleanup_statuses: list[str] = []

    monkeypatch.setattr(
        module,
        "parse_source",
        lambda *_args, **_kwargs: ParseOutput(
            markdown="# Complete\n",
            blocks=[],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            raw_artifacts_dir=str(stage),
            upstream_task_id=upstream_task_id,
        ),
    )
    monkeypatch.setattr(
        module,
        "build_artifacts",
        lambda **_kwargs: {"quality_status": "pass", "parser_provider": "pdf_parser_bridge"},
    )
    monkeypatch.setattr(module, "artifact_summary", lambda *_args, **_kwargs: {})

    def failing_cleanup(*_args, **_kwargs):
        cleanup_statuses.append(module.store.get_task(task_id)["status"])
        raise RuntimeError("temporary cleanup outage")

    monkeypatch.setattr(module, "cleanup_pdf_parser_bridge_resources", failing_cleanup)

    result = module._process_task(task_id, source, ParseConfig(), document_kind="pdf")

    assert result["status"] == "completed"
    assert result["upstream_cleanup_status"] == "deferred"
    assert result["upstream_cleanup_attempts"] == 1
    assert cleanup_statuses == ["completed"]

    monkeypatch.setattr(
        module,
        "cleanup_pdf_parser_bridge_resources",
        lambda *_args, **_kwargs: {
            "state": "deleted",
            "cleaned": True,
            "staged_cleaned": True,
        },
    )
    module.store.update_task(task_id, upstream_cleanup_updated_at="")

    assert module._retry_one_pending_upstream_cleanup() is True
    retried = module.store.get_task(task_id)
    assert retried["status"] == "completed"
    assert retried["upstream_cleanup_status"] == "cleaned"
    assert retried["upstream_cleanup_attempts"] == 2
