import pdf_parser_artifact_orchestrator_service as orchestrator
from task_store import COMPLETED, COMPLETED_MISSING_ARTIFACT


def test_cache_mineru_result_artifacts_writes_markdown_artifacts_and_completes_task():
    task = {
        "task_id": "orchestrated",
        "filename": "report.pdf",
        "pdf_page_count": 3,
        "status": "pending",
        "stage": "submitted",
        "completed_at": None,
        "error": "old error",
    }
    response = {
        "backend": "mineru",
        "results": {
            "report.md": {
                "md_content": "# 标题\n正文\n",
                "content_list": [{"type": "text", "page_idx": 0, "text": "标题"}],
            }
        },
    }
    calls = []

    def inject(markdown, content_list, total_pages=None):
        calls.append(("inject", markdown, content_list, total_pages))
        return "[PDF_PAGE: 1]\n" + markdown

    def backfill(markdown, content_list):
        calls.append(("backfill", markdown, content_list))
        return markdown + "\n[PDF_PAGE: 3]\n", [3]

    def write_markdown(value, markdown):
        calls.append(("write_markdown", value["task_id"], markdown))

    def save_mineru_artifacts(value, upstream_response, file_name, file_data, markdown):
        calls.append(("save_mineru_artifacts", value["task_id"], upstream_response, file_name, file_data, markdown))
        return {"table_count": 2, "single_row_table_count": 1}

    def append_log(value, message, level="info"):
        calls.append(("log", value["task_id"], message, level))

    def persist(value):
        calls.append(("persist", value.copy()))

    result = orchestrator.cache_mineru_result_artifacts(
        task,
        response,
        task_requires_markdown_artifact=lambda _task: True,
        mark_completed_missing_artifact=lambda *_args, **_kwargs: None,
        inject_pdf_page_markers=inject,
        backfill_sparse_markdown_pages=backfill,
        write_markdown=write_markdown,
        save_mineru_artifacts=save_mineru_artifacts,
        append_log=append_log,
        now_iso=lambda: "2026-05-01T00:01:00Z",
        persist_task=persist,
    )

    assert result == "[PDF_PAGE: 1]\n# 标题\n正文\n\n[PDF_PAGE: 3]\n"
    assert task["status"] == COMPLETED
    assert task["stage"] == COMPLETED
    assert task["error"] is None
    assert task["completed_at"] == "2026-05-01T00:01:00Z"
    assert calls[0] == ("inject", "# 标题\n正文\n", response["results"]["report.md"]["content_list"], 3)
    assert calls[2][0] == "write_markdown"
    assert calls[3][0] == "save_mineru_artifacts"
    assert calls[3][3] == "report.md"
    assert ("log", "orchestrated", "质量报告已生成: 2 个表格, 1 个单行/空壳表", "info") in calls
    assert ("log", "orchestrated", f"Markdown 结果已获取 ({len(result)} 字符)", "success") in calls
    assert ("log", "orchestrated", "已从 content_list 回填 1 个稀疏 Markdown 页", "info") in calls
    assert calls[-1][0] == "persist"


def test_cache_mineru_result_artifacts_missing_markdown_marks_completed_missing_artifact():
    task = {"task_id": "missing-md", "status": COMPLETED}
    marked = []

    result = orchestrator.cache_mineru_result_artifacts(
        task,
        {"results": {"report.md": {"content_list": []}}},
        local_markdown=None,
        task_requires_markdown_artifact=lambda _task: True,
        mark_completed_missing_artifact=lambda value, detail=None: marked.append((value["task_id"], detail)),
        inject_pdf_page_markers=lambda *_args, **_kwargs: "",
        backfill_sparse_markdown_pages=lambda markdown, _content_list: (markdown, []),
        write_markdown=lambda *_args, **_kwargs: None,
        save_mineru_artifacts=lambda *_args, **_kwargs: {},
        append_log=lambda *_args, **_kwargs: None,
        now_iso=lambda: "2026-05-01T00:01:00Z",
        persist_task=lambda _task: None,
    )

    assert result == {"_error": True, "detail": "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。"}
    assert marked == [("missing-md", "任务已完成，但 MinerU 结果中没有可用的 Markdown 内容。")]


def test_missing_local_markdown_error_uses_specific_404_message():
    task = {"task_id": "gone"}
    marked = []

    result = orchestrator.missing_local_markdown_error(
        task,
        {"_error": True, "status": 404, "detail": "not found"},
        mark_completed_missing_artifact=lambda value, detail=None: marked.append((value["task_id"], detail)),
    )

    assert result == {
        "_error": True,
        "detail": "任务已完成，但本地 Markdown 结果不存在，且上游 MinerU 结果已不可拉取。",
    }
    assert marked == [("gone", "任务已完成，但本地 Markdown 结果不存在，且上游 MinerU 结果已不可拉取。")]


def test_completed_missing_local_markdown_returns_task_error_after_transition():
    task = {"task_id": "local-missing", "status": COMPLETED, "error": None}

    def mark_completed_missing_artifact(value):
        value["status"] = COMPLETED_MISSING_ARTIFACT
        value["error"] = "missing markdown"

    result = orchestrator.completed_missing_local_markdown(
        task,
        mark_completed_missing_artifact=mark_completed_missing_artifact,
    )

    assert task["status"] == COMPLETED_MISSING_ARTIFACT
    assert result == {"_error": True, "detail": "missing markdown"}
