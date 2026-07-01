import pytest

from services import agent_runtime_statement_context as context


CORE_KEYS = {
    "income_statement": ("operating_revenue", "net_profit"),
    "cash_flow_statement": ("operating_cash_flow_net",),
    "balance_sheet": ("total_assets", "total_liabilities"),
}
CORE_NAME_TERMS = {
    "income_statement": ("营业收入", "净利润"),
    "cash_flow_statement": ("经营活动现金流量净额",),
    "balance_sheet": ("资产总计", "负债合计"),
}


def normalize(value):
    return "".join(str(value or "").lower().replace("：", "").split())


def rank(record, statement_type):
    return context.statement_record_rank(
        record,
        statement_type,
        core_keys=CORE_KEYS,
        core_name_terms=CORE_NAME_TERMS,
        normalize_financial_text=normalize,
    )


def is_core(record, statement_type):
    return context.is_core_statement_record(record, statement_type, statement_record_rank_fn=rank)


def test_iter_metric_records_walks_nested_dicts_and_lists():
    metric = {"metric_name": "营业收入", "value": 1}
    source_only = {"source": {"period": "2025"}, "value": 2}
    payload = {
        "data": [
            {"ignored": {"plain": "x"}},
            {"rows": [metric, {"children": [source_only]}]},
        ]
    }

    assert context.iter_metric_records(payload) == [metric, source_only]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025年度", (2025, "2025年度")),
        ("截至2024-12-31", (2024, "截至2024-12-31")),
        ("无期间", (0, "无期间")),
        (None, (0, "")),
    ],
)
def test_period_sort_key_extracts_first_20xx_year(value, expected):
    assert context.period_sort_key(value) == expected


def test_record_source_and_source_value_prefer_record_value_then_source_fallback():
    record = {
        "period": "",
        "pdf_page": 7,
        "source": {"period": "2025", "pdf_page": 8, "task_id": "task-1"},
    }

    assert context.record_source(record) == record["source"]
    assert context.record_source({"source": "not-a-dict"}) == {}
    assert context.record_source_value(record, "pdf_page") == 7
    assert context.record_source_value(record, "period") == "2025"
    assert context.record_source_value(record, "task_id") == "task-1"


def test_statement_record_rank_and_core_detection_use_injected_dependencies():
    normalize_calls = []

    def tracking_normalize(value):
        normalize_calls.append(value)
        return normalize(value)

    by_key = context.statement_record_rank(
        {"metric_key": "net_profit", "metric_name": "净利润"},
        "income_statement",
        core_keys=CORE_KEYS,
        core_name_terms=CORE_NAME_TERMS,
        normalize_financial_text=tracking_normalize,
    )
    by_name = context.statement_record_rank(
        {"metric_name": "公司 营业收入：合计"},
        "income_statement",
        core_keys=CORE_KEYS,
        core_name_terms=CORE_NAME_TERMS,
        normalize_financial_text=tracking_normalize,
    )
    non_core = context.statement_record_rank(
        {"metric_key": "other", "metric_name": "其他"},
        "income_statement",
        core_keys=CORE_KEYS,
        core_name_terms=CORE_NAME_TERMS,
        normalize_financial_text=tracking_normalize,
    )

    assert by_key == (0, 1, "净利润")
    assert by_name == (1, 0, "公司 营业收入：合计")
    assert non_core == (9, 999, "其他")
    assert context.is_core_statement_record({"metric_name": "净利润"}, "income_statement", statement_record_rank_fn=rank)
    assert not context.is_core_statement_record({"metric_name": "其他"}, "income_statement", statement_record_rank_fn=rank)
    assert "公司 营业收入：合计" in normalize_calls


def test_latest_records_by_statement_keeps_latest_period_and_core_rank_order():
    records = [
        {"statement_type": "income_statement", "metric_key": "operating_revenue", "period": "2024", "value": 1},
        {"statement_type": "income_statement", "metric_key": "net_profit", "period": "2025", "value": 2},
        {"statement_type": "income_statement", "metric_key": "operating_revenue", "source": {"period": "2025"}, "value": 3},
        {"statement_type": "income_statement", "metric_key": "other", "period": "2025", "value": 4},
        {"statement_type": "cash_flow_statement", "metric_name": "经营活动现金流量净额", "period": "2023", "value": 5},
        {"statement_type": "balance_sheet", "metric_key": "total_liabilities", "period": "2026", "value": 6},
        {"statement_type": "balance_sheet", "metric_key": "total_assets", "period": "2026", "value": 7},
    ]

    output = context.latest_records_by_statement(
        records,
        is_core_statement_record_fn=is_core,
        statement_record_rank_fn=rank,
    )

    assert output == [records[2], records[1], records[4], records[6], records[5]]


def test_agent_chat_runtime_statement_context_wrappers_preserve_compatibility(monkeypatch):
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    monkeypatch.setattr(runtime, "THREE_STATEMENT_CORE_KEYS", {"income_statement": ("patched_key",)})
    monkeypatch.setattr(runtime, "THREE_STATEMENT_CORE_NAME_TERMS", {"income_statement": ("patched term",)})
    monkeypatch.setattr(runtime, "_normalize_financial_text", lambda value: str(value or "").lower().replace(" ", ""))

    by_key = {"statement_type": "income_statement", "metric_key": "patched_key", "period": "2025"}
    by_name = {"statement_type": "income_statement", "metric_name": "xx patched term yy", "period": "2025"}
    non_core = {"statement_type": "income_statement", "metric_key": "old_key", "period": "2025"}
    payload = {"data": [{"nested": [by_key, by_name, non_core]}]}

    assert runtime._iter_metric_records(payload) == [by_key, by_name, non_core]
    assert runtime._period_sort_key("2025年度") == context.period_sort_key("2025年度")
    assert runtime._record_source_value({"period": "", "source": {"period": "2025"}}, "period") == "2025"
    assert runtime._statement_record_rank(by_key, "income_statement") == (0, 0, "")
    assert runtime._is_core_statement_record(by_name, "income_statement")
    assert runtime._latest_records_by_statement(runtime._iter_metric_records(payload)) == [by_key, by_name]
