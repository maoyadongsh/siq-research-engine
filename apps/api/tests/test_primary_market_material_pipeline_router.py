from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from routers import primary_market_materials as router

from services import primary_market_materials


def _user(user_id: int = 7):
    return SimpleNamespace(id=user_id, username=f"user-{user_id}", role="analyst")


class _ExpiringUser:
    def __init__(self):
        self.expired = False

    def _value(self, value):
        if self.expired:
            raise AssertionError("expired ORM user was accessed after an async commit")
        return value

    @property
    def id(self):
        return self._value(7)

    @property
    def username(self):
        return self._value("expiring-user")

    @property
    def role(self):
        return self._value("analyst")


def test_generic_material_parse_endpoint_uses_document_parser_for_office_file(monkeypatch):
    document = {
        "deal_id": "DEAL-MATERIAL-001",
        "document_id": "DOC-0123456789ABCDEF",
        "document_type": "business_plan",
        "original_filename": "business-plan.docx",
        "parser_kind": "document",
    }
    run = {"parse_run_id": "PRUN-20260716-0123456789AB", "status": "queued"}
    seen = {}
    monkeypatch.setattr(router, "_require_access", lambda *_args: None)
    monkeypatch.setattr(
        primary_market_materials,
        "read_material_parse_status",
        lambda *_args: {"document": document, "parse_run": None},
    )
    monkeypatch.setattr(
        primary_market_materials,
        "get_primary_market_material",
        lambda *_args: document,
    )
    monkeypatch.setattr(primary_market_materials, "create_parse_run", lambda *_args, **_kwargs: run)

    async def submit_generic(**kwargs):
        seen.update(kwargs)
        return run, False

    async def reject_prospectus(**_kwargs):
        raise AssertionError("ordinary materials must not use the prospectus parser")

    monkeypatch.setattr(router, "_submit_generic_document_parse", submit_generic)
    monkeypatch.setattr(router, "_submit_document_parse", reject_prospectus)
    monkeypatch.setattr(
        router,
        "_material_pipeline_response",
        lambda *_args, **_kwargs: {"pipeline": {"stages": {"parse": {"status": "queued"}}}},
    )

    response = asyncio.run(
        router.start_material_parse(
            "DEAL-MATERIAL-001",
            document["document_id"],
            current_user=_user(),
            async_session=object(),
        )
    )

    assert seen["document"]["original_filename"].endswith(".docx")
    assert response["pipeline"]["stages"]["parse"]["status"] == "queued"


def test_generic_submission_freezes_user_identity_before_async_commits(monkeypatch, tmp_path):
    user = _ExpiringUser()
    raw_path = tmp_path / "business-plan.pdf"
    raw_path.write_bytes(b"%PDF-1.4\nsmoke")
    document = {
        "document_id": "DOC-0123456789ABCDEF",
        "original_filename": raw_path.name,
        "content_type": "application/pdf",
    }
    parse_run = {
        "parse_run_id": "PRUN-20260716-0123456789AB",
        "parser_owner_scope": {
            "owner_id": "7",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
    }
    seen = {}

    monkeypatch.setattr(router, "_generic_raw_path", lambda *_args: raw_path)

    async def reserve_quota(_session, current_user, increment=1):
        assert current_user.id == 7
        assert increment == 1
        current_user.expired = True

    monkeypatch.setattr(router.document_parser, "_enforce_quota_or_429_async", reserve_quota)

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"tasks": [{"task_id": "document-task-1", "parser_version": "document_parser_v1"}]}

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            seen["headers"] = kwargs["headers"]
            return Response()

    monkeypatch.setattr(router.httpx, "AsyncClient", Client)

    async def record_usage(*_args, **kwargs):
        seen["usage_user_id"] = kwargs["user_id"]

    async def record_artifact(*_args, **kwargs):
        seen["artifact_user_id"] = kwargs["user_id"]

    monkeypatch.setattr(router, "record_usage_async", record_usage)
    monkeypatch.setattr(router.document_parser, "_record_document_artifact_async", record_artifact)
    monkeypatch.setattr(
        router.primary_market_materials,
        "update_parse_run_submission",
        lambda *_args, **kwargs: seen.setdefault("actor", kwargs["actor"]) or {"status": "queued"},
    )
    monkeypatch.setattr(router.deal_documents, "bind_parser_task", lambda *_args, **_kwargs: None)

    updated, reused = asyncio.run(
        router._submit_generic_document_parse(
            deal_id="DEAL-MATERIAL-001",
            document=document,
            parse_run=parse_run,
            user=user,
            session=object(),
        )
    )

    assert reused is False
    assert updated == {"id": 7, "username": "expiring-user"}
    assert seen["headers"]["X-SIQ-User-Id"] == "7"
    assert seen["usage_user_id"] == 7
    assert seen["artifact_user_id"] == 7


def test_prospectus_submission_freezes_actor_before_submission_hooks_commit(monkeypatch, tmp_path):
    user = _ExpiringUser()
    raw_path = tmp_path / "prospectus.pdf"
    raw_path.write_bytes(b"%PDF-1.4\nprospectus")
    parse_run = {"parse_run_id": "PRUN-20260716-ABCDEF012345"}
    seen = {}
    monkeypatch.setattr(router.primary_market_materials, "deal_raw_pdf_path", lambda *_args: raw_path)

    async def submit_pdf_parse(**_kwargs):
        user.expired = True
        return SimpleNamespace(
            new_tasks=[{"task_id": "prospectus-task-1", "parser_version": "pdf_parser_v1"}],
            reused_tasks=[],
            payload={},
            status_code=200,
            parse_config_hash="a" * 64,
        )

    monkeypatch.setattr(router, "submit_pdf_parse", submit_pdf_parse)
    monkeypatch.setattr(
        router.primary_market_materials,
        "update_parse_run_submission",
        lambda *_args, **kwargs: seen.setdefault("actor", kwargs["actor"]) or {"status": "queued"},
    )

    updated, reused = asyncio.run(
        router._submit_document_parse(
            deal_id="DEAL-MATERIAL-001",
            document={"document_id": "DOC-0123456789ABCDEF", "original_filename": raw_path.name},
            parse_run=parse_run,
            user=user,
            session=object(),
        )
    )

    assert reused is False
    assert updated == {"id": 7, "username": "expiring-user"}


def test_degraded_collaborator_poll_does_not_mutate_owner_run(monkeypatch):
    document = {
        "document_id": "DOC-0123456789ABCDEF",
        "parse_task_id": "owner-task-1",
        "parse_status": "queued",
    }
    run = {
        "parse_run_id": "PRUN-20260716-0123456789AB",
        "parser_task_id": "owner-task-1",
        "status": "queued",
        "submitted_by": {"id": 7},
    }
    monkeypatch.setattr(
        router,
        "_latest_or_bound_generic_run",
        lambda *_args, **_kwargs: ({"document": document, "parse_run": run}, run),
    )

    async def degraded(*_args, **_kwargs):
        raise HTTPException(
            503,
            detail={"code": "document_parser_poll_degraded", "retryable": True},
        )

    monkeypatch.setattr(router, "_fetch_generic_parser_task", degraded)
    monkeypatch.setattr(
        primary_market_materials,
        "update_parse_run_submission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("run must not mutate")),
    )
    monkeypatch.setattr(
        router,
        "_material_pipeline_response",
        lambda *_args, **_kwargs: {
            "pipeline": {"stages": {"parse": {"status": "queued"}}}
        },
    )

    response = asyncio.run(
        router._reconcile_generic_material(
            "DEAL-MATERIAL-001",
            document["document_id"],
            user=_user(9),
        )
    )

    assert response["pipeline"]["stages"]["parse"]["poll_status"] == "degraded"
    assert response["pipeline"]["stages"]["parse"]["retryable"] is True


def test_completed_with_warnings_is_terminal_success():
    assert "completed_with_warnings" in primary_market_materials.PARSER_SUCCESS_STATUSES


def test_temporary_archive_outage_stays_retryable_and_does_not_write_wiki_failure(
    monkeypatch,
):
    document = {
        "document_id": "DOC-0123456789ABCDEF",
        "parse_task_id": "owner-task-1",
        "parse_status": "queued",
    }
    run = {
        "parse_run_id": "PRUN-20260716-0123456789AB",
        "parser_task_id": "owner-task-1",
        "status": "queued",
        "parser_owner_scope": {
            "owner_id": "7",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
    }
    monkeypatch.setattr(
        router,
        "_latest_or_bound_generic_run",
        lambda *_args, **_kwargs: ({"document": document, "parse_run": run}, run),
    )

    async def completed(*_args, **_kwargs):
        return {"task_id": "owner-task-1", "status": "completed"}

    async def unavailable(**_kwargs):
        raise router.document_parser_artifact_transport.DocumentArtifactTransportUnavailable(
            "parser API unavailable"
        )

    statuses = []
    monkeypatch.setattr(router, "_fetch_generic_parser_task", completed)
    monkeypatch.setattr(
        router.document_parser_artifact_transport,
        "archive_document_parser_result",
        unavailable,
    )
    monkeypatch.setattr(
        primary_market_materials,
        "update_parse_run_submission",
        lambda *_args, **kwargs: statuses.append(kwargs["status"]) or run,
    )
    monkeypatch.setattr(
        router.primary_market_wiki,
        "record_company_wiki_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("temporary transport outage must not write a Wiki failure")
        ),
    )
    monkeypatch.setattr(
        router,
        "_material_pipeline_response",
        lambda *_args, **_kwargs: {
            "pipeline": {"stages": {"parse": {"status": "archiving"}}}
        },
    )

    response = asyncio.run(
        router._reconcile_generic_material(
            "DEAL-MATERIAL-001",
            document["document_id"],
            user=_user(9),
        )
    )

    assert statuses == ["archiving"]
    assert response["pipeline"]["stages"]["parse"]["archive_status"] == "degraded"
    assert response["pipeline"]["stages"]["parse"]["retryable"] is True


def test_prospectus_poll_uses_frozen_run_scope_instead_of_current_collaborator(
    monkeypatch,
):
    seen = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"task": {"task_id": "prospectus-task-1", "status": "completed"}}

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **kwargs):
            seen["headers"] = kwargs["headers"]
            return Response()

    monkeypatch.setattr(router.httpx, "AsyncClient", Client)
    task = asyncio.run(
        router._fetch_parser_task(
            "prospectus-task-1",
            parse_run={
                "parser_owner_scope": {
                    "owner_id": "7",
                    "tenant_id": "tenant-primary",
                    "market_scope": "CN",
                    "user_role": "investment_director",
                }
            },
        )
    )

    assert task["task_id"] == "prospectus-task-1"
    assert seen["headers"]["X-SIQ-User-Id"] == "7"
    assert seen["headers"]["X-SIQ-Tenant-Id"] == "tenant-primary"
    assert seen["headers"]["X-SIQ-User-Role"] == "investment_director"


def test_prospectus_poll_without_persisted_owner_fails_closed():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            router._fetch_parser_task(
                "prospectus-task-1",
                parse_run={"parse_run_id": "PRUN-20260716-0123456789AB"},
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "pdf_parser_identity_scope_invalid"
