from types import SimpleNamespace

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import primary_market_meeting
from services import deal_store
from services import ic_policy
from services import ic_profile_contract
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


def _primary_market_user(
    *,
    user_id: int = 7,
    username: str = "ic-admin",
    role: UserRole = UserRole.SUPER_ADMIN,
) -> User:
    return User(
        id=user_id,
        username=username,
        email=f"{username}@example.test",
        hashed_password="x",
        full_name=username,
        role=role,
        is_active=True,
        approval_status="approved",
    )


def _primary_market_client(monkeypatch, tmp_path, user: User | None = None) -> TestClient:
    monkeypatch.setattr(deal_store, "WIKI_ROOT", tmp_path / "wiki")
    app = FastAPI()
    app.include_router(primary_market_meeting.router, prefix="/api")
    current_user_payload = user or _primary_market_user()

    async def current_user() -> User:
        return current_user_payload

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
        monkeypatch.setattr(primary_market_meeting.deal_store, "user_can_access_deal", lambda *args, **kwargs: True)

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
        assert "不做行业技术判断" in captured["message"]
        assert "不做最终投资决策" in captured["message"]
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
    contracts = ic_profile_contract.list_ic_profile_contracts()
    assert {contract["profile_id"] for contract in contracts} == expected
    for profile in primary_market_meeting.IC_MEETING_PROFILES:
        message = primary_market_meeting._profile_scoped_meeting_message(profile, "请介绍你的职责")
        assert f"profile_id: {profile}" in message
        assert f"agents/hermes/profiles/{profile}/IDENTITY.md" in message
        assert "若主持人问题要求越权" in message
        assert "Deal OS evidence" in message
        assert "主持人原始问题:\n\n请介绍你的职责" in message


def test_primary_market_meeting_profile_contract_reads_profile_files():
    contract = ic_profile_contract.get_ic_profile_contract("siq_ic_finance_auditor")

    assert contract["profile_id"] == "siq_ic_finance_auditor"
    assert contract["role_name"].startswith("SIQ 投委会财务专家")
    assert "财务分析、估值模型、现金流、盈利模式" in contract["core_focus"]
    assert any("不做行业技术判断" in item for item in contract["boundaries"])
    assert contract["retrieval_collections"] == ["siq_deal_shared", "siq_ic_finance_auditor"]


def test_primary_market_meeting_message_includes_receipt_and_report_context(monkeypatch):
    monkeypatch.setattr(
        primary_market_meeting.ic_startup_retrieval,
        "read_startup_retrieval_receipt",
        lambda deal_id, profile_id: {
            "deal_id": deal_id,
            "agent_id": profile_id,
            "receipt": {
                "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
                "shared_hits": 6,
                "private_hits": 1,
                "gaps": ["missing_cashflow"],
            },
        },
    )
    monkeypatch.setattr(
        primary_market_meeting.deal_reports,
        "list_r1_agent_reports",
        lambda deal_id: {
            "deal_id": deal_id,
            "agents": [
                {
                    "agent_id": "siq_ic_finance_auditor",
                    "has_report": True,
                    "status": "warn",
                    "score": 78,
                    "recommendation": "review",
                    "artifact_path": "discussion/01_R1_finance_auditor_report.md",
                }
            ],
        },
    )

    message = primary_market_meeting._profile_scoped_meeting_message(
        "siq_ic_finance_auditor",
        "请更新财务意见",
        "DEAL-EVIDENCE-001",
    )

    assert "一级市场项目证据上下文:" in message
    assert "startup_retrieval_receipt: present" in message
    assert "receipt_id=startup-siq_ic_finance_auditor-R1-001" in message
    assert "r1_report: present" in message
    assert "artifact_path=discussion/01_R1_finance_auditor_report.md" in message
    assert "主持人原始问题:\n\n请更新财务意见" in message


def test_primary_market_meeting_quality_check_writes_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr(deal_store, "WIKI_ROOT", tmp_path / "wiki")
    deal_store.create_deal_package(deal_id="DEAL-MEET-QUALITY", company_name="Quality Robotics")
    client = _primary_market_client(monkeypatch, tmp_path)

    quality = primary_market_meeting._evaluate_and_store_reply_quality(
        deal_id="DEAL-MEET-QUALITY",
        lane="agent-siq_ic_finance_auditor",
        profile="siq_ic_finance_auditor",
        message="你来拍板",
        reply="我决定投资这个项目。",
    )

    assert quality is not None
    assert quality["status"] == "fail"
    stored = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-QUALITY" / "discussion" / "meeting_transcript.json",
        {},
    )
    event = stored["events"][0]
    assert event["event_type"] == "quality_check"
    assert event["tone"] == "error"
    assert event["agent_id"] == "siq_ic_finance_auditor"
    assert "role.boundary=fail" in event["body"]

    stored_quality = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-QUALITY" / "discussion" / "meeting_quality.json",
        {},
    )
    quality_event = stored_quality["events"][0]
    assert stored_quality["schema_version"] == "siq_primary_market_meeting_quality_v1"
    assert quality_event["profile_id"] == "siq_ic_finance_auditor"
    assert quality_event["lane"] == "agent-siq_ic_finance_auditor"
    assert quality_event["transcript_event_id"] == event["id"]
    assert quality_event["quality"]["status"] == "fail"

    response = client.get(
        "/api/primary-market/meeting/DEAL-MEET-QUALITY/quality",
        params={"lane": "agent-siq_ic_finance_auditor", "profile_id": "siq_ic_finance_auditor"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_primary_market_meeting_quality_v1"
    assert payload["total"] == 1
    assert payload["events"][0]["quality"]["status"] == "fail"


def test_primary_market_meeting_readiness_endpoint(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-READY", company_name="Ready Robotics")

    response = client.get("/api/primary-market/meeting/DEAL-MEET-READY/agents/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "siq_primary_market_meeting_readiness_v1"
    assert payload["deal_id"] == "DEAL-MEET-READY"
    assert payload["summary"]["agents"] == len(ic_policy.IC_PROFILE_IDS)
    assert {item["agent_id"] for item in payload["agents"]} == set(ic_policy.IC_PROFILE_IDS)
    finance = next(item for item in payload["agents"] if item["agent_id"] == "siq_ic_finance_auditor")
    assert finance["contract"]["startup_retrieval_required"] is True
    assert finance["startup_receipt"]["required"] is True


def test_primary_market_meeting_prepare_agent_appends_receipt_event(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-PREP", company_name="Prep Robotics")

    def fake_generate_startup_retrieval_receipt(*args, **kwargs):
        assert args[:2] == ("DEAL-MEET-PREP", "siq_ic_finance_auditor")
        return {
            "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
            "shared_hits": 2,
            "private_hits": 0,
            "gaps": ["missing_finance_evidence"],
        }

    monkeypatch.setattr(
        primary_market_meeting.ic_startup_retrieval,
        "generate_startup_retrieval_receipt",
        fake_generate_startup_retrieval_receipt,
    )
    monkeypatch.setattr(
        primary_market_meeting.primary_market_meeting_readiness,
        "build_meeting_readiness",
        lambda deal_id: {"deal_id": deal_id, "summary": {"agents": 7}},
    )

    response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-PREP/agents/siq_ic_finance_auditor/prepare",
        json={"round_name": "R1", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "siq_ic_finance_auditor"
    assert payload["receipt"]["receipt_id"] == "startup-siq_ic_finance_auditor-R1-001"
    assert payload["event"]["event_type"] == "receipt_generated"
    assert payload["event"]["agent_id"] == "siq_ic_finance_auditor"


def test_primary_market_meeting_workflow_advance_dry_run(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-WORKFLOW", company_name="Workflow Robotics")

    def fake_dry_run(deal_id, **kwargs):
        return {"deal_id": deal_id, "next_action": "run-r1-agent", "kwargs": kwargs}

    monkeypatch.setattr(
        primary_market_meeting.ic_agent_runtime,
        "build_workflow_advance_next_dry_run",
        fake_dry_run,
    )

    response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-WORKFLOW/workflow/advance",
        json={"dry_run": True, "max_agents": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["result"]["next_action"] == "run-r1-agent"
    assert payload["result"]["kwargs"]["max_agents"] == 2
    assert payload["event"]["event_type"] == "audit_event"
    assert payload["event"]["lane"] == "workflow-main"
    assert payload["event"]["meta"]["dry_run"] is True
    stored = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-WORKFLOW" / "discussion" / "meeting_transcript.json",
        {},
    )
    assert stored["events"][0]["title"] == "Workflow 下一步预演"


def test_primary_market_meeting_r1_agent_facade_dry_run_and_execute(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-R1AGENT", company_name="R1 Agent Robotics")
    captured = {}

    def fake_dry_run(deal_id, profile_id, **kwargs):
        captured["dry_run"] = (deal_id, profile_id, kwargs)
        return {
            "schema_version": "siq_ic_workflow_r1_agent_run_dry_run_v1",
            "deal_id": deal_id,
            "agent_id": profile_id,
            "workflow_action": "run-r1-agent",
            "dry_run": True,
            "allowed": True,
        }

    async def fake_run(deal_id, profile_id, **kwargs):
        captured["execute"] = (deal_id, profile_id, kwargs)
        return {
            "schema_version": "siq_ic_workflow_r1_agent_run_v1",
            "deal_id": deal_id,
            "agent_id": profile_id,
            "workflow_action": "run-r1-agent",
            "dry_run": False,
            "markdown_path": "discussion/01_R1_finance_auditor_report.md",
            "json_path": "phases/r1_reports.json",
            "hermes_run_id": "run-finance-1",
            "report_written": True,
            "workflow_advanced": True,
            "report": {"score": 82, "recommendation": "watch"},
        }

    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "build_workflow_r1_agent_run_dry_run", fake_dry_run)
    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "run_workflow_r1_agent", fake_run)
    monkeypatch.setattr(
        primary_market_meeting.primary_market_meeting_readiness,
        "build_meeting_readiness",
        lambda deal_id: {"deal_id": deal_id, "summary": {"r1_reports_present": 1}},
    )

    dry_response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1AGENT/agents/siq_ic_finance_auditor/run-r1",
        json={"dry_run": True, "round_name": "R1"},
    )
    execute_response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1AGENT/agents/siq_ic_finance_auditor/run-r1",
        json={"dry_run": False, "round_name": "R1", "allow_hermes": True},
    )

    assert dry_response.status_code == 200
    dry_payload = dry_response.json()
    assert dry_payload["dry_run"] is True
    assert dry_payload["result"]["workflow_action"] == "run-r1-agent"
    assert captured["dry_run"] == ("DEAL-MEET-R1AGENT", "siq_ic_finance_auditor", {"round_name": "R1"})

    assert execute_response.status_code == 200
    execute_payload = execute_response.json()
    assert execute_payload["dry_run"] is False
    assert execute_payload["agent_id"] == "siq_ic_finance_auditor"
    assert execute_payload["event"]["event_type"] == "artifact_written"
    assert execute_payload["event"]["lane"] == "agent-siq_ic_finance_auditor"
    assert execute_payload["event"]["agent_id"] == "siq_ic_finance_auditor"
    assert "artifact_path: discussion/01_R1_finance_auditor_report.md" in execute_payload["event"]["body"]
    assert execute_payload["event"]["meta"]["source"] == "workflow.run_r1_agent"
    assert captured["execute"][0:2] == ("DEAL-MEET-R1AGENT", "siq_ic_finance_auditor")
    assert captured["execute"][2]["round_name"] == "R1"
    assert captured["execute"][2]["created_by"]["username"] == "ic-admin"

    stored = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-R1AGENT" / "discussion" / "meeting_transcript.json",
        {},
    )
    assert stored["events"][0]["event_type"] == "artifact_written"
    assert stored["events"][0]["meta"]["artifact_path"] == "discussion/01_R1_finance_auditor_report.md"


def test_primary_market_meeting_r1_agent_returns_conflict_for_active_claim(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-CLAIM", company_name="Claim Robotics")

    async def fake_run(*_args, **_kwargs):
        raise primary_market_meeting.ic_agent_runtime.ICTaskAlreadyClaimedError(
            {
                "task_key": "DEAL-MEET-CLAIM:R1:siq_ic_strategist",
                "attempt": 1,
                "lease_expires_at": "2026-07-12T09:02:00Z",
            }
        )

    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "run_workflow_r1_agent", fake_run)

    response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-CLAIM/agents/siq_ic_strategist/run-r1",
        json={"dry_run": False, "round_name": "R1", "allow_hermes": True},
    )

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_primary_market_meeting_r1_agent_execution_requires_hermes_consent(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-R1AGENT-CONSENT", company_name="R1 Agent Consent")
    called = {"run": False}

    async def fake_run(*args, **kwargs):
        called["run"] = True
        return {}

    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "run_workflow_r1_agent", fake_run)

    response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1AGENT-CONSENT/agents/siq_ic_finance_auditor/run-r1",
        json={"dry_run": False, "round_name": "R1"},
    )

    assert response.status_code == 400
    assert "allow_hermes" in response.json()["detail"]
    assert called["run"] is False


def test_primary_market_meeting_r1_serial_facade_dry_run_and_execute(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-R1SERIAL", company_name="R1 Serial Robotics")
    captured = {}

    def fake_dry_run(deal_id, **kwargs):
        captured["dry_run"] = (deal_id, kwargs)
        return {
            "schema_version": "siq_ic_workflow_r1_serial_run_dry_run_v1",
            "deal_id": deal_id,
            "workflow_action": "run-r1-serial",
            "dry_run": True,
            "planned_agent_ids": ["siq_ic_strategist", "siq_ic_sector_expert"],
            "planned_count": 2,
        }

    async def fake_run(deal_id, **kwargs):
        captured["execute"] = (deal_id, kwargs)
        return {
            "schema_version": "siq_ic_workflow_r1_serial_run_v1",
            "deal_id": deal_id,
            "workflow_action": "run-r1-serial",
            "dry_run": False,
            "planned_agent_ids": ["siq_ic_strategist", "siq_ic_sector_expert"],
            "executed_agent_ids": ["siq_ic_strategist", "siq_ic_sector_expert"],
            "executed_count": 2,
            "report_written": True,
            "workflow_advanced": True,
            "agent_runs": [
                {"agent_id": "siq_ic_strategist", "markdown_path": "discussion/01_R1_strategist_report.md"},
                {"agent_id": "siq_ic_sector_expert", "markdown_path": "discussion/02_R1_sector_expert_report.md"},
            ],
        }

    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "build_workflow_r1_serial_run_dry_run", fake_dry_run)
    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "run_workflow_r1_serial", fake_run)
    monkeypatch.setattr(
        primary_market_meeting.primary_market_meeting_readiness,
        "build_meeting_readiness",
        lambda deal_id: {"deal_id": deal_id, "summary": {"r1_reports_present": 2}},
    )

    dry_response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1SERIAL/workflow/run-r1-serial",
        json={"dry_run": True, "round_name": "R1", "max_agents": 2},
    )
    execute_response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1SERIAL/workflow/run-r1-serial",
        json={"dry_run": False, "round_name": "R1", "max_agents": 2, "allow_hermes": True},
    )

    assert dry_response.status_code == 200
    dry_payload = dry_response.json()
    assert dry_payload["dry_run"] is True
    assert dry_payload["result"]["planned_count"] == 2
    assert captured["dry_run"] == ("DEAL-MEET-R1SERIAL", {"round_name": "R1", "max_agents": 2})

    assert execute_response.status_code == 200
    execute_payload = execute_response.json()
    assert execute_payload["dry_run"] is False
    assert execute_payload["event"]["event_type"] == "artifact_written"
    assert execute_payload["event"]["lane"] == "workflow-main"
    assert execute_payload["event"]["agent_id"] == "siq_ic_master_coordinator"
    assert "executed_count: 2" in execute_payload["event"]["body"]
    assert "discussion/01_R1_strategist_report.md" in execute_payload["event"]["body"]
    assert execute_payload["event"]["meta"]["source"] == "workflow.run_r1_serial"
    assert captured["execute"][0] == "DEAL-MEET-R1SERIAL"
    assert captured["execute"][1]["max_agents"] == 2
    assert captured["execute"][1]["created_by"]["username"] == "ic-admin"

    stored = deal_store.read_json(
        tmp_path / "wiki" / "deals" / "DEAL-MEET-R1SERIAL" / "discussion" / "meeting_transcript.json",
        {},
    )
    assert stored["events"][0]["event_type"] == "artifact_written"
    assert stored["events"][0]["meta"]["executed_agent_ids"] == ["siq_ic_strategist", "siq_ic_sector_expert"]


def test_primary_market_meeting_r1_serial_execution_requires_hermes_consent(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deal_store.create_deal_package(deal_id="DEAL-MEET-R1SERIAL-CONSENT", company_name="R1 Serial Consent")
    called = {"run": False}

    async def fake_run(*args, **kwargs):
        called["run"] = True
        return {}

    monkeypatch.setattr(primary_market_meeting.ic_agent_runtime, "run_workflow_r1_serial", fake_run)

    response = client.post(
        "/api/primary-market/meeting/DEAL-MEET-R1SERIAL-CONSENT/workflow/run-r1-serial",
        json={"dry_run": False, "round_name": "R1", "max_agents": 2},
    )

    assert response.status_code == 400
    assert "allow_hermes" in response.json()["detail"]
    assert called["run"] is False


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


def test_primary_market_project_facade_paginates_without_hiding_total_or_statuses(monkeypatch, tmp_path):
    client = _primary_market_client(monkeypatch, tmp_path)
    deals = [
        {
            "deal_id": f"DEAL-PAGE-{index:03d}",
            "company_name": f"Company {index:03d}",
            "status": "draft",
        }
        for index in range(55)
    ]
    monkeypatch.setattr(primary_market_meeting.deal_store, "list_deals", lambda: deals)
    monkeypatch.setattr(primary_market_meeting.deal_store, "filter_deals_for_user", lambda items, _user: items)
    monkeypatch.setattr(
        primary_market_meeting.deal_status,
        "summarize_deal_status",
        lambda deal_id: {"deal_id": deal_id, "ready_for_next_action": True},
    )

    response = client.get(
        "/api/primary-market/projects",
        params={"page": 2, "page_size": 50, "include_status": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["deal_id"] for item in payload["deals"]] == [f"DEAL-PAGE-{index:03d}" for index in range(50, 55)]
    assert payload["stats"]["total"] == 55
    assert payload["pagination"] == {"page": 2, "page_size": 50, "total": 55, "has_more": False}
    assert sorted(payload["status_summaries"]) == [f"DEAL-PAGE-{index:03d}" for index in range(50, 55)]


def test_primary_market_project_facade_requires_deal_access(monkeypatch, tmp_path):
    owner = _primary_market_user(user_id=7, username="owner", role=UserRole.ANALYST)
    other_analyst = _primary_market_user(user_id=8, username="other-analyst", role=UserRole.ANALYST)
    owner_client = _primary_market_client(monkeypatch, tmp_path, owner)
    other_client = _primary_market_client(monkeypatch, tmp_path, other_analyst)
    deal_store.create_deal_package(
        deal_id="DEAL-PM-BOLA",
        company_name="Private Meeting Robotics",
        industry="Robotics",
        created_by={"id": 7, "username": "owner"},
        overwrite=True,
    )

    owner_detail = owner_client.get("/api/primary-market/projects/DEAL-PM-BOLA")
    assert owner_detail.status_code == 200
    assert owner_detail.json()["project_meta"]["created_by"] == {"id": 7, "username": "owner"}

    listed = other_client.get("/api/primary-market/projects")
    detail = other_client.get("/api/primary-market/projects/DEAL-PM-BOLA")
    status = other_client.get("/api/primary-market/projects/DEAL-PM-BOLA/status")
    transcript_write = other_client.post(
        "/api/primary-market/projects/DEAL-PM-BOLA/meeting-transcript/events",
        json={"lane": "main", "event": {"event_type": "message", "body": "should be denied"}},
    )

    assert listed.status_code == 200
    assert [item["deal_id"] for item in listed.json()["deals"]] == []
    assert detail.status_code == 404
    assert status.status_code == 404
    assert transcript_write.status_code == 404
    transcript_path = tmp_path / "wiki" / "deals" / "DEAL-PM-BOLA" / "discussion" / "meeting_transcript.json"
    assert not transcript_path.exists()
    access_decisions = tmp_path / "wiki" / "deals" / "DEAL-PM-BOLA" / "audit" / "access_decisions.ndjson"
    assert access_decisions.is_file()
    access_log = access_decisions.read_text(encoding="utf-8")
    assert "primary_market.view" in access_log
    assert "primary_market.write" in access_log
    assert "deal_object_access_denied" in access_log


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
