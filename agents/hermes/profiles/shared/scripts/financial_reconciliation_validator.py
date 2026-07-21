#!/usr/bin/env python3
"""Deterministic financial statement/note reconciliation checks for SIQ."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]


def _default_wiki_root() -> Path:
    if value := os.getenv("SIQ_WIKI_ROOT"):
        return Path(value).expanduser()
    if value := os.getenv("SIQ_DATA_ROOT"):
        return Path(value).expanduser() / "wiki"
    if value := os.getenv("SIQ_PROJECT_ROOT"):
        return Path(value).expanduser() / "data" / "wiki"
    return REPO_ROOT / "data" / "wiki"


WIKI_ROOT = _default_wiki_root()
SCRIPTS_DIR = Path(__file__).resolve().parent
NOTE_DETAIL_LOOKUP_PATH = SCRIPTS_DIR / "note_detail_lookup.py"
ZERO_AMOUNT_MARKERS = {"-", "—", "–", "不适用", "N/A", "NA", "nil", "Nil", ""}
CHINA_GOODWILL_ACCOUNTING_RULES = (
    "中国上市公司年报口径下，商誉来自非同一控制下企业合并形成的超额对价。",
    "商誉应至少在每年年度终了进行减值测试，并分摊至相关资产组或资产组组合。",
    "商誉减值损失计入当期损益；已确认的商誉减值损失以后会计期间不得转回。",
    "回答时必须区分商誉账面原值、减值准备余额、账面价值、当期减值损失和减值准备变动。",
)


class ReconciliationError(ValueError):
    pass


def load_note_detail_lookup() -> Any:
    spec = importlib.util.spec_from_file_location("siq_note_detail_lookup", NOTE_DETAIL_LOOKUP_PATH)
    if spec is None or spec.loader is None:
        raise ReconciliationError(f"cannot load {NOTE_DETAIL_LOOKUP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("siq_note_detail_lookup", module)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def as_decimal(value: Any, field_name: str = "value") -> Decimal:
    if value is None or value == "":
        raise ReconciliationError(f"{field_name} is empty")
    text = str(value).strip().replace(",", "").replace("，", "")
    negative = text.startswith(("(", "（")) and (")" in text or "）" in text)
    text = text.strip("()（） ")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ReconciliationError(f"{field_name} is not numeric: {value!r}") from exc
    return -abs(number) if negative else number


def as_table_decimal(value: Any, field_name: str = "value") -> Decimal:
    text = str(value if value is not None else "").strip()
    if text in ZERO_AMOUNT_MARKERS:
        return Decimal("0")
    return as_decimal(value, field_name)


def plain(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fixed(value: Decimal, digits: int = 2) -> str:
    return format(value.quantize(Decimal("1").scaleb(-digits), rounding=ROUND_HALF_UP), f".{digits}f")


def report_year(report_id: str) -> int | None:
    match = re.search(r"(20\d{2})", report_id or "")
    return int(match.group(1)) if match else None


def table_source(table: dict[str, Any]) -> dict[str, Any]:
    return {
        key: table.get(key)
        for key in ("source_type", "file", "metric", "unit", "pdf_page", "table_index", "md_line", "task_id")
    }


def row_label(record: dict[str, Any]) -> str:
    if not record:
        return ""
    return str(next(iter(record.values()), "") or "").strip()


def numeric_period_keys(record: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key, value in list(record.items())[1:]:
        try:
            as_table_decimal(value, str(key))
        except ReconciliationError:
            continue
        keys.append(str(key))
    return keys


def period_amount_from_record(
    record: dict[str, Any],
    report_id: str,
    *,
    current: bool = True,
) -> tuple[Decimal, str] | None:
    keys = numeric_period_keys(record)
    if not keys:
        return None

    year = report_year(report_id)
    preferred: list[str] = []
    if year:
        target_year = str(year if current else year - 1)
        preferred.extend([key for key in keys if target_year in key])

    if current:
        preferred.extend(
            [
                key
                for key in keys
                if any(term in key for term in ("期末", "年末", "本年年末", "本期期末"))
            ]
        )
    else:
        preferred.extend(
            [
                key
                for key in keys
                if any(term in key for term in ("期初", "年初", "上年年末", "上期期末"))
            ]
        )

    preferred.extend(reversed(keys) if current else keys)
    for key in preferred:
        if key in record:
            try:
                return as_table_decimal(record.get(key), key), key
            except ReconciliationError:
                continue
    return None


def find_table_row_amount(
    table: dict[str, Any],
    report_id: str,
    *,
    include_any: tuple[str, ...],
    include_all: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
    current: bool = True,
) -> dict[str, Any] | None:
    for record in table.get("records") or []:
        if not isinstance(record, dict):
            continue
        label = row_label(record)
        if include_any and not any(term in label for term in include_any):
            continue
        if include_all and not all(term in label for term in include_all):
            continue
        if exclude_any and any(term in label for term in exclude_any):
            continue
        amount = period_amount_from_record(record, report_id, current=current)
        if amount is None:
            continue
        value, key = amount
        return {"value": value, "period_key": key, "label": label, "record": record}
    return None


def record_period_amount(
    record: dict[str, Any] | None,
    report_id: str,
    *,
    current: bool,
) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    amount = period_amount_from_record(record, report_id, current=current)
    if amount is None:
        return None
    value, key = amount
    return {"value": value, "period_key": key, "label": row_label(record), "record": record}


def adjacent_blank_row_amount(
    table: dict[str, Any],
    report_id: str,
    anchor_record: dict[str, Any],
    *,
    offset: int,
    current: bool,
) -> dict[str, Any] | None:
    records = [record for record in table.get("records") or [] if isinstance(record, dict)]
    try:
        anchor_index = next(index for index, record in enumerate(records) if record is anchor_record)
    except StopIteration:
        try:
            anchor_index = records.index(anchor_record)
        except ValueError:
            return None

    target_index = anchor_index + offset
    if target_index < 0 or target_index >= len(records):
        return None
    target = records[target_index]
    if row_label(target):
        return None
    return record_period_amount(target, report_id, current=current)


def find_compact_goodwill_amounts(tables: list[dict[str, Any]], report_id: str) -> dict[str, Any] | None:
    """Handle compact Chinese annual-report goodwill notes.

    Some listed-company reports disclose goodwill in one comparative table with
    rows like 小计 / 减:减值准备 / 账面价值 instead of separate 原值 and 减值准备
    movement tables. This is common enough that the validator should treat it
    as first-class evidence rather than returning note_tables_not_found.
    """

    for table in tables:
        metric = str(table.get("metric") or "")
        if "商誉" not in metric:
            continue
        gross = find_table_row_amount(
            table,
            report_id,
            include_any=("小计", "合计"),
            exclude_any=("账面", "净额", "净值", "减值", "准备"),
        )
        impairment = find_table_row_amount(
            table,
            report_id,
            include_any=("减值准备",),
            current=True,
        )
        net = find_table_row_amount(
            table,
            report_id,
            include_any=("账面价值", "账面净额", "账面净值"),
            current=True,
        )
        if impairment is not None:
            # Some reports omit labels on the subtotal rows. In a compact goodwill
            # table, the blank numeric row immediately before the allowance is the
            # gross amount and the one immediately after it is the carrying amount.
            gross = gross or adjacent_blank_row_amount(
                table,
                report_id,
                impairment["record"],
                offset=-1,
                current=True,
            )
            net = net or adjacent_blank_row_amount(
                table,
                report_id,
                impairment["record"],
                offset=1,
                current=True,
            )
        if gross is None or impairment is None:
            continue

        prior_gross = record_period_amount(gross["record"], report_id, current=False)
        prior_impairment = record_period_amount(impairment["record"], report_id, current=False)
        prior_net = record_period_amount(net["record"], report_id, current=False) if net is not None else None
        return {
            "mode": "compact_goodwill_table",
            "gross": gross,
            "impairment": impairment,
            "note_net": net,
            "prior_gross": prior_gross,
            "prior_impairment": prior_impairment,
            "prior_note_net": prior_net,
            "gross_source": table_source(table),
            "impairment_source": table_source(table),
            "note_net_source": table_source(table) if net else None,
        }
    return None


def find_separate_goodwill_amounts(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    gross_table = next((table for table in tables if "账面原值" in str(table.get("metric") or "")), None)
    impairment_table = next((table for table in tables if "减值准备" in str(table.get("metric") or "")), None)
    if not gross_table or not impairment_table:
        return None
    gross = table_total_period_end(gross_table)
    impairment = table_total_period_end(impairment_table)
    if gross is None or impairment is None:
        return None
    return {
        "mode": "separate_movement_tables",
        "gross": {"value": gross, "period_key": "期末余额", "label": "合计"},
        "impairment": {"value": impairment, "period_key": "期末余额", "label": "合计"},
        "note_net": None,
        "prior_gross": None,
        "prior_impairment": None,
        "prior_note_net": None,
        "gross_source": table_source(gross_table),
        "impairment_source": table_source(impairment_table),
        "note_net_source": None,
    }


def amount_with_chinese_unit(number_text: str, unit: str | None) -> Decimal:
    amount = as_decimal(number_text, "amount")
    unit_text = str(unit or "元")
    if "亿" in unit_text:
        return amount * Decimal("100000000")
    if "万" in unit_text:
        return amount * Decimal("10000")
    return amount


def positive_scale(value: Any, default: Decimal = Decimal("1")) -> Decimal:
    try:
        scale = as_decimal(value, "base_scale")
    except ReconciliationError:
        return default
    return scale if scale > 0 else default


def unit_scale(unit: str | None) -> Decimal:
    unit_text = str(unit or "")
    if "亿" in unit_text:
        return Decimal("100000000")
    if "百万" in unit_text:
        return Decimal("1000000")
    if "万" in unit_text:
        return Decimal("10000")
    if "千" in unit_text:
        return Decimal("1000")
    return Decimal("1")


def amount_context(statement: dict[str, Any], amounts: dict[str, Any]) -> dict[str, Any]:
    statement_unit = str(statement.get("unit_hint") or "元")
    statement_scale = positive_scale(statement.get("base_scale"), unit_scale(statement_unit))
    note_source = amounts.get("gross_source") or {}
    explicit_note_unit = str(note_source.get("unit") or "").strip()
    note_unit = explicit_note_unit or statement_unit
    note_scale = unit_scale(explicit_note_unit) if explicit_note_unit else statement_scale
    return {
        "note_amount_unit": note_unit,
        "note_base_scale": note_scale,
        "note_unit_inferred_from_statement": not bool(explicit_note_unit),
        "statement_amount_unit": statement_unit,
        "statement_base_scale": statement_scale,
    }


def source_with_amount_context(
    source: dict[str, Any] | None,
    *,
    unit: str,
    base_scale: Decimal,
    inferred: bool,
) -> dict[str, Any] | None:
    if source is None:
        return None
    enriched = dict(source)
    enriched["unit"] = str(enriched.get("unit") or unit)
    enriched["base_scale"] = plain(base_scale)
    if inferred:
        enriched["unit_inferred_from_statement"] = True
    return enriched


def hundred_million(amount: Decimal, base_scale: Decimal) -> Decimal:
    return amount * base_scale / Decimal("100000000")


def goodwill_note_text_window(company_dir: Path, report_id: str, tables: list[dict[str, Any]]) -> list[tuple[int, str]]:
    report_path = company_dir / "reports" / report_id / "report.md"
    try:
        lines = report_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    md_lines = [
        int(table.get("md_line"))
        for table in tables
        if isinstance(table.get("md_line"), int) or str(table.get("md_line") or "").isdigit()
    ]
    if not md_lines:
        return list(enumerate(lines, start=1))
    start = max(min(md_lines) - 20, 1)
    end = min(max(md_lines) + 40, len(lines))
    return [(idx, lines[idx - 1]) for idx in range(start, end + 1)]


def extract_current_period_goodwill_impairment_loss(
    company_dir: Path,
    report_id: str,
    tables: list[dict[str, Any]],
) -> dict[str, Any] | None:
    patterns = (
        re.compile(
            r"(?:本年|本期|当年|当期).{0,40}(?:计入当期损益|确认|计提).{0,20}金额为(?:人民币)?"
            r"(?P<amount>[-+]?[\d,，]+(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)?"
        ),
        re.compile(
            r"(?:商誉减值损失|减值损失).{0,30}(?:人民币)?"
            r"(?P<amount>[-+]?[\d,，]+(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)?"
        ),
    )
    for line_no, line in goodwill_note_text_window(company_dir, report_id, tables):
        if not any(term in line for term in ("本年", "本期", "当年", "当期", "减值损失")):
            continue
        if not any(term in line for term in ("计入当期损益", "确认", "计提", "减值损失")):
            continue
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            return {
                "amount": plain(amount_with_chinese_unit(match.group("amount"), match.group("unit"))),
                "source": {
                    "source_type": "wiki_report_md",
                    "file": f"reports/{report_id}/report.md",
                    "md_line": line_no,
                    "text_preview": line.strip()[:220],
                },
            }
    return None


def find_company_dir(company_text: str) -> Path | None:
    catalog = read_json(WIKI_ROOT / "_meta/company_catalog.json", {}) or {}
    haystack = str(company_text or "").lower()
    for company in catalog.get("companies") or []:
        values = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            *(company.get("aliases") or []),
        ]
        if any(value and str(value).lower() in haystack for value in values):
            rel = company.get("company_path") or f"companies/{company.get('company_id')}"
            return WIKI_ROOT / rel
    direct = WIKI_ROOT / "companies" / company_text
    if direct.exists():
        return direct
    matches = sorted((WIKI_ROOT / "companies").glob(f"{company_text}-*"))
    return matches[0] if matches else None


def primary_report_id(company_dir: Path, explicit_report_id: str | None = None) -> str:
    if explicit_report_id:
        return explicit_report_id
    company = read_json(company_dir / "company.json", {}) or {}
    return str(company.get("primary_report_id") or "2025-annual")


def metrics_records(company_dir: Path, report_id: str) -> list[dict[str, Any]]:
    candidates = [
        company_dir / "metrics" / "reports" / report_id / "three_statements.json",
        company_dir / "metrics" / "latest" / "three_statements.json",
        company_dir / "metrics" / "three_statements.json",
    ]
    for path in candidates:
        data = read_json(path, {}) or {}
        records = data.get("data", {}).get("metrics") if isinstance(data.get("data"), dict) else data.get("data")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
    return []


def goodwill_statement_record(company_dir: Path, report_id: str) -> dict[str, Any] | None:
    for record in metrics_records(company_dir, report_id):
        if record.get("metric_key") == "goodwill" or record.get("metric_name") == "商誉":
            return record
    return None


def table_total_period_end(table: dict[str, Any]) -> Decimal | None:
    records = table.get("records") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        first_value = next(iter(record.values()), "")
        if "合计" not in str(first_value) and "商誉减值准备" not in str(first_value):
            continue
        for key in ("期末余额", "期末账面余额", "年末余额"):
            if record.get(key) not in (None, ""):
                return as_decimal(record.get(key), key)
        for key, value in reversed(list(record.items())):
            if value not in (None, ""):
                try:
                    return as_decimal(value, key)
                except ReconciliationError:
                    continue
    return None


def goodwill_reconciliation(company_text: str, report_id: str | None = None, tolerance: Decimal = Decimal("1")) -> dict[str, Any]:
    company_dir = find_company_dir(company_text)
    if not company_dir:
        return {"status": "company_not_found", "company": company_text}
    resolved_report_id = primary_report_id(company_dir, report_id)
    statement = goodwill_statement_record(company_dir, resolved_report_id)
    if not statement:
        return {"status": "statement_metric_not_found", "company_id": company_dir.name, "report_id": resolved_report_id}

    statement_net = as_decimal(statement.get("raw_value"), "statement_goodwill")
    note_lookup = load_note_detail_lookup().resolve_note_detail_tables(
        company_dir.name,
        "商誉",
        report_id=resolved_report_id,
        limit=8,
    )
    tables = note_lookup.get("tables") or []
    amounts = find_separate_goodwill_amounts(tables) or find_compact_goodwill_amounts(tables, resolved_report_id)
    if not amounts:
        return {
            "status": "note_tables_not_found",
            "company_id": company_dir.name,
            "report_id": resolved_report_id,
            "found_tables": [table.get("metric") for table in tables],
        }

    units = amount_context(statement, amounts)
    note_scale = units["note_base_scale"]
    statement_scale = units["statement_base_scale"]
    gross = amounts["gross"]["value"]
    # Parenthesized allowances are parsed as negative accounting presentation,
    # but this formula consumes the allowance balance as a positive deduction.
    impairment = abs(amounts["impairment"]["value"])

    calculated_net = gross - impairment
    difference = calculated_net * note_scale - statement_net * statement_scale
    note_net_difference: Decimal | None = None
    note_net = amounts.get("note_net")
    if note_net is not None:
        note_net_difference = (calculated_net - note_net["value"]) * note_scale

    prior_gross = amounts.get("prior_gross")
    prior_impairment = amounts.get("prior_impairment")
    gross_change = gross - prior_gross["value"] if prior_gross is not None else None
    impairment_allowance_change = (
        impairment - abs(prior_impairment["value"])
        if prior_impairment is not None
        else None
    )
    current_period_loss = extract_current_period_goodwill_impairment_loss(company_dir, resolved_report_id, tables)
    status = "pass" if abs(difference) <= tolerance else "fail"
    if note_net_difference is not None and abs(note_net_difference) > tolerance:
        status = "fail"

    movement_checks: dict[str, Any] = {
        "gross_change": plain(gross_change),
        "impairment_allowance_change": plain(impairment_allowance_change),
        "current_period_impairment_loss": current_period_loss.get("amount") if current_period_loss else None,
        "has_current_period_goodwill_impairment": bool(
            (current_period_loss and as_decimal(current_period_loss["amount"], "current_period_impairment_loss") > 0)
            or (impairment_allowance_change is not None and impairment_allowance_change > 0)
        ),
    }
    if current_period_loss and impairment_allowance_change is not None:
        loss = as_decimal(current_period_loss["amount"], "current_period_impairment_loss")
        movement_checks["current_period_loss_vs_allowance_change_difference"] = plain(loss - impairment_allowance_change)

    return {
        "status": status,
        "operation": "goodwill_reconciliation",
        "company_id": company_dir.name,
        "report_id": resolved_report_id,
        "formula": "note_gross - impairment_allowance = statement_carrying_amount",
        "table_mode": amounts.get("mode"),
        "units": {
            key: plain(value) if isinstance(value, Decimal) else value
            for key, value in units.items()
        },
        "result": {
            "note_gross": plain(gross),
            "impairment_allowance": plain(impairment),
            "calculated_net": plain(calculated_net),
            "statement_net": plain(statement_net),
            "difference": plain(difference),
            "note_net": plain(note_net["value"]) if note_net is not None else None,
            "note_net_difference": plain(note_net_difference),
            "tolerance": plain(tolerance),
        },
        "movement_checks": movement_checks,
        "accounting_rules": list(CHINA_GOODWILL_ACCOUNTING_RULES),
        "roles": {
            "statement_net": "statement_carrying_amount",
            "note_gross": "note_gross_cost",
            "impairment_allowance": "impairment_allowance",
            "current_period_impairment_loss": "profit_or_loss_impairment_loss",
        },
        "sources": {
            "statement": {
                "source_type": "wiki_metrics",
                "file": f"metrics/reports/{resolved_report_id}/three_statements.json",
                "metric": statement.get("metric_name"),
                "period": statement.get("period"),
                "unit": units["statement_amount_unit"],
                "base_scale": plain(statement_scale),
                **(statement.get("source") or {}),
            },
            "note_gross": source_with_amount_context(
                amounts.get("gross_source"),
                unit=units["note_amount_unit"],
                base_scale=note_scale,
                inferred=units["note_unit_inferred_from_statement"],
            ),
            "impairment_allowance": source_with_amount_context(
                amounts.get("impairment_source"),
                unit=units["note_amount_unit"],
                base_scale=note_scale,
                inferred=units["note_unit_inferred_from_statement"],
            ),
            "note_net": source_with_amount_context(
                amounts.get("note_net_source"),
                unit=units["note_amount_unit"],
                base_scale=note_scale,
                inferred=units["note_unit_inferred_from_statement"],
            ),
            "current_period_impairment_loss": current_period_loss.get("source") if current_period_loss else None,
        },
        "display": (
            f"{fixed(hundred_million(gross, note_scale), 2)}亿元 - "
            f"{fixed(hundred_million(impairment, note_scale), 2)}亿元 = "
            f"{fixed(hundred_million(calculated_net, note_scale), 2)}亿元；"
            f"主表净额 {fixed(hundred_million(statement_net, statement_scale), 2)}亿元"
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["## 勾稽校验", f"- 状态：{payload.get('status')}"]
    if payload.get("operation") == "goodwill_reconciliation":
        result = payload.get("result") or {}
        movement = payload.get("movement_checks") or {}
        amount_unit = (payload.get("units") or {}).get("note_amount_unit") or "元"
        lines.append(f"- 公式：{payload.get('formula')}")
        lines.append(f"- 结果：{payload.get('display')}")
        lines.append(f"- 差异：{result.get('difference')} 元，容忍阈值 {result.get('tolerance')} 元")
        if result.get("note_net") is not None:
            lines.append(
                f"- 附注账面价值复核：{result.get('note_net')} {amount_unit}；"
                f"差异 {result.get('note_net_difference')} 元"
            )
        if movement and any(movement.get(key) is not None for key in ("gross_change", "impairment_allowance_change", "current_period_impairment_loss")):
            lines.append(
                "- 减值变动："
                f"原值变动 {movement.get('gross_change') or '未识别'} {amount_unit}；"
                f"减值准备变动 {movement.get('impairment_allowance_change') or '未识别'} {amount_unit}；"
                f"当期商誉减值损失 {movement.get('current_period_impairment_loss') or '未识别'} 元。"
            )
            if movement.get("has_current_period_goodwill_impairment"):
                lines.append("- 会计判断：本期存在商誉减值/减值准备增加，不能表述为“本期未新增减值”。")
        lines.append("- 口径：主表商誉=账面净额；附注商誉账面原值=原值；商誉减值准备=备抵；当期减值损失=计入当期损益的减值金额。")
        lines.append("- 中国准则：商誉至少每年进行减值测试；减值损失计入当期损益；已确认的商誉减值以后不得转回。")
    if payload.get("status") not in {"pass", "ok"} and payload.get("reason"):
        lines.append(f"- 原因：{payload['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SIQ financial reconciliation validator")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", dest="output_format")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("goodwill", help="validate goodwill gross - impairment = statement net")
    p.add_argument("--company", required=True)
    p.add_argument("--report-id", default="")
    p.add_argument("--tolerance", default="1")
    p.add_argument("--format", choices=("json", "markdown"), dest="sub_format")
    args = parser.parse_args(argv)

    try:
        if args.command == "goodwill":
            payload = goodwill_reconciliation(args.company, args.report_id or None, as_decimal(args.tolerance, "tolerance"))
        else:
            payload = {"status": "error", "error": f"unsupported command: {args.command}"}
    except ReconciliationError as exc:
        payload = {"status": "error", "operation": args.command, "error": str(exc)}

    output_format = args.sub_format or args.output_format
    if output_format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
