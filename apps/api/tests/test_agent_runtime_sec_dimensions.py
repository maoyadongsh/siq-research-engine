import json
import re

from services import (
    agent_chat_runtime as runtime,
    agent_runtime_catalog,
    agent_runtime_sec_dimensions as sec_dimensions,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_geographical_revenue_query_accepts_company_name_followed_by_year():
    assert sec_dimensions.geographical_revenue_query_applies(
        "分析英伟达2026财年总营收以及美国市场营收"
    )
    assert not sec_dimensions.geographical_revenue_query_applies("分析英伟达2026财年总营收")


def test_cjk_company_alias_can_be_immediately_followed_by_fiscal_year():
    def normalize(value):
        return re.sub(r"\s+", "", str(value or "")).casefold()

    assert agent_runtime_catalog._alias_match_score(
        "分析英伟达2026财年营收",
        normalize("分析英伟达2026财年营收"),
        "英伟达",
        normalize,
    ) > 0


def test_resolve_us_geographical_revenue_reads_dimensional_xbrl_facts(tmp_path):
    company_dir = tmp_path / "NVDA-NVIDIA-CORP"
    report_id = "2026-10-K-test"
    report_dir = company_dir / "reports" / report_id
    source_url = "https://www.sec.gov/Archives/edgar/data/1045810/test.htm"
    _write_json(
        report_dir / "manifest.json",
        {
            "market": "US",
            "company_id": "US:0001045810",
            "filing_id": "US:0001045810:test",
            "parse_run_id": "run-test",
            "report_id": report_id,
            "fiscal_year": 2026,
            "period_end": "2026-01-25",
            "source_url": source_url,
        },
    )
    _write_json(
        report_dir / "xbrl" / "facts_raw.json",
        {
            "facts": [
                {
                    "fact_id": "fact-us",
                    "concept": "us-gaap:Revenues",
                    "value_text": "149,617",
                    "value_numeric": "149617000000",
                    "unit": "USD",
                    "scale": "6",
                    "period_start": "2025-01-27",
                    "period_end": "2026-01-25",
                    "duration_days": 364,
                    "fiscal_year": 2026,
                    "dimensions": {"srt:StatementGeographicalAxis": "country:US"},
                    "html_anchor": "f-1177",
                },
                {
                    "fact_id": "fact-cn",
                    "concept": "us-gaap:Revenues",
                    "value_text": "19,677",
                    "value_numeric": "19677000000",
                    "unit": "USD",
                    "period_start": "2025-01-27",
                    "period_end": "2026-01-25",
                    "duration_days": 364,
                    "fiscal_year": 2026,
                    "dimensions": {
                        "srt:StatementGeographicalAxis": "nvda:ChinaIncludingHongKongMember"
                    },
                    "html_anchor": "f-1183",
                },
                {
                    "fact_id": "fact-consolidated",
                    "concept": "us-gaap:Revenues",
                    "value_numeric": "215938000000",
                    "unit": "USD",
                    "period_start": "2025-01-27",
                    "period_end": "2026-01-25",
                    "duration_days": 364,
                    "fiscal_year": 2026,
                    "dimensions": {},
                    "html_anchor": "f-72",
                },
                {
                    "fact_id": "fact-basis",
                    "concept": (
                        "us-gaap:ScheduleOfRevenuesFromExternalCustomersAndLongLivedAssetsByGeographicalAreasTableTextBlock"
                    ),
                    "value_text": "Revenue by geographic area is based upon the location of customers' headquarters.",
                    "period_start": "2025-01-27",
                    "period_end": "2026-01-25",
                    "duration_days": 364,
                    "fiscal_year": 2026,
                    "html_anchor": "f-1176",
                },
            ]
        },
    )

    result = sec_dimensions.resolve_us_geographical_revenue(
        company_dir,
        report_id,
        read_json_file=lambda path: json.loads(path.read_text()) if path.is_file() else None,
    )

    assert result is not None
    assert [(row["region"], row["value"], row["source_anchor"]) for row in result["rows"]] == [
        ("美国", "149617000000", "f-1177"),
        ("中国（含香港）", "19677000000", "f-1183"),
    ]
    assert result["basis"]["source_anchor"] == "f-1176"
    rendered = sec_dimensions.render_us_geographical_revenue_context(result)
    assert "不得回答“未提供地区细分”" in rendered
    assert "149.617 billion USD" in rendered
    assert "1,496.17 亿美元" in rendered
    assert "f-1177" in rendered


def test_financial_answer_replaces_false_geography_gap_with_sec_fact():
    result = {
        "period": "2026-01-25",
        "rows": [
            {
                "member": "country:US",
                "region": "美国",
                "region_en": "United States",
                "value": "149617000000",
            }
        ],
    }
    draft = (
        "## 结论\n"
        "- 英伟达 2026 财年总营收为 215.938 billion USD。\n"
        "- 当前数据未提供地区细分，无法单独分析美国市场营收。\n\n"
        "## 引用来源\n[1] source_type=wiki_metrics"
    )

    corrected = runtime._correct_us_geographical_revenue_answer(draft, result)

    assert "当前数据未提供地区细分" not in corrected
    assert "美国为 149.617 billion USD（1,496.17 亿美元）" in corrected
