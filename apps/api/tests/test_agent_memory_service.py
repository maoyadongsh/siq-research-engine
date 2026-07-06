from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from services import agent_memory_service as memory


class _Dialect:
    name = "sqlite"


class _Bind:
    dialect = _Dialect()


class _Session:
    bind = _Bind()


def test_context_from_session_id_parses_user_and_normalizes_profile():
    context = memory.context_from_session_id(
        "user-42-assistant-123e4567-e89b-12d3-a456-426614174000"
    )

    assert context is not None
    assert context.user_id == 42
    assert context.profile == "siq_assistant"
    assert context.agent_group == "secondary_market"
    assert context.visibility == "user_private"


def test_context_from_session_id_marks_ic_profiles_project_shared():
    context = memory.context_from_session_id(
        "user-7-siq_ic_chairman-123e4567-e89b-12d3-a456-426614174000",
        deal_id="deal-alpha",
    )

    assert context is not None
    assert context.agent_group == "primary_market"
    assert context.visibility == "project_shared"


def test_memory_enabled_skips_sqlite_by_default(monkeypatch):
    monkeypatch.delenv("SIQ_AGENT_MEMORY_ALLOW_SQLITE", raising=False)
    monkeypatch.delenv("SIQ_AGENT_MEMORY_ENABLED", raising=False)

    assert memory.memory_enabled(_Session()) is False


def test_memory_enabled_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_ENABLED", "false")

    assert memory.memory_enabled(SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))) is False


def test_extract_explicit_memory_text_requires_clear_trigger():
    assert memory.extract_explicit_memory_text("请记住：我默认关注现金流和商誉") == "我默认关注现金流和商誉"
    assert memory.extract_explicit_memory_text("普通提问：分析一下上汽集团") is None


def test_classify_explicit_memory_type_detects_corrections_and_project_facts():
    assert (
        memory.classify_explicit_memory_type("请记住：你之前说错了，A 公司不是港股", agent_group="secondary_market")
        == "correction"
    )
    assert (
        memory.classify_explicit_memory_type("请记住：本项目红线是数据合规", agent_group="primary_market")
        == "project_fact"
    )
    assert (
        memory.classify_explicit_memory_type("请记住：我默认关注现金流", agent_group="secondary_market")
        == "user_preference"
    )


def test_build_memory_context_block_renders_traceable_items():
    block = memory.build_memory_context_block(
        [
            {
                "id": 10,
                "visibility": "user_private",
                "memory_type": "user_preference",
                "title": "偏好",
                "content": "默认关注现金流。",
                "source_type": "chat_message",
                "source_id": "5",
                "score": 0.88,
            }
        ]
    )

    assert block is not None
    assert block.startswith("<memory-context>")
    assert "source=chat_message:5" in block
    assert "默认关注现金流" in block


def test_memory_recency_weight_decays_old_memory_and_full_recall_bypasses(monkeypatch):
    monkeypatch.setenv("SIQ_AGENT_MEMORY_TIME_DECAY_HALF_LIFE_DAYS", "30")
    old_time = datetime.now(timezone.utc) - timedelta(days=30)

    decayed = memory.memory_recency_weight(old_time, source_type="chat_message", query="普通问题")
    bypassed = memory.memory_recency_weight(old_time, source_type="chat_message", query="请全量检索所有记忆")
    profile_file = memory.memory_recency_weight(old_time, source_type="profile_file", query="普通问题")

    assert 0.49 <= decayed <= 0.51
    assert bypassed == 1.0
    assert profile_file == 1.0
