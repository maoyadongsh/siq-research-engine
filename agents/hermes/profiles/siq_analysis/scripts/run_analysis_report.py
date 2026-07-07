#!/usr/bin/env python3
"""Deterministic SIQ annual report pipeline.

The model may still write high-quality section drafts, but the operational
steps are fixed here: resolve company, create checkpoints, optionally consume
existing drafts, render artifacts, repair citations, validate quality, and
return one machine-readable result.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from provenance_utils import (
    is_missing,
    load_provenance_lookup,
    normalize_evidence_package,
    normalize_evidence_record,
)


SCRIPT_DIR = Path(__file__).resolve().parent
RESOLVE_SCRIPT = SCRIPT_DIR / "resolve_company.py"
GENERATE_DRAFTS_SCRIPT = SCRIPT_DIR / "generate_section_drafts.py"
RUN_RESEARCH_SUBAGENTS_SCRIPT = SCRIPT_DIR / "run_research_subagents.py"
VALIDATE_RESEARCH_PACKS_SCRIPT = SCRIPT_DIR / "validate_research_packs.py"
MERGE_RESEARCH_PACKS_SCRIPT = SCRIPT_DIR / "merge_research_packs.py"
PEER_METRICS_SCRIPT = SCRIPT_DIR / "peer_metrics_builder.py"
QUALITATIVE_EVIDENCE_SCRIPT = SCRIPT_DIR / "qualitative_evidence_builder.py"
MARKET_SNAPSHOT_SCRIPT = SCRIPT_DIR / "market_snapshot_builder.py"
INDUSTRY_RESEARCH_SCRIPT = SCRIPT_DIR / "industry_research_builder.py"
RECOVER_SCRIPT = SCRIPT_DIR / "recover_report_from_workdir.py"
VALIDATE_SCRIPT = SCRIPT_DIR / "validate_report_quality.py"
TEMPLATE_JSON = SCRIPT_DIR.parent / "templates" / "siq_analysis_report_v1.1.json"
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


CORE_KEYS = [
    "operating_revenue",
    "operating_cost",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "monetary_capital",
    "inventory",
    "accounts_receivable",
    "notes_receivable",
    "short_term_borrowings",
    "current_portion_noncurrent_liabilities",
    "current_assets",
    "current_liabilities",
    "contract_liabilities",
    "cash_for_purchases",
]

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
    "capital_expenditure",
    "operating_profit",
    "total_profit",
    "interest_expense",
}

ALIASES = {
    "net_profit_parent": "parent_net_profit",
    "net_operating_cash_flow": "operating_cash_flow_net",
    "monetary_funds": "monetary_capital",
    "capital_expenditure": "cash_for_purchases",
}


WIKI_REQUIRED_PATTERNS = [
    "company.json",
    "company.md",
    "_index.json",
    "reports/{year}-annual/report.json",
    "reports/{year}-annual/report.md",
    "reports/{year}-annual/document_full.json",
    "reports/{year}-annual/artifact_manifest.json",
    "metrics/key_metrics.json",
    "metrics/three_statements.json",
    "metrics/validation.json",
    "evidence/evidence_index.json",
    "evidence/pdf_refs.json",
    "semantic/facts.json",
    "semantic/claims.json",
    "semantic/relations.json",
    "semantic/segments.json",
    "semantic/evidence_semantic.json",
    "semantic/llm/{year}-annual/business_profile.json",
    "semantic/llm/{year}-annual/risks.json",
    "semantic/llm/{year}-annual/events.json",
    "semantic/llm/{year}-annual/review_queue.json",
    "graph/report.md",
    "graph/company.md",
    "tracking/report_manifest.json",
    "tracking/tracking-items.md",
]

WIKI_KEY_DIRS = [
    "reports",
    "metrics",
    "evidence",
    "semantic",
    "graph",
    "tracking",
    "factcheck",
    "analysis",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


SENSITIVE_CMD_VALUE_FLAGS = {
    "--api-key",
    "--auth-token",
    "--bearer",
    "--benchmark-hint",
    "--password",
    "--research-benchmark-hint",
    "--research-prompt",
    "--research-subagent-prompt",
    "--secret",
    "--token",
}

SENSITIVE_CMD_VALUE_PREFIXES = tuple(f"{flag}=" for flag in sorted(SENSITIVE_CMD_VALUE_FLAGS))


def redact_cmd(cmd: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for raw_part in cmd:
        part = str(raw_part)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if part in SENSITIVE_CMD_VALUE_FLAGS:
            redacted.append(part)
            redact_next = True
            continue
        matched_prefix = next((prefix for prefix in SENSITIVE_CMD_VALUE_PREFIXES if part.startswith(prefix)), None)
        if matched_prefix:
            redacted.append(f"{matched_prefix}<redacted>")
        else:
            redacted.append(part)
    return redacted


def run_json(cmd: list[str]) -> dict[str, Any]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    payload: Any = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
    return {
        "cmd": redact_cmd(cmd),
        "returncode": result.returncode,
        "stdout": stdout[-4000:],
        "stderr": result.stderr.strip()[-4000:],
        "json": payload,
        "ok": result.returncode == 0,
    }


def safe_json_summary(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {"readable": False}
    summary: dict[str, Any] = {"readable": True, "top_level_type": type(payload).__name__}
    if isinstance(payload, dict):
        summary["top_level_keys"] = list(payload.keys())[:30]
        for key in ["data", "evidence", "facts", "claims", "segments", "metrics", "items"]:
            value = payload.get(key)
            if isinstance(value, list):
                summary[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                summary[f"{key}_keys"] = len(value)
        if "generated_at" in payload:
            summary["generated_at"] = payload.get("generated_at")
        if "task_id" in payload:
            summary["task_id"] = payload.get("task_id")
    elif isinstance(payload, list):
        summary["item_count"] = len(payload)
    return summary


def build_wiki_inventory(company_dir: Path, year: int) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    suffix_counts: dict[str, int] = {}
    directory_counts: dict[str, int] = {}
    for path in sorted(company_dir.rglob("*")):
        if path.is_dir():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(company_dir).as_posix()
        total_bytes += stat.st_size
        suffix = path.suffix.lower() or "<none>"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        top_dir = rel.split("/", 1)[0]
        directory_counts[top_dir] = directory_counts.get(top_dir, 0) + 1
        entry: dict[str, Any] = {
            "path": rel,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }
        if path.suffix.lower() == ".json" and stat.st_size <= 8_000_000:
            entry["json_summary"] = safe_json_summary(path)
        elif path.suffix.lower() in {".md", ".html"}:
            entry["text_summary"] = {
                "readable": True,
                "lines": None,
                "indexed_only": stat.st_size > 1_000_000,
            }
        files.append(entry)

    required_files: list[dict[str, Any]] = []
    missing_required: list[str] = []
    for pattern in WIKI_REQUIRED_PATTERNS:
        rel = pattern.format(year=year)
        path = company_dir / rel
        exists = path.exists()
        required_files.append({"path": rel, "exists": exists})
        if not exists:
            missing_required.append(rel)

    key_dirs = [
        {
            "path": directory,
            "exists": (company_dir / directory).exists(),
            "file_count": directory_counts.get(directory, 0),
        }
        for directory in WIKI_KEY_DIRS
    ]
    return {
        "schema_version": 1,
        "generated_by": "run_analysis_report.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_dir": str(company_dir),
        "report_year": year,
        "read_scope": "single_company_wiki_full_inventory",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "suffix_counts": suffix_counts,
        "key_dirs": key_dirs,
        "required_files": required_files,
        "missing_required_files": missing_required,
        "files": files,
        "postgres_role": "supplement_only_after_wiki_inventory",
        "notes": [
            "大文件采用目录级/索引级盘点，不把全文塞入 section_drafts。",
            "最终事实优先使用 wiki metrics/evidence/PDF 链接；PostgreSQL 仅用于补缺、交叉校验和补页码。",
        ],
    }


def value_to_yi(value: Any, unit: str | None, metric_key: str | None = None) -> Any:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return value
    unit = str(unit or "").strip()
    if unit in {"元", "人民币元", "CNY", "千元"}:
        return value / 100_000_000
    if unit in {"万元"}:
        return value / 10_000
    if not unit and metric_key in MONETARY_METRIC_KEYS and abs(value) >= 100_000:
        return value / 100_000_000
    return value


def normalize_metric_item(item: dict[str, Any], source_file: str) -> dict[str, Any]:
    key = str(item.get("canonical_name") or item.get("metric_key") or item.get("metric_name") or "")
    key = ALIASES.get(key, key)
    unit = str(item.get("unit") if item.get("unit") is not None else item.get("unit_hint") or "").strip()
    values: dict[str, Any] = {}
    if isinstance(item.get("values"), dict):
        for year, value in item["values"].items():
            values[str(year)] = value_to_yi(value, unit, key)
    elif item.get("period") and item.get("normalized_value") is not None:
        period = str(item.get("period"))
        year = period[:4]
        values[year] = item.get("normalized_value")
    sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    normalized_sources: dict[str, Any] = {}
    for year, item_source in sources.items():
        normalized_sources[str(year)] = {
            "file": source_file,
            "table_index": item_source.get("table_index"),
            "line": item_source.get("line"),
            "md_line": item_source.get("md_line"),
            "pdf_page": item_source.get("pdf_page"),
            "task_id": item_source.get("task_id"),
        }
    if source:
        year = str(item.get("period", ""))[:4] or "2025"
        normalized_sources[year] = {
            "file": source_file,
            "table_index": source.get("table_index"),
            "line": source.get("line"),
            "md_line": source.get("md_line"),
            "pdf_page": source.get("pdf_page"),
            "pdf_page_number": source.get("pdf_page_number"),
            "task_id": source.get("task_id"),
        }
    normalized_unit = "亿元" if key in MONETARY_METRIC_KEYS else unit
    if unit in {"元", "人民币元", "CNY", "万元"}:
        normalized_unit = "亿元"
    return {
        "canonical_name": key,
        "display_name": item.get("name") or item.get("metric_name") or key,
        "unit": normalized_unit,
        "values": values,
        "sources": normalized_sources,
        "quality": "source_loaded",
    }


def merge_metric(target: dict[str, Any], item: dict[str, Any]) -> None:
    key = item.get("canonical_name")
    if not key:
        return
    default_unit = "亿元" if key in MONETARY_METRIC_KEYS else item.get("unit", "")
    existing = target.setdefault(key, {"canonical_name": key, "unit": default_unit, "values": {}, "sources": {}})
    existing.setdefault("display_name", item.get("display_name") or key)
    existing.setdefault("unit", item.get("unit", default_unit))
    existing["values"].update({year: value for year, value in item.get("values", {}).items() if value is not None})
    existing["sources"].update({year: src for year, src in item.get("sources", {}).items() if src})
    existing["quality"] = item.get("quality", existing.get("quality", "source_loaded"))


def build_metric_snapshot(company_dir: Path, resolved: dict[str, Any], year: int) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    key_metrics = load_json_if_exists(company_dir / "metrics" / "key_metrics.json")
    for item in key_metrics.get("data", []) or []:
        if isinstance(item, dict):
            merge_metric(metrics, normalize_metric_item(item, "metrics/key_metrics.json"))

    three_statements = load_json_if_exists(company_dir / "metrics" / "three_statements.json")
    for item in three_statements.get("data", {}).get("metrics", []) or []:
        if isinstance(item, dict):
            merge_metric(metrics, normalize_metric_item(item, "metrics/three_statements.json"))

    operating_revenue = metric_value(metrics, "operating_revenue", str(year))
    operating_cost = metric_value(metrics, "operating_cost", str(year))
    if isinstance(operating_revenue, (int, float)) and operating_revenue:
        if isinstance(operating_cost, (int, float)):
            # 营业成本在现金流量表中可能为负值，需取绝对值
            operating_cost_abs = abs(operating_cost)
            gross_margin = (operating_revenue - operating_cost_abs) / operating_revenue * 100
            metrics["gross_margin"] = {
                "canonical_name": "gross_margin",
                "display_name": "毛利率",
                "unit": "%",
                "values": {str(year): gross_margin},
                "sources": {
                    str(year): {
                        "derived_from": ["operating_revenue", "operating_cost"],
                        "formula": "(营业收入-|营业成本|)/营业收入",
                    }
                },
                "quality": "derived",
            }

    ocf = metric_value(metrics, "operating_cash_flow_net", str(year))
    capex = metric_value(metrics, "cash_for_purchases", str(year))
    if isinstance(ocf, (int, float)) and isinstance(capex, (int, float)):
        metrics["free_cash_flow"] = {
            "canonical_name": "free_cash_flow",
            "display_name": "自由现金流",
            "unit": "亿元",
            "values": {str(year): ocf - abs(capex)},
            "sources": {
                str(year): {
                    "derived_from": ["operating_cash_flow_net", "cash_for_purchases"],
                    "formula": "经营现金流净额-购建固定资产无形资产和其他长期资产支付的现金",
                }
            },
            "quality": "derived",
        }

    total_assets = metric_value(metrics, "total_assets", str(year))
    total_liabilities = metric_value(metrics, "total_liabilities", str(year))
    if isinstance(total_assets, (int, float)) and total_assets and isinstance(total_liabilities, (int, float)):
        metrics["debt_to_asset_ratio"] = {
            "canonical_name": "debt_to_asset_ratio",
            "display_name": "资产负债率",
            "unit": "%",
            "values": {str(year): total_liabilities / total_assets * 100},
            "sources": {
                str(year): {
                    "derived_from": ["total_liabilities", "total_assets"],
                    "formula": "总负债/总资产",
                }
            },
            "quality": "derived",
        }

    missing_core = [key for key in CORE_KEYS if key not in metrics]
    return {
        "schema_version": 1,
        "generated_by": "run_analysis_report.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": resolved["company"]["company_id"],
        "stock_code": resolved["company"]["stock_code"],
        "company_short_name": resolved["company"]["company_short_name"],
        "report_year": year,
        "task_id": resolved["report"].get("task_id"),
        "metrics": metrics,
        "missing_core_metrics": missing_core,
    }


def metric(metrics: dict[str, Any], key: str) -> dict[str, Any]:
    return metrics.get(key) or metrics.get(ALIASES.get(key, "")) or {}


def metric_value(metrics: dict[str, Any], key: str, year: str = "2025") -> Any:
    item = metric(metrics, key)
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return values.get(year)


def yoy(metrics: dict[str, Any], key: str, year: int) -> Any:
    current = metric_value(metrics, key, str(year))
    previous = metric_value(metrics, key, str(year - 1))
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous:
        return (current - previous) / abs(previous) * 100
    return None


def evidence_by_metric(company_dir: Path) -> dict[str, list[dict[str, Any]]]:
    index = load_json_if_exists(company_dir / "evidence" / "evidence_index.json")
    result: dict[str, list[dict[str, Any]]] = {}
    for item in index.get("evidence", []) or []:
        if not isinstance(item, dict):
            continue
        key = item.get("metric_key") or item.get("canonical_name") or item.get("metric_name")
        if key:
            result.setdefault(str(key), []).append(item)
    return result


def clean_source_value(value: Any) -> Any:
    if value in {None, "", "None", "null", "未返回"}:
        return "未返回"
    return value


def build_evidence_package(company_dir: Path, snapshot: dict[str, Any], resolved: dict[str, Any], year: int) -> dict[str, Any]:
    existing = evidence_by_metric(company_dir)
    task_id = resolved["report"].get("task_id")
    lookup = load_provenance_lookup(company_dir, year)
    financial_evidence: dict[str, Any] = {}
    for key, item in sorted(snapshot.get("metrics", {}).items()):
        evidence = list(existing.get(key, []))
        if not evidence:
            for alias, canonical in ALIASES.items():
                if canonical == key:
                    evidence.extend(existing.get(alias, []))
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        source = sources.get(str(year)) or sources.get(f"{year}-12-31") or {}
        if not evidence and source:
            table_index = clean_source_value(source.get("table_index", "未返回"))
            page = source.get("pdf_page_number")
            if is_missing(page):
                page = source.get("pdf_page", "未返回")
            page = clean_source_value(page)
            md_line = clean_source_value(source.get("md_line", source.get("line", "未返回")))
            source_task_id = clean_source_value(source.get("task_id") or task_id or "未返回")
            evidence = [{
                "metric_key": key,
                "metric_name": item.get("display_name") or key,
                "period": str(year),
                "task_id": source_task_id,
                "pdf_page_number": page,
                "pdf_page": source.get("pdf_page"),
                "table_index": table_index,
                "md_line": md_line,
                "source_kind": source.get("file", "metrics"),
                "open_pdf_page_url": (
                    public_api_url(f"/api/pdf_page/{source_task_id}/{page}")
                    if source_task_id != "未返回" and page != "未返回"
                    else None
                ),
                "open_source_page_url": (
                    public_api_url(f"/api/source/{source_task_id}/page/{page}")
                    if source_task_id != "未返回" and page != "未返回"
                    else None
                ),
                "open_source_table_url": (
                    public_api_url(f"/api/source/{source_task_id}/table/{table_index}")
                    if source_task_id != "未返回" and table_index != "未返回"
                    else None
                ),
            }]
        financial_evidence[key] = {
            "evidence": [
                normalize_evidence_record(
                    ev,
                    lookup=lookup,
                    default_task_id=task_id,
                    metric_key=key,
                    period=str(year),
                    url_builder=public_api_url,
                )
                for ev in evidence[:8]
                if isinstance(ev, dict)
            ]
        }
    package = {
        "schema_version": 1,
        "generated_by": "run_analysis_report.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": resolved["company"]["company_id"],
        "report_year": year,
        "financial_evidence": financial_evidence,
    }
    return normalize_evidence_package(
        package,
        snapshot=snapshot,
        lookup=lookup,
        default_task_id=task_id,
        year=year,
        aliases=ALIASES,
        url_builder=public_api_url,
    )


def section_drafts_have_stale_evidence(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    stale_tokens = (
        ":pNone",
        ":pnull",
        ":punknown",
        ":p未返回",
        "/None",
        "/null",
        "/unknown",
        "/未返回",
    )
    return any(token in text for token in stale_tokens)


def status_from_paths(paths: dict[str, Any], keys: list[str]) -> str:
    states = [bool(paths.get(key, {}).get("exists")) for key in keys]
    if all(states):
        return "ready"
    if any(states):
        return "partial"
    return "missing"


def build_preflight(resolved: dict[str, Any], year: int) -> dict[str, Any]:
    paths = resolved.get("paths", {})
    return {
        "template_id": "siq_analysis_report_v1.1",
        "company_status": "ready" if resolved.get("ok") else "missing",
        "artifact_status": status_from_paths(paths, ["report_md", "report_json", "document_full", "artifact_manifest"]),
        "postgres_status": "not_used",
        "rule_semantic_status": status_from_paths(
            paths,
            ["semantic_facts", "semantic_claims", "semantic_relations", "semantic_segments", "semantic_evidence"],
        ),
        "llm_semantic_status": status_from_paths(paths, ["llm_business_profile", "llm_risks", "llm_events", "llm_review_queue"]),
        "evidence_status": status_from_paths(paths, ["evidence_index", "pdf_refs"]),
        "blocking_issues": [],
        "warnings": [],
        "company_id": resolved["company"]["company_id"],
        "stock_code": resolved["company"]["stock_code"],
        "company_short_name": resolved["company"]["company_short_name"],
        "company_full_name": resolved["company"].get("company_full_name"),
        "report_id": resolved["report"].get("report_id"),
        "report_year": resolved["report"].get("report_year") or year,
        "report_type": resolved["report"].get("report_kind") or "annual_report",
        "task_id": resolved["report"].get("task_id"),
        "source_filename": resolved["report"].get("source_filename"),
    }


def build_outline(snapshot: dict[str, Any], preflight: dict[str, Any], year: int) -> dict[str, Any]:
    metrics = snapshot.get("metrics", {})
    revenue_yoy = yoy(metrics, "operating_revenue", year)
    profit = metric_value(metrics, "parent_net_profit", str(year))
    ocf = metric_value(metrics, "operating_cash_flow_net", str(year))
    gross_margin = metric_value(metrics, "gross_margin", str(year))
    if isinstance(profit, (int, float)) and profit < 0:
        profit_phrase = "盈利承压并进入亏损修复观察期"
    else:
        profit_phrase = "盈利仍需结合扣非与现金流验证"
    if isinstance(ocf, (int, float)) and ocf < 0:
        cash_phrase = "经营现金流转负，现金流质量是核心约束"
    else:
        cash_phrase = "经营现金流为正，但仍需验证可持续性"
    return {
        "core_judgment": (
            f"{preflight.get('company_short_name')} {year} 年处于经营质量与财务安全再验证阶段，"
            f"{profit_phrase}，{cash_phrase}。"
        ),
        "core_contradiction": "收入规模、毛利率、扣非利润、经营现金流和债务覆盖之间需要交叉验证。",
        "calculated_derived_metrics": {
            "operating_revenue_yoy_pct": revenue_yoy,
            "gross_margin_pct": gross_margin,
            "operating_cash_flow_yoy_pct": yoy(metrics, "operating_cash_flow_net", year),
            "debt_ratio_pct": metric_value(metrics, "debt_to_asset_ratio", str(year)),
            "free_cash_flow_yi": metric_value(metrics, "free_cash_flow", str(year)),
        },
        "red_flags": [
            "若扣非利润弱于归母利润，需警惕非经常性损益掩盖主营压力。",
            "若经营现金流与利润同向恶化，偿债和转型投入弹性会下降。",
            "若存货或应收增长快于收入，收入质量和资产减值风险需要提高权重。",
        ],
        "yellow_flags": [
            "行业价格竞争、产品结构变化和产能利用率会影响毛利率弹性。",
            "资本开支、研发投入和合作模式会影响后续自由现金流。",
        ],
        "improvement_items": [
            "毛利率改善、扣非亏损收窄、经营现金流恢复为正，是最关键的改善信号。",
        ],
        "deterioration_items": [
            "收入继续下滑、毛利率下探、存货和应收周转恶化，会推翻修复判断。",
        ],
        "observation_items": [
            "持续跟踪收入增速、毛利率、扣非净利润、经营现金流、存货、短债覆盖和治理事项。",
        ],
        "improvement_conditions": [
            "收入变化能够传导至毛利率、费用率和扣非利润改善。",
            "经营现金流能够覆盖资本开支和短期债务滚续。",
        ],
        "falsifying_evidence": [
            "收入增长但现金流转弱、应收和存货同步放大。",
            "短债压力上升且货币资金受限或融资续接能力下降。",
        ],
    }


def report_prefix(resolved: dict[str, Any], year: int) -> Path:
    company_dir = Path(resolved["paths"]["company_dir"]["path"])
    analysis_dir = company_dir / "analysis"
    stock_code = resolved["company"]["stock_code"]
    short_name = resolved["company"]["company_short_name"]
    return analysis_dir / f"{stock_code}-{short_name}-{year}-analysis"


def work_dir_for(prefix: Path) -> Path:
    return prefix.parent / ".work" / prefix.name


def completed_report_state(prefix: Path, work_dir: Path) -> dict[str, Any] | None:
    files = {
        "md": prefix.with_suffix(".md"),
        "json": prefix.with_suffix(".json"),
        "html": prefix.with_suffix(".html"),
    }
    if not all(path.exists() for path in files.values()):
        return None

    validation_path = work_dir / "final_validation.json"
    pipeline_path = work_dir / "pipeline_result.json"
    recovery_path = work_dir / "recovery_result.json"
    validation = load_json_if_exists(validation_path)
    pipeline = load_json_if_exists(pipeline_path)
    recovery = load_json_if_exists(recovery_path)
    validation_ok = bool(validation.get("ok"))
    pipeline_ok = bool(pipeline.get("ok") and pipeline.get("stage") == "completed")
    recovery_ok = bool(recovery.get("ok"))
    if not validation_ok and not pipeline_ok and not recovery_ok:
        return None

    return {
        "files": {key: str(path) for key, path in files.items()},
        "validation": validation if validation else recovery.get("validation") if isinstance(recovery, dict) else {},
        "artifacts": {
            "final_validation": str(validation_path) if validation_path.exists() else None,
            "pipeline_result": str(pipeline_path) if pipeline_path.exists() else None,
            "recovery_result": str(recovery_path) if recovery_path.exists() else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="股票代码、company_id、公司简称或别名")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output-prefix", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--reuse-checkpoint", action="store_true", help="存在阶段产物时不重建 preflight/evidence/metrics/outline")
    parser.add_argument("--prepare-only", action="store_true", help="只写入定位、预检、证据包、指标快照和分析主线，不渲染最终报告")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--use-research-packs",
        action="store_true",
        help="启用 siq_analysis 内部子智能体 research_packs 生成、校验和 section_drafts 合并。",
    )
    parser.add_argument(
        "--research-subagent-mode",
        choices=["deterministic", "external", "hybrid", "prompt-only"],
        default="deterministic",
        help="research pack 执行模式；默认 deterministic 保持旧行为，external/hybrid 接收真实子智能体 pack。",
    )
    parser.add_argument(
        "--research-subagent-pack-dir",
        type=Path,
        help="external/hybrid 模式下读取真实子智能体产出的 research_pack JSON 目录。",
    )
    parser.add_argument(
        "--no-research-subagent-fallback",
        action="store_true",
        help="external/hybrid 模式下不使用确定性 fallback 填补缺失 pack。",
    )
    parser.add_argument(
        "--research-subagent-prompt",
        default="",
        help="传给 research subagents 的任务提示词；子智能体可据此决定是否检索额外标杆或外部来源。",
    )
    parser.add_argument(
        "--research-subagent-prompt-file",
        type=Path,
        help="从文件读取额外任务提示词并传给 research subagents。",
    )
    parser.add_argument(
        "--research-benchmark-hint",
        action="append",
        default=[],
        help="可重复的标杆提示；仅作为提示词上下文，不进入硬编码同业样本。",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="允许覆盖已有最终报告；覆盖前会自动备份。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使已有通过验收的最终报告，也强制重建流水线产物。",
    )
    parser.add_argument("--write-json", type=Path)
    args = parser.parse_args()

    started_at = datetime.now().isoformat(timespec="seconds")
    resolve = run_json([sys.executable, str(RESOLVE_SCRIPT), "--company", args.company, "--year", str(args.year)])
    result: dict[str, Any] = {
        "ok": False,
        "stage": "resolve",
        "started_at": started_at,
        "company_query": args.company,
        "year": args.year,
        "steps": {"resolve": resolve},
    }
    if not resolve["ok"] or not isinstance(resolve.get("json"), dict) or not resolve["json"].get("ok"):
        result["stage"] = "resolve_failed"
        result["next_action"] = "使用 resolve_company.py 查看候选公司，必须从 company_catalog.json 唯一定位。"
        if args.write_json:
            dump_json(args.write_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    resolved = resolve["json"]
    prefix = args.output_prefix or report_prefix(resolved, args.year)
    work_dir = args.work_dir or work_dir_for(prefix)
    result["output_prefix"] = str(prefix)
    result["work_dir"] = str(work_dir)
    result["files"] = {
        "md": str(prefix.with_suffix(".md")),
        "json": str(prefix.with_suffix(".json")),
        "html": str(prefix.with_suffix(".html")),
    }

    completed = completed_report_state(prefix, work_dir)
    requested_subagent_execution = args.use_research_packs and (
        args.research_subagent_mode != "deterministic"
        or args.research_subagent_pack_dir is not None
        or args.no_research_subagent_fallback
    )
    if completed and not args.force and not args.validate_only and not requested_subagent_execution:
        result["ok"] = True
        result["stage"] = "already_completed"
        result["files"] = completed["files"]
        result["validation"] = completed.get("validation")
        result["artifacts"] = completed.get("artifacts")
        result["next_action"] = "报告已通过验收，默认不重复生成；如需覆盖重建，请显式传 --force。"
        if args.write_json:
            dump_json(args.write_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.validate_only:
        validate = run_json([sys.executable, str(VALIDATE_SCRIPT), "--prefix", str(prefix), "--write-json", str(work_dir / "final_validation.json")])
        result["steps"]["validate"] = validate
        payload = validate.get("json") if isinstance(validate.get("json"), dict) else {}
        result["validation"] = payload
        result["ok"] = bool(validate["ok"] and payload.get("ok"))
        result["stage"] = "completed" if result["ok"] else "validation_failed"
        if args.write_json:
            dump_json(args.write_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2

    work_dir.mkdir(parents=True, exist_ok=True)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    company_dir = Path(resolved["paths"]["company_dir"]["path"])

    preflight_path = work_dir / "preflight.json"
    wiki_inventory_path = work_dir / "wiki_inventory.json"
    snapshot_path = work_dir / "metric_snapshot.json"
    evidence_path = work_dir / "evidence_package.json"
    outline_path = work_dir / "analysis_outline.json"
    peer_metrics_path = work_dir / "peer_metrics.json"
    qualitative_snapshot_path = work_dir / "qualitative_snapshot.json"
    market_snapshot_path = work_dir / "market_snapshot.json"
    industry_research_path = work_dir / "industry_research.json"
    research_packs_dir = work_dir / "research_packs"
    research_pack_manifest_path = work_dir / "research_pack_manifest.json"
    research_pack_validation_path = work_dir / "research_pack_validation.json"
    research_pack_merge_manifest_path = work_dir / "research_pack_merge_manifest.json"
    research_subagent_run_manifest_path = work_dir / "research_subagent_run_manifest.json"
    research_subagent_prompt_bundle_path = work_dir / "research_subagent_prompts.json"

    if not args.reuse_checkpoint or not preflight_path.exists():
        dump_json(preflight_path, build_preflight(resolved, args.year))
    if not args.reuse_checkpoint or not wiki_inventory_path.exists():
        dump_json(wiki_inventory_path, build_wiki_inventory(company_dir, args.year))
    if not args.reuse_checkpoint or not snapshot_path.exists():
        snapshot = build_metric_snapshot(company_dir, resolved, args.year)
        dump_json(snapshot_path, snapshot)
    else:
        snapshot = load_json(snapshot_path)
    if not args.reuse_checkpoint or not evidence_path.exists():
        evidence_package = build_evidence_package(company_dir, snapshot, resolved, args.year)
    else:
        evidence_package = load_json(evidence_path)
    normalized_evidence = normalize_evidence_package(
        evidence_package,
        snapshot=snapshot,
        lookup=load_provenance_lookup(company_dir, args.year),
        default_task_id=resolved["report"].get("task_id"),
        year=args.year,
        aliases=ALIASES,
        url_builder=public_api_url,
    )
    evidence_changed = normalized_evidence != evidence_package
    if evidence_changed or not evidence_path.exists():
        dump_json(evidence_path, normalized_evidence)
    evidence_package = normalized_evidence
    if not args.reuse_checkpoint or not outline_path.exists():
        dump_json(outline_path, build_outline(snapshot, load_json(preflight_path), args.year))
    if not args.reuse_checkpoint or not peer_metrics_path.exists():
        peer = run_json([
            sys.executable,
            str(PEER_METRICS_SCRIPT),
            "--company-dir",
            str(company_dir),
            "--year",
            str(args.year),
            "--output",
            str(peer_metrics_path),
        ])
        result["steps"]["peer_metrics"] = peer
    if not args.reuse_checkpoint or not qualitative_snapshot_path.exists():
        qualitative = run_json([
            sys.executable,
            str(QUALITATIVE_EVIDENCE_SCRIPT),
            "--company-dir",
            str(company_dir),
            "--report-id",
            str(resolved["report"].get("report_id") or f"{args.year}-annual"),
            "--year",
            str(args.year),
            "--output",
            str(qualitative_snapshot_path),
        ])
        result["steps"]["qualitative_snapshot"] = qualitative
    if not args.reuse_checkpoint or not market_snapshot_path.exists():
        market = run_json([
            sys.executable,
            str(MARKET_SNAPSHOT_SCRIPT),
            "--company-dir",
            str(company_dir),
            "--work-dir",
            str(work_dir),
            "--year",
            str(args.year),
            "--output",
            str(market_snapshot_path),
        ])
        result["steps"]["market_snapshot"] = market
    if not args.reuse_checkpoint or not industry_research_path.exists():
        industry_research = run_json([
            sys.executable,
            str(INDUSTRY_RESEARCH_SCRIPT),
            "--company-dir",
            str(company_dir),
            "--year",
            str(args.year),
            "--output",
            str(industry_research_path),
        ])
        result["steps"]["industry_research"] = industry_research

    checkpoint_summary = {
        "preflight": str(preflight_path),
        "wiki_inventory": str(wiki_inventory_path),
        "metric_snapshot": str(snapshot_path),
        "evidence_package": str(evidence_path),
        "analysis_outline": str(outline_path),
        "peer_metrics": str(peer_metrics_path),
        "qualitative_snapshot": str(qualitative_snapshot_path),
        "market_snapshot": str(market_snapshot_path),
        "industry_research": str(industry_research_path),
        "research_packs": str(research_packs_dir),
        "research_pack_manifest": str(research_pack_manifest_path),
        "research_pack_validation": str(research_pack_validation_path),
        "research_pack_merge_manifest": str(research_pack_merge_manifest_path),
        "research_subagent_run_manifest": str(research_subagent_run_manifest_path),
        "research_subagent_prompt_bundle": str(research_subagent_prompt_bundle_path),
        "section_drafts": str(work_dir / "section_drafts.json"),
        "quality_report": str(work_dir / "quality_report.json"),
    }
    result["checkpoints"] = checkpoint_summary

    if args.use_research_packs:
        effective_research_subagent_mode = args.research_subagent_mode
        if args.research_subagent_pack_dir and effective_research_subagent_mode == "deterministic":
            effective_research_subagent_mode = "hybrid"
        result["research_subagent_mode"] = effective_research_subagent_mode
        should_build_packs = (
            effective_research_subagent_mode != "deterministic"
            or not args.reuse_checkpoint
            or not research_pack_manifest_path.exists()
            or args.research_subagent_pack_dir is not None
            or args.no_research_subagent_fallback
        )
        if should_build_packs:
            research_pack_cmd = [
                sys.executable,
                str(RUN_RESEARCH_SUBAGENTS_SCRIPT),
                "--work-dir",
                str(work_dir),
                "--year",
                str(args.year),
                "--mode",
                effective_research_subagent_mode,
                "--output-dir",
                str(research_packs_dir),
                "--write-manifest",
                str(research_pack_manifest_path),
                "--write-run-manifest",
                str(research_subagent_run_manifest_path),
                "--prompt-bundle",
                str(research_subagent_prompt_bundle_path),
            ]
            if args.research_subagent_pack_dir:
                research_pack_cmd.extend(["--external-pack-dir", str(args.research_subagent_pack_dir)])
            if args.no_research_subagent_fallback:
                research_pack_cmd.append("--no-fallback")
            if args.research_subagent_prompt:
                research_pack_cmd.extend(["--research-prompt", args.research_subagent_prompt])
            if args.research_subagent_prompt_file:
                research_pack_cmd.extend(["--research-prompt-file", str(args.research_subagent_prompt_file)])
            for benchmark_hint in args.research_benchmark_hint:
                research_pack_cmd.extend(["--benchmark-hint", benchmark_hint])
            research_packs = run_json(research_pack_cmd)
            result["steps"]["run_research_subagents"] = research_packs
            payload = research_packs.get("json") if isinstance(research_packs.get("json"), dict) else {}
            if effective_research_subagent_mode == "prompt-only":
                result["ok"] = bool(research_packs["ok"] and payload.get("ok"))
                result["stage"] = str(payload.get("stage") or "prompt_bundle_ready")
                result["research_subagent_run"] = payload
                result["next_action"] = payload.get("next_action") or "将真实子智能体 pack 写入 research_packs 后用 external 或 hybrid 模式继续。"
                if args.write_json:
                    dump_json(args.write_json, result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 2
            if not research_packs["ok"] or not payload.get("ok"):
                result["ok"] = False
                result["stage"] = "research_subagents_failed"
                result["research_subagent_run"] = payload
                result["next_action"] = "检查 research_subagent_run_manifest.json，先保证五个核心子智能体 pack 都可写入并通过校验。"
                if args.write_json:
                    dump_json(args.write_json, result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        else:
            result["steps"]["run_research_subagents"] = {
                "ok": True,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "json": {
                    "ok": True,
                    "stage": "reused_existing",
                    "mode": effective_research_subagent_mode,
                    "manifest": str(research_pack_manifest_path),
                },
                "cmd": ["reuse", str(research_pack_manifest_path)],
            }

        validate_packs = run_json([
            sys.executable,
            str(VALIDATE_RESEARCH_PACKS_SCRIPT),
            str(work_dir),
        ])
        result["steps"]["validate_research_packs"] = validate_packs
        payload = validate_packs.get("json") if isinstance(validate_packs.get("json"), dict) else {}
        if payload:
            dump_json(research_pack_validation_path, payload)
        if not validate_packs["ok"] or not payload.get("ok"):
            result["ok"] = False
            result["stage"] = "research_pack_validation_failed"
            result["research_pack_validation"] = payload
            result["next_action"] = "检查 research_pack_validation.json；缺失 pack、外部来源缺口或 prohibited_content_hits 必须先处理。"
            if args.write_json:
                dump_json(args.write_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

    if args.prepare_only:
        result["ok"] = True
        result["stage"] = "prepared"
        result["next_action"] = (
            "读取 preflight/metric_snapshot/evidence_package/analysis_outline，"
            "生成高质量 section_drafts.json 后再用 --reuse-checkpoint 执行最终渲染、溯源修复和质量验收。"
        )
        if args.write_json:
            dump_json(args.write_json, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    draft_path = work_dir / "section_drafts.json"
    draft_validation_path = work_dir / "section_drafts_validation.json"
    refresh_stale_drafts = args.reuse_checkpoint and section_drafts_have_stale_evidence(draft_path)
    if not args.reuse_checkpoint or not draft_path.exists() or refresh_stale_drafts:
        draft = run_json([
            sys.executable,
            str(GENERATE_DRAFTS_SCRIPT),
            "--work-dir",
            str(work_dir),
            "--year",
            str(args.year),
            "--output",
            str(draft_path),
            "--write-validation",
            str(draft_validation_path),
        ])
        result["steps"]["generate_section_drafts"] = draft
        if refresh_stale_drafts:
            result["steps"]["generate_section_drafts"]["json"] = {
                **(result["steps"]["generate_section_drafts"].get("json") or {}),
                "refreshed_stale_evidence_ids": True,
            }
        draft_payload = draft.get("json") if isinstance(draft.get("json"), dict) else {}
        if not draft["ok"] or not draft_payload.get("ok"):
            result["ok"] = False
            result["stage"] = "section_drafts_failed"
            result["draft_validation"] = draft_payload.get("validation") if isinstance(draft_payload, dict) else None
            result["next_action"] = "检查 section_drafts_validation.json，先修复草稿 schema、章节字段或证据缺口。"
            if args.write_json:
                dump_json(args.write_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
    else:
        result["steps"]["generate_section_drafts"] = {
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "json": {"ok": True, "stage": "reused_existing", "output": str(draft_path)},
            "cmd": ["reuse", str(draft_path)],
        }

    if args.use_research_packs:
        merge_packs = run_json([
            sys.executable,
            str(MERGE_RESEARCH_PACKS_SCRIPT),
            "--work-dir",
            str(work_dir),
            "--year",
            str(args.year),
            "--section-drafts",
            str(draft_path),
            "--output",
            str(draft_path),
            "--write-manifest",
            str(research_pack_merge_manifest_path),
        ])
        result["steps"]["merge_research_packs"] = merge_packs
        payload = merge_packs.get("json") if isinstance(merge_packs.get("json"), dict) else {}
        if not merge_packs["ok"] or not payload.get("ok"):
            result["ok"] = False
            result["stage"] = "research_pack_merge_failed"
            result["next_action"] = "检查 research_packs 和 section_drafts.json 的 section_id 是否匹配。"
            if args.write_json:
                dump_json(args.write_json, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

    recovery_json = work_dir / "recovery_result.json"
    recover_cmd = [
        sys.executable,
        str(RECOVER_SCRIPT),
        "--work-dir",
        str(work_dir),
        "--output-prefix",
        str(prefix),
        "--write-json",
        str(recovery_json),
    ]
    if args.allow_overwrite:
        recover_cmd.append("--allow-overwrite")
    recover = run_json(recover_cmd)
    result["steps"]["recover_render_repair_validate"] = recover
    payload = recover.get("json") if isinstance(recover.get("json"), dict) else {}
    result["recovery"] = payload
    result["validation"] = payload.get("validation") if isinstance(payload, dict) else None
    result["ok"] = bool(recover["ok"] and payload.get("ok"))
    result["stage"] = "completed" if result["ok"] else payload.get("stage", "pipeline_failed")
    result["next_action"] = payload.get("next_action") if isinstance(payload, dict) else "查看 recover step stdout/stderr。"

    # Refresh the company-level index so the frontend / cross-product tooling
    # always sees the freshest pointer set. Best-effort: failures here do not
    # invalidate the analysis pipeline result.
    try:
        index_script = Path("/home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/update_company_index.py")
        if index_script.exists():
            run_json([sys.executable, str(index_script), "--company-dir", str(company_dir)])
    except Exception as exc:  # pragma: no cover - non-critical
        result.setdefault("warnings", []).append(f"company_index_update_failed:{exc}")

    if args.write_json:
        dump_json(args.write_json, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
