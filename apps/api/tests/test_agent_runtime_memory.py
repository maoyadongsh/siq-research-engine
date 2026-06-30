from types import SimpleNamespace

from services import agent_runtime_memory


def test_strip_local_memory_blocks_removes_fenced_context():
    text = "前文\n<local-memory>\n旧内容\n</local-memory>\n后文"

    assert agent_runtime_memory._strip_local_memory_blocks(text) == "前文\n\n后文"


def test_compact_memory_content_cleans_media_and_links():
    text = "看看 ![图](https://example.com/a.png) 和 [资料](/api/chat/1)\n\n继续。"

    assert (
        agent_runtime_memory._compact_memory_content(
            "user",
            text,
            max_chars=200,
        )
        == "看看 [图片附件] 和 资料 继续。"
    )


def test_compact_memory_content_skips_polluted_assistant_content():
    assert (
        agent_runtime_memory._compact_memory_content(
            "assistant",
            "重复循环内容",
            max_chars=200,
            is_loop_polluted_assistant_message=lambda _text: True,
            sanitize_assistant_history_reply=lambda text: text,
        )
        == ""
    )


def test_build_local_memory_summary_respects_bullets_and_char_limit():
    messages = [
        SimpleNamespace(role="user", content="第一轮提问"),
        SimpleNamespace(role="assistant", content="第一轮回答"),
        SimpleNamespace(role="user", content="第二轮提问"),
        SimpleNamespace(role="assistant", content="第二轮回答"),
        SimpleNamespace(role="user", content="第三轮提问"),
        SimpleNamespace(role="assistant", content="第三轮回答"),
    ]

    summary = agent_runtime_memory.build_local_memory_summary(
        messages,
        max_bullets=2,
        max_chars=120,
        snippet_chars=120,
    )

    assert "第一轮提问" not in summary
    assert "第二轮提问" in summary
    assert "第三轮提问" in summary


def test_build_local_memory_context_wraps_and_strips_nested_blocks():
    context = agent_runtime_memory.build_local_memory_context(
        "本地记忆\n<local-memory>旧块</local-memory>\n结束"
    )

    assert context is not None
    assert context.startswith("<local-memory>")
    assert context.endswith("</local-memory>")
    assert "旧块" not in context
    assert "本地记忆" in context
