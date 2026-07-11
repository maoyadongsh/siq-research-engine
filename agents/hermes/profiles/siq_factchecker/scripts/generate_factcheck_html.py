#!/usr/bin/env python3
"""Generate an HTML view for SIQ_factchecker v2 JSON."""

import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

CHECK_NAMES = {
    "data_consistency": "数据原文一致性",
    "calculation_consistency": "计算公式一致性",
    "traceability": "证据链完整性",
    "logic_support": "结论支撑充分性",
    "a_share_risk_completeness": "A股风险完整性",
    "template_compliance": "模板与规则合规性",
}

CHECK_DESCRIPTIONS = {
    "data_consistency": "核对报告关键数据是否能回到年报、解析表或结构化证据。",
    "calculation_consistency": "复算核心公式，识别口径、符号或单位错误。",
    "traceability": "检查重要结论是否带有可回跳证据与原始页定位。",
    "logic_support": "判断投资结论、风险判断与数据证据是否匹配。",
    "a_share_risk_completeness": "覆盖 A 股常见风险、会计口径与监管表达要求。",
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

SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "suggestion": "Suggestion",
    "info": "Info",
    "issue": "Issue",
}

EVIDENCE_STATUS_LABELS = {
    "available": "可用",
    "unavailable": "不可用",
    "local_wiki_available": "本地证据可用",
    "local-wiki-available": "本地证据可用",
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
    "wiki_evidence": "本地证据",
    "verified": "已验证",
    "calculated": "已计算",
    "none": "无",
    "unknown": "未知",
}


def esc(value):
    return html.escape(str(value), quote=True)


def safe_key(value, fallback="unknown"):
    raw = str(value or fallback).strip().lower()
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "-" for ch in raw) or fallback


def issue_text(issue):
    if isinstance(issue, dict):
        parts = [issue.get("message", "")]
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
    return str(issue)


def evidence_links(ev):
    links = []
    for label, key in [
        ("PDF", "open_pdf_page_url"),
        ("原文", "open_source_page_url"),
        ("表格", "open_source_table_url"),
    ]:
        url = str(ev.get(key) or "").strip()
        if url:
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>')
    return " / ".join(links)


def evidence_value(ev):
    raw = ev.get("raw_value")
    value = raw if raw not in (None, "") else ev.get("value", "")
    unit = str(ev.get("unit") or "").strip()
    if value in (None, ""):
        return "-"
    return f"{value} {unit}".strip()


def evidence_location(ev):
    parts = []
    pdf_page = ev.get("pdf_page_number") or ev.get("pdf_page")
    table_index = ev.get("table_index")
    md_line = ev.get("markdown_line") or ev.get("md_line")
    if pdf_page not in (None, ""):
        parts.append(f"PDF {pdf_page}")
    if table_index not in (None, ""):
        parts.append(f"表 {table_index}")
    if md_line not in (None, ""):
        parts.append(f"MD {md_line}")
    return " · ".join(parts) or "-"


def display_label(value, labels):
    text = str(value or "unknown").strip()
    key = safe_key(text)
    return labels.get(key, text or labels.get("unknown", "未知"))


def metric_name(ev):
    return (
        ev.get("item_name")
        or ev.get("metric")
        or ev.get("metric_or_claim")
        or ev.get("canonical_name")
        or ev.get("message")
        or "-"
    )


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


def render_evidence_rows(evidence):
    rows = []
    for ev in evidence[:30]:
        source_type = ev.get("statement_type") or ev.get("source_type") or ev.get("status") or "-"
        source = ev.get("file") or ev.get("source") or ""
        links = evidence_links(ev)
        source_html = esc(source) if source else '<span class="muted">-</span>'
        if links:
            source_html = f"{source_html}<div class=\"link-row\">{links}</div>"
        rows.append(
            f"""
            <tr>
              <td><span class="source-type">{esc(display_label(source_type, SOURCE_TYPE_LABELS))}</span></td>
              <td>{esc(metric_name(ev))}</td>
              <td class="number">{esc(evidence_value(ev))}</td>
              <td>{esc(evidence_location(ev))}</td>
              <td>{source_html}</td>
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
        items.append(
            f"""
            <li>
              <span class="rec-index">{index}</span>
              <p>{esc(rec)}</p>
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
    recommendations = data.get("recommendations", [])
    verified_at = data.get("verified_at", datetime.now().isoformat())

    critical = summary.get("critical", 0)
    warning = summary.get("warning", 0)
    suggestion = summary.get("suggestion", 0)
    evidence_status = summary.get("company_evidence_status") or summary.get("database_status") or "unknown"
    evidence_status_label = display_label(evidence_status, EVIDENCE_STATUS_LABELS)
    evidence_total = summary.get("evidence_rows") or summary.get("local_evidence_rows") or len(evidence)
    passed_checks = sum(1 for check in checks.values() if check.get("status") == "pass")
    shown_evidence = min(len(evidence), 30)

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
  font-size: clamp(28px, 4vw, 42px);
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
.metric.critical b { color: var(--red); }
.metric.warning b { color: var(--amber); }
.metric.suggestion b { color: var(--teal); }
.metric.evidence b {
  color: var(--blue);
  font-size: 20px;
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
  .metric.evidence { grid-column: 1 / -1; }
  .section-head,
  .check-topline { display: block; }
  .check-badges { margin-top: 10px; justify-content: flex-start; }
  .issue { grid-template-columns: 1fr; }
  .meta-list div { grid-template-columns: 76px minmax(0, 1fr); }
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
    <p class="eyebrow">SIQ Factchecker · Formal Review</p>
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
      <div class="metric critical"><span>Critical</span><b>{esc(critical)}</b></div>
      <div class="metric warning"><span>Warning</span><b>{esc(warning)}</b></div>
      <div class="metric suggestion"><span>Suggestion</span><b>{esc(suggestion)}</b></div>
      <div class="metric"><span>通过维度</span><b>{esc(passed_checks)}/{esc(len(checks))}</b></div>
      <div class="metric evidence"><span>证据状态</span><b>{esc(evidence_status_label)}</b></div>
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

  <section class="section" aria-labelledby="evidence-title">
    <div class="section-head">
      <div>
        <h2 id="evidence-title">证据摘要</h2>
        <p class="section-note">共识别 {esc(evidence_total)} 条证据；当前展示 {esc(shown_evidence)} 条关键定位。</p>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>来源类型</th><th>项目</th><th>值</th><th>定位</th><th>来源与回跳</th></tr>
        </thead>
        <tbody>{render_evidence_rows(evidence)}</tbody>
      </table>
    </div>
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
