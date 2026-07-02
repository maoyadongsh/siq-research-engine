"""Pure artifact orchestration for completed MinerU PDF parser results."""

from __future__ import annotations

from task_store import COMPLETED, missing_artifact_message


def select_markdown_result(upstream_response):
    if not isinstance(upstream_response, dict):
        return None, None, None
    results = upstream_response.get("results")
    if not isinstance(results, dict):
        return None, None, None
    for file_name, file_data in results.items():
        if isinstance(file_data, dict) and "md_content" in file_data:
            return file_data["md_content"], file_name, file_data
    return None, None, None


def cache_mineru_result_artifacts(
    task,
    upstream_response,
    *,
    local_markdown=None,
    task_requires_markdown_artifact,
    mark_completed_missing_artifact,
    inject_pdf_page_markers,
    backfill_sparse_markdown_pages,
    write_markdown,
    save_mineru_artifacts,
    append_log,
    now_iso,
    persist_task,
):
    markdown, selected_file_name, selected_file_data = select_markdown_result(upstream_response)
    if markdown is None:
        if task_requires_markdown_artifact(task) and local_markdown is None:
            detail = "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。"
            mark_completed_missing_artifact(task, detail)
            return {"_error": True, "detail": detail}
        return None

    content_list = selected_file_data.get("content_list") if isinstance(selected_file_data, dict) else None
    markdown = inject_pdf_page_markers(
        markdown,
        content_list,
        total_pages=task.get("pdf_page_count"),
    )
    markdown, restored_pages = backfill_sparse_markdown_pages(markdown, content_list)
    write_markdown(task, markdown)
    if selected_file_data is not None:
        quality_report = save_mineru_artifacts(
            task,
            upstream_response,
            selected_file_name,
            selected_file_data,
            markdown,
        )
        append_log(
            task,
            f"质量报告已生成: {quality_report['table_count']} 个表格, {quality_report['single_row_table_count']} 个单行/空壳表",
            "info",
        )
    append_log(task, f"Markdown 结果已获取 ({len(markdown)} 字符)", "success")
    if restored_pages:
        append_log(task, f"已从 content_list 回填 {len(restored_pages)} 个稀疏 Markdown 页", "info")
    task["status"] = COMPLETED
    task["stage"] = COMPLETED
    task["error"] = None
    task["completed_at"] = task.get("completed_at") or now_iso()
    persist_task(task)
    return markdown


def missing_local_markdown_error(task, response, *, mark_completed_missing_artifact):
    detail = response.get("detail", "Failed to fetch result")
    if response.get("status") == 404:
        detail = "任务已完成，但本地 Markdown 结果不存在，且上游 MinerU 结果已不可拉取。"
    mark_completed_missing_artifact(task, detail)
    return {"_error": True, "detail": detail}


def completed_missing_local_markdown(task, *, mark_completed_missing_artifact):
    mark_completed_missing_artifact(task)
    return {"_error": True, "detail": task.get("error") or missing_artifact_message()}
