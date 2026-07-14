from __future__ import annotations

import hashlib
import json

import anyio
import httpx
import pytest
from fastapi import HTTPException
from services.pdf_submission import (
    PDFSubmissionHooks,
    normalize_pdf_parse_config,
    pdf_parse_config_hash,
    submit_pdf_parse,
)


class FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str = "application/pdf") -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._read = False

    async def read(self, _size: int = -1) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._content


class FakeResponse:
    def __init__(self, status_code: int, payload: object | None, *, content: bytes = b"") -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content or (json.dumps(payload).encode() if payload is not None else b"")
        self.headers = {"content-type": "application/json" if payload is not None else "text/plain"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def client_factory(response: FakeResponse, calls: list[dict[str, object]]):
    class FakeClient:
        def __init__(self, timeout=None):
            calls.append({"timeout": timeout})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data=None, files=None, headers=None):
            calls[-1].update({"url": url, "data": data, "files": files, "headers": headers})
            return response

    return FakeClient


def test_profile_enters_hash_and_controlled_context_is_forwarded():
    base = normalize_pdf_parse_config({"market": "cn"})
    prospectus = normalize_pdf_parse_config(
        {"market": "cn"},
        document_profile="cn_a_share_prospectus",
        source_context={
            "domain": "primary_market",
            "deal_id": "DEAL-1",
            "document_id": "DOC-1",
            "source_type": "primary_market_prospectus",
        },
    )

    assert pdf_parse_config_hash(base, parser_version="pdf_parser_v1") != pdf_parse_config_hash(
        prospectus, parser_version="pdf_parser_v1"
    )
    assert prospectus.requested_market == "CN"
    assert prospectus.parser_form()["document_profile"] == "cn_a_share_prospectus"
    assert json.loads(prospectus.parser_form()["source_context"])["document_id"] == "DOC-1"

    with pytest.raises(HTTPException) as exc:
        normalize_pdf_parse_config({}, source_context={"artifact_path": "/tmp/private.pdf"})
    assert exc.value.status_code == 400


def test_oversized_and_empty_uploads_never_reserve_or_call_parser():
    calls: list[str] = []

    async def reserve(_count: int):
        calls.append("reserve")

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("parser must not be called")

    async def run_case():
        with pytest.raises(HTTPException) as oversized:
            await submit_pdf_parse(
                files=[FakeUpload("large.pdf", b"123456")],
                parser_api_base="http://parser.test",
                max_file_bytes=5,
                hooks=PDFSubmissionHooks(reserve_quota=reserve),
                http_client_factory=ForbiddenClient,
                limiter=None,
            )
        assert oversized.value.status_code == 413

        with pytest.raises(HTTPException) as empty:
            await submit_pdf_parse(
                files=[FakeUpload("empty.pdf", b"")],
                parser_api_base="http://parser.test",
                hooks=PDFSubmissionHooks(reserve_quota=reserve),
                http_client_factory=ForbiddenClient,
                limiter=None,
            )
        assert empty.value.status_code == 400

    anyio.run(run_case)
    assert calls == []


def test_success_classifies_new_and_reused_tasks_and_preserves_artifact_semantics():
    old_content = b"%PDF-1.4 old"
    new_content = b"%PDF-1.4 new"
    config = normalize_pdf_parse_config({"market": "CN"})
    config_hash = pdf_parse_config_hash(config, parser_version="test-v1")
    old_task = {
        "task_id": "old-task",
        "filename": "old.pdf",
        "file_sha256": hashlib.sha256(old_content).hexdigest(),
        "parse_config_hash": config_hash,
        "market": "CN",
        "status": "completed",
    }
    response = FakeResponse(
        200,
        {
            "tasks": [
                old_task,
                {
                    "task_id": "new-task",
                    "filename": "new.pdf",
                    "file_sha256": hashlib.sha256(new_content).hexdigest(),
                    "parse_config_hash": config_hash,
                },
            ]
        },
    )
    events: list[object] = []
    http_calls: list[dict[str, object]] = []

    async def lookup():
        return {"old.pdf": old_task}

    async def reserve(count: int):
        events.append(("reserve", count))

    async def usage(tasks):
        events.append(("usage", [task["task_id"] for task in tasks]))

    async def record(task, source):
        events.append(("artifact", task["task_id"], source))

    async def has_artifact(task_id: str):
        events.append(("has", task_id))
        return False

    async def run_case():
        result = await submit_pdf_parse(
            files=[FakeUpload("old.pdf", old_content), FakeUpload("new.pdf", new_content)],
            parser_api_base="http://parser.test/",
            config=config,
            parser_version="test-v1",
            headers={"X-SIQ-User-Id": "7"},
            hooks=PDFSubmissionHooks(
                lookup_tasks=lookup,
                reserve_quota=reserve,
                record_usage=usage,
                record_artifact=record,
                has_artifact=has_artifact,
            ),
            http_client_factory=client_factory(response, http_calls),
            limiter=None,
        )
        assert [task["task_id"] for task in result.new_tasks] == ["new-task"]
        assert [task["task_id"] for task in result.reused_tasks] == ["old-task"]
        assert result.payload["tasks"][1]["market"] == "CN"

    anyio.run(run_case)

    assert events == [
        ("reserve", 1),
        ("usage", ["new-task"]),
        ("artifact", "new-task", "new_parse"),
        ("has", "old-task"),
        ("artifact", "old-task", "reused_parse"),
    ]
    assert http_calls[0]["url"] == "http://parser.test/api/upload"
    assert http_calls[0]["headers"] == {"X-SIQ-User-Id": "7"}


def test_duplicate_content_in_one_batch_short_circuits_before_quota_and_parser():
    events: list[object] = []

    async def lookup():
        events.append("lookup")
        return {}

    async def reserve(count: int):
        events.append(("reserve", count))

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("parser must not be called for duplicate batch content")

    async def run_case():
        result = await submit_pdf_parse(
            files=[
                FakeUpload("first.pdf", b"same-pdf"),
                FakeUpload("renamed.pdf", b"same-pdf"),
            ],
            parser_api_base="http://parser.test",
            hooks=PDFSubmissionHooks(lookup_tasks=lookup, reserve_quota=reserve),
            http_client_factory=ForbiddenClient,
            limiter=None,
        )
        assert result.status_code == 409
        assert result.payload["error"] == "duplicate_file_content"
        assert result.payload["filename"] == "renamed.pdf"

    anyio.run(run_case)
    assert events == []


def test_duplicate_response_records_reused_artifact_and_releases_reserved_quota():
    content = b"%PDF-1.4 duplicate"
    response = FakeResponse(
        409,
        {
            "error": "duplicate_file_content",
            "filename": "renamed.pdf",
            "existingTask": {"task_id": "shared-task", "filename": "original.pdf"},
        },
    )
    events: list[object] = []

    async def reserve(count: int):
        events.append(("reserve", count))

    async def release():
        events.append("release")

    async def record(task, source):
        events.append(("artifact", task["task_id"], task["market"], source))

    async def run_case():
        result = await submit_pdf_parse(
            files=[FakeUpload("renamed.pdf", content)],
            parser_api_base="http://parser.test",
            hooks=PDFSubmissionHooks(
                reserve_quota=reserve,
                release_quota=release,
                record_artifact=record,
            ),
            http_client_factory=client_factory(response, []),
            limiter=None,
        )
        assert result.status_code == 409
        assert result.payload["existingTask"]["market"] == "CN"

    anyio.run(run_case)
    assert events == [
        ("reserve", 1),
        ("artifact", "shared-task", "CN", "reused_parse"),
        "release",
    ]


def test_parser_network_and_http_failures_release_quota():
    events: list[object] = []

    async def reserve(count: int):
        events.append(("reserve", count))

    async def release():
        events.append("release")

    hooks = PDFSubmissionHooks(reserve_quota=reserve, release_quota=release)

    class NetworkFailureClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("down", request=httpx.Request("POST", url))

    async def run_case():
        with pytest.raises(HTTPException) as exc:
            await submit_pdf_parse(
                files=[FakeUpload("one.pdf", b"%PDF")],
                parser_api_base="http://parser.test",
                hooks=hooks,
                http_client_factory=NetworkFailureClient,
                limiter=None,
            )
        assert exc.value.status_code == 502

        result = await submit_pdf_parse(
            files=[FakeUpload("two.pdf", b"%PDF-2")],
            parser_api_base="http://parser.test",
            hooks=hooks,
            http_client_factory=client_factory(FakeResponse(500, {"error": "failed"}), []),
            limiter=None,
        )
        assert result.status_code == 500

    anyio.run(run_case)
    assert events == [("reserve", 1), "release", ("reserve", 1), "release"]


def test_non_json_parser_response_is_preserved_and_releases_quota():
    events: list[object] = []

    async def reserve(count: int):
        events.append(("reserve", count))

    async def release():
        events.append("release")

    async def run_case():
        result = await submit_pdf_parse(
            files=[FakeUpload("one.pdf", b"%PDF")],
            parser_api_base="http://parser.test",
            hooks=PDFSubmissionHooks(reserve_quota=reserve, release_quota=release),
            http_client_factory=client_factory(
                FakeResponse(502, None, content=b"bad gateway"),
                [],
            ),
            limiter=None,
        )
        assert result.status_code == 502
        assert result.payload is None
        assert result.content == b"bad gateway"
        assert result.content_type == "text/plain"

    anyio.run(run_case)
    assert events == [("reserve", 1), "release"]
