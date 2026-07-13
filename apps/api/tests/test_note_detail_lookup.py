import json
import sys
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[3]
    / "agents"
    / "hermes"
    / "profiles"
    / "shared"
    / "scripts"
)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import note_detail_lookup as lookup  # noqa: E402


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _table(table_index: int, line: int, heading: str, preview: str) -> dict[str, object]:
    return {
        "table_index": table_index,
        "line": line,
        "heading": heading,
        "preview": preview,
        "pdf_page_number": 192 if table_index < 367 else 193,
        "source_confidence": "medium",
    }


def test_note_detail_lookup_recovers_tables_before_continuation_anchor(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = wiki_root / "companies" / "002594-比亚迪"
    report_dir = company_dir / "reports" / "2025-annual"
    semantic_dir = company_dir / "semantic"
    report_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)

    _write_json(
        company_dir / "company.json",
        {
            "company_id": "002594-比亚迪",
            "stock_code": "002594",
            "company_short_name": "比亚迪",
            "primary_report_id": "2025-annual",
            "reports": [
                {
                    "report_id": "2025-annual",
                    "task_id": "cae95ef6-293f-455a-bde6-024ae45a2bc4",
                }
            ],
        },
    )

    report_lines = [
        "# 19、商誉",
        "# （1） 商誉原值",
        "<table><tr><td>项目</td><td>年末余额</td></tr><tr><td>合计</td><td>999</td></tr></table>",
        "# 20、其他事项",
        "# （1） 其他事项明细",
        "<table><tr><td>项目</td><td>年末余额</td></tr><tr><td>合计</td><td>888</td></tr></table>",
        "# 七、合并财务报表主要项目注释（续）",
        "",
        "# 19、商誉",
        "# （1） 商誉原值",
        "<table><tr><td>项目</td><td>年末余额</td></tr><tr><td>合计</td><td>4,437,242</td></tr></table>",
        "# （2） 商誉减值准备",
        "<table><tr><td>项目</td><td>年末余额</td></tr><tr><td>合计</td><td>9,671</td></tr></table>",
        "# （3） 商誉所在资产组或资产组组合的相关信息",
        "<table><tr><td>资产组</td><td>是否一致</td></tr><tr><td>Juno Newco</td><td>是</td></tr></table>",
        "# 七、合并财务报表主要项目注释（续）",
        "# 19、商誉（续）",
        "# （4） 可收回金额的具体确定方法",
        "<table><tr><td>资产组</td><td>折现率</td></tr><tr><td>Juno Newco</td><td>14.67%</td></tr></table>",
        "",
        "# 20、长期待摊费用",
        "# （1） 长期待摊费用变动",
        "<table><tr><td>项目</td><td>年末余额</td></tr><tr><td>合计</td><td>4,171,222</td></tr></table>",
    ]
    (report_dir / "report.md").write_text("\n".join(report_lines), encoding="utf-8")
    _write_json(
        report_dir / "report.json",
        {
            "tables": [
                _table(300, 3, "（1） 商誉原值", "合计 999"),
                _table(301, 6, "（1） 其他事项明细", "合计 888"),
                _table(364, 11, "（1） 商誉原值", "合计 4,437,242"),
                _table(365, 13, "（2） 商誉减值准备", "合计 9,671"),
                _table(366, 15, "（3） 商誉所在资产组或资产组组合的相关信息", "Juno Newco 是"),
                _table(367, 19, "（4） 可收回金额的具体确定方法", "Juno Newco 14.67%"),
                _table(368, 23, "（1） 长期待摊费用变动", "合计 4,171,222"),
            ]
        },
    )
    _write_json(
        semantic_dir / "document_links.json",
        {
            "links": [
                {
                    "document_link_id": "existing-367",
                    "source": {
                        "kind": "note",
                        "name": "商誉",
                        "title": "商誉",
                        "note_ref": "七、19",
                        "note_title": "商誉",
                        "line": 17,
                    },
                    "target": {
                        "kind": "note_table",
                        "name": "（4） 可收回金额的具体确定方法",
                        "title": "（4） 可收回金额的具体确定方法",
                        "note_ref": "七、19",
                        "note_title": "商誉",
                        "line": 19,
                        "md_line": 19,
                        "pdf_page_number": 193,
                        "table_index": 367,
                        "preview": "Juno Newco 14.67%",
                    },
                    "relation": {
                        "semantic_relation": "detail_disclosure",
                        "confidence": "high",
                    },
                    "confidence": "high",
                }
            ]
        },
    )

    monkeypatch.setattr(lookup, "WIKI_BASE", wiki_root)

    result = lookup.resolve_note_detail_tables("比亚迪", "商誉", limit=8)

    tables_by_index = {table["table_index"]: table for table in result["tables"]}
    assert set(tables_by_index) == {364, 365, 366, 367}
    assert all(tables_by_index[index]["source_type"] == "wiki_report_table" for index in (364, 365, 366))
    assert tables_by_index[364]["file"] == "reports/2025-annual/report.json"
    assert tables_by_index[367]["source_type"] == "wiki_document_links"
    assert {table["financial_scope"] for table in tables_by_index.values()} == {"consolidated"}
    assert 300 not in tables_by_index
    assert 368 not in tables_by_index


def test_report_financial_scope_requires_explicit_section_heading(tmp_path):
    report_md = tmp_path / "report.md"
    report_md.write_text(
        "\n".join(
            (
                "# 四 合并财务报表项目附注(续)",
                "# (21) 商誉",
                "<table></table>",
                "# 十七 母公司财务报表主要项目注释",
                "# (3) 长期股权投资",
                "<table></table>",
            )
        ),
        encoding="utf-8",
    )

    assert lookup.report_financial_scope(report_md, 3) == "consolidated"
    assert lookup.report_financial_scope(report_md, 6) == "parent_company"
