#!/usr/bin/env python3
"""
模块6: 合并HTML报告生成器
将五大模块的输出合并为单个HTML文件。
命名格式: <stock_code>-<company_short_name>-跟踪报告-<date>.html

样式规则（已固化）：
- 背景色：白色 (#ffffff)，确保打印和屏幕阅读的高可读性
- 文本色：深灰 (#1f2328)，对比度符合 WCAG AA 标准
- 辅助色：GitHub Light 主题色系，蓝色 #0969da、红色 #cf222e 等
- 禁止：深色/暗色主题，避免在白色背景上使用低对比度文字
"""

import os
import re
import sys
import html as html_lib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

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

from company_identity import company_dir_path, generated_report_filename
from finsight_tracking_rules import write_report_manifest


def esc(value) -> str:
    return html_lib.escape(str(value), quote=True)


def file_badge(files: List[str], empty: str = "暂无") -> str:
    return esc(files[0] if files else empty)


def compact_text(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def markdown_to_html(md: str) -> str:
    """简易 Markdown 转 HTML"""
    html = md

    # 转义HTML特殊字符
    html = html.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 代码块
    html = re.sub(r'```json\n(.*?)```', r'<pre class="code-block json"><code>\1</code></pre>', html, flags=re.DOTALL)
    html = re.sub(r'```\n(.*?)```', r'<pre class="code-block"><code>\1</code></pre>', html, flags=re.DOTALL)

    # 标题
    html = re.sub(r'^###### (.*?)$', r'<h6>\1</h6>', html, flags=re.MULTILINE)
    html = re.sub(r'^##### (.*?)$', r'<h5>\1</h5>', html, flags=re.MULTILINE)
    html = re.sub(r'^#### (.*?)$', r'<h4>\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

    # 粗体/斜体
    html = re.sub(r'\*\*\*(.*?)\*\*\*', r'<strong><em>\1</em></strong>', html)
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)

    # 行内代码
    html = re.sub(r'`(.*?)`', r'<code>\1</code>', html)

    # 链接
    html = re.sub(
        r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        html,
    )

    # 引用块
    lines = html.split('\n')
    result = []
    in_quote = False
    for line in lines:
        if line.startswith('> '):
            if not in_quote:
                result.append('<blockquote>')
                in_quote = True
            result.append(line[2:] + '<br>')
        else:
            if in_quote:
                result.append('</blockquote>')
                in_quote = False
            result.append(line)
    if in_quote:
        result.append('</blockquote>')
    html = '\n'.join(result)

    # 表格
    lines = html.split('\n')
    result = []
    in_table = False
    pending_header = None
    for line in lines:
        if '|' in line and not line.strip().startswith('<'):
            cells = [c.strip() for c in line.split('|') if c.strip()]
            if cells and all(c.replace('-', '').replace(':', '') == '' for c in cells):
                if pending_header is not None:
                    result.append('<table>')
                    result.append('<thead><tr><th>' + '</th><th>'.join(pending_header) + '</th></tr></thead>')
                    result.append('<tbody>')
                    in_table = True
                    pending_header = None
                continue
            if not in_table:
                pending_header = cells
                continue
            result.append('<tr><td>' + '</td><td>'.join(cells) + '</td></tr>')
        else:
            if pending_header is not None:
                result.append('<table>')
                result.append('<tbody><tr><td>' + '</td><td>'.join(pending_header) + '</td></tr>')
                in_table = True
                pending_header = None
            if in_table:
                result.append('</tbody></table>')
                in_table = False
            result.append(line)
    if in_table:
        result.append('</tbody></table>')
    elif pending_header is not None:
        result.append('<table><tbody><tr><td>' + '</td><td>'.join(pending_header) + '</td></tr></tbody></table>')
    html = '\n'.join(result)

    # 列表
    lines = html.split('\n')
    result = []
    in_ul = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_ul:
                result.append('<ul>')
                in_ul = True
            content = stripped[2:]
            result.append(f'<li>{content}</li>')
        else:
            if in_ul and stripped and not stripped.startswith('<'):
                result.append('</ul>')
                in_ul = False
            result.append(line)
    if in_ul:
        result.append('</ul>')
    html = '\n'.join(result)

    # 水平线
    html = re.sub(r'^---+$', '<hr>', html, flags=re.MULTILINE)

    # 段落（简单处理：空行分隔）
    paragraphs = html.split('\n\n')
    new_paragraphs = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith('<') and not p.startswith('<blockquote>'):
            new_paragraphs.append(p)
        else:
            new_paragraphs.append(f'<p>{p}</p>')
    html = '\n\n'.join(new_paragraphs)

    return html


def read_markdown_file(path: str) -> str:
    """读取 Markdown 文件内容"""
    if not path or not os.path.exists(path):
        return "<p><em>文件不存在或暂不可用</em></p>"
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def parse_tracking_items(content: str) -> Dict:
    """解析 tracking-items.md 为结构化数据"""
    items = {
        "summary": {},
        "high_priority": [],
        "all_items": []
    }

    # 提取分类汇总
    summary_match = re.search(r'## 分类汇总\n\n(.*?)(?=\n## )', content, re.DOTALL)
    if summary_match:
        summary_text = summary_match.group(1)
        for line in summary_text.split('\n'):
            match = re.search(r'\*\*(.*?)\*\*:\s*(\d+)\s*项', line)
            if match:
                items["summary"][match.group(1)] = int(match.group(2))

    # 提取高优先级事项
    item_blocks = re.split(r'\n---\n', content)
    for block in item_blocks:
        if '🔴' in block or 'high' in block:
            title_match = re.search(r'### .*?\| (.*?)(?:\n|$)', block)
            desc_match = re.search(r'\*\*描述\*\*:\s*(.*?)(?:\n|$)', block)
            if title_match and desc_match:
                items["high_priority"].append({
                    "title": title_match.group(1).strip(),
                    "description": desc_match.group(1).strip()
                })

    return items


def generate_html_report(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    report_date: str = None,
) -> str:
    """
    生成合并HTML报告

    命名格式: <stock_code>-<company_short_name>-跟踪报告-<date>.html
    """
    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    tracking_dir = os.path.join(company_dir, "tracking")

    # 读取各模块数据
    tracking_items_path = os.path.join(tracking_dir, "tracking-items.md")
    sentiment_dir = os.path.join(tracking_dir, "sentiment")
    metrics_dir = os.path.join(tracking_dir, "metrics")
    alerts_dir = os.path.join(tracking_dir, "alerts")
    updates_dir = os.path.join(tracking_dir, "updates")

    # 读取 tracking-items
    tracking_items_content = read_markdown_file(tracking_items_path)
    tracking_items_html = markdown_to_html(tracking_items_content)
    tracking_items_data = parse_tracking_items(tracking_items_content)

    # 读取最新舆情
    sentiment_files = sorted([f for f in os.listdir(sentiment_dir) if f.endswith('.md')], reverse=True) if os.path.exists(sentiment_dir) else []
    sentiment_content = read_markdown_file(os.path.join(sentiment_dir, sentiment_files[0])) if sentiment_files else ""
    sentiment_html = markdown_to_html(sentiment_content) if sentiment_content else "<p><em>暂无舆情数据</em></p>"

    # 读取最新指标
    metrics_files = sorted([f for f in os.listdir(metrics_dir) if f.endswith('.md')], reverse=True) if os.path.exists(metrics_dir) else []
    metrics_content = read_markdown_file(os.path.join(metrics_dir, metrics_files[0])) if metrics_files else ""
    metrics_html = markdown_to_html(metrics_content) if metrics_content else "<p><em>暂无指标数据</em></p>"

    # 读取最新预警
    alert_files = sorted([f for f in os.listdir(alerts_dir) if f.endswith('.md')], reverse=True) if os.path.exists(alerts_dir) else []
    alerts_content = read_markdown_file(os.path.join(alerts_dir, alert_files[0])) if alert_files else ""
    alerts_html = markdown_to_html(alerts_content) if alerts_content else "<p><em>当前无活跃预警</em></p>"

    # 读取最新更新记录
    update_files = sorted([f for f in os.listdir(updates_dir) if f.endswith('.md') and not os.path.isdir(os.path.join(updates_dir, f))], reverse=True) if os.path.exists(updates_dir) else []
    update_content = read_markdown_file(os.path.join(updates_dir, update_files[0])) if update_files else ""
    update_html = markdown_to_html(update_content) if update_content else ""

    # 计算统计
    total_items = sum(tracking_items_data.get("summary", {}).values())
    high_priority_count = len(tracking_items_data.get("high_priority", []))
    alert_count = len(alert_files)

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if alert_count > 0:
        status_tone = "alert"
        status_label = "需要优先跟进"
        status_message = f"当前存在 {alert_count} 条活跃预警，请优先核对触发依据、证据来源和后续验证动作。"
    elif high_priority_count > 0:
        status_tone = "watch"
        status_label = "重点观察"
        status_message = f"当前有 {high_priority_count} 个高优先级跟踪事项，建议按到期日和证据完整性推进复核。"
    else:
        status_tone = "steady"
        status_label = "常规跟踪"
        status_message = "未识别活跃预警或高优先级事项，按既定频率持续观察财务指标、公告和舆情变化。"

    attention_items = tracking_items_data.get("high_priority", [])[:3]
    if attention_items:
        attention_html = "".join(
            f"""
            <article class="attention-item">
              <span class="priority-chip">High</span>
              <h3>{esc(compact_text(item.get("title", "高优先级事项"), 70))}</h3>
              <p>{esc(compact_text(item.get("description", ""), 170))}</p>
            </article>"""
            for item in attention_items
        )
    else:
        attention_html = '<div class="empty-state">当前没有高优先级跟踪事项。</div>'

    css = """
:root {
  color-scheme: light;
  --bg-primary: #ffffff;
  --bg-readable: #fafafa;
  --page: #f5f7fa;
  --surface: #ffffff;
  --surface-soft: #f9fbfd;
  --surface-warm: #fff8ed;
  --line: #d9e0e8;
  --line-strong: #c4ccd7;
  --text-primary: #17202a;
  --text-secondary: #4b5563;
  --text-muted: #667085;
  --accent-blue: #26547c;
  --accent-teal: #0f766e;
  --accent-green: #157347;
  --accent-yellow: #9a6700;
  --accent-orange: #b45309;
  --accent-red: #b42318;
  --accent-purple: #6f4aa8;
  --blue-soft: #e6eef6;
  --teal-soft: #d9f3ef;
  --green-soft: #def7e8;
  --amber-soft: #fff1d6;
  --red-soft: #fde4df;
  --purple-soft: #eee8fb;
  --shadow: 0 18px 50px rgba(23, 32, 42, .08);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--page);
  background-color: #ffffff;
  color: var(--text-primary);
  font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.66;
}
a {
  color: #175cd3;
  font-weight: 700;
  text-decoration: none;
}
a:hover { text-decoration: underline; }
a:focus-visible,
button:focus-visible {
  outline: 3px solid rgba(23, 92, 211, .26);
  outline-offset: 3px;
}
.container,
.page {
  width: min(1180px, calc(100% - 40px));
  margin: 32px auto;
}
.hero {
  background: linear-gradient(135deg, #ffffff 0%, #f0f7ff 55%, #fff8ed 100%);
  border: 1px solid var(--line);
  border-top: 6px solid var(--accent-blue);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 34px 38px 30px;
}
.eyebrow {
  margin: 0 0 8px;
  color: var(--accent-teal);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0;
}
h1 {
  margin: 0;
  font-size: clamp(28px, 4vw, 42px);
  line-height: 1.18;
  letter-spacing: 0;
}
.hero-subtitle {
  margin: 8px 0 0;
  color: var(--text-secondary);
}
.meta-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(260px, .8fr);
  gap: 22px;
  margin-top: 24px;
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
  color: var(--text-muted);
  font-weight: 700;
}
.meta-list dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.status-panel {
  border: 1px solid var(--line);
  border-left: 6px solid var(--accent-blue);
  border-radius: 8px;
  background: var(--surface);
  padding: 16px 18px;
}
.status-panel p {
  margin: 8px 0 0;
  color: var(--text-secondary);
}
.status-label,
.badge,
.priority-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  font-size: 12px;
  font-weight: 800;
}
.status-label { min-height: 34px; font-size: 13px; }
.status-alert { border-left-color: var(--accent-red); }
.status-alert .status-label,
.badge.alert,
.priority-chip { background: var(--red-soft); color: var(--accent-red); border-color: #f1aaa1; }
.status-watch { border-left-color: var(--accent-orange); }
.status-watch .status-label,
.badge.warning { background: var(--amber-soft); color: var(--accent-orange); border-color: #f7c66d; }
.status-steady { border-left-color: var(--accent-green); }
.status-steady .status-label { background: var(--green-soft); color: var(--accent-green); border-color: #a8dfbd; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-top: 22px;
}
.stat-card {
  min-height: 108px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 15px 16px;
}
.stat-card span {
  display: block;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 700;
}
.stat-card b,
.stat-card .number {
  display: block;
  margin-top: 8px;
  font-size: 30px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.stat-card .label { color: var(--text-muted); font-size: 13px; }
.stat-card.blue b, .stat-card.blue .number { color: var(--accent-blue); }
.stat-card.red b, .stat-card.red .number { color: var(--accent-red); }
.stat-card.yellow b, .stat-card.yellow .number { color: var(--accent-yellow); }
.stat-card.green b, .stat-card.green .number { color: var(--accent-green); }
.stat-card.purple b, .stat-card.purple .number { color: var(--accent-purple); }
.report-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 18px 0 0;
}
.report-nav a {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 5px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, .74);
  color: var(--accent-blue);
  font-size: 13px;
}
.attention-panel {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.section-note,
.attention-panel > p {
  margin: 5px 0 0;
  color: var(--text-muted);
}
.attention-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.attention-item {
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 5px solid var(--accent-red);
  border-radius: 8px;
  padding: 16px;
}
.attention-item h3 {
  margin: 10px 0 6px;
  font-size: 16px;
  line-height: 1.35;
}
.attention-item p {
  margin: 0;
  color: var(--text-secondary);
}
.report-section,
.section {
  margin-top: 22px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.section-header {
  width: 100%;
  appearance: none;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: #f8fafc;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 22px;
  text-align: left;
  cursor: pointer;
}
.section-header:hover { background: #eef4fb; }
.section-title-wrap {
  min-width: 0;
}
.section-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--accent-teal);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
}
.section-header h2 {
  margin: 0;
  font-size: 21px;
  line-height: 1.25;
  letter-spacing: 0;
}
.section-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.badge { background: #eef2f6; color: #344054; }
.section-content {
  padding: 24px 26px 28px;
  overflow-x: auto;
}
.section-content.collapsed { display: none; }
.section-content h1 {
  margin: 0 0 18px;
  font-size: 24px;
  line-height: 1.25;
}
.section-content h2 {
  margin: 26px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
  color: var(--accent-blue);
  font-size: 19px;
}
.section-content h3 {
  margin: 20px 0 10px;
  font-size: 17px;
  line-height: 1.35;
}
.section-content h4,
.section-content h5,
.section-content h6 {
  margin: 16px 0 8px;
}
.section-content p {
  margin: 0 0 12px;
  color: var(--text-secondary);
}
.section-content strong { color: var(--text-primary); }
.section-content blockquote {
  margin: 14px 0;
  padding: 12px 16px;
  border-left: 4px solid var(--accent-blue);
  background: var(--surface-soft);
  color: var(--text-secondary);
}
.section-content ul,
.section-content ol {
  margin: 12px 0 12px 22px;
  color: var(--text-secondary);
}
.section-content li { margin-bottom: 7px; }
.section-content table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  margin: 16px 0;
  background: var(--surface);
  font-size: 14px;
}
.section-content th,
.section-content td {
  padding: 12px 14px;
  text-align: left;
  vertical-align: top;
  border: 1px solid var(--line);
}
.section-content th {
  background: #eef2f6;
  color: #344054;
  font-weight: 800;
}
.section-content tbody tr:nth-child(even) td { background: #fbfcfd; }
.section-content td {
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}
.section-content code {
  background: #eef2f6;
  border-radius: 4px;
  padding: 2px 6px;
  color: var(--accent-purple);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}
.section-content pre {
  max-height: 420px;
  overflow: auto;
  margin: 14px 0;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfd;
}
.section-content pre code {
  background: transparent;
  padding: 0;
  color: var(--text-secondary);
}
.section-content hr {
  border: 0;
  border-top: 1px solid var(--line);
  margin: 24px 0;
}
.priority-high,
.priority-medium,
.priority-low {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.priority-high { background: var(--red-soft); color: var(--accent-red); }
.priority-medium { background: var(--amber-soft); color: var(--accent-orange); }
.priority-low { background: var(--blue-soft); color: var(--accent-blue); }
.empty-state {
  color: var(--text-muted);
  padding: 18px;
  border: 1px dashed var(--line-strong);
  border-radius: 8px;
  background: var(--bg-readable);
}
.toggle-arrow {
  color: var(--text-muted);
  font-size: 14px;
  transition: transform .2s ease;
}
.toggle-arrow.collapsed { transform: rotate(-90deg); }
.footer {
  margin-top: 26px;
  padding: 18px 0 0;
  border-top: 1px solid var(--line);
  color: var(--text-muted);
  font-size: 13px;
}
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg-primary); }
::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 4px; }
@media (max-width: 900px) {
  .page,
  .container {
    width: min(100% - 24px, 1180px);
    margin: 18px auto;
  }
  .hero { padding: 24px 20px; }
  .meta-grid { grid-template-columns: 1fr; }
  .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-card.purple { grid-column: 1 / -1; }
  .attention-grid { grid-template-columns: 1fr; }
  .section-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .section-actions {
    width: 100%;
    justify-content: space-between;
  }
  .section-content { padding: 18px 16px 22px; }
  .meta-list div { grid-template-columns: 76px minmax(0, 1fr); }
}
@media print {
  body { background: #ffffff; }
  .page { width: 100%; margin: 0; }
  .hero,
  .stat-card,
  .attention-item,
  .report-section,
  .section {
    box-shadow: none;
    break-inside: avoid;
  }
  .report-nav,
  .toggle-arrow { display: none; }
  a { color: inherit; text-decoration: none; }
}
"""

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(company_name)} ({esc(stock_code)}) 跟踪报告 - {esc(report_date)}</title>
    <style>{css}</style>
</head>
<body>
    <main class="page">
        <header class="hero">
            <p class="eyebrow">SIQ Tracking · Continuous Monitoring</p>
            <h1>{esc(company_name)} 持续跟踪报告</h1>
            <p class="hero-subtitle">围绕上游分析和事实核查结论，持续观察财务指标、公告舆情、预警状态和研究假设变化。</p>
            <div class="meta-grid">
                <dl class="meta-list">
                    <div><dt>股票代码</dt><dd>{esc(stock_code)}</dd></div>
                    <div><dt>公司名称</dt><dd>{esc(company_name)}</dd></div>
                    <div><dt>报告日期</dt><dd>{esc(report_date)}</dd></div>
                    <div><dt>生成时间</dt><dd>{esc(generated_at)}</dd></div>
                    <div><dt>生成来源</dt><dd>finsight_tracking 自动流水线</dd></div>
                </dl>
                <aside class="status-panel status-{esc(status_tone)}" aria-label="跟踪状态">
                    <span class="status-label">{esc(status_label)}</span>
                    <p>{esc(status_message)}</p>
                </aside>
            </div>
            <div class="stats-grid" aria-label="跟踪摘要">
                <div class="stat-card blue"><span>跟踪事项</span><b>{esc(total_items)}</b></div>
                <div class="stat-card red"><span>高优先级</span><b>{esc(high_priority_count)}</b></div>
                <div class="stat-card yellow"><span>指标面板</span><b>{esc(len(metrics_files))}</b></div>
                <div class="stat-card {'red' if alert_count > 0 else 'green'}"><span>活跃预警</span><b>{esc(alert_count)}</b></div>
                <div class="stat-card purple"><span>舆情日报</span><b>{esc(len(sentiment_files))}</b></div>
            </div>
            <nav class="report-nav" aria-label="报告章节">
                <a href="#tracking-items">跟踪事项</a>
                <a href="#metrics">指标追踪</a>
                <a href="#sentiment">舆情监控</a>
                <a href="#alerts">预警状态</a>
                <a href="#updates">更新记录</a>
            </nav>
            <section class="attention-panel" aria-labelledby="attention-title">
                <h2 id="attention-title">优先跟进</h2>
                <p>将影响研究结论复核、证据完整性或时效性的事项前置展示。</p>
                <div class="attention-grid">{attention_html}</div>
            </section>
        </header>

        <section class="report-section" id="tracking-items">
            <button class="section-header" type="button" onclick="toggleSection(this)" aria-expanded="true">
                <span class="section-title-wrap">
                    <span class="section-kicker">Tracking Items</span>
                    <h2>跟踪事项清单</h2>
                    <span class="section-note">承接分析报告和事实核查结果，记录需要持续验证的假设、风险与异常。</span>
                </span>
                <span class="section-actions">
                    <span class="badge{' alert' if high_priority_count > 0 else ''}">{esc(total_items)} 项</span>
                    <span class="toggle-arrow" aria-hidden="true">▼</span>
                </span>
            </button>
            <div class="section-content">
                {tracking_items_html}
            </div>
        </section>

        <section class="report-section" id="metrics">
            <button class="section-header" type="button" onclick="toggleSection(this)" aria-expanded="true">
                <span class="section-title-wrap">
                    <span class="section-kicker">Metrics</span>
                    <h2>指标追踪面板</h2>
                    <span class="section-note">展示关键指标、趋势变化、证据回跳和需要复核的数据口径。</span>
                </span>
                <span class="section-actions">
                    <span class="badge">{file_badge(metrics_files)}</span>
                    <span class="toggle-arrow" aria-hidden="true">▼</span>
                </span>
            </button>
            <div class="section-content">
                {metrics_html}
            </div>
        </section>

        <section class="report-section" id="sentiment">
            <button class="section-header" type="button" onclick="toggleSection(this)" aria-expanded="true">
                <span class="section-title-wrap">
                    <span class="section-kicker">Sentiment</span>
                    <h2>舆情监控</h2>
                    <span class="section-note">汇总公告、媒体、社区或人工输入中的变化线索，并标注数据属性。</span>
                </span>
                <span class="section-actions">
                    <span class="badge">{file_badge(sentiment_files)}</span>
                    <span class="toggle-arrow" aria-hidden="true">▼</span>
                </span>
            </button>
            <div class="section-content">
                {sentiment_html}
            </div>
        </section>

        <section class="report-section" id="alerts">
            <button class="section-header" type="button" onclick="toggleSection(this)" aria-expanded="true">
                <span class="section-title-wrap">
                    <span class="section-kicker">Alerts</span>
                    <h2>预警状态</h2>
                    <span class="section-note">按规则触发的异常、超期、指标变化和后续处理建议。</span>
                </span>
                <span class="section-actions">
                    <span class="badge{' alert' if alert_count > 0 else ''}">{esc(alert_count)} 条活跃</span>
                    <span class="toggle-arrow" aria-hidden="true">▼</span>
                </span>
            </button>
            <div class="section-content">
                {alerts_html}
            </div>
        </section>

        <section class="report-section" id="updates">
            <button class="section-header" type="button" onclick="toggleSection(this)" aria-expanded="true">
                <span class="section-title-wrap">
                    <span class="section-kicker">Updates</span>
                    <h2>更新记录</h2>
                    <span class="section-note">记录本轮跟踪对事项、指标、预警和上游研究结论的影响。</span>
                </span>
                <span class="section-actions">
                    <span class="badge">{file_badge(update_files)}</span>
                    <span class="toggle-arrow" aria-hidden="true">▼</span>
                </span>
            </button>
            <div class="section-content">
                {update_html}
            </div>
        </section>

        <footer class="footer">
            <p>finsight_tracking 金融跟踪与预警系统 · 自动生成 · 本报告仅供研究参考，不构成投资建议。</p>
        </footer>
    </main>

    <script>
        function toggleSection(header) {{
            const content = header.nextElementSibling;
            const arrow = header.querySelector('.toggle-arrow');
            const expanded = header.getAttribute('aria-expanded') !== 'false';
            header.setAttribute('aria-expanded', String(!expanded));
            content.classList.toggle('collapsed');
            arrow.classList.toggle('collapsed');
        }}
    </script>
</body>
</html>'''

    return html


def run_html_reporter(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    report_date: str = None,
) -> Optional[str]:
    """
    主入口：生成合并HTML报告

    输出: wiki/tracking/<stock>-<company>/<stock_code>-<company_short_name>-跟踪报告-<date>.html
    """
    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    tracking_dir = os.path.join(company_dir, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    # 生成HTML内容
    html_content = generate_html_report(stock_code, company_name, wiki_base, report_date)

    # 保存文件
    filename = generated_report_filename(stock_code, company_name, "跟踪报告", report_date, ".html")
    output_path = os.path.join(tracking_dir, filename)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    write_report_manifest(stock_code, company_name, output_path)

    print(f"✅ 合并HTML报告已生成: {output_path}")
    return output_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="合并HTML报告生成器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--date", help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    args = parser.parse_args()

    run_html_reporter(args.stock, args.company, args.wiki_base, args.date)
