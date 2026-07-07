from __future__ import annotations

import html as html_module
import re
from typing import Any, Callable


SOURCE_PREFIXES: list[tuple[str, str, str]] = [
    ("【本地事实证据】", "本地事实", "source-local"),
    ("【本地同业模型判断】", "同业模型", "source-model"),
    ("【基于本地证据的分析判断】", "本地分析", "source-local"),
    ("【模型测算】", "模型测算", "source-model"),
    ("【外部搜索补证形成的判断】", "外部补证判断", "source-external"),
    ("【外部搜索补证】", "外部搜索", "source-external"),
    ("【风险链】", "风险链", "source-risk"),
    ("【跟踪信号】", "跟踪信号", "source-tracking"),
    ("【证据状态判断】", "证据状态", "source-review"),
]

ROLE_LABELS = {
    "synthesis": "综合解读",
    "diagnosis": "诊断",
    "analysis": "分析",
    "bridge": "桥接",
    "model": "模型",
    "table": "指标",
    "risk_chain": "风险链",
    "scenario": "情景",
    "tracking": "跟踪",
    "evidence": "证据",
    "audit": "审阅",
}


def is_positive_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text)) and int(text) > 0


def is_nonnegative_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text))


def source_anchor(url: str, label: str) -> str:
    safe_url = html_module.escape(url, quote=True)
    safe_label = html_module.escape(label)
    return f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_label}</a>'


def evidence_links_from_id(
    evidence_id: Any,
    preflight: dict[str, Any],
    *,
    public_api_url: Callable[[str], str],
) -> str:
    text = str(evidence_id)
    if text.endswith(":missing"):
        return ""
    task_id = preflight.get("task_id") or ""
    page_match = re.search(r":p([^:]+)", text)
    table_match = re.search(r":t([^:]+)", text)
    page = page_match.group(1) if page_match else ""
    table = table_match.group(1) if table_match else ""
    links = []
    if task_id and is_positive_int_token(page):
        links.append(source_anchor(public_api_url(f"/api/pdf_page/{task_id}/{page}"), "PDF"))
        links.append(source_anchor(public_api_url(f"/api/source/{task_id}/page/{page}"), "页来源"))
    if task_id and is_nonnegative_int_token(table):
        links.append(source_anchor(public_api_url(f"/api/source/{task_id}/table/{table}"), "表格"))
    return "".join(links)


def split_source_prefix(text: Any, role: str) -> tuple[str, str, str]:
    raw = str(text or "").strip()
    for prefix, label, cls in SOURCE_PREFIXES:
        if raw.startswith(prefix):
            return label, cls, raw[len(prefix):].strip()
    if role in {"model", "table"}:
        return "模型/指标", "source-model", raw
    if role in {"risk_chain", "scenario"}:
        return "风险/情景", "source-risk", raw
    if role == "tracking":
        return "跟踪信号", "source-tracking", raw
    if role in {"evidence", "audit"}:
        return "证据/审阅", "source-fact", raw
    if role == "synthesis":
        return "综合解读", "source-review", raw
    return "分析判断", "source-review", raw


def sentence_paragraphs(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    clean = re.sub(r"。+；", "；", clean)
    clean = re.sub(r"；+", "；", clean)
    if not clean:
        return []
    parts = re.split(r"(?<=[。！？])\s+", clean)
    paragraphs = [part.strip() for part in parts if part.strip()]
    return paragraphs or [clean]


def truncate_text(text: str, max_chars: int = 220) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip("，。；、 ") + "…"


def render_narrative_item(item: Any, role: str) -> str:
    label, source_cls, text = split_source_prefix(item, role)
    paragraphs = sentence_paragraphs(text)
    if not paragraphs:
        return ""
    body = "".join(f"<p>{html_module.escape(paragraph)}</p>" for paragraph in paragraphs)
    return (
        f'<article class="narrative-item {html_module.escape(role)}">'
        f'<span class="source-badge {html_module.escape(source_cls)}">{html_module.escape(label)}</span>'
        f"{body}</article>"
    )


def render_section_content(
    section: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    *,
    public_api_url: Callable[[str], str],
) -> str:
    """Render a single section's content with CFO-style narrative blocks."""
    parts = []
    preflight = preflight or {}

    blocks = section.get("narrative_blocks", [])
    if isinstance(blocks, list) and blocks:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            title = str(block.get("title") or "").strip()
            items = block.get("items", [])
            if not title or not isinstance(items, list) or not items:
                continue
            role = str(block.get("role") or "analysis").strip()
            role_class = html_module.escape(f"role-{role}")
            role_label = ROLE_LABELS.get(role, role)
            parts.append(f'<div class="subsection narrative-block {html_module.escape(role)} {role_class}">')
            parts.append(
                f'<div class="subsection-title">{html_module.escape(title)}'
                f'<span class="role-badge {role_class}">{html_module.escape(role_label)}</span></div>'
            )
            parts.append('<div class="narrative-items">')
            for item in items:
                rendered = render_narrative_item(item, role)
                if rendered:
                    parts.append(rendered)
            parts.append("</div></div>")

        evidence = section.get("evidence_ids", [])
        if evidence:
            parts.append('<details class="evidence-details">')
            parts.append(f"<summary>本节证据 · {len(evidence)} 项</summary>")
            parts.append('<div class="evidence-list">')
            for ev in evidence:
                is_missing = str(ev).endswith(":missing") or str(ev).endswith("未返回")
                cls = "missing" if is_missing else ""
                links = evidence_links_from_id(ev, preflight, public_api_url=public_api_url)
                parts.append(f'<span class="evidence-tag {cls}">{html_module.escape(str(ev))}{links}</span>')
            parts.append("</div></details>")

        return "\n".join(parts)

    # Legacy fallback: keep old fields readable, but do not revive the
    # mechanical 事实/计算/判断/风险 skeleton.
    facts = section.get("facts", [])
    if facts:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">证据锚点</div>')
        parts.append('<ul class="content-list">')
        for fact in facts:
            parts.append(f'<li class="fact">{html_module.escape(str(fact))}</li>')
        parts.append("</ul></div>")

    calcs = section.get("calculations", [])
    if calcs:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">模型口径</div>')
        parts.append('<ul class="content-list">')
        for calc in calcs:
            parts.append(f'<li class="calc">{html_module.escape(str(calc))}</li>')
        parts.append("</ul></div>")

    judgements = section.get("judgements", [])
    if judgements:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">财务解释</div>')
        parts.append('<ul class="content-list">')
        for judgement in judgements:
            parts.append(f'<li class="judge">{html_module.escape(str(judgement))}</li>')
        parts.append("</ul></div>")

    risks = section.get("risks_or_improvement_conditions", [])
    if risks:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">验证边界</div>')
        parts.append('<ul class="content-list">')
        for risk in risks:
            parts.append(f'<li class="risk">{html_module.escape(str(risk))}</li>')
        parts.append("</ul></div>")

    evidence = section.get("evidence_ids", [])
    if evidence:
        parts.append('<details class="evidence-details">')
        parts.append(f"<summary>本节证据 · {len(evidence)} 项</summary>")
        parts.append('<div class="evidence-list">')
        for ev in evidence:
            is_missing = str(ev).endswith(":missing") or str(ev).endswith("未返回")
            cls = "missing" if is_missing else ""
            links = evidence_links_from_id(ev, preflight, public_api_url=public_api_url)
            parts.append(f'<span class="evidence-tag {cls}">{html_module.escape(str(ev))}{links}</span>')
        parts.append("</div></details>")

    return "\n".join(parts)


def render_navigation(sections: list[dict[str, Any]]) -> str:
    """Render a compact TOC for long 14-section financial reports."""
    links = []
    for i, section in enumerate(sections):
        sid = html_module.escape(str(section.get("section_id") or i + 1))
        title = html_module.escape(str(section.get("title") or f"第 {i + 1} 节"))
        links.append(f'<a href="#section-{sid}" aria-label="跳转到{title}"><span class="toc-index">{i + 1:02d}</span><span>{title}</span></a>')
    if not links:
        return ""
    return (
        '<aside class="section-toc" aria-label="报告目录">'
        '<div class="toc-eyebrow">报告目录</div>'
        f'<nav>{"".join(links)}</nav>'
        "</aside>"
    )
