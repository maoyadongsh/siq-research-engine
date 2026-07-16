import json
from pathlib import Path

import pytest

from services import agent_runtime_financial_guard as guard, agent_runtime_financial_provenance as provenance


def _deps(**overrides):
    defaults = {
        "build_primary_data_evidence_supplement": lambda _message, _context: None,
        "merge_primary_data_refs_into_citations": lambda reply, supplement: f"{reply}\n\n{supplement}",
        "build_human_efficiency_evidence_context": lambda _message, _context: None,
        "build_three_statement_core_context": lambda _message, _context: None,
        "is_statement_query": lambda _message: False,
        "statement_metric_result": lambda _message, _context: (None, None),
        "should_inject_note_detail_context": lambda _message: False,
        "note_detail_result": lambda _message, _context, **_kwargs: (None, None),
        "build_wiki_fulltext_fallback_context": lambda _message, _context: None,
        "build_postgres_fallback_context": lambda _message, _context: None,
        "build_pdf2md_parse_only_context": lambda _message, _context: None,
        "is_runtime_status_reply": lambda reply: reply.lstrip().startswith("[失败]"),
        "invalid_task_ids_in_reply": lambda _message, _context, _reply: [],
        "needs_financial_evidence_contract": lambda _message, _context: True,
        "append_primary_data_evidence_if_needed": lambda _message, _context, reply: reply,
        "append_calculation_trace_warning_if_needed": lambda _message, reply: reply,
        "has_primary_data_evidence_trace": lambda reply: "source_type=wiki_metrics" in reply,
        "has_structured_evidence_trace": lambda reply: "source_type=" in reply,
    }
    defaults.update(overrides)
    return guard.FinancialEvidenceContractDependencies(**defaults)


def _yoy_trace(*, result: str = "0.02003764892559409", company_id: str = "HK:01398") -> str:
    return json.dumps(
        {
            "schema_version": "siq_financial_calculation_trace_v1",
            "tool": "financial_calculator.py",
            "operation": "yoy",
            "metric": "operating_revenue_yoy",
            "period": "2025",
            "inputs": {
                "current": {
                    "metric": "operating_revenue",
                    "period": "2025",
                    "value": "838270",
                    "unit": "RMB million",
                    "evidence_id": "EVID-REV-2025",
                },
                "previous": {
                    "metric": "operating_revenue",
                    "period": "2024",
                    "value": "821803",
                    "unit": "RMB million",
                    "evidence_id": "EVID-REV-2024",
                },
            },
            "result": {"rate": result, "percent": str(float(result) * 100)},
            "research_identity": {
                "market": "HK",
                "company_id": company_id,
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            },
        },
        ensure_ascii=False,
    )


def _reconciliation_trace(*, net: str = "1183", result: str = "1183") -> str:
    inputs = {}
    for role, metric, value, evidence_id in (
        ("gross", "goodwill_gross", "1282", "EVID-GW-GROSS"),
        ("allowance", "goodwill_impairment_allowance", "99", "EVID-GW-ALLOWANCE"),
        ("net", "goodwill_net", net, "EVID-GW-NET"),
    ):
        inputs[role] = {
            "metric": metric,
            "period": "2025",
            "value": value,
            "unit": "RMB million",
            "evidence_id": evidence_id,
        }
    return json.dumps(
        {
            "schema_version": "siq_financial_reconciliation_trace_v1",
            "tool": "financial_reconciliation_validator.py",
            "operation": "goodwill_reconciliation",
            "status": "passed",
            "metric": "goodwill_gross_allowance_net",
            "period": "2025",
            "inputs": inputs,
            "result": {"net": result},
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            },
        },
        ensure_ascii=False,
    )


def _reconciliation_evidence() -> str:
    lines = []
    for label, metric, value, evidence_id, quote in (
        ("P1", "goodwill_gross", "1282", "EVID-GW-GROSS", "商誉原值 1,282"),
        ("P2", "goodwill_impairment_allowance", "99", "EVID-GW-ALLOWANCE", "商誉减值准备 99"),
        ("P3", "goodwill_net", "1183", "EVID-GW-NET", "商誉净额 1,183"),
    ):
        lines.append(
            f"[{label}] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            f"canonical_name={metric} metric_name={metric} period_key=2025 value={value} "
            f'unit="RMB million" evidence_id={evidence_id} quote="{quote}" task_id=task-ok'
        )
    return "\n".join(lines)


def _trusted_reconciliation_evidence() -> tuple[dict, ...]:
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "run-hk-01398-2025",
    }
    records = []
    for metric, metric_name, aliases, value, evidence_id in (
        ("goodwill_gross", "商誉原值", ("商誉原值", "附注原值", "账面原值"), "1282", "EVID-GW-GROSS"),
        (
            "goodwill_impairment_allowance",
            "商誉减值准备",
            ("商誉减值准备", "减值准备"),
            "99",
            "EVID-GW-ALLOWANCE",
        ),
        ("goodwill_net", "商誉净额", ("商誉净额", "主表净额", "账面净值"), "1183", "EVID-GW-NET"),
    ):
        records.append(
            {
                "source_type": "trusted_wiki_table_cell",
                "metric": metric,
                "canonical_name": metric,
                "metric_name": metric_name,
                "aliases": aliases,
                "period": "2025",
                "period_key": "2025",
                "value": value,
                "raw_value": value,
                "unit": "RMB million",
                "evidence_id": evidence_id,
                "quote": f"{metric_name} {value}",
                "task_id": "task-recon",
                "pdf_page": 1,
                "table_index": 1,
                "financial_scope": "consolidated",
                **identity,
            }
        )
    return tuple(records)


def test_runtime_status_reply_is_not_warned():
    reply = "  [失败] run failed"

    assert guard._is_runtime_status_reply(reply)
    assert guard.append_calculation_trace_warning_if_needed("请计算人均营收", reply) == reply


def test_financial_evidence_fallback_prefers_primary_data_supplement():
    reply = guard.build_financial_evidence_fallback_reply(
        "收入是多少？",
        {"company": "AAPL"},
        deps=_deps(build_primary_data_evidence_supplement=lambda _message, _context: "source_type=wiki_metrics"),
    )

    assert reply is not None
    assert "模型本轮输出缺少主要数据级溯源" in reply
    assert "source_type=wiki_metrics" in reply


def test_financial_evidence_fallback_uses_statement_renderer_and_tolerates_exceptions():
    def renderer(_result, *, max_rows):
        return f"statement rows max={max_rows}"

    reply = guard.build_financial_evidence_fallback_reply(
        "资产负债表",
        None,
        deps=_deps(
            is_statement_query=lambda _message: True,
            statement_metric_result=lambda _message, _context: ({"rows": [1]}, renderer),
        ),
    )
    skipped = guard.build_financial_evidence_fallback_reply(
        "资产负债表",
        None,
        deps=_deps(
            is_statement_query=lambda _message: True,
            statement_metric_result=lambda _message, _context: (
                {"rows": [1]},
                lambda _result, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
            ),
            build_postgres_fallback_context=lambda _message, _context: "postgres rows",
        ),
    )

    assert "statement rows max=40" in reply
    assert "postgres rows" in skipped


def test_financial_tool_loop_reply_recovers_with_deterministic_evidence():
    raw_reply = (
        "I stopped retrying terminal because it hit the tool-call guardrail "
        "(same_tool_failure_halt) after 5 repeated non-progressing attempts."
    )

    reply = guard.recover_financial_tool_loop_reply(
        "分析美的集团的商誉",
        {"company": "美的集团"},
        raw_reply,
        deps=_deps(
            build_primary_data_evidence_supplement=lambda _message, _context: (
                "[D1] source_type=wiki_metrics, metric=商誉, value=34256859"
            ),
        ),
    )

    assert reply is not None
    assert "工具调用参数连续失败" in reply
    assert "后端已验证的原始事实" in reply
    assert "source_type=wiki_metrics" in reply
    assert "same_tool_failure_halt" not in reply
    assert "I stopped retrying terminal" not in reply


def test_financial_tool_loop_reply_returns_clean_status_without_evidence():
    raw_reply = "[Tool loop hard stop: terminal made no progress]"

    reply = guard.recover_financial_tool_loop_reply(
        "分析未知公司的商誉",
        None,
        raw_reply,
        deps=_deps(),
    )

    assert reply is not None
    assert "没有足够的已验证证据" in reply
    assert "Tool loop hard stop" not in reply
    assert guard.recover_financial_tool_loop_reply("分析商誉", None, "正常回答", deps=_deps()) is None


def test_enforce_financial_evidence_contract_returns_guarded_reply_when_auto_evidence_is_added():
    reply = guard.enforce_financial_evidence_contract(
        "收入是多少？",
        None,
        "收入增长。",
        deps=_deps(
            append_primary_data_evidence_if_needed=lambda _message, _context, _reply: (
                _reply + "\n[P1] source_type=wiki_metrics"
            ),
        ),
    )

    assert reply.endswith("[P1] source_type=wiki_metrics")


def test_enforce_financial_evidence_contract_blocks_unbacked_financial_fact():
    reply = guard.enforce_financial_evidence_contract(
        "上汽集团 2025 年营业收入是多少？",
        None,
        "上汽集团 2025 年营业收入是 100 亿元。",
        deps=_deps(),
    )

    assert "## 证据不足" in reply
    assert "不能确定" in reply
    assert "guardrail_status=blocked" in reply
    assert f"guardrail_reason={guard.FINANCIAL_EVIDENCE_MISSING_GUARDRAIL_REASON}" in reply
    assert "100 亿元" not in reply


def test_enforce_financial_evidence_contract_blocks_equal_value_from_wrong_identity():
    context = {
        "research_identity": {
            "market": "HK",
            "company_id": "HK:01398",
            "filing_id": "HK:01398:2025",
            "parse_run_id": "run-hk-2025",
        }
    }
    raw_reply = (
        "工商银行 2025 年营业收入为 8,382.70 亿元。\n"
        "[P1] source_type=wiki_metrics market=HK company_id=HK:WRONG filing_id=HK:01398:2025 "
        "parse_run_id=run-hk-2025 canonical_name=operating_revenue metric_name=营业收入 "
        'period_key=2025 value=8382.70 unit="亿元" currency=CNY '
        'evidence_id=EVID-REV-2025 quote="营业收入 838,270"'
    )

    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        context,
        raw_reply,
        deps=_deps(),
    )

    assert "## 财务证据身份不一致" in reply
    assert "reason=company_id_mismatch" in reply
    assert "guardrail_status=blocked" in reply
    assert f"guardrail_reason={guard.FINANCIAL_EVIDENCE_IDENTITY_MISMATCH_GUARDRAIL_REASON}" in reply
    assert raw_reply not in reply


def test_warn_mode_keeps_unbacked_financial_model_output(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")

    reply = guard.enforce_financial_evidence_contract(
        "上汽集团 2025 年营业收入是多少？",
        None,
        "上汽集团 2025 年营业收入是 100 亿元。",
        deps=_deps(),
    )

    assert "上汽集团 2025 年营业收入是 100 亿元。" in reply
    assert "## 证据不足" in reply
    assert "guardrail_status=warning" in reply


def test_warn_mode_keeps_derived_output_and_marks_missing_calculation(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")

    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        None,
        "工商银行 2025 年营业收入同比增长 2.0%。",
        deps=_deps(),
    )

    assert "同比增长 2.0%" in reply
    assert "## 计算校验缺失" in reply
    assert "guardrail_status=warning" in reply


def test_warn_mode_guard_application_is_idempotent(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")

    first = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        None,
        "工商银行 2025 年营业收入同比增长 2.0%。",
        deps=_deps(),
    )
    second = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        None,
        first,
        deps=_deps(),
    )

    assert second.count("## 计算校验缺失") == 1
    assert second.count("guardrail_status=warning") == 1


def test_warn_mode_keeps_identity_and_reference_diagnostics_but_blocks_proven_claim_mismatch(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")

    identity_reply = guard.enforce_financial_evidence_contract(
        "营业收入是多少？",
        {"research_identity": {"market": "US"}},
        "模型原始收入回答。",
        deps=_deps(),
    )
    invalid_reference_reply = guard.enforce_financial_evidence_contract(
        "营业收入是多少？",
        None,
        "模型原始引用回答。\n[P1] source_type=wiki_metrics task_id=missing",
        deps=_deps(invalid_task_ids_in_reply=lambda _message, _context, _reply: ["missing"]),
    )
    mismatch_reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 100 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "模型原始收入回答。" in identity_reply
    assert "guardrail_reason=financial_research_identity_incomplete" in identity_reply
    assert "模型原始引用回答。" in invalid_reference_reply
    assert "## 证据链无效" in invalid_reference_reply
    assert "营业收入为 100 亿元" not in mismatch_reply
    assert "guardrail_reason=financial_claim_mismatch" in mismatch_reply
    assert "guardrail_status=blocked" in mismatch_reply
    assert all("guardrail_status=warning" in reply for reply in (identity_reply, invalid_reference_reply))


def test_warn_mode_keeps_output_when_auto_evidence_adds_invalid_task_id(monkeypatch):
    monkeypatch.setenv("SIQ_FINANCIAL_GUARDRAIL_MODE", "warn")

    reply = guard.enforce_financial_evidence_contract(
        "营业收入是多少？",
        None,
        "模型原始收入回答。",
        deps=_deps(
            append_primary_data_evidence_if_needed=lambda _message, _context, original: (
                f"{original}\n[P1] source_type=wiki_metrics task_id=added-invalid"
            ),
            invalid_task_ids_in_reply=lambda _message, _context, candidate: (
                ["added-invalid"] if "added-invalid" in candidate else []
            ),
        ),
    )

    assert "模型原始收入回答。" in reply
    assert "## 证据链无效" in reply
    assert "guardrail_status=warning" in reply


@pytest.mark.parametrize("market", ("HK", "JP", "KR", "EU", "US"))
def test_enforce_financial_evidence_contract_blocks_incomplete_non_cn_identity(market):
    context = {"research_identity": {"market": market}}

    reply = guard.enforce_financial_evidence_contract(
        "2025 年营业收入是多少？",
        context,
        "2025 年营业收入为 100 亿元。",
        deps=_deps(),
    )

    assert reply.startswith("## 研究身份不完整")
    assert f"identity_market={market}" in reply
    assert "identity_missing_fields=company_id,filing_id,parse_run_id" in reply
    assert f"guardrail_reason={guard.FINANCIAL_RESEARCH_IDENTITY_INCOMPLETE_GUARDRAIL_REASON}" in reply
    assert "100 亿元" not in reply
    assert context["fallback_reason"] == "research_identity_incomplete"
    assert context["_audit_fallback_events"][-1] == {
        "reason": "research_identity_incomplete",
        "stage": "financial_answer_blocked_for_non_cn_market",
        "source": "research_identity_guard",
        "detail": f"market={market} missing=company_id,filing_id,parse_run_id",
    }


def test_enforce_financial_evidence_contract_does_not_block_non_financial_chat_for_incomplete_identity():
    context = {"research_identity": {"market": "HK"}}

    reply = guard.enforce_financial_evidence_contract(
        "你好，请介绍一下你自己。",
        context,
        "你好，我是 SIQ 助手。",
        deps=_deps(needs_financial_evidence_contract=lambda _message, _context: False),
    )

    assert reply == "你好，我是 SIQ 助手。"
    assert "_audit_fallback_events" not in context


def test_enforce_financial_evidence_contract_blocks_value_mismatch_with_valid_source():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 6,351.26 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "guardrail_status=blocked" in reply
    assert f"guardrail_reason={guard.FINANCIAL_CLAIM_MISMATCH_GUARDRAIL_REASON}" in reply
    assert "claim_verifier_status=failed" in reply
    assert "metric=operating_revenue" in reply
    assert "evidence=8382.7亿元" in reply


def test_enforce_financial_evidence_contract_blocks_period_mismatch_with_valid_source():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2024 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "guardrail_status=blocked" in reply
    assert "reason=period_mismatch" in reply
    assert "claimed_period=2024" in reply
    assert "period=2025" in reply


def test_enforce_financial_evidence_contract_allows_value_matching_source():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" not in reply
    assert "8,382.70 亿元" in reply
    assert "source_type=wiki_metrics" in reply


def test_enforce_financial_evidence_contract_allows_scaled_rmb_million_source():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" not in reply
    assert "8,382.70 亿元" in reply


def test_enforce_financial_evidence_contract_allows_explicit_scale_source():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit=RMB scale=1000000 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" not in reply
    assert "8,382.70 亿元" in reply


def test_enforce_financial_evidence_contract_allows_explicit_currency_match():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为人民币 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" not in reply
    assert "人民币 8,382.70 亿元" in reply


def test_enforce_financial_evidence_contract_blocks_currency_mismatch():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为人民币 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="HKD million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "reason=currency_mismatch" in reply
    assert "claimed_currency=CNY" in reply
    assert "evidence_currency=HKD" in reply


def test_enforce_financial_evidence_contract_blocks_matching_value_without_quote():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            "value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 task_id=task-ok"
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "reason=missing_quote" in reply
    assert "claim_verifier_status=failed" in reply


def test_enforce_financial_evidence_contract_allows_matching_value_with_reviewable_locator_without_quote():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            "value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 task_id=task-ok "
            "pdf_page=7 table_index=4 md_line=10"
        ),
        deps=_deps(),
    )

    assert "guardrail_reason=financial_claim_mismatch" not in reply
    assert "工商银行 2025 年营业收入为 8,382.70 亿元。" in reply


def test_enforce_financial_evidence_contract_blocks_matching_value_without_evidence_id():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            "value=8382.70 unit=亿元 quote=\"营业收入 838,270\" task_id=task-ok"
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "reason=missing_evidence_id" in reply
    assert "claim_verifier_status=failed" in reply


def test_enforce_financial_evidence_contract_blocks_matching_value_without_company_id():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "reason=missing_company_id" in reply
    assert "claim_verifier_status=failed" in reply


def test_enforce_financial_evidence_contract_blocks_matching_value_without_filing_id():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入是多少？",
        None,
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=8382.70 unit=亿元 evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "reason=missing_filing_id" in reply
    assert "claim_verifier_status=failed" in reply


def test_enforce_financial_evidence_contract_does_not_verify_source_line_only():
    reply = guard.enforce_financial_evidence_contract(
        "营业收入是多少？",
        None,
        (
            "## 引用来源\n"
            "[P1] source_type=wiki_metrics canonical_name=operating_revenue metric_name=营业收入 "
            "period_key=2025 value=8382.70 unit=亿元 evidence_id=EVID-REV-2025"
        ),
        deps=_deps(),
    )

    assert "## 财务数值证据不一致" not in reply
    assert "source_type=wiki_metrics" in reply


def test_enforce_financial_evidence_contract_blocks_invalid_task_id_with_fallback():
    reply = guard.enforce_financial_evidence_contract(
        "收入是多少？",
        None,
        "[P1] source_type=wiki_metrics, task_id=missing",
        deps=_deps(
            invalid_task_ids_in_reply=lambda _message, _context, _reply: ["missing"],
            build_postgres_fallback_context=lambda _message, _context: "postgres rows",
        ),
    )

    assert "## 证据链无效" in reply
    assert "missing" in reply
    assert "postgres rows" in reply


def test_enforce_financial_evidence_contract_blocks_derived_metric_without_calculator_trace():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        None,
        (
            "工商银行 2025 年营业收入同比增长 2.0%。\n\n"
            "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 计算校验缺失" in reply
    assert "guardrail_status=blocked" in reply
    assert f"guardrail_reason={guard.FINANCIAL_CALCULATION_TRACE_MISSING_GUARDRAIL_REASON}" in reply
    assert "calculation_trace_reason=calculator_trace_missing" in reply
    assert "同比增长 2.0%" not in reply


def test_enforce_financial_evidence_contract_allows_derived_metric_with_calculator_trace():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "工商银行 2025 年营业收入同比增长 2.0%。\n\n"
            "## 计算器校验\n"
            f"```json\n{_yoy_trace()}\n```\n\n"
            "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok\n'
            "[P2] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2024 "
            'value=821803 unit="RMB million" evidence_id=EVID-REV-2024 quote="营业收入 821,803" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 计算校验缺失" not in reply
    assert "同比增长 2.0%" in reply
    assert '"operation": "yoy"' in reply


def test_explicit_no_calculation_note_does_not_trigger_calculator_guard():
    reply = (
        "2025 年营业收入为 751,766。\n\n"
        "## 计算器校验\n"
        "- 本次只回答单指标，未计算同比，也未使用 financial_calculator.py。\n"
        "[D1] source_type=wiki_metrics company_id=HK:00700 filing_id=HK:00700:2025-annual "
        "parse_run_id=run-hk canonical_name=operating_revenue metric_name=营业收入 period=2025 "
        "value=751766 raw_value=751,766 unit=RMB million task_id=task-ok pdf_page=8 table_index=4"
    )

    guarded = guard.enforce_financial_evidence_contract(
        "HK TENCENT 2025 年营业收入是多少？不要计算同比。",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:00700",
                "filing_id": "HK:00700:2025-annual",
                "parse_run_id": "run-hk",
            }
        },
        reply,
        deps=_deps(),
    )

    assert "计算校验无效" not in guarded
    assert "financial_calculation_trace_missing" not in guarded


def test_explicit_not_applicable_calculation_note_does_not_trigger_calculator_guard():
    reply = (
        "2025 年总资产为 566,942,110。\n\n"
        "## 计算器校验\n"
        "不适用：本题仅披露主表原始值，未涉及人均、每股、同比、增长率、占比或 CAGR。\n"
        "[D1] source_type=wiki_metrics market=KR company_id=KR:005930 "
        "filing_id=KR:005930:2025-annual parse_run_id=run-kr "
        "canonical_name=total_assets metric_name=总资产 period=2025-12-31 "
        "value=566942110 raw_value=566,942,110 unit=KRW million currency=KRW "
        "task_id=task-ok pdf_page=83 table_index=67"
    )

    guarded = guard.enforce_financial_evidence_contract(
        "Samsung Electronics 2025 total assets?",
        {
            "research_identity": {
                "market": "KR",
                "company_id": "KR:005930",
                "filing_id": "KR:005930:2025-annual",
                "parse_run_id": "run-kr",
            }
        },
        reply,
        deps=_deps(),
    )

    assert "计算校验无效" not in guarded
    assert "financial_calculation_trace_missing" not in guarded


@pytest.mark.parametrize(
    "wording",
    (
        "营业收入 YoY 为 -1.26%。",
        "营业收入增幅为 1.26%。",
        "营业收入降幅为 1.26%。",
        "营业收入增长幅度为 1.26%。",
        "营业收入下降幅度为 1.26%。",
    ),
)
def test_yoy_wording_requires_yoy_operations(wording):
    assert guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset({"yoy", "yoy_growth"})


@pytest.mark.parametrize(
    "wording",
    (
        "商誉较 2024 年增长 15.8%。",
        "商誉较2024年下降2.2%。",
        "商誉上升约 3.0%。",
        "商誉减少 2.0％。",
        "营业收入与上年相比增加 100 万元。",
    ),
)
def test_contextual_change_wording_requires_yoy_operations(wording):
    assert guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset({"yoy", "yoy_growth"})


@pytest.mark.parametrize(
    "wording",
    (
        "处置子公司导致商誉减少 0.21 亿元。",
        "本期商誉增加 1.5 亿元。",
    ),
)
def test_ordinary_amount_change_does_not_require_yoy_operations(wording):
    assert not guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset()


def test_amount_change_followed_by_percentage_share_requires_only_ratio():
    wording = "商誉减少 0.21 亿元；占总资产的 0.2%。"

    assert guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset({"ratio"})


@pytest.mark.parametrize(
    "wording",
    (
        "华域视觉的商誉原值为 11.15 亿元，占商誉原值前两大组合的 86.91%。",
        "KUKA（商誉原值 8.77 亿元，占 68.4%）。",
        "研发资金占用率为 5.2%。",
        "该客户占销售额的 12 个百分点。",
    ),
)
def test_contextual_percentage_share_requires_ratio_operation(wording):
    assert guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset({"ratio"})


@pytest.mark.parametrize(
    "wording",
    (
        "存货占用资金 5 亿元。",
        "关联方占款 2 亿元。",
        "资金占用 5 亿元，利率为 3.2%。",
        "关联方占款 2 亿元；税率为 25%。",
    ),
)
def test_ordinary_occupancy_or_advance_does_not_require_ratio_operation(wording):
    assert not guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset()


def test_yoy_wording_in_evidence_reference_does_not_trigger_operations():
    reference = (
        "[P1] source_type=wiki_metrics metric_name=营业收入 "
        'quote="营业收入 YoY 降幅为 1.26%" evidence_id=EVID-REV-2025'
    )

    assert not guard._reply_has_derived_financial_metric(reference)
    assert guard._required_calculator_operations("", reference) == frozenset()


def test_yoy_wording_in_guardrail_diagnostic_does_not_trigger_operations():
    diagnostic = (
        "## 计算校验无效\n"
        "- 后端未能校验 YoY、增幅或下降幅度。\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_calculation_trace_missing"
    )

    assert not guard._reply_has_derived_financial_metric(diagnostic)
    assert guard._required_calculator_operations("", diagnostic) == frozenset()


def test_negated_yoy_wording_does_not_trigger_operations():
    note = "不适用：本题仅披露主表原始值，未涉及 YoY、增幅、降幅或增长幅度。"

    assert not guard._reply_has_derived_financial_metric(note)
    assert guard._required_calculator_operations("", note) == frozenset()


@pytest.mark.parametrize(
    "wording",
    (
        "营业收入同比增长 99%，未涉及 CAGR。",
        "未涉及 CAGR，但营业收入同比增长 99%。",
        "营业收入同比增长 99% 且未涉及 CAGR。",
    ),
)
def test_unrelated_negated_metric_does_not_hide_real_yoy_claim(wording: str):
    assert guard._reply_has_derived_financial_metric(wording)
    assert guard._required_calculator_operations("", wording) == frozenset(
        {"yoy", "yoy_growth"}
    )


def test_enforce_financial_evidence_contract_blocks_forged_99_percent_yoy_marker_trace():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "工商银行 2025 年营业收入同比增长 99.0%。\n\n"
            "## 计算器校验\n"
            "- financial_calculator.py operation=yoy current=838270 previous=821803 result=0.9900\n\n"
            "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 '
            'quote="营业收入 838,270" task_id=task-ok\n'
            "[P2] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2024 "
            'value=821803 unit="RMB million" evidence_id=EVID-REV-2024 '
            'quote="营业收入 821,803" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 计算校验无效" in reply
    assert "calculation_trace_reason=trace_unstructured" in reply
    assert "同比增长 99.0%" not in reply


@pytest.mark.parametrize(
    ("trace", "reason"),
    (
        (_yoy_trace(result="0.99"), "trace_result_mismatch"),
        (_yoy_trace().replace('"percent": "2.003764892559409"', '"percent": "99"'), "trace_result_mismatch"),
        (_yoy_trace(company_id="HK:WRONG"), "trace_identity_company_id_mismatch"),
        (_yoy_trace().replace('"operation": "yoy"', '"operation": "magic_growth"'), "trace_unknown_operation"),
        (_yoy_trace().replace('"period": "2025", "value": "838270"', '"value": "838270"'), "trace_input_fields_missing"),
        (_yoy_trace().replace('"value": "838270"', '"value": "999999"', 1), "trace_input_value_mismatch"),
        (_yoy_trace().replace('"unit": "RMB million"', '"unit": "USD million"', 1), "trace_input_currency_mismatch"),
    ),
)
def test_enforce_financial_evidence_contract_rejects_invalid_structured_yoy_trace(trace: str, reason: str):
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年营业收入同比是多少？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "工商银行 2025 年营业收入同比增长 2.0%。\n\n"
            f"## 计算器校验\n```json\n{trace}\n```\n\n"
            "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2025 "
            'value=838270 unit="RMB million" evidence_id=EVID-REV-2025 quote="营业收入 838,270" task_id=task-ok\n'
            "[P2] source_type=wiki_metrics market=HK company_id=HK:01398 "
            "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
            "canonical_name=operating_revenue metric_name=营业收入 period_key=2024 "
            'value=821803 unit="RMB million" evidence_id=EVID-REV-2024 quote="营业收入 821,803" task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 计算校验无效" in reply
    assert f"calculation_trace_reason={reason}" in reply


def test_enforce_financial_evidence_contract_blocks_ratio_trace_for_wrong_derived_metric():
    trace = json.dumps(
        {
            "schema_version": "siq_financial_calculation_trace_v1",
            "tool": "financial_calculator.py",
            "operation": "ratio",
            "metric": "debt_to_asset_ratio",
            "period": "2025",
            "inputs": {
                "numerator": {"metric": "gross_profit", "period": "2025", "value": "40", "unit": "HKD million", "evidence_id": "E-GP"},
                "denominator": {"metric": "revenue", "period": "2025", "value": "100", "unit": "HKD million", "evidence_id": "E-REV"},
            },
            "result": {"ratio": "0.4", "percent": "40"},
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            },
        }
    )
    reply = guard.enforce_financial_evidence_contract(
        "工商银行 2025 年毛利率是多少？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            f"2025 年毛利率为 40%。\n```json\n{trace}\n```\n"
            "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 filing_id=HK:01398:2025-annual "
            "parse_run_id=run-hk-01398-2025 canonical_name=gross_profit metric_name=gross_profit period_key=2025 "
            'value=40 unit="HKD million" evidence_id=E-GP quote="gross profit 40"\n'
            "[P2] source_type=wiki_metrics market=HK company_id=HK:01398 filing_id=HK:01398:2025-annual "
            "parse_run_id=run-hk-01398-2025 canonical_name=revenue metric_name=revenue period_key=2025 "
            'value=100 unit="HKD million" evidence_id=E-REV quote="revenue 100"'
        ),
        deps=_deps(),
    )

    # The prose-derived metric allowlist is intentionally relaxed, but the
    # trace still cannot label gross-profit/revenue inputs as debt/assets.
    assert "calculation_trace_reason=trace_input_metric_mismatch" in reply


def test_enforce_financial_evidence_contract_allows_recomputed_reconciliation_trace():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行商誉原值、减值准备和净额如何勾稽？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "2025 年商誉原值为 12.82 亿元，减值准备为 0.99 亿元，净额为 11.83 亿元。\n\n"
            f"## 勾稽校验\n```json\n{_reconciliation_trace()}\n```\n\n{_reconciliation_evidence()}"
        ),
        deps=_deps(),
    )

    assert "## 计算校验无效" not in reply
    assert "商誉原值为 12.82 亿元" in reply


def test_guard_does_not_mask_wrong_reconciliation_rhs_with_valid_equation():
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "run-hk-01398-2025",
    }
    reply = guard.enforce_financial_evidence_contract(
        "工商银行商誉原值、减值准备和净额如何勾稽？",
        {"research_identity": identity},
        (
            "附注原值 1,282 RMB million - 减值准备 99 RMB million = 999 RMB million。\n"
            "账面原值 1,282 RMB million - 减值准备 99 RMB million = 1,183 RMB million。\n"
            "[D1] source_type=wiki_metrics task_id=task-recon pdf_page=1 table_index=1"
        ),
        deps=_deps(),
        trusted_calculation_evidence=_trusted_reconciliation_evidence(),
    )

    assert "## 财务数值证据不一致" in reply
    assert "metric=goodwill_net" in reply
    assert "claimed=999" in reply
    assert "evidence=1183" in reply


def test_enforce_financial_evidence_contract_allows_compact_summary_with_trusted_tool_receipt():
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "run-hk-01398-2025",
    }
    trusted_receipt = {
        "status": "ok",
        "operation": "yoy",
        "input": {
            "current": "120",
            "current_unit": "HKD million",
            "current_currency": "HKD",
            "previous": "100",
            "previous_unit": "HKD million",
            "previous_currency": "HKD",
        },
        "result": {"rate": "0.2", "percent": "20"},
        "receipt_source": "hermes_session_tool",
        "receipt_tool_call_id": "call-yoy",
    }
    compact_reply = (
        "营业收入同比增长 20%。\n\n"
        "## 计算器校验\n- 同比：(120 - 100) / 100 = 20%，状态 ok。\n\n"
        "[P1] source_type=wiki_metrics market=HK company_id=HK:01398 "
        "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
        "canonical_name=operating_revenue metric_name=operating_revenue period_key=2025 "
        'value=120 unit="HKD million" evidence_id=E-REV-2025 quote="revenue 120" task_id=task-ok\n'
        "[P2] source_type=wiki_metrics market=HK company_id=HK:01398 "
        "filing_id=HK:01398:2025-annual parse_run_id=run-hk-01398-2025 "
        "canonical_name=operating_revenue metric_name=operating_revenue period_key=2024 "
        'value=100 unit="HKD million" evidence_id=E-REV-2024 quote="revenue 100" task_id=task-ok'
    )

    reply = guard.enforce_financial_evidence_contract(
        "营业收入同比是多少？",
        {"research_identity": identity},
        compact_reply,
        deps=_deps(),
        trusted_calculation_runs=(trusted_receipt,),
    )

    assert "## 计算校验无效" not in reply
    assert "siq_financial_calculation_trace_v1" not in reply
    assert "## 计算器校验" in reply


def test_enforce_financial_evidence_contract_blocks_reconciliation_input_mismatch():
    reply = guard.enforce_financial_evidence_contract(
        "工商银行商誉原值、减值准备和净额如何勾稽？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "2025 年商誉原值为 12.82 亿元，减值准备为 0.99 亿元，净额为 99 亿元。\n\n"
            f"## 勾稽校验\n```json\n{_reconciliation_trace(net='9900', result='9900')}\n```\n\n"
            f"{_reconciliation_evidence()}"
        ),
        deps=_deps(),
    )

    assert "## 计算校验无效" in reply
    assert "calculation_trace_reason=trace_input_value_mismatch" in reply


def test_enforce_financial_evidence_contract_blocks_failed_reconciliation_status():
    failed_trace = _reconciliation_trace().replace('"status": "passed"', '"status": "failed"')
    reply = guard.enforce_financial_evidence_contract(
        "工商银行商誉原值、减值准备和净额如何勾稽？",
        {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:01398",
                "filing_id": "HK:01398:2025-annual",
                "parse_run_id": "run-hk-01398-2025",
            }
        },
        (
            "2025 年商誉原值为 12.82 亿元，减值准备为 0.99 亿元，净额为 11.83 亿元。\n\n"
            f"## 勾稽校验\n```json\n{failed_trace}\n```\n\n{_reconciliation_evidence()}"
        ),
        deps=_deps(),
    )

    assert "calculation_trace_reason=trace_reconciliation_status_invalid" in reply


def test_enforce_financial_evidence_contract_allows_reported_eps_without_calculator_trace():
    reply = guard.enforce_financial_evidence_contract(
        "英伟达的基本每股收益是多少？",
        None,
        (
            "基本每股收益见下方结构化证据。\n\n"
            "[P1] source_type=wiki_metrics company_id=US:0001045810 filing_id=2026-10-K "
            "canonical_name=basic_eps metric_name=基本每股收益 period_key=2026 "
            'value=5.00 unit="USD/share" evidence_id=EVID-EPS-2026 '
            'evidence_source_type=sec_xbrl_fact source_anchor=f-501 task_id=task-ok'
        ),
        deps=_deps(),
    )

    assert "## 计算校验缺失" not in reply
    assert "基本每股收益见下方结构化证据" in reply


def test_detects_derived_financial_metric_case_insensitively():
    assert guard._reply_has_derived_financial_metric("未来三年 CAGR 为 8%")
    assert guard._reply_has_derived_financial_metric("人均营收为 120 万元/人")
    assert guard._reply_has_derived_financial_metric("ROE 为 12.5%，净息差为 1.42%")
    assert not guard._reply_has_derived_financial_metric("营业收入为 12 亿元")


def test_detects_cross_unit_amount_normalization_without_flagging_unrelated_amounts():
    reply = "比亚迪商誉为 4,427,571 千元（约 44.27571 亿元）。"

    assert guard._reply_has_derived_financial_metric(reply)
    assert guard._required_calculator_operations("查询比亚迪商誉", reply) == frozenset({"normalize_amount"})
    assert not guard._reply_has_derived_financial_metric("营业收入为 100 亿元，利润为 20 万元。")


def test_directly_reported_eps_does_not_require_calculator_trace():
    assert not guard._reply_has_derived_financial_metric("基本每股收益为 6.42 元。")
    assert not guard._reply_has_derived_financial_metric("EPS was 6.42.")
    assert guard._reply_has_derived_financial_metric("每股营收为 12.4 元。")


def test_detects_calculator_and_reconciliation_traces():
    assert guard._reply_has_calculator_trace("派生计算 operation=ratio")
    assert guard._reply_has_calculator_trace("## 计算器校验\n- ok")
    assert guard._reply_has_reconciliation_trace("## 勾稽校验\n- ok")
    assert guard._reply_has_reconciliation_trace("goodwill_reconciliation passed")
    assert not guard._reply_has_reconciliation_trace("商誉净额说明")


def test_detects_reconciliation_metric_only_with_subject_and_relation():
    assert guard._reply_has_reconciliation_metric("商誉原值 12.82 亿元，减值准备 0.99 亿元，净额 11.83 亿元")
    assert guard._reply_has_reconciliation_metric("坏账准备勾稽关系")
    assert not guard._reply_has_reconciliation_metric(
        "商誉减值准备计提 0.99 亿元。\n"
        "[1] source_type=wiki_metrics metric=商誉减值准备 value=0.99 unit=亿元"
    )
    assert not guard._reply_has_reconciliation_metric("原值、净额和账面价值")
    assert not guard._reply_has_reconciliation_metric("商誉说明")


def test_appends_calculator_warning_for_derived_metric_without_trace():
    guarded = guard.append_calculation_trace_warning_if_needed("请计算人均营收", "人均营收约为 120 万元/人。")

    assert "## 计算校验提示" in guarded
    assert "financial_calculator.py" in guarded


def test_does_not_append_warning_when_calculator_trace_exists():
    reply = "人均营收为 120 万元/人，派生计算（financial_calculator.py）：120000000 / 100 = 1200000 元/人。"

    assert guard.append_calculation_trace_warning_if_needed("请计算人均营收", reply) == reply


def test_requires_reconciliation_trace_even_when_calculator_trace_exists():
    reply = (
        "商誉原值 40.71 亿元，减值准备 0.18 亿元，净额 40.52 亿元。\n"
        "华安基金占比 99.49%（financial_calculator.py ratio）。"
    )

    guarded = guard.append_calculation_trace_warning_if_needed("请分析商誉原值、减值准备和净额", reply)

    assert "## 计算校验提示" in guarded
    assert "financial_reconciliation_validator.py" in guarded


def test_appends_tool_availability_correction_when_script_exists(monkeypatch, tmp_path):
    calculator = tmp_path / "financial_calculator.py"
    validator = tmp_path / "financial_reconciliation_validator.py"
    calculator.write_text("# calculator\n")
    validator.write_text("# validator\n")
    monkeypatch.setattr(guard, "FINANCIAL_CALCULATOR_PATH", calculator)
    monkeypatch.setattr(guard, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", validator)

    reply = "注：financial_calculator.py 和 financial_reconciliation_validator.py 当前不可用。"

    guarded = guard.append_financial_tool_availability_correction_if_needed(reply)

    assert "## 工具状态纠正" in guarded
    assert str(calculator) in guarded
    assert str(validator) in guarded


def test_does_not_append_tool_availability_correction_without_script(monkeypatch):
    monkeypatch.setattr(guard, "FINANCIAL_CALCULATOR_PATH", Path("/missing/financial_calculator.py"))
    monkeypatch.setattr(guard, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", Path("/missing/financial_reconciliation_validator.py"))
    reply = "注：financial_calculator.py 当前不可用。"

    assert guard.append_financial_tool_availability_correction_if_needed(reply) == reply


def test_runtime_wrapper_uses_impl_financial_tool_paths(monkeypatch, tmp_path):
    from services import agent_chat_runtime as runtime

    calculator = tmp_path / "financial_calculator.py"
    calculator.write_text("# calculator\n")
    monkeypatch.setattr(runtime, "FINANCIAL_CALCULATOR_PATH", calculator)
    monkeypatch.setattr(runtime, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", Path("/missing/financial_reconciliation_validator.py"))

    reply = "注：financial_calculator.py 当前不可用。"

    guarded = runtime.append_financial_tool_availability_correction_if_needed(reply)

    assert "## 工具状态纠正" in guarded
    assert str(calculator) in guarded


def test_financial_llm_provenance_records_required_fields_and_jsonl(tmp_path):
    provenance.RECENT_FINANCIAL_LLM_PROVENANCE.clear()
    record = provenance.build_financial_llm_provenance(
        provider="custom:test",
        model="test-model",
        model_input="Use evidence_id=EVID-1 to explain revenue.",
        output="Revenue explanation.",
        stored_output="Guarded revenue explanation.",
        context={"evidence": {"evidence_id": "EVID-1", "evidence_hash": "hash-a"}},
    )

    assert {
        "provider",
        "model",
        "prompt_version",
        "input_evidence_ids",
        "input_hash",
        "output_hash",
        "created_at",
    }.issubset(record)
    assert record["provider"] == "custom:test"
    assert record["model"] == "test-model"
    assert record["input_evidence_ids"] == ["EVID-1"]
    assert len(record["input_hash"]) == 64
    assert len(record["output_hash"]) == 64
    assert record["fact_trust_level"] == "evidence_bound_explanation"
    assert record["canonical_promotable"] is False
    assert record["output_was_guarded"] is True

    log_path = tmp_path / "financial_llm_provenance.jsonl"
    stored = provenance.record_financial_llm_provenance(record, log_path=log_path)

    assert provenance.RECENT_FINANCIAL_LLM_PROVENANCE[-1] == stored
    assert json.loads(log_path.read_text(encoding="utf-8").strip()) == stored


def test_financial_llm_provenance_without_evidence_is_candidate_only():
    record = provenance.build_financial_llm_provenance(
        provider="custom:test",
        model="test-model",
        model_input="Explain the company financials without citations.",
        output="Revenue increased.",
    )

    assert record["input_evidence_ids"] == []
    assert record["fact_trust_level"] == "candidate_explanation"
    assert record["canonical_promotable"] is False
    assert provenance.can_promote_financial_llm_output_to_canonical(record) is False


def test_record_financial_llm_provenance_if_needed_records_contract_output(tmp_path):
    profile_dir = tmp_path / "siq_assistant"
    profile_dir.mkdir()
    (profile_dir / "config.yaml").write_text(
        "model:\n  provider: custom:test\n  default: test-model\n",
        encoding="utf-8",
    )
    stored: list[dict] = []

    record = provenance.record_financial_llm_provenance_if_needed(
        message="营业收入是多少？",
        context={},
        profile="siq_assistant",
        profile_dirs={"siq_assistant": profile_dir},
        model_input={"message": "营业收入是多少？"},
        raw_output="营业收入是 100。",
        stored_output="## 证据不足\n不能确定。",
        is_runtime_status_reply=lambda _reply: False,
        needs_financial_evidence_contract=lambda _message, _context: True,
        record_provenance=lambda payload: stored.append(dict(payload)) or dict(payload),
    )

    assert record == stored[0]
    assert record["provider"] == "custom:test"
    assert record["model"] == "test-model"
    assert record["input_evidence_ids"] == []
    assert record["fact_trust_level"] == "candidate_explanation"
    assert record["output_was_guarded"] is True


def test_record_financial_llm_provenance_if_needed_skips_irrelevant_or_status_output():
    calls: list[dict] = []

    skipped_plain = provenance.record_financial_llm_provenance_if_needed(
        message="你好",
        context=None,
        profile="siq_assistant",
        model_input="你好",
        raw_output="你好，有什么可以帮你？",
        stored_output="你好，有什么可以帮你？",
        is_runtime_status_reply=lambda _reply: False,
        needs_financial_evidence_contract=lambda _message, _context: False,
        record_provenance=lambda payload: calls.append(dict(payload)) or dict(payload),
    )
    skipped_status = provenance.record_financial_llm_provenance_if_needed(
        message="收入是多少？",
        context={"evidence": {"evidence_id": "EVID-1"}},
        profile="siq_assistant",
        model_input="evidence_id=EVID-1",
        raw_output="[失败] run failed",
        stored_output="[失败] run failed",
        is_runtime_status_reply=lambda _reply: True,
        needs_financial_evidence_contract=lambda _message, _context: True,
        record_provenance=lambda payload: calls.append(dict(payload)) or dict(payload),
    )

    assert skipped_plain is None
    assert skipped_status is None
    assert calls == []


def test_financial_llm_cache_key_changes_when_evidence_hash_changes():
    base_key = "message-cache-key"
    context_a = {"evidence": {"evidence_id": "EVID-1", "evidence_hash": "hash-a"}}
    context_b = {"evidence": {"evidence_id": "EVID-1", "evidence_hash": "hash-b"}}

    key_a1 = provenance.financial_llm_cache_key(base_key, message="Explain revenue", context=context_a)
    key_a2 = provenance.financial_llm_cache_key(base_key, message="Explain revenue", context=context_a)
    key_b = provenance.financial_llm_cache_key(base_key, message="Explain revenue", context=context_b)

    assert key_a1 == key_a2
    assert key_a1 != key_b
    assert provenance.financial_llm_cache_key(base_key, message="Plain chat", context={}) == base_key


def test_runtime_dedupe_hash_includes_financial_evidence_hash():
    import pytest

    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    context_a = {"evidence": {"evidence_id": "EVID-1", "evidence_hash": "hash-a"}}
    context_b = {"evidence": {"evidence_id": "EVID-1", "evidence_hash": "hash-b"}}

    assert runtime._dedupe_hash_with_attachments("解释营收", context_a, []) == runtime._dedupe_hash_with_attachments(
        "解释营收",
        context_a,
        [],
    )
    assert runtime._dedupe_hash_with_attachments("解释营收", context_a, []) != runtime._dedupe_hash_with_attachments(
        "解释营收",
        context_b,
        [],
    )


@pytest.mark.parametrize(
    "reply",
    (
        "商誉净额占总资产比例：1,183,122,320.47 元 / 960,207,461,450.69 元约为 0.1232%。",
        "资产负债率 62.37%（负债 5989.02 亿元 / 资产 9602.07 亿元）。",
    ),
)
def test_required_operations_detects_long_contextual_ratios(reply: str):
    operations = guard._required_calculator_operations("分析偿债能力", reply)

    assert "ratio" in operations
