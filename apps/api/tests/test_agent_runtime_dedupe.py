from services import agent_runtime_dedupe


class _ModelLike:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, exclude_none=True):
        if not exclude_none:
            return dict(self._payload)
        return {key: value for key, value in self._payload.items() if value is not None}


class _AttachmentLike:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


def test_dedupe_hash_normalizes_message_and_context():
    context_dict = {"company": "上汽集团", "year": 2025}
    context_model = _ModelLike({"company": "上汽集团", "year": 2025, "ignored": None})

    assert agent_runtime_dedupe._dedupe_hash("  继续\n分析  ", context_dict) == agent_runtime_dedupe._dedupe_hash(
        "继续 分析",
        context_model,
    )


def test_dedupe_hash_with_attachments_ignores_invalid_entries_and_normalizes_sizes():
    attachments_a = [
        _AttachmentLike({"id": "att-1", "path": "/tmp/a.png", "size": "12"}),
        {"id": "att-2", "size": 0},
    ]
    attachments_b = [{"id": "att-1", "path": "/tmp/a.png", "size": 12}]

    assert agent_runtime_dedupe._dedupe_hash_with_attachments(
        "问题",
        None,
        attachments_a,
    ) == agent_runtime_dedupe._dedupe_hash_with_attachments(
        " 问题 ",
        None,
        attachments_b,
    )


def test_recent_completed_run_memory_uses_active_key_and_fallbacks():
    agent_runtime_dedupe.RECENT_COMPLETED_RUNS.clear()

    def active_key(profile, session_id):
        return (str(profile).lower(), session_id)

    agent_runtime_dedupe.remember_completed_run(
        "SIQ_ASSISTANT",
        "session-1",
        "hash-1",
        "",
        active_key=active_key,
    )

    assert agent_runtime_dedupe.recent_duplicate_reply(
        "siq_assistant",
        "session-1",
        "hash-1",
        active_key=active_key,
        idempotency_window_seconds=60,
        duplicate_message="duplicate",
        analysis_duplicate_message="analysis duplicate",
    ) == "duplicate"

    agent_runtime_dedupe.remember_completed_run(
        "siq_analysis",
        "session-2",
        "hash-2",
        "",
        active_key=active_key,
    )
    assert agent_runtime_dedupe.recent_duplicate_reply(
        "siq_analysis",
        "session-2",
        "hash-2",
        active_key=active_key,
        idempotency_window_seconds=60,
        duplicate_message="duplicate",
        analysis_duplicate_message="analysis duplicate",
    ) == "analysis duplicate"


def test_recent_completed_run_forget_and_expiry():
    agent_runtime_dedupe.RECENT_COMPLETED_RUNS.clear()

    def active_key(profile, session_id):
        return (profile, session_id)

    agent_runtime_dedupe.remember_completed_run(
        "siq_assistant",
        "session-1",
        "hash-1",
        "cached reply",
        active_key=active_key,
    )
    assert agent_runtime_dedupe.recent_duplicate_reply(
        "siq_assistant",
        "session-1",
        "different-hash",
        active_key=active_key,
        idempotency_window_seconds=60,
        duplicate_message="duplicate",
        analysis_duplicate_message="analysis duplicate",
    ) is None
    assert agent_runtime_dedupe.recent_duplicate_reply(
        "siq_assistant",
        "session-1",
        "hash-1",
        active_key=active_key,
        idempotency_window_seconds=-1,
        duplicate_message="duplicate",
        analysis_duplicate_message="analysis duplicate",
    ) is None

    agent_runtime_dedupe.forget_recent_completed_run(
        "siq_assistant",
        "session-1",
        "hash-1",
        active_key=active_key,
    )
    assert ("siq_assistant", "session-1") not in agent_runtime_dedupe.RECENT_COMPLETED_RUNS
