from pathlib import Path

import pytest

from services import agent_runtime_parse_only as parse_only
from services import agent_chat_runtime as runtime


def test_pdf2md_filename_and_alias_helpers_are_pure_service_logic():
    filename = "光环新网_CN_300383_2025年度报告.pdf"

    assert parse_only.infer_stock_code_from_text("CN_300383") == "300383"
    assert parse_only.infer_company_name_from_filename(filename) == "光环新网"
    assert parse_only.pdf2md_task_aliases(
        {
            "task_id": "task-1",
            "stock_code": "300383",
            "company_name": "光环新网",
            "filename": filename,
        }
    ) == ["task-1", "300383", "光环新网", filename, "光环新网", "CN", "300383", "2025年度报告.pdf"]


def test_pdf2md_filename_helpers_handle_complex_names_and_blank_aliases():
    filename = "宁德时代集团股份有限公司-SZ-300750-2025年度报告-修订版.pdf"

    assert parse_only.infer_stock_code_from_text("prefix SZ-300750 suffix") == "300750"
    assert parse_only.infer_company_name_from_filename(filename) == "宁德时代"
    assert parse_only.pdf2md_task_aliases(
        {
            "task_id": " task-2 ",
            "stock_code": "",
            "company_name": " ",
            "filename": filename,
        }
    ) == [
        "task-2",
        filename,
        "宁德时代集团股份有限公司",
        "SZ",
        "300750",
        "2025年度报告",
        "修订版.pdf",
    ]


def test_pdf2md_info_matches_message_uses_context_hint_and_runtime_wrapper():
    info = {
        "task_id": "task-1",
        "stock_code": "300383",
        "company_name": "光环新网",
        "filename": "光环新网_CN_300383_2025年度报告.pdf",
    }

    assert parse_only.pdf2md_info_matches_message(
        info,
        "这份年报的商誉是多少",
        {"company": {"name": "光环新网"}},
        normalize_text=runtime._normalize_financial_text,
        context_company_hint=runtime._context_company_hint,
    )
    assert runtime._pdf2md_info_matches_message(info, "请分析 300383 年报")


def test_pdf2md_info_matches_message_accepts_stock_code_alias_from_filename():
    info = {"filename": "Alpha_SH_600000_2025年度报告.pdf"}

    assert parse_only.pdf2md_info_matches_message(
        info,
        "请看一下 600000 的年报",
        normalize_text=lambda value: "".join(ch.lower() for ch in str(value) if ch.isalnum()),
        context_company_hint=lambda context: "",
    )


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


def test_pdf2md_parse_only_matches_skips_existing_wiki_before_applying_limit():
    infos = [
        {"task_id": "task-1", "company_name": "Alpha"},
        {"task_id": "task-2", "company_name": "Beta"},
    ]

    matches = parse_only._pdf2md_parse_only_matches(
        "Alpha Beta 年报",
        limit=1,
        iter_pdf2md_task_infos=lambda: infos,
        pdf2md_info_matches_message=lambda info, message, context: info["company_name"] in message,
        wiki_company_exists_for_pdf2md_info=lambda info: info["task_id"] == "task-1",
        is_general_assistant_request=lambda message: False,
        resolve_company_dir=lambda message, context: None,
    )

    assert matches == [infos[1]]


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


def test_should_consider_pdf2md_parse_only_context_short_circuits_general_and_existing_company_dir():
    calls: list[str] = []

    def matches(current_message, context=None, *, limit=None):
        calls.append(current_message)
        return [{"task_id": "task-1"}]

    assert not parse_only._should_consider_pdf2md_parse_only_context(
        "你能做什么",
        pdf2md_parse_only_matches=matches,
        is_general_assistant_request=lambda value: True,
        resolve_company_dir=lambda current_message, context: None,
        report_fulltext_fallback_terms=("全文",),
        context_company_hint=lambda context: "Alpha",
    )
    assert not parse_only._should_consider_pdf2md_parse_only_context(
        "Alpha 年报全文",
        pdf2md_parse_only_matches=matches,
        is_general_assistant_request=lambda value: False,
        resolve_company_dir=lambda current_message, context: Path("/wiki/companies/Alpha"),
        report_fulltext_fallback_terms=("全文",),
        context_company_hint=lambda context: "Alpha",
    )
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


def test_build_pdf2md_parse_only_context_returns_none_without_matches():
    def matches(message, context=None, *, limit=None):
        assert message == "Alpha 年报"
        assert limit == 2
        return []

    assert (
        parse_only.build_pdf2md_parse_only_context(
            "Alpha 年报",
            pdf2md_parse_only_matches=matches,
            parse_only_context_limit=2,
        )
        is None
    )
