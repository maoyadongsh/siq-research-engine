from scripts.audit_async_sync_session import sync_session_usage


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
