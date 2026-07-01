import json

from services import agent_runtime_catalog as catalog


def _write_catalog(wiki_root, payload):
    meta_dir = wiki_root / "_meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "company_catalog.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_wiki_catalog_query_intent_positive_and_negative():
    assert catalog.is_wiki_catalog_query("知识库里已收录多少家公司")
    assert catalog.is_wiki_catalog_query("请列表展示已入库的公司")
    assert catalog.is_wiki_catalog_query("company_catalog count")
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
