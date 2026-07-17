import importlib.util
import json
import types
from pathlib import Path

import anyio
from services.hermes_client import RunRuntimeMetadata

from services import agent_chat_runtime as runtime, agent_runtime_answer_audit as audit

WIKI_TASK_ID = "11111111-1111-1111-1111-111111111111"
POSTGRES_TASK_ID = "22222222-2222-2222-2222-222222222222"
REPO_ROOT = Path(__file__).resolve().parents[3]


class _StreamEvent:
    def __init__(self, event_type: str, text: str = "", *, runtime_metadata=None):
        self.type = event_type
        self.text = text
        self.tool = None
        self.preview = None
        self.duration = None
        self.error = None
        self.runtime = runtime_metadata


class _PydanticLikeContext:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, *, exclude_none: bool = False):
        return self.payload


def _load_financial_qa_benchmark_module():
    source = REPO_ROOT / "scripts" / "maintenance" / "run_financial_qa_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_financial_qa_benchmark_for_runtime_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_answer_audit_source_field_parser_ignores_xbrl_assignments_inside_quote():
    fields = audit._extract_source_fields(
        "[D1] source_type=wiki_metrics, scale=1, "
        'quote="<ix:nonFraction id=\'f-78\' scale=\'6\'>416,161</ix:nonFraction>", '
        "source_url=https://www.sec.gov/example.htm, source_anchor=f-78, xbrl_tag=us-gaap:Revenue"
    )

    assert fields["scale"] == "1"
    assert "scale='6'" in fields["quote"]
    assert fields["source_anchor"] == "f-78"
    assert fields["xbrl_tag"] == "us-gaap:Revenue"


def test_answer_audit_persists_secret_free_runtime_provenance():
    record = audit.build_answer_audit_trace(
        message="分析当前公司",
        final_reply="已完成。",
        runtime_provenance={
            "runtime_target": "openshell",
            "canary_run_id": "canary-0123456789ab",
            "authorization": "Bearer must-not-be-persisted",
            "base": "http://127.0.0.1:28651/v1/runs",
        },
    )

    assert record["runtime_provenance"] == {
        "runtime_target": "openshell",
        "canary_run_id": "canary-0123456789ab",
    }


def test_answer_audit_trace_extracts_wiki_source_and_guardrail_fields():
    reply = f"""营业收入同比提升，计算过程已复核。

## 计算器校验
- financial_calculator.py operation=ratio numerator=120 denominator=100

## 引用来源
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, statement_type=income_statement, metric=营业收入, canonical_name=revenue, period=2025, value=120, raw_value=120, unit=RMB million, currency=RMB, scale=1000000, task_id={WIKI_TASK_ID}, pdf_page=7, table_index=2, html_anchor=revenue-2025, evidence_id=evidence-1, md_line=50
"""
    context = {
        "question_id": "q-wiki-001",
        "company": {"name": "上汽集团", "code": "600104"},
        "query_plan": {"mode": "wiki_first"},
    }

    record = audit.build_answer_audit_trace(
        message="上汽集团 2025 年营业收入同比是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-wiki",
        raw_reply="营业收入同比提升。",
        final_reply=reply,
    )

    assert record["question_id"] == "q-wiki-001"
    assert record["resolved_company"] == {"name": "上汽集团", "code": "600104"}
    assert record["resolved_period"] == {"period": "2025"}
    assert record["wiki_facts"][0]["source_type"] == "wiki_metrics"
    assert record["wiki_facts"][0]["metric"] == "营业收入"
    assert record["wiki_facts"][0]["metric_name"] == "营业收入"
    assert record["wiki_facts"][0]["statement_type"] == "income_statement"
    assert record["wiki_facts"][0]["canonical_name"] == "revenue"
    assert record["wiki_facts"][0]["value"] == "120"
    assert record["wiki_facts"][0]["raw_value"] == "120"
    assert record["wiki_facts"][0]["unit"] == "RMB million"
    assert record["wiki_facts"][0]["currency"] == "RMB"
    assert record["wiki_facts"][0]["scale"] == "1000000"
    assert record["wiki_facts"][0]["task_id"] == WIKI_TASK_ID
    assert record["wiki_facts"][0]["source_page"] == "7"
    assert record["wiki_facts"][0]["html_anchor"] == "revenue-2025"
    assert record["wiki_facts"][0]["evidence_id"] == "evidence-1"
    assert record["postgres_facts"] == []
    assert record["query_plan"]["mode"] == "wiki_first"
    assert record["query_plan"]["observed_source_types"] == ["wiki_metrics"]
    assert record["calculator_runs"][1]["operation"] == "ratio"
    assert record["guardrail_result"]["blocked"] is False
    assert record["guardrail_result"]["allowed"] is True
    assert record["guardrail_result"]["has_calculator_runs"] is False
    assert record["guardrail_result"]["output_was_guarded"] is True
    assert record["guardrail_result"]["has_wiki_facts"] is True


def test_answer_audit_trace_marks_legacy_calculator_marker_as_unvalidated():
    record = audit.build_answer_audit_trace(
        message="营业收入同比是多少？",
        context=None,
        profile="siq_assistant",
        session_id="legacy-marker",
        raw_reply="## 计算器校验\n- financial_calculator.py operation=yoy result=0.99",
        final_reply="## 计算校验无效\ncalculation_trace_reason=trace_unstructured",
    )

    assert record["calculation_trace_validation"] == {
        "checked": True,
        "allowed": False,
        "reason": "trace_unstructured",
        "structured_run_count": 0,
    }
    assert all(item["source"] != "reply_structured" for item in record["calculator_runs"])


def test_answer_audit_trace_records_only_validated_structured_run_as_calculator_run():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    trace = {
        "schema_version": "siq_financial_calculation_trace_v1",
        "tool": "financial_calculator.py",
        "operation": "ratio",
        "metric": "gross_margin",
        "period": "2025",
        "inputs": {
            "numerator": {"metric": "gross_profit", "period": "2025", "value": "40", "unit": "HKD million", "evidence_id": "E-GP"},
            "denominator": {"metric": "revenue", "period": "2025", "value": "100", "unit": "HKD million", "evidence_id": "E-REV"},
        },
        "result": {"ratio": "0.4", "percent": "40"},
        "research_identity": identity,
    }
    reply = (
        f"毛利率为 40%。\n```json\n{json.dumps(trace)}\n```\n"
        "[D1] source_type=wiki_metrics market=HK company_id=HK:00700 filing_id=HK:00700:2025-annual "
        "parse_run_id=run-hk-00700 canonical_name=gross_profit metric_name=gross_profit period_key=2025 "
        'value=40 unit="HKD million" evidence_id=E-GP quote="gross profit 40"\n'
        "[D2] source_type=wiki_metrics market=HK company_id=HK:00700 filing_id=HK:00700:2025-annual "
        "parse_run_id=run-hk-00700 canonical_name=revenue metric_name=revenue period_key=2025 "
        'value=100 unit="HKD million" evidence_id=E-REV quote="revenue 100"'
    )

    record = audit.build_answer_audit_trace(
        message="毛利率是多少？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="structured-run",
        raw_reply=reply,
        final_reply=reply,
    )

    structured = [item for item in record["calculator_runs"] if item["source"] == "reply_structured"]
    assert structured[0]["validated"] is True
    assert record["calculation_trace_validation"]["allowed"] is True
    assert record["guardrail_result"]["has_calculator_runs"] is True


def test_answer_audit_pure_reconciliation_does_not_require_calculator_trace():
    identity = {
        "market": "CN",
        "company_id": "000333-美的集团",
        "filing_id": "CN:000333-美的集团:2025-annual",
        "parse_run_id": "task-midea",
    }
    trace = {
        "schema_version": "siq_financial_reconciliation_trace_v1",
        "tool": "financial_reconciliation_validator.py",
        "operation": "goodwill_reconciliation",
        "metric": "goodwill_gross_allowance_net",
        "period": "2025-12-31",
        "inputs": {
            "gross": {"metric": "goodwill_gross", "period": "2025-12-31", "value": "34813270", "unit": "人民币千元", "evidence_id": "gross"},
            "allowance": {"metric": "goodwill_impairment_allowance", "period": "2025-12-31", "value": "556411", "unit": "人民币千元", "evidence_id": "allowance"},
            "net": {"metric": "goodwill_net", "period": "2025-12-31", "value": "34256859", "unit": "人民币千元", "evidence_id": "net"},
        },
        "result": {"net": "34256859"},
        "status": "passed",
        "research_identity": identity,
    }
    references = "\n".join(
        (
            f"[D1] source_type=wiki_metrics market=CN company_id={identity['company_id']} filing_id={identity['filing_id']} parse_run_id=task-midea canonical_name=goodwill_gross metric_name=goodwill_gross period_key=2025-12-31 value=34813270 unit=人民币千元 evidence_id=gross quote=原值",
            f"[D2] source_type=wiki_metrics market=CN company_id={identity['company_id']} filing_id={identity['filing_id']} parse_run_id=task-midea canonical_name=goodwill_impairment_allowance metric_name=goodwill_impairment_allowance period_key=2025-12-31 value=556411 unit=人民币千元 evidence_id=allowance quote=减值准备",
            f"[D3] source_type=wiki_metrics market=CN company_id={identity['company_id']} filing_id={identity['filing_id']} parse_run_id=task-midea canonical_name=goodwill_net metric_name=goodwill_net period_key=2025-12-31 value=34256859 unit=人民币千元 evidence_id=net quote=净值",
        )
    )
    reply = f"## 勾稽校验\n```json\n{json.dumps(trace, ensure_ascii=False)}\n```\n{references}"

    record = audit.build_answer_audit_trace(
        message="商誉原值、减值准备和净值如何勾稽？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="pure-reconciliation",
        raw_reply=reply,
        final_reply=reply,
    )

    assert record["calculation_trace_validation"]["allowed"] is True
    assert record["calculation_trace_validation"]["reason"] is None


def test_answer_audit_trace_keeps_full_trusted_json_behind_compact_summary():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    receipt = {
        "status": "ok",
        "operation": "ratio",
        "input": {
            "numerator": "40",
            "numerator_unit": "HKD million",
            "numerator_currency": "HKD",
            "denominator": "100",
            "denominator_unit": "HKD million",
            "denominator_currency": "HKD",
        },
        "result": {"ratio": "0.4", "percent": "40"},
        "receipt_source": "hermes_session_tool",
        "receipt_tool_call_id": "call-ratio",
    }
    reply = (
        "毛利率为 40%。\n\n## 计算器校验\n- 毛利率：40%，状态 ok。\n"
        "[D1] source_type=wiki_metrics market=HK company_id=HK:00700 filing_id=HK:00700:2025-annual "
        "parse_run_id=run-hk-00700 canonical_name=gross_profit metric_name=gross_profit period_key=2025 "
        'value=40 unit="HKD million" evidence_id=E-GP quote="gross profit 40"\n'
        "[D2] source_type=wiki_metrics market=HK company_id=HK:00700 filing_id=HK:00700:2025-annual "
        "parse_run_id=run-hk-00700 canonical_name=revenue metric_name=revenue period_key=2025 "
        'value=100 unit="HKD million" evidence_id=E-REV quote="revenue 100"'
    )

    record = audit.build_answer_audit_trace(
        message="毛利率是多少？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="trusted-summary",
        raw_reply=reply,
        final_reply=reply,
        trusted_calculation_runs=(receipt,),
    )

    trusted = [item for item in record["calculator_runs"] if item["source"] == "runtime_tool_receipt"]
    assert trusted[0]["validated"] is True
    assert trusted[0]["payload"]["schema_version"] == "siq_financial_calculation_trace_v1"
    assert trusted[0]["payload"]["receipt"]["receipt_tool_call_id"] == "call-ratio"
    assert record["calculation_trace_validation"]["allowed"] is True
    assert "siq_financial_calculation_trace_v1" not in reply


def test_answer_audit_validates_trusted_amount_normalization_without_legacy_heading():
    identity = {
        "market": "CN",
        "company_id": "002594-比亚迪",
        "filing_id": "CN:002594-比亚迪:2025-annual",
        "parse_run_id": "run-byd-2025",
    }
    receipt = {
        "status": "ok",
        "operation": "normalize_amount",
        "input": {"value": "4427571", "unit": "千元", "currency": "CNY"},
        "result": {
            "native_base_value": "4427571000",
            "native_100m_value": "44.27571",
            "cny_base_value": "4427571000",
            "cny_100m_value": "44.27571",
        },
        "receipt_source": "hermes_session_tool",
        "receipt_tool_call_id": "call-normalize",
    }
    reply = (
        "比亚迪 2025 年商誉为 4,427,571 千元（约 44.27571 亿元）。\n"
        "[D1] source_type=wiki_metrics market=CN company_id=002594-比亚迪 "
        "filing_id=CN:002594-比亚迪:2025-annual parse_run_id=run-byd-2025 "
        "canonical_name=goodwill metric_name=商誉 period_key=2025-12-31 "
        'value=4427571 unit=千元 evidence_id=E-BYD-GOODWILL quote="商誉 4,427,571" '
        f"task_id={WIKI_TASK_ID} pdf_page=123 table_index=108 md_line=3014"
    )

    record = audit.build_answer_audit_trace(
        message="比亚迪商誉是多少？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="trusted-normalize",
        raw_reply=reply,
        final_reply=reply,
        trusted_calculation_runs=(receipt,),
    )

    assert record["calculation_trace_validation"] == {
        "checked": True,
        "allowed": True,
        "reason": None,
        "structured_run_count": 1,
    }
    trusted = [item for item in record["calculator_runs"] if item["source"] == "runtime_tool_receipt"]
    assert trusted[0]["operation"] == "normalize_amount"
    assert trusted[0]["validated"] is True
    assert record["guardrail_result"]["has_calculator_runs"] is True


def test_answer_audit_resolves_empty_context_for_backend_evidence_recompute(monkeypatch):
    reply = (
        "美的集团商誉净额由 2024-12-31 的 29,581,014 千元增至 "
        "2025-12-31 的 34,256,859 千元，同比 +15.81%。\n\n"
        "## 计算器校验\n"
        "- financial_calculator.py yoy："
        "(34,256,859 - 29,581,014) / abs(29,581,014) = +15.81%。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_metrics, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=132, table_index=89, md_line=2497。\n"
        "[D2] source_type=wiki_document_links, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, "
        "pdf_page=206, table_index=163, md_line=4325。"
    )

    monkeypatch.setattr(
        runtime.agent_runtime_answer_audit,
        "record_answer_audit_trace_for_reply",
        lambda **kwargs: audit.build_answer_audit_trace(**kwargs),
    )

    record = runtime._record_answer_audit_trace_compat(
        message="分析美的集团的商誉",
        context=None,
        profile="siq_assistant",
        session_id="resolved-audit-context",
        raw_reply=reply,
        final_reply=reply,
    )

    backend_runs = [
        item for item in record["calculator_runs"] if item["source"] == "backend_evidence_recompute"
    ]
    assert record["resolved_company"]["id"] == "000333-美的集团"
    assert record["resolved_period"]["filing_id"] == "CN:000333-美的集团:2025-annual"
    assert record["calculation_trace_validation"]["checked"] is True
    assert record["calculation_trace_validation"]["allowed"] is True
    assert record["calculation_trace_validation"]["reason"] is None
    assert backend_runs
    assert {item["operation"] for item in backend_runs} == {"yoy"}
    assert all(item["validated"] is True for item in backend_runs)
    assert backend_runs[0]["payload"]["research_identity"] == {
        "market": "CN",
        "company_id": "000333-美的集团",
        "filing_id": "CN:000333-美的集团:2025-annual",
        "parse_run_id": "f4dead73-e0de-42b4-b1b7-d8cf217214ee",
    }


def test_answer_audit_trace_extracts_research_identity_context():
    context = {
        "research_identity": {
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "parse-hk-00700",
        },
        "company": {"name": "Tencent", "code": "00700"},
    }

    record = audit.build_answer_audit_trace(
        message="腾讯 2025 收入是多少？",
        context=runtime.agent_runtime_context.mutable_context_dict(context),
        profile="siq_assistant",
        session_id="session-research-identity",
        raw_reply="收入 100",
        final_reply=(
            "[D1] source_type=postgresql_agent_view, market=HK, company_id=HK:00700, "
            "filing_id=HK:00700:2025-annual, parse_run_id=parse-hk-00700, "
            "metric=收入, period=2025, value=100, unit=HKD million"
        ),
    )

    assert record["resolved_company"] == {
        "id": "HK:00700",
        "name": "Tencent",
        "code": "00700",
        "market": "HK",
    }
    assert record["resolved_period"]["filing_id"] == "HK:00700:2025-annual"
    assert record["resolved_period"]["parse_run_id"] == "parse-hk-00700"
    assert record["postgres_facts"][0]["company_id"] == "HK:00700"
    assert record["postgres_facts"][0]["parse_run_id"] == "parse-hk-00700"


def test_answer_audit_trace_extracts_postgresql_source_and_fallback_reason():
    reply = f"""PostgreSQL fallback 返回了商誉指标。

## 引用来源
[P1] source_type=postgresql, table=document_parser.financial_metrics, statement_id=stmt-1, statement_type=balance_sheet, filing_id=CN:600104:2025, report_id=annual-2025, metric=商誉, canonical_name=goodwill, period_key=2025FY, value=42, raw_value=42, unit=RMB million, currency=RMB, task_id={POSTGRES_TASK_ID}, pdf_page=88, table_index=12, md_line=500, bbox=10:20:30:40, quote_text=商誉42
"""
    context = {
        "resolved_company": {"id": "CN:600104", "name": "上汽集团", "stock_code": "600104"},
        "resolved_period": {"fiscal_year": "2025", "period_end": "2025-12-31"},
        "fallback_reason": "wiki_miss_then_postgres",
        "query_plan": {"mode": "postgres_fallback"},
    }

    record = audit.build_answer_audit_trace(
        message="上汽集团商誉是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-postgres",
        final_reply=reply,
    )

    assert record["fallback_reason"] == "wiki_miss_then_postgres"
    assert record["resolved_company"]["id"] == "CN:600104"
    assert record["postgres_facts"][0]["source_type"] == "postgresql"
    assert record["postgres_facts"][0]["table"] == "document_parser.financial_metrics"
    assert record["postgres_facts"][0]["statement_type"] == "balance_sheet"
    assert record["postgres_facts"][0]["metric_name"] == "商誉"
    assert record["postgres_facts"][0]["canonical_name"] == "goodwill"
    assert record["postgres_facts"][0]["value"] == "42"
    assert record["postgres_facts"][0]["filing_id"] == "CN:600104:2025"
    assert record["postgres_facts"][0]["bbox"] == "10:20:30:40"
    assert record["postgres_facts"][0]["source_page"] == "88"
    assert record["postgres_facts"][0]["quote"] == "商誉42"
    assert record["postgres_facts"][0]["period_key"] == "2025FY"
    assert record["resolved_period"]["fiscal_year"] == "2025"
    assert record["resolved_period"]["period_end"] == "2025-12-31"
    assert record["resolved_period"]["period_key"] == "2025FY"
    assert record["resolved_period"]["report_id"] == "annual-2025"
    assert record["resolved_period"]["filing_id"] == "CN:600104:2025"
    assert record["wiki_facts"] == []
    assert record["citations"][0]["label"] == "[P1]"
    assert record["guardrail_result"]["has_postgres_facts"] is True


def test_answer_audit_trace_records_claim_verifier_result_from_raw_reply():
    raw_reply = """工商银行 2025 年营业收入为 6,351.26 亿元。

## 引用来源
[P1] source_type=wiki_metrics, company_id=HK:01398, filing_id=2025-annual, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=8382.70, unit=亿元, evidence_id=EVID-REV-2025, quote="营业收入 838,270"
"""

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        profile="siq_assistant",
        session_id="session-claim-verifier",
        raw_reply=raw_reply,
        final_reply=(
            "## 财务数值证据不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["checked"] is True
    assert verifier["allowed"] is False
    assert verifier["claim_count"] == 1
    assert verifier["evidence_fact_count"] == 1
    assert verifier["violation_count"] == 1
    assert verifier["violations"][0]["metric"] == "operating_revenue"
    assert verifier["violations"][0]["claimed_value"] == 6351.26
    assert verifier["violations"][0]["evidence_value"] == 8382.7
    assert verifier["violations"][0]["evidence_id"] == "EVID-REV-2025"
    assert verifier["violations"][0]["evidence_quote"] == "营业收入 838,270"
    assert verifier["violations"][0]["reason"] == "value_mismatch"
    assert verifier["violations"][0]["claimed_period"] == "2025"
    delivered_verifier = record["delivered_claim_verifier_result"]
    assert delivered_verifier["allowed"] is True
    assert delivered_verifier["violation_count"] == 0


def test_answer_audit_does_not_reverify_numeric_values_in_guardrail_diagnostic(monkeypatch):
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk",
    }
    evidence = (
        {
            "source_type": "trusted_wiki_table_cell",
            "metric": "operating_revenue",
            "canonical_name": "operating_revenue",
            "metric_name": "营业收入",
            "aliases": ("营业收入", "operating_revenue"),
            "period": "2025",
            "value": "100",
            "unit": "RMB million",
            "currency": "RMB",
            "evidence_id": "E-REV",
            "quote": "营业收入 100",
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": identity["filing_id"],
            "parse_run_id": identity["parse_run_id"],
            "task_id": "run-hk",
            "pdf_page": "7",
            "table_index": "4",
            "md_line": "10",
        },
    )
    raw_reply = (
        "营业收入为 100 百万元。\n"
        "[D1] source_type=wiki_metrics, market=HK, company_id=HK:00700, "
        "filing_id=HK:00700:2025-annual, parse_run_id=run-hk, "
        "canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, "
        'value=100, unit="RMB million", evidence_id=E-REV, quote="营业收入 100", '
        "task_id=run-hk, pdf_page=7, table_index=4, md_line=10"
    )
    final_reply = (
        f"{raw_reply}\n\n"
        "## 财务数值证据不一致\n"
        "- mismatch_1: reason=value_mismatch metric=operating_revenue period=2025 "
        "claimed=999百万元 claimed_currency=unknown evidence=100人民币百万元 "
        "evidence_currency=RMB evidence_id=E-REV\n"
        "guardrail_status=warning\n"
        "guardrail_reason=financial_claim_mismatch\n"
        "claim_verifier_status=failed"
    )
    calculation_inputs: list[str] = []
    original_validate = audit.validate_calculation_traces

    def capture_calculation_input(reply, *args, **kwargs):
        calculation_inputs.append(reply)
        return original_validate(reply, *args, **kwargs)

    monkeypatch.setattr(audit, "validate_calculation_traces", capture_calculation_input)

    record = audit.build_answer_audit_trace(
        message="腾讯 2025 年营业收入是多少？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="diagnostic-claim-reverify",
        raw_reply=raw_reply,
        final_reply=final_reply,
        trusted_calculation_evidence=evidence,
    )

    assert record["claim_verifier_result"]["claim_count"] == 1
    assert record["claim_verifier_result"]["violation_count"] == 0
    assert record["delivered_claim_verifier_result"]["claim_count"] == 1
    assert record["delivered_claim_verifier_result"]["allowed"] is True
    assert record["delivered_claim_verifier_result"]["violation_count"] == 0
    assert calculation_inputs
    assert all("mismatch_1" not in item for item in calculation_inputs)


def test_answer_audit_does_not_materialize_calculator_run_from_guardrail_diagnostic():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk",
    }

    def evidence(period, value, evidence_id):
        return {
            "source_type": "trusted_wiki_table_cell",
            "metric": "operating_revenue",
            "canonical_name": "operating_revenue",
            "metric_name": "营业收入",
            "aliases": ("营业收入", "operating_revenue"),
            "period": period,
            "value": value,
            "unit": "RMB million",
            "currency": "RMB",
            "evidence_id": evidence_id,
            "quote": f"营业收入 {value}",
            **identity,
            "task_id": "run-hk",
            "pdf_page": "7",
            "table_index": "4",
            "md_line": "10",
        }

    final_reply = (
        "## 计算校验无效\n"
        "- 营业收入由 2024 年的 100 百万元增至 2025 年的 120 百万元，同比 20%。\n"
        "[D1] source_type=wiki_metrics, task_id=run-hk, pdf_page=7, table_index=4, md_line=10\n"
        "guardrail_status=blocked\n"
        "guardrail_reason=financial_calculation_trace_invalid"
    )

    record = audit.build_answer_audit_trace(
        message="腾讯营业收入同比是多少？",
        context={"research_identity": identity},
        profile="siq_assistant",
        session_id="diagnostic-calculator-run",
        final_reply=final_reply,
        trusted_calculation_evidence=(
            evidence("2024", "100", "E-REV-2024"),
            evidence("2025", "120", "E-REV-2025"),
        ),
    )

    assert record["calculator_runs"] == []
    assert record["calculation_trace_validation"]["checked"] is False
    assert record["calculation_trace_validation"]["structured_run_count"] == 0
    assert record["guardrail_result"]["has_calculator_runs"] is False


def test_answer_audit_trace_records_financial_evidence_identity_mismatch():
    raw_reply = """工商银行 2025 年营业收入为 8,382.70 亿元。

## 引用来源
[P1] source_type=wiki_metrics, market=HK, company_id=HK:WRONG, filing_id=HK:01398:2025, parse_run_id=run-hk-2025, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=8382.70, unit=亿元, evidence_id=EVID-REV-2025, quote="营业收入 838,270"
"""
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025",
        "parse_run_id": "run-hk-2025",
    }

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        context={"research_identity": identity},
        raw_reply=raw_reply,
        final_reply=(
            "## 财务证据身份不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_evidence_identity_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["allowed"] is False
    assert verifier["violations"][0]["reason"] == "company_id_mismatch"
    assert verifier["violations"][0]["company_id"] == "HK:WRONG"
    assert verifier["violations"][0]["expected_company_id"] == "HK:01398"
    assert record["guardrail_result"]["reason"] == "financial_evidence_identity_mismatch"


def test_answer_audit_trace_records_claim_verifier_period_mismatch():
    raw_reply = """工商银行 2024 年营业收入为 8,382.70 亿元。

## 引用来源
[P1] source_type=wiki_metrics, company_id=HK:01398, filing_id=2025-annual, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=8382.70, unit=亿元, evidence_id=EVID-REV-2025, quote="营业收入 838,270"
"""

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        profile="siq_assistant",
        session_id="session-claim-period-verifier",
        raw_reply=raw_reply,
        final_reply=(
            "## 财务数值证据不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["checked"] is True
    assert verifier["allowed"] is False
    assert verifier["violation_count"] == 1
    assert verifier["violations"][0]["reason"] == "period_mismatch"
    assert verifier["violations"][0]["claimed_period"] == "2024"
    assert verifier["violations"][0]["period"] == "2025"
    assert verifier["violations"][0]["claimed_value"] == 8382.70
    assert verifier["violations"][0]["evidence_value"] == 8382.70


def test_answer_audit_trace_records_claim_verifier_missing_quote():
    raw_reply = """工商银行 2025 年营业收入为 8,382.70 亿元。

## 引用来源
[P1] source_type=wiki_metrics, company_id=HK:01398, filing_id=2025-annual, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=8382.70, unit=亿元, evidence_id=EVID-REV-2025
"""

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        profile="siq_assistant",
        session_id="session-claim-missing-quote",
        raw_reply=raw_reply,
        final_reply=(
            "## 财务数值证据不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["checked"] is True
    assert verifier["allowed"] is False
    assert verifier["violation_count"] == 1
    assert verifier["violations"][0]["reason"] == "missing_quote"
    assert verifier["violations"][0]["evidence_id"] == "EVID-REV-2025"


def test_answer_audit_trace_records_claim_verifier_missing_company_id():
    raw_reply = """工商银行 2025 年营业收入为 8,382.70 亿元。

## 引用来源
[P1] source_type=wiki_metrics, filing_id=2025-annual, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=8382.70, unit=亿元, evidence_id=EVID-REV-2025, quote="营业收入 838,270"
"""

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        profile="siq_assistant",
        session_id="session-claim-missing-company",
        raw_reply=raw_reply,
        final_reply=(
            "## 财务数值证据不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["checked"] is True
    assert verifier["allowed"] is False
    assert verifier["violation_count"] == 1
    assert verifier["violations"][0]["reason"] == "missing_company_id"
    assert verifier["violations"][0]["filing_id"] == "2025-annual"


def test_answer_audit_trace_records_claim_verifier_currency_mismatch():
    raw_reply = """工商银行 2025 年营业收入为人民币 8,382.70 亿元。

## 引用来源
[P1] source_type=wiki_metrics, company_id=HK:01398, filing_id=2025-annual, canonical_name=operating_revenue, metric_name=营业收入, period_key=2025, value=838270, unit="HKD million", evidence_id=EVID-REV-2025, quote="营业收入 838,270"
"""

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入是多少？",
        profile="siq_assistant",
        session_id="session-claim-currency-mismatch",
        raw_reply=raw_reply,
        final_reply=(
            "## 财务数值证据不一致\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch\n"
            "claim_verifier_status=failed"
        ),
    )

    verifier = record["claim_verifier_result"]
    assert verifier["checked"] is True
    assert verifier["allowed"] is False
    assert verifier["violation_count"] == 1
    assert verifier["violations"][0]["reason"] == "currency_mismatch"
    assert verifier["violations"][0]["claimed_currency"] == "CNY"
    assert verifier["violations"][0]["evidence_currency"] == "HKD"


def test_answer_audit_trace_records_missing_calculation_trace_guardrail():
    final_reply = (
        "## 计算校验缺失\n"
        "- 后端检测到本轮回答涉及派生财务指标，但未检测到对应的确定性计算器 trace。\n\n"
        "guardrail_status=blocked\n"
        "guardrail_reason=financial_calculation_trace_missing\n"
        "calculation_trace_reason=calculator_trace_missing"
    )

    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入同比是多少？",
        profile="siq_assistant",
        session_id="session-missing-calculation-trace",
        raw_reply="工商银行 2025 年营业收入同比增长 2.0%。",
        final_reply=final_reply,
    )

    assert record["guardrail_result"]["blocked"] is True
    assert record["guardrail_result"]["reason"] == "financial_calculation_trace_missing"
    assert record["guardrail_result"]["calculation_warning_appended"] is True
    assert record["calculator_runs"] == []


def test_answer_audit_warning_marker_remains_allowed():
    record = audit.build_answer_audit_trace(
        message="工商银行 2025 年营业收入同比是多少？",
        profile="siq_assistant",
        session_id="session-calculation-warning",
        raw_reply="工商银行 2025 年营业收入同比增长 2.0%。",
        final_reply=(
            "工商银行 2025 年营业收入同比增长 2.0%。\n\n"
            "## 计算校验无效\n"
            "guardrail_status=warning\n"
            "guardrail_reason=financial_calculation_trace_missing"
        ),
    )

    result = record["guardrail_result"]
    assert result["status"] == "warning"
    assert result["blocked"] is False
    assert result["allowed"] is True
    assert result["reason"] == "financial_calculation_trace_missing"
    assert result["calculation_warning_appended"] is True


def test_answer_audit_trace_extracts_legal_corpus_citations_without_source_type():
    reply = """基于现有事实和检索结果，初步倾向认为该事项需要履行进一步核实程序。

## 引用来源
[1] source=中华人民共和国公司法, source_path=/legal/中华人民共和国公司法_20231229.md, chunk_index=44, quote="董事、监事、高级管理人员应当遵守法律、行政法规和公司章程", relevance=董事高管义务判断依据
[2] source=上市公司信息披露管理办法, source_path=/legal/上市公司信息披露管理办法.md, chunk_index=12, quote="信息披露义务人应当真实、准确、完整、及时地披露信息", relevance=披露义务判断依据
"""

    record = audit.build_answer_audit_trace(
        message="请评估该事项是否存在上市公司治理和信息披露风险。",
        context={"query_plan": {"mode": "legal_review"}},
        profile="siq_legal",
        session_id="session-legal",
        final_reply=reply,
    )

    assert record["query_plan"]["observed_source_types"] == ["legal_corpus"]
    assert len(record["legal_facts"]) == 2
    assert record["legal_facts"][0]["source_type"] == "legal_corpus"
    assert record["legal_facts"][0]["source"] == "中华人民共和国公司法"
    assert record["legal_facts"][0]["source_path"] == "/legal/中华人民共和国公司法_20231229.md"
    assert record["legal_facts"][0]["chunk_index"] == "44"
    assert record["legal_facts"][0]["quote"] == "董事、监事、高级管理人员应当遵守法律、行政法规和公司章程"
    assert record["legal_facts"][0]["relevance"] == "董事高管义务判断依据"
    assert record["citations"][1]["source"] == "上市公司信息披露管理办法"
    assert record["wiki_facts"] == []
    assert record["postgres_facts"] == []
    assert record["guardrail_result"]["has_legal_facts"] is True


def test_answer_audit_trace_preserves_grouped_numbers_and_pipe_quotes():
    reply = f"""收入引用行包含千分位和表格片段。

## 引用来源
| [D2] source_type=wiki_metrics | file=metrics/three_statements.json | metric=Revenue | canonical_name=revenue | period=2025 | value=751,766 | raw_value=751,766 | unit=HKD million | currency=HKD | quote=Revenues | 751,766 | 660,257 | task_id={WIKI_TASK_ID} | pdf_page=15 | table_index=4 | source_url=https://source.test/report?source_token=secret-token&format=html |
"""

    record = audit.build_answer_audit_trace(
        message="Revenue 是多少？",
        profile="siq_assistant",
        session_id="session-pipe-quote",
        final_reply=reply,
    )

    fact = record["wiki_facts"][0]
    citation = record["citations"][0]
    assert fact["value"] == "751,766"
    assert fact["raw_value"] == "751,766"
    assert fact["quote"] == "Revenues | 751,766 | 660,257"
    assert fact["unit"] == "HKD million"
    assert fact["currency"] == "HKD"
    assert citation["source_url"] == "https://source.test/report?source_token=[REDACTED]&format=html"
    assert "source_token" not in fact


def test_answer_audit_trace_prefers_structured_fallback_events():
    reply = "未直接展示数据库引用，但运行时记录了兜底阶段。"
    context = {
        "_audit_fallback_events": [
            {"reason": "wiki_structured_miss", "stage": "postgres_fallback_started"},
            {"reason": "postgres_unavailable", "stage": "legacy_postgres_exception"},
        ],
    }

    record = audit.build_answer_audit_trace(
        message="收入是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-fallback-events",
        final_reply=reply,
    )

    assert record["fallback_reason"] == "postgres_unavailable"
    assert record["fallback_events"][1]["stage"] == "legacy_postgres_exception"


def test_answer_audit_trace_prioritizes_incomplete_identity_block_reason():
    context = {
        "_audit_fallback_events": [
            {"reason": "wiki_fulltext_miss", "stage": "postgres_fallback_started"},
            {"reason": "market_boundary_closed", "stage": "legacy_fallback_skipped_for_non_cn_market"},
            {
                "reason": "research_identity_incomplete",
                "stage": "financial_answer_blocked_for_non_cn_market",
            },
        ],
    }

    record = audit.build_answer_audit_trace(
        message="收入是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-incomplete-identity",
        final_reply=(
            "## 研究身份不完整\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_research_identity_incomplete"
        ),
    )

    assert record["fallback_reason"] == "research_identity_incomplete"
    assert record["guardrail_result"]["reason"] == "financial_research_identity_incomplete"


def test_answer_audit_trace_prioritizes_exact_wiki_report_identity_mismatch():
    context = {
        "_audit_fallback_events": [
            {
                "reason": "research_identity_report_mismatch",
                "stage": "wiki_report_selector_failed",
                "detail": "parse_run_id_not_found",
            },
        ],
    }

    record = audit.build_answer_audit_trace(
        message="收入是多少？",
        context=context,
        profile="siq_assistant",
        session_id="session-wiki-identity-mismatch",
        final_reply="## 证据不足\nguardrail_status=blocked\nguardrail_reason=financial_evidence_missing",
    )

    assert record["fallback_reason"] == "research_identity_report_mismatch"
    assert record["fallback_events"][0]["detail"] == "parse_run_id_not_found"


def test_answer_audit_trace_redacts_database_urls_tokens_and_passwords():
    reply = f"""引用行带有需要脱敏的调试 URL。

## 引用来源
[P1] source_type=postgresql, table=financial_metrics, metric=收入, task_id={POSTGRES_TASK_ID}, pdf_page=1, table_index=2, md_line=3，[打开PDF页](https://source.test/page?source_token=reply-source-token&keep=1)
"""
    context = {
        "question_id": "qid-secret",
        "company": {"name": "测试公司", "password": "company-password"},
        "query_plan": {
            "database_url": "postgresql://postgres:context-password@db/siq",
            "token": "context-token",
            "safe": "kept",
        },
    }

    record = audit.build_answer_audit_trace(
        message=(
            "qid=message-q database_url=postgresql://user:message-password@db/siq "
            "token=message-token password=message-password direct=postgresql://reader:direct-password@db/siq"
        ),
        context=context,
        profile="siq_assistant",
        session_id="session-secret",
        final_reply=reply,
    )
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)

    for secret in (
        "company-password",
        "context-password",
        "context-token",
        "message-password",
        "message-token",
        "direct-password",
        "reply-source-token",
        "postgresql://user",
        "postgresql://reader",
    ):
        assert secret not in serialized
    assert audit.REDACTED_DATABASE_URL in serialized
    assert "source_token=[REDACTED]" in serialized
    assert record["query_plan"]["database_url"] == audit.REDACTED
    assert record["query_plan"]["token"] == audit.REDACTED
    assert record["query_plan"]["safe"] == "kept"


def test_record_answer_audit_trace_writes_jsonl(tmp_path):
    log_path = tmp_path / "audit" / "answer_audit_trace.jsonl"
    first = audit.build_answer_audit_trace(
        message="question_id=q-jsonl-1 收入是多少？",
        final_reply=f"[D1] source_type=wiki_metrics, metric=收入, task_id={WIKI_TASK_ID}, pdf_page=7",
        profile="siq_assistant",
        session_id="session-jsonl-1",
    )
    second = audit.build_answer_audit_trace(
        message="商誉是多少？",
        final_reply=f"[P1] source_type=postgresql, metric=商誉, task_id={POSTGRES_TASK_ID}, table_index=3",
        profile="siq_assistant",
        session_id="session-jsonl-2",
    )

    stored_first = audit.record_answer_audit_trace(first, log_path=log_path)
    stored_second = audit.record_answer_audit_trace(second, log_path=log_path)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert payloads == [stored_first, stored_second]
    assert payloads[0]["session_id"] == "session-jsonl-1"
    assert payloads[1]["postgres_facts"][0]["source_type"] == "postgresql"
    assert audit.is_answer_audit_trace_id(payloads[0]["trace_id"])
    assert payloads[0]["trace_id"] != payloads[1]["trace_id"]


def test_get_answer_audit_trace_reads_recent_and_jsonl_records(tmp_path):
    log_path = tmp_path / "audit" / "answer_audit_trace.jsonl"
    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    first = audit.record_answer_audit_trace(
        audit.build_answer_audit_trace(
            message="question_id=q-readable 收入是多少？",
            final_reply=f"[D1] source_type=wiki_metrics, metric=收入, task_id={WIKI_TASK_ID}, pdf_page=7",
            profile="siq_assistant",
            session_id="session-readable",
        ),
        log_path=log_path,
    )

    assert audit.get_answer_audit_trace(first["trace_id"], log_path=log_path) == first

    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    loaded = audit.get_answer_audit_trace(first["trace_id"], log_path=log_path)

    assert loaded == first
    assert audit.get_answer_audit_trace("bad-trace-id", log_path=log_path) is None


def test_render_answer_audit_summary_and_append_are_stable():
    record = {
        "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
        "question_id": "q-audit-ui-1",
        "query_plan": {"observed_source_types": ["wiki_metrics", "postgresql"]},
        "wiki_facts": [{"source_type": "wiki_metrics"}],
        "postgres_facts": [{"source_type": "postgresql"}],
        "citations": [{"label": "[D1]"}, {"label": "[P1]"}],
        "fallback_reason": "market_view_hit",
        "calculator_runs": [{"operation": "yoy"}],
        "guardrail_result": {"blocked": False},
    }

    summary = audit.render_answer_audit_summary(record)

    assert summary.startswith("## 审计详情")
    assert "trace_id: `aat_" in summary
    assert "question_id: `q-audit-ui-1`" in summary
    assert "source_counts: `wiki=1, postgres=1, citations=2`" in summary
    assert "fallback_reason: `market_view_hit`" in summary
    assert "calculator_runs: `1`" in summary
    assert "guardrail: `passed`" in summary
    assert "observed_sources: `wiki_metrics, postgresql`" in summary

    appended = audit.append_answer_audit_summary("最终回答", record)
    assert appended.endswith(summary)
    assert audit.append_answer_audit_summary(appended, record) == appended
    assert audit.append_answer_audit_summary("最终回答\n\n### 审计详情：\n- 已存在", record).count("审计详情") == 1
    assert audit.append_answer_audit_summary("   ", record) == "   "


def test_collect_chat_reply_records_answer_audit_after_non_stream_guard(monkeypatch):
    async def run_case():
        saved: list[tuple[str, str, str, str | None]] = []
        remembered: list[tuple[str, str, str | None, str]] = []
        provenance_calls: list[dict[str, object]] = []
        audit_calls: list[dict[str, object]] = []
        captured_audit_records: list[dict[str, object]] = []
        refreshed: list[tuple[str, str]] = []
        terminal_runtime = RunRuntimeMetadata(
            requested_model="siq_assistant",
            effective_provider="host-effective",
            effective_model="host-effective-model",
            configured_provider="host-configured",
            configured_model="host-configured-model",
            fallback_activated=False,
        )
        raw_reply = f"""最终回答

## 引用来源
[D1] source_type=wiki_metrics, metric=收入, canonical_name=revenue, period=2025, value=120, task_id={WIKI_TASK_ID}, pdf_page=7
"""

        async def fake_prepare_envelope(*_args, **_kwargs):
            return runtime.ChatRequestEnvelope(
                all_attachments=[{"name": "report.pdf"}],
                message_hash="hash-non-stream",
                user_display_message="收入是多少？\n\n[attachment: report.pdf]",
            )

        async def fake_preflight(*_args, **kwargs):
            return runtime.ChatRunPreflightContext(
                history=[],
                local_memory_context=None,
                attachments=kwargs["attachments"],
            )

        async def fake_save_message(_session, role, content, session_id, *, attachments=None, audit_trace_id=None):
            saved.append((
                role,
                content,
                session_id,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                audit_trace_id,
            ))

        async def fake_refresh(_session, profile, session_id):
            refreshed.append((profile, session_id))

        async def fake_analyze_images(*_args, **_kwargs):
            return None, True

        async def fake_wait_for_pdf_attachment_parses(_attachments):
            return None

        async def fake_create_run(run_input, history, *, profile, session_id):
            assert run_input["message"] == "收入是多少？"
            assert history == []
            assert profile == "siq_assistant"
            assert session_id == runtime.hermes_runs_session_id("siq_assistant", "audit-non-stream-session")
            return "run-audit-non-stream"

        async def fake_collect_run_result(run_id, *, profile, timeout):
            assert run_id == "run-audit-non-stream"
            assert profile == "siq_assistant"
            assert timeout == runtime.hermes_timeout()
            return raw_reply

        def fake_record_answer_audit_trace_for_reply(**kwargs):
            audit_calls.append(kwargs)
            return {
                "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
                "trace_id": "aat_1234567890abcdef1234567890abcdef",
                "question_id": "q-non-stream-audit",
                "wiki_facts": [{"source_type": "wiki_metrics"}],
                "postgres_facts": [],
                "citations": [{"label": "[D1]"}],
                "calculator_runs": [],
                "guardrail_result": {"blocked": False},
            }

        def fake_remember(profile, session_id, message_hash, reply):
            remembered.append((profile, session_id, message_hash, reply))

        def fake_provenance(**kwargs):
            provenance_calls.append(kwargs)

        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(
            runtime,
            "pop_run_terminal_result",
            lambda run_id: runtime.RunTerminalResult(
                run_id=run_id,
                status="succeeded",
                received_text=raw_reply,
                runtime=terminal_runtime,
            ),
        )
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", fake_provenance)
        monkeypatch.setattr(runtime.agent_runtime_answer_audit, "record_answer_audit_trace_for_reply", fake_record_answer_audit_trace_for_reply)

        session = types.SimpleNamespace()
        reply = await runtime._collect_chat_reply_impl(
            "收入是多少？",
            session,
            session_id="audit-non-stream-session",
            profile="siq_assistant",
            context={"question_id": "q-non-stream-audit"},
            enforce_evidence_contract=False,
            answer_audit_callback=captured_audit_records.append,
        )
        return raw_reply, reply, saved, refreshed, remembered, provenance_calls, audit_calls, captured_audit_records

    raw_reply, reply, saved, refreshed, remembered, provenance_calls, audit_calls, captured_audit_records = anyio.run(run_case)

    assert reply == raw_reply.strip()
    assert saved[0] == (
        "user",
        "收入是多少？\n\n[attachment: report.pdf]",
        "audit-non-stream-session",
        '[{"name": "report.pdf"}]',
        None,
    )
    assert saved[1] == (
        "assistant",
        reply,
        "audit-non-stream-session",
        None,
        "aat_1234567890abcdef1234567890abcdef",
    )
    assert refreshed == [("siq_assistant", "audit-non-stream-session")]
    assert remembered == [("siq_assistant", "audit-non-stream-session", "hash-non-stream", reply)]
    assert provenance_calls[0]["raw_output"] == raw_reply
    assert provenance_calls[0]["stored_output"] == reply
    assert provenance_calls[0]["terminal_runtime"].effective_provider == "host-effective"
    assert provenance_calls[0]["runtime_provenance"] == {"runtime_target": "host"}
    assert audit_calls[0]["raw_reply"] == raw_reply
    assert audit_calls[0]["final_reply"].rstrip() == reply
    assert audit_calls[0]["enforce_evidence_contract"] is False
    assert captured_audit_records[0]["trace_id"] == "aat_1234567890abcdef1234567890abcdef"


def test_collect_chat_reply_normalizes_context_before_fallback_audit_trace(monkeypatch, tmp_path):
    async def run_case():
        saved: list[tuple[str, str, str]] = []
        captured_records: list[dict[str, object]] = []
        raw_reply = "上汽集团 2025 年营业收入是 100 亿元。"
        context = _PydanticLikeContext(
            {
                "question_id": "q-context-normalized",
                "company": {"name": "上汽集团", "code": "600104"},
                "resolved_period": {"fiscal_year": "2025", "filing_id": "CN:600104:2025"},
            }
        )

        async def fake_prepare_envelope(*_args, **_kwargs):
            return runtime.ChatRequestEnvelope(
                all_attachments=[],
                message_hash="hash-context-normalized",
                user_display_message="上汽集团 2025 年营业收入是多少？",
            )

        async def fake_preflight(*_args, **kwargs):
            return runtime.ChatRunPreflightContext(
                history=[],
                local_memory_context=None,
                attachments=kwargs["attachments"],
            )

        async def fake_save_message(_session, role, content, session_id, **_kwargs):
            saved.append((role, content, session_id))

        async def fake_refresh(*_args, **_kwargs):
            return None

        async def fake_wait_for_pdf_attachment_parses(_attachments):
            return None

        async def fake_analyze_images(*_args, **_kwargs):
            return None, True

        async def fake_create_run(run_input, _history, *, profile, session_id):
            assert isinstance(run_input["context"], dict)
            assert run_input["context"]["company"]["name"] == "上汽集团"
            assert profile == "siq_assistant"
            assert session_id == runtime.hermes_runs_session_id("siq_assistant", "context-normalized-session")
            return "run-context-normalized"

        async def fake_collect_run_result(_run_id, *, profile, timeout):
            assert profile == "siq_assistant"
            assert timeout == runtime.hermes_timeout()
            return raw_reply

        def fake_postgres_fallback_context(_message, context):
            assert isinstance(context, dict)
            runtime.agent_runtime_postgres_fallback.record_postgres_fallback_event(
                context,
                reason="postgres_unavailable",
                stage="forced_postgres_miss",
            )
            return None

        monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(tmp_path / "answer_audit_trace.jsonl"))
        audit.RECENT_ANSWER_AUDIT_TRACES.clear()
        monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
        monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
        monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
        monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
        monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
        monkeypatch.setattr(runtime, "save_message", fake_save_message)
        monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh)
        monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
        monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
        monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
        monkeypatch.setattr(runtime, "create_run", fake_create_run)
        monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
        monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)
        monkeypatch.setattr(runtime, "_needs_financial_evidence_contract", lambda _message, _context: True)
        monkeypatch.setattr(runtime, "build_primary_data_evidence_supplement", lambda _message, _context: None)
        monkeypatch.setattr(runtime, "build_human_efficiency_evidence_context", lambda _message, _context: None)
        monkeypatch.setattr(runtime, "build_three_statement_core_context", lambda _message, _context: None)
        monkeypatch.setattr(runtime, "_is_statement_query", lambda _message: False)
        monkeypatch.setattr(runtime, "_should_inject_note_detail_context", lambda _message: False)
        monkeypatch.setattr(runtime, "build_wiki_fulltext_fallback_context", lambda _message, _context: None)
        monkeypatch.setattr(runtime, "build_postgres_fallback_context", fake_postgres_fallback_context)
        monkeypatch.setattr(runtime, "build_pdf2md_parse_only_context", lambda _message, _context: None)
        monkeypatch.setattr(runtime, "append_primary_data_evidence_if_needed", lambda _message, _context, reply: reply)
        monkeypatch.setattr(runtime, "append_calculation_trace_warning_if_needed", lambda _message, reply: reply)
        monkeypatch.setattr(runtime, "_has_primary_data_evidence_trace", lambda _reply: False)
        monkeypatch.setattr(runtime, "_has_structured_evidence_trace", lambda _reply: False)

        reply = await runtime._collect_chat_reply_impl(
            "上汽集团 2025 年营业收入是多少？",
            object(),
            session_id="context-normalized-session",
            profile="siq_assistant",
            context=context,
            enforce_evidence_contract=True,
            answer_audit_callback=captured_records.append,
        )
        return reply, saved, captured_records

    reply, saved, captured_records = anyio.run(run_case)
    record = captured_records[0]

    assert "## 证据不足" in reply
    assert saved[-1] == ("assistant", reply, "context-normalized-session")
    assert record["question_id"] == "q-context-normalized"
    assert record["resolved_company"] == {"name": "上汽集团", "code": "600104", "market": "CN"}
    assert record["resolved_period"]["fiscal_year"] == "2025"
    assert record["resolved_period"]["filing_id"] == "CN:600104:2025"
    assert record["fallback_reason"] == "postgres_unavailable"
    assert any(event["stage"] == "forced_postgres_miss" for event in record["fallback_events"])
    assert record["guardrail_result"]["blocked"] is True
    assert record["guardrail_result"]["allowed"] is False
    assert record["guardrail_result"]["reason"] == "financial_evidence_missing"


def test_live_runtime_answer_audit_trace_feeds_financial_qa_benchmark(monkeypatch, tmp_path):
    benchmark = _load_financial_qa_benchmark_module()
    case_root = tmp_path / "bench"
    trace_log = case_root / "traces.jsonl"
    case = {
        "schema_version": "siq_financial_qa_benchmark_case_v1",
        "case_id": "live-runtime-trace-1",
        "tier": "P0",
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "period": "2025-12-31",
        "question": "腾讯 2025 年收入是多少？",
        "source_policy": {
            "primary": "wiki_metrics",
            "allow_postgres_fallback": True,
            "allowed_fallback_reasons": ["wiki_missing"],
            "forbid_semantic_numeric_source": True,
        },
        "expected_facts": [
            {
                "canonical_name": "revenue",
                "statement_type": "income_statement",
                "period": "2025-12-31",
                "value": "100",
                "raw_value": "100",
                "unit": "RMB million",
                "currency": "RMB",
                "tolerance_ratio": 0,
                "required_source_types": ["wiki_metrics"],
                "fallback_source_types": ["postgresql_agent_view"],
                "required_evidence": ["table_index", "quote"],
            }
        ],
        "required_evidence": [{"table_index": 4, "quote": "Revenue 100"}],
        "expected_guardrail": {"should_answer": True},
        "expected_trace": {"must_have_wiki_facts": True, "fallback_reason": None},
    }
    raw_reply = (
        "[D1] source_type=wiki_metrics, company_id=HK:00700, filing_id=HK:00700:2025-annual, "
        "statement_type=income_statement, canonical_name=revenue, period=2025-12-31, "
        "value=100, raw_value=100, unit=RMB million, currency=RMB, table_index=4, quote=Revenue 100"
    )
    saved_messages: list[tuple[str, str, str]] = []
    captured_records: list[dict] = []

    async def fake_prepare_envelope(message, *_args, **_kwargs):
        return runtime.ChatRequestEnvelope(
            all_attachments=[],
            message_hash="hash-live-runtime-trace-1",
            user_display_message=message,
        )

    async def fake_preflight(*_args, **_kwargs):
        return runtime.ChatRunPreflightContext(history=[], local_memory_context=None, attachments=[])

    async def fake_save_message(_session, role, content, session_id, **_kwargs):
        saved_messages.append((role, content, session_id))

    async def fake_refresh(_session, _profile, _session_id):
        return None

    async def fake_wait_for_pdf_attachment_parses(_attachments):
        return None

    async def fake_analyze_images(*_args, **_kwargs):
        return None, True

    async def fake_create_run(run_input, history, *, profile, session_id):
        assert run_input["message"] == case["question"]
        assert history == []
        assert profile == "siq_assistant"
        assert session_id == runtime.hermes_runs_session_id("siq_assistant", "live-runtime-session")
        return "run-live-runtime-trace-1"

    async def fake_collect_run_result(run_id, *, profile, timeout):
        assert run_id == "run-live-runtime-trace-1"
        assert profile == "siq_assistant"
        assert timeout == runtime.hermes_timeout()
        return raw_reply

    monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(trace_log))
    audit.RECENT_ANSWER_AUDIT_TRACES.clear()
    monkeypatch.setattr(runtime, "_prepare_chat_request_envelope", fake_prepare_envelope)
    monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
    monkeypatch.setattr(runtime, "_load_chat_run_preflight_context", fake_preflight)
    monkeypatch.setattr(runtime, "wait_for_pdf_attachment_parses", fake_wait_for_pdf_attachment_parses)
    monkeypatch.setattr(runtime, "_attachments_with_fresh_metadata", lambda attachments: attachments)
    monkeypatch.setattr(runtime, "save_message", fake_save_message)
    monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh)
    monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images)
    monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda reply: reply)
    monkeypatch.setattr(runtime, "build_hermes_run_input", lambda message, **kwargs: {"message": message, **kwargs})
    monkeypatch.setattr(runtime, "create_run", fake_create_run)
    monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
    monkeypatch.setattr(runtime, "_remember_completed_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_record_financial_llm_provenance_if_needed", lambda **_kwargs: None)

    async def run_case():
        return await runtime._collect_chat_reply_impl(
            case["question"],
            object(),
            session_id="live-runtime-session",
            profile="siq_assistant",
            context={
                "question_id": case["case_id"],
                "company": {"market": "HK", "id": "HK:00700"},
                "resolved_period": {"period": "2025-12-31", "filing_id": "HK:00700:2025-annual"},
                "query_plan": {"mode": "wiki_first", "allow_postgres_fallback": True},
            },
            enforce_evidence_contract=False,
            answer_audit_callback=captured_records.append,
        )

    reply = anyio.run(run_case)
    _write_jsonl(case_root / "cases.jsonl", [case])

    report = benchmark.run_benchmark(case_root=case_root, trace_log=trace_log, mode="trace-offline")

    assert reply == raw_reply
    assert saved_messages == [
        ("user", case["question"], "live-runtime-session"),
        ("assistant", raw_reply, "live-runtime-session"),
    ]
    assert captured_records and captured_records[0]["question_id"] == "live-runtime-trace-1"
    assert trace_log.exists()
    assert report["passed"] is True
    assert report["results"][0]["facts"][0]["source_bucket"] == "wiki_facts"
    assert report["summary"]["key_fact_accuracy"] == 1.0


def test_collect_stream_run_records_answer_audit_without_changing_visible_reply(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="audit-stream-session",
            run_id="run-audit-stream",
        )
        state.original_message = "收入是多少？"
        state.context = {"question_id": "q-stream-audit"}
        saved: list[tuple[str, str, str, str]] = []
        remembered: list[tuple[str, str, str | None, str]] = []
        done_replies: list[str] = []
        provenance_calls: list[dict[str, object]] = []
        terminal_runtime = RunRuntimeMetadata(
            requested_model="siq_assistant",
            effective_provider="stream-effective",
            effective_model="stream-effective-model",
            configured_provider="stream-configured",
            configured_model="stream-configured-model",
            fallback_activated=False,
        )

        async def fake_stream_run(*_args, **_kwargs):
            yield _StreamEvent("delta", "最终回答")
            yield _StreamEvent("done", "最终回答", runtime_metadata=terminal_runtime)

        async def fake_save_message_in_background(role, content, session_id, *, profile, audit_trace_id=None):
            saved.append((role, content, session_id, profile, audit_trace_id))

        async def fake_done_payload(reply):
            done_replies.append(reply)
            return {"new_achievements": [], "reply_seen": reply}

        def fake_remember(profile, session_id, message_hash, reply):
            remembered.append((profile, session_id, message_hash, reply))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message_in_background)
        monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember)
        monkeypatch.setattr(
            runtime,
            "_record_financial_llm_provenance_if_needed",
            lambda **kwargs: provenance_calls.append(kwargs),
        )
        monkeypatch.setattr(
            runtime.agent_runtime_answer_audit,
            "record_answer_audit_trace_for_reply",
            lambda **_kwargs: {
                "schema_version": audit.ANSWER_AUDIT_TRACE_SCHEMA,
                "trace_id": "aat_fedcba0987654321fedcba0987654321",
                "question_id": "q-stream-audit",
                "wiki_facts": [],
                "postgres_facts": [],
                "citations": [],
                "calculator_runs": [],
                "guardrail_result": {"blocked": False},
            },
        )
        await runtime._collect_stream_run(
            state,
            fake_done_payload,
            enforce_evidence_contract=False,
            emit_audit_trace_id=True,
        )
        return state, saved, remembered, done_replies, provenance_calls

    state, saved, remembered, done_replies, provenance_calls = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["progress", "delta", "progress", "done"]
    assert state.content == "最终回答"
    assert state.done_payload is not None
    assert state.done_payload["content"] == "最终回答"
    assert state.done_payload["audit_trace_id"] == "aat_fedcba0987654321fedcba0987654321"
    assert provenance_calls[0]["terminal_runtime"].effective_provider == "stream-effective"
    assert provenance_calls[0]["runtime_provenance"] == {"runtime_target": "host"}
    assert done_replies == [state.content]
    assert saved == [(
        "assistant",
        state.content,
        "audit-stream-session",
        "siq_assistant",
        "aat_fedcba0987654321fedcba0987654321",
    )]
    assert remembered == [("siq_assistant", "audit-stream-session", None, state.content)]
