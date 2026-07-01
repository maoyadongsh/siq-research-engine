from services import agent_chat_runtime as runtime
from services import agent_runtime_postgres_fallback as fallback


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


def test_agent_chat_runtime_wrappers_remain_compatible():
    context = {"company": {"name": "上汽集团", "code": "600104"}}

    assert "当前页面公司提示：上汽集团 600104" in runtime._postgres_query_text("财务情况", context)
    assert runtime._postgres_prepare_parsed({}, "财务情况").get("query_type") == "company_all"
    assert "营收" in runtime._postgres_requested_metric_terms("营收是多少？")
    assert runtime._postgres_row_matches_requested_terms(
        {"metric_payload": {"metric_name": "营业收入"}},
        ["营业收入"],
    )
