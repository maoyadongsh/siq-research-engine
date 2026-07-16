from types import SimpleNamespace

import anyio
import pytest
from routers import primary_market_meeting
from services.auth_service import User, UserRole

from services import ic_agent_runtime, ic_profile_contract, ic_task_contracts

LEGAL_PROFILE = "siq_ic_legal_scanner"
DEAL_ID = "DEAL-LEGAL-RUNTIME-001"
IC_PROFILES = (
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)


def _user() -> User:
    return User(
        id=71,
        username="legal-reviewer",
        email="legal-reviewer@example.test",
        hashed_password="x",
        full_name="Legal Reviewer",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def _retrieval_payload() -> dict:
    return {
        "milvus_used": True,
        "evidence_hits": [],
        "shared_vector_hits": [
            {
                "source_id": "VEC-siq_deal_shared-001",
                "evidence_id": "EVID-LEGAL-001",
                "citation": "shareholders-agreement.pdf p.12",
                "quote_preview": "交割前应取得核心经营许可。",
            }
        ],
        "background_knowledge_hits": [
            {
                "source_id": "VEC-siq_ic_legal_scanner-001",
                "title": "一级市场法律尽调方法论",
                "knowledge_lane": "domain_background",
                "quote_preview": "核验主体、股权、资质、合同、诉讼、知识产权与数据合规。",
            }
        ],
        "vector_retrieval": {
            "status": "completed",
            "collections": ["siq_deal_shared", LEGAL_PROFILE],
            "physical_collections": {
                "siq_deal_shared": "ic_collaboration_shared",
                LEGAL_PROFILE: "ic_legal_scanner",
            },
        },
    }


def test_legal_profile_contract_covers_independent_and_collaborative_duties():
    contract = ic_profile_contract.get_ic_profile_contract(LEGAL_PROFILE)

    assert contract["profile_id"] == LEGAL_PROFILE
    assert contract["namespace_policy"]["namespace"] == "primary_market"
    assert contract["namespace_policy"]["allowed_roots"] == ["data/wiki/deals/{deal_id}"]
    assert "data/wiki/companies" in contract["namespace_policy"]["forbidden_roots"]
    assert contract["retrieval"]["logical_collections"] == ["siq_deal_shared", LEGAL_PROFILE]
    assert contract["retrieval"]["physical_collections"] == [
        "ic_collaboration_shared",
        "ic_legal_scanner",
    ]
    assert contract["phase_capabilities"] == {
        "R1A": ["independent_legal_report"],
        "R2": ["legal_revision", "score_delta", "condition_update"],
        "R3": ["red_or_blue_argument_on_legal_and_compliance_topics"],
    }
    assert ic_task_contracts.ROLE_PHASE_CAPABILITIES[LEGAL_PROFILE] == ("R1A", "R2", "R3")
    duties = " ".join(contract["responsibilities"])
    for duty in ("ownership", "licenses", "litigation", "IP", "data compliance", "closing conditions"):
        assert duty in duties
    boundaries = " ".join(contract["boundaries"])
    assert "does not replace industry or financial analysis" in boundaries
    assert "不做最终投资决策" in boundaries


def test_legal_scanner_live_context_retrieves_deal_shared_and_private_collections(monkeypatch):
    captured = {}

    def fake_retrieve(deal_id, profile_id, **kwargs):
        captured.update({"deal_id": deal_id, "profile_id": profile_id, **kwargs})
        return _retrieval_payload()

    monkeypatch.setattr(primary_market_meeting.deal_retrieval, "retrieve_for_agent", fake_retrieve)
    context = primary_market_meeting._live_meeting_retrieval_context(
        LEGAL_PROFILE,
        DEAL_ID,
        "请核验股权权属、资质、诉讼、知识产权、数据合规和交割条件",
    )

    assert captured["deal_id"] == DEAL_ID
    assert captured["profile_id"] == LEGAL_PROFILE
    assert captured["include_vector"] is True
    assert captured["query"].startswith("请核验股权权属")
    assert f"project_tag={DEAL_ID}" in context
    assert "shared_collection: siq_deal_shared -> ic_collaboration_shared" in context
    assert "private_collection: siq_ic_legal_scanner -> ic_legal_scanner" in context
    assert "EVID-LEGAL-001" in context
    assert "VEC-siq_ic_legal_scanner-001" in context
    assert "data/wiki/companies" in context


@pytest.mark.parametrize("profile_id", IC_PROFILES)
def test_every_ic_chat_entrypoint_injects_role_contract_and_runs_dual_kb_rerank(
    monkeypatch,
    profile_id,
):
    captured = {}
    private_physical = profile_id.removeprefix("siq_")

    def fake_retrieve(deal_id, requested_profile, **kwargs):
        captured.update({"deal_id": deal_id, "profile_id": requested_profile, **kwargs})
        return {
            "milvus_used": True,
            "evidence_hits": [],
            "shared_vector_hits": [{
                "source_id": "VEC-SHARED-001",
                "evidence_id": "EVID-CURRENT-DEAL-001",
                "project_tag": deal_id,
                "citation": "current-deal/material.md",
                "quote_preview": "当前 Deal 项目底稿证据。",
            }],
            "background_knowledge_hits": [{
                "source_id": f"VEC-{requested_profile}-001",
                "collection": requested_profile,
                "title": "本角色私有方法论",
                "knowledge_lane": "methodology",
                "quote_preview": "只用于评价框架，不是项目事实。",
            }],
            "vector_retrieval": {
                "status": "completed",
                "collections": ["siq_deal_shared", requested_profile],
                "physical_collections": {
                    "siq_deal_shared": "ic_collaboration_shared",
                    requested_profile: private_physical,
                },
                "shared_filter_applied": True,
                "shared_project_tag": deal_id,
                "retrieval_strategy": {"mode": "dense_bm25_rrf"},
            },
            "rerank": {
                "status": "completed",
                "candidate_count": 2,
                "result_count": 2,
                "model": "Qwen3-VL-Reranker-2B",
            },
            "retrieval_observability": {
                "collection_candidate_counts": {"siq_deal_shared": 1, requested_profile: 1}
            },
        }

    monkeypatch.setattr(primary_market_meeting.deal_retrieval, "retrieve_for_agent", fake_retrieve)
    monkeypatch.setattr(
        primary_market_meeting,
        "_receipt_context_for",
        lambda *_args: {"required": True, "present": False},
    )
    monkeypatch.setattr(
        primary_market_meeting,
        "_r1_report_context_for",
        lambda *_args: {"required": False, "present": False},
    )

    scoped = primary_market_meeting._profile_scoped_meeting_message(
        profile_id,
        "请按你的职责独立核验当前项目。",
        DEAL_ID,
        retrieval_query="请按你的职责独立核验当前项目。",
    )

    assert captured == {
        "deal_id": DEAL_ID,
        "profile_id": profile_id,
        "query": "请按你的职责独立核验当前项目。",
        "limit": 8,
        "include_vector": True,
        "include_rerank": True,
    }
    for source_name in ("IDENTITY.md", "AGENTS.md", "SOUL.md", "TOOLS.md"):
        assert f"agents/hermes/profiles/{profile_id}/{source_name}" in scoped
    assert "shared_collection: siq_deal_shared -> ic_collaboration_shared (仅允许当前 project_tag)" in scoped
    assert f"private_collection: {profile_id} -> {private_physical}" in scoped
    assert "retrieval_strategy: dense_bm25_rrf" in scoped
    assert "rerank_status=completed" in scoped
    assert "私库只能支持方法论或法律框架" in scoped


def _formal_receipt(profile_id=LEGAL_PROFILE):
    physical_private = profile_id.removeprefix("siq_")
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": f"startup-{profile_id}-R1-001",
        "deal_id": DEAL_ID,
        "project_tag": DEAL_ID,
        "agent_id": profile_id,
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "private_hits": 1,
        "milvus_used": True,
        "dual_kb_connected": True,
        "background_knowledge_refs": [{"ref_id": "KBREF-LEGAL-001"}],
        "physical_collections": {
            "siq_deal_shared": "ic_collaboration_shared",
            profile_id: physical_private,
        },
        "vector_retrieval": {
            "shared_filter_applied": True,
            "shared_project_tag": DEAL_ID,
            "retrieval_strategy": {
                "mode": "dense_bm25_rrf",
                "embedding_model": "Qwen3-VL-Embedding-2B",
            },
        },
        "rerank_ready": True,
        "rerank": {"status": "completed", "model": "Qwen3-VL-Reranker-2B"},
    }


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (lambda receipt: receipt.update(project_tag="DEAL-OTHER-001"), "shared_project_tag_mismatch"),
        (lambda receipt: receipt.update(dual_kb_connected=False), "dual_kb_not_connected"),
        (
            lambda receipt: receipt["physical_collections"].update(siq_deal_shared="wrong_shared"),
            "shared_physical_collection_mismatch",
        ),
        (
            lambda receipt: receipt["vector_retrieval"].update(shared_project_tag="DEAL-OTHER-001"),
            "deal_scoped_shared_filter_missing",
        ),
        (
            lambda receipt: receipt["vector_retrieval"]["retrieval_strategy"].update(mode="dense_only"),
            "hybrid_embedding_receipt_missing",
        ),
        (lambda receipt: receipt.update(rerank_ready=False), "reranker_not_ready"),
    ],
)
def test_formal_task_gate_rejects_unbound_or_incomplete_dual_kb_receipt(mutate, expected_reason):
    receipt = _formal_receipt()
    mutate(receipt)

    reasons = ic_agent_runtime._startup_receipt_gate_blocks(receipt)

    assert f"startup_receipt_gate_blocked:{expected_reason}" in reasons


def test_formal_task_gate_accepts_current_project_bound_hybrid_receipt():
    assert ic_agent_runtime._startup_receipt_gate_blocks(_formal_receipt()) == []


def test_legal_chat_uses_raw_query_for_control_then_runs_hermes_with_role_retrieval(monkeypatch):
    async def run_case():
        captured = {"controls": [], "retrieval": []}

        async def fake_quota(*_args, **_kwargs):
            return (0, None)

        async def fake_usage(*_args, **_kwargs):
            return None

        async def fake_resolve(*_args, **_kwargs):
            return "legal-session-001"

        async def fake_collect(message, _async_session, **kwargs):
            captured["hermes_message"] = message
            captured["hermes_kwargs"] = kwargs
            return "## 法务结论\n\n已进入法务 Hermes runtime，并基于项目证据列出待核验清单。"

        class SessionManager:
            def increment_message_count(self, session_id):
                captured["incremented"] = session_id

        def fake_control(message, profile):
            captured["controls"].append((message, profile))
            return None

        def fake_retrieve(deal_id, profile_id, **kwargs):
            captured["retrieval"].append({"deal_id": deal_id, "profile_id": profile_id, **kwargs})
            return _retrieval_payload()

        monkeypatch.setattr(primary_market_meeting, "enforce_quota_or_429_async", fake_quota)
        monkeypatch.setattr(primary_market_meeting, "record_usage_async", fake_usage)
        monkeypatch.setattr(primary_market_meeting, "resolve_or_create_meeting_session", fake_resolve)
        monkeypatch.setattr(primary_market_meeting, "collect_chat_reply", fake_collect)
        monkeypatch.setattr(primary_market_meeting, "maybe_handle_model_control", fake_control)
        monkeypatch.setattr(primary_market_meeting, "get_session_manager", lambda: SessionManager())
        monkeypatch.setattr(primary_market_meeting, "_evaluate_and_store_reply_quality", lambda **_kwargs: None)
        monkeypatch.setattr(
            primary_market_meeting,
            "_load_deal_summary",
            lambda deal_id, **_kwargs: {
                "deal_id": deal_id,
                "company_name": "Legal Robotics",
                "current_phase": "R2",
            },
        )
        monkeypatch.setattr(
            primary_market_meeting,
            "_receipt_context_for",
            lambda *_args: {"required": True, "present": False},
        )
        monkeypatch.setattr(
            primary_market_meeting,
            "_r1_report_context_for",
            lambda *_args: {"required": True, "present": False},
        )
        monkeypatch.setattr(primary_market_meeting.deal_retrieval, "retrieve_for_agent", fake_retrieve)

        enriched_message = "\n".join(
            [
                "你是法务合规委员，正在 SIQ 一级市场投研决策流程中发言。",
                "最近会议纪要：SIQ IC Legal Scanner 当前使用云端 StepFun。",
                "人类主持人问题：请基于当前项目列出法务合规尽调清单，并按投决影响排序。",
            ]
        )
        req = primary_market_meeting.PrimaryMarketMeetingChatRequest(
            message=enriched_message,
            retrieval_query="请基于当前项目列出法务合规尽调清单，并按投决影响排序。",
            display_message="@法务合规委员 合规清单",
            deal_id=DEAL_ID,
            lane=f"agent-{LEGAL_PROFILE}",
            context={
                "deal_id": DEAL_ID,
                "company_name": "Legal Robotics",
                "lane": f"agent-{LEGAL_PROFILE}",
                "agent": {"id": LEGAL_PROFILE, "label": "法务合规委员"},
            },
        )

        response = await primary_market_meeting.chat(
            LEGAL_PROFILE,
            req,
            current_user=_user(),
            async_session=SimpleNamespace(expunge=lambda _user: None),
        )

        assert response.reply.startswith("## 法务结论")
        assert captured["controls"] == [
            ("请基于当前项目列出法务合规尽调清单，并按投决影响排序。", LEGAL_PROFILE)
        ]
        assert len(captured["retrieval"]) == 1
        assert captured["retrieval"][0]["deal_id"] == DEAL_ID
        assert captured["retrieval"][0]["profile_id"] == LEGAL_PROFILE
        assert captured["retrieval"][0]["include_vector"] is True
        assert "ic_collaboration_shared" in captured["hermes_message"]
        assert "ic_legal_scanner" in captured["hermes_message"]
        assert "法律合规、股权结构、监管合规、知识产权" in captured["hermes_message"]
        assert "不做财务指标计算" in captured["hermes_message"]
        assert "严禁读取或引用 data/wiki/companies" in captured["hermes_message"]
        assert captured["hermes_kwargs"]["profile"] == LEGAL_PROFILE
        assert captured["hermes_kwargs"]["context"].domain == "primary_market"
        assert captured["hermes_kwargs"]["context"].deal_id == DEAL_ID
        assert captured["incremented"] == "legal-session-001"

    anyio.run(run_case)
