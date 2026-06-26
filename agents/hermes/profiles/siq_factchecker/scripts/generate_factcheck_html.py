#!/usr/bin/env python3
"""Generate an HTML view for SIQ_factchecker v2 JSON."""

import html
import json
import os
import sys
from pathlib import Path
from datetime import datetime

CHECK_NAMES = {
    'data_consistency': '数据原文一致性',
    'calculation_consistency': '计算公式一致性',
    'traceability': '证据链完整性',
    'logic_support': '结论支撑充分性',
    'a_share_risk_completeness': 'A股风险完整性',
    'template_compliance': '模板与规则合规性',
}


def esc(value):
    return html.escape(str(value), quote=True)


def issue_text(issue):
    if isinstance(issue, dict):
        parts = [issue.get('message', '')]
        if issue.get('expected'):
            parts.append(f"期望: {issue['expected']}")
        if issue.get('actual'):
            parts.append(f"实际: {issue['actual']}")
        refs = issue.get('evidence_refs', [])
        if refs:
            parts.append(f"证据: {len(refs)}条")
        elif 'evidence_refs' in issue:
            parts.append("证据: 缺失/不适用")
        return '；'.join(str(p) for p in parts if p)
    return str(issue)


def generate_html(factcheck_path: str) -> str:
    with open(factcheck_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    verdict = data.get('verdict', 'unknown')
    company_id = data.get('company_id', '')
    report_file = data.get('report_file', '')
    summary = data.get('summary', {})
    verified_at = data.get('verified_at', datetime.now().isoformat())
    verdict_colors = {'approve': '#dcfce7', 'request_changes': '#fef3c7', 'block': '#fee2e2'}
    verdict_labels = {'approve': '通过', 'request_changes': '需修改', 'block': '阻断'}
    verdict_color = verdict_colors.get(verdict, '#475569')

    rows = []
    for key, check in data.get('checks', {}).items():
        status = check.get('status', 'unknown')
        issues = check.get('issues', [])
        status_color = {'pass': '#dcfce7', 'warning': '#fef3c7', 'fail': '#fee2e2'}.get(status, '#e2e8f0')
        if issues:
            issues_html = '<ul>' + ''.join(
                f"<li><strong>{esc(i.get('severity', 'issue') if isinstance(i, dict) else 'issue')}</strong>: {esc(issue_text(i))}</li>"
                for i in issues
            ) + '</ul>'
        else:
            issues_html = '<span class="ok">无问题</span>'
        rows.append(f'''
        <tr>
          <td>{esc(CHECK_NAMES.get(key, key))}</td>
          <td><span class="pill" style="background:{status_color}">{esc(status)}</span></td>
          <td>{len(issues)}</td>
          <td>{issues_html}</td>
        </tr>''')

    evidence_rows = []
    for ev in data.get('evidence_summary', [])[:20]:
        metric = ev.get('item_name') or ev.get('metric') or ev.get('metric_or_claim') or ev.get('canonical_name') or ev.get('message', '')
        source_type = ev.get('statement_type') or ev.get('source_type') or ev.get('status', '')
        pdf_page = ev.get('pdf_page_number') or ev.get('pdf_page') or ''
        md_line = ev.get('markdown_line') or ev.get('md_line') or ''
        evidence_rows.append(f'''
        <tr>
          <td>{esc(source_type)}</td>
          <td>{esc(metric)}</td>
          <td>{esc(ev.get('value', ''))}</td>
          <td>{esc(pdf_page)}</td>
          <td>{esc(ev.get('table_index', ''))}</td>
          <td>{esc(md_line)}</td>
        </tr>''')
    evidence_html = ''.join(evidence_rows) or '<tr><td colspan="6" class="muted">无可用证据摘要</td></tr>'

    recs = data.get('recommendations', [])
    rec_html = ''.join(f'<li>{esc(rec)}</li>' for rec in recs) or '<li class="muted">暂无优先修改建议</li>'

    calc_rows = []
    for calc in data.get('calculation_audit', []):
        refs = calc.get('evidence_refs', [])
        calc_rows.append(f'''
        <tr>
          <td>{esc(calc.get('name', ''))}</td>
          <td>{esc(calc.get('status', ''))}</td>
          <td>{esc(calc.get('recomputed_value', ''))} {esc(calc.get('unit', ''))}</td>
          <td>{esc(calc.get('reported_value', '未识别'))}</td>
          <td>{esc(calc.get('delta', ''))}</td>
          <td>{esc(len(refs))}</td>
        </tr>''')
    calc_html = ''.join(calc_rows) or '<tr><td colspan="6" class="muted">暂无可自动重算项目</td></tr>'

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>事实核查报告 - {esc(company_id)}</title>
<style>
body {{ margin:0; background:#f6f8fb; color:#111827; font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",Arial,sans-serif; line-height:1.68; }}
.container {{ max-width:1180px; margin:32px auto; background:#ffffff; border:1px solid #e2e8f0; border-radius:22px; box-shadow:0 24px 70px rgba(15,23,42,.08); overflow:hidden; }}
.header {{ padding:36px 42px; background:linear-gradient(135deg,#ffffff 0%,#f5f9ff 48%,#eaf4ff 100%); color:#0f172a; border-bottom:1px solid #dbeafe; }}
h1 {{ margin:0 0 8px; font-size:30px; color:#0f172a; letter-spacing:0; }}
.meta {{ color:#475569; }}
.summary {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; padding:24px 42px; background:#f8fafc; border-bottom:1px solid #e2e8f0; }}
.card {{ padding:16px; background:white; border:1px solid #e2e8f0; border-radius:14px; box-shadow:0 10px 30px rgba(15,23,42,.04); color:#334155; }}
.card b {{ display:block; font-size:24px; margin-top:6px; }}
.verdict {{ color:#0f172a; background:{verdict_color}; border:1px solid rgba(15,23,42,.08); border-radius:999px; padding:7px 14px; display:inline-block; font-weight:700; }}
.section {{ padding:28px 42px; border-bottom:1px solid #e2e8f0; }}
h2 {{ margin:0 0 16px; font-size:20px; color:#0f172a; }}
table {{ width:100%; border-collapse:collapse; background:white; }}
th, td {{ border-bottom:1px solid #e2e8f0; padding:12px; text-align:left; vertical-align:top; color:#1f2937; }}
th {{ background:#f1f5f9; color:#0f172a; }}
ul {{ margin:0; padding-left:18px; }}
.pill {{ color:#0f172a; border:1px solid rgba(15,23,42,.08); border-radius:999px; padding:4px 10px; font-size:12px; font-weight:700; }}
.ok {{ color:#15803d; }}
.muted {{ color:#64748b; }}
.footer {{ padding:18px 42px; color:#64748b; font-size:13px; background:#f8fafc; }}
@media (max-width:800px) {{ .summary {{ grid-template-columns:1fr 1fr; padding:18px; }} .section,.header,.footer {{ padding:22px; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>事实核查报告</h1>
    <div class="meta">{esc(company_id)} | {esc(report_file)}</div>
  </div>
  <div class="summary">
    <div class="card">审校结论<br><b><span class="verdict">{esc(verdict_labels.get(verdict, verdict))}</span></b></div>
    <div class="card">Critical<b>{esc(summary.get('critical', 0))}</b></div>
    <div class="card">Warning<b>{esc(summary.get('warning', 0))}</b></div>
    <div class="card">Suggestion<b>{esc(summary.get('suggestion', 0))}</b></div>
    <div class="card">证据命中<b>{esc(summary.get('company_evidence_status', summary.get('database_status', 'unknown')))}</b></div>
  </div>
  <div class="section">
    <h2>核查维度</h2>
    <table><thead><tr><th>维度</th><th>状态</th><th>问题数</th><th>问题详情</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
  </div>
  <div class="section">
    <h2>证据摘要</h2>
    <table><thead><tr><th>来源</th><th>项目</th><th>值</th><th>PDF页</th><th>表格</th><th>Markdown行</th></tr></thead><tbody>{evidence_html}</tbody></table>
  </div>
  <div class="section">
    <h2>公式重算</h2>
    <table><thead><tr><th>项目</th><th>状态</th><th>重算值</th><th>报告值</th><th>差异</th><th>证据数</th></tr></thead><tbody>{calc_html}</tbody></table>
  </div>
  <div class="section">
    <h2>优先修改建议</h2>
    <ul>{rec_html}</ul>
  </div>
  <div class="footer">核查时间: {esc(verified_at)} | SIQ_factchecker v2.0</div>
</div>
</body>
</html>'''


def main():
    if len(sys.argv) < 2:
        print('用法: python generate_factcheck_html.py <factcheck_json_path> [output_html_path]')
        sys.exit(1)
    factcheck_path = sys.argv[1]
    if not os.path.exists(factcheck_path):
        print(f'错误: 文件不存在 - {factcheck_path}')
        sys.exit(1)
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        source = Path(factcheck_path)
        output_path = str(source.with_suffix('.html'))
        if source.parent.name == 'analysis' and source.name.endswith('-factcheck.json'):
            target_dir = source.parent.parent / 'factcheck'
            target_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(target_dir / source.with_suffix('.html').name)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(generate_html(factcheck_path))
    print(f'HTML报告已生成: {output_path}')


if __name__ == '__main__':
    main()
