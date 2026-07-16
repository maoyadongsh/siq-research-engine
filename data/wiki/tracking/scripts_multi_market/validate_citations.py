#!/usr/bin/env python3
"""Validate citation coverage for finsight_tracking outputs."""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_SCRIPT_PATH = Path(__file__).resolve()
_PROJECT_ROOT = _SCRIPT_PATH.parents[4]
DEFAULT_WIKI_BASE = str(Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or _SCRIPT_PATH.parents[2]
).expanduser().resolve())
WIKISET_DIR = Path(
    os.environ.get("SIQ_WIKISET_ROOT")
    or os.environ.get("WIKISET_ROOT")
    or _PROJECT_ROOT / "scripts" / "wiki" / "wikiset"
).expanduser().resolve()
if str(WIKISET_DIR) not in sys.path:
    sys.path.insert(0, str(WIKISET_DIR))

from company_identity import company_dir_path


REQUIRES_LOCAL_EVIDENCE = {"wiki_metrics", "wiki_analysis", "wiki_evidence", "wiki_semantic"}


def _extract_fenced_block(content: str, fence: str) -> str:
    start = content.find(f"```{fence}")
    if start < 0:
        return ""
    end = content.find("```", start + len(fence) + 3)
    if end < 0:
        return ""
    return content[start + len(fence) + 3:end].strip()


def _has_evidence_locator(ref: dict[str, Any]) -> bool:
    return bool(
        ref.get("pdf_page")
        or ref.get("pdf_page_number")
        or ref.get("open_pdf_page_url")
        or ref.get("open_source_page_url")
        or ref.get("open_source_table_url")
        or ref.get("source_url")
        or ref.get("html_anchor")
        or ref.get("xpath")
        or ref.get("xbrl_fact_id")
        or ref.get("fact_id")
        or ref.get("section_id")
        or ref.get("table_id")
    )


def _validate_tracking_items(tracking_dir: Path) -> list[dict[str, str]]:
    issues = []
    path = tracking_dir / "tracking-items.md"
    if not path.exists():
        issues.append({"file": str(path), "issue": "tracking-items.md 不存在"})
        return issues

    content = path.read_text(encoding="utf-8")
    yaml_block = _extract_fenced_block(content, "yaml")
    if not yaml_block:
        issues.append({"file": str(path), "issue": "缺少 YAML 结构化数据"})
        return issues

    try:
        parsed = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        issues.append({"file": str(path), "issue": f"YAML 解析失败: {exc}"})
        return issues

    for item in parsed.get("items", []):
        source_type = item.get("source_type")
        if source_type not in REQUIRES_LOCAL_EVIDENCE:
            continue
        refs = item.get("source_refs") or []
        if not refs:
            issues.append({"file": str(path), "item": item.get("id", ""), "issue": "本地证据来源缺少 source_refs"})
            continue
        if not any(_has_evidence_locator(ref) for ref in refs if isinstance(ref, dict)):
            issues.append({"file": str(path), "item": item.get("id", ""), "issue": "source_refs 缺少可回溯证据定位"})
    return issues


def _validate_latest_metrics(tracking_dir: Path) -> list[dict[str, str]]:
    issues = []
    metrics_dir = tracking_dir / "metrics"
    if not metrics_dir.exists():
        return issues
    files = sorted(metrics_dir.glob("*.md"), reverse=True)
    if not files:
        return issues
    path = files[0]
    content = path.read_text(encoding="utf-8")
    json_block = _extract_fenced_block(content, "json")
    if not json_block:
        issues.append({"file": str(path), "issue": "指标面板缺少 JSON 结构化数据"})
        return issues
    try:
        metrics = json.loads(json_block)
    except json.JSONDecodeError as exc:
        issues.append({"file": str(path), "issue": f"指标 JSON 解析失败: {exc}"})
        return issues
    for metric in metrics:
        refs = metric.get("source_refs") or []
        if not refs:
            issues.append({"file": str(path), "metric": metric.get("canonical_name", ""), "issue": "指标缺少 source_refs"})
            continue
        if not any(_has_evidence_locator(ref) for ref in refs if isinstance(ref, dict)):
            issues.append({"file": str(path), "metric": metric.get("canonical_name", ""), "issue": "指标 source_refs 缺少可回溯证据定位"})
    return issues


def _validate_alerts(tracking_dir: Path) -> list[dict[str, str]]:
    """Validate that alert records carry citation fields when they reference metrics."""
    issues: list[dict[str, str]] = []
    alerts_dir = tracking_dir / "alerts"
    if not alerts_dir.exists():
        return issues
    latest_alerts = sorted(alerts_dir.glob("*.md"), reverse=True)[:1]
    for path in latest_alerts:
        content = path.read_text(encoding="utf-8")
        # Alerts can ship a structured json block; if absent, fall back to
        # heuristic checks for citation fields in the prose body.
        json_block = _extract_fenced_block(content, "json")
        if json_block:
            try:
                payload = json.loads(json_block)
            except json.JSONDecodeError as exc:
                issues.append({"file": str(path), "issue": f"alerts JSON 解析失败: {exc}"})
                continue
            alerts = payload if isinstance(payload, list) else payload.get("alerts") or []
            for entry in alerts:
                if not isinstance(entry, dict):
                    continue
                # Alerts triggered by metric thresholds must reference the metric/source.
                category = str(entry.get("category") or "")
                if category in {"异常指标", "财务承诺", "异常变动", "metric"}:
                    refs = entry.get("source_refs") or entry.get("evidence_refs") or []
                    if not refs:
                        issues.append({
                            "file": str(path),
                            "alert_id": entry.get("id", ""),
                            "issue": "metric-class alert 缺少 source_refs/evidence_refs",
                        })
                        continue
                    if not any(_has_evidence_locator(ref) for ref in refs if isinstance(ref, dict)):
                        issues.append({
                            "file": str(path),
                            "alert_id": entry.get("id", ""),
                            "issue": "alert source_refs 缺少可回溯证据定位",
                        })
        else:
            # No json block: only flag when the alert prose claims a metric value
            # but no /api/source or /api/pdf_page link is present anywhere.
            if re.search(r"(同比|环比|本期|上期).{0,40}([-+]?\d+(\.\d+)?\s*(亿元|万元|%|倍|个百分点|[A-Z]{3}(?:\s+(?:million|billion))?))", content):
                if "/api/pdf_page/" not in content and "/api/source/" not in content and "task_id" not in content:
                    issues.append({
                        "file": str(path),
                        "issue": "alert 含数值描述但未提供任何来源链接（task_id/pdf_page/source）",
                    })
    return issues


def _validate_updates(tracking_dir: Path) -> list[dict[str, str]]:
    """Validate that update records preserve evidence pointers."""
    issues: list[dict[str, str]] = []
    updates_dir = tracking_dir / "updates"
    if not updates_dir.exists():
        return issues
    for path in sorted(updates_dir.glob("*.md")):
        if path.parent.name == "archive":
            continue
        content = path.read_text(encoding="utf-8")
        # Update records are short, but if they cite a financial number they
        # must point back to the metrics file or analysis.
        if re.search(r"[-+]?\d+(\.\d+)?\s*(亿元|万元|%|倍|[A-Z]{3}(?:\s+(?:million|billion))?)", content):
            if not re.search(r"\.\.\/(metrics|tracking-items|analysis)|task_id|/api/(pdf_page|source)/", content):
                issues.append({
                    "file": str(path),
                    "issue": "update 含数值但未链接到 metrics/tracking-items/analysis 或来源",
                })
    return issues


def validate_citations(stock_code: str, company_name: str, wiki_base: str = DEFAULT_WIKI_BASE) -> dict[str, Any]:
    company_dir = company_dir_path(wiki_base, stock_code, company_name)
    tracking_dir = company_dir / "tracking"
    issues = []
    if not tracking_dir.exists():
        issues.append({"file": str(tracking_dir), "issue": "tracking 目录不存在"})
    else:
        issues.extend(_validate_tracking_items(tracking_dir))
        issues.extend(_validate_latest_metrics(tracking_dir))
        issues.extend(_validate_alerts(tracking_dir))
        issues.extend(_validate_updates(tracking_dir))

    return {
        "stock_code": stock_code,
        "company_name": company_name,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "passed": not issues,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 finsight_tracking 产物证据链")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = validate_citations(args.stock, args.company, args.wiki_base)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "通过" if result["passed"] else "失败"
        print(f"证据链校验: {status}")
        for issue in result["issues"]:
            print(f"- {issue}")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
