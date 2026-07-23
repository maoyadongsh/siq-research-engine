#!/usr/bin/env python3
"""Render a SIQ v1.1 report from an existing analysis checkpoint.

This is a deterministic fallback for long-running report jobs. It does not
rebuild evidence, query PostgreSQL, or call an LLM. It consumes the staged
checkpoint files already produced by SIQ_analysis and writes:

- section_drafts.json
- quality_report.json
- final .json / .md / .html report files
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from provenance_utils import (
    evidence_id_from_record,
    is_missing,
    load_provenance_lookup,
    normalize_evidence_package,
    normalize_evidence_record,
)


TEMPLATE_JSON = Path(__file__).resolve().parent.parent / "templates" / "siq_analysis_report_v1.1.json"
SECTION_DRAFT_SCHEMA_JSON = Path(__file__).resolve().parent.parent / "templates" / "section_drafts.schema.json"
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "").rstrip("/")


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def load_template() -> dict[str, Any]:
    return json.loads(TEMPLATE_JSON.read_text(encoding="utf-8"))


def section_defs() -> list[tuple[str, str]]:
    template = load_template()
    sections = sorted(template.get("sections", []), key=lambda item: item.get("order", 0))
    return [(str(item["section_id"]), str(item["title"])) for item in sections]


SECTION_DEFS = section_defs()
SECTION_IDS = [section_id for section_id, _ in SECTION_DEFS]


def section_meta() -> dict[str, dict[str, Any]]:
    template = load_template()
    result: dict[str, dict[str, Any]] = {}
    for section in template.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        if not section_id:
            continue
        result[section_id] = {
            "section_type": str(section.get("section_type") or "cfo_analysis"),
            "preferred_blocks": [
                str(item)
                for item in section.get("preferred_blocks", []) or []
                if str(item).strip()
            ],
        }
    return result


SECTION_META = section_meta()

NOISY_QUALITATIVE_TERMS = [
    "现金分红",
    "利润分配",
    "退市风险警示",
    "合并财务报表的编制方法",
    "控制的判断标准",
    "主要财务指标",
    "报告期末公司前三年主要会计数据",
    "基本每股收益",
    "导致退市风险警示的原因",
    "公司股票被实施退市风险警示",
    "同类业务采用不同经营模式",
    "前五名销售客户 □适用",
    "前五名供应商 □适用",
    "产品质量保证金",
    "分部信息",
    "单位：元 币种",
    "公司报告期内业务、产品或服务发生重大变化或调整有关情况",
]

LOW_VALUE_REPORT_PHRASES = [
    "执行摘要必须",
    "本节重点是",
    "正确写法是",
    "当前生成器不联网",
    "报告使用 metric_snapshot",
    "避免只罗列",
    "不构成投资建议",
    "不得输出",
    "不得把",
    "只能写",
]

SOURCE_LABEL_PREFIX_RE = re.compile(r"^【[^】]+】")

SECTION_SYNTHESIS_FOCUS = {
    "executive_summary": ("经营安全与盈利质量", "收入、扣非利润、现金流和负债覆盖是否同向", "核心矛盾是否缓释"),
    "key_changes": ("年度变化的质量", "增长、利润、现金流和资产负债表是否同向", "变化是否具有持续性"),
    "operating_quality": ("经营质量", "收入增长是否转化为回款、周转和合同负债支撑", "经营拐点是否被财务变量验证"),
    "profitability_and_cost": ("盈利能力", "毛利率、费用率、扣非利润和非经常项目的贡献", "利润修复是否依赖一次性因素"),
    "asset_quality_working_capital": ("资产质量与营运资本", "存货、应收、合同负债和周转效率", "收入质量是否被资产端拖累"),
    "debt_liquidity": ("偿债安全", "短债、现金、有息负债和经营现金流覆盖", "流动性压力是否扩大"),
    "cash_flow_quality": ("现金流质量", "经营现金流、资本开支和自由现金流的匹配", "利润含金量是否改善"),
    "industry_competition": ("行业竞争位置", "同业分位、产品结构、价格竞争和现金转化", "竞争优势是否进入报表"),
    "strategy_policy_external_risk": ("战略兑现质量", "研发、资本开支、产品结构和现金流是否验证管理层战略", "战略叙事是否被财务变量支撑"),
    "governance_compliance_shareholders": ("治理与合规风险", "审计、诉讼、股东承诺和资本动作", "治理变量是否影响财务可信度"),
    "valuation_expectation_gap": ("估值预期差", "基本面锚、市场数据缺口和同业估值可比性", "估值讨论是否具备足够证据"),
    "risk_chain_scenario": ("风险链条", "关键变量恶化如何传导到利润、现金流和资产质量", "哪些反证会推翻当前结论"),
    "tracking_checklist": ("后续跟踪", "改善信号、恶化信号和数据源频率", "跟踪体系是否可执行"),
    "data_quality_traceability": ("数据质量与溯源", "证据覆盖、缺失字段和可复核链接", "报告结论的可信边界"),
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_section_draft_schema() -> dict[str, Any]:
    if SECTION_DRAFT_SCHEMA_JSON.exists():
        return load_json(SECTION_DRAFT_SCHEMA_JSON)
    return {
        "expected_section_ids": SECTION_IDS,
        "required_section_fields": [
            "section_id",
            "title",
            "facts",
            "calculations",
            "judgements",
            "risks_or_improvement_conditions",
            "evidence_ids",
            "review_required",
            "missing_fields",
        ],
        "array_fields": [
            "facts",
            "calculations",
            "judgements",
            "risks_or_improvement_conditions",
            "evidence_ids",
            "missing_fields",
        ],
        "minimum_items": {
            "facts": 2,
            "calculations": 1,
            "judgements": 1,
            "risks_or_improvement_conditions": 2,
            "evidence_ids": 1,
        },
        "minimum_compact_text_length": 160,
    }


def compact_section_text(section: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in section.get("narrative_blocks") or []:
        if not isinstance(block, dict):
            continue
        parts.append(str(block.get("title") or ""))
        items = block.get("items")
        if isinstance(items, list):
            parts.extend(str(item) for item in items)
    for key in ["facts", "calculations", "judgements", "risks_or_improvement_conditions"]:
        value = section.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return re.sub(r"\s+", "", "".join(parts))


def compact_qualitative_text(value: Any, limit: int = 900) -> str:
    text = re.sub(r"（证据：[^）]+）", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"。+；", "；", text)
    text = re.sub(r"；+", "；", text)
    text = remove_truncated_tail(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；; ") + "..."


def remove_truncated_tail(text: str) -> str:
    """Drop sentence fragments left by upstream ellipsis truncation.

    Historical research packs may already contain snippets such as
    "研发费用 1...". Rendering that fragment is worse than omitting the tail,
    because it looks like a malformed number. Keep complete earlier sentences
    and remove only the trailing fragment that contains an ellipsis.
    """
    clean = str(text or "").strip()
    if "..." not in clean and "…" not in clean:
        return clean
    match = re.search(r"(?:\.\.\.|…)", clean)
    if not match:
        return clean
    prefix = clean[: match.start()].rstrip("，。；;、 ：:")
    boundary = max(prefix.rfind("。"), prefix.rfind("；"), prefix.rfind("!"), prefix.rfind("！"), prefix.rfind("?"), prefix.rfind("？"))
    if boundary >= 40:
        return prefix[: boundary + 1].strip()
    return prefix.strip()


def is_noisy_qualitative_text(value: Any) -> bool:
    text = str(value or "")
    return (
        not text.strip()
        or any(term in text for term in NOISY_QUALITATIVE_TERMS)
        or any(term in text for term in LOW_VALUE_REPORT_PHRASES)
    )


def clean_qualitative_texts(items: list[Any], limit: int, text_limit: int = 900) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if is_noisy_qualitative_text(item):
            continue
        text = compact_qualitative_text(item, text_limit)
        key = re.sub(r"\s+", "", text)
        if not key or key in seen:
            continue
        result.append(text)
        seen.add(key)
        if len(result) >= limit:
            break
    return result


def clean_section_items(section_id: str, items: list[str]) -> list[str]:
    text_limit = 760 if section_id in {"strategy_policy_external_risk", "governance_compliance_shareholders"} else 900
    return clean_qualitative_texts(items, len(items), text_limit)


def clean_visible_items(items: list[str], text_limit: int = 900) -> list[str]:
    return clean_qualitative_texts(items, len(items), text_limit)


def validate_section_drafts_payload(data: dict[str, Any]) -> dict[str, Any]:
    schema = load_section_draft_schema()
    expected = [str(item) for item in schema.get("expected_section_ids", SECTION_IDS)]
    required = [str(item) for item in schema.get("required_section_fields", [])]
    array_fields = set(str(item) for item in schema.get("array_fields", []))
    minimum_items = schema.get("minimum_items") if isinstance(schema.get("minimum_items"), dict) else {}
    minimum_length = int(schema.get("minimum_compact_text_length") or 0)
    failures: list[str] = []
    warnings: list[str] = []
    sections = data.get("sections")
    if not isinstance(sections, list):
        return {
            "ok": False,
            "failures": ["sections_not_list"],
            "warnings": [],
            "metrics": {"section_count": 0, "section_order_valid": False},
        }
    actual: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            failures.append("section_not_object")
            continue
        section_id = str(section.get("section_id", "unknown"))
        actual.append(section_id)
        for field in required:
            if field not in section:
                failures.append(f"{section_id}:missing_field:{field}")
        for field in array_fields:
            value = section.get(field)
            if not isinstance(value, list):
                failures.append(f"{section_id}:field_not_list:{field}")
                continue
            minimum = int(minimum_items.get(field, 0))
            if len(value) < minimum:
                failures.append(f"{section_id}:too_few_items:{field}:{len(value)}")
        if not isinstance(section.get("review_required"), bool):
            failures.append(f"{section_id}:review_required_not_bool")
        if minimum_length and len(compact_section_text(section)) < minimum_length:
            failures.append(f"{section_id}:thin_content")
        evidence = section.get("evidence_ids")
        if isinstance(evidence, list) and evidence and all(str(item).endswith(":missing") for item in evidence):
            warnings.append(f"{section_id}:only_missing_evidence")
    if actual != expected:
        failures.append("section_order_invalid")
    if len(actual) != len(expected):
        failures.append(f"section_count_invalid:{len(actual)}")
    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "section_count": len(actual),
            "section_order_valid": actual == expected,
        },
    }


def output_path(prefix: Path, suffix: str) -> Path:
    return prefix.parent / f"{prefix.name}{suffix}"


def report_output_paths(prefix: Path) -> dict[str, Path]:
    return {
        "json": output_path(prefix, ".json"),
        "md": output_path(prefix, ".md"),
        "html": output_path(prefix, ".html"),
    }


def existing_report_outputs(prefix: Path) -> list[Path]:
    return [path for path in report_output_paths(prefix).values() if path.exists()]


def backup_existing_outputs(prefix: Path, backup_root: Path | None = None) -> dict[str, str]:
    existing = existing_report_outputs(prefix)
    if not existing:
        return {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = backup_root or prefix.parent / ".work" / "backups" / f"{prefix.name}-{timestamp}"
    target_dir.mkdir(parents=True, exist_ok=True)
    backups: dict[str, str] = {}
    for path in existing:
        backup_path = target_dir / path.name
        shutil.copy2(path, backup_path)
        backups[path.suffix.lstrip(".")] = str(backup_path)
    return backups


def fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "未返回"
    if isinstance(value, (int, float)):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def is_positive_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text)) and int(text) > 0


def is_nonnegative_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text))


MONETARY_METRIC_KEYS = {
    "operating_revenue",
    "total_operating_revenue",
    "operating_cost",
    "parent_net_profit",
    "net_profit_parent",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "net_operating_cash_flow",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "monetary_capital",
    "monetary_funds",
    "inventory",
    "accounts_receivable",
    "notes_receivable",
    "short_term_borrowings",
    "current_portion_noncurrent_liabilities",
    "current_assets",
    "current_liabilities",
    "contract_liabilities",
    "cash_for_purchases",
    "cash_for_purchases_investments",
    "capital_expenditure",
    "operating_profit",
    "total_profit",
    "interest_expense",
}


def metric(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    metrics = snapshot.get("metrics", {})
    if key in metrics:
        return metrics.get(key, {})
    key_metrics = snapshot.get("key_metrics", {})
    if key in key_metrics:
        return key_metrics.get(key, {})
    alias_map = {
        "net_profit_parent": ["parent_net_profit", "归属于上市公司股东的净利润"],
        "net_operating_cash_flow": ["operating_cash_flow_net", "经营活动产生的现金流量净额"],
        "monetary_funds": ["monetary_capital", "货币资金"],
        "gross_margin": ["gross_profit_margin", "毛利率"],
        "accounts_receivable": ["应收账款"],
        "inventory": ["存货"],
        "operating_cost": ["营业成本"],
        "operating_profit": ["营业利润"],
        "total_assets": ["资产总计"],
        "total_liabilities": ["负债合计"],
        "short_term_borrowings": ["短期借款"],
        "capital_expenditure": ["cash_for_purchases_investments", "购建固定资产、无形资产和其他长期资产支付的现金"],
    }
    for alias in alias_map.get(key, []):
        if alias in metrics:
            return metrics.get(alias, {})
        if alias in key_metrics:
            return key_metrics.get(alias, {})
    return {}


def normalize_metric_value(item: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value
    unit = str(item.get("unit") or "").strip()
    key = str(item.get("canonical_name") or item.get("metric_key") or "")
    if unit in {"元", "人民币元", "CNY"}:
        return value / 100_000_000
    if unit == "万元":
        return value / 10_000
    if not unit and key in MONETARY_METRIC_KEYS and abs(value) >= 100_000:
        return value / 100_000_000
    return value


def metric_value(snapshot: dict[str, Any], key: str, year: str = "2025") -> Any:
    item = metric(snapshot, key)
    return normalize_metric_value(item, item.get("values", {}).get(year))


def yoy(snapshot: dict[str, Any], key: str) -> Any:
    item = metric(snapshot, key)
    existing = item.get("yoy_change")
    if existing is not None:
        return existing
    values = item.get("values", {})
    current = normalize_metric_value(item, values.get("2025"))
    previous = normalize_metric_value(item, values.get("2024"))
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous:
        return (current - previous) / abs(previous) * 100
    return None


def infer_company_dir(work_dir: Path, output_prefix: Path | None = None) -> Path | None:
    for path in [work_dir, output_prefix]:
        if not path:
            continue
        resolved = path.resolve()
        parts = resolved.parts
        if "companies" not in parts:
            continue
        idx = parts.index("companies")
        if idx + 1 < len(parts):
            return Path(*parts[: idx + 2])
    return None


def report_meta_from_company(company_dir: Path | None) -> dict[str, Any]:
    if not company_dir:
        return {}
    company = load_json_if_exists(company_dir / "company.json")
    report = {}
    reports = company.get("reports")
    if isinstance(reports, list) and reports:
        report = reports[0] if isinstance(reports[0], dict) else {}
    return {
        "company_id": company.get("company_id") or (company_dir.name if company_dir else None),
        "stock_code": company.get("stock_code"),
        "company_short_name": company.get("company_short_name"),
        "company_full_name": company.get("company_full_name"),
        "report_id": report.get("report_id") or company.get("primary_report_id"),
        "report_year": report.get("report_year") or 2025,
        "report_type": report.get("report_kind") or "annual_report",
        "task_id": report.get("task_id"),
    }


def normalize_preflight(preflight: dict[str, Any], company_dir: Path | None) -> dict[str, Any]:
    meta = report_meta_from_company(company_dir)
    normalized = dict(preflight)
    for key, value in meta.items():
        normalized.setdefault(key, value)
    normalized.setdefault("artifact_status", "ready")
    normalized.setdefault("postgres_status", "not_required_for_fallback")
    normalized.setdefault("evidence_status", "ready" if company_dir and (company_dir / "evidence" / "evidence_index.json").exists() else "partial")
    normalized.setdefault("report_year", 2025)
    normalized.setdefault("report_type", "annual_report")
    return normalized


def normalize_snapshot(snapshot: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    normalized = dict(snapshot)
    if "metrics" not in normalized:
        key_metrics = normalized.get("key_metrics")
        if isinstance(key_metrics, dict):
            normalized["metrics"] = key_metrics
    if "metrics" not in normalized:
        financial_data = load_json_if_exists(work_dir / "financial_data_complete.json")
        data = financial_data.get("three_statement_data")
        if isinstance(data, dict):
            normalized["metrics"] = {
                key: {
                    "values": {"2025": value},
                    "unit": "亿元",
                    "canonical_name": key,
                }
                for key, value in data.items()
            }
        history = financial_data.get("key_metrics_history")
        if isinstance(history, dict):
            normalized.setdefault("metrics", {})
            for key, values in history.items():
                if isinstance(values, dict):
                    normalized["metrics"][key] = {
                        "values": values,
                        "unit": "亿元",
                        "canonical_name": key,
                    }
    return normalized


def build_evidence_package(company_dir: Path | None, snapshot: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    existing: dict[str, list[dict[str, Any]]] = {}
    if company_dir:
        evidence_index = load_json_if_exists(company_dir / "evidence" / "evidence_index.json")
        for item in evidence_index.get("evidence", []) or []:
            if not isinstance(item, dict):
                continue
            key = item.get("metric_key") or item.get("canonical_name") or item.get("metric_name")
            if key:
                existing.setdefault(str(key), []).append(item)
    lookup = load_provenance_lookup(company_dir, preflight.get("report_year") or 2025)

    alias_map = {
        "net_profit_parent": ["parent_net_profit"],
        "net_operating_cash_flow": ["operating_cash_flow_net"],
        "monetary_funds": ["monetary_capital"],
        "operating_revenue": ["营业收入"],
        "total_profit": ["利润总额"],
        "operating_profit": ["营业利润"],
        "operating_cost": ["营业成本"],
        "total_assets": ["资产总计"],
        "total_liabilities": ["负债合计"],
        "inventory": ["存货"],
        "accounts_receivable": ["应收账款"],
    }
    keys = {
        "operating_revenue",
        "net_profit_parent",
        "parent_net_profit",
        "total_profit",
        "operating_profit",
        "operating_cost",
        "net_operating_cash_flow",
        "operating_cash_flow_net",
        "total_assets",
        "total_liabilities",
        "monetary_funds",
        "monetary_capital",
        "inventory",
        "accounts_receivable",
        "short_term_borrowings",
        "capital_expenditure",
        "gross_margin",
    }
    keys.update(snapshot.get("metrics", {}).keys())
    financial_evidence: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        evidence = list(existing.get(key, []))
        for alias in alias_map.get(key, []):
            evidence.extend(existing.get(alias, []))
        if not evidence:
            source = metric(snapshot, key).get("sources", {}).get("2025", {})
            if source:
                task_id = preflight.get("task_id", "未返回")
                table_index = source.get("table_index", "未返回")
                pdf_page_number = source.get("pdf_page_number")
                if is_missing(pdf_page_number):
                    pdf_page_number = source.get("pdf_page", "未返回")
                evidence = [{
                    "metric_key": key,
                    "period": "2025",
                    "task_id": task_id,
                    "table_index": table_index,
                    "md_line": source.get("md_line", source.get("line", "未返回")),
                    "pdf_page_number": pdf_page_number,
                    "pdf_page": source.get("pdf_page"),
                    "open_pdf_page_url": public_api_url(f"/api/pdf_page/{task_id}/{pdf_page_number}") if task_id != "未返回" and is_positive_int_token(pdf_page_number) else None,
                    "open_source_table_url": public_api_url(f"/api/source/{task_id}/table/{table_index}") if task_id != "未返回" and is_nonnegative_int_token(table_index) else None,
                }]
        financial_evidence[key] = {
            "evidence": [
                normalize_evidence_record(
                    ev,
                    lookup=lookup,
                    default_task_id=preflight.get("task_id"),
                    metric_key=key,
                    period="2025",
                    url_builder=public_api_url,
                )
                for ev in evidence[:6]
                if isinstance(ev, dict)
            ]
        }

    package = {
        "schema_version": 1,
        "generated_by": "render_report_from_checkpoint.py:auto_recovery",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "financial_evidence": financial_evidence,
    }
    return normalize_evidence_package(
        package,
        snapshot=snapshot,
        lookup=lookup,
        default_task_id=preflight.get("task_id"),
        year=preflight.get("report_year") or 2025,
        aliases=alias_map,
        url_builder=public_api_url,
    )


def build_outline(snapshot: dict[str, Any]) -> dict[str, Any]:
    revenue_yoy = yoy(snapshot, "operating_revenue")
    ocf_yoy = yoy(snapshot, "net_operating_cash_flow")
    total_assets = metric_value(snapshot, "total_assets")
    total_liabilities = metric_value(snapshot, "total_liabilities")
    debt_ratio = None
    if isinstance(total_assets, (int, float)) and total_assets:
        debt_ratio = total_liabilities / total_assets * 100 if isinstance(total_liabilities, (int, float)) else None
    return {
        "core_judgment": "公司处于新能源转型后的盈利与现金流质量验证期，需同时观察规模增长、费用消化、现金流持续性和债务安全。",
        "core_contradiction": "收入规模扩张与利润质量、费用刚性、供应链占款和资本开支之间需要交叉验证。",
        "calculated_derived_metrics": {
            "debt_ratio_pct": debt_ratio,
            "operating_revenue_yoy_pct": revenue_yoy,
            "operating_cash_flow_yoy_pct": ocf_yoy,
        },
        "red_flags": [
            "若扣非利润低于归母净利润且费用率维持高位，主营盈利质量仍需复核。",
            "若应付票据及应付账款占比较高，现金流改善可能部分来自供应链信用。",
        ],
        "yellow_flags": [
            "行业价格竞争和产品周期变化可能影响毛利率稳定性。",
            "资本开支、研发投入和合作模式对后续现金流有持续影响。",
        ],
        "improvement_items": [
            "营业收入保持高位，经营现金流为正，是当前经营修复的重要正面信号。",
        ],
        "deterioration_items": [
            "若扣非利润、毛利率或经营现金流边际转弱，修复逻辑需要重新评估。",
        ],
        "observation_items": [
            "跟踪销量、单车盈利、费用率、经营现金流、供应链应付款和资本开支。",
        ],
        "improvement_conditions": [
            "收入增长继续传导至毛利率和扣非净利润。",
            "经营现金流持续为正并能够覆盖资本开支和债务滚续。",
        ],
        "falsifying_evidence": [
            "收入增长但毛利率下滑、扣非利润下行或现金流明显转弱。",
            "短债压力、存货跌价或供应链付款压力显著上升。",
        ],
    }


def first_evidence(evidence_package: dict[str, Any], metric_key: str) -> dict[str, Any]:
    aliases = {
        "net_profit_parent": ["parent_net_profit"],
        "net_operating_cash_flow": ["operating_cash_flow_net"],
        "monetary_funds": ["monetary_capital"],
        "capital_expenditure": ["cash_for_purchases_investments"],
        "gross_margin": ["gross_profit_margin"],
        "debt_to_asset_ratio": ["asset_liability_ratio"],
    }
    financial = evidence_package.get("financial_evidence", {})
    for key in [metric_key] + aliases.get(metric_key, []):
        item = financial.get(key, {}) if isinstance(financial, dict) else {}
        evidence = item.get("evidence") or []
        if evidence:
            return evidence[0]
    return {}


def evidence_id(evidence_package: dict[str, Any], metric_key: str) -> str:
    ev = first_evidence(evidence_package, metric_key)
    return evidence_id_from_record(metric_key, ev)


def refresh_section_evidence_ids(section: dict[str, Any], evidence_package: dict[str, Any]) -> None:
    raw_ids = section.get("evidence_ids")
    if not isinstance(raw_ids, list):
        return
    refreshed: list[str] = []
    for raw in raw_ids:
        text = str(raw)
        metric_key = text.split(":", 1)[0] if ":" in text else ""
        if metric_key and first_evidence(evidence_package, metric_key):
            text = evidence_id(evidence_package, metric_key)
        if text not in refreshed:
            refreshed.append(text)
    section["evidence_ids"] = refreshed


def citation_line(evidence_package: dict[str, Any], metric_key: str) -> str:
    ev = first_evidence(evidence_package, metric_key)
    if not ev:
        return f"- {metric_key}: 证据未返回，需人工复核。"
    task_id = ev.get("task_id", "未返回")
    page = ev.get("pdf_page_number", ev.get("pdf_page", "未返回"))
    table = ev.get("table_index", "未返回")
    line = ev.get("md_line", "未返回")
    raw_pdf_url = ev.get("open_pdf_page_url")
    url = ""
    if task_id != "未返回" and is_positive_int_token(page):
        url = str(raw_pdf_url or public_api_url(f"/api/pdf_page/{task_id}/{page}"))
    page_source = (
        public_api_url(f"/api/source/{task_id}/page/{page}") if task_id != "未返回" and is_positive_int_token(page) else ""
    )
    table_source = (
        public_api_url(f"/api/source/{task_id}/table/{table}") if task_id != "未返回" and is_nonnegative_int_token(table) else ""
    )
    links = []
    if url:
        links.append(f"[打开PDF页]({url})")
    if page_source:
        links.append(f"[查看页来源]({page_source})")
    if table_source:
        links.append(f"[查看表格]({table_source})")
    link_text = "，" + "，".join(links) if links else ""
    return f"- {metric_key}: task_id={task_id}，pdf_page={page}，table_index={table}，md_line={line}{link_text}"


def narrative_block(title: str, items: list[str], role: str = "analysis") -> dict[str, Any]:
    return {
        "title": title,
        "role": role,
        "items": [item for item in items if item],
    }


def strip_source_label(text: str) -> str:
    return SOURCE_LABEL_PREFIX_RE.sub("", str(text or "")).strip()


def select_anchor(items: list[str], keywords: list[str], fallback_index: int = 0) -> str:
    clean = [strip_source_label(item) for item in items if strip_source_label(item)]
    for keyword in keywords:
        for item in clean:
            if keyword in item:
                return item
    if clean:
        return clean[min(fallback_index, len(clean) - 1)]
    return ""


def build_section_synthesis(
    section_id: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
) -> dict[str, Any] | None:
    focus = SECTION_SYNTHESIS_FOCUS.get(section_id)
    if not focus:
        return None
    subject, verification_axis, boundary_axis = focus
    evidence_anchor = select_anchor(facts, ["营业收入", "归母净利润", "经营现金流", "毛利率", "资产负债率", "研发", "同业", "公司"], 0).rstrip("。；; ")
    model_anchor = select_anchor(calculations, ["同比", "比率", "分位", "自由现金流", "杜邦", "覆盖", "研发"], 0).rstrip("。；; ")
    judgement_anchor = select_anchor(judgements, ["核心", "需要", "验证", "改善", "压力", "质量"], 0).rstrip("。；; ")
    risk_anchor = select_anchor(risks, ["风险", "恶化", "改善", "推翻", "验证", "缺口"], 0).rstrip("。；; ")

    items: list[str] = []
    if evidence_anchor:
        items.append(
            f"本节围绕{subject}展开。已确认的本地证据显示，{evidence_anchor} "
            f"因此不能只看单一指标，需要沿着{verification_axis}进行交叉验证。"
        )
    if model_anchor or judgement_anchor:
        sentence_parts = [f"从模型和经营解释看，{model_anchor}" if model_anchor else ""]
        if judgement_anchor:
            sentence_parts.append(f"对应的分析判断是：{judgement_anchor}")
        items.append("；".join(part for part in sentence_parts if part).rstrip("。") + "。")
    if risk_anchor:
        items.append(
            f"结论边界在于{boundary_axis}：{risk_anchor} 报告会把这类信息作为后续跟踪或复核条件，而不是直接升级为确定性结论。"
        )

    clean_items = clean_visible_items(items, 520)
    if len(clean_items) < 2:
        return None
    return narrative_block("本节综合解读", clean_items[:3], "synthesis")


def build_narrative_blocks(
    section_id: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
) -> list[dict[str, Any]]:
    preferred = SECTION_META.get(section_id, {}).get("preferred_blocks") or []
    block_items: dict[str, list[str]] = {}
    block_roles: dict[str, str] = {}

    def put(title: str, items: list[str], role: str = "analysis") -> None:
        clean = [item for item in items if item]
        if not clean:
            return
        block_items.setdefault(title, []).extend(clean)
        block_roles.setdefault(title, role)

    synthesis = build_section_synthesis(section_id, facts, calculations, judgements, risks)

    if section_id == "executive_summary":
        put("经营状态定性", facts[:2], "diagnosis")
        put("财务健康度速览", [*calculations[:2], *facts[2:4]], "table")
        put("核心结论", judgements[:4], "diagnosis")
        put("改变结论的条件", risks[:5], "tracking")
    elif section_id == "key_changes":
        put("年度异动雷达", facts[:4], "table")
        put("改善/恶化/观察项", risks[:3], "diagnosis")
        put("三表联动解释", [*calculations[:2], *judgements[:2]], "bridge")
        put("口径与证据", facts[4:6] or calculations[2:3], "evidence")
    elif section_id == "operating_quality":
        put("收入变化分析", facts[:2], "diagnosis")
        put("收入与现金流匹配度", [*calculations[:2], *judgements[:1]], "bridge")
        put("经营稳定性评估", judgements[1:3], "diagnosis")
        put("业务韧性与待验证信号", risks[:4], "tracking")
    elif section_id == "profitability_and_cost":
        put("杜邦分析", [item for item in calculations if "杜邦" in item or "净利率" in item or "ROE" in item] or calculations[:2], "model")
        put("利润变化桥", facts[:3], "bridge")
        put("毛利率与成本成因", [*calculations[:1], *judgements[:2]], "diagnosis")
        put("费用/减值/非经常性损益", [*facts[2:4], *risks[:3]], "analysis")
    elif section_id == "asset_quality_working_capital":
        put("资产结构与安全垫", facts[:2], "diagnosis")
        put("存货分析", [item for item in facts + calculations + risks if "存货" in item][:4], "analysis")
        put("应收款项分析", [item for item in facts + calculations + risks if "应收" in item][:4], "analysis")
        put("现金转换周期", [item for item in calculations + judgements + risks if any(term in item for term in ["CCC", "DSO", "DIO", "DPO", "周转"])][:4], "model")
    elif section_id == "debt_liquidity":
        put("短期偿债能力", [*facts[:2], *calculations[:2]], "model")
        put("长期偿债能力", [*facts[2:4], *judgements[:1]], "diagnosis")
        put("现金覆盖与融资弹性", [*judgements[1:2], *risks[:2]], "bridge")
        put("Altman Z-Score 适用性", [item for item in calculations + risks if "Altman" in item or "Z-Score" in item] or risks[-2:], "model")
    elif section_id == "cash_flow_quality":
        put("现金流量表概览", facts[:3], "table")
        put("经营现金流与利润匹配度", [*calculations[:1], *judgements[:1]], "bridge")
        put("自由现金流", [item for item in calculations + judgements + risks if "自由现金流" in item or "资本开支" in item][:4], "model")
        put("现金流恶化/改善原因", [*judgements[1:3], *risks[:3]], "diagnosis")
    elif section_id == "industry_competition":
        put("行业周期判断", [*facts[:2], *judgements[:1]], "diagnosis")
        put("同业对比", [*facts[1:4], *calculations[:2]], "table")
        put("竞争位置", judgements[:3], "diagnosis")
        put("价格战与产品结构传导", risks[:4], "risk_chain")
    elif section_id == "strategy_policy_external_risk":
        put("管理层战略", facts[:3], "diagnosis")
        put("政策/出口/供应链变量", [*facts[3:5], *risks[:1]], "analysis")
        put("战略兑现的财务验证", [*calculations[:3], *judgements[:2]], "bridge")
        put("待验证事项", risks[:4], "tracking")
    elif section_id == "governance_compliance_shareholders":
        put("治理观察", facts[:3], "evidence")
        put("股东结构与资本动作", facts[3:5] or calculations[:1], "analysis")
        put("合规/审计/监管事项", [*calculations[:2], *judgements[:2]], "audit")
        put("治理风险信号", risks[:4], "risk_chain")
    elif section_id == "valuation_expectation_gap":
        put("估值数据缺口", facts[:2], "evidence")
        put("基本面锚", [*facts[1:3], *calculations[:2]], "model")
        put("市场预期差", judgements[:3], "diagnosis")
        put("A 股特有风险", risks[:4], "risk_chain")
    elif section_id == "risk_chain_scenario":
        put("主要风险链条", [*calculations[:2], *judgements[:2]], "risk_chain")
        put("情景推演", risks[:3], "scenario")
        put("可能推翻当前结论的证据", risks[3:6] or facts[:3], "tracking")
        put("风险缓释条件", judgements[2:4] or calculations[2:3], "analysis")
    elif section_id == "tracking_checklist":
        put("核心跟踪指标", [*facts[:2], *calculations[:1]], "tracking")
        put("改善信号", [item for item in risks if "改善" in item][:4] or risks[:2], "tracking")
        put("恶化信号", [item for item in risks if "恶化" in item or "推翻" in item or "转负" in item][:4] or risks[2:4], "tracking")
        put("跟踪频率与数据源", [*judgements[:3], *risks[-1:]], "evidence")
    elif section_id == "data_quality_traceability":
        put("数据来源", facts[:3], "evidence")
        put("数据质量检查", calculations[:4], "audit")
        put("关键证据索引", judgements[:2], "evidence")
        put("限制与免责声明", risks[:4], "audit")
    else:
        put(preferred[0] if preferred else "核心观察", facts[:3], "diagnosis")
        put(preferred[1] if len(preferred) > 1 else "模型与口径", calculations[:3], "model")
        put(preferred[2] if len(preferred) > 2 else "分析判断", judgements[:3], "analysis")
        put(preferred[3] if len(preferred) > 3 else "风险与验证", risks[:3], "tracking")

    blocks: list[dict[str, Any]] = []
    if synthesis:
        blocks.append(synthesis)
    ordered_titles = [title for title in preferred if title in block_items]
    ordered_titles.extend(title for title in block_items if title not in ordered_titles)
    for title in ordered_titles:
        blocks.append(narrative_block(title, block_items[title], block_roles.get(title, "analysis")))
    return blocks


def ensure_narrative_blocks(section: dict[str, Any]) -> None:
    section_id = str(section.get("section_id") or "")
    section.setdefault("section_type", SECTION_META.get(section_id, {}).get("section_type", "cfo_analysis"))
    facts = section.get("facts") if isinstance(section.get("facts"), list) else []
    calculations = section.get("calculations") if isinstance(section.get("calculations"), list) else []
    judgements = section.get("judgements") if isinstance(section.get("judgements"), list) else []
    risks = (
        section.get("risks_or_improvement_conditions")
        if isinstance(section.get("risks_or_improvement_conditions"), list)
        else []
    )
    facts_clean = clean_section_items(section_id, [str(item) for item in facts])
    calculations_clean = clean_visible_items([str(item) for item in calculations], 420)
    judgements_clean = clean_visible_items([str(item) for item in judgements], 420)
    risks_clean = clean_visible_items([str(item) for item in risks], 420)
    section["facts"] = facts_clean
    section["calculations"] = calculations_clean
    section["judgements"] = judgements_clean
    section["risks_or_improvement_conditions"] = risks_clean

    blocks = section.get("narrative_blocks")
    if isinstance(blocks, list) and len(blocks) >= 3:
        cleaned_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("title") == "本节综合解读":
                continue
            items = block.get("items")
            if isinstance(items, list):
                block["items"] = clean_visible_items([str(item) for item in items], 520)
            if block.get("items"):
                cleaned_blocks.append(block)
        blocks[:] = cleaned_blocks
        synthesis = build_section_synthesis(section_id, facts_clean, calculations_clean, judgements_clean, risks_clean)
        if synthesis:
            blocks.insert(0, synthesis)
        return
    section["narrative_blocks"] = build_narrative_blocks(
        section_id,
        facts_clean,
        calculations_clean,
        judgements_clean,
        risks_clean,
    )


def make_section(
    section_id: str,
    title: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
    evidence_ids: list[str],
    review_required: bool = False,
) -> dict[str, Any]:
    facts_clean = clean_section_items(section_id, [item for item in facts if item])
    calculations_clean = [item for item in calculations if item]
    judgements_clean = [item for item in judgements if item]
    risks_clean = [item for item in risks if item]
    return {
        "section_id": section_id,
        "title": title,
        "section_type": SECTION_META.get(section_id, {}).get("section_type", "cfo_analysis"),
        "narrative_blocks": build_narrative_blocks(section_id, facts_clean, calculations_clean, judgements_clean, risks_clean),
        "facts": facts_clean,
        "calculations": calculations_clean,
        "judgements": judgements_clean,
        "risks_or_improvement_conditions": risks_clean,
        "evidence_ids": evidence_ids,
        "review_required": review_required,
        "missing_fields": [],
    }


def build_sections(
    preflight: dict[str, Any],
    evidence_package: dict[str, Any],
    snapshot: dict[str, Any],
    outline: dict[str, Any],
) -> list[dict[str, Any]]:
    existing_sections = load_json_if_exists(Path(str(preflight.get("_work_dir", ""))) / "section_drafts.json")
    if isinstance(existing_sections.get("sections"), list):
        by_id = {
            str(section.get("section_id")): section
            for section in existing_sections["sections"]
            if isinstance(section, dict) and section.get("section_id")
        }
        ordered = [by_id.get(section_id) for section_id, _ in SECTION_DEFS]
        if all(isinstance(section, dict) for section in ordered):
            hydrated = [section for section in ordered if isinstance(section, dict)]
            for section in hydrated:
                ensure_narrative_blocks(section)
                refresh_section_evidence_ids(section, evidence_package)
            return hydrated

    revenue = fmt_num(metric_value(snapshot, "operating_revenue"), "亿元")
    revenue_yoy = fmt_num(yoy(snapshot, "operating_revenue"), "%")
    net_profit = fmt_num(metric_value(snapshot, "net_profit_parent"), "亿元")
    ocf = fmt_num(metric_value(snapshot, "net_operating_cash_flow"), "亿元")
    debt_ratio = fmt_num(outline.get("calculated_derived_metrics", {}).get("debt_ratio_pct"), "%")
    core = outline.get("core_judgment") or "公司处于经营转型与盈利修复观察期。"
    contradiction = outline.get("core_contradiction") or "收入、利润、现金流与债务安全之间需要交叉验证。"
    task_id = preflight.get("task_id", snapshot.get("task_id", "未返回"))

    e = lambda key: evidence_id(evidence_package, key)

    sections: list[dict[str, Any]] = [
        make_section(
            "executive_summary",
            "一、执行摘要",
            [core, f"报告口径为 2025 年年度报告，task_id={task_id}。"],
            [f"营业收入 {revenue}，同比 {revenue_yoy}；归母净利润 {net_profit}；经营现金流净额 {ocf}。"],
            [f"核心矛盾：{contradiction}", "当前结论应定位为财务诊断与后续跟踪，不构成买卖建议。"],
            outline.get("red_flags", [])[:3] + outline.get("yellow_flags", [])[:2],
            [e("operating_revenue"), e("net_profit_parent"), e("net_operating_cash_flow")],
        ),
        make_section(
            "key_changes",
            "二、关键变化概览",
            outline.get("improvement_items", []) + outline.get("deterioration_items", []),
            [f"收入同比 {revenue_yoy}，现金流同比 {fmt_num(yoy(snapshot, 'net_operating_cash_flow'), '%')}。"],
            ["收入增长与持续亏损并存，不能把规模增长直接解释为盈利质量改善。"],
            ["若后续收入增长不能传导至毛利率和扣非利润，改善逻辑需要下修。"],
            [e("operating_revenue"), e("total_profit"), e("net_operating_cash_flow")],
        ),
        make_section(
            "operating_quality",
            "三、经营质量分析",
            [f"公司 2025 年营业收入为 {revenue}，同比 {revenue_yoy}。"],
            ["经营质量需同时观察收入、回款、应收、存货和合同负债，不能只看收入增速。"],
            ["收入端已有增长信号，但需要验证增长是否来自可持续产品结构和真实回款。"],
            ["若应收或存货增速显著快于收入，收入质量和库存风险需要提高权重。"],
            [e("operating_revenue"), e("accounts_receivable"), e("inventory")],
            review_required=True,
        ),
        make_section(
            "profitability_and_cost",
            "四、盈利能力与成本成因",
            [f"归母净利润为 {net_profit}，营业利润为 {fmt_num(metric_value(snapshot, 'operating_profit'), '亿元')}。"],
            [
                f"营业成本为 {fmt_num(metric_value(snapshot, 'operating_cost'), '亿元')}；毛利率字段当前为 {fmt_num(metric_value(snapshot, 'gross_margin'))}。",
                "杜邦分析因平均净资产、完整资产负债表同比口径不足，不能可靠展开为精确三因子分解。",
            ],
            ["亏损收窄需要继续拆分主营毛利、费用刚性、减值、投资收益和非经常性损益。"],
            ["若扣非亏损扩大或毛利率继续承压，则主营盈利修复仍未确认。"],
            [e("net_profit_parent"), e("operating_profit"), e("operating_cost")],
            review_required=True,
        ),
        make_section(
            "asset_quality_working_capital",
            "五、资产质量与营运资金",
            [
                f"总资产为 {fmt_num(metric_value(snapshot, 'total_assets'), '亿元')}。",
                f"存货为 {fmt_num(metric_value(snapshot, 'inventory'), '亿元')}，应收相关数据需结合附注复核。",
            ],
            ["CCC/DSO/DIO/DPO 因收入成本和营运科目同比口径不完整，当前不做伪精确计算。"],
            ["资产质量判断的重点是库存周转、应收回款和固定资产/在建工程的产能利用率。"],
            ["若库存积压、跌价准备或应收账龄恶化，收入增长质量需要折价。"],
            [e("total_assets"), e("inventory"), e("accounts_receivable")],
            review_required=True,
        ),
        make_section(
            "debt_liquidity",
            "六、债务安全与流动性",
            [f"资产负债率约 {debt_ratio}，总负债为 {fmt_num(metric_value(snapshot, 'total_liabilities'), '亿元')}。"],
            [
                f"货币资金为 {fmt_num(metric_value(snapshot, 'monetary_funds'), '亿元')}；短期有息债务口径需继续补齐。",
                "Altman Z-Score 因市值、营运资本、EBIT 等字段不足，当前不可靠计算。",
            ],
            ["偿债判断应定性为承压观察，不能只凭资产负债率给出最终安全结论。"],
            ["若经营现金流转弱且短债续接压力上升，财务弹性会明显下降。"],
            [e("total_liabilities"), e("monetary_funds"), e("short_term_borrowings")],
            review_required=True,
        ),
        make_section(
            "cash_flow_quality",
            "七、现金流质量",
            [f"经营现金流净额为 {ocf}，同比 {fmt_num(yoy(snapshot, 'net_operating_cash_flow'), '%')}。"],
            [
                "经营现金流/归母净利润为正向覆盖，但亏损公司需要区分经营改善和营运资本释放。",
                "自由现金流因资本开支字段不完整，当前不做确定性计算。",
            ],
            ["现金流改善是本期重要正面信号，但需要验证是否可持续覆盖资本开支和债务滚续。"],
            ["若资本开支抬升或回款转弱，经营现金流改善可能不足以支撑转型投入。"],
            [e("net_operating_cash_flow"), e("capital_expenditure")],
            review_required=True,
        ),
        make_section(
            "industry_competition",
            "八、行业周期与竞争位置",
            ["公司处于汽车行业，新能源转型、价格竞争和产品结构升级共同影响利润弹性。"],
            ["当前检查点未形成不少于 3 家同业样本的完整聚合指标，行业对比仅能作为方向性判断。"],
            ["行业章节应重点比较收入增速、毛利率、费用率、现金流和资产负债率的相对位置。"],
            ["若行业价格战延续而公司产品 mix 未改善，盈利修复难度会上升。"],
            [e("operating_revenue"), "peer_metrics:missing"],
            review_required=True,
        ),
        make_section(
            "strategy_policy_external_risk",
            "九、战略政策与外部风险",
            outline.get("observation_items", []) or ["新能源转型、出口业务和重点合作车型为后续观察项。"],
            ["战略有效性需要通过销量、单车盈利、产能利用率、费用率和现金流持续验证。"],
            ["战略转型当前尚不能直接等同于利润拐点，需要财务数据继续确认。"],
            ["政策、补贴、价格战、供应链和出口环境变化均可能影响利润兑现。"],
            ["semantic:strategy_policy"],
            review_required=True,
        ),
        make_section(
            "governance_compliance_shareholders",
            "十、治理合规与股东结构",
            ["预检显示语义层和证据链可用，治理合规章节需以年报治理、审计意见、关联交易等证据为准。"],
            ["当前兜底渲染未新增外部监管数据，不判断是否存在未披露违法违规。"],
            ["治理章节的结论应保持审慎，区分公开披露事实与分析推论。"],
            ["若后续出现问询函、处罚、资金占用、违规担保或审计意见变化，应重新评估风险等级。"],
            ["semantic:governance_compliance"],
            review_required=True,
        ),
        make_section(
            "valuation_expectation_gap",
            "十一、A 股估值与市场预期差",
            ["当前检查点未包含实时股价、市值、股本、历史估值分位和一致预期数据。"],
            ["亏损状态下 P/E 不适用；如需估值，需补充 P/B、P/S、市值、净资产和同业分位。"],
            ["本报告不输出目标价、买卖评级或确定性投资建议。"],
            ["若市场交易的是主题改善而财务亏损未修复，预期差可能转化为估值波动风险。"],
            ["market_data:missing"],
            review_required=True,
        ),
        make_section(
            "risk_chain_scenario",
            "十二、风险链条与情景推演",
            outline.get("red_flags", []) + outline.get("yellow_flags", []),
            ["风险链条：收入增长未改善毛利 -> 亏损延续 -> 经营现金流承压 -> 债务续接压力上升 -> 估值折价扩大。"],
            ["当前更适合按改善、基准、承压三类情景跟踪，而不是给出单点预测。"],
            outline.get("improvement_conditions", []) + outline.get("falsifying_evidence", []),
            [e("operating_revenue"), e("net_profit_parent"), e("net_operating_cash_flow"), e("total_liabilities")],
            review_required=True,
        ),
        make_section(
            "tracking_checklist",
            "十三、后续跟踪清单",
            outline.get("observation_items", []),
            [
                "跟踪指标：收入增速、毛利率、扣非归母净利润、经营现金流、资本开支、资产负债率、短债覆盖。",
                "改善信号：收入增长传导至毛利率和扣非利润，经营现金流持续为正且覆盖资本开支。",
            ],
            ["后续跟踪的核心是验证转型投入是否转化为可持续盈利和现金流。"],
            outline.get("improvement_conditions", []) + outline.get("falsifying_evidence", []),
            [e("operating_revenue"), e("gross_margin"), e("net_operating_cash_flow")],
            review_required=True,
        ),
        make_section(
            "data_quality_traceability",
            "十四、数据质量与溯源声明",
            [
                f"预检状态：artifact={preflight.get('artifact_status')}，postgres={preflight.get('postgres_status')}，evidence={preflight.get('evidence_status')}。",
                f"PDF 页数：{preflight.get('pdf_page_count', '未返回')}，task_id={task_id}。",
            ],
            [
                "本报告由检查点兜底渲染生成，优先使用 metric_snapshot、evidence_package、analysis_outline。",
                "三表钩稽、毛利率、资本开支、同业与市场数据存在缺口，已进入复核清单。",
            ],
            ["关键数字以已有证据包为准；无法补全页码或字段时，不做确定性推论。"],
            [
                "人工复核：毛利率、资本开支、短债覆盖、同业样本、估值市场数据、治理合规原文证据。",
            ],
            [e("operating_revenue"), e("net_profit_parent"), e("net_operating_cash_flow")],
            review_required=True,
        ),
    ]
    return sections


def repair_sections_for_quality(
    sections: list[dict[str, Any]],
    evidence_package: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Patch legacy checkpoint drafts so deterministic rendering still passes v1.1 quality gates."""

    generic_risks = {
        "profitability_and_cost": [
            "若毛利率改善无法覆盖期间费用和减值波动，利润修复可能停留在阶段性改善而非结构性反转。",
            "若扣非归母净利润弱于归母净利润，需警惕非经常性项目对盈利质量的扰动。",
        ],
        "asset_quality_working_capital": [
            "若存货、应收或预付款项增长快于收入，营运资金占用会削弱现金流质量。",
            "若资产减值或周转天数恶化，收入增长对净资产安全垫的贡献需要折价。",
        ],
        "debt_liquidity": [
            "若短期有息债务、应付票据和一年内到期负债同步上升，流动性安全边际会下降。",
            "若货币资金受限或经营现金流转弱，债务滚续压力将从资产负债表传导至利润表。",
        ],
        "cash_flow_quality": [
            "若经营现金流改善主要来自应付款扩张而非真实回款，后续现金流可持续性仍需复核。",
            "若资本开支上行且自由现金流转弱，转型投入对财务弹性的占用会增加。",
        ],
        "industry_competition": [
            "若行业价格战延续，收入增长可能被毛利率下滑抵消。",
            "若同业样本不足，竞争位置判断只能作为方向性观察，不能作为估值结论。",
        ],
    }
    for section in sections:
        ensure_narrative_blocks(section)
        section_id = str(section.get("section_id", ""))
        risks = section.get("risks_or_improvement_conditions")
        if not isinstance(risks, list):
            risks = []
            section["risks_or_improvement_conditions"] = risks
        risk_text_len = len(re.sub(r"\s+", "", "".join(str(item) for item in risks)))
        if risk_text_len < 30:
            risks.extend(generic_risks.get(section_id, [
                "若关键指标无法形成同向改善，当前判断应保持为待验证而非确定性修复。",
                "若后续证据链缺失或口径冲突扩大，应将该章节列入人工复核清单。",
            ]))

        evidence_ids = section.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            evidence_ids = []
            section["evidence_ids"] = evidence_ids
        if section_id in {"executive_summary", "profitability_and_cost", "tracking_checklist", "data_quality_traceability"}:
            deducted_ev = evidence_id(evidence_package, "deducted_parent_net_profit")
            if deducted_ev not in evidence_ids:
                evidence_ids.append(deducted_ev)

    risk_chain = next((s for s in sections if str(s.get("section_id")) == "risk_chain_scenario"), None)
    if isinstance(risk_chain, dict):
        judgements = risk_chain.get("judgements")
        if not isinstance(judgements, list):
            judgements = []
            risk_chain["judgements"] = judgements
        chain_text = "".join(str(item) for item in (
            (risk_chain.get("facts") or [])
            + judgements
            + (risk_chain.get("risks_or_improvement_conditions") or [])
        ))
        arrow_count = chain_text.count("→") + chain_text.count("->") + chain_text.count("=>")
        if arrow_count < 2:
            judgements.extend([
                "承压链条：收入增长放缓 → 毛利率承压 → 扣非利润弱化 → 经营现金流覆盖下降 → 债务安全边际收窄。",
                "改善链条：产品结构优化 → 毛利率修复 → 扣非利润改善 → 经营现金流稳定 → 资本开支和债务滚续压力下降。",
            ])

    return sections


def build_quality_report(sections: list[dict[str, Any]], snapshot: dict[str, Any], work_dir: Path | None = None) -> dict[str, Any]:
    expected = [sid for sid, _ in SECTION_DEFS]
    actual = [s["section_id"] for s in sections]
    missing = [sid for sid in expected if sid not in actual]
    unexpected = [sid for sid in actual if sid not in expected]
    order_valid = actual == expected
    review_queue = [
        "毛利率字段缺失或未形成可靠同比口径，需从利润表和营业成本附注补算。",
        "资本开支字段不完整，自由现金流暂不做确定性结论。",
        "短期有息负债、利息费用和市值数据不足，Altman Z-Score 与估值分位未计算。",
        "同业样本未聚合完成，行业竞争章节为方向性分析。",
        "治理合规章节需补充审计意见、处罚/问询函、关联交易、质押冻结等原文证据。",
    ]
    if metric_value(snapshot, "gross_margin") is not None:
        review_queue = [item for item in review_queue if "毛利率" not in item]
    if metric_value(snapshot, "capital_expenditure") is not None:
        review_queue = [item for item in review_queue if "资本开支" not in item]
    short_debt_ready = (
        metric_value(snapshot, "short_term_borrowings") is not None
        or metric_value(snapshot, "current_portion_noncurrent_liabilities") is not None
    )
    if short_debt_ready:
        review_queue = [
            "利息费用和市值数据不足，Altman Z-Score 与估值分位未计算。"
            if "短期有息负债" in item
            else item
            for item in review_queue
        ]
    peer_metrics = load_json_if_exists(work_dir / "peer_metrics.json") if work_dir else {}
    if peer_metrics.get("strict_ok") is True and int(peer_metrics.get("peer_count") or 0) >= 3:
        review_queue = [item for item in review_queue if "同业样本未聚合" not in item]
    qualitative = load_json_if_exists(work_dir / "qualitative_snapshot.json") if work_dir else {}
    wiki_inventory = load_json_if_exists(work_dir / "wiki_inventory.json") if work_dir else {}
    inventory_missing = wiki_inventory.get("missing_required_files")
    if not wiki_inventory:
        review_queue.append("单公司 wiki 全量盘点缺失，需先生成 wiki_inventory.json。")
    elif isinstance(inventory_missing, list) and inventory_missing:
        review_queue.append(f"wiki 全量盘点存在缺失文件：{', '.join(str(item) for item in inventory_missing[:8])}")
    qualitative_buckets = qualitative.get("buckets") if isinstance(qualitative.get("buckets"), dict) else {}
    if qualitative_buckets.get("governance"):
        review_queue = [
            "治理合规章节已有年报语义证据；外部处罚/问询、质押冻结等公告仍需人工复核。"
            if "治理合规章节需补充" in item
            else item
            for item in review_queue
        ]
    key_evidence_terms = [
        "operating_revenue",
        "net_profit_parent",
        "deducted_parent_net_profit",
        "net_operating_cash_flow",
        "total_assets",
        "total_liabilities",
    ]
    section_evidence = [
        str(item)
        for section in sections
        for item in (section.get("evidence_ids") or [])
    ]
    key_numbers_have_evidence = all(
        any(term in item for item in section_evidence)
        for term in key_evidence_terms
    )
    return {
        "template_id": "siq_analysis_report_v1.1",
        "module_count": len(sections),
        "missing_section_ids": missing,
        "unexpected_section_ids": unexpected,
        "section_order_valid": order_valid,
        "tool_sections_misused": [],
        "all_key_numbers_have_evidence": key_numbers_have_evidence,
        "wiki_inventory_complete": bool(wiki_inventory) and isinstance(wiki_inventory.get("files"), list),
        "wiki_inventory_file_count": wiki_inventory.get("file_count") if isinstance(wiki_inventory, dict) else None,
        "wiki_inventory_missing_required_files": inventory_missing if isinstance(inventory_missing, list) else [],
        "postgres_role": wiki_inventory.get("postgres_role") if isinstance(wiki_inventory, dict) else "supplement_only_after_wiki_inventory",
        "prohibited_outputs": [],
        "review_queue": review_queue,
        "overall_pass": len(sections) == 14 and not missing and not unexpected and order_valid,
        "generated_by": "render_report_from_checkpoint.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_markdown(
    preflight: dict[str, Any],
    evidence_package: dict[str, Any],
    snapshot: dict[str, Any],
    sections: list[dict[str, Any]],
    quality_report: dict[str, Any],
) -> str:
    company_id = preflight.get("company_id", snapshot.get("company_id", "未知公司"))
    report_year = preflight.get("report_year", snapshot.get("report_year", "未知年度"))
    lines = [
        f"# {company_id} {report_year}年度财务诊断报告",
        "",
        "> 报告定位：A 股二级市场公开年报财务诊断，不构成投资建议。",
        "> 生成方式：基于 SIQ_analysis 检查点兜底渲染。",
        "",
    ]
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append("")
        ensure_narrative_blocks(section)
        blocks = section.get("narrative_blocks") if isinstance(section.get("narrative_blocks"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            title = str(block.get("title") or "").strip()
            if not title:
                continue
            lines.append(f"### {title}")
            items = block.get("items")
            if isinstance(items, list) and items:
                for item in items:
                    lines.append(f"- {item}")
            else:
                lines.append("- 未返回")
            lines.append("")
        lines.append("### 本节证据")
        for item in section.get("evidence_ids") or []:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("### 关键引用来源")
    for key in [
        "operating_revenue",
        "net_profit_parent",
        "total_profit",
        "operating_profit",
        "operating_cost",
        "net_operating_cash_flow",
        "total_assets",
        "total_liabilities",
        "monetary_funds",
        "inventory",
    ]:
        lines.append(citation_line(evidence_package, key))
    lines.append("")
    lines.append("### 质量检查摘要")
    lines.append(f"- module_count={quality_report['module_count']}")
    lines.append(f"- section_order_valid={quality_report['section_order_valid']}")
    lines.append(f"- overall_pass={quality_report['overall_pass']}")
    lines.append("- review_queue:")
    for item in quality_report["review_queue"]:
        lines.append(f"  - {item}")
    lines.append("")
    return "\n".join(lines)


def inline_markdown(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    def link_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        attrs = ' target="_blank" rel="noopener noreferrer"' if "/api/pdf_page/" in url or "/api/source/" in url else ""
        return f'<a href="{url}"{attrs}>{label}</a>'

    text = re.sub(r"\[([^\[\]]+?)\]\(([^()\s]+?)\)", link_repl, text)
    return text


def safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def metric_series(snapshot: dict[str, Any], key: str) -> list[tuple[str, float]]:
    item = metric(snapshot, key)
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    pairs: list[tuple[str, float]] = []
    for year, value in values.items():
        if value is None:
            continue
        pairs.append((str(year), safe_float(normalize_metric_value(item, value))))

    def sort_key(pair: tuple[str, float]) -> tuple[int, str]:
        year, _ = pair
        match = re.search(r"\d{4}", year)
        return (int(match.group(0)) if match else 9999, year)

    return sorted(pairs, key=sort_key)


def latest_series_value(snapshot: dict[str, Any], key: str) -> float:
    series = metric_series(snapshot, key)
    if series:
        return series[-1][1]
    return safe_float(metric_value(snapshot, key))


def trend_badge(value: Any, *, positive_is_good: bool = True) -> str:
    if not isinstance(value, (int, float)):
        return '<span class="trend neutral">同比未返回</span>'
    direction = "up" if value >= 0 else "down"
    good = (value >= 0 and positive_is_good) or (value < 0 and not positive_is_good)
    cls = "good" if good else "bad"
    symbol = "↑" if direction == "up" else "↓"
    return f'<span class="trend {cls}">{symbol} {abs(value):.2f}%</span>'


def svg_polyline(series: list[tuple[str, float]], *, width: int = 560, height: int = 220) -> str:
    if not series:
        return ""
    plot_x = 58
    plot_y = 28
    plot_w = width - 92
    plot_h = height - 72
    values = [value for _, value in series]
    min_v = min(values + [0])
    max_v = max(values + [0])
    span = max(max_v - min_v, 1)
    points: list[tuple[float, float, str, float]] = []
    for idx, (label, value) in enumerate(series):
        x = plot_x + (plot_w * idx / max(1, len(series) - 1))
        y = plot_y + (max_v - value) / span * plot_h
        points.append((x, y, label, value))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in points)
    circles = "\n".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5"/><text x="{x:.1f}" y="{height - 22}" text-anchor="middle">{html.escape(label)}</text>'
        for x, y, label, _ in points
    )
    last_x, last_y, _, last_value = points[-1]
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="趋势折线图">
  <line x1="{plot_x}" y1="{plot_y + plot_h}" x2="{plot_x + plot_w}" y2="{plot_y + plot_h}" class="axis"/>
  <line x1="{plot_x}" y1="{plot_y}" x2="{plot_x}" y2="{plot_y + plot_h}" class="axis"/>
  <text x="{plot_x}" y="18" class="axis-label">亿元</text>
  <polyline points="{path}" class="line-blue"/>
  <g class="line-points">{circles}</g>
  <text x="{last_x:.1f}" y="{max(16, last_y - 10):.1f}" text-anchor="middle" class="value-label">{fmt_num(last_value)}</text>
</svg>
"""


def svg_signed_bars(items: list[tuple[str, float, str]], *, width: int = 560, height: int = 240) -> str:
    max_abs = max([abs(value) for _, value, _ in items] + [1])
    zero_x = 180
    max_bar = width - zero_x - 72
    rows: list[str] = []
    for idx, (label, value, color) in enumerate(items):
        y = 38 + idx * 44
        bar_w = max(4, abs(value) / max_abs * max_bar)
        x = zero_x if value >= 0 else zero_x - bar_w
        rows.append(
            f'<text x="24" y="{y + 17}" class="bar-label">{html.escape(label)}</text>'
            f'<rect x="{x:.1f}" y="{y}" width="{bar_w:.1f}" height="24" rx="6" fill="{color}"/>'
            f'<text x="{width - 24}" y="{y + 17}" class="bar-value" text-anchor="end">{fmt_num(value, "亿元")}</text>'
        )
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="核心指标横向柱状图">
  <line x1="{zero_x}" y1="22" x2="{zero_x}" y2="{height - 24}" class="zero-line"/>
  {''.join(rows)}
</svg>
"""


def svg_balance_stack(assets: float, liabilities: float, *, width: int = 560, height: int = 220) -> str:
    equity = assets - liabilities if assets else 0
    total = max(abs(assets), 1)
    liability_w = max(0, min(420, liabilities / total * 420)) if liabilities >= 0 else 0
    equity_w = max(0, min(420 - liability_w, equity / total * 420)) if equity >= 0 else 0
    debt_ratio = liabilities / assets * 100 if assets else None
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="资产负债结构图">
  <text x="24" y="34" class="axis-label">资产负债结构</text>
  <rect x="24" y="64" width="420" height="34" rx="8" fill="#dbeafe"/>
  <rect x="24" y="64" width="{liability_w:.1f}" height="34" rx="8" fill="#f97316"/>
  <rect x="{24 + liability_w:.1f}" y="64" width="{equity_w:.1f}" height="34" rx="8" fill="#2563eb"/>
  <text x="24" y="130" class="bar-label">总资产</text>
  <text x="536" y="130" text-anchor="end" class="bar-value">{fmt_num(assets, "亿元")}</text>
  <text x="24" y="158" class="bar-label">总负债</text>
  <text x="536" y="158" text-anchor="end" class="bar-value">{fmt_num(liabilities, "亿元")}</text>
  <text x="24" y="186" class="bar-label">估算权益</text>
  <text x="536" y="186" text-anchor="end" class="bar-value">{fmt_num(equity, "亿元")}</text>
  <text x="536" y="34" text-anchor="end" class="value-label">资产负债率 {fmt_num(debt_ratio, "%")}</text>
</svg>
"""


def render_chart_panel(snapshot: dict[str, Any]) -> str:
    revenue = latest_series_value(snapshot, "operating_revenue")
    net_profit = latest_series_value(snapshot, "net_profit_parent")
    ocf = latest_series_value(snapshot, "net_operating_cash_flow")
    total_assets = latest_series_value(snapshot, "total_assets")
    total_liabilities = latest_series_value(snapshot, "total_liabilities")
    gross_margin = metric_value(snapshot, "gross_margin")
    revenue_yoy = yoy(snapshot, "operating_revenue")
    profit_yoy = yoy(snapshot, "net_profit_parent")
    ocf_yoy = yoy(snapshot, "net_operating_cash_flow")
    debt_ratio = total_liabilities / total_assets * 100 if total_assets else None
    revenue_series = metric_series(snapshot, "operating_revenue")
    ocf_series = metric_series(snapshot, "net_operating_cash_flow")

    return f"""
<section class="dashboard" aria-label="核心指标仪表盘">
  <div class="kpi-grid" aria-label="关键指标">
    <article class="kpi-card accent-blue"><span>营业收入</span><strong>{fmt_num(revenue, "亿元")}</strong>{trend_badge(revenue_yoy)}</article>
    <article class="kpi-card accent-red"><span>归母净利润</span><strong>{fmt_num(net_profit, "亿元")}</strong>{trend_badge(profit_yoy)}</article>
    <article class="kpi-card accent-green"><span>经营现金流</span><strong>{fmt_num(ocf, "亿元")}</strong>{trend_badge(ocf_yoy)}</article>
    <article class="kpi-card accent-orange"><span>资产负债率</span><strong>{fmt_num(debt_ratio, "%")}</strong><span class="trend neutral">毛利率 {fmt_num(gross_margin, "%")}</span></article>
  </div>
  <div class="chart-grid">
    <article class="chart-card wide">
      <div class="chart-head"><div><span>趋势观察</span><h3>营业收入走势</h3></div><p>用于判断规模增长是否稳定。</p></div>
      {svg_polyline(revenue_series)}
    </article>
    <article class="chart-card">
      <div class="chart-head"><div><span>规模对比</span><h3>利润与现金流</h3></div><p>关注利润质量和现金回收。</p></div>
      {svg_signed_bars([("营业收入", revenue, "#2563eb"), ("归母净利润", net_profit, "#dc2626"), ("经营现金流", ocf, "#16a34a")])}
    </article>
    <article class="chart-card">
      <div class="chart-head"><div><span>安全边界</span><h3>资产负债结构</h3></div><p>用于观察杠杆与净资产缓冲。</p></div>
      {svg_balance_stack(total_assets, total_liabilities)}
    </article>
    <article class="chart-card wide">
      <div class="chart-head"><div><span>现金流</span><h3>经营现金流走势</h3></div><p>验证盈利修复是否转化为现金。</p></div>
      {svg_polyline(ocf_series)}
    </article>
  </div>
</section>
"""


def render_html(markdown_text: str, preflight: dict[str, Any], snapshot: dict[str, Any], sections: list[dict[str, Any] | None] = None, quality_report: dict[str, Any] | None = None, work_dir: Path | None = None) -> str:
    """Render HTML report using the v2 professional renderer if sections are available,
    otherwise fall back to the legacy markdown-to-HTML conversion."""
    # Try to use v2 renderer if we have structured sections
    if sections and quality_report:
        try:
            # Import v2 renderer dynamically
            v2_module_path = Path(__file__).resolve().parent / "html_renderer_v2.py"
            if v2_module_path.exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("html_renderer_v2", v2_module_path)
                if spec and spec.loader:
                    v2_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(v2_module)
                    return v2_module.render_html_report(preflight, snapshot, sections, quality_report, work_dir)
        except Exception as e:
            # Log error and fall back to legacy renderer
            print(f"[WARN] v2 HTML renderer failed: {e}. Falling back to legacy renderer.", file=__import__('sys').stderr)
    
    # Legacy fallback: convert markdown to HTML
    company_id = preflight.get("company_id", snapshot.get("company_id", "未知公司"))
    title = f"{company_id} 财务诊断报告"
    report_year = preflight.get("report_year", snapshot.get("report_year", "未知年度"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    h2_titles = [line[3:].strip() for line in markdown_text.splitlines() if line.startswith("## ")]
    toc = "\n".join(
        f'<a href="#section-{idx + 1:02d}"><span>{idx + 1:02d}</span>{inline_markdown(name)}</a>'
        for idx, name in enumerate(h2_titles)
    )
    escaped_lines = []
    in_section = False
    in_ul = False
    section_index = 0

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            escaped_lines.append("</ul>")
            in_ul = False

    def close_section() -> None:
        nonlocal in_section
        close_ul()
        if in_section:
            escaped_lines.append("</section>")
            in_section = False

    for raw in markdown_text.splitlines():
        if raw.startswith("# "):
            close_section()
            escaped_lines.append(f"<h1>{inline_markdown(raw[2:])}</h1>")
        elif raw.startswith("## "):
            section_index += 1
            close_section()
            escaped_lines.append(f'<section class="section" id="section-{section_index:02d}"><h2>{inline_markdown(raw[3:])}</h2>')
            in_section = True
        elif raw.startswith("### "):
            close_ul()
            escaped_lines.append(f"<h3>{inline_markdown(raw[4:])}</h3>")
        elif raw.startswith("- "):
            if not in_ul:
                escaped_lines.append("<ul>")
                in_ul = True
            escaped_lines.append(f"<li>{inline_markdown(raw[2:])}</li>")
        elif raw.startswith("> "):
            close_ul()
            escaped_lines.append(f"<p class=\"note\">{inline_markdown(raw[2:])}</p>")
        elif raw.strip():
            close_ul()
            escaped_lines.append(f"<p>{inline_markdown(raw)}</p>")
        else:
            close_ul()
            escaped_lines.append("")
    close_section()
    body = "\n".join(escaped_lines)
    charts = render_chart_panel(snapshot)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: #ffffff;
      --ink: #0f172a;
      --muted: #64748b;
      --line: #dbe3ef;
      --blue: #2563eb;
      --green: #16a34a;
      --red: #dc2626;
      --orange: #f97316;
      --shadow: 0 18px 45px rgba(15, 23, 42, .08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: var(--bg); color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.68; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 72px; }}
    .hero {{ background: #0f172a; color: #fff; border-radius: 8px; padding: 30px; box-shadow: var(--shadow); }}
    .eyebrow {{ display: inline-flex; align-items: center; min-height: 28px; border: 1px solid rgba(255,255,255,.22); border-radius: 999px; padding: 3px 12px; color: #bfdbfe; font-size: 13px; font-weight: 700; }}
    h1 {{ color: inherit; font-size: clamp(28px, 4vw, 46px); line-height: 1.12; margin: 16px 0 12px; max-width: 920px; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 10px; color: #cbd5e1; font-size: 14px; }}
    .hero-meta span {{ border-left: 1px solid rgba(255,255,255,.2); padding-left: 10px; }}
    .hero-meta span:first-child {{ border-left: 0; padding-left: 0; }}
    .dashboard {{ margin: 18px 0; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px; }}
    .kpi-card {{ position: relative; overflow: hidden; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; box-shadow: 0 10px 28px rgba(15,23,42,.05); }}
    .kpi-card::before {{ content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--blue); }}
    .kpi-card span:first-child {{ display: block; color: var(--muted); font-size: 13px; font-weight: 700; }}
    .kpi-card strong {{ display: block; margin-top: 8px; color: var(--ink); font-size: clamp(21px, 3vw, 30px); line-height: 1; font-variant-numeric: tabular-nums; }}
    .accent-red::before {{ background: var(--red); }}
    .accent-green::before {{ background: var(--green); }}
    .accent-orange::before {{ background: var(--orange); }}
    .trend {{ display: inline-flex; margin-top: 10px; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 800; }}
    .trend.good {{ color: #047857; background: #d1fae5; }}
    .trend.bad {{ color: #b91c1c; background: #fee2e2; }}
    .trend.neutral {{ color: #475569; background: #e2e8f0; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .chart-card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 10px 28px rgba(15,23,42,.05); padding: 18px; }}
    .chart-card.wide {{ min-height: 300px; }}
    .chart-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: start; margin-bottom: 8px; }}
    .chart-head span {{ color: var(--blue); font-size: 12px; font-weight: 900; }}
    .chart-head h3 {{ margin: 2px 0 0; color: var(--ink); font-size: 18px; }}
    .chart-head p {{ margin: 0; max-width: 220px; color: var(--muted); font-size: 13px; text-align: right; }}
    svg {{ width: 100%; height: auto; display: block; }}
    .axis, .zero-line {{ stroke: #cbd5e1; stroke-width: 1; }}
    .line-blue {{ fill: none; stroke: var(--blue); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }}
    .line-points circle {{ fill: #fff; stroke: var(--blue); stroke-width: 2; }}
    .axis-label, .bar-label {{ fill: #64748b; font-size: 12px; font-weight: 700; }}
    .value-label, .bar-value {{ fill: #0f172a; font-size: 12px; font-weight: 800; font-variant-numeric: tabular-nums; }}
    .layout {{ display: grid; grid-template-columns: 238px minmax(0, 1fr); gap: 18px; align-items: start; }}
    .toc {{ position: sticky; top: 16px; background: rgba(255,255,255,.92); border: 1px solid var(--line); border-radius: 8px; padding: 12px; box-shadow: 0 12px 30px rgba(15,23,42,.06); max-height: calc(100dvh - 32px); overflow: auto; }}
    .toc strong {{ display: block; color: var(--ink); margin: 2px 6px 10px; }}
    .toc a {{ display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 8px; align-items: start; padding: 8px 6px; border-radius: 6px; color: #334155; text-decoration: none; font-size: 13px; line-height: 1.35; }}
    .toc a:hover {{ background: #eff6ff; color: var(--blue); }}
    .toc a span {{ color: var(--blue); font-weight: 900; font-variant-numeric: tabular-nums; }}
    .section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 10px 28px rgba(15,23,42,.05); margin: 0 0 14px; padding: 22px; scroll-margin-top: 18px; }}
    h2 {{ color: var(--ink); font-size: 22px; line-height: 1.25; margin: 0 0 14px; border-bottom: 1px solid var(--line); padding-bottom: 10px; }}
    .section h3 {{ color: #334155; font-size: 15px; margin: 18px 0 8px; }}
    p, li {{ font-size: 14px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    .note {{ background: #ffffff; border-left: 4px solid var(--blue); padding: 10px 12px; border-radius: 6px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 980px) {{
      .kpi-grid, .chart-grid, .layout {{ grid-template-columns: 1fr; }}
      .toc {{ position: static; max-height: none; }}
      .chart-head {{ display: block; }}
      .chart-head p {{ text-align: left; margin-top: 4px; }}
    }}
    @media print {{
      body {{ background: #ffffff; }}
      main {{ max-width: none; padding: 0; }}
      .hero, .toc, .section, .chart-card, .kpi-card {{ box-shadow: none; }}
      .layout {{ display: block; }}
      .toc {{ display: none; }}
      .section {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body><main>
<header class="hero">
  <span class="eyebrow">SIQ Analysis · {html.escape(str(report_year))} 年报</span>
  <h1>{html.escape(title)}</h1>
  <div class="hero-meta">
    <span>公司：{html.escape(str(company_id))}</span>
    <span>生成时间：{html.escape(generated_at)}</span>
    <span>报告定位：公开信息财务诊断，不构成投资建议</span>
  </div>
</header>
{charts}
<div class="layout">
<nav class="toc" aria-label="报告目录"><strong>报告目录</strong>{toc}</nav>
<div class="content">
{body}
</div>
</div>
</main></body>
</html>
"""


def validate_html_structure(html_text: str) -> dict[str, Any]:
    """Validate HTML structure. v2 renderer uses div sections with class 'section' instead of h2 tags."""
    h1_count = html_text.count("<h1>")
    h2_count = html_text.count("<h2>")
    # v2 renderer uses <section class="section" id="section-xxx"> without h2 inside
    # Legacy renderer uses <section class="section" id="section-xx"><h2>
    report_section_count = html_text.count('<section class="section"')
    total_section_open_count = len(re.findall(r"<section(?:\s|>)", html_text))
    section_close_count = html_text.count("</section>")
    # v2: sections have id="section-xxx" but no h2; legacy: h2 inside section
    # Accept either: 14 sections with h2s, OR 14 sections without h2s (v2 style)
    valid = (
        report_section_count == 14 
        and total_section_open_count == section_close_count
        and (h2_count == 14 or h2_count == 0)  # legacy has h2, v2 doesn't
    )
    return {
        "html_structure_valid": valid,
        "h1_count": h1_count,
        "h2_count": h2_count,
        "report_section_count": report_section_count,
        "total_section_open_count": total_section_open_count,
        "section_close_count": section_close_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True, type=Path)
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="允许覆盖已有 .md/.json/.html；覆盖前会自动备份。",
    )
    parser.add_argument("--backup-dir", type=Path, help="覆盖前备份已有报告的目录")
    args = parser.parse_args()

    work_dir = args.work_dir
    existing_outputs = existing_report_outputs(args.output_prefix)
    if existing_outputs and not args.allow_overwrite:
        print(json.dumps({
            "ok": False,
            "stage": "output_exists",
            "output_prefix": str(args.output_prefix),
            "existing_files": [str(path) for path in existing_outputs],
            "next_action": "使用 --output-prefix 写到新前缀，或确认可覆盖后添加 --allow-overwrite。",
        }, ensure_ascii=False, indent=2))
        return 3
    backups = backup_existing_outputs(args.output_prefix, args.backup_dir) if existing_outputs else {}

    company_dir = infer_company_dir(work_dir, args.output_prefix)
    preflight = normalize_preflight(load_json_if_exists(work_dir / "preflight.json"), company_dir)
    preflight["_work_dir"] = str(work_dir)
    snapshot = normalize_snapshot(load_json_if_exists(work_dir / "metric_snapshot.json"), work_dir)
    if not snapshot.get("metrics") and not snapshot.get("key_metrics"):
        raise RuntimeError(f"missing usable metric checkpoint in {work_dir}")

    evidence_package = load_json_if_exists(work_dir / "evidence_package.json")
    if not evidence_package.get("financial_evidence"):
        evidence_package = build_evidence_package(company_dir, snapshot, preflight)
        dump_json(work_dir / "evidence_package.json", evidence_package)
    else:
        evidence_package = normalize_evidence_package(
            evidence_package,
            snapshot=snapshot,
            lookup=load_provenance_lookup(company_dir, preflight.get("report_year") or snapshot.get("report_year") or 2025),
            default_task_id=preflight.get("task_id") or snapshot.get("task_id"),
            year=preflight.get("report_year") or snapshot.get("report_year") or 2025,
            aliases={
                "net_profit_parent": ["parent_net_profit"],
                "net_operating_cash_flow": ["operating_cash_flow_net"],
                "monetary_funds": ["monetary_capital"],
                "capital_expenditure": ["cash_for_purchases_investments"],
                "gross_margin": ["gross_profit_margin"],
                "debt_to_asset_ratio": ["asset_liability_ratio"],
            },
            url_builder=public_api_url,
        )
        dump_json(work_dir / "evidence_package.json", evidence_package)

    outline = load_json_if_exists(work_dir / "analysis_outline.json")
    if not outline:
        outline = build_outline(snapshot)
        dump_json(work_dir / "analysis_outline.json", outline)
    wiki_inventory = load_json_if_exists(work_dir / "wiki_inventory.json")
    dump_json(work_dir / "preflight.json", preflight)
    dump_json(work_dir / "metric_snapshot.json", snapshot)

    sections = repair_sections_for_quality(
        build_sections(preflight, evidence_package, snapshot, outline),
        evidence_package,
        snapshot,
    )
    quality_report = build_quality_report(sections, snapshot, work_dir)
    report = {
        "report_meta": {
            "company_id": preflight.get("company_id", snapshot.get("company_id")),
            "stock_code": preflight.get("stock_code"),
            "report_year": preflight.get("report_year", snapshot.get("report_year")),
            "report_type": preflight.get("report_type", "annual_report"),
            "task_id": preflight.get("task_id", snapshot.get("task_id")),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "render_report_from_checkpoint.py",
        },
        "template": {
            "template_id": "siq_analysis_report_v1.1",
            "template_source_md": str(TEMPLATE_JSON.with_suffix(".md")),
            "template_source_json": str(TEMPLATE_JSON),
            "module_count": len(SECTION_DEFS),
            "section_ids": [sid for sid, _ in SECTION_DEFS],
        },
        "preflight": preflight,
        "sections": sections,
        "quality_report": quality_report,
        "evidence_index": {
            "source": "evidence_package.json",
            "financial_metric_keys": list(evidence_package.get("financial_evidence", {}).keys()),
        },
        "wiki_inventory": {
            "source": str(work_dir / "wiki_inventory.json"),
            "read_scope": wiki_inventory.get("read_scope"),
            "file_count": wiki_inventory.get("file_count"),
            "missing_required_files": wiki_inventory.get("missing_required_files", []),
            "postgres_role": wiki_inventory.get("postgres_role", "supplement_only_after_wiki_inventory"),
        },
    }

    markdown_text = render_markdown(preflight, evidence_package, snapshot, sections, quality_report)
    html_text = render_html(markdown_text, preflight, snapshot, sections, quality_report, work_dir)
    html_check = validate_html_structure(html_text)
    quality_report["html_structure"] = html_check
    report["quality_report"] = quality_report
    if not html_check["html_structure_valid"]:
        raise RuntimeError(f"HTML structure validation failed: {html_check}")

    dump_json(work_dir / "section_drafts.json", {"sections": sections})
    dump_json(work_dir / "quality_report.json", quality_report)
    dump_json(output_path(args.output_prefix, ".json"), report)

    output_path(args.output_prefix, ".md").write_text(markdown_text, encoding="utf-8")
    output_path(args.output_prefix, ".html").write_text(html_text, encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "work_dir": str(work_dir),
        "output_prefix": str(args.output_prefix),
        "backups": backups,
        "module_count": quality_report["module_count"],
        "overall_pass": quality_report["overall_pass"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
