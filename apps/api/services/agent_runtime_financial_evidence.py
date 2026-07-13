"""Build server-trusted numeric evidence for deterministic answer calculations."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Mapping, Sequence

IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
DATE_HEADER_RE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日")
YEAR_RE = re.compile(r"\b(20\d{2})\b")

METRIC_ALIASES = (
    (("商誉", "goodwill"), "goodwill_net"),
    (("营业总收入",), "total_operating_revenue"),
    (("营业收入", "营收", "revenue"), "operating_revenue"),
    (("毛利润", "毛利", "gross profit"), "gross_profit"),
    (("归属于母公司", "归母净利润"), "parent_net_profit"),
    (("净利润", "net profit", "net income"), "net_profit"),
    (("资产总计", "总资产", "total assets"), "total_assets"),
    (("负债合计", "总负债", "total liabilities"), "total_liabilities"),
    (("所有者权益", "股东权益", "shareholders equity"), "shareholders_equity"),
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("−", "-")
    if not text or text in {"-", "—", "--", "不适用", "未返回"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _period(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    match = DATE_HEADER_RE.search(text)
    if match:
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    year = YEAR_RE.search(text)
    return year.group(1) if year else fallback


def _metric_for_label(label: Any) -> str:
    text = re.sub(r"\s+", "", str(label or "")).lower()
    for aliases, metric in METRIC_ALIASES:
        if any(re.sub(r"\s+", "", alias.lower()) in text for alias in aliases):
            return metric
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"financial_metric_{digest}"


def _stable_id(*parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return "trusted:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _identity(expected_identity: Mapping[str, Any] | None) -> dict[str, str]:
    values = {field: str((expected_identity or {}).get(field) or "").strip() for field in IDENTITY_FIELDS}
    return values if all(values.values()) else {}


def _result_matches_identity(
    result: Mapping[str, Any] | None,
    identity: Mapping[str, str],
) -> bool:
    """Reject retrieval output that cannot be tied to the resolved research identity."""

    if not isinstance(result, Mapping):
        return False
    company_id = str(result.get("company_id") or "").strip()
    report_id = str(result.get("report_id") or "").strip()
    task_id = str(result.get("parse_run_id") or result.get("task_id") or "").strip()
    filing_id = str(result.get("filing_id") or "").strip()
    if not company_id or company_id != identity.get("company_id"):
        return False
    if not task_id or task_id != identity.get("parse_run_id"):
        return False
    if filing_id:
        return filing_id == identity.get("filing_id")
    if not report_id:
        return False
    expected_filing_id = str(identity.get("filing_id") or "")
    return expected_filing_id == report_id or expected_filing_id.endswith(f":{report_id}")


def _table_matches_result(table: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    """Fail closed when a resolver accidentally returns a mixed-company table list."""

    for field in ("company_id", "report_id"):
        table_value = str(table.get(field) or "").strip()
        result_value = str(result.get(field) or "").strip()
        if table_value and result_value and table_value != result_value:
            return False
    table_task_id = str(table.get("parse_run_id") or table.get("task_id") or "").strip()
    result_task_id = str(result.get("parse_run_id") or result.get("task_id") or "").strip()
    return not (table_task_id and result_task_id and table_task_id != result_task_id)


def _record(
    *,
    metric: str,
    metric_name: str,
    aliases: Sequence[str],
    period: str,
    value: Decimal,
    unit: str,
    identity: Mapping[str, str],
    report_id: Any,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
    row_index: Any,
    quote: str,
    derived_from: Sequence[str] = (),
) -> dict[str, Any]:
    evidence_id = _stable_id(
        identity.get("company_id"),
        report_id,
        task_id,
        table_index,
        row_index,
        metric,
        period,
        value,
        unit,
        *derived_from,
    )
    payload: dict[str, Any] = {
        "source_type": "trusted_wiki_table_cell",
        "metric": metric,
        "canonical_name": metric,
        "metric_name": metric_name,
        "aliases": tuple(dict.fromkeys(alias for alias in aliases if alias)),
        "period": period,
        "period_key": period,
        "value": str(value),
        "raw_value": str(value),
        "unit": unit,
        "evidence_id": evidence_id,
        "report_id": str(report_id or ""),
        "task_id": str(task_id or ""),
        "pdf_page": pdf_page,
        "table_index": table_index,
        "md_line": md_line,
        "quote": quote,
        **identity,
    }
    if derived_from:
        payload["derived_from_evidence_ids"] = tuple(derived_from)
        payload["source_type"] = "trusted_backend_derived_fact"
    return payload


def _statement_evidence(
    result: Mapping[str, Any] | None,
    identity: Mapping[str, str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if not isinstance(result, Mapping):
        return evidence
    report_id = result.get("report_id")
    for table in result.get("tables") or ():
        if not isinstance(table, Mapping) or not _table_matches_result(table, result):
            continue
        headers = [str(header or "") for header in table.get("headers") or ()]
        records = [record for record in table.get("records") or () if isinstance(record, Mapping)]
        if not headers or not records:
            continue
        label_key = headers[0]
        unit = str(table.get("unit") or "").strip()
        if not unit:
            continue
        for row_index, row in enumerate(records):
            label = str(row.get(label_key) or "").strip()
            if not label:
                continue
            metric = _metric_for_label(label)
            if metric == "goodwill_net":
                metric_name = "商誉账面净值"
                aliases = (label, "商誉净值", "账面净值", "商誉净额")
            else:
                metric_name = label
                aliases = (label,)
            for header in headers[1:]:
                value = _decimal(row.get(header))
                period = _period(header, str(report_id or ""))
                if value is None or not period:
                    continue
                evidence.append(
                    _record(
                        metric=metric,
                        metric_name=metric_name,
                        aliases=aliases,
                        period=period,
                        value=value,
                        unit=unit,
                        identity=identity,
                        report_id=table.get("report_id") or report_id,
                        task_id=table.get("task_id") or result.get("task_id"),
                        pdf_page=table.get("pdf_page"),
                        table_index=table.get("table_index"),
                        md_line=table.get("md_line"),
                        row_index=f"statement:{row_index}:{header}",
                        quote=f"{label} {header} {row.get(header)}",
                    )
                )
    return evidence


def _goodwill_note_evidence(
    result: Mapping[str, Any] | None,
    identity: Mapping[str, str],
    *,
    fallback_unit: str,
    statement_net_periods: set[str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if not isinstance(result, Mapping):
        return evidence
    report_id = result.get("report_id")
    for table in result.get("tables") or ():
        if (
            not isinstance(table, Mapping)
            or not _table_matches_result(table, result)
            or "商誉" not in str(table.get("metric") or "")
        ):
            continue
        headers = [str(header or "") for header in table.get("headers") or ()]
        rows = [record for record in table.get("records") or () if isinstance(record, Mapping)]
        if not headers or not rows:
            continue
        label_key = headers[0]
        unit = str(table.get("unit") or fallback_unit or "").strip()
        if not unit:
            continue
        impairment_index = next(
            (index for index, row in enumerate(rows) if "减值准备" in str(row.get(label_key) or "")),
            None,
        )
        if impairment_index is None:
            continue
        components: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            label = str(row.get(label_key) or "").strip()
            compact_label = re.sub(r"\s+", "", label)
            if row_index == impairment_index:
                metric = "goodwill_impairment_allowance"
                metric_name = "商誉减值准备"
                aliases = (label, "减值准备", "商誉减值准备")
            elif any(term in compact_label for term in ("账面价值", "账面净额", "账面净值")):
                metric = "goodwill_net_note"
                metric_name = "商誉账面净值"
                aliases = (label, "商誉净值", "账面净值", "商誉净额")
            elif compact_label in {"小计", "合计", "账面原值", "原值小计"}:
                metric = "goodwill_gross"
                metric_name = "商誉账面原值"
                aliases = (label, "商誉原值", "账面原值", "原值小计")
            elif row_index == impairment_index - 1 and not label:
                metric = "goodwill_gross"
                metric_name = "商誉账面原值"
                aliases = ("商誉原值", "账面原值", "原值小计")
            elif row_index == impairment_index + 1 and not label:
                metric = "goodwill_net_note"
                metric_name = "商誉账面净值"
                aliases = ("商誉净值", "账面净值", "商誉净额")
            elif label:
                metric = f"goodwill_component_{hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]}"
                metric_name = label
                short_label = re.sub(r"集团$", "", label).strip()
                aliases = (label, short_label, f"{label}商誉", f"{short_label}商誉")
            else:
                continue
            for header in headers[1:]:
                value = _decimal(row.get(header))
                period = _period(header, str(report_id or ""))
                if value is None or not period:
                    continue
                if metric == "goodwill_impairment_allowance":
                    value = abs(value)
                if metric == "goodwill_net_note" and period in statement_net_periods:
                    continue
                item = _record(
                    metric=metric,
                    metric_name=metric_name,
                    aliases=aliases,
                    period=period,
                    value=value,
                    unit=unit,
                    identity=identity,
                    report_id=table.get("report_id") or report_id,
                    task_id=table.get("task_id") or result.get("task_id"),
                    pdf_page=table.get("pdf_page"),
                    table_index=table.get("table_index"),
                    md_line=table.get("md_line"),
                    row_index=f"note:{row_index}:{header}",
                    quote=f"{label or metric_name} {header} {row.get(header)}",
                )
                evidence.append(item)
                if metric.startswith("goodwill_component_"):
                    components.append(item)

        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in components:
            grouped.setdefault(
                (str(item.get("period") or ""), str(item.get("task_id") or ""), str(item.get("table_index") or "")),
                [],
            ).append(item)
        for period_components in grouped.values():
            for left, right in combinations(period_components, 2):
                left_value = _decimal(left.get("value"))
                right_value = _decimal(right.get("value"))
                if left_value is None or right_value is None:
                    continue
                names = sorted((str(left.get("metric_name") or ""), str(right.get("metric_name") or "")))
                short_names = [re.sub(r"集团$", "", name).strip() for name in names]
                derived_ids = sorted((str(left.get("evidence_id") or ""), str(right.get("evidence_id") or "")))
                evidence.append(
                    _record(
                        metric="goodwill_component_sum_" + hashlib.sha256("|".join(names).encode("utf-8")).hexdigest()[:12],
                        metric_name=" + ".join(names),
                        aliases=(" + ".join(names), " + ".join(short_names), "合计"),
                        period=str(left.get("period") or ""),
                        value=left_value + right_value,
                        unit=str(left.get("unit") or ""),
                        identity=identity,
                        report_id=left.get("report_id"),
                        task_id=left.get("task_id"),
                        pdf_page=left.get("pdf_page"),
                        table_index=left.get("table_index"),
                        md_line=left.get("md_line"),
                        row_index="derived-sum:" + ":".join(derived_ids),
                        quote=f"后端确定性求和：{names[0]} + {names[1]}",
                        derived_from=derived_ids,
                    )
                )
    return evidence


def _change_evidence(items: Sequence[Mapping[str, Any]], identity: Mapping[str, str]) -> list[dict[str, Any]]:
    """Materialize adjacent-period absolute changes from trusted source cells."""

    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for item in items:
        if str(item.get("source_type") or "") == "trusted_backend_derived_fact":
            continue
        key = (
            str(item.get("metric") or ""),
            str(item.get("unit") or ""),
            str(item.get("task_id") or ""),
        )
        if all(key):
            grouped.setdefault(key, []).append(item)

    output: list[dict[str, Any]] = []
    for metric_items in grouped.values():
        ordered = sorted(metric_items, key=lambda item: (_period(item.get("period")), str(item.get("evidence_id") or "")))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            previous_period = str(previous.get("period") or "")
            current_period = str(current.get("period") or "")
            previous_year = YEAR_RE.search(previous_period)
            current_year = YEAR_RE.search(current_period)
            if previous_year and current_year and int(current_year.group(1)) - int(previous_year.group(1)) != 1:
                continue
            previous_value = _decimal(previous.get("value"))
            current_value = _decimal(current.get("value"))
            if previous_value is None or current_value is None:
                continue
            metric = str(current.get("metric") or "")
            metric_name = str(current.get("metric_name") or metric)
            direction_alias = "本期减少" if current_value < previous_value else "本期净增"
            source_aliases = tuple(str(alias or "") for alias in (current.get("aliases") or ()) if alias)
            change_aliases = tuple(f"{alias}变动" for alias in source_aliases)
            derived_ids = (
                str(previous.get("evidence_id") or ""),
                str(current.get("evidence_id") or ""),
            )
            output.append(
                _record(
                    metric=f"{metric}_absolute_change",
                    metric_name=f"{metric_name}变动额",
                    aliases=(
                        f"{metric_name}变动",
                        f"{metric_name}同比变动",
                        f"{metric_name}绝对变动",
                        *change_aliases,
                        direction_alias,
                        "绝对变动",
                    ),
                    period=current_period,
                    value=abs(current_value - previous_value),
                    unit=str(current.get("unit") or ""),
                    identity=identity,
                    report_id=current.get("report_id"),
                    task_id=current.get("task_id"),
                    pdf_page=current.get("pdf_page"),
                    table_index=current.get("table_index"),
                    md_line=current.get("md_line"),
                    row_index="derived-change:" + ":".join(derived_ids),
                    quote=f"后端确定性变动重算：{previous_value} -> {current_value}",
                    derived_from=derived_ids,
                )
            )
    return output


def build_trusted_calculation_evidence(
    *,
    statement_result: Mapping[str, Any] | None,
    note_result: Mapping[str, Any] | None,
    expected_identity: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    """Return source-cell facts that the model cannot author or mutate."""

    identity = _identity(expected_identity)
    if not identity:
        return ()
    statement = _statement_evidence(statement_result, identity) if _result_matches_identity(statement_result, identity) else []
    statement_net_periods = {
        str(item.get("period") or "")
        for item in statement
        if str(item.get("metric") or "") == "goodwill_net"
    }
    fallback_unit = next(
        (str(item.get("unit") or "") for item in statement if str(item.get("unit") or "")),
        "",
    )
    note = (
        _goodwill_note_evidence(
            note_result,
            identity,
            fallback_unit=fallback_unit,
            statement_net_periods=statement_net_periods,
        )
        if _result_matches_identity(note_result, identity)
        else []
    )
    seen: set[str] = set()
    output: list[Mapping[str, Any]] = []
    changes = _change_evidence((*statement, *note), identity)
    for item in (*statement, *note, *changes):
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        output.append(item)
    return tuple(output)


__all__ = ["build_trusted_calculation_evidence"]
