"""Financial runtime display formatting helpers."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def _parse_number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("€", "").replace("%", "").strip()
    if text in {"", "-", "未返回", "N/A", "None", "null"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _row_numeric_values(row: list[str] | None) -> list[float]:
    values: list[float] = []
    for cell in (row or [])[1:]:
        text = str(cell or "").strip()
        if not text or re.fullmatch(r"\[\d+\]", text):
            continue
        value = _parse_number(text)
        if value is not None:
            values.append(value)
    return values


def _fmt_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "未返回"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.{digits}f}"
    return str(value)


def _calculator_per_capita_display(payload: dict[str, Any] | None, *, preferred: str = "cny_10k") -> str:
    if not payload or payload.get("status") != "ok":
        return "未返回"
    result = payload.get("result") or {}
    if preferred == "native_10k":
        value = result.get("native_10k_per")
        unit = result.get("native_10k_per_unit")
    elif preferred == "native_per":
        value = result.get("native_per")
        unit = result.get("native_per_unit")
    else:
        value = result.get("cny_10k_per")
        unit = result.get("cny_10k_per_unit")
    if value in (None, "") or not unit:
        return "未返回"
    try:
        digits = 4 if preferred in {"native_10k", "cny_10k"} else 2
        formatted = float(value)
        return f"{formatted:,.{digits}f}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def _calculator_formula_text(payload: dict[str, Any] | None) -> str:
    formula = (payload or {}).get("formula") or []
    if not formula:
        return "未返回"
    return "；".join(str(item) for item in formula)


def _statement_row_table(row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "pdf_page_number": (row or {}).get("pdf_page"),
        "table_index": (row or {}).get("table_index"),
        "line": (row or {}).get("md_line"),
    }


def _table_trace(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    report_id: str,
    task_id: Any,
    table: dict[str, Any],
    links: str = "",
) -> str:
    pdf_page = table.get("pdf_page_number") or table.get("pdf_page") or "未返回"
    table_index = table.get("table_index") if table.get("table_index") not in (None, "") else "未返回"
    md_line = table.get("line") or table.get("md_line") or table.get("markdown_line") or "未返回"
    return (
        f"[H{index}] source_type={source_type}, file={file}, metric={metric}, period={report_id}, "
        f"task_id={task_id or '未返回'}, pdf_page={pdf_page}, table_index={table_index}, md_line={md_line}"
        + (f"，{links}" if links else "")
    )


PerCapitaCalculator = Callable[..., dict[str, Any] | None]
TableSourceLinks = Callable[[Any, Any, Any], str]


def _table_trace_with_links(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    report_id: str,
    task_id: Any,
    table: dict[str, Any],
    table_source_links: TableSourceLinks | None,
) -> str:
    pdf_page = table.get("pdf_page_number") or table.get("pdf_page") or "未返回"
    table_index = table.get("table_index") if table.get("table_index") not in (None, "") else "未返回"
    links = table_source_links(task_id, pdf_page, table_index) if table_source_links else ""
    return _table_trace(
        index,
        source_type=source_type,
        file=file,
        metric=metric,
        report_id=report_id,
        task_id=task_id,
        table=table,
        links=links,
    )


def _render_generic_human_efficiency_evidence_markdown(
    result: dict[str, Any],
    *,
    calculator_per_capita: PerCapitaCalculator,
    table_source_links: TableSourceLinks | None = None,
) -> str:
    values = result.get("values") or {}
    rows = result.get("rows") or {}
    task_id = result.get("task_id")
    report_id = str(result.get("report_id") or "2025-annual")
    revenue = values.get("revenue_2025")
    parent_profit = values.get("parent_profit_2025")
    net_profit = values.get("net_profit_2025")
    employees = values.get("employees_2025")
    compensation = values.get("compensation_increase_2025")
    profit_base = parent_profit if parent_profit is not None else net_profit
    per_revenue = calculator_per_capita(revenue, amount_unit="元", count=employees, currency="CNY")
    per_profit = calculator_per_capita(profit_base, amount_unit="元", count=employees, currency="CNY")
    per_compensation = calculator_per_capita(compensation, amount_unit="元", count=employees, currency="CNY")

    employee_result = result.get("employee_result") or {}
    employee_table = {
        "pdf_page_number": employee_result.get("pdf_page"),
        "table_index": employee_result.get("table_index"),
        "line": employee_result.get("md_line"),
    }
    compensation_table = result.get("compensation_table") or {}

    lines = [
        "## 财务指标溯源补充",
        "- 后端已按指标重新定位 PDF 页和表格；以下为本轮人效分析中财务指标/派生指标的可审计来源。",
        "",
        "| 指标 | 数值/公式 | 口径 | PDF页/表格 |",
        "| --- | --- | --- | --- |",
        (
            f"| 营业收入 | 2025: {_fmt_number(revenue, 2)} 元 | 合并利润表 / 营业收入 | "
            f"pdf_page={(rows.get('revenue') or {}).get('pdf_page') or '未返回'}, "
            f"table_index={(rows.get('revenue') or {}).get('table_index') or '未返回'} |"
        ),
        (
            f"| 年末员工数 | 2025: {_fmt_number(employees, 0)} 人 | "
            "报告期末母公司和主要子公司的员工情况 / 在职员工的数量合计 | "
            f"pdf_page={employee_table.get('pdf_page_number') or '未返回'}, "
            f"table_index={employee_table.get('table_index') or '未返回'} |"
        ),
        (
            f"| 人均营收 | {_fmt_number(revenue, 2)} 元 / {_fmt_number(employees, 0)} 人 = "
            f"{_calculator_per_capita_display(per_revenue)} | "
            f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_revenue)} | "
            "见营业收入 + 年末员工数来源 |"
        ),
    ]
    if profit_base is not None:
        profit_label = "归母净利润" if parent_profit is not None else "净利润"
        profit_row = rows.get("parent_profit") if parent_profit is not None else rows.get("net_profit")
        lines.extend(
            [
                (
                    f"| {profit_label} | 2025: {_fmt_number(profit_base, 2)} 元 | "
                    f"合并利润表 / {profit_label} | "
                    f"pdf_page={(profit_row or {}).get('pdf_page') or '未返回'}, "
                    f"table_index={(profit_row or {}).get('table_index') or '未返回'} |"
                ),
                (
                    f"| 人均{profit_label} | {_fmt_number(profit_base, 2)} 元 / "
                    f"{_fmt_number(employees, 0)} 人 = {_calculator_per_capita_display(per_profit)} | "
                    f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_profit)} | "
                    f"见{profit_label} + 年末员工数来源 |"
                ),
            ]
        )
    if compensation is not None:
        compensation_page = compensation_table.get("pdf_page_number") or compensation_table.get("pdf_page") or "未返回"
        lines.extend(
            [
                (
                    f"| 人力成本 | 2025 本期增加: {_fmt_number(compensation, 2)} 元 | "
                    "应付职工薪酬列示 / 合计 / 本期增加 | "
                    f"pdf_page={compensation_page}, "
                    f"table_index={compensation_table.get('table_index') or '未返回'} |"
                ),
                (
                    f"| 人均人力成本 | {_fmt_number(compensation, 2)} 元 / "
                    f"{_fmt_number(employees, 0)} 人 = {_calculator_per_capita_display(per_compensation)} | "
                    f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_compensation)} | "
                    "见人力成本 + 年末员工数来源 |"
                ),
            ]
        )

    lines.extend(["", "## 指标级引用来源"])
    refs = [
        (
            "营业收入",
            "wiki_metrics",
            (rows.get("revenue") or {}).get("file") or "metrics/three_statements.json",
            _statement_row_table(rows.get("revenue")),
        ),
        ("年末员工数", "wiki_report_table", f"reports/{report_id}/report.md", employee_table),
    ]
    if parent_profit is not None:
        refs.append(
            (
                "归母净利润",
                "wiki_metrics",
                (rows.get("parent_profit") or {}).get("file") or "metrics/three_statements.json",
                _statement_row_table(rows.get("parent_profit")),
            )
        )
    elif net_profit is not None:
        refs.append(
            (
                "净利润",
                "wiki_metrics",
                (rows.get("net_profit") or {}).get("file") or "metrics/three_statements.json",
                _statement_row_table(rows.get("net_profit")),
            )
        )
    if compensation_table:
        refs.append(("应付职工薪酬", "wiki_report_table", f"reports/{report_id}/report.md", compensation_table))
    for index, (metric, source_type, file, table) in enumerate(refs, start=1):
        lines.append(
            _table_trace_with_links(
                index,
                source_type=source_type,
                file=file,
                metric=metric,
                report_id=report_id,
                task_id=task_id,
                table=table or {},
                table_source_links=table_source_links,
            )
        )
    return "\n".join(lines)


def render_human_efficiency_evidence_markdown(
    result: dict[str, Any],
    *,
    calculator_per_capita: PerCapitaCalculator,
    table_source_links: TableSourceLinks | None = None,
) -> str:
    if result.get("mode") == "generic_cny":
        return _render_generic_human_efficiency_evidence_markdown(
            result,
            calculator_per_capita=calculator_per_capita,
            table_source_links=table_source_links,
        )

    values = result.get("values") or {}
    tables = result.get("tables") or {}
    task_id = result.get("task_id")
    report_id = str(result.get("report_id") or "2025-annual")
    revenue_2025 = values.get("revenue_2025")
    personnel_2025 = values.get("personnel_2025")
    employees_2025 = values.get("employees_2025")
    per_revenue = calculator_per_capita(
        revenue_2025,
        amount_unit="百万欧元",
        count=employees_2025,
        currency="EUR",
    )
    per_personnel = calculator_per_capita(
        personnel_2025,
        amount_unit="百万欧元",
        count=employees_2025,
        currency="EUR",
    )

    lines = [
        "## 财务指标溯源补充",
        "- 后端已按指标重新定位 PDF 页和表格；以下为本轮人效分析中财务指标/派生指标的可审计来源。",
        "",
        "| 指标 | 数值/公式 | 口径 | PDF页/表格 |",
        "| --- | --- | --- | --- |",
    ]
    income_table = tables.get("income") or {}
    personnel_table = tables.get("personnel") or {}
    employees_table = tables.get("employees") or {}
    regional_sales_table = tables.get("regional_sales") or {}
    income_page = income_table.get("pdf_page_number") or income_table.get("pdf_page") or "未返回"
    personnel_page = personnel_table.get("pdf_page_number") or personnel_table.get("pdf_page") or "未返回"
    employees_page = employees_table.get("pdf_page_number") or employees_table.get("pdf_page") or "未返回"
    lines.append(
        f"| 营业收入 | 2025: €{_fmt_number(revenue_2025, 0)} million；"
        f"2024: €{_fmt_number(values.get('revenue_2024'), 0)} million | "
        f"Statement of income / Sales revenue | pdf_page={income_page}, "
        f"table_index={income_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 人力成本 | 2025: €{_fmt_number(personnel_2025, 0)} million；"
        f"2024: €{_fmt_number(values.get('personnel_2024'), 0)} million | "
        f"Personnel expenses | pdf_page={personnel_page}, "
        f"table_index={personnel_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 年末员工数 | 2025: {_fmt_number(employees_2025, 0)}；"
        f"2024: {_fmt_number(values.get('employees_2024'), 0)} | "
        f"Number of employees as of December 31 | pdf_page={employees_page}, "
        f"table_index={employees_table.get('table_index') or '未返回'} |"
    )
    lines.append(
        f"| 人均营收 | €{_fmt_number(revenue_2025, 0)} million / {_fmt_number(employees_2025, 0)} = "
        f"{_calculator_per_capita_display(per_revenue, preferred='native_per')} | "
        f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_revenue)} | "
        "见营业收入 + 年末员工数来源 |"
    )
    lines.append(
        f"| 人均人力成本 | €{_fmt_number(personnel_2025, 0)} million / "
        f"{_fmt_number(employees_2025, 0)} = "
        f"{_calculator_per_capita_display(per_personnel, preferred='native_per')} | "
        f"派生计算（financial_calculator.py）：{_calculator_formula_text(per_personnel)} | "
        "见人力成本 + 年末员工数来源 |"
    )
    if result.get("regional_rows"):
        for row in result["regional_rows"]:
            regional_sales_page = regional_sales_table.get("pdf_page_number") or regional_sales_table.get("pdf_page")
            lines.append(
                f"| {row['region']} 人均营收 | €{_fmt_number(row['sales_million_eur'], 0)} million / "
                f"{_fmt_number(row['employees'], 0)} = "
                f"{_calculator_per_capita_display(row.get('revenue_per_employee'), preferred='native_per')} | "
                f"派生计算（financial_calculator.py）：{_calculator_formula_text(row.get('revenue_per_employee'))} | "
                f"sales: pdf_page={regional_sales_page or '未返回'}, "
                f"table_index={regional_sales_table.get('table_index') or '未返回'}；"
                f"employees: pdf_page={employees_table.get('pdf_page_number') or '未返回'}, "
                f"table_index={employees_table.get('table_index') or '未返回'} |"
            )

    lines.extend(["", "## 指标级引用来源"])
    refs = [
        ("营业收入", "wiki_report_table", "reports/%s/report.md" % report_id, income_table),
        ("人力成本", "wiki_report_table", "reports/%s/report.md" % report_id, personnel_table),
        ("年末员工数", "wiki_report_table", "reports/%s/report.md" % report_id, employees_table),
    ]
    if tables.get("average_employees"):
        refs.append(("平均员工数", "wiki_report_table", "reports/%s/report.md" % report_id, tables["average_employees"]))
    if regional_sales_table:
        refs.append(
            (
                "区域销售/location of company",
                "wiki_report_table",
                "reports/%s/report.md" % report_id,
                regional_sales_table,
            )
        )
    for index, (metric, source_type, file, table) in enumerate(refs, start=1):
        lines.append(
            _table_trace_with_links(
                index,
                source_type=source_type,
                file=file,
                metric=metric,
                report_id=report_id,
                task_id=task_id,
                table=table or {},
                table_source_links=table_source_links,
            )
        )
    return "\n".join(lines)


__all__ = [
    "_calculator_formula_text",
    "_calculator_per_capita_display",
    "_fmt_number",
    "_parse_number",
    "_row_numeric_values",
    "_statement_row_table",
    "_table_trace",
    "_render_generic_human_efficiency_evidence_markdown",
    "render_human_efficiency_evidence_markdown",
]
