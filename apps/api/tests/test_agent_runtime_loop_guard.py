from services import agent_runtime_loop_guard as guard


def test_detect_repeated_output_loop_flags_same_status_line_repetition():
    text = "\n".join(["准备上下文"] * 20 + ["继续执行报告生成"] * guard.OUTPUT_LOOP_SAME_LINE_LIMIT)

    loop = guard._detect_repeated_output_loop(text)

    assert loop == {
        "reason": "same_line_repeated",
        "sample": "继续执行报告生成",
        "repeated_lines": guard.OUTPUT_LOOP_SAME_LINE_LIMIT,
        "unique_lines": 1,
    }


def test_detect_process_trace_loop_ignores_finished_answer_sections():
    text = "\n".join(
        [
            "我需要读取这些表格的完整内容。",
            "让我用 Python 读取这些表格的完整内容。",
        ]
        * 4
        + [
            "# 结论",
            "该公司现金流质量改善。",
            "# 引用来源",
            "[D1] source_type=wiki_metrics",
        ]
    )

    assert guard._detect_process_trace_loop(text) is None


def test_sanitize_assistant_history_reply_replaces_loop_text_without_long_original_context():
    text = "\n".join(
        f"让我读取第{page}页关于资产减值的内容，以及检索商誉相关附注。"
        for page in range(21, 37)
    )

    sanitized = guard._sanitize_assistant_history_reply(text)

    assert guard.HISTORY_LOOP_SANITIZED_MESSAGE in sanitized
    assert "循环类型：linear_page_scan_loop" in sanitized
    assert "第21页" not in sanitized
    assert "第22页" not in sanitized


def test_assistant_display_and_failed_history_replies_hide_polluted_output():
    polluted = "\n".join(
        [
            "我需要读取这些表格的完整内容。",
            "让我用 Python 读取这些表格的完整内容。",
        ]
        * 7
    )

    assert guard._assistant_reply_for_display(polluted) == guard.OUTPUT_LOOP_STOP_MESSAGE
    assert guard._failed_run_reply_for_history(polluted) == guard.OUTPUT_LOOP_STOP_MESSAGE
    assert guard._assistant_reply_for_display("正常回答") == "正常回答"
    assert guard._failed_run_reply_for_history("") == guard.RUN_FAILED_MESSAGE


def test_external_tool_loop_guard_replies_are_polluted_and_sanitized():
    replies = (
        (
            "I stopped retrying terminal because it hit the tool-call guardrail "
            "(same_tool_failure_halt) after 5 repeated non-progressing attempts."
        ),
        "[Tool loop hard stop: repeated tool calls made no progress]",
    )

    for reply in replies:
        assert guard._is_loop_polluted_assistant_message(reply) is True

        sanitized = guard._sanitize_assistant_history_reply(reply)
        assert sanitized == guard.HISTORY_LOOP_SANITIZED_MESSAGE
        assert reply not in sanitized
        assert "same_tool_failure_halt" not in sanitized
        assert "I stopped retrying terminal" not in sanitized
        assert "[Tool loop hard stop:" not in sanitized

        assert guard._assistant_reply_for_display(reply) == guard.OUTPUT_LOOP_STOP_MESSAGE
        assert guard._failed_run_reply_for_history(reply) == guard.OUTPUT_LOOP_STOP_MESSAGE


def test_external_tool_loop_stream_filter_holds_split_markers_without_delaying_normal_text():
    normal = guard.ExternalToolLoopStreamFilter()
    assert normal.feed("正常回答") == "正常回答"
    assert normal.feed("，继续。") == "，继续。"
    assert normal.feed("", final=True) == ""
    assert normal.blocked is False

    guarded = guard.ExternalToolLoopStreamFilter()
    assert guarded.feed("I stopped retrying ") == ""
    assert guarded.feed("terminal because the tool loop stopped.") == ""
    assert guarded.blocked is True

    late_marker = guard.ExternalToolLoopStreamFilter()
    assert late_marker.feed("已验证内容。") == "已验证内容。"
    assert late_marker.feed("same_tool_") == ""
    assert late_marker.feed("failure_halt") == ""
    assert late_marker.blocked is True


def test_assistant_history_strips_backend_guardrail_diagnostic():
    content = (
        "结论可由原始数据支持。\n\n"
        "## 计算校验无效\n"
        "- 后端检测到 trace 无效。\n\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_calculation_trace_missing"
    )

    sanitized = guard._sanitize_assistant_history_reply(content)

    assert sanitized == "结论可由原始数据支持。"
    assert "计算校验无效" not in sanitized
    assert "guardrail_status" not in sanitized


def test_assistant_display_collapses_duplicate_legacy_guardrail_diagnostic():
    diagnostic = (
        "## 计算校验无效\n"
        "- 后端检测到 trace 无效。\n\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_calculation_trace_missing"
    )
    persisted = f"结论可由原始数据支持。\n\n{diagnostic}\n\n{diagnostic}"

    displayed = guard._assistant_reply_for_display(persisted)

    assert displayed.count("## 计算校验无效") == 1
    assert displayed.count("guardrail_status=warning") == 1


def test_guardrail_sanitizer_ignores_markdown_headings_inside_code_fences():
    content = (
        "下面是文档示例：\n\n"
        "```markdown\n"
        "## 计算校验无效\n"
        "guardrail_status=warning\n"
        "```\n\n"
        "## 计算校验提示\n"
        "- 真实后端提示。"
    )

    sanitized = guard.strip_guardrail_diagnostics(content)

    assert "```markdown\n## 计算校验无效" in sanitized
    assert "## 计算校验提示" not in sanitized
    assert "真实后端提示" not in sanitized
