from pathlib import Path

import pytest

from services import agent_runtime_parse_only as parse_only


def test_pdf2md_parse_only_matches_filters_general_wiki_and_limit():
    infos = [
        {"task_id": "task-1", "company_name": "Alpha"},
        {"task_id": "task-2", "company_name": "Beta"},
        {"task_id": "task-3", "company_name": "Gamma"},
    ]

    matches = parse_only._pdf2md_parse_only_matches(
        "Alpha Beta Gamma 年报",
        limit=1,
        iter_pdf2md_task_infos=lambda: infos,
        pdf2md_info_matches_message=lambda info, message, context: info["company_name"] in message,
        wiki_company_exists_for_pdf2md_info=lambda info: info["task_id"] == "task-2",
        is_general_assistant_request=lambda message: False,
        resolve_company_dir=lambda message, context: None,
    )

    assert matches == [infos[0]]

    assert (
        parse_only._pdf2md_parse_only_matches(
            "你能做什么",
            iter_pdf2md_task_infos=lambda: infos,
            pdf2md_info_matches_message=lambda info, message, context: True,
            wiki_company_exists_for_pdf2md_info=lambda info: False,
            is_general_assistant_request=lambda message: True,
            resolve_company_dir=lambda message, context: None,
        )
        == []
    )
    assert (
        parse_only._pdf2md_parse_only_matches(
            "Alpha 年报",
            iter_pdf2md_task_infos=lambda: infos,
            pdf2md_info_matches_message=lambda info, message, context: True,
            wiki_company_exists_for_pdf2md_info=lambda info: False,
            is_general_assistant_request=lambda message: False,
            resolve_company_dir=lambda message, context: Path("/wiki/companies/Alpha"),
        )
        == []
    )


@pytest.mark.parametrize("message,context_hint,expected", [("Alpha 年报全文", "", True), ("看看这家公司", "Alpha", True), ("随便聊聊", "", False)])
def test_should_consider_pdf2md_parse_only_context(message, context_hint, expected):
    calls: list[tuple[str, int | None]] = []

    def matches(current_message, context=None, *, limit=None):
        calls.append((current_message, limit))
        return [{"task_id": "task-1"}]

    result = parse_only._should_consider_pdf2md_parse_only_context(
        message,
        {"company": {"name": context_hint}} if context_hint else None,
        pdf2md_parse_only_matches=matches,
        is_general_assistant_request=lambda value: False,
        resolve_company_dir=lambda current_message, context: None,
        report_fulltext_fallback_terms=("全文",),
        context_company_hint=lambda context: context_hint,
    )

    assert result is expected
    if expected:
        assert calls == [(message, 1)]
    else:
        assert calls == []


def test_build_pdf2md_parse_only_context_formats_real_artifact_paths(tmp_path):
    result_dir = tmp_path / "task-1"
    result_md = result_dir / "result.md"

    def matches(message, context=None, *, limit=None):
        assert limit == 2
        return [
            {
                "task_id": "task-1",
                "stock_code": "123456",
                "company_name": "Alpha",
                "filename": "Alpha_CN_123456_2025.pdf",
                "result_dir": result_dir,
                "result_md": result_md,
            }
        ]

    context = parse_only.build_pdf2md_parse_only_context(
        "Alpha 年报",
        pdf2md_parse_only_matches=matches,
        parse_only_context_limit=2,
    )

    assert context is not None
    assert "只匹配到 PDF parser results 解析产物" in context
    assert "source_type=pdf2md_parse_result" in context
    assert "task-1" in context
    assert str(result_dir) in context
    assert str(result_md) in context
