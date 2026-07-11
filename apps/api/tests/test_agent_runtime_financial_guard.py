import json
from pathlib import Path

from services import agent_runtime_financial_provenance as provenance
from services import agent_runtime_financial_guard as guard


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


def test_detects_derived_financial_metric_case_insensitively():
    assert guard._reply_has_derived_financial_metric("未来三年 CAGR 为 8%")
    assert guard._reply_has_derived_financial_metric("人均营收为 120 万元/人")
    assert not guard._reply_has_derived_financial_metric("营业收入为 12 亿元")


def test_detects_calculator_and_reconciliation_traces():
    assert guard._reply_has_calculator_trace("派生计算 operation=ratio")
    assert guard._reply_has_calculator_trace("## 计算器校验\n- ok")
    assert guard._reply_has_reconciliation_trace("## 勾稽校验\n- ok")
    assert guard._reply_has_reconciliation_trace("goodwill_reconciliation passed")
    assert not guard._reply_has_reconciliation_trace("商誉净额说明")


def test_detects_reconciliation_metric_only_with_subject_and_relation():
    assert guard._reply_has_reconciliation_metric("商誉原值 12.82 亿元，减值准备 0.99 亿元，净额 11.83 亿元")
    assert guard._reply_has_reconciliation_metric("坏账准备勾稽关系")
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
