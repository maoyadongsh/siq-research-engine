import json
import re

import pytest

from services import agent_runtime_catalog as catalog


def _write_catalog(wiki_root, payload):
    meta_dir = wiki_root / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_market_catalog(wiki_root, market, companies):
    market_root = wiki_root if market == "CN" else wiki_root / market.lower()
    _write_catalog(
        market_root,
        {
            "market": market,
            "generated_at": "2026-07-12T00:00:00Z",
            "company_count": len(companies),
            "companies": companies,
        },
    )
    return market_root


def _normalize_company_text(value):
    return re.sub(r"[\s（）()_\-：:、,，;；/\.]+", "", str(value or "").lower())


def test_wiki_catalog_query_intent_positive_and_negative():
    assert catalog.is_wiki_catalog_query("知识库里已收录多少家公司")
    assert catalog.is_wiki_catalog_query("请列表展示已入库的公司")
    assert catalog.is_wiki_catalog_query("company_catalog count")
    assert catalog.is_wiki_catalog_query("全市场收录了哪些公司")
    assert not catalog.is_wiki_catalog_query("帮我分析上汽集团的毛利率")
    assert not catalog.is_wiki_catalog_query("")


def test_wiki_catalog_query_ignores_general_assistant_request():
    assert not catalog.is_wiki_catalog_query(
        "智能体简介，请说明已入库公司范围",
        is_general_assistant_request=lambda _message: True,
    )


def test_load_wiki_catalog_companies_sorts_and_filters_entries(tmp_path):
    wiki_root = tmp_path / "wiki"
    _write_catalog(
        wiki_root,
        {
            "companies": [
                {"stock_code": "600104", "company_short_name": "上汽集团"},
                "not-a-company",
                {"company_id": "000333-美的集团", "company_full_name": "美的集团"},
                {"stock_code": "002594", "company_short_name": "比亚迪"},
            ]
        },
    )

    _catalog_payload, companies = catalog.load_wiki_catalog_companies(wiki_root=wiki_root)

    assert [company.get("stock_code") or company.get("company_id") for company in companies] == [
        "000333-美的集团",
        "002594",
        "600104",
    ]


def test_requested_catalog_markets_defaults_to_all_and_honors_explicit_scope():
    assert catalog.requested_catalog_markets("已入库多少家公司") == catalog.MARKET_ORDER
    assert catalog.requested_catalog_markets("全市场入库数量") == catalog.MARKET_ORDER
    assert catalog.requested_catalog_markets("港股和美股有哪些公司") == ("HK", "US")
    assert catalog.requested_catalog_markets("JP 市场公司清单") == ("JP",)


def test_load_market_catalogs_filters_migrated_non_cn_duplicates(tmp_path):
    wiki_root = tmp_path / "wiki"
    _write_market_catalog(
        wiki_root,
        "CN",
        [
            {"market": "CN", "stock_code": "600104", "company_short_name": "上汽集团"},
            {"market": "HK", "stock_code": "00981", "company_short_name": "SMIC"},
            {
                "identity_kind": "generic_subject",
                "stock_code": "GENBASF",
                "company_short_name": "BASF",
            },
        ],
    )
    _write_market_catalog(
        wiki_root,
        "HK",
        [{"market": "HK", "stock_code": "00981", "company_short_name": "SMIC"}],
    )

    catalogs = catalog.load_market_catalogs(wiki_root=wiki_root)

    assert [(item.market, len(item.companies)) for item in catalogs] == [("CN", 1), ("HK", 1)]


def test_format_catalog_company_line_uses_fallback_fields():
    assert catalog.format_catalog_company_line(
        1,
        {
            "company_id": "GENBASF-BASF",
            "company_full_name": "BASF SE",
            "status": "needs_review",
            "report_count": 0,
            "has_three_statement_metrics": False,
        },
    ) == "1.  BASF SE，company_id=GENBASF-BASF，status=needs_review，reports=0，三大表指标=无"


def test_build_wiki_catalog_reply_counts_and_lists_from_tmp_catalog(tmp_path):
    wiki_root = tmp_path / "wiki"
    _write_catalog(
        wiki_root,
        {
            "generated_at": "2026-06-12T00:00:00Z",
            "company_count": 3,
            "companies": [
                {
                    "company_id": "600104-上汽集团",
                    "stock_code": "600104",
                    "company_short_name": "上汽集团",
                    "status": "ready",
                    "report_count": 2,
                    "has_three_statement_metrics": True,
                },
                {
                    "company_id": "000333-美的集团",
                    "stock_code": "000333",
                    "company_short_name": "美的集团",
                    "status": "needs_review",
                    "report_count": 1,
                    "has_three_statement_metrics": False,
                },
            ],
        },
    )

    reply = catalog.build_wiki_catalog_reply("请展示已入库公司列表", wiki_root=wiki_root)

    assert reply is not None
    assert "一共 **2 家**" in reply
    assert "company_count=3" in reply
    assert "ready：1 家；needs_review：1 家；报告合计：3 份。" in reply
    assert "1. 000333 美的集团" in reply
    assert "2. 600104 上汽集团" in reply
    assert "三大表指标=无" in reply
    assert str(wiki_root / "_meta" / "company_catalog.json") in reply


def test_build_wiki_catalog_reply_aggregates_markets_and_supports_market_filter(tmp_path):
    wiki_root = tmp_path / "wiki"
    _write_market_catalog(
        wiki_root,
        "CN",
        [
            {
                "market": "CN",
                "company_id": "600104-上汽集团",
                "stock_code": "600104",
                "company_short_name": "上汽集团",
                "status": "ready",
                "report_count": 1,
            }
        ],
    )
    _write_market_catalog(
        wiki_root,
        "US",
        [
            {
                "market": "US",
                "company_id": "US:0000320193",
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "status": "ready",
                "report_count": 1,
            },
            {
                "market": "US",
                "company_id": "US:0000789019",
                "ticker": "MSFT",
                "company_name": "Microsoft Corp.",
                "status": "ready",
                "report_count": 1,
            },
        ],
    )

    all_reply = catalog.build_wiki_catalog_reply("全市场入库了多少家公司", wiki_root=wiki_root)
    us_reply = catalog.build_wiki_catalog_reply("请展示美股已入库公司列表", wiki_root=wiki_root)

    assert all_reply is not None
    assert "全市场已入库公司一共 **3 家**" in all_reply
    assert "A股（CN）1 家" in all_reply
    assert "美股（US）2 家" in all_reply
    assert str(wiki_root / "us" / "_meta" / "company_catalog.json") in all_reply
    assert us_reply is not None
    assert "美股已入库公司一共 **2 家**" in us_reply
    assert "### 美股（US）" in us_reply
    assert "AAPL Apple Inc." in us_reply
    assert "上汽集团" not in us_reply


def test_resolve_catalog_company_dirs_across_markets_with_precise_short_ticker_matching(tmp_path):
    wiki_root = tmp_path / "wiki"
    us_root = _write_market_catalog(
        wiki_root,
        "US",
        [
            {
                "market": "US",
                "company_id": "US:0000320193",
                "company_wiki_id": "AAPL-Apple-Inc",
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "aliases": ["苹果公司"],
                "company_wiki_path": "data/wiki/us/companies/AAPL-Apple-Inc",
            }
        ],
    )
    company_dir = us_root / "companies" / "AAPL-Apple-Inc"
    company_dir.mkdir(parents=True)
    by_name = catalog.resolve_catalog_company_dirs(
        "请分析美股苹果公司的营收",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
    )
    by_ticker = catalog.resolve_catalog_company_dirs(
        "US AAPL revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
    )
    false_positive = catalog.resolve_catalog_company_dirs(
        "请说明 aapple 这个单词",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
    )

    assert by_name == [company_dir.resolve()]
    assert by_ticker == [company_dir.resolve()]
    assert false_positive == []


def test_resolve_catalog_company_dirs_accepts_meaningful_company_name_prefix(tmp_path):
    wiki_root = tmp_path / "wiki"
    jp_root = _write_market_catalog(
        wiki_root,
        "JP",
        [
            {
                "market": "JP",
                "company_id": "JP:7203",
                "company_wiki_id": "7203-Toyota-Motor-Corporation",
                "ticker": "7203",
                "company_name": "Toyota Motor Corporation",
                "aliases": ["Toyota Motor Corporation"],
            }
        ],
    )
    company_dir = jp_root / "companies" / "7203-Toyota-Motor-Corporation"
    company_dir.mkdir(parents=True)
    matches = catalog.resolve_catalog_company_dirs(
        "日本 Toyota Motor revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
    )
    false_positive = catalog.resolve_catalog_company_dirs(
        "日本 Toyota oil market",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
    )

    assert matches == [company_dir.resolve()]
    assert false_positive == []


@pytest.mark.parametrize(
    ("market", "company_id", "wiki_id"),
    (
        ("HK", "HK:00700", "00700-Tencent-Holdings-Ltd"),
        ("JP", "JP:7203", "7203-Toyota-Motor-Corporation"),
        ("KR", "KR:005930", "005930-Samsung-Electronics-Co-Ltd"),
        ("EU", "EU:NL:ASML", "ASML-ASML-Holding-N-V"),
        ("US", "US:0000320193", "AAPL-Apple-Inc"),
    ),
)
def test_resolve_catalog_company_dirs_uses_canonical_company_id_without_text_alias(
    tmp_path,
    market,
    company_id,
    wiki_id,
):
    wiki_root = tmp_path / "wiki"
    market_root = _write_market_catalog(
        wiki_root,
        market,
        [
            {
                "market": market,
                "company_id": company_id,
                "company_wiki_id": wiki_id,
                "company_name": f"{market} Exact Company",
            }
        ],
    )
    company_dir = market_root / "companies" / wiki_id
    company_dir.mkdir(parents=True)

    matches = catalog.resolve_catalog_company_dirs(
        "2025 revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
        market_hint=market,
        company_id_hint=company_id,
    )

    assert matches == [company_dir.resolve()]


def test_resolve_catalog_company_dirs_company_id_not_found_does_not_fallback_to_text_alias(tmp_path):
    wiki_root = tmp_path / "wiki"
    us_root = _write_market_catalog(
        wiki_root,
        "US",
        [
            {
                "market": "US",
                "company_id": "US:0000320193",
                "company_wiki_id": "AAPL-Apple-Inc",
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
            }
        ],
    )
    (us_root / "companies" / "AAPL-Apple-Inc").mkdir(parents=True)

    matches = catalog.resolve_catalog_company_dirs(
        "US AAPL revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
        market_hint="US",
        company_id_hint="US:0000789019",
    )

    assert matches == []


def test_resolve_catalog_company_dirs_exact_company_without_directory_fails_closed(tmp_path):
    wiki_root = tmp_path / "wiki"
    _write_market_catalog(
        wiki_root,
        "JP",
        [
            {
                "market": "JP",
                "company_id": "JP:7203",
                "company_wiki_id": "7203-Toyota-Motor-Corporation",
            }
        ],
    )

    matches = catalog.resolve_catalog_company_dirs(
        "Toyota revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
        market_hint="JP",
        company_id_hint="JP:7203",
    )

    assert matches == []


def test_resolve_catalog_company_dirs_duplicate_canonical_company_id_fails_closed(tmp_path):
    wiki_root = tmp_path / "wiki"
    hk_root = _write_market_catalog(
        wiki_root,
        "HK",
        [
            {
                "market": "HK",
                "company_id": "HK:00700",
                "company_wiki_id": "00700-Tencent-A",
            },
            {
                "market": "HK",
                "company_id": "hk:00700",
                "company_wiki_id": "00700-Tencent-B",
            },
        ],
    )
    (hk_root / "companies" / "00700-Tencent-A").mkdir(parents=True)
    (hk_root / "companies" / "00700-Tencent-B").mkdir(parents=True)

    matches = catalog.resolve_catalog_company_dirs(
        "Tencent revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
        market_hint="HK",
        company_id_hint="HK:00700",
    )

    assert matches == []


def test_resolve_catalog_company_dirs_market_company_id_conflict_fails_closed(tmp_path):
    wiki_root = tmp_path / "wiki"
    hk_root = _write_market_catalog(
        wiki_root,
        "HK",
        [
            {
                "market": "HK",
                "company_id": "HK:00700",
                "company_wiki_id": "00700-Tencent-Holdings-Ltd",
                "company_name": "Tencent Holdings Ltd",
            }
        ],
    )
    (hk_root / "companies" / "00700-Tencent-Holdings-Ltd").mkdir(parents=True)

    matches = catalog.resolve_catalog_company_dirs(
        "Tencent Holdings revenue",
        wiki_root=wiki_root,
        normalize_text=_normalize_company_text,
        market_hint="HK",
        company_id_hint="JP:00700",
    )

    assert matches == []


def test_build_wiki_catalog_reply_returns_read_error_for_missing_or_invalid_catalog(tmp_path):
    missing_reply = catalog.build_wiki_catalog_reply("现在入库了多少家公司", wiki_root=tmp_path / "missing")
    assert missing_reply is not None
    assert "当前无法读取已入库公司清单" in missing_reply
    assert "count=0" in missing_reply

    wiki_root = tmp_path / "wiki"
    _write_catalog(wiki_root, [{"stock_code": "600104"}])
    invalid_reply = catalog.build_wiki_catalog_reply("现在入库了多少家公司", wiki_root=wiki_root)

    assert invalid_reply is not None
    assert "格式异常" in invalid_reply


def test_build_wiki_catalog_reply_returns_none_for_non_catalog_query(tmp_path):
    assert catalog.build_wiki_catalog_reply("帮我分析上汽集团营收", wiki_root=tmp_path) is None
