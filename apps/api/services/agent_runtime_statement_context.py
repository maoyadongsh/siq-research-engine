import re
from collections.abc import Callable, Mapping
from typing import Any


NormalizeFinancialText = Callable[[Any], str]


def iter_metric_records(obj: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(key in obj for key in ("metric_name", "metric_key", "canonical_name", "item_name", "statement_type", "source")):
            records.append(obj)
        for value in obj.values():
            records.extend(iter_metric_records(value))
    elif isinstance(obj, list):
        for item in obj:
            records.extend(iter_metric_records(item))
    return records


def period_sort_key(value: Any) -> tuple[int, str]:
    text = str(value or "")
    years = re.findall(r"20\d{2}", text)
    year = int(years[0]) if years else 0
    return (year, text)


def record_source(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("source")
    return source if isinstance(source, dict) else {}


def record_source_value(record: dict[str, Any], key: str) -> Any:
    source = record_source(record)
    return record.get(key) if record.get(key) not in (None, "") else source.get(key)


def statement_record_rank(
    record: dict[str, Any],
    statement_type: str,
    *,
    core_keys: Mapping[str, tuple[str, ...]],
    core_name_terms: Mapping[str, tuple[str, ...]],
    normalize_financial_text: NormalizeFinancialText,
) -> tuple[int, int, str]:
    metric_key = str(record.get("metric_key") or record.get("canonical_name") or "")
    name = str(record.get("metric_name") or record.get("name") or record.get("item_name") or "")
    key_order = core_keys.get(statement_type, ())
    if metric_key in key_order:
        return (0, key_order.index(metric_key), name)
    if metric_key:
        return (9, 999, name)
    normalized_name = normalize_financial_text(name)
    for index, term in enumerate(core_name_terms.get(statement_type, ())):
        if normalize_financial_text(term) in normalized_name:
            return (1, index, name)
    return (9, 999, name)


def is_core_statement_record(
    record: dict[str, Any],
    statement_type: str,
    *,
    statement_record_rank_fn: Callable[[dict[str, Any], str], tuple[int, int, str]],
) -> bool:
    return statement_record_rank_fn(record, statement_type)[0] < 9


def latest_records_by_statement(
    records: list[dict[str, Any]],
    *,
    is_core_statement_record_fn: Callable[[dict[str, Any], str], bool],
    statement_record_rank_fn: Callable[[dict[str, Any], str], tuple[int, int, str]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for statement_type in ("income_statement", "cash_flow_statement", "balance_sheet"):
        statement_records = [
            record
            for record in records
            if record.get("statement_type") == statement_type and is_core_statement_record_fn(record, statement_type)
        ]
        if not statement_records:
            continue
        latest_period = max(
            (str(record.get("period") or record_source(record).get("period") or "") for record in statement_records),
            key=period_sort_key,
        )
        latest_records = [
            record
            for record in statement_records
            if str(record.get("period") or record_source(record).get("period") or "") == latest_period
        ]
        latest_records.sort(key=lambda record: statement_record_rank_fn(record, statement_type))
        output.extend(latest_records)
    return output
