from __future__ import annotations

import html
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg


OUT = Path(os.environ.get("SIQ_ZTE_EXPORT_PATH", Path(__file__).with_name("中兴通讯_financial_all_metrics_wide_明细.xlsx"))).expanduser()


def connect():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)
    return psycopg.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "15432")),
        dbname=os.environ.get("PGDATABASE", "siq"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )

SUMMARY_SQL = """
SELECT
  w.stock_code,
  w.stock_name,
  w.report_year,
  w.report_period,
  w.period_key,
  COALESCE(b.cnt,0) AS balance_metrics,
  COALESCE(i.cnt,0) AS income_metrics,
  COALESCE(c.cnt,0) AS cash_flow_metrics,
  COALESCE(k.cnt,0) AS key_metrics_count,
  COALESCE(a.cnt,0) AS all_metrics_count
FROM pdf2md.financial_all_metrics_wide w
LEFT JOIN LATERAL (SELECT count(*) cnt FROM jsonb_each(w.balance_sheet)) b ON true
LEFT JOIN LATERAL (SELECT count(*) cnt FROM jsonb_each(w.income_statement)) i ON true
LEFT JOIN LATERAL (SELECT count(*) cnt FROM jsonb_each(w.cash_flow_statement)) c ON true
LEFT JOIN LATERAL (SELECT count(*) cnt FROM jsonb_each(w.key_metrics)) k ON true
LEFT JOIN LATERAL (SELECT count(*) cnt FROM jsonb_each(w.all_metrics)) a ON true
WHERE w.stock_name='中兴通讯'
ORDER BY w.period_key;
"""

DETAIL_SQL = """
SELECT
  w.stock_code AS 股票代码,
  w.stock_name AS 公司简称,
  w.report_year AS 报告年份,
  w.report_period AS 报告期间,
  w.period_key AS 期间,
  COALESCE(m.value->>'item_name', m.value->>'metric_name', m.key) AS 指标名称,
  m.key AS 标准指标名,
  m.value->>'value' AS 数值,
  m.value->>'raw_value' AS 原始值,
  m.value->>'unit' AS 单位,
  m.value->>'statement_id' AS 报表ID,
  m.value->>'scope' AS 报表口径,
  m.value->'source'->>'table_index' AS 来源表格序号,
  m.value->'source'->>'line' AS 来源行号
FROM pdf2md.financial_all_metrics_wide w
CROSS JOIN LATERAL jsonb_each(w.all_metrics) AS m(key, value)
WHERE w.stock_name='中兴通讯'
ORDER BY w.period_key, 报表ID NULLS LAST, 来源表格序号 NULLS LAST, 指标名称;
"""


def fetch(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
    return cols, rows


def col_name(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def sheet_xml(cols: list[str], rows: list[tuple[Any, ...]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return "<c/>"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"<c><v>{value}</v></c>"
        text = html.escape(str(value), quote=False)
        return f'<c t="inlineStr"><is><t>{text}</t></is></c>'

    xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_index, row in enumerate([cols] + [list(row) for row in rows], start=1):
        xml.append(f'<row r="{row_index}">')
        for col_index, value in enumerate(row, start=1):
            ref = f"{col_name(col_index)}{row_index}"
            rendered = cell(value)
            if rendered == "<c/>":
                xml.append(f'<c r="{ref}"/>')
            else:
                xml.append(rendered.replace("<c", f'<c r="{ref}"', 1))
        xml.append("</row>")
    xml.append("</sheetData></worksheet>")
    return "".join(xml)


def write_xlsx(summary_rows: list[tuple[Any, ...]], detail_cols: list[str], detail_rows: list[tuple[Any, ...]]) -> None:
    summary_cols = [
        "股票代码",
        "公司简称",
        "报告年份",
        "报告期间",
        "期间",
        "资产负债表指标数",
        "利润表指标数",
        "现金流量表指标数",
        "关键指标数",
        "全部指标数",
    ]
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="宽表7条汇总" sheetId="1" r:id="rId1"/><sheet name="展开明细" sheetId="2" r:id="rId2"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml(summary_cols, summary_rows))
        zf.writestr("xl/worksheets/sheet2.xml", sheet_xml(detail_cols, detail_rows))


def main() -> None:
    _, summary_rows = fetch(SUMMARY_SQL)
    detail_cols, detail_rows = fetch(DETAIL_SQL)
    write_xlsx(summary_rows, detail_cols, detail_rows)
    print(OUT)
    print(f"summary_rows={len(summary_rows)} detail_rows={len(detail_rows)} generated_at={datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
