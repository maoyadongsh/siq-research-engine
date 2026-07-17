import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import anyio
from routers import agent_user_router
from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from schemas import ChatRequest
from services.auth_service import User, UserRole

from services import legal_workflow as workflow


def _user(user_id: int = 7) -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"User {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def _legal_hits() -> list[dict]:
    return [
        {
            "rank": 1,
            "source": "中华人民共和国公司法",
            "source_path": "/legal/中华人民共和国公司法_20231229.md",
            "chunk_index": 44,
            "text": "董事、监事、高级管理人员应当遵守法律、行政法规和公司章程，对公司负有忠实义务和勤勉义务。",
        },
        {
            "rank": 2,
            "source": "中华人民共和国证券法",
            "source_path": "/legal/中华人民共和国证券法_20191228.md",
            "chunk_index": 12,
            "text": "信息披露义务人披露的信息，应当真实、准确、完整，简明清晰，通俗易懂，不得有虚假记载、误导性陈述或者重大遗漏。",
        },
        {
            "rank": 3,
            "source": "上市公司信息披露管理办法",
            "source_path": "/legal/上市公司信息披露管理办法.md",
            "chunk_index": 7,
            "text": "上市公司及其他信息披露义务人应当及时依法履行信息披露义务，披露的信息应当真实、准确、完整。",
        },
        {
            "rank": 4,
            "source": "股票上市规则",
            "source_path": "/legal/上海证券交易所股票上市规则.md",
            "chunk_index": 31,
            "text": "上市公司发生的交易达到披露标准的，应当及时披露交易概述、交易对方、交易标的、定价依据和对公司的影响。",
        },
        {
            "rank": 5,
            "source": "上市公司治理准则",
            "source_path": "/legal/上市公司治理准则.md",
            "chunk_index": 18,
            "text": "上市公司应当建立健全内部控制制度，保证公司治理机制有效运行，维护公司和全体股东的合法权益。",
        },
    ]


def _annual_report_legal_hits() -> list[dict]:
    return [
        {
            "rank": 1,
            "source": "中华人民共和国证券法",
            "source_path": "/legal/中华人民共和国证券法_20191228.md",
            "chunk_index": 24,
            "text": "上市公司应当在每一会计年度结束之日起四个月内报送并公告年度报告。",
            "relevance": "年度报告披露时限与定期报告义务",
        },
        {
            "rank": 2,
            "source": "中华人民共和国证券法",
            "source_path": "/legal/中华人民共和国证券法_20191228.md",
            "chunk_index": 25,
            "text": "董事、监事和高级管理人员应当保证定期报告真实、准确、完整。",
            "relevance": "董监高对年度报告的书面确认义务",
        },
        {
            "rank": 3,
            "source": "上市公司信息披露管理办法",
            "source_path": "/legal/上市公司信息披露管理办法.md",
            "chunk_index": 12,
            "text": "年度报告应当记载公司基本情况、主要会计数据、公司治理和重大事项。",
            "relevance": "年度报告内容完整性审查",
        },
        {
            "rank": 4,
            "source": "上海证券交易所股票上市规则",
            "source_path": "/legal/上海证券交易所股票上市规则.md",
            "chunk_index": 31,
            "text": "上市公司应当及时披露达到标准的关联交易并履行相应审议程序。",
            "relevance": "上交所上市公司关联交易审议与披露",
        },
        {
            "rank": 5,
            "source": "中华人民共和国公司法",
            "source_path": "/legal/中华人民共和国公司法_20231229.md",
            "chunk_index": 35,
            "text": "上市公司设独立董事，并在董事会中设置审计委员会。",
            "relevance": "董事会、独立董事与审计委员会治理审查",
        },
        {
            "rank": 6,
            "source": "中华人民共和国公司法",
            "source_path": "/legal/中华人民共和国公司法_20231229.md",
            "chunk_index": 34,
            "text": "上市公司提供重大担保应当由股东会作出决议。",
            "relevance": "对外担保决策程序审查",
        },
        {
            "rank": 7,
            "source": "企业内部控制基本规范",
            "source_path": "/legal/企业内部控制基本规范.md",
            "chunk_index": 4,
            "text": "企业应当建立并有效实施内部控制，对内部控制有效性进行评价。",
            "relevance": "内部控制评价与审计意见审查",
        },
        {
            "rank": 8,
            "source": "中华人民共和国证券法",
            "source_path": "/legal/中华人民共和国证券法_20191228.md",
            "chunk_index": 26,
            "text": "涉及公司的重大诉讼、仲裁属于应当及时报送并公告的重大事件。",
            "relevance": "重大诉讼、仲裁及监管事项披露审查",
        },
    ]


def _irrelevant_annual_report_hits() -> list[dict]:
    return [
        {
            "rank": 90,
            "source": "中华人民共和国证券投资基金法",
            "source_path": "/legal/中华人民共和国证券投资基金法_20150424.md",
            "chunk_index": 1,
            "text": "为了规范证券投资基金活动，保护投资人及相关当事人的合法权益，制定本法。",
        },
        {
            "rank": 91,
            "source": "中华人民共和国证券投资基金法",
            "source_path": "/legal/中华人民共和国证券投资基金法_20150424.md",
            "chunk_index": 2,
            "text": "基金管理人和基金托管人依照本法和基金合同履行受托职责。",
        },
        {
            "rank": 92,
            "source": "汕头经济特区优化营商环境条例",
            "source_path": "/legal/汕头经济特区优化营商环境条例_20210928.md",
            "chunk_index": 9,
            "text": "汕头市有关部门应当优化不动产登记和融资服务。",
        },
        {
            "rank": 93,
            "source": "汕头经济特区优化营商环境条例",
            "source_path": "/legal/汕头经济特区优化营商环境条例_20210928.md",
            "chunk_index": 10,
            "text": "汕头市有关部门应当支持中小企业发展。",
        },
    ]


def _prepare_annual_legal_workflow(monkeypatch, tmp_path, hits: list[dict]) -> tuple[Path, Path]:
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-上汽集团"
    legal_dir = company_dir / "legal"
    legal_dir.mkdir(parents=True)

    def fake_retrieve(query, *, top_k=8, timeout=900):
        payload = {
            "ok": True,
            "query": query,
            "collection": "ic_legal_scanner",
            "results": hits,
        }
        return payload, subprocess.CompletedProcess(["legal_milvus_cli.py"], 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(
        workflow,
        "_resolve_company",
        lambda query: {
            "company_id": "600104-上汽集团",
            "stock_code": "600104",
            "company_short_name": "上汽集团",
            "company_full_name": "上海汽车集团股份有限公司",
            "company_path": "companies/600104-上汽集团",
        },
    )
    monkeypatch.setattr(workflow, "_retrieve_legal_sources", fake_retrieve)
    return company_dir, legal_dir


def _write_annual_report_fact_fixture(company_dir: Path) -> Path:
    report_path = company_dir / "reports" / "2025-annual" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        """# 上海汽车集团股份有限公司 2025 年年度报告

- 董事会由 11 名董事组成，其中独立董事 4 名。
- 德勤华永会计师事务所出具标准无保留意见审计报告。
- 2025 年日常关联交易实际发生金额未超过预计金额。
- 截至 2025 年 12 月 31 日，对外担保余额为 0.00 亿元。
- 内部控制审计报告为标准无保留意见。
- 营业收入 5,274.2 亿元，同比下降 21.75%。
""",
        encoding="utf-8",
    )
    return report_path


def test_legal_generation_intent_requires_explicit_artifact():
    assert workflow.is_legal_generation_request("请问上市公司关联交易披露有什么合规风险？") is False
    assert workflow.is_legal_generation_request("请生成HTML法律意见书：关联交易披露义务") is True
    assert workflow.is_legal_generation_request("请生成法律意见书初稿，不要生成HTML，直接在对话中输出") is False
    assert workflow.is_legal_generation_request("为什么法务助手没有固化 HTML？") is False


def test_build_legal_request_uses_current_company_context():
    request = workflow.build_legal_workflow_request(
        "请生成HTML法律意见书：关联交易披露义务",
        {"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
    )

    assert request is not None
    assert request.company_query == "600104-上汽集团"
    assert "关联交易披露义务" in request.topic
    assert request.jurisdiction == "中国大陆"


def test_annual_legal_workflow_autoroute_is_opt_in(monkeypatch):
    request = workflow.build_legal_workflow_request(
        "请对上汽集团2025年年报出具法律意见书",
        {"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
    )

    assert request is not None
    assert workflow.legal_workflow_autoroute_enabled(request) is False

    monkeypatch.setenv("SIQ_LEGAL_ANNUAL_WORKFLOW_AUTOROUTE", "1")
    assert workflow.legal_workflow_autoroute_enabled(request) is True

    monkeypatch.setenv("SIQ_LEGAL_WORKFLOW_AUTOROUTE", "0")
    assert workflow.legal_workflow_autoroute_enabled(request) is False


def test_build_legal_request_decodes_existing_source_report_url(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    source_report = (
        wiki_root
        / "companies"
        / "600104-上汽集团"
        / "legal"
        / "legal_opinion_20260522_144600.html"
    )
    source_report.parent.mkdir(parents=True)
    source_report.write_text("<html><body>历史法律意见</body></html>", encoding="utf-8")
    company_url_part = quote("600104-上汽集团", safe="")
    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)

    request = workflow.build_legal_workflow_request(
        "对年报出具法律意见",
        {
            "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
            "report": {
                "type": "legal",
                "filename": source_report.name,
                "url": f"/api/wiki/companies/{company_url_part}/legal/{source_report.name}",
            },
        },
    )

    assert request is not None
    assert request.report_path == source_report
    assert request.report_path.is_file()


def test_build_legal_request_rejects_missing_or_unsupported_source_report(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "600104-上汽集团"
    unsupported = company_dir / "legal" / "notes.txt"
    unsupported.parent.mkdir(parents=True)
    unsupported.write_text("not a legal report artifact", encoding="utf-8")
    company_url_part = quote("600104-上汽集团", safe="")
    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    base_context = {
        "company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"},
    }

    missing_request = workflow.build_legal_workflow_request(
        "对年报出具法律意见",
        {
            **base_context,
            "report": {
                "type": "legal",
                "filename": "missing.html",
                "url": f"/api/wiki/companies/{company_url_part}/legal/missing.html",
            },
        },
    )
    unsupported_request = workflow.build_legal_workflow_request(
        "对年报出具法律意见",
        {
            **base_context,
            "report": {
                "type": "legal",
                "filename": unsupported.name,
                "url": f"/api/wiki/companies/{company_url_part}/legal/{unsupported.name}",
            },
        },
    )

    assert missing_request is not None
    assert unsupported_request is not None
    assert missing_request.report_path is None
    assert unsupported_request.report_path is None


def test_annual_legal_opinion_consumes_source_report_fact_pack(monkeypatch, tmp_path):
    company_dir, _ = _prepare_annual_legal_workflow(monkeypatch, tmp_path, _annual_report_legal_hits())
    source_report = _write_annual_report_fact_fixture(company_dir)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="2025年年度报告合规审查",
            prompt="请对上汽集团2025年年报出具法律意见",
            report_path=source_report,
        )
    )

    assert response.result["ok"] is True
    html_text = Path(response.result["html_path"]).read_text(encoding="utf-8")
    assert "法律审查意见书" in html_text
    assert "文书编号：SIQ-LGL-AR" in html_text
    assert "事实覆盖" in html_text
    assert "出具主体：SIQ 法务合规智能体" in html_text
    assert "11 名董事" in html_text
    assert "独立董事 4 名" in html_text
    assert "德勤华永会计师事务所" in html_text
    assert "0.00 亿元" in html_text
    assert "21.75%" in html_text


def test_annual_legal_opinion_covers_review_dimensions_with_relevant_law(monkeypatch, tmp_path):
    company_dir, _ = _prepare_annual_legal_workflow(monkeypatch, tmp_path, _annual_report_legal_hits())
    source_report = _write_annual_report_fact_fixture(company_dir)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="2025年年度报告合规审查",
            prompt="请对上汽集团2025年年报出具法律意见",
            report_path=source_report,
        )
    )

    assert response.result["ok"] is True
    html_text = Path(response.result["html_path"]).read_text(encoding="utf-8")
    heading_text = " ".join(re.findall(r"<h[23][^>]*>(.*?)</h[23]>", html_text, re.DOTALL))
    expected_dimensions = {
        "定期报告与信息披露": ("定期报告", "年度报告", "信息披露"),
        "公司治理": ("公司治理", "董事会", "审计委员会"),
        "关联交易": ("关联交易",),
        "对外担保": ("对外担保", "担保"),
        "内部控制与审计": ("内部控制", "内控", "审计"),
    }
    missing_dimensions = [
        name
        for name, aliases in expected_dimensions.items()
        if not any(alias in heading_text for alias in aliases)
    ]
    assert missing_dimensions == []

    manifest = json.loads(Path(response.result["manifest_path"]).read_text(encoding="utf-8"))
    citation_sources = {item["source"] for item in manifest["citations"]}
    assert "中华人民共和国证券法" in citation_sources
    assert "上市公司信息披露管理办法" in citation_sources
    assert "上海证券交易所股票上市规则" in citation_sources
    assert "中华人民共和国公司法" in citation_sources
    assert "企业内部控制基本规范" in citation_sources


def test_annual_legal_opinion_filters_low_relevance_laws(monkeypatch, tmp_path):
    hits = [*_annual_report_legal_hits(), *_irrelevant_annual_report_hits()]
    company_dir, _ = _prepare_annual_legal_workflow(monkeypatch, tmp_path, hits)
    source_report = _write_annual_report_fact_fixture(company_dir)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="2025年年度报告合规审查",
            prompt="请对上汽集团2025年年报出具法律意见",
            report_path=source_report,
        )
    )

    assert response.result["ok"] is True
    manifest = json.loads(Path(response.result["manifest_path"]).read_text(encoding="utf-8"))
    citation_sources = {item["source"] for item in manifest["citations"]}
    assert "中华人民共和国证券投资基金法" not in citation_sources
    assert "汕头经济特区优化营商环境条例" not in citation_sources


def test_annual_legal_opinion_blocks_only_low_relevance_laws(monkeypatch, tmp_path):
    company_dir, legal_dir = _prepare_annual_legal_workflow(
        monkeypatch,
        tmp_path,
        _irrelevant_annual_report_hits(),
    )
    source_report = _write_annual_report_fact_fixture(company_dir)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="2025年年度报告合规审查",
            prompt="请对上汽集团2025年年报出具法律意见",
            report_path=source_report,
        )
    )

    assert response.result["ok"] is False
    assert "citation" in str(response.result.get("stage") or "") or "relevance" in str(response.result)
    assert not list(legal_dir.glob("*.html"))


def test_run_legal_workflow_writes_validated_html_and_manifest(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    legal_dir = wiki_root / "companies" / "600104-上汽集团" / "legal"
    legal_dir.mkdir(parents=True)

    def fake_retrieve(query, *, top_k=8, timeout=900):
        payload = {
            "ok": True,
            "query": query,
            "collection": "ic_legal_scanner",
            "results": _legal_hits(),
        }
        return payload, subprocess.CompletedProcess(["legal_milvus_cli.py"], 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(
        workflow,
        "_resolve_company",
        lambda query: {
            "company_id": "600104-上汽集团",
            "stock_code": "600104",
            "company_short_name": "上汽集团",
            "company_full_name": "上海汽车集团股份有限公司",
            "company_path": "companies/600104-上汽集团",
        },
    )
    monkeypatch.setattr(workflow, "_retrieve_legal_sources", fake_retrieve)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="关联交易披露义务",
            prompt="请生成HTML法律意见书：关联交易披露义务",
        )
    )

    assert response.result["ok"] is True
    html_path = Path(response.result["html_path"])
    manifest_path = Path(response.result["manifest_path"])
    retrieval_path = Path(response.result["retrieval_path"])
    validation_path = Path(response.result["validation_path"])
    assert html_path.exists()
    assert manifest_path.exists()
    assert retrieval_path.exists()
    assert validation_path.exists()
    assert response.result["validation"]["ok"] is True
    assert "不替代执业律师" in html_path.read_text(encoding="utf-8")
    assert "法律意见书" in html_path.read_text(encoding="utf-8")
    assert "文书编号：SIQ-LGL" in html_path.read_text(encoding="utf-8")
    assert "出具主体：SIQ 法务合规智能体" in html_path.read_text(encoding="utf-8")
    assert "[1] source=中华人民共和国公司法" in html_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["company_code"] == "600104"
    assert manifest["company_dir"] == "600104-上汽集团"
    assert manifest["citation_count"] == 5
    assert "HTML 法律意见书" in response.reply
    assert response.result["artifact"]["artifact_type"] == "legal"
    assert response.result["artifact"]["validation_result"]["ok"] is True
    assert response.result["artifact"]["citations"][0]["source_type"] == "legal_corpus"
    assert response.result["audit_trace_id"].startswith("aat_")


def test_legal_contract_failure_keeps_html_in_drafts(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki"
    legal_dir = wiki_root / "companies" / "600104-上汽集团" / "legal"
    legal_dir.mkdir(parents=True)

    def fake_retrieve(query, *, top_k=8, timeout=900):
        payload = {"ok": True, "query": query, "results": _legal_hits()}
        return payload, subprocess.CompletedProcess(["legal_milvus_cli.py"], 0, stdout="", stderr="")

    monkeypatch.setattr(workflow, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(
        workflow,
        "_resolve_company",
        lambda query: {
            "company_id": "600104-上汽集团",
            "stock_code": "600104",
            "company_short_name": "上汽集团",
            "company_path": "companies/600104-上汽集团",
        },
    )
    monkeypatch.setattr(workflow, "_retrieve_legal_sources", fake_retrieve)
    monkeypatch.setattr(workflow, "citation_has_locator", lambda _citation: False)

    response = workflow.run_legal_workflow(
        workflow.LegalWorkflowRequest(
            company_query="600104-上汽集团",
            topic="关联交易披露义务",
            prompt="请生成HTML法律意见书：关联交易披露义务",
        )
    )

    assert response.result["ok"] is False
    assert response.result["stage"] == "contract_validation_failed"
    assert response.result["validation_result"]["failures"] == ["citations_traceable"]
    assert Path(response.result["draft_path"]).exists()
    assert Path(response.result["artifact_manifest_path"]).exists()
    assert not list(legal_dir.glob("*.html"))
    assert response.result["artifact"]["html_url"] == ""
    assert "质量校验: `需复核" in response.reply


def test_legal_chat_routes_regular_question_stays_on_chat(monkeypatch):
    calls = {"collect": 0, "workflow": 0}

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-legal-session"

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        return "基于现有事实，建议先核实交易金额和关联关系。"

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        raise AssertionError("ordinary legal Q&A should not generate HTML")

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_run_legal_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(SpecialistAgentConfig(prefix="/legal", tag="legal", profile="siq_legal"))
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请问上市公司关联交易披露有什么合规风险？",
                context={"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 1, "workflow": 0}
        assert "建议先核实" in payload.reply

    anyio.run(run_case)


def test_legal_chat_routes_annual_opinion_to_hermes_by_default(monkeypatch):
    calls = {"collect": 0, "workflow": 0}

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-legal-session"

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        return "已交由 Hermes 法务助手按原版 profile 生成年报法律意见书。"

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        raise AssertionError("annual legal opinions should use Hermes by default")

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    monkeypatch.delenv("SIQ_LEGAL_ANNUAL_WORKFLOW_AUTOROUTE", raising=False)
    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_run_legal_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(SpecialistAgentConfig(prefix="/legal", tag="legal", profile="siq_legal"))
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请对上汽集团2025年年报出具法律意见书",
                context={"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 1, "workflow": 0}
        assert "Hermes 法务助手" in payload.reply

    anyio.run(run_case)


def test_legal_chat_routes_explicit_html_generation_to_workflow(monkeypatch):
    calls = {"collect": 0, "workflow": 0}
    saved: list[tuple[str, str, str | None]] = []
    trace_id = "aat_1234567890abcdef1234567890abcdef"

    async def noop_quota(*args, **kwargs):
        return (1, None)

    async def noop_usage(*args, **kwargs):
        return None

    async def fake_resolve_session(*args, **kwargs):
        return "user-7-legal-session"

    async def fake_save_message(async_session, role, content, session_id, attachments=None, audit_trace_id=None):
        saved.append((role, content, audit_trace_id))
        return SimpleNamespace(id=len(saved), role=role, content=content, session_id=session_id)

    async def fake_collect_chat_reply(*args, **kwargs):
        calls["collect"] += 1
        raise AssertionError("explicit HTML legal opinion should use legal workflow")

    async def fake_workflow_reply(workflow_request):
        calls["workflow"] += 1
        assert workflow_request.company_query == "600104-上汽集团"
        assert "关联交易披露义务" in workflow_request.topic
        assert workflow_request.session_id == "user-7-legal-session"
        return SimpleNamespace(
            reply="已生成正式法务合规 HTML 意见书\n\n- 打开意见书: [HTML 法律意见书](/api/wiki/companies/600104/legal/opinion.html)",
            result={"artifact": {"artifact_type": "legal"}, "audit_trace_id": trace_id},
        )

    async def fake_record_workspace(*args, **kwargs):
        return {"workspace_synced": True}

    monkeypatch.setattr(agent_user_router, "enforce_quota_or_429_async", noop_quota)
    monkeypatch.setattr(agent_user_router, "record_usage_async", noop_usage)
    monkeypatch.setattr(agent_user_router, "resolve_or_create_session", fake_resolve_session)
    monkeypatch.setattr(agent_user_router, "save_message", fake_save_message)
    monkeypatch.setattr(agent_user_router, "collect_chat_reply", fake_collect_chat_reply)
    monkeypatch.setattr(agent_user_router, "_run_legal_workflow_reply", fake_workflow_reply)
    monkeypatch.setattr(agent_user_router, "_record_agent_workspace_artifact_background", fake_record_workspace)
    monkeypatch.setattr(
        agent_user_router,
        "get_session_manager",
        lambda: SimpleNamespace(increment_message_count=lambda session_id: None),
    )

    router = create_specialist_agent_router(SpecialistAgentConfig(prefix="/legal", tag="legal", profile="siq_legal"))
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/chat") and "POST" in route.methods)

    async def run_case():
        payload = await endpoint(
            ChatRequest(
                message="请生成HTML法律意见书：关联交易披露义务",
                context={"company": {"dir": "600104-上汽集团", "code": "600104", "name": "上汽集团"}},
            ),
            current_user=_user(),
            async_session=SimpleNamespace(),
        )

        assert calls == {"collect": 0, "workflow": 1}
        assert payload.reply.startswith("已生成正式法务合规 HTML 意见书")
        assert saved[0] == ("user", "请生成HTML法律意见书：关联交易披露义务", None)
        assert saved[1][0] == "assistant"
        assert saved[1][2] == trace_id
        assert payload.audit_trace_id == trace_id
        assert payload.artifact == {"artifact_type": "legal"}

    anyio.run(run_case)
