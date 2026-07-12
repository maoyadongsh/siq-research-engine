#!/usr/bin/env python3
"""Repair existing wiki paths and identity fields to the naming contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from company_identity import (  # noqa: E402
    canonicalize_company_json,
    clean_filename,
    parse_download_filename_identity,
    report_source_metadata,
)

SKIP_JSON_FILES = {"document_full.json"}
ID_KEYS = {"company_id", "company_dir"}
SHORT_KEYS = {"company_short_name", "stock_name"}
FULL_KEYS = {"company_full_name"}
PATH_KEYS = {"company_path", "output_dir", "report_md", "report_json", "document_full", "artifact_manifest"}
PROVENANCE_KEYS = {
    "filename",
    "source_filename",
    "source_filename_metadata",
    "filename_pattern",
    "result_file",
    "raw_request_sha256",
    "raw_response_sha256",
    "response_content_sha256",
}
SEMANTIC_NAME_KEYS = {
    "name",
    "value",
    "subject",
    "source_entity_name",
    "target_entity_name",
    "company",
}

A_SHARE_STOCK_CODE_RE = re.compile(
    r"^(?:000|001|002|003|300|301|600|601|603|605|688|689|8\d{5}|4\d{5})$"
)
NON_A_SHARE_DIR_RE = re.compile(r"^(?:HK|KR|JP|US|EU)[A-Za-z0-9]", re.IGNORECASE)
NON_A_SHARE_MARKETS = {"HK", "KR", "JP", "US", "EU"}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    old_text = path.read_text("utf-8") if path.exists() else ""
    if old_text == text:
        return False
    path.write_text(text, "utf-8")
    return True


def write_text(path: Path, text: str) -> bool:
    old_text = path.read_text("utf-8") if path.exists() else ""
    if old_text == text:
        return False
    path.write_text(text, "utf-8")
    return True


def dedupe(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def report_kind_label(report_kind: str) -> str:
    return {
        "annual_report": "年报",
        "annual_report_summary": "年报摘要",
        "interim_report": "半年报",
        "interim_report_summary": "半年报摘要",
    }.get(report_kind, report_kind or "报告")


def build_company_md(company: dict[str, Any]) -> str:
    reports = company.get("reports") if isinstance(company.get("reports"), list) else []
    primary = company.get("primary_report_id") or (reports[0].get("report_id") if reports and isinstance(reports[0], dict) else "")
    lines = [
        f"# {company.get('company_short_name')}（{company.get('stock_code')}）",
        "",
        f"- 公司全称：{company.get('company_full_name')}",
        f"- 证券代码：{company.get('stock_code')}",
        f"- 交易所：{company.get('exchange')}",
        f"- 主报告：{primary}",
        "",
        "## 可用报告",
        "",
    ]
    for report in reports:
        if not isinstance(report, dict):
            continue
        label = report_kind_label(str(report.get("report_kind") or ""))
        lines.append(f"- {report.get('report_year')} {label}：[{report.get('report_id')}](reports/{report.get('report_id')}/report.md)")
    lines.extend(
        [
            "",
            "## 指标入口",
            "",
            "- [三大表指标](metrics/three_statements.json)",
            "- [关键指标](metrics/key_metrics.json)",
            "- [校验结果](metrics/validation.json)",
            "",
            "## 证据链入口",
            "",
            "- [证据索引](evidence/evidence_index.json)",
            "- [PDF 引用](evidence/pdf_refs.json)",
            "- [图片证据](evidence/image_manifest.json)",
            "",
            "## 分析入口",
            "",
            "- [分析目录](analysis/README.md)",
            "",
        ]
    )
    return "\n".join(lines)


def build_analysis_readme(company: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# {company.get('company_short_name')} 分析工作区",
            "",
            "本目录用于沉淀围绕单个上市公司的多维分析结论。",
            "",
            "建议维度：",
            "",
            "- financial.md：财务质量与三大表分析",
            "- operations.md：经营业务与增长驱动",
            "- governance.md：治理结构与股东情况",
            "- risk.md：风险因素与审计关注",
            "- valuation.md：估值与市场定价",
            "- strategy.md：战略、资本开支与长期竞争力",
            "",
            "所有重要判断必须引用 `../evidence/evidence_index.json` 中的证据对象，或引用年报 PDF 页码和表格索引。",
            "",
        ]
    )


def is_a_share_company_dir(company_dir: Path, company: dict[str, Any]) -> bool:
    """This repair script owns only the legacy A-share company namespace."""
    if NON_A_SHARE_DIR_RE.match(company_dir.name):
        return False
    market = str(
        company.get("market")
        or company.get("source_market")
        or company.get("listing_market")
        or ""
    ).strip().upper()
    if market in NON_A_SHARE_MARKETS:
        return False
    if company.get("identity_route") == "generic_non_a_share_wiki_import":
        return False
    stock_code = str(company.get("stock_code") or "").strip()
    if stock_code:
        return bool(A_SHARE_STOCK_CODE_RE.match(stock_code))
    dir_code = company_dir.name.split("-", 1)[0]
    return bool(A_SHARE_STOCK_CODE_RE.match(dir_code))


def enrich_report_metadata(report: dict[str, Any]) -> None:
    source_filename = report.get("source_filename")
    metadata = report_source_metadata(source_filename)
    if metadata:
        report["source_filename_metadata"] = metadata


def transform_string(value: str, replacements: list[dict[str, str]], key: str | None) -> str:
    if key in PROVENANCE_KEYS:
        return value
    updated = value
    for item in replacements:
        old_id = item["old_id"]
        new_id = item["new_id"]
        old_short = item["old_short"]
        new_short = item["new_short"]
        old_full = item["old_full"]
        new_full = item["new_full"]

        if old_id and key in ID_KEYS and updated == old_id:
            updated = new_id
        elif old_id and updated == old_id:
            updated = new_id

        if old_short and key in SHORT_KEYS and updated == old_short:
            updated = new_short
        if key in FULL_KEYS and updated in {value for value in (old_id, old_short, old_full) if value}:
            updated = new_full
        if key in SEMANTIC_NAME_KEYS:
            if old_id:
                updated = updated.replace(old_id, new_short)
            if old_short:
                updated = updated.replace(old_short, new_short)
            if old_full:
                updated = updated.replace(old_full, new_full)
        if key in PATH_KEYS or "companies/" in updated or "/companies/" in updated:
            updated = updated.replace(f"companies/{old_id}", f"companies/{new_id}")
            updated = updated.replace(f"/companies/{old_id}", f"/companies/{new_id}")
    return updated


def transform_json(value: Any, replacements: list[dict[str, str]], key: str | None = None) -> Any:
    if isinstance(value, dict):
        updated = {k: transform_json(v, replacements, k) for k, v in value.items()}
        if isinstance(updated.get("aliases"), list):
            aliases = []
            for alias in updated["aliases"]:
                text = str(alias or "").strip()
                for item in replacements:
                    if text in {item["old_id"], item["old_short"], item["old_full"]}:
                        text = item["new_short"] if text != item["old_full"] else item["new_full"]
                aliases.append(text)
            for item in replacements:
                if updated.get("stock_code") == item["stock_code"]:
                    aliases.extend([item["new_short"], item["new_full"]])
            updated["aliases"] = dedupe([alias for alias in aliases if alias])
        if isinstance(updated.get("reports"), list):
            for report in updated["reports"]:
                if isinstance(report, dict):
                    enrich_report_metadata(report)
        if isinstance(updated.get("report"), dict):
            enrich_report_metadata(updated["report"])
        if isinstance(updated.get("source"), dict) and isinstance(updated.get("report"), dict):
            metadata = updated["report"].get("source_filename_metadata")
            if metadata:
                updated["source"]["source_filename_metadata"] = metadata
        return updated
    if isinstance(value, list):
        return [transform_json(item, replacements, key) for item in value]
    if isinstance(value, str):
        return transform_string(value, replacements, key)
    return value


def add_replacement(
    replacements: list[dict[str, str]],
    *,
    stock_code: str,
    old_id: str,
    old_short: str,
    old_full: str,
    new_id: str,
    new_short: str,
    new_full: str,
) -> None:
    old_values = {value for value in (old_id, old_short, old_full) if value}
    if not old_values:
        return
    if old_values <= {new_id, new_short, new_full}:
        return
    replacement = {
        "stock_code": stock_code,
        "old_id": old_id,
        "new_id": new_id,
        "old_short": old_short,
        "new_short": new_short,
        "old_full": old_full,
        "new_full": new_full,
    }
    key = tuple(replacement.items())
    if key not in {tuple(item.items()) for item in replacements}:
        replacements.append(replacement)


def build_replacements(
    company_dir: Path,
    company: dict[str, Any],
    updated_company: dict[str, Any],
) -> list[dict[str, str]]:
    stock_code = str(updated_company.get("stock_code") or company.get("stock_code") or "")
    new_id = str(updated_company.get("company_id") or "")
    new_short = str(updated_company.get("company_short_name") or "")
    new_full = str(updated_company.get("company_full_name") or new_short)
    replacements: list[dict[str, str]] = []

    add_replacement(
        replacements,
        stock_code=stock_code,
        old_id=str(company.get("company_id") or company_dir.name),
        old_short=str(company.get("company_short_name") or ""),
        old_full=str(company.get("company_full_name") or ""),
        new_id=new_id,
        new_short=new_short,
        new_full=new_full,
    )
    if company_dir.name != new_id:
        old_short = company_dir.name.split("-", 1)[1] if "-" in company_dir.name else company_dir.name
        add_replacement(
            replacements,
            stock_code=stock_code,
            old_id=company_dir.name,
            old_short=old_short,
            old_full=old_short,
            new_id=new_id,
            new_short=new_short,
            new_full=new_full,
        )

    reports = company.get("reports") if isinstance(company.get("reports"), list) else []
    for report in reports:
        if not isinstance(report, dict):
            continue
        source_filename = report.get("source_filename")
        parsed = parse_download_filename_identity(source_filename)
        if parsed and parsed.get("stock_code") and parsed.get("stock_code") != stock_code:
            continue
        stem = clean_filename(source_filename)
        if not stem or stem == new_short:
            continue
        add_replacement(
            replacements,
            stock_code=stock_code,
            old_id=f"{stock_code}-{stem}" if stock_code else stem,
            old_short=stem,
            old_full=stem,
            new_id=new_id,
            new_short=new_short,
            new_full=new_full,
        )
    return replacements


def repair_company_dir(company_dir: Path) -> tuple[Path, list[dict[str, str]]]:
    company_path = company_dir / "company.json"
    company = read_json(company_path, {})
    if not isinstance(company, dict) or not company:
        return company_dir, []
    if not is_a_share_company_dir(company_dir, company):
        return company_dir, []

    updated_company, _ = canonicalize_company_json(company)
    new_id = str(updated_company.get("company_id") or "")
    replacements = build_replacements(company_dir, company, updated_company)

    target_dir = company_dir.parent / new_id
    changed = False
    if company_dir.name != new_id:
        if target_dir.exists():
            raise RuntimeError(f"target directory already exists: {target_dir}")
        company_dir.rename(target_dir)
        company_dir = target_dir
        changed = True

    company = transform_json(updated_company, replacements)
    changed = write_json(company_dir / "company.json", company) or changed
    changed = write_text(company_dir / "company.md", build_company_md(company)) or changed
    analysis_dir = company_dir / "analysis"
    if analysis_dir.exists():
        changed = write_text(analysis_dir / "README.md", build_analysis_readme(company)) or changed

    for path in sorted(company_dir.rglob("*.json")):
        if path.name in SKIP_JSON_FILES or path.name == "company.json":
            continue
        payload = read_json(path, None)
        if payload is None:
            continue
        updated_payload = transform_json(payload, replacements)
        changed = write_json(path, updated_payload) or changed

    for path in sorted(company_dir.rglob("*.md")):
        if "reports" in path.relative_to(company_dir).parts or path.name in {"company.md", "README.md"}:
            continue
        text = path.read_text("utf-8", errors="ignore")
        old_text = text
        for replacement in replacements:
            if replacement["old_id"]:
                text = text.replace(replacement["old_id"], replacement["new_id"])
            if replacement["old_short"]:
                text = text.replace(replacement["old_short"], replacement["new_short"])
        if text != old_text:
            path.write_text(text, "utf-8")
            changed = True

    return company_dir, replacements if changed else []


def repair_meta(wiki_root: Path, replacements: list[dict[str, str]]) -> None:
    meta_dir = wiki_root / "_meta"
    for path in sorted(meta_dir.rglob("*.json")):
        payload = read_json(path, None)
        if payload is None:
            continue
        write_json(path, transform_json(payload, replacements))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root)
    companies_root = wiki_root / "companies"
    replacements: list[dict[str, str]] = []

    for company_dir in sorted(path for path in companies_root.iterdir() if path.is_dir()):
        _, company_replacements = repair_company_dir(company_dir)
        replacements.extend(company_replacements)

    if replacements:
        repair_meta(wiki_root, replacements)

    print(json.dumps({"schema_version": 1, "repaired": replacements}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
