#!/usr/bin/env python3
"""Generate Obsidian-friendly Markdown graph layer for the company wiki."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NODE_LIMITS = {
    "segments": 80,
    "facts": 60,
    "claims": 40,
    "notes": 80,
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def slug(text: Any, max_len: int = 80) -> str:
    value = str(text or "").strip()
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"[\\/:*?\"<>|#^\[\]]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    if not value:
        value = "未命名"
    return value[:max_len].strip(" .-") or "未命名"


def md_link(path_from_company: str, label: str | None = None) -> str:
    target = path_from_company.replace("\\", "/")
    return f"[{label or target}]({target})"


def wiki_link(relative_without_ext: str, alias: str | None = None) -> str:
    target = relative_without_ext.replace("\\", "/")
    return f"[[{target}|{alias}]]" if alias else f"[[{target}]]"


def yaml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def frontmatter(items: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in items.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def write_md(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def bullet(label: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return f"- {label}: {value}\n"


def format_value(value: Any, unit: Any = None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
    elif isinstance(value, int):
        text = f"{value:,}"
    else:
        text = str(value)
    if unit:
        text += f" {unit}"
    return text


def first_list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    return value if isinstance(value, list) else []


def evidence_map(company_dir: Path) -> dict[str, dict[str, Any]]:
    evidence_data = load_json(company_dir / "semantic" / "evidence_semantic.json", {})
    return {
        item.get("evidence_id"): item
        for item in first_list(evidence_data, "evidence")
        if item.get("evidence_id")
    }


def evidence_lines(evidence_ids: list[str], ev_map: dict[str, dict[str, Any]]) -> str:
    if not evidence_ids:
        return "- 证据: 无\n"
    lines = []
    for evidence_id in evidence_ids[:8]:
        ev = ev_map.get(evidence_id, {})
        bits = [f"`{evidence_id}`"]
        if ev.get("source_file"):
            source = ev["source_file"]
            line = ev.get("md_line_start")
            label = f"{source}:{line}" if line else source
            if str(source).endswith(".md"):
                bits.append(wiki_link(str(source)[:-3], label))
            else:
                bits.append(f"`{label}`")
        if ev.get("pdf_page_number"):
            bits.append(f"PDF 第 {ev['pdf_page_number']} 页")
        if ev.get("table_index") is not None:
            bits.append(f"table {ev['table_index']}")
        if ev.get("open_pdf_page_url"):
            bits.append(f"`{ev['open_pdf_page_url']}`")
        lines.append("- 证据: " + " | ".join(bits))
    if len(evidence_ids) > 8:
        lines.append(f"- 证据: 另有 {len(evidence_ids) - 8} 条，见 `semantic/evidence_semantic.json`")
    return "\n".join(lines) + "\n"


def sorted_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        segments,
        key=lambda x: (
            priority.get(str(x.get("importance", "")).lower(), 9),
            x.get("md_line_start") or 999999,
            x.get("segment_id") or "",
        ),
    )


def sorted_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"financial_metric_fact": 0, "identity_fact": 1}
    return sorted(
        facts,
        key=lambda x: (
            priority.get(x.get("fact_type"), 9),
            x.get("period") or "",
            x.get("fact_id") or "",
        ),
    )


def build_company_graph(company_dir: Path, wiki_root: Path) -> dict[str, Any]:
    company = load_json(company_dir / "company.json", {})
    company_id = company.get("company_id") or company_dir.name
    short_name = company.get("company_short_name") or company_id
    full_name = company.get("company_full_name") or short_name
    stock_code = company.get("stock_code") or company_id.split("-", 1)[0]
    report_id = company.get("primary_report_id") or "2025-annual"
    report_year = str(report_id).split("-", 1)[0] if str(report_id)[:4].isdigit() else ""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    graph_dir = company_dir / "graph"
    obsidian_dir = company_dir / "obsidian"
    clean_dir(graph_dir)
    clean_dir(obsidian_dir)

    ev_map = evidence_map(company_dir)
    segments = first_list(load_json(company_dir / "semantic" / "segments.json", {}), "segments")
    facts = first_list(load_json(company_dir / "semantic" / "facts.json", {}), "facts")
    relations = first_list(load_json(company_dir / "semantic" / "relations.json", {}), "relations")
    claims = first_list(load_json(company_dir / "semantic" / "claims.json", {}), "claims")
    note_data = load_json(company_dir / "semantic" / "note_links.json", {})
    note_links = first_list(note_data, "links")

    node_index: list[dict[str, Any]] = []

    def add_node(node_type: str, node_id: str, title: str, rel_path: str, links: list[str]) -> None:
        node_index.append(
            {
                "node_type": node_type,
                "node_id": node_id,
                "title": title,
                "path": rel_path,
                "links": links,
            }
        )

    company_node = "graph/company.md"
    company_body = frontmatter(
        {
            "node_type": "company",
            "company_id": company_id,
            "stock_code": stock_code,
            "company_short_name": short_name,
            "company_full_name": full_name,
            "report_id": report_id,
            "generated_at": generated_at,
            "tags": ["company", "obsidian_graph"],
        }
    )
    company_body += f"# {short_name}\n\n"
    company_body += bullet("公司全称", full_name)
    company_body += bullet("证券代码", stock_code)
    company_body += bullet("报告", wiki_link("graph/report", report_id))
    company_body += "\n## 图谱入口\n\n"
    company_body += f"- {wiki_link('graph/report', '报告节点')}\n"
    company_body += f"- {wiki_link('obsidian/index', 'Obsidian 图谱入口')}\n"
    company_body += "- Agent 检索索引: `semantic/retrieval_index.json`\n"
    company_body += f"- 年报原文: {wiki_link(f'reports/{report_id}/report', 'report.md')}\n"
    company_body += "\n## 主题\n\n"
    for seg in sorted_segments(segments)[:20]:
        seg_file = f"graph/segments/{seg.get('segment_id')}.md"
        company_body += f"- {wiki_link(seg_file[:-3], seg.get('title') or seg.get('segment_id'))}\n"
    company_body += "\n## 核心判断\n\n"
    for claim in claims[:15]:
        claim_file = f"graph/claims/{claim.get('claim_id')}.md"
        company_body += f"- {wiki_link(claim_file[:-3], claim.get('statement') or claim.get('claim_id'))}\n"
    write_md(company_dir / company_node, company_body)
    add_node("company", f"company:{stock_code}", short_name, company_node, ["graph/report.md"])

    report_body = frontmatter(
        {
            "node_type": "report",
            "company_id": company_id,
            "report_id": report_id,
            "report_year": report_year,
            "tags": ["report", "annual_report", "obsidian_graph"],
        }
    )
    report_body += f"# {short_name} {report_year} 年报\n\n" if report_year else f"# {short_name} {report_id}\n\n"
    report_body += f"- 公司: {wiki_link('graph/company', short_name)}\n"
    report_body += f"- 原文: {wiki_link(f'reports/{report_id}/report', 'report.md')}\n"
    report_body += f"- 结构化源: `reports/{report_id}/document_full.json`\n"
    report_body += "- 证据索引: `evidence/evidence_index.json`\n"
    report_body += "\n## 关系类型\n\n"
    report_body += f"- {wiki_link('graph/company', short_name)} -> reported -> 指标事实\n"
    report_body += "- 报表项目 -> links_to -> 附注节点\n"
    report_body += "- facts -> supports -> claims\n"
    write_md(company_dir / "graph/report.md", report_body)
    add_node("report", f"report:{company_id}:{report_id}", f"{short_name} 2025 年报", "graph/report.md", ["graph/company.md"])

    fact_path_by_id: dict[str, str] = {}
    fact_ids_by_metric: dict[str, list[str]] = defaultdict(list)
    for fact in sorted_facts(facts)[: NODE_LIMITS["facts"]]:
        fact_id = fact.get("fact_id")
        if not fact_id:
            continue
        obj = fact.get("object") or {}
        metric_name = obj.get("name") or fact.get("predicate") or fact_id
        title = f"{metric_name} {fact.get('period') or ''}".strip()
        filename = f"{fact_id}.md"
        rel = f"graph/facts/{filename}"
        fact_path_by_id[fact_id] = rel
        metric_key = obj.get("metric_key") or metric_name
        fact_ids_by_metric[str(metric_key)].append(fact_id)

        body = frontmatter(
            {
                "node_type": "fact",
                "company_id": company_id,
                "fact_id": fact_id,
                "fact_type": fact.get("fact_type"),
                "period": fact.get("period"),
                "confidence": fact.get("confidence"),
                "needs_review": bool(fact.get("needs_review")),
                "tags": ["fact", "obsidian_graph"],
            }
        )
        body += f"# {title}\n\n"
        body += f"- 公司: {wiki_link('graph/company', short_name)}\n"
        body += f"- 报告: {wiki_link('graph/report', report_id)}\n"
        body += bullet("谓词", fact.get("predicate"))
        body += bullet("指标", metric_name)
        body += bullet("数值", format_value(fact.get("value"), fact.get("unit")))
        body += bullet("原始值", obj.get("raw_value"))
        body += bullet("期间", fact.get("period"))
        body += "\n## 证据\n\n"
        body += evidence_lines(fact.get("evidence_ids") or [], ev_map)
        write_md(company_dir / rel, body)
        add_node("fact", fact_id, title, rel, ["graph/company.md", "graph/report.md"])

    for claim in claims[: NODE_LIMITS["claims"]]:
        claim_id = claim.get("claim_id")
        if not claim_id:
            continue
        title = claim.get("statement") or claim_id
        rel = f"graph/claims/{claim_id}.md"
        supporting = claim.get("supporting_facts") or []
        links = ["graph/company.md", "graph/report.md"]
        body = frontmatter(
            {
                "node_type": "claim",
                "company_id": company_id,
                "claim_id": claim_id,
                "claim_type": claim.get("claim_type"),
                "stance": claim.get("stance"),
                "strength": claim.get("strength"),
                "confidence": claim.get("confidence"),
                "needs_review": bool(claim.get("needs_review")),
                "tags": ["claim", "obsidian_graph"],
            }
        )
        body += f"# {title}\n\n"
        body += f"- 公司: {wiki_link('graph/company', short_name)}\n"
        body += f"- 报告: {wiki_link('graph/report', report_id)}\n"
        body += bullet("立场", claim.get("stance"))
        body += bullet("强度", claim.get("strength"))
        body += bullet("置信度", claim.get("confidence"))
        body += "\n## 支撑事实\n\n"
        for fact_id in supporting:
            fact_rel = fact_path_by_id.get(fact_id)
            if fact_rel:
                body += f"- {wiki_link(fact_rel[:-3], fact_id)}\n"
                links.append(fact_rel)
            else:
                body += f"- `{fact_id}`\n"
        body += "\n## 证据\n\n"
        body += evidence_lines(claim.get("evidence_ids") or [], ev_map)
        write_md(company_dir / rel, body)
        add_node("claim", claim_id, title, rel, links)

    for seg in sorted_segments(segments)[: NODE_LIMITS["segments"]]:
        seg_id = seg.get("segment_id")
        if not seg_id:
            continue
        title = seg.get("title") or seg_id
        rel = f"graph/segments/{seg_id}.md"
        body = frontmatter(
            {
                "node_type": "segment",
                "company_id": company_id,
                "segment_id": seg_id,
                "segment_type": seg.get("segment_type"),
                "importance": seg.get("importance"),
                "pdf_page_start": seg.get("pdf_page_start"),
                "pdf_page_end": seg.get("pdf_page_end"),
                "md_line_start": seg.get("md_line_start"),
                "md_line_end": seg.get("md_line_end"),
                "tags": ["segment", "obsidian_graph", seg.get("segment_type")],
            }
        )
        body += f"# {title}\n\n"
        body += f"- 公司: {wiki_link('graph/company', short_name)}\n"
        body += f"- 报告: {wiki_link('graph/report', report_id)}\n"
        body += bullet("主题类型", seg.get("segment_type"))
        body += bullet("重要性", seg.get("importance"))
        body += bullet("Markdown 行号", f"{seg.get('md_line_start')} - {seg.get('md_line_end')}")
        body += bullet("PDF 页码", f"{seg.get('pdf_page_start')} - {seg.get('pdf_page_end')}")
        if seg.get("keywords"):
            body += "- 关键词: " + "、".join(str(x) for x in seg["keywords"]) + "\n"
        body += "\n## 摘要\n\n"
        body += (seg.get("summary") or "") + "\n\n"
        body += "## 证据\n\n"
        body += evidence_lines(seg.get("evidence_ids") or [], ev_map)
        write_md(company_dir / rel, body)
        add_node("segment", seg_id, title, rel, ["graph/company.md", "graph/report.md"])

    note_nodes_seen: set[str] = set()
    for link in note_links[: NODE_LIMITS["notes"]]:
        note_id = link.get("note_link_id")
        if not note_id:
            continue
        statement = link.get("statement") or {}
        note = link.get("note") or {}
        item = statement.get("item") or statement.get("alias") or "报表项目"
        note_title = note.get("title") or note.get("alias") or item
        title = f"{item} -> {note_title}"
        rel = f"graph/notes/{note_id}.md"
        note_nodes_seen.add(rel)
        amount_check = ((link.get("linkage") or {}).get("amount_check") or {}).get("status")
        body = frontmatter(
            {
                "node_type": "note_link",
                "company_id": company_id,
                "note_link_id": note_id,
                "statement_item": item,
                "note_title": note_title,
                "amount_check": amount_check,
                "confidence": (link.get("linkage") or {}).get("confidence"),
                "needs_review": bool(link.get("needs_review")),
                "tags": ["note_link", "obsidian_graph"],
            }
        )
        body += f"# {title}\n\n"
        body += f"- 公司: {wiki_link('graph/company', short_name)}\n"
        body += f"- 报告: {wiki_link('graph/report', report_id)}\n"
        body += bullet("报表项目", item)
        body += bullet("报表行号", statement.get("line"))
        body += bullet("报表 PDF 页", statement.get("pdf_page_number"))
        body += bullet("报表 table_index", statement.get("table_index"))
        body += bullet("附注标题", note_title)
        body += bullet("附注编号", note.get("ref"))
        body += bullet("附注行号", note.get("line"))
        body += bullet("附注 PDF 页", note.get("pdf_page_number"))
        body += bullet("匹配方法", (link.get("linkage") or {}).get("method"))
        body += bullet("金额校验", amount_check)
        if statement.get("open_pdf_page_url"):
            body += bullet("打开报表 PDF 页", f"`{statement.get('open_pdf_page_url')}`")
        if statement.get("open_source_table_url"):
            body += bullet("打开报表表格", f"`{statement.get('open_source_table_url')}`")
        if note.get("open_pdf_page_url"):
            body += bullet("打开附注 PDF 页", f"`{note.get('open_pdf_page_url')}`")
        body += "\n## 证据\n\n"
        body += evidence_lines(link.get("evidence_ids") or [], ev_map)
        write_md(company_dir / rel, body)
        add_node("note_link", note_id, title, rel, ["graph/company.md", "graph/report.md"])

    metric_to_paths: dict[str, list[str]] = {}
    for rel in relations:
        metric_key = ((rel.get("properties") or {}).get("metric_key")) or rel.get("target_entity_name")
        if not metric_key:
            continue
        metric_to_paths.setdefault(str(metric_key), [])
    for metric_key in metric_to_paths:
        for fact_id in fact_ids_by_metric.get(metric_key, []):
            if fact_id in fact_path_by_id:
                metric_to_paths[metric_key].append(fact_path_by_id[fact_id])

    obsidian_index = frontmatter(
        {
            "node_type": "obsidian_index",
            "company_id": company_id,
            "tags": ["obsidian", "graph_index"],
        }
    )
    obsidian_index += f"# {short_name} Obsidian 图谱入口\n\n"
    obsidian_index += f"- 公司节点: {wiki_link('graph/company', short_name)}\n"
    obsidian_index += f"- 报告节点: {wiki_link('graph/report', report_id)}\n"
    obsidian_index += "- 机器索引: `graph/graph_index.json`\n"
    obsidian_index += "- 原始语义层: `semantic/retrieval_index.json`\n\n"
    obsidian_index += "## 节点数量\n\n"
    counts = defaultdict(int)
    for node in node_index:
        counts[node["node_type"]] += 1
    for key in sorted(counts):
        obsidian_index += f"- {key}: {counts[key]}\n"
    obsidian_index += "\n## 推荐打开方式\n\n"
    obsidian_index += "在 Obsidian 中先打开本页，再打开局部图谱。图谱节点来自 Markdown 双链，证据细节仍以 JSON 和 report.md 为准。\n"
    write_md(obsidian_dir / "index.md", obsidian_index)

    obsidian_readme = "# Obsidian 派生层说明\n\n"
    obsidian_readme += "本目录用于 Obsidian 可视化，不替代 `semantic/`、`metrics/`、`evidence/` 的机器可读事实底座。\n\n"
    obsidian_readme += "- `../graph/company.md` 是公司中心节点。\n"
    obsidian_readme += "- `../graph/report.md` 是报告节点。\n"
    obsidian_readme += "- `../graph/facts/`、`../graph/claims/`、`../graph/segments/`、`../graph/notes/` 是可视化节点。\n"
    obsidian_readme += "- `../graph/graph_index.json` 是节点清单。\n"
    obsidian_readme += "- 报告写作和审计仍应回到 JSON 证据链与 PDF 页面 URL。\n"
    write_md(obsidian_dir / "README.md", obsidian_readme)

    graph_index = {
        "schema_version": 1,
        "generated_at": generated_at,
        "company_id": company_id,
        "report_id": report_id,
        "node_counts": dict(sorted(counts.items())),
        "limits": NODE_LIMITS,
        "node_count": len(node_index),
        "nodes": node_index,
        "source_files": {
            "segments": "semantic/segments.json",
            "facts": "semantic/facts.json",
            "relations": "semantic/relations.json",
            "claims": "semantic/claims.json",
            "note_links": "semantic/note_links.json",
            "evidence": "semantic/evidence_semantic.json",
        },
    }
    dump_json(graph_dir / "graph_index.json", graph_index)

    return {
        "company_id": company_id,
        "status": "ok",
        "node_count": len(node_index),
        "node_counts": dict(sorted(counts.items())),
        "graph_index": str((graph_dir / "graph_index.json").relative_to(wiki_root)),
        "obsidian_index": str((obsidian_dir / "index.md").relative_to(wiki_root)),
    }


def company_dirs_for(wiki_root: Path, company: str) -> list[Path]:
    companies_dir = wiki_root / "companies"
    if company:
        return [companies_dir / company]
    return sorted(p for p in companies_dir.iterdir() if p.is_dir())


def build_manifest(
    wiki_root: Path,
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    selected_company: str = "",
) -> dict[str, Any]:
    manifest_path = wiki_root / "_meta" / "obsidian_graph_manifest.json"
    existing = load_json(manifest_path, {}) if selected_company else {}
    old_results = existing.get("results") if isinstance(existing.get("results"), list) else []
    old_failures = existing.get("failures") if isinstance(existing.get("failures"), list) else []
    if selected_company:
        current_ids = {selected_company}
        current_ids.update(str(item.get("company_id") or "") for item in results + failures)
        results = [item for item in old_results if item.get("company_id") not in current_ids] + results
        failures = [item for item in old_failures if item.get("company_id") not in current_ids] + failures

    totals: dict[str, int] = defaultdict(int)
    for result in results:
        for key, value in result.get("node_counts", {}).items():
            totals[key] += int(value)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "company" if selected_company else "all",
        "selected_company": selected_company,
        "company_count": len(results),
        "failure_count": len(failures),
        "node_count": sum(int(r.get("node_count", 0)) for r in results),
        "node_counts": dict(sorted(totals.items())),
        "description": "Obsidian-friendly Markdown graph layer generated from semantic JSON. It is a visualization derivative, not the source of truth.",
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    parser.add_argument("--company", default="", help="Optional company_id, e.g. 002594-比亚迪")
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root).resolve()
    results = []
    failures = []

    for company_dir in company_dirs_for(wiki_root, args.company):
        if not (company_dir / "company.json").exists():
            failures.append({"company_id": company_dir.name, "error": "company.json not found"})
            continue
        try:
            results.append(build_company_graph(company_dir, wiki_root))
        except Exception as exc:  # pragma: no cover - batch manifest should keep going.
            failures.append({"company_id": company_dir.name, "error": repr(exc)})

    manifest = build_manifest(wiki_root, results, failures, args.company)
    dump_json(wiki_root / "_meta" / "obsidian_graph_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("company_count", "failure_count", "node_count", "node_counts")}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
