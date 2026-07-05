from types import SimpleNamespace

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import primary_market_meeting
from services import deal_store
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole


class _MeetingSessionManager:
    def __init__(self):
        self.restored = []
        self.current = []
        self.created = []
        self.incremented = []

    def get_current_session_id(self, user_id, profile):
        return None

    def create_session(self, user_id, profile, **kwargs):
        session_id = f"user-{user_id}-{profile}-session-a"
        self.created.append((user_id, profile, kwargs))
        if kwargs.get("return_deleted"):
            return session_id, []
        return session_id

    def set_current_session(self, user_id, profile, session_id):
        self.current.append((user_id, profile, session_id))
        raise HTTPException(404, "Session not found or expired")

    def restore_session(self, user_id, profile, session_id, **kwargs):
        self.restored.append((user_id, profile, session_id, kwargs))
        return session_id

    def increment_message_count(self, session_id):
        self.incremented.append(session_id)


def _user() -> User:
    return User(
        id=7,
        username="analyst",
        email="analyst@example.test",
        hashed_password="x",
        full_name="Analyst",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def _primary_market_client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(deal_store, "WIKI_ROOT", tmp_path / "wiki")
    app = FastAPI()
    app.include_router(primary_market_meeting.router, prefix="/api")

    async def current_user() -> User:
        return User(
            id=7,
            username="ic-admin",
            email="ic-admin@example.test",
            hashed_password="x",
            full_name="IC Admin",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
            approval_status="approved",
        )

    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app)


def test_primary_market_meeting_chat_uses_project_scoped_ic_session(monkeypatch):
    async def run_case():
        session_manager = _MeetingSessionManager()
        captured = {}

        async def fake_enforce_quota_or_429_async(*args, **kwargs):
            captured["quota"] = kwargs
            return (0, None)

        async def fake_record_usage_async(*args, **kwargs):
            captured["usage"] = kwargs
            return None

        async def fake_collect_chat_reply(message, async_session, **kwargs):
            captured["message"] = message
            captured["async_session"] = async_session
            captured.update(kwargs)
            return "finance reply"

        async def fake_db_session_summaries(*args, **kwargs):
            return []

        monkeypatch.setattr(primary_market_meeting, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(primary_market_meeting, "enforce_quota_or_429_async", fake_enforce_quota_or_429_async)
        monkeypatch.setattr(primary_market_meeting, "record_usage_async", fake_record_usage_async)
        monkeypatch.setattr(primary_market_meeting, "collect_chat_reply", fake_collect_chat_reply)
        monkeypatch.setattr(primary_market_meeting, "maybe_handle_model_control", lambda message, profile: None)
        monkeypatch.setattr(primary_market_meeting.chat_router, "_db_session_summaries", fake_db_session_summaries)
        monkeypatch.setattr(
            primary_market_meeting.deal_store,
            "read_deal_detail",
            lambda deal_id: {"summary": {"deal_id": deal_id, "company_name": "Alpha", "current_phase": "R1"}},
        )

        async_session = SimpleNamespace(expunge=lambda _user: None)
        req = primary_market_meeting.PrimaryMarketMeetingChatRequest(
            message="请评估收入确认风险",
            display_message="@财务审计委员 请评估收入确认风险",
            deal_id="DEAL-YUSHU-2026-001",
            context={
                "deal_id": "DEAL-YUSHU-2026-001",
                "company_name": "Alpha",
                "phase": "R1",
                "agent": {"id": "siq_ic_finance_auditor", "label": "财务审计委员"},
            },
        )

        response = await primary_market_meeting.chat(
            "siq_ic_finance_auditor",
            req,
            current_user=_user(),
            async_session=async_session,
        )

        expected_session_profile = "primary-market-DEAL-YUSHU-2026-001-1cecb59e-main"
        expected_session_id = f"user-7-{expected_session_profile}-session-a"
        assert response.reply == "finance reply"
        assert response.session_id == expected_session_id
        assert captured["profile"] == "siq_ic_finance_auditor"
        assert captured["session_id"] == expected_session_id
        assert "一级市场 IC profile 职责护栏:" in captured["message"]
        assert "profile_id: siq_ic_finance_auditor" in captured["message"]
        assert "SIQ 投委会财务专家" in captured["message"]
        assert "财务分析、估值模型、现金流、盈利模式" in captured["message"]
        assert "不得替代行业技术判断、法律合规审查、宏观政策分析、风险清单或最终投决" in captured["message"]
        assert "主持人原始问题:\n\n请评估收入确认风险" in captured["message"]
        assert captured["display_message"] == "@财务审计委员 请评估收入确认风险"
        assert captured["enforce_evidence_contract"] is False
        assert captured["context"].company is None
        assert "deal_id: DEAL-YUSHU-2026-001" in captured["context"].page.title
        assert "这是一级市场项目，不是二级市场股票代码上下文" in captured["context"].page.title
        assert captured["usage"]["source"] == "siq_ic_finance_auditor"
        assert session_manager.created == [("7", expected_session_profile, {"user_role": "analyst"})]
        assert session_manager.current == []
        assert session_manager.restored == []
        assert session_manager.incremented == [expected_session_id]

    anyio.run(run_case)


def test_primary_market_meeting_role_contracts_cover_all_ic_profiles():
    expected = set(primary_market_meeting.IC_MEETING_PROFILES)
    assert set(primary_market_meeting._IC_PROFILE_ROLE_CONTRACTS) == expected
    for profile in primary_market_meeting.IC_MEETING_PROFILES:
        message = primary_market_meeting._profile_scoped_meeting_message(profile, "请介绍你的职责")
        assert f"profile_id: {profile}" in message
        assert f"agents/hermes/profiles/{profile}/IDENTITY.md" in message
        assert "若主持人问题要求越权" in message
        assert "主持人原始问题:\n\n请介绍你的职责" in message


def test_primary_market_meeting_transcript_appends_event(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-001", company_name="Meeting Robotics")

    response = client.post(
        "/api/primary-market/projects/DEAL-MEET-001/meeting-transcript/events",
        json={
            "lane": "main",
            "event": {
                "id": "evt-001",
                "event_type": "agent_note",
                "phase": "R1",
                "speaker": "财务审计委员",
                "title": "收入确认风险",
                "body": "需要复核合同里程碑。",
                "tone": "cautious",
                "meta": {"absolute_path": "/tmp/private-contract.pdf", "source": "contract"},
                "agent_id": "siq_ic_finance_auditor",
                "created_at": "2026-07-05T01:02:03+00:00",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deal_id"] == "DEAL-MEET-001"
    assert payload["lane"] == "main"
    assert payload["event"]["event_type"] == "agent_note"
    assert "type" not in payload["event"]
    assert payload["event"]["agent_id"] == "siq_ic_finance_auditor"
    assert payload["event"]["meta"] == {"source": "contract"}
    assert payload["events"] == [payload["event"]]

    stored = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-001" / "discussion" / "meeting_transcript.json",
        {},
    )
    assert stored["events"][0]["event_type"] == "agent_note"


def test_primary_market_meeting_transcript_accepts_frontend_event_shape(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-UI", company_name="UI Robotics")

    response = client.post(
        "/api/primary-market/projects/DEAL-MEET-UI/meeting-transcript/events",
        json={
            "lane": "main",
            "event": {
                "id": "meeting-ui-001",
                "event_type": "agent_speech",
                "phase": "R1",
                "speaker": "财务审计委员",
                "title": "点名发言",
                "body": "现金流质量需要复核。",
                "tone": "info",
                "meta": "hermes:siq_ic_finance_auditor",
                "agent_id": "siq_ic_finance_auditor",
                "created_at": "2026-07-05T12:00:00.000Z",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"]["event_type"] == "agent_speech"
    assert payload["event"]["meta"] == "hermes:siq_ic_finance_auditor"
    assert payload["event"]["agent_id"] == "siq_ic_finance_auditor"
    assert payload["event"]["created_at"] == "2026-07-05T12:00:00.000Z"


def test_primary_market_meeting_transcript_reads_lane_and_limit(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-002", company_name="Lane Robotics")

    for event_id, lane, body in (
        ("evt-main-1", "main", "first main"),
        ("evt-side-1", "risk", "risk lane"),
        ("evt-main-2", "main", "second main"),
    ):
        response = client.post(
            "/api/primary-market/projects/DEAL-MEET-002/meeting-transcript/events",
            json={
                "lane": lane,
                "event": {
                    "id": event_id,
                    "event_type": "message",
                    "speaker": "IC",
                    "body": body,
                },
            },
        )
        assert response.status_code == 200

    response = client.get(
        "/api/primary-market/projects/DEAL-MEET-002/meeting-transcript",
        params={"lane": "main", "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deal_id"] == "DEAL-MEET-002"
    assert payload["lane"] == "main"
    assert payload["total"] == 2
    assert [event["id"] for event in payload["events"]] == ["evt-main-2"]
    assert payload["events"][0]["event_type"] == "message"


def test_primary_market_meeting_suggestions_are_model_generated(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(
        deal_id="DEAL-SUGGEST-001",
        company_name="Suggestion Robotics",
        industry="Robotics",
        stage="Series B",
    )
    captured = {}

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        captured["prompt"] = prompt
        captured["history"] = history
        captured["profile"] = profile
        captured["session_id"] = session_id
        return "suggestion-run-1"

    async def fake_collect_run_result(run_id, *, profile, timeout=None):
        captured["run_id"] = run_id
        captured["collect_profile"] = profile
        captured["timeout"] = timeout
        return """
        {
          "intro": "我是风险管理委员，会结合当前项目阶段生成下行情景、监测指标和止损机制。",
          "questions": [
            {"label": "下行情景", "prompt": "请基于当前项目阶段构建三种下行情景。"},
            {"label": "风险清单", "prompt": "请按概率、影响和可控性排序当前核心风险。"},
            {"label": "补证优先", "prompt": "请指出风险判断最需要补充的证据和材料。"},
            {"label": "投后指标", "prompt": "请设计投后监测指标和触发阈值。"},
            {"label": "止损机制", "prompt": "请提出交易文件和投后管理中的止损机制。"}
          ]
        }
        """

    monkeypatch.setattr(primary_market_meeting, "create_run", fake_create_run)
    monkeypatch.setattr(primary_market_meeting, "collect_run_result", fake_collect_run_result)

    response = client.get(
        "/api/primary-market/meeting/siq_ic_risk_controller/suggestions",
        params={
            "deal_id": "DEAL-SUGGEST-001",
            "lane": "agent-siq_ic_risk_controller",
            "mode": "single",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "model"
    assert payload["profile"] == "siq_ic_risk_controller"
    assert payload["questions"][0]["label"] == "下行情景"
    assert payload["questions"][4]["prompt"].startswith("请提出交易文件")
    assert captured["history"] == []
    assert captured["session_id"] is None
    assert captured["profile"] == "siq_ic_risk_controller"
    assert captured["collect_profile"] == "siq_ic_risk_controller"
    assert "DEAL-SUGGEST-001" in captured["prompt"]
    assert "Suggestion Robotics" in captured["prompt"]
    assert "风险管理委员" in captured["prompt"]


def test_primary_market_meeting_uploads_project_scoped_attachment(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-ATT", company_name="Attachment Robotics")

    response = client.post(
        "/api/primary-market/projects/DEAL-MEET-ATT/meeting/attachments",
        json={
            "files": [
                {
                    "filename": "memo.txt",
                    "content_type": "text/plain",
                    "data_url": "data:text/plain;base64,aGVsbG8=",
                }
            ]
        },
    )

    assert response.status_code == 200
    attachment = response.json()["attachments"][0]
    assert attachment["filename"] == "memo.txt"
    assert attachment["kind"] == "document"
    assert attachment["url"].startswith("/api/primary-market/projects/DEAL-MEET-ATT/meeting/attachments/")
    assert "chat_uploads/primary_market_projects/DEAL-MEET-ATT/" in attachment["path"].replace("\\", "/")

    stored_name = attachment["url"].rsplit("/", 1)[-1]
    download = client.get(f"/api/primary-market/projects/DEAL-MEET-ATT/meeting/attachments/{stored_name}")
    assert download.status_code == 200
    assert download.content == b"hello"

    missing = client.get("/api/primary-market/projects/DEAL-MEET-ATT/meeting/attachments/missing.txt")
    assert missing.status_code == 404


def test_primary_market_meeting_transcript_rejects_missing_deal(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)

    read_response = client.get("/api/primary-market/projects/DEAL-MISSING-002/meeting-transcript")
    write_response = client.post(
        "/api/primary-market/projects/DEAL-MISSING-002/meeting-transcript/events",
        json={"lane": "main", "event": {"event_type": "message", "body": "hello"}},
    )

    assert read_response.status_code == 404
    assert write_response.status_code == 404


def test_primary_market_project_facade_lists_and_filters_projects(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(
        deal_id="DEAL-ALPHA-001",
        company_name="Alpha Robotics",
        industry="Robotics",
        overwrite=True,
    )
    deal_store.create_deal_package(
        deal_id="DEAL-BETA-001",
        company_name="Beta Health",
        industry="Healthcare",
        overwrite=True,
    )

    response = client.get("/api/primary-market/projects", params={"q": "robot", "status": "draft"})

    assert response.status_code == 200
    payload = response.json()
    assert [deal["deal_id"] for deal in payload["deals"]] == ["DEAL-ALPHA-001"]
    assert payload["deals"][0]["company_name"] == "Alpha Robotics"


def test_primary_market_project_facade_rejects_missing_project(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)

    response = client.get("/api/primary-market/projects/DEAL-MISSING-001")

    assert response.status_code == 404


def test_primary_market_meeting_rejects_non_ic_profile():
    try:
        primary_market_meeting.canonical_meeting_profile("siq_assistant")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("siq_assistant must not be exposed through primary-market meeting chat")


def test_primary_market_meeting_rejects_ic_profile_aliases():
    try:
        primary_market_meeting.canonical_meeting_profile("ic_finance")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("primary-market meeting chat should expose canonical siq_ic_* profiles only")


def test_primary_market_meeting_session_scope_is_deal_lane_scoped():
    scope = primary_market_meeting._meeting_session_scope(
        "siq_ic_finance_auditor",
        "DEAL-YUSHU-2026-001",
        "agent-siq_ic_finance_auditor",
    )

    assert scope == "primary-market-DEAL-YUSHU-2026-001-1cecb59e-agent-siq_ic_finance_auditor"


def test_primary_market_meeting_rejects_mismatched_single_agent_lane():
    try:
        primary_market_meeting._meeting_session_scope(
            "siq_ic_finance_auditor",
            "DEAL-YUSHU-2026-001",
            "agent-siq_ic_chairman",
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("single-agent lane must match requested IC profile")


def test_primary_market_meeting_requires_deal_id():
    try:
        primary_market_meeting.deal_id_from_request(primary_market_meeting.PrimaryMarketMeetingChatRequest(message="hello"))
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("primary-market meeting chat must require a deal id")


def test_primary_market_meeting_rejects_missing_deal(monkeypatch):
    async def run_case():
        monkeypatch.setattr(primary_market_meeting, "enforce_quota_or_429_async", lambda *args, **kwargs: None)
        monkeypatch.setattr(primary_market_meeting, "record_usage_async", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            primary_market_meeting.deal_store,
            "read_deal_detail",
            lambda deal_id: (_ for _ in ()).throw(FileNotFoundError(deal_id)),
        )

        req = primary_market_meeting.PrimaryMarketMeetingChatRequest(
            message="hello",
            deal_id="DEAL-MISSING-001",
        )
        try:
            await primary_market_meeting.chat(
                "siq_ic_finance_auditor",
                req,
                current_user=_user(),
                async_session=SimpleNamespace(expunge=lambda _user: None),
            )
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("missing deal must be rejected")

    anyio.run(run_case)
