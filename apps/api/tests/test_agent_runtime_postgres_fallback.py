import sys
from dataclasses import replace

from services import agent_chat_runtime as runtime, agent_runtime_postgres_fallback as fallback

FINANCIAL_NOTE_TERMS = ("应付职工薪酬", "商誉")
CORE_TERMS = ("营业收入", "净利润", "归母净利润")
CORE_ALIASES = {
    "营业收入": ("营业收入", "营收", "收入"),
    "净利润": ("净利润", "利润"),
    "归母净利润": ("归属于母公司股东的净利润", "归母净利润", "归母"),
}


def _normalize(value):
    return runtime._normalize_financial_text(value)


def _payload(row):
    payload = row.get("metric_payload")
    return payload if isinstance(payload, dict) else {}


def _predicate_kwargs(**overrides):
    kwargs = {
        "is_general_assistant_request": lambda message: "你能做什么" in message,
        "is_human_capital_query": lambda message: "人员结构" in message,
        "is_statement_query": lambda message: "资产负债表" in message,
        "should_inject_note_detail_context": lambda message: "商誉构成" in message,
        "postgres_fallback_terms": ("数据库", "PostgreSQL", "postgres"),
        "context_company": lambda context: context.get("company", {}) if isinstance(context, dict) else {},
    }
    kwargs.update(overrides)
    return kwargs


def test_load_financial_query_api_imports_module_from_script_dir(tmp_path):
    module_name = "financial_query_api_loader_test_module"
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text("VALUE = 'loaded'\n", encoding="utf-8")
    sys.modules.pop(module_name, None)
    original_path = list(sys.path)

    try:
        module = fallback.load_financial_query_api(tmp_path, module_name=module_name)

        assert module is not None
        assert module.VALUE == "loaded"
        assert str(tmp_path) in sys.path
    finally:
        sys.modules.pop(module_name, None)
        sys.path[:] = original_path


def test_load_financial_query_api_returns_none_for_import_failure(tmp_path):
    module_name = "financial_query_api_missing_test_module"
    sys.modules.pop(module_name, None)

    assert fallback.load_financial_query_api(tmp_path, module_name=module_name) is None


def test_financial_query_connection_factory_prefers_top_level_connection():
    def get_connection():
        return "top"

    class Module:
        pass

    module = Module()
    module.get_connection = get_connection

    assert fallback.financial_query_connection_factory(module) is get_connection


def test_financial_query_connection_factory_supports_nested_pg_connection():
    def nested_connection():
        return "nested"

    class Pg:
        pass

    class Module:
        pass

    module = Module()
    module.get_connection = None
    module.pg = Pg()
    module.pg.get_connection = nested_connection

    assert fallback.financial_query_connection_factory(module) is nested_connection


def test_financial_query_connection_factory_returns_none_when_missing():
    class Module:
        get_connection = "not-callable"
        pg = object()

    assert fallback.financial_query_connection_factory(Module()) is None


def test_postgres_query_text_appends_company_hint():
    context = {"company": {"name": "上汽集团", "code": "600104"}}

    query_text = fallback.postgres_query_text(
        "分析一下财务表现",
        context,
        context_company_hint=runtime._context_company_hint,
    )

    assert query_text == "分析一下财务表现\n\n当前页面公司提示：上汽集团 600104"


def test_postgres_query_text_keeps_message_without_company_hint():
    assert (
        fallback.postgres_query_text(
            "分析一下财务表现",
            None,
            context_company_hint=runtime._context_company_hint,
        )
        == "分析一下财务表现"
    )


def test_postgres_query_text_keeps_message_when_hint_callback_is_empty():
    seen_context = {}

    def empty_hint(context):
        seen_context["value"] = context
        return ""

    context = {"company": {"name": "上汽集团"}}

    assert (
        fallback.postgres_query_text(
            "分析一下财务表现",
            context,
            context_company_hint=empty_hint,
        )
        == "分析一下财务表现"
    )
    assert seen_context["value"] is context


def test_should_consider_postgres_fallback_routes_only_financial_or_contextual_queries():
    assert not fallback.should_consider_postgres_fallback(" ", None, **_predicate_kwargs())
    assert not fallback.should_consider_postgres_fallback("你能做什么？", None, **_predicate_kwargs())
    assert not fallback.should_consider_postgres_fallback("分析人员结构", {"company": {"name": "上汽"}}, **_predicate_kwargs())
    assert fallback.should_consider_postgres_fallback("看一下资产负债表", None, **_predicate_kwargs())
    assert fallback.should_consider_postgres_fallback("商誉构成是多少", None, **_predicate_kwargs())
    assert fallback.should_consider_postgres_fallback("请查数据库里的收入", None, **_predicate_kwargs())
    assert fallback.should_consider_postgres_fallback("收入是多少？", {"company": {"name": "上汽集团"}}, **_predicate_kwargs())
    assert not fallback.should_consider_postgres_fallback("收入是多少？", None, **_predicate_kwargs())


def test_postgres_market_agent_view_result_records_hit_and_defaults():
    seen = {}

    class Module:
        @staticmethod
        def query_market_agent_view_result(query_text, parsed, company, limit=20):
            seen["args"] = (query_text, parsed, company, limit)
            return {"rows": [{"metric_name": "收入"}], "schema": "pdf2md_hk"}

    context = {"company": {"name": "Tencent", "code": "00700"}}
    parsed = {"query_type": "metric"}

    result = fallback.postgres_market_agent_view_result(
        Module(),
        "收入是多少？",
        context,
        parsed,
        "收入是多少？",
        5,
        context_company=lambda value: value.get("company", {}),
    )

    assert result == {
        "rows": [{"metric_name": "收入"}],
        "schema": "pdf2md_hk",
        "question": "收入是多少？",
        "fallback_reason": "market_view_hit",
    }
    assert seen["args"] == ("收入是多少？", parsed, {"name": "Tencent", "code": "00700"}, 5)
    assert context["_audit_fallback_events"][-1]["reason"] == "market_view_hit"


def test_postgres_market_agent_view_result_passes_target_scope_from_context():
    seen = {}

    class Module:
        @staticmethod
        def query_market_agent_view_result(query_text, parsed, company, *, limit=20, market=None):
            seen["args"] = (query_text, parsed, company, limit, market)
            return {"rows": [{"metric_name": "收入"}], "schema": "pdf2md_hk"}

    context = {
        "company": {"name": "Tencent", "company_id": "HK:00700", "market": "HK"},
        "resolved_period": {"filing_id": "HK:00700:2025-annual", "parse_run_id": "parse-target"},
    }

    result = fallback.postgres_market_agent_view_result(
        Module(),
        "收入是多少？",
        context,
        {"query_type": "metric"},
        "收入是多少？",
        5,
        context_company=lambda value: value.get("company", {}),
    )

    assert result is not None
    assert seen["args"] == (
        "收入是多少？",
        {
            "query_type": "metric",
            "market": "HK",
            "parse_run_id": "parse-target",
            "filing_id": "HK:00700:2025-annual",
        },
        {"name": "Tencent", "company_id": "HK:00700", "market": "HK"},
        5,
        "HK",
    )


def test_postgres_market_agent_view_result_passes_research_identity_scope():
    seen = {}

    class Module:
        @staticmethod
        def query_market_agent_view_result(query_text, parsed, company, *, limit=20, market=None):
            seen["args"] = (query_text, parsed, company, limit, market)
            return {"rows": [{"metric_name": "收入"}], "schema": "pdf2md_hk"}

    context = {
        "research_identity": {
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "parse-ri",
        },
        "company": {"name": "Tencent", "code": "00700"},
    }

    result = fallback.postgres_market_agent_view_result(
        Module(),
        "收入是多少？",
        context,
        {"query_type": "metric"},
        "收入是多少？",
        5,
        context_company=lambda value: value.get("company", {}),
    )

    assert result is not None
    assert seen["args"] == (
        "收入是多少？",
        {
            "query_type": "metric",
            "market": "HK",
            "parse_run_id": "parse-ri",
            "filing_id": "HK:00700:2025-annual",
        },
        {"name": "Tencent", "code": "00700"},
        5,
        "HK",
    )


def test_postgres_market_agent_view_result_records_miss_and_unsupported_module():
    class UnsupportedModule:
        pass

    class EmptyModule:
        @staticmethod
        def query_market_agent_view_result(_query_text, _parsed, _company, limit=20):
            return {"rows": []}

    unsupported_context = {}
    assert fallback.postgres_market_agent_view_result(
        UnsupportedModule(),
        "收入是多少？",
        unsupported_context,
        {},
        "收入是多少？",
        5,
        context_company=lambda _value: {},
    ) is None
    assert "_audit_fallback_events" not in unsupported_context

    miss_context = {}
    assert fallback.postgres_market_agent_view_result(
        EmptyModule(),
        "收入是多少？",
        miss_context,
        {},
        "收入是多少？",
        5,
        context_company=lambda _value: {},
    ) is None
    assert miss_context["_audit_fallback_events"][-1]["reason"] == "market_view_miss"


def test_postgres_market_agent_view_result_records_exception_and_logs():
    seen: list[str] = []

    class BrokenModule:
        @staticmethod
        def query_market_agent_view_result(_query_text, _parsed, _company, limit=20):
            raise RuntimeError("boom")

    context = {}
    result = fallback.postgres_market_agent_view_result(
        BrokenModule(),
        "收入是多少？",
        context,
        {},
        "收入是多少？",
        5,
        context_company=lambda _value: {},
        log_exception=lambda exc: seen.append(exc.__class__.__name__),
    )

    assert result is None
    assert seen == ["RuntimeError"]
    assert context["_audit_fallback_events"][-1]["reason"] == "postgres_unavailable"
    assert context["_audit_fallback_events"][-1]["detail"] == "RuntimeError"


def test_postgres_query_metric_rows_uses_statement_table_query_for_table_type():
    calls: list[str] = []

    class Module:
        @staticmethod
        def infer_metric_from_database(_cur, parsed, company, query_text):
            calls.append(f"infer:{parsed['query_type']}:{company['code']}:{query_text}")

        @staticmethod
        def query_statement_table(_cur, parsed, company, limit):
            calls.append(f"table:{parsed['query_type']}:{company['code']}:{limit}")
            return ["financial_income_statement"], [{"metric_name": "营业收入"}]

        @staticmethod
        def query_metric_from_split_tables(*_args):
            raise AssertionError("split table query should not run for table requests")

    source_tables, rows = fallback.postgres_query_metric_rows(
        Module(),
        object(),
        {"query_type": "table"},
        {"code": "600104"},
        "利润表",
        3,
    )

    assert source_tables == ["financial_income_statement"]
    assert rows == [{"metric_name": "营业收入"}]
    assert calls == ["infer:table:600104:利润表", "table:table:600104:3"]


def test_postgres_query_metric_rows_falls_back_to_wide_and_dedupes_rows():
    calls: list[str] = []

    class Module:
        @staticmethod
        def infer_metric_from_database(_cur, parsed, _company, _query_text):
            parsed["metric_name"] = "营业收入"
            calls.append("infer")

        @staticmethod
        def query_metric_from_split_tables(_cur, parsed, _company, limit):
            calls.append(f"split:{parsed['metric_name']}:{limit}")
            return ["financial_metrics_split"], []

        @staticmethod
        def query_metric_from_wide(_cur, parsed, _company, limit):
            calls.append(f"wide:{parsed['metric_name']}:{limit}")
            return ["financial_metrics_split", "financial_metrics_wide"], [
                {"metric_name": "营业收入", "value": 1},
                {"metric_name": "营业收入", "value": 1},
            ]

        @staticmethod
        def dedupe_response_rows(rows, limit):
            calls.append(f"dedupe:{len(rows)}:{limit}")
            return rows[:1]

    parsed = {"query_type": "metric"}

    source_tables, rows = fallback.postgres_query_metric_rows(
        Module(),
        object(),
        parsed,
        {"code": "600104"},
        "营收",
        1,
    )

    assert parsed["metric_name"] == "营业收入"
    assert source_tables == ["financial_metrics_split", "financial_metrics_wide"]
    assert rows == [{"metric_name": "营业收入", "value": 1}]
    assert calls == ["infer", "split:营业收入:1", "wide:营业收入:1", "dedupe:2:1"]


class _DocumentTablesCursor:
    def __init__(self, table_rows=None, *, fail=False):
        self.table_rows = table_rows or []
        self.fail = fail
        self.executed: list[tuple[str, list[object]]] = []

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("database offline")
        self.executed.append((sql, list(params or [])))

    def fetchall(self):
        return self.table_rows


def test_postgres_enrich_rows_with_table_pages_skips_rows_that_already_have_page():
    cur = _DocumentTablesCursor()
    rows = [{"task_id": "task-1", "table_index": 2, "source_page_number": 9}]

    fallback.postgres_enrich_rows_with_table_pages(
        cur,
        rows,
        postgres_row_pdf_page=lambda row: row.get("source_page_number"),
        postgres_row_table_index=lambda row: row.get("table_index"),
    )

    assert cur.executed == []
    assert rows == [{"task_id": "task-1", "table_index": 2, "source_page_number": 9}]


def test_postgres_enrich_rows_with_table_pages_adds_page_and_markdown_line():
    cur = _DocumentTablesCursor(
        [
            {
                "task_id": "task-1",
                "table_index": 2,
                "pdf_page_number": 15,
                "markdown_line": 88,
            }
        ]
    )
    rows = [
        {"task_id": "task-1", "table_index": "2", "metric_name": "营业收入"},
        {"task_id": "task-1", "table_index": 2, "metric_name": "净利润"},
        {"task_id": "task-2", "table_index": "", "metric_name": "忽略"},
    ]

    fallback.postgres_enrich_rows_with_table_pages(
        cur,
        rows,
        postgres_row_pdf_page=lambda row: row.get("source_page_number") or row.get("pdf_page_number"),
        postgres_row_table_index=lambda row: row.get("table_index"),
    )

    assert "pdf2md.document_tables" in cur.executed[0][0]
    assert cur.executed[0][1] == ["task-1", 2]
    assert rows[0]["source_page_number"] == 15
    assert rows[0]["source_markdown_line"] == 88
    assert rows[1]["source_page_number"] == 15
    assert rows[1]["source_markdown_line"] == 88
    assert "source_page_number" not in rows[2]


def test_postgres_enrich_rows_with_table_pages_tolerates_document_table_query_failure():
    cur = _DocumentTablesCursor(fail=True)
    rows = [{"task_id": "task-1", "table_index": 2, "metric_name": "营业收入"}]

    fallback.postgres_enrich_rows_with_table_pages(
        cur,
        rows,
        postgres_row_pdf_page=lambda row: row.get("source_page_number"),
        postgres_row_table_index=lambda row: row.get("table_index"),
    )

    assert rows == [{"task_id": "task-1", "table_index": 2, "metric_name": "营业收入"}]


def test_postgres_prepare_parsed_infers_company_all_for_broad_financial_query():
    parsed = {"company_name": "上汽集团"}

    output = fallback.postgres_prepare_parsed(parsed, "上汽集团经营情况和主要数据如何？")

    assert output["query_type"] == "company_all"
    assert parsed == {"company_name": "上汽集团"}


def test_postgres_prepare_parsed_keeps_specific_metric_parse():
    parsed = {"metric_name": "商誉", "query_type": "metric"}

    output = fallback.postgres_prepare_parsed(parsed, "财务表现如何？")

    assert output == {"metric_name": "商誉", "query_type": "metric"}


def test_postgres_prepare_parsed_preserves_missing_fields_and_zero_values():
    parsed = {"company_name": "上汽集团", "pdf_page": 0, "table_index": 0}

    output = fallback.postgres_prepare_parsed(parsed, "上汽集团怎么样？")

    assert output == {"company_name": "上汽集团", "pdf_page": 0, "table_index": 0}
    assert output is not parsed
    assert parsed == {"company_name": "上汽集团", "pdf_page": 0, "table_index": 0}


def test_postgres_requested_metric_terms_matches_aliases_and_request_terms():
    terms = fallback.postgres_requested_metric_terms(
        "请看营收、归母和应付职工薪酬",
        financial_note_metric_terms=FINANCIAL_NOTE_TERMS,
        core_key_metric_terms=CORE_TERMS,
        core_key_metric_aliases=CORE_ALIASES,
    )

    assert "应付职工薪酬" in terms
    assert "营业收入" in terms
    assert "营收" in terms
    assert "收入" in terms
    assert "归属于母公司股东的净利润" in terms
    assert "归母净利润" in terms
    assert "归母" in terms
    assert terms == sorted(dict.fromkeys(terms), key=len, reverse=True)


def test_postgres_requested_metric_terms_matches_spaced_english_alias():
    terms = fallback.postgres_requested_metric_terms(
        "US Apple total assets",
        financial_note_metric_terms=(),
        core_key_metric_terms=("total assets",),
        core_key_metric_aliases={"总资产": ("total_assets", "total assets")},
    )

    assert "total assets" in terms
    assert "total_assets" in terms


def test_postgres_requested_metric_terms_empty_message_returns_empty_terms():
    assert (
        fallback.postgres_requested_metric_terms(
            "   ",
            financial_note_metric_terms=FINANCIAL_NOTE_TERMS,
            core_key_metric_terms=CORE_TERMS,
            core_key_metric_aliases=CORE_ALIASES,
        )
        == []
    )


def test_postgres_row_matches_requested_terms_does_not_filter_empty_terms():
    assert fallback.postgres_row_matches_requested_terms(
        {"metric_name": "货币资金"},
        [],
        normalize_financial_text=_normalize,
        postgres_row_payload=_payload,
    )


def test_postgres_row_matches_requested_terms_skips_payload_callback_for_empty_terms():
    def fail_payload(_row):
        raise AssertionError("payload callback should not be called")

    assert fallback.postgres_row_matches_requested_terms(
        {},
        [],
        normalize_financial_text=_normalize,
        postgres_row_payload=fail_payload,
    )


def test_postgres_row_matches_requested_terms_uses_alias_normalization():
    row = {"metric_name": "归属于母公司股东的净利润"}

    assert fallback.postgres_row_matches_requested_terms(
        row,
        ["归母净利润", "归属于母公司股东的净利润"],
        normalize_financial_text=_normalize,
        postgres_row_payload=_payload,
    )


def test_postgres_row_matches_requested_terms_uses_payload_fallback():
    row = {"metric_payload": {"canonical_name": "operating revenue", "metric_name": "营业收入"}}

    assert fallback.postgres_row_matches_requested_terms(
        row,
        ["营业收入"],
        normalize_financial_text=_normalize,
        postgres_row_payload=_payload,
    )


def test_postgres_row_matches_requested_terms_returns_false_for_missing_fields():
    assert not fallback.postgres_row_matches_requested_terms(
        {"metric_payload": None},
        ["营业收入"],
        normalize_financial_text=_normalize,
        postgres_row_payload=_payload,
    )


def test_fallback_audit_context_helpers_append_events_without_mutating_input():
    original = {"company": {"name": "上汽集团"}}

    output = fallback.audit_context_with_fallback_event(
        original,
        reason="wiki_structured_miss",
        stage="postgres_fallback_started",
        detail="x" * 400,
        source="wiki_first",
    )

    assert output is not original
    assert "_audit_fallback_events" not in original
    assert output["fallback_reason"] == "wiki_structured_miss"
    assert output["_audit_fallback_events"][0]["source"] == "wiki_first"
    assert len(output["_audit_fallback_events"][0]["detail"]) == 300


def test_record_postgres_fallback_event_mutates_dict_context_only():
    context = {"_audit_fallback_events": [{"reason": "existing"}], "fallback_reason": "existing"}

    fallback.record_postgres_fallback_event(
        context,
        reason="postgres_hit",
        stage="legacy_rows",
    )
    fallback.record_postgres_fallback_event(None, reason="ignored", stage="ignored")

    assert [event["reason"] for event in context["_audit_fallback_events"]] == ["existing", "postgres_hit"]
    assert context["fallback_reason"] == "existing"


def test_audit_context_for_final_reply_preserves_postgres_cited_reply_and_marks_uncited_reply():
    context = {
        "_audit_fallback_events": [{"reason": "wiki_structured_miss", "stage": "postgres_fallback_started"}],
        "fallback_reason": "wiki_structured_miss",
    }

    cited = fallback.audit_context_for_final_reply(context, "引用来源: source_type=postgresql_agent_view")
    uncited = fallback.audit_context_for_final_reply(context, "这里没有数据库引用")

    assert cited is context
    assert uncited is not context
    assert uncited["_audit_fallback_events"][-1]["stage"] == "runtime_answer_no_postgres_citation"


class _FallbackCursor:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql, _params=None):
        self.executed.append(str(sql).strip())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FallbackConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FallbackModule:
    company = {"code": "600104", "name": "上汽集团"}
    metric_rows = [{"metric_name": "营业收入", "value": 100, "task_id": "t1", "table_index": 1}]
    company_all_rows: list[dict] = []

    @staticmethod
    def merge_parse(query_text, use_hermes):
        return {"query_type": "metric", "query_text": query_text, "use_hermes": use_hermes}

    @classmethod
    def resolve_company(cls, _cur, _parsed, _query_text):
        return cls.company

    @classmethod
    def query_company_all_metrics(cls, _cur, _parsed, _company, _limit):
        return ["financial_all"], list(cls.company_all_rows)

    @staticmethod
    def normalize_json(value):
        return value


def _fallback_deps(*, module=None, connection=None, consider=True, requested_terms=None, rows=None, market_result=None, logs=None):
    events: list[dict] = []
    rows = rows if rows is not None else [{"metric_name": "营业收入", "value": 100}]
    logs = logs if logs is not None else []

    def record(context, **event):
        events.append(event)
        fallback.record_postgres_fallback_event(context, **event)

    deps = fallback.PostgresFallbackDependencies(
        should_consider_postgres_fallback=lambda _message, _context: consider,
        record_postgres_fallback_event=record,
        load_financial_query_api=lambda: module,
        postgres_query_text=lambda message, _context: f"{message} query",
        postgres_prepare_parsed=lambda parsed, _message: dict(parsed),
        postgres_market_agent_view_result=lambda *_args: market_result,
        financial_query_connection_factory=lambda _module: (lambda: connection) if connection else None,
        postgres_requested_metric_terms=lambda _message: requested_terms or [],
        postgres_query_metric_rows=lambda _module, _cur, parsed, _company, _query_text, _limit: (
            ["financial_metrics"],
            [dict(row, parsed_metric=parsed.get("metric_name")) for row in rows],
        ),
        postgres_row_matches_requested_terms=lambda row, terms: any(term in str(row.get("metric_name", "")) for term in terms),
        postgres_enrich_rows_with_table_pages=lambda _cur, output_rows: [row.setdefault("source_page_number", 7) for row in output_rows],
        normalize_json=lambda loaded_module, value: loaded_module.normalize_json(value),
        log_exception=lambda exc: logs.append(exc.__class__.__name__),
    )
    return deps, events


def test_postgres_fallback_result_short_circuits_market_agent_view_hit():
    context = {}
    module = _FallbackModule()
    deps, events = _fallback_deps(
        module=module,
        market_result={"rows": [{"metric_name": "revenue"}], "fallback_reason": "market_view_hit"},
    )

    result = fallback.postgres_fallback_result("收入是多少？", context, limit=3, deps=deps)

    assert result == {"rows": [{"metric_name": "revenue"}], "fallback_reason": "market_view_hit"}
    assert [event["reason"] for event in events] == ["wiki_structured_miss"]


def test_postgres_fallback_result_fail_closes_non_cn_market_after_market_view_miss():
    context = {
        "company": {"name": "Tencent", "company_id": "HK:00700", "market": "HK"},
        "resolved_period": {"filing_id": "HK:00700:2025-annual", "parse_run_id": "parse-hk"},
    }
    module = _FallbackModule()
    deps, events = _fallback_deps(module=module)

    result = fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps)

    assert result is None
    assert events[-1] == {
        "reason": "market_boundary_closed",
        "stage": "legacy_fallback_skipped_for_non_cn_market",
        "detail": "HK",
        "source": "postgres_market_view",
    }
    assert [event["reason"] for event in events] == ["wiki_structured_miss", "market_boundary_closed"]
    assert context["_audit_fallback_events"][-1]["reason"] == "market_boundary_closed"


def test_postgres_fallback_result_fail_closes_research_identity_market_after_market_view_miss():
    context = {
        "research_identity": {
            "market": "JP",
            "company_id": "JP:7203",
            "filing_id": "JP:7203:2025-annual",
            "parse_run_id": "parse-jp",
        },
        "company": {"name": "Toyota"},
    }
    module = _FallbackModule()
    deps, events = _fallback_deps(module=module)

    result = fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps)

    assert result is None
    assert events[-1]["reason"] == "market_boundary_closed"
    assert events[-1]["detail"] == "JP"


def test_postgres_fallback_result_fail_closes_us_sec_alias_before_legacy_connection():
    context = {
        "company": {"name": "Apple", "company_id": "US_SEC:AAPL"},
        "filing": {
            "market": "US_SEC",
            "filing_id": "US_SEC:AAPL:10-K:2025",
            "parse_run_id": "parse-us",
        },
    }
    module = _FallbackModule()
    deps, events = _fallback_deps(module=module)

    result = fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps)

    assert result is None
    assert events[-1]["reason"] == "market_boundary_closed"
    assert events[-1]["detail"] == "US"


def test_postgres_fallback_result_skips_all_database_queries_for_incomplete_non_cn_identity():
    context = {"research_identity": {"market": "EU"}}
    load_calls = []
    deps, events = _fallback_deps(module=_FallbackModule())
    deps = replace(
        deps,
        load_financial_query_api=lambda: load_calls.append("load") or _FallbackModule(),
    )

    result = fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps)

    assert result is None
    assert load_calls == []
    assert [event["reason"] for event in events] == [
        "wiki_structured_miss",
        "research_identity_incomplete",
        "market_boundary_closed",
    ]
    assert events[1]["stage"] == "market_agent_view_skipped_for_incomplete_identity"
    assert events[1]["detail"] == "market=EU missing=company_id,filing_id,parse_run_id"
    assert events[2]["stage"] == "legacy_fallback_skipped_for_non_cn_market"


def test_postgres_fallback_result_returns_legacy_rows_and_records_hit():
    cursor = _FallbackCursor()
    context = {}
    deps, events = _fallback_deps(module=_FallbackModule(), connection=_FallbackConnection(cursor), requested_terms=["营业收入"])

    result = fallback.postgres_fallback_result("营业收入是多少？", context, limit=2, deps=deps)

    assert result is not None
    assert result["question"] == "营业收入是多少？"
    assert result["query_text"] == "营业收入是多少？ query"
    assert result["source_tables"] == ["financial_metrics"]
    assert result["rows"] == [{"metric_name": "营业收入", "value": 100, "parsed_metric": None, "source_page_number": 7}]
    assert result["parsed"]["resolved_code"] == "600104"
    assert any(sql == "SET TRANSACTION READ ONLY" for sql in cursor.executed)
    assert [event["reason"] for event in events] == ["wiki_structured_miss", "postgres_hit"]


def test_postgres_fallback_result_keeps_legacy_for_cn_and_unknown_market():
    for context in ({"market": "CN"}, {}):
        cursor = _FallbackCursor()
        deps, events = _fallback_deps(
            module=_FallbackModule(),
            connection=_FallbackConnection(cursor),
            requested_terms=["营业收入"],
        )

        result = fallback.postgres_fallback_result("营业收入是多少？", context, limit=2, deps=deps)

        assert result is not None
        assert result["fallback_reason"] == "postgres_hit"
        assert [event["reason"] for event in events] == ["wiki_structured_miss", "postgres_hit"]


def test_postgres_fallback_result_records_company_and_metric_misses():
    class MissingCompanyModule(_FallbackModule):
        company = None

    context = {}
    deps, events = _fallback_deps(module=MissingCompanyModule(), connection=_FallbackConnection(_FallbackCursor()))

    assert fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps) is None
    assert events[-1]["stage"] == "legacy_resolve_company_no_match"

    context = {}
    deps, events = _fallback_deps(
        module=_FallbackModule(),
        connection=_FallbackConnection(_FallbackCursor()),
        requested_terms=["净利润"],
        rows=[{"metric_name": "营业收入", "value": 100}],
    )

    assert fallback.postgres_fallback_result("净利润是多少？", context, limit=2, deps=deps) is None
    assert events[-1]["stage"] == "legacy_rows_do_not_match_requested_terms"


def test_postgres_fallback_result_records_unavailable_for_exception_and_not_applicable():
    logs: list[str] = []

    class BrokenModule(_FallbackModule):
        @staticmethod
        def merge_parse(_query_text, _use_hermes):
            raise RuntimeError("boom")

    context = {}
    deps, events = _fallback_deps(module=BrokenModule(), logs=logs)

    assert fallback.postgres_fallback_result("收入是多少？", context, limit=2, deps=deps) is None
    assert events[-1]["reason"] == "postgres_unavailable"
    assert events[-1]["detail"] == "RuntimeError"
    assert logs == ["RuntimeError"]

    context = {}
    deps, events = _fallback_deps(module=BrokenModule(), consider=False)
    assert fallback.postgres_fallback_result("普通问题", context, limit=2, deps=deps) is None
    assert events == [{"reason": "postgres_not_applicable", "stage": "should_consider_postgres_fallback_false"}]


def test_build_postgres_fallback_context_records_dict_event_and_renders_result():
    context = {}
    seen = {}

    def fake_result(message, ctx):
        seen["fallback"] = (message, ctx)
        return {"rows": [1]}

    deps = fallback.PostgresFallbackContextDependencies(
        record_postgres_fallback_event=fallback.record_postgres_fallback_event,
        audit_context_with_fallback_event=fallback.audit_context_with_fallback_event,
        postgres_fallback_result=fake_result,
        render_postgres_fallback_context=lambda result: f"rendered:{len(result['rows'])}",
    )

    output = fallback.build_postgres_fallback_context("收入是多少？", context, deps=deps)

    assert output == "rendered:1"
    assert seen["fallback"] == ("收入是多少？", context)
    assert context["_audit_fallback_events"][0]["reason"] == "wiki_fulltext_miss"
    assert context["_audit_fallback_events"][0]["stage"] == "postgres_context_fallback_attempt"


def test_build_postgres_fallback_context_wraps_non_dict_context_and_returns_none_without_result():
    seen = {}

    def fake_audit_context(value, **event):
        seen["audit"] = (value, event)
        return {"wrapped": True}

    deps = fallback.PostgresFallbackContextDependencies(
        record_postgres_fallback_event=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dict recorder should not run")),
        audit_context_with_fallback_event=fake_audit_context,
        postgres_fallback_result=lambda message, ctx: seen.setdefault("fallback", (message, ctx)) and None,
        render_postgres_fallback_context=lambda _result: "unused",
    )

    output = fallback.build_postgres_fallback_context("收入是多少？", "raw-context", deps=deps)

    assert output is None
    assert seen["audit"][0] == "raw-context"
    assert seen["audit"][1]["reason"] == "wiki_fulltext_miss"
    assert seen["fallback"] == ("收入是多少？", {"wrapped": True})


def test_agent_chat_runtime_wrappers_remain_compatible():
    context = {"company": {"name": "上汽集团", "code": "600104"}}

    assert "当前页面公司提示：上汽集团 600104" in runtime._postgres_query_text("财务情况", context)
    assert runtime._postgres_prepare_parsed({}, "财务情况").get("query_type") == "company_all"
    assert "营收" in runtime._postgres_requested_metric_terms("营收是多少？")
    assert runtime._postgres_row_matches_requested_terms(
        {"metric_payload": {"metric_name": "营业收入"}},
        ["营业收入"],
    )
    assert runtime._should_consider_postgres_fallback("收入是多少？", context)
    assert not runtime._should_consider_postgres_fallback("你能做什么？", context)


def test_agent_chat_runtime_load_financial_query_api_wrapper_delegates(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_loader(script_dir):
        seen["script_dir"] = script_dir
        return sentinel

    monkeypatch.setattr(runtime.agent_runtime_postgres_fallback, "load_financial_query_api", fake_loader)

    assert runtime._load_financial_query_api() is sentinel
    assert seen["script_dir"] == runtime.FINANCIAL_QUERY_API_DIR


def test_postgres_fallback_records_unavailable_reason(monkeypatch):
    class BrokenModule:
        @staticmethod
        def merge_parse(_query_text, _use_hermes):
            return {"query_type": "metric"}

        @staticmethod
        def normalize_json(value):
            return value

    context = {"company": {"name": "上汽集团", "code": "600104"}}

    monkeypatch.setattr(runtime, "_should_consider_postgres_fallback", lambda _message, _context: True)
    monkeypatch.setattr(runtime, "_load_financial_query_api", lambda: BrokenModule())
    monkeypatch.setattr(runtime, "_financial_query_connection_factory", lambda _module: None)

    assert runtime._postgres_fallback_result("上汽集团收入是多少？", context) is None

    events = context["_audit_fallback_events"]
    assert any(event["reason"] == "wiki_structured_miss" for event in events)
    assert any(event["reason"] == "postgres_unavailable" for event in events)
    assert context["fallback_reason"] == "wiki_structured_miss"
