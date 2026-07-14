"""Build server-trusted numeric evidence for deterministic answer calculations."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Mapping, Sequence

from services.agent_runtime_financial_claim_verifier import normalize_financial_minus_signs

IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
DATE_HEADER_RE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日")
YEAR_RE = re.compile(r"\b(20\d{2})\b")

GOODWILL_TOTAL_LABELS = frozenset({"小计", "合计", "账面原值", "原值小计"})
GOODWILL_GROSS_ALIASES = ("商誉原值", "账面原值", "原值小计", "商誉原值合计", "商誉总额")
GOODWILL_OPENING_HEADER_TERMS = ("期初", "年初")
GOODWILL_CLOSING_HEADER_TERMS = ("期末", "年末")
GOODWILL_TRAILING_FOOTNOTE_RE = re.compile(
    r"\s*[（(]\s*[ivxlcdm]{1,8}\s*[)）]\s*$",
    re.IGNORECASE,
)
GOODWILL_STATED_SHORT_NAME_RE = re.compile(
    r"\s*[（(]\s*(?:以下)?简称\s*[“”\"'‘’](?P<name>[^“”\"'‘’]+)[“”\"'‘’]\s*[)）]\s*$",
)
GOODWILL_LEGAL_ENTITY_SUFFIX_RE = re.compile(r"(?:有限责任公司|股份有限公司|有限公司|集团)$")
CONSOLIDATED_SCOPE_ALIASES = frozenset({"consolidated", "group", "合并", "集团", "合并报表"})
PARENT_SCOPE_ALIASES = frozenset(
    {"parent", "parent_company", "parent company", "company", "standalone", "separate", "母公司", "公司", "单体"}
)

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
    text = normalize_financial_minus_signs(value).strip().replace(",", "")
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


def _goodwill_balance_period(header: Any, report_id: Any) -> str:
    """Map annual-note opening/closing balances to statement-compatible dates."""

    explicit_period = _period(header)
    if explicit_period:
        return explicit_period

    compact_header = re.sub(r"\s+", "", str(header or ""))
    compact_report_id = str(report_id or "").strip().lower()
    report_year = YEAR_RE.search(compact_report_id)
    if report_year is None or ("annual" not in compact_report_id and "年报" not in compact_report_id):
        return ""
    year = int(report_year.group(1))
    if any(term in compact_header for term in GOODWILL_CLOSING_HEADER_TERMS):
        return f"{year:04d}-12-31"
    if any(term in compact_header for term in GOODWILL_OPENING_HEADER_TERMS):
        return f"{year - 1:04d}-12-31"
    return ""


def _goodwill_table_kind(
    table: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    label_key: str,
) -> str:
    """Classify goodwill tables before interpreting ambiguous rows such as 合计."""

    metric = re.sub(r"\s+", "", str(table.get("metric") or "")).lower()
    if "减值准备" in metric or "impairmentallowance" in metric:
        return "allowance"
    if any(term in metric for term in ("账面原值", "商誉原值", "goodwillgross")):
        return "gross"
    if any("减值准备" in str(row.get(label_key) or "") for row in rows):
        return "combined"
    return ""


def _goodwill_component_aliases(label: str) -> tuple[str, ...]:
    """Keep the source label while adding only explicitly supported short names."""

    source_label = str(label or "").strip()
    cleaned_label = GOODWILL_TRAILING_FOOTNOTE_RE.sub("", source_label).strip()
    stated_short_name_match = GOODWILL_STATED_SHORT_NAME_RE.search(cleaned_label)
    legal_name = GOODWILL_STATED_SHORT_NAME_RE.sub("", cleaned_label).strip()
    stated_short_name = (
        str(stated_short_name_match.group("name") or "").strip()
        if stated_short_name_match is not None
        else ""
    )
    base_aliases: list[str] = []
    for candidate in (source_label, cleaned_label, legal_name, stated_short_name):
        if candidate and candidate not in base_aliases:
            base_aliases.append(candidate)
        short_candidate = GOODWILL_LEGAL_ENTITY_SUFFIX_RE.sub("", candidate).strip()
        if short_candidate and short_candidate not in base_aliases:
            base_aliases.append(short_candidate)
    return tuple((*base_aliases, *(f"{alias}商誉" for alias in base_aliases)))


def _goodwill_component_sum_aliases(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> tuple[str, ...]:
    """Combine source-backed component names without inventing new abbreviations."""

    def base_aliases(item: Mapping[str, Any]) -> tuple[str, ...]:
        candidates = (str(item.get("metric_name") or ""), *(str(alias or "") for alias in item.get("aliases") or ()))
        return tuple(dict.fromkeys(candidate for candidate in candidates if candidate and not candidate.endswith("商誉")))

    combined: list[str] = []
    for first, second in ((base_aliases(left), base_aliases(right)), (base_aliases(right), base_aliases(left))):
        for first_name in first:
            for second_name in second:
                alias = f"{first_name} + {second_name}"
                if alias not in combined:
                    combined.append(alias)
    return tuple((*combined, "合计"))


def _stable_id(*parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return "trusted:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _normalized_financial_scope(value: Any) -> str:
    text = re.sub(r"[\s_\-]+", "", str(value or "")).lower()
    if text in {re.sub(r"[\s_\-]+", "", item).lower() for item in CONSOLIDATED_SCOPE_ALIASES}:
        return "consolidated"
    if text in {re.sub(r"[\s_\-]+", "", item).lower() for item in PARENT_SCOPE_ALIASES}:
        return "parent"
    return text


def _financial_scope(
    table: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    metric: str,
    header: str = "",
) -> str:
    column_scopes = table.get("column_scopes")
    if isinstance(column_scopes, Mapping) and header:
        if scope := _normalized_financial_scope(column_scopes.get(header)):
            return scope
    for source in (table, result):
        for field in ("financial_scope", "statement_scope", "scope"):
            if scope := _normalized_financial_scope(source.get(field)):
                return scope
    return ""


def _source_lineage(table: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    task_id = table.get("parse_run_id") or table.get("task_id") or result.get("parse_run_id") or result.get("task_id")
    report_id = table.get("report_id") or result.get("report_id")
    table_index = table.get("table_index")
    md_line = table.get("md_line")
    if not task_id or (table_index in (None, "") and md_line in (None, "")):
        return ""
    return _stable_id("source-lineage", task_id, report_id, table_index, md_line)


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
    financial_scope: str,
    source_lineage: str,
    derived_from: Sequence[str] = (),
    change_direction: str = "",
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
        "financial_scope": financial_scope,
        "source_lineage": source_lineage,
        **identity,
    }
    if change_direction:
        payload["change_direction"] = change_direction
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
        period_counts: dict[str, int] = {}
        for header in headers[1:]:
            if period := _period(header):
                period_counts[period] = period_counts.get(period, 0) + 1
        repeated_periods = {period for period, count in period_counts.items() if count > 1}
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
            values_by_period: dict[str, list[tuple[str, Decimal]]] = {}
            for header in headers[1:]:
                value = _decimal(row.get(header))
                # Statement tables commonly contain a numeric note-reference
                # column (for example ``附注七 = 19``).  Only dated headers are
                # balance facts; falling back to the report id would turn the
                # note number into a financial amount.
                period = _period(header)
                if value is None or not period:
                    continue
                values_by_period.setdefault(period, []).append((header, value))
            for period, period_values in values_by_period.items():
                scoped_values = [
                    (
                        header,
                        value,
                        _financial_scope(table, result, metric=metric, header=header),
                    )
                    for header, value in period_values
                ]
                if period in repeated_periods and any(not scope for _header, _value, scope in scoped_values):
                    continue
                if len(scoped_values) > 1:
                    scopes = [scope for _header, _value, scope in scoped_values]
                    if not all(scopes) or len(set(scopes)) != len(scopes):
                        # Multiple values for one period are ambiguous unless
                        # every source column has a distinct explicit scope.
                        continue
                for header, value, financial_scope in scoped_values:
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
                            financial_scope=financial_scope,
                            source_lineage=_source_lineage(table, result),
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
        table_kind = _goodwill_table_kind(table, rows, label_key)
        if not table_kind:
            continue
        impairment_index = next(
            (index for index, row in enumerate(rows) if "减值准备" in str(row.get(label_key) or "")),
            None,
        )
        total_index = next(
            (
                index
                for index, row in enumerate(rows)
                if re.sub(r"\s+", "", str(row.get(label_key) or "")) in GOODWILL_TOTAL_LABELS
            ),
            None,
        )
        allowance_balance_index = total_index if total_index is not None else impairment_index
        components: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            label = str(row.get(label_key) or "").strip()
            compact_label = re.sub(r"\s+", "", label)
            if table_kind == "allowance":
                if row_index != allowance_balance_index:
                    continue
                metric = "goodwill_impairment_allowance"
                metric_name = "商誉减值准备"
                aliases = (label, "减值准备", "商誉减值准备")
            elif table_kind == "gross":
                if compact_label in GOODWILL_TOTAL_LABELS:
                    metric = "goodwill_gross"
                    metric_name = "商誉账面原值"
                    aliases = (label, *GOODWILL_GROSS_ALIASES)
                elif label:
                    metric = f"goodwill_component_{hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]}"
                    metric_name = label
                    aliases = _goodwill_component_aliases(label)
                else:
                    continue
            elif row_index == impairment_index:
                metric = "goodwill_impairment_allowance"
                metric_name = "商誉减值准备"
                aliases = (label, "减值准备", "商誉减值准备")
            elif any(term in compact_label for term in ("账面价值", "账面净额", "账面净值")):
                metric = "goodwill_net_note"
                metric_name = "商誉账面净值"
                aliases = (label, "商誉净值", "账面净值", "商誉净额")
            elif compact_label in GOODWILL_TOTAL_LABELS:
                metric = "goodwill_gross"
                metric_name = "商誉账面原值"
                aliases = (label, *GOODWILL_GROSS_ALIASES)
            elif impairment_index is not None and row_index == impairment_index - 1 and not label:
                metric = "goodwill_gross"
                metric_name = "商誉账面原值"
                aliases = GOODWILL_GROSS_ALIASES
            elif impairment_index is not None and row_index == impairment_index + 1 and not label:
                metric = "goodwill_net_note"
                metric_name = "商誉账面净值"
                aliases = ("商誉净值", "账面净值", "商誉净额")
            elif label:
                metric = f"goodwill_component_{hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]}"
                metric_name = label
                aliases = _goodwill_component_aliases(label)
            else:
                continue
            for header in headers[1:]:
                value = _decimal(row.get(header))
                period = _goodwill_balance_period(header, table.get("report_id") or report_id)
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
                    financial_scope=_financial_scope(table, result, metric=metric, header=header),
                    source_lineage=_source_lineage(table, result),
                )
                evidence.append(item)
                if metric.startswith("goodwill_component_"):
                    components.append(item)

        grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        for item in components:
            financial_scope = str(item.get("financial_scope") or "")
            source_lineage = str(item.get("source_lineage") or "")
            if not financial_scope or not source_lineage:
                continue
            grouped.setdefault(
                (
                    str(item.get("period") or ""),
                    str(item.get("task_id") or ""),
                    str(item.get("table_index") or ""),
                    financial_scope,
                    source_lineage,
                ),
                [],
            ).append(item)
        for period_components in grouped.values():
            for left, right in combinations(period_components, 2):
                left_value = _decimal(left.get("value"))
                right_value = _decimal(right.get("value"))
                if left_value is None or right_value is None:
                    continue
                names = sorted((str(left.get("metric_name") or ""), str(right.get("metric_name") or "")))
                derived_ids = sorted((str(left.get("evidence_id") or ""), str(right.get("evidence_id") or "")))
                evidence.append(
                    _record(
                        metric="goodwill_component_sum_" + hashlib.sha256("|".join(names).encode("utf-8")).hexdigest()[:12],
                        metric_name=" + ".join(names),
                        aliases=_goodwill_component_sum_aliases(left, right),
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
                        financial_scope=str(left.get("financial_scope") or ""),
                        source_lineage=str(left.get("source_lineage") or ""),
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
            str(item.get("financial_scope") or ""),
            str(item.get("source_lineage") or ""),
        )
        if all(key[:3]) and key[-1]:
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
            change_value = current_value - previous_value
            if change_value < 0:
                change_direction, direction_alias = "decrease", "本期减少"
            elif change_value > 0:
                change_direction, direction_alias = "increase", "本期净增"
            else:
                change_direction, direction_alias = "unchanged", "本期持平"
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
                    value=abs(change_value),
                    unit=str(current.get("unit") or ""),
                    identity=identity,
                    report_id=current.get("report_id"),
                    task_id=current.get("task_id"),
                    pdf_page=current.get("pdf_page"),
                    table_index=current.get("table_index"),
                    md_line=current.get("md_line"),
                    row_index="derived-change:" + ":".join(derived_ids),
                    quote=f"后端确定性变动重算：{previous_value} -> {current_value}",
                    financial_scope=str(current.get("financial_scope") or ""),
                    source_lineage=str(current.get("source_lineage") or ""),
                    derived_from=derived_ids,
                    change_direction=change_direction,
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
