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
