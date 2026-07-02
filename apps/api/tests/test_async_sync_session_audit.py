import json

from scripts.audit_async_sync_session import (
    advisory_buckets,
    finding_summary,
    iter_sync_session_findings,
    main,
    sync_session_usage,
)


EXPECTED_SUMMARY = {
    "total": 56,
    "by_kind": {
        "depends_get_session": 54,
        "next_get_session": 2,
    },
    "by_path": {
        "routers/agent_user_router.py": 2,
        "routers/chat.py": 5,
        "routers/document_parser.py": 25,
        "routers/market_reports.py": 1,
        "routers/source.py": 5,
        "routers/workspace.py": 18,
    },
}

EXPECTED_BUCKETS = [
    {
        "priority": "P0",
        "path": "routers/chat.py",
        "total": 5,
        "depends_get_session": 3,
        "next_get_session": 2,
    },
    {
        "priority": "P1",
        "path": "routers/document_parser.py",
        "total": 25,
        "depends_get_session": 25,
        "next_get_session": 0,
    },
    {
        "priority": "P1",
        "path": "routers/workspace.py",
        "total": 18,
        "depends_get_session": 18,
        "next_get_session": 0,
    },
    {
        "priority": "P2",
        "path": "routers/source.py",
        "total": 5,
        "depends_get_session": 5,
        "next_get_session": 0,
    },
    {
        "priority": "P3",
        "path": "routers/agent_user_router.py",
        "total": 2,
        "depends_get_session": 2,
        "next_get_session": 0,
    },
    {
        "priority": "P3",
        "path": "routers/market_reports.py",
        "total": 1,
        "depends_get_session": 1,
        "next_get_session": 0,
    },
]


ALLOWED_SYNC_SESSION_USAGE = {
    "routers/agent_user_router.py::create_specialist_agent_router.chat::param sync_session: Session = Depends(get_session)",
    "routers/agent_user_router.py::create_specialist_agent_router.chat_stream::param sync_session: Session = Depends(get_session)",
    "routers/chat.py::upload_chat_attachments::param session: Session = Depends(get_session)",
    "routers/chat.py::chat::param sync_session: Session = Depends(get_session)",
    "routers/chat.py::chat::body next(get_session())",
    "routers/chat.py::chat_stream::param sync_session: Session = Depends(get_session)",
    "routers/chat.py::chat_stream.done_payload::body next(get_session())",
    "routers/document_parser.py::create_document_tasks::param session: Session = Depends(get_session)",
    "routers/document_parser.py::import_document_from_mineru::param session: Session = Depends(get_session)",
    "routers/document_parser.py::list_document_tasks::param session: Session = Depends(get_session)",
    "routers/document_parser.py::get_document_task::param session: Session = Depends(get_session)",
    "routers/document_parser.py::get_document_status::param session: Session = Depends(get_session)",
    "routers/document_parser.py::get_document_result::param session: Session = Depends(get_session)",
    "routers/document_parser.py::cancel_document_task::param session: Session = Depends(get_session)",
    "routers/document_parser.py::retry_document_task::param session: Session = Depends(get_session)",
    "routers/document_parser.py::delete_document_task::param session: Session = Depends(get_session)",
    "routers/document_parser.py::get_document_artifact::param session: Session = Depends(get_session)",
    "routers/document_parser.py::download_document_package::param session: Session = Depends(get_session)",
    "routers/document_parser.py::download_document_batch::param session: Session = Depends(get_session)",
    "routers/document_parser.py::source_page::param session: Session = Depends(get_session)",
    "routers/document_parser.py::source_page_image::param session: Session = Depends(get_session)",
    "routers/document_parser.py::source_block::param session: Session = Depends(get_session)",
    "routers/document_parser.py::source_table::param session: Session = Depends(get_session)",
    "routers/document_parser.py::source_image::param session: Session = Depends(get_session)",
    "routers/document_parser.py::document_figures::param session: Session = Depends(get_session)",
    "routers/document_parser.py::document_figure::param session: Session = Depends(get_session)",
    "routers/document_parser.py::document_table_relations::param session: Session = Depends(get_session)",
    "routers/document_parser.py::review_document_table_relation::param session: Session = Depends(get_session)",
    "routers/document_parser.py::split_document_logical_table::param session: Session = Depends(get_session)",
    "routers/document_parser.py::merge_document_logical_tables::param session: Session = Depends(get_session)",
    "routers/document_parser.py::extract_document_schema::param session: Session = Depends(get_session)",
    "routers/document_parser.py::get_document_extraction::param session: Session = Depends(get_session)",
    "routers/market_reports.py::us_sec_upload_files::param session: Session = Depends(get_session)",
    "routers/source.py::get_source_open_url::param session: Session = Depends(get_session)",
    "routers/source.py::get_source_table::param session: Session = Depends(get_session)",
    "routers/source.py::get_source_page::param session: Session = Depends(get_session)",
    "routers/source.py::get_pdf_page::param session: Session = Depends(get_session)",
    "routers/source.py::submit_source_table_correction::param session: Session = Depends(get_session)",
    "routers/workspace.py::authenticated_pdf_upload::param session: Session = Depends(get_session)",
    "routers/workspace.py::list_my_pdf_tasks::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_status::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_result::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_quality::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_financial::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_cancel::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_refetch::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_reparse::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_artifact::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_download::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_download_complete::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_download_corrected::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_source_table::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_source_page::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_source_correction::param session: Session = Depends(get_session)",
    "routers/workspace.py::pdf_task_page_image::param session: Session = Depends(get_session)",
    "routers/workspace.py::delete_my_pdf_task::param session: Session = Depends(get_session)",
}


def test_async_routes_do_not_add_new_sync_session_usage():
    findings = sync_session_usage()
    unexpected = findings - ALLOWED_SYNC_SESSION_USAGE
    stale = ALLOWED_SYNC_SESSION_USAGE - findings

    assert not unexpected, "Unexpected sync Session usage in async routes:\n" + "\n".join(
        sorted(unexpected)
    )
    assert not stale, "Stale sync Session allowlist entries:\n" + "\n".join(sorted(stale))


def test_async_sync_session_audit_summary_and_buckets_are_stable():
    findings = iter_sync_session_findings()

    assert finding_summary(findings) == EXPECTED_SUMMARY
    assert [
        {
            "priority": bucket["priority"],
            "path": bucket["path"],
            "total": bucket["total"],
            "depends_get_session": bucket["depends_get_session"],
            "next_get_session": bucket["next_get_session"],
        }
        for bucket in advisory_buckets(findings)
    ] == EXPECTED_BUCKETS


def test_async_sync_session_audit_scans_nested_async_functions(tmp_path):
    routers_dir = tmp_path / "routers"
    services_dir = tmp_path / "services"
    routers_dir.mkdir()
    services_dir.mkdir()
    (services_dir / "auth_dependencies.py").write_text("async def clean():\n    return None\n", encoding="utf-8")
    (routers_dir / "demo.py").write_text(
        """
from fastapi import Depends
from sqlmodel import Session
from database import get_session


def create_router():
    async def endpoint(session: Session = Depends(get_session)):
        async def done_payload():
            return next(get_session())
        return session
""",
        encoding="utf-8",
    )

    findings = iter_sync_session_findings(tmp_path)

    assert [finding.key for finding in findings] == [
        "routers/demo.py::create_router.endpoint::param session: Session = Depends(get_session)",
        "routers/demo.py::create_router.endpoint.done_payload::body next(get_session())",
    ]
    assert finding_summary(findings) == {
        "total": 2,
        "by_kind": {"depends_get_session": 1, "next_get_session": 1},
        "by_path": {"routers/demo.py": 2},
    }


def test_async_sync_session_audit_json_summary_omits_findings_when_requested(capsys):
    assert main(["--json", "--summary"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["summary"] == EXPECTED_SUMMARY
    assert "findings" not in payload
    assert payload["advisory"]["buckets"][0]["priority"] == "P0"
    assert payload["advisory"]["buckets"][0]["path"] == "routers/chat.py"
