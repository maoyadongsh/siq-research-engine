#!/usr/bin/env python3
"""Generate an HTML view for SIQ_factchecker v2 JSON."""

import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

KEY_EVIDENCE_LIMIT = 4

CHECK_NAMES = {
    "identity_consistency": "研究身份一致性",
    "data_consistency": "数据原文一致性",
    "period_consistency": "报告期一致性",
    "calculation_consistency": "计算公式一致性",
    "claim_support": "声明证据充分性",
    "traceability": "证据链完整性",
    "logic_support": "结论支撑充分性",
    "a_share_risk_completeness": "A股风险完整性",
    "market_risk_completeness": "市场风险完整性",
    "template_compliance": "模板与规则合规性",
}

CHECK_DESCRIPTIONS = {
    "identity_consistency": "核对市场、公司、申报文件与解析批次是否完全一致。",
    "data_consistency": "核对报告关键数据是否能回到年报、解析表或结构化证据。",
    "period_consistency": "核对源报告财年、截止日与结构化指标期间是否一致。",
    "calculation_consistency": "复算核心公式，识别口径、符号或单位错误。",
    "claim_support": "检查分析声明是否绑定可回溯的证据引用。",
    "traceability": "检查重要结论是否带有可回跳证据与原始页定位。",
    "logic_support": "判断投资结论、风险判断与数据证据是否匹配。",
    "a_share_risk_completeness": "覆盖 A 股常见风险、会计口径与监管表达要求。",
    "market_risk_completeness": "按披露市场和报告类型核对风险章节与证据覆盖。",
    "template_compliance": "检查报告结构、章节和交付格式是否符合 SIQ 规则。",
}

VERDICT_LABELS = {
    "approve": "通过",
    "request_changes": "需修改",
    "block": "阻断发布",
}

VERDICT_MESSAGES = {
    "approve": "未发现阻断项，报告可进入交付或归档流程。",
    "request_changes": "报告主体可读，但仍有问题需要修订后再交付。",
    "block": "存在关键事实、证据或合规问题，建议暂停发布。",
}

STATUS_LABELS = {
    "pass": "通过",
    "warning": "关注",
    "fail": "未通过",
    "blocked": "阻断",
    "unknown": "未知",
}

CLAIM_STATUS_LABELS = {
    "verified": "已验证",
    "contradicted": "存在反证",
    "unsupported": "证据不足",
    "unknown": "未判定",
}

SEVERITY_LABELS = {
    "critical": "严重",
    "warning": "警告",
    "suggestion": "建议",
    "info": "说明",
    "issue": "问题",
}

EVIDENCE_STATUS_LABELS = {
    "available": "可用",
    "unavailable": "不可用",
    "wiki_exact_identity": "身份一致且可回溯",
    "wiki-exact-identity": "身份一致且可回溯",
    "local_wiki_available": "本地证据可用",
    "local-wiki-available": "本地证据可用",
    "not_required": "无需外部数据库",
    "not-required": "无需外部数据库",
    "not_checked": "未检查",
    "not-checked": "未检查",
    "verified": "已验证",
    "calculated": "已计算",
    "insufficient_evidence": "证据不足",
    "insufficient-evidence": "证据不足",
    "unknown": "未知",
}

SOURCE_TYPE_LABELS = {
    "balance_sheet": "资产负债表",
    "income_statement": "利润表",
    "cash_flow": "现金流量表",
    "cash_flow_statement": "现金流量表",
    "financial_indicator": "财务指标",
    "normalized_metric": "结构化财务指标",
    "xbrl_fact": "XBRL 财务事实",
    "sec_html_section": "SEC 披露章节",
    "pdf_table": "报告表格",
    "pdf_text": "报告原文",
    "result_markdown_formal_statement_window": "财务报表原文",
    "formal_statement_window": "财务报表原文",
    "report_section": "报告章节",
    "wiki_evidence": "本地证据",
    "verified": "已验证",
    "calculated": "已计算",
    "none": "无",
    "unknown": "未知",
}

METRIC_LABELS = {
    "operating_revenue": "营业收入",
    "revenue": "营业收入",
    "net_profit": "净利润",
    "parent_net_profit": "归母净利润",
    "net_profit_parent": "归母净利润",
    "operating_profit": "营业利润",
    "operating_cost": "营业成本",
    "total_assets": "资产总额",
    "total_liabilities": "负债总额",
    "total_equity": "所有者权益",
    "equity_attributable_parent": "归母权益",
    "operating_cash_flow_net": "经营活动现金流净额",
    "cash_and_cash_equivalents": "现金及现金等价物",
    "monetary_capital": "货币资金",
    "accounts_receivable": "应收账款",
    "inventory": "存货",
    "weighted_avg_roe": "加权平均净资产收益率",
}

RAW_LABELS = {
    "non-current assets held for sale": "持有待售非流动资产",
    "total assets": "资产总额",
    "total liabilities": "负债总额",
    "total liabilities at 31 dec": "负债总额（截至 12 月 31 日）",
    "total equity": "所有者权益",
    "total shareholders’ equity": "股东权益总额",
    "total shareholders' equity": "股东权益总额",
    "net income": "净利润",
    "operating income": "营业利润",
    "operating revenue": "营业收入",
    "revenue": "营业收入",
    "cash and cash equivalents": "现金及现金等价物",
}

SECTION_LABELS = {
    "item_1": "业务概览（Item 1）",
    "item_1a": "风险因素（Item 1A）",
    "item_1b": "未解决监管意见（Item 1B）",
    "item_2": "主要资产（Item 2）",
    "item_3": "法律诉讼（Item 3）",
    "item_7": "管理层讨论与分析（Item 7）",
    "item_7a": "市场风险披露（Item 7A）",
    "item_8": "财务报表及附注（Item 8）",
    "part_i_item_1": "财务报表（Part I Item 1）",
    "part_i_item_2": "管理层讨论与分析（Part I Item 2）",
    "part_ii_item_1a": "风险因素（Part II Item 1A）",
}

FILE_LABELS = {
    "business.md": "业务概览",
    "risk_factors.md": "风险因素",
    "mda.md": "管理层讨论与分析",
    "financial_statements.md": "财务报表及附注",
    "normalized_metrics.json": "结构化财务指标",
    "financial_data.json": "结构化财务数据",
    "financial_checks.json": "财务校验结果",
    "three_statements.json": "三大财务报表",
    "source_map.json": "证据定位目录",
}


def esc(value):
    return html.escape(str(value), quote=True)


def as_count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_key(value, fallback="unknown"):
    raw = str(value or fallback).strip().lower()
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "-" for ch in raw) or fallback


def chinese_ui_text(value):
    text = str(value or "")
    replacements = (
        (r"financial checks", "财务校验"),
        (r"source map", "证据定位目录"),
        (r"ResearchIdentity", "研究身份"),
        (r"content_hash", "内容哈希"),
        (r"sidecar", "产物元数据"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def issue_text(issue):
    if isinstance(issue, dict):
        parts = [chinese_ui_text(issue.get("message", ""))]
        if issue.get("expected"):
            parts.append(f"期望: {issue['expected']}")
        if issue.get("actual"):
            parts.append(f"实际: {issue['actual']}")
        refs = issue.get("evidence_refs", [])
        if refs:
            parts.append(f"证据: {len(refs)}条")
        elif "evidence_refs" in issue:
            parts.append("证据: 缺失/不适用")
        return "；".join(str(p) for p in parts if p)
    return chinese_ui_text(issue)


def clean_text(value, limit=180):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def safe_link_url(value):
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith(("/", "#")) and not url.startswith("//"):
        return url
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return url
    return ""


def evidence_links(ev):
    candidates = [
        ("查看 PDF 定位", ev.get("open_pdf_page_url")),
        ("查看原文定位", ev.get("open_source_page_url")),
        ("查看表格定位", ev.get("open_source_table_url")),
    ]
    source_url = safe_link_url(ev.get("source_url"))
    anchor = str(ev.get("html_anchor") or "").strip()
    if source_url and anchor and "#" not in source_url:
        source_url = f"{source_url}#{quote(anchor, safe='-_:.')}"
    candidates.append(("查看披露原文", source_url))

    links = []
    seen = set()
    for label, value in candidates:
        url = safe_link_url(value)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>')
    return "".join(links)


def evidence_value(ev):
    raw = ev.get("raw_value")
    value = raw if raw not in (None, "") else ev.get("value", "")
    unit = str(ev.get("unit") or "").strip()
    if value in (None, ""):
        return "-"
    return f"{value} {unit}".strip()


def humanize_identifier(value):
    text = clean_text(value, 100)
    if not text:
        return ""
    key = safe_key(text)
    if key in METRIC_LABELS:
        return METRIC_LABELS[key]
    raw_key = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip().casefold()
    if raw_key in RAW_LABELS:
        suffix = text[len(re.sub(r"\s*\([^)]*\)\s*$", "", text).rstrip()) :].strip()
        if suffix.startswith("(") and suffix.endswith(")"):
            unit = suffix[1:-1]
            unit_label = {
                "$m": "百万美元",
                "us$m": "百万美元",
                "€m": "百万欧元",
                "eur m": "百万欧元",
                "£m": "百万英镑",
            }.get(unit.casefold(), unit)
            return f"{RAW_LABELS[raw_key]}（{unit_label}）"
        return RAW_LABELS[raw_key]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return spaced.replace("_", " ").replace("-", " ").strip()


def section_name(value):
    section_id = str(value or "").strip()
    if not section_id:
        return ""
    key = safe_key(section_id).replace("-", "_")
    if key in SECTION_LABELS:
        return SECTION_LABELS[key]
    return f"报告章节 {humanize_identifier(section_id)}"


def evidence_period(ev):
    value = next(
        (
            ev.get(key)
            for key in ("period", "period_key", "period_end", "fiscal_year", "reporting_period")
            if ev.get(key) not in (None, "")
        ),
        "",
    )
    text = clean_text(value, 64)
    if not text:
        return ""
    if re.fullmatch(r"20\d{2}", text):
        return f"{text} 年"
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        return f"截至 {text}"
    return text


def evidence_location(ev):
    parts = []
    pdf_page = ev.get("pdf_page_number") or ev.get("pdf_page")
    table_index = ev.get("table_index") or ev.get("table_id")
    md_line = ev.get("markdown_line") or ev.get("md_line")
    section = section_name(ev.get("section_id"))
    if section:
        parts.append(section)
    if pdf_page not in (None, ""):
        parts.append(f"PDF 第 {pdf_page} 页")
    if table_index not in (None, ""):
        parts.append(f"表格 {table_index}")
    if md_line not in (None, ""):
        parts.append(f"解析文本第 {md_line} 行")
    return " · ".join(parts) or "-"


def display_label(value, labels):
    text = str(value or "unknown").strip()
    key = safe_key(text)
    return labels.get(key, text or labels.get("unknown", "未知"))


def evidence_quote(ev):
    return clean_text(ev.get("quote") or ev.get("quote_text"), 180)


def evidence_label(ev):
    for key in (
        "item_name",
        "metric",
        "metric_or_claim",
        "canonical_name",
        "claim",
        "claim_text",
        "description",
        "message",
        "xbrl_concept",
    ):
        value = ev.get(key)
        if value not in (None, ""):
            return humanize_identifier(value)

    quote_text = evidence_quote(ev)
    if quote_text:
        first_field = re.split(r"\s*\|\s*|\s*[:：]\s*", quote_text, maxsplit=1)[0]
        if first_field:
            return humanize_identifier(first_field)

    section = section_name(ev.get("section_id"))
    if section:
        return section

    for key in ("file", "local_source_id", "source_path"):
        source = str(ev.get(key) or "").strip()
        if source:
            name = Path(source).name
            return FILE_LABELS.get(name, f"报告资料：{name}")
    return "报告证据定位"


def evidence_source_name(ev):
    source_path = next(
        (
            str(ev.get(key)).strip()
            for key in ("file", "local_source_id", "source_path", "source")
            if ev.get(key) not in (None, "")
        ),
        "",
    )
    if source_path:
        name = Path(source_path).name
        return FILE_LABELS.get(name, name), source_path
    if ev.get("source_url"):
        return "公开披露原文", ""
    return "本地解析证据", ""


def evidence_audit_fields(ev):
    fields = []
    seen = set()
    for label, key in (
        ("证据编号", "evidence_id"),
        ("XBRL 事实编号", "xbrl_fact_id"),
        ("事实编号", "fact_id"),
        ("解析任务编号", "task_id"),
        ("PDF 任务编号", "pdf_task_id"),
    ):
        value = clean_text(ev.get(key), 160)
        if not value or value in seen:
            continue
        seen.add(value)
        fields.append(
            f'<span class="audit-field"><span>{esc(label)}</span><code>{esc(value)}</code></span>'
        )
    return "".join(fields) or '<span class="muted">无额外审计编号</span>'


def evidence_source_type(ev):
    value = ev.get("statement_type") or ev.get("source_type") or ev.get("status") or "unknown"
    return display_label(value, SOURCE_TYPE_LABELS)


def key_evidence(evidence, limit=KEY_EVIDENCE_LIMIT):
    selected = []
    seen = set()
    for ev in evidence:
        label = evidence_label(ev)
        quote_text = evidence_quote(ev)
        if quote_text:
            key = ("quote", label.casefold(), quote_text.casefold())
        elif ev.get("section_id"):
            key = ("section", str(ev.get("section_id")).casefold())
        else:
            key = (
                "metric",
                label.casefold(),
                evidence_period(ev).casefold(),
                evidence_value(ev).casefold(),
            )
        if key in seen:
            continue
        seen.add(key)
        selected.append(ev)
        if len(selected) >= limit:
            break
    return selected


def render_key_evidence(evidence):
    items = []
    for ev in key_evidence(evidence):
        label = evidence_label(ev)
        quote_text = evidence_quote(ev)
        period = evidence_period(ev)
        location = evidence_location(ev)
        value = evidence_value(ev)
        links = evidence_links(ev)
        readable_location = location if location != "-" and location.casefold() != label.casefold() else ""
        context = " · ".join(part for part in (period, readable_location) if part)
        detail_parts = []
        if value != "-":
            detail_parts.append(f'<span class="key-value">核验值：{esc(value)}</span>')
        if quote_text and quote_text.casefold() != label.casefold():
            detail_parts.append(f'<p class="evidence-quote">{esc(quote_text)}</p>')
        if links:
            detail_parts.append(f'<div class="link-row">{links}</div>')
        items.append(
            f"""
            <article class="key-evidence-item">
              <div class="key-evidence-topline">
                <h3>{esc(label)}</h3>
                <span class="source-type">{esc(evidence_source_type(ev))}</span>
              </div>
              {f'<p class="evidence-context">{esc(context)}</p>' if context else ''}
              {''.join(detail_parts)}
            </article>"""
        )
    return "".join(items) or '<div class="empty-state">暂无可展示的关键核验依据</div>'


def render_issues(issues):
    if not issues:
        return '<div class="empty-inline">未发现问题</div>'
    items = []
    for issue in issues:
        severity = "issue"
        if isinstance(issue, dict):
            severity = safe_key(issue.get("severity") or issue.get("level") or "issue", "issue")
        label = SEVERITY_LABELS.get(severity, severity.title())
        items.append(
            f"""
            <li class="issue issue-{esc(severity)}">
              <span class="severity">{esc(label)}</span>
              <span class="issue-copy">{esc(issue_text(issue))}</span>
            </li>"""
        )
    return f'<ul class="issue-list">{"".join(items)}</ul>'


def render_check_items(checks):
    rows = []
    for key, check in checks.items():
        status = safe_key(check.get("status", "unknown"))
        issues = check.get("issues", [])
        rows.append(
            f"""
            <article class="check-item check-{esc(status)}">
              <div class="check-topline">
                <div>
                  <h3>{esc(CHECK_NAMES.get(key, key))}</h3>
                  <p>{esc(CHECK_DESCRIPTIONS.get(key, "核查该维度的事实、证据与表达质量。"))}</p>
                </div>
                <div class="check-badges">
                  <span class="status status-{esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span>
                  <span class="count">{esc(len(issues))} 项</span>
                </div>
              </div>
              {render_issues(issues)}
            </article>"""
        )
    return "".join(rows) or '<div class="empty-state">暂无核查维度结果</div>'


def render_claim_verdicts(verdicts):
    items = []
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        status = safe_key(verdict.get("status") or verdict.get("verdict") or "unknown")
        claim = clean_text(verdict.get("claim") or verdict.get("text") or verdict.get("claim_id"), 500)
        reason = clean_text(verdict.get("reason"), 500)
        if not reason and status == "verified":
            reason = "声明的数值、单位、期间与对应证据一致。"
        elif not reason:
            reason = "未提供具体核验原因。"
        metric = humanize_identifier(verdict.get("metric_key"))
        period = clean_text(verdict.get("period"), 40)
        context = " · ".join(part for part in (metric, f"截至 {period}" if period else "") if part)
        items.append(
            f"""
            <article class="claim-item claim-{esc(status)}">
              <div class="claim-topline">
                <h3>{esc(claim or "未命名声明")}</h3>
                <span class="claim-status claim-status-{esc(status)}">{esc(CLAIM_STATUS_LABELS.get(status, status))}</span>
              </div>
              {f'<p class="claim-context">{esc(context)}</p>' if context else ''}
              <p class="claim-reason"><strong>核验理由：</strong>{esc(reason)}</p>
            </article>"""
        )
    return "".join(items) or '<div class="empty-state">分析产物未提供可逐条核验的结构化声明</div>'


def render_evidence_rows(evidence):
    rows = []
    for ev in evidence:
        period = evidence_period(ev)
        location = evidence_location(ev)
        period_location = " · ".join(part for part in (period, location if location != "-" else "") if part) or "-"
        source_label, source_path = evidence_source_name(ev)
        links = evidence_links(ev)
        source_html = f'<span class="source-name">{esc(source_label)}</span>'
        if source_path:
            source_html += f'<code class="source-file">{esc(source_path)}</code>'
        if links:
            source_html = f"{source_html}<div class=\"link-row\">{links}</div>"
        quote_text = evidence_quote(ev)
        label = evidence_label(ev)
        value = evidence_value(ev)
        evidence_detail = f'<strong>{esc(label)}</strong>'
        if value != "-":
            evidence_detail += f'<span class="evidence-value">核验值：{esc(value)}</span>'
        if quote_text and quote_text.casefold() != label.casefold():
            evidence_detail += f'<span class="evidence-row-quote">{esc(quote_text)}</span>'
        rows.append(
            f"""
            <tr>
              <td data-label="指标或声明">{evidence_detail}</td>
              <td data-label="来源类型"><span class="source-type">{esc(evidence_source_type(ev))}</span></td>
              <td data-label="期间与定位">{esc(period_location)}</td>
              <td data-label="来源与回跳">{source_html}</td>
              <td data-label="审计字段"><div class="audit-fields">{evidence_audit_fields(ev)}</div></td>
            </tr>"""
        )
    if rows:
        return "".join(rows)
    return '<tr><td colspan="5" class="empty-cell">无可用证据摘要</td></tr>'


def render_calc_rows(calculations):
    rows = []
    for calc in calculations:
        refs = calc.get("evidence_refs", [])
        status = safe_key(calc.get("status", "unknown"))
        rows.append(
            f"""
            <tr>
              <td>{esc(calc.get("name", ""))}</td>
              <td><span class="status status-{esc(status)}">{esc(STATUS_LABELS.get(status, status))}</span></td>
              <td class="number">{esc(calc.get("recomputed_value", ""))} {esc(calc.get("unit", ""))}</td>
              <td class="number">{esc(calc.get("reported_value", "未识别"))}</td>
              <td class="number">{esc(calc.get("delta", ""))}</td>
              <td>{esc(len(refs))}</td>
            </tr>"""
        )
    if rows:
        return "".join(rows)
    return '<tr><td colspan="6" class="empty-cell">暂无可自动重算项目</td></tr>'


def render_recommendations(recommendations):
    if not recommendations:
        return '<div class="empty-state">暂无优先修改建议</div>'
    items = []
    for index, rec in enumerate(recommendations, start=1):
        text = str(rec)
        match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
        if match:
            check_key, detail = match.groups()
            text = f"{CHECK_NAMES.get(check_key, humanize_identifier(check_key))}：{detail}"
        text = chinese_ui_text(text)
        items.append(
            f"""
            <li>
              <span class="rec-index">{index}</span>
              <p>{esc(text)}</p>
            </li>"""
        )
    return f'<ol class="recommendations">{"".join(items)}</ol>'


def generate_html(factcheck_path: str) -> str:
    with open(factcheck_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    verdict = safe_key(data.get("verdict", "unknown"))
    company_id = data.get("company_id", "")
    report_file = data.get("report_file", "")
    summary = data.get("summary", {})
    checks = data.get("checks", {})
    evidence = data.get("evidence_summary", [])
    calculations = data.get("calculation_audit", [])
    claim_verdicts = data.get("claim_verdicts", [])
    if not isinstance(claim_verdicts, list):
        claim_verdicts = []
    recommendations = data.get("recommendations", [])
    verified_at = data.get("verified_at", datetime.now().isoformat())

    critical = as_count(summary.get("critical"))
    warning = as_count(summary.get("warning"))
    anomaly_count = critical + warning
    evidence_status = summary.get("company_evidence_status") or summary.get("database_status") or "unknown"
    evidence_status_label = display_label(evidence_status, EVIDENCE_STATUS_LABELS)
    evidence_total = summary.get("evidence_rows") or summary.get("local_evidence_rows") or len(evidence)
    passed_checks = sum(1 for check in checks.values() if check.get("status") == "pass")
    checked_claims = as_count(summary.get("checked_claim_count"))
    verified_claims = min(checked_claims, as_count(summary.get("verified_claim_count")))
    contradicted_claims = as_count(summary.get("contradicted_claim_count"))
    unsupported_claims = as_count(summary.get("unsupported_claim_count"))
    traceable_evidence = sum(1 for ev in evidence if evidence_location(ev) != "-")
    if checked_claims:
        claim_coverage = f"{round(verified_claims / checked_claims * 100)}%"
        verified_claim_display = str(verified_claims)
        claim_coverage_detail = f"{verified_claims}/{checked_claims} 条声明有证据支撑"
    else:
        claim_coverage = "有限核查"
        verified_claim_display = "未提供"
        claim_coverage_detail = "分析产物未提供结构化声明清单"

    css = """
:root {
  color-scheme: light;
  --page: #f5f7fa;
  --surface: #ffffff;
  --surface-soft: #f9faf7;
  --ink: #17202a;
  --muted: #667085;
  --line: #d9e0e8;
  --line-strong: #c4ccd7;
  --teal: #0f766e;
  --teal-soft: #d9f3ef;
  --blue: #26547c;
  --blue-soft: #e6eef6;
  --amber: #b45309;
  --amber-soft: #fff1d6;
  --red: #b42318;
  --red-soft: #fde4df;
  --green: #157347;
  --green-soft: #def7e8;
  --shadow: 0 18px 50px rgba(23, 32, 42, .08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.66;
}
a {
  color: #175cd3;
  font-weight: 700;
  text-decoration: none;
}
a:hover { text-decoration: underline; }
a:focus-visible {
  outline: 3px solid rgba(23, 92, 211, .28);
  outline-offset: 3px;
  border-radius: 4px;
}
.page {
  width: min(1180px, calc(100% - 40px));
  margin: 32px auto;
}
.hero {
  background: var(--surface);
  border: 1px solid var(--line);
  border-top: 6px solid var(--blue);
  box-shadow: var(--shadow);
  padding: 34px 38px 30px;
}
.eyebrow {
  margin: 0 0 8px;
  color: var(--teal);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .04em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: 38px;
  line-height: 1.18;
  letter-spacing: 0;
}
.meta-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(220px, .8fr);
  gap: 22px;
  margin-top: 22px;
}
.meta-list {
  display: grid;
  gap: 8px;
  margin: 0;
}
.meta-list div {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 10px;
}
.meta-list dt {
  color: var(--muted);
  font-weight: 700;
}
.meta-list dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.verdict-panel {
  border: 1px solid var(--line);
  border-left: 6px solid var(--blue);
  background: var(--surface-soft);
  padding: 16px 18px;
}
.verdict-panel p {
  margin: 8px 0 0;
  color: #344054;
}
.verdict-label {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 5px 12px;
  border-radius: 999px;
  font-weight: 800;
  border: 1px solid var(--line-strong);
  background: var(--blue-soft);
  color: var(--blue);
}
.verdict-approve { border-left-color: var(--green); }
.verdict-approve .verdict-label { background: var(--green-soft); color: var(--green); }
.verdict-request_changes { border-left-color: var(--amber); }
.verdict-request_changes .verdict-label { background: var(--amber-soft); color: var(--amber); }
.verdict-block { border-left-color: var(--red); }
.verdict-block .verdict-label { background: var(--red-soft); color: var(--red); }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0 0;
}
.metric {
  min-height: 104px;
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 15px 16px;
}
.metric span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}
.metric b {
  display: block;
  margin-top: 8px;
  font-size: 28px;
  line-height: 1.15;
  overflow-wrap: anywhere;
}
.metric small {
  display: block;
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
}
.metric.claims b,
.metric.coverage b { color: var(--teal); }
.metric.anomaly b { color: var(--red); }
.metric.evidence b {
  color: var(--blue);
}
.section {
  margin-top: 22px;
  padding: 28px 0 0;
  border-top: 1px solid var(--line);
}
.section-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-end;
  margin-bottom: 14px;
}
h2 {
  margin: 0;
  font-size: 22px;
  line-height: 1.25;
  letter-spacing: 0;
}
.section-note {
  margin: 5px 0 0;
  color: var(--muted);
}
.check-list {
  display: grid;
  gap: 12px;
}
.check-item {
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 5px solid var(--line-strong);
  padding: 18px 20px;
}
.check-pass { border-left-color: var(--green); }
.check-warning { border-left-color: var(--amber); }
.check-fail,
.check-blocked { border-left-color: var(--red); }
.check-topline {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
}
h3 {
  margin: 0;
  font-size: 17px;
}
.check-topline p {
  margin: 4px 0 0;
  color: var(--muted);
}
.check-badges {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  white-space: nowrap;
}
.status,
.count,
.source-type,
.severity {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  border: 1px solid var(--line-strong);
}
.status-pass { background: var(--green-soft); color: var(--green); border-color: #a8dfbd; }
.status-warning { background: var(--amber-soft); color: var(--amber); border-color: #f7c66d; }
.status-fail,
.status-blocked { background: var(--red-soft); color: var(--red); border-color: #f1aaa1; }
.status-unknown { background: var(--blue-soft); color: var(--blue); }
.count,
.source-type { background: #eef2f6; color: #344054; }
.issue-list {
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
  display: grid;
  gap: 9px;
}
.issue {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 10px;
  padding: 11px 12px;
  background: #fbfcfd;
  border: 1px solid var(--line);
}
.issue-critical .severity { background: var(--red-soft); color: var(--red); border-color: #f1aaa1; }
.issue-warning .severity { background: var(--amber-soft); color: var(--amber); border-color: #f7c66d; }
.issue-suggestion .severity { background: var(--teal-soft); color: var(--teal); border-color: #9dd8cf; }
.issue-copy { overflow-wrap: anywhere; }
.empty-inline {
  margin-top: 12px;
  color: var(--green);
  font-weight: 700;
}
.claim-list {
  display: grid;
  gap: 10px;
}
.claim-item {
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-left: 5px solid var(--line-strong);
  background: var(--surface);
}
.claim-verified { border-left-color: var(--green); }
.claim-contradicted { border-left-color: var(--red); }
.claim-unsupported { border-left-color: var(--amber); }
.claim-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.claim-topline h3 { overflow-wrap: anywhere; }
.claim-status {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 28px;
  padding: 3px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.claim-status-verified { background: var(--green-soft); color: var(--green); border-color: #a8dfbd; }
.claim-status-contradicted { background: var(--red-soft); color: var(--red); border-color: #f1aaa1; }
.claim-status-unsupported { background: var(--amber-soft); color: var(--amber); border-color: #f7c66d; }
.claim-context,
.claim-reason {
  margin: 7px 0 0;
  overflow-wrap: anywhere;
}
.claim-context { color: var(--muted); }
.key-evidence-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.key-evidence-item {
  min-width: 0;
  padding: 17px 18px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--teal);
  background: var(--surface);
}
.key-evidence-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.key-evidence-topline h3 { overflow-wrap: anywhere; }
.evidence-context,
.evidence-quote {
  margin: 7px 0 0;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.evidence-quote {
  color: #344054;
  border-left: 3px solid var(--line-strong);
  padding-left: 10px;
}
.key-value,
.evidence-value,
.evidence-row-quote,
.source-file {
  display: block;
  margin-top: 6px;
  overflow-wrap: anywhere;
}
.key-value,
.evidence-value {
  color: #344054;
  font-variant-numeric: tabular-nums;
}
.evidence-row-quote {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}
.evidence-audit {
  margin-top: 14px;
  border: 1px solid var(--line);
  background: var(--surface);
}
.evidence-audit > summary {
  min-height: 48px;
  padding: 12px 16px;
  color: #344054;
  cursor: pointer;
  font-weight: 800;
}
.evidence-audit > summary:hover { background: #f6f8fa; }
.evidence-audit > summary:focus-visible {
  outline: 3px solid rgba(23, 92, 211, .28);
  outline-offset: 3px;
}
.evidence-audit[open] > summary { border-bottom: 1px solid var(--line); }
.evidence-audit .table-wrap { border: 0; }
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  background: var(--surface);
}
table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  background: var(--surface);
}
th,
td {
  padding: 13px 14px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--line);
}
th {
  color: #344054;
  background: #eef2f6;
  font-size: 13px;
  font-weight: 800;
}
tbody tr:nth-child(even) td { background: #fbfcfd; }
tbody tr:last-child td { border-bottom: 0; }
.number {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 5px;
}
.link-row a {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
}
.source-name { display: block; }
.source-file {
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  font-weight: 400;
  white-space: normal;
}
.audit-fields {
  display: grid;
  gap: 7px;
}
.audit-field {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.audit-field > span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 700;
}
.audit-field code {
  color: #667085;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  font-weight: 400;
  overflow-wrap: anywhere;
  white-space: normal;
}
.muted,
.empty-cell,
.empty-state {
  color: var(--muted);
}
.empty-cell {
  text-align: center;
  padding: 28px;
}
.recommendations {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}
.recommendations li {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
}
.recommendations p {
  margin: 0;
}
.rec-index {
  display: inline-grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--blue);
  color: #fff;
  font-weight: 800;
  font-size: 13px;
}
.footer {
  margin-top: 26px;
  padding: 18px 0 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 900px) {
  .page { width: min(100% - 24px, 1180px); margin: 18px auto; }
  .hero { padding: 24px 20px; }
  .meta-grid { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric:last-child { grid-column: 1 / -1; }
  .section-head,
  .check-topline,
  .claim-topline { display: block; }
  .check-badges { margin-top: 10px; justify-content: flex-start; }
  .claim-status { margin-top: 8px; }
  .issue { grid-template-columns: 1fr; }
  .meta-list div { grid-template-columns: 76px minmax(0, 1fr); }
}
@media (max-width: 700px) {
  h1 { font-size: 28px; }
  .key-evidence-list { grid-template-columns: 1fr; }
  .key-evidence-topline { display: block; }
  .key-evidence-topline .source-type { margin-top: 8px; }
  .evidence-table { min-width: 0; }
  .evidence-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  .evidence-table,
  .evidence-table tbody,
  .evidence-table tr,
  .evidence-table td {
    display: block;
    width: 100%;
  }
  .evidence-table tr {
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
  }
  .evidence-table tr:last-child { border-bottom: 0; }
  .evidence-table td,
  .evidence-table tbody tr:nth-child(even) td {
    display: grid;
    grid-template-columns: minmax(104px, .34fr) minmax(0, 1fr);
    gap: 10px;
    padding: 8px 0;
    border: 0;
    background: var(--surface);
    overflow-wrap: anywhere;
  }
  .evidence-table td::before {
    content: attr(data-label);
    color: var(--muted);
    font-size: 12px;
    font-weight: 800;
  }
}
@media (max-width: 430px) {
  .page { width: min(100% - 16px, 1180px); }
  .hero { padding: 20px 16px; }
  .metric-grid { grid-template-columns: 1fr; }
  .metric:last-child { grid-column: auto; }
  .evidence-table td,
  .evidence-table tbody tr:nth-child(even) td { grid-template-columns: 1fr; gap: 4px; }
}
@media print {
  body { background: #fff; }
  .page { width: 100%; margin: 0; }
  .hero,
  .metric,
  .check-item,
  .table-wrap,
  .recommendations li {
    box-shadow: none;
    break-inside: avoid;
  }
  a { color: inherit; text-decoration: none; }
}
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>事实核查报告 - {esc(company_id)}</title>
<style>{css}</style>
</head>
<body>
<main class="page">
  <header class="hero">
    <p class="eyebrow">SIQ 事实核查 · 正式审阅</p>
    <h1>事实核查报告</h1>
    <div class="meta-grid">
      <dl class="meta-list">
        <div><dt>公司</dt><dd>{esc(company_id)}</dd></div>
        <div><dt>核查对象</dt><dd>{esc(report_file or "-")}</dd></div>
        <div><dt>核查时间</dt><dd>{esc(verified_at)}</dd></div>
      </dl>
      <aside class="verdict-panel verdict-{esc(verdict)}" aria-label="审校结论">
        <span class="verdict-label">{esc(VERDICT_LABELS.get(verdict, verdict))}</span>
        <p>{esc(VERDICT_MESSAGES.get(verdict, "核查已完成，请结合明细项判断是否可交付。"))}</p>
      </aside>
    </div>
    <div class="metric-grid" aria-label="核查摘要">
      <div class="metric claims">
        <span>已核验声明</span><b>{esc(verified_claim_display)}</b>
        <small>{esc(f'共收到 {checked_claims} 条结构化声明' if checked_claims else '仅完成数据、身份与引用层核查')}</small>
      </div>
      <div class="metric anomaly">
        <span>异常项</span><b>{esc(anomaly_count)}</b>
        <small>严重 {esc(critical)} · 警告 {esc(warning)}</small>
      </div>
      <div class="metric coverage">
        <span>声明覆盖</span><b>{esc(claim_coverage)}</b>
        <small>{esc(claim_coverage_detail)}；反证 {esc(contradicted_claims)}，无支撑 {esc(unsupported_claims)}</small>
      </div>
      <div class="metric evidence">
        <span>证据定位</span><b>{esc(traceable_evidence)}/{esc(len(evidence))}</b>
        <small>{esc(evidence_status_label)}</small>
      </div>
      <div class="metric">
        <span>通过维度</span><b>{esc(passed_checks)}/{esc(len(checks))}</b>
        <small>按事实、期间、计算、证据链与市场规则核查</small>
      </div>
    </div>
  </header>

  <section class="section" aria-labelledby="checks-title">
    <div class="section-head">
      <div>
        <h2 id="checks-title">核查维度</h2>
        <p class="section-note">按事实、计算、证据链、逻辑、风险与模板规则逐项审校。</p>
      </div>
    </div>
    <div class="check-list">{render_check_items(checks)}</div>
  </section>

  <section class="section" aria-labelledby="claims-title">
    <div class="section-head">
      <div>
        <h2 id="claims-title">逐条声明核验</h2>
        <p class="section-note">逐条展示声明、核验状态及判定理由；反证与证据不足项必须先修订。</p>
      </div>
    </div>
    <div class="claim-list">{render_claim_verdicts(claim_verdicts)}</div>
  </section>

  <section class="section" aria-labelledby="evidence-title">
    <div class="section-head">
      <div>
        <h2 id="evidence-title">关键核验依据</h2>
        <p class="section-note">共识别 {esc(evidence_total)} 条证据；优先展示最多 {esc(KEY_EVIDENCE_LIMIT)} 条可读定位，完整审计清单默认收起。</p>
      </div>
    </div>
    <div class="key-evidence-list">{render_key_evidence(evidence)}</div>
    <details class="evidence-audit">
      <summary>完整证据审计清单（{esc(len(evidence))} 条）</summary>
      <div class="table-wrap">
        <table class="evidence-table" aria-label="完整证据审计清单">
          <thead>
            <tr><th>指标或声明</th><th>来源类型</th><th>期间与定位</th><th>来源与回跳</th><th>审计字段</th></tr>
          </thead>
          <tbody>{render_evidence_rows(evidence)}</tbody>
        </table>
      </div>
    </details>
  </section>

  <section class="section" aria-labelledby="calc-title">
    <div class="section-head">
      <div>
        <h2 id="calc-title">公式重算</h2>
        <p class="section-note">对可结构化识别的财务公式进行复算并标记差异。</p>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>项目</th><th>状态</th><th>重算值</th><th>报告值</th><th>差异</th><th>证据数</th></tr>
        </thead>
        <tbody>{render_calc_rows(calculations)}</tbody>
      </table>
    </div>
  </section>

  <section class="section" aria-labelledby="rec-title">
    <div class="section-head">
      <div>
        <h2 id="rec-title">优先修改建议</h2>
        <p class="section-note">建议先处理阻断项和 Warning，再进入正式发布。</p>
      </div>
    </div>
    {render_recommendations(recommendations)}
  </section>

  <footer class="footer">SIQ_factchecker v2.0 · 静态 HTML 审阅报告 · 生成源: {esc(Path(factcheck_path).name)}</footer>
</main>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("用法: python generate_factcheck_html.py <factcheck_json_path> [output_html_path]")
        sys.exit(1)
    factcheck_path = sys.argv[1]
    if not os.path.exists(factcheck_path):
        print(f"错误: 文件不存在 - {factcheck_path}")
        sys.exit(1)
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        source = Path(factcheck_path)
        output_path = str(source.with_suffix(".html"))
        if source.parent.name == "analysis" and source.name.endswith("-factcheck.json"):
            target_dir = source.parent.parent / "factcheck"
            target_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(target_dir / source.with_suffix(".html").name)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(generate_html(factcheck_path))
    print(f"HTML报告已生成: {output_path}")


if __name__ == "__main__":
    main()
