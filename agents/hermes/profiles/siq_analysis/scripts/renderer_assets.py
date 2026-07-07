#!/usr/bin/env python3
"""Static presentation assets for the SIQ HTML report renderer.

This module intentionally keeps CSS and browser-side chart scripts out of
``html_renderer_v2.py`` so the renderer facade can stay focused on data shaping
and HTML assembly.
"""

from __future__ import annotations

BASE_CSS_STYLES = """
:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-card: #1e293b;
  --bg-card-hover: #27354f;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --border-color: #334155;
  --accent-blue: #3b82f6;
  --accent-cyan: #06b6d4;
  --accent-green: #10b981;
  --accent-red: #ef4444;
  --accent-orange: #f59e0b;
  --accent-purple: #8b5cf6;
  --accent-pink: #ec4899;
  --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -2px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.4), 0 4px 6px -4px rgba(0, 0, 0, 0.4);
  --radius: 12px;
  --radius-sm: 8px;
  --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  min-height: 100vh;
}

/* Scrollbar */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: var(--bg-primary); }
::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* Layout */
.container { max-width: 1400px; margin: 0 auto; padding: 0 24px; }

/* Header */
.report-header {
  background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
  border-bottom: 1px solid var(--border-color);
  padding: 40px 0 32px;
  position: relative;
  overflow: hidden;
}
.report-header::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-green), var(--accent-orange), var(--accent-purple));
  pointer-events: none;
}
.header-content { position: relative; z-index: 1; }
.stock-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(59,130,246,0.15);
  border: 1px solid rgba(59,130,246,0.3);
  color: var(--accent-blue);
  padding: 6px 16px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 16px;
}
.report-title {
  font-size: 36px;
  font-weight: 800;
  background: linear-gradient(135deg, var(--text-primary) 0%, var(--accent-cyan) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 8px;
}
.report-subtitle {
  color: var(--text-secondary);
  font-size: 15px;
  margin-bottom: 24px;
}
.report-meta {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 13px;
}
.meta-item svg { width: 16px; height: 16px; opacity: 0.7; }

/* KPI Cards */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin: 24px 0;
}
.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  padding: 20px;
  transition: var(--transition);
  position: relative;
  overflow: hidden;
}
.kpi-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
  opacity: 0;
  transition: var(--transition);
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); border-color: var(--accent-blue); }
.kpi-card:hover::before { opacity: 1; }
.kpi-card.positive::before { background: linear-gradient(90deg, var(--accent-green), #34d399); }
.kpi-card.negative::before { background: linear-gradient(90deg, var(--accent-red), #f87171); }
.kpi-card.warning::before { background: linear-gradient(90deg, var(--accent-orange), #fbbf24); }
.kpi-label {
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}
.kpi-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.kpi-change {
  font-size: 13px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.kpi-change.up { color: var(--accent-green); }
.kpi-change.down { color: var(--accent-red); }
.kpi-change.neutral { color: var(--text-muted); }

/* Section styling */
.section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  margin: 24px 0;
  overflow: hidden;
  scroll-margin-top: 24px;
}
.section-header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  gap: 12px;
}
.section-number {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  color: white;
  flex-shrink: 0;
}
.section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  flex: 1;
  margin: 0;
  line-height: 1.35;
}
.section-header h2 {
  flex: 1;
  margin: 0;
  line-height: 1.35;
}
.section-content {
  padding: 24px;
  display: block;
}

/* Subsection */
.subsection {
  margin-bottom: 24px;
}
.subsection:last-child { margin-bottom: 0; }
.subsection-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-cyan);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.subsection-title::before {
  content: '';
  width: 4px;
  height: 16px;
  background: linear-gradient(180deg, var(--accent-cyan), var(--accent-blue));
  border-radius: 2px;
}

/* Lists */
.content-list {
  list-style: none;
  padding: 0;
}
.content-list li {
  padding: 10px 0;
  padding-left: 24px;
  position: relative;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
  border-bottom: 1px solid rgba(51,65,85,0.3);
}
.content-list li:last-child { border-bottom: none; }
.content-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 16px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-blue);
  opacity: 0.6;
}
.content-list li.fact::before { background: var(--accent-blue); }
.content-list li.calc::before { background: var(--accent-cyan); }
.content-list li.judge::before { background: var(--accent-purple); }
.content-list li.risk::before { background: var(--accent-red); }
.content-list li.evidence::before { background: var(--accent-green); opacity: 0.4; }

/* Evidence tags */
.evidence-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(16,185,129,0.1);
  border: 1px solid rgba(16,185,129,0.2);
  color: var(--accent-green);
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-family: "SF Mono", "Fira Code", monospace;
  margin: 2px;
}
.evidence-tag a {
  color: inherit;
  text-decoration: none;
  border-left: 1px solid rgba(16,185,129,0.35);
  margin-left: 6px;
  padding-left: 6px;
}
.evidence-tag a:hover { text-decoration: underline; }
.evidence-tag.missing {
  background: rgba(239,68,68,0.1);
  border-color: rgba(239,68,68,0.2);
  color: var(--accent-red);
}

/* Charts */
.chart-container {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 16px;
  margin: 16px 0;
  position: relative;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.chart-title::before {
  content: '';
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, var(--accent-blue), var(--accent-cyan));
  border-radius: 2px;
}
.chart-area {
  width: 100%;
  height: 320px;
  display: none;
}
.chart-area.small { height: 240px; }
.chart-area.large { height: 400px; }
.chart-area.hero { height: 480px; }
.charts-enhanced .chart-area { display: block; }
.charts-enhanced .chart-fallback { display: none; }
.charts-enhanced .chart-container.static-fallback .chart-area { display: none; }
.chart-fallback {
  min-height: 260px;
  display: flex;
  align-items: stretch;
}
.charts-enhanced .chart-container.static-fallback .chart-fallback { display: flex; }
.chart-fallback svg {
  width: 100%;
  height: auto;
  display: block;
}
.chart-fallback svg text,
.chart-fallback svg rect,
.chart-fallback svg path,
.chart-fallback svg circle,
.chart-fallback svg polygon,
.chart-fallback svg polyline {
  transition: opacity 160ms ease, stroke-width 160ms ease, filter 160ms ease;
}
.svg-axis { stroke: #475569; stroke-width: 1; }
.svg-grid { stroke: rgba(71,85,105,0.35); stroke-width: 1; }
.svg-label { fill: var(--text-secondary); font-size: 12px; font-weight: 600; }
.svg-value { fill: var(--text-primary); font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.svg-muted { fill: var(--text-muted); font-size: 11px; }
.svg-line-blue { fill: none; stroke: var(--accent-blue); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.svg-line-green { fill: none; stroke: var(--accent-green); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.svg-dot { fill: var(--bg-primary); stroke-width: 2; }
.svg-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 8px 8px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}
.svg-chip i {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  display: inline-block;
}
.chart-fallback-empty {
  width: 100%;
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
}
.chart-interactive {
  cursor: pointer;
  outline: none;
}
.chart-hit,
.ib-hit {
  pointer-events: all;
}
.chart-interactive:hover .chart-mark,
.chart-interactive:focus-visible .chart-mark {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.20));
}
.chart-interactive:focus-visible .chart-hit {
  stroke: #f8fafc;
  stroke-width: 2;
}
.chart-container.chart-has-active .chart-interactive {
  opacity: 0.24;
}
.chart-container.chart-has-active .chart-interactive.is-active {
  opacity: 1;
}
.chart-container .chart-interactive.is-active .chart-mark {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.22));
}
.report-chart-tooltip {
  position: fixed;
  z-index: 500;
  max-width: 280px;
  pointer-events: none;
  background: rgba(17,24,39,0.96);
  color: #ffffff;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 16px 34px -24px rgba(15,23,42,0.9);
  opacity: 0;
  transform: translate(-50%, -120%);
  transition: opacity 120ms ease;
  font-size: 12px;
  line-height: 1.5;
}
.report-chart-tooltip.visible {
  opacity: 1;
}
.report-chart-tooltip strong {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}
.report-chart-tooltip span {
  display: block;
  color: #d1d5db;
}
.report-chart-tooltip .tooltip-value,
.income-bridge-tooltip .tooltip-value {
  color: #ffffff;
  font-size: 15px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
}
.income-bridge-panel {
  background: #ffffff;
  border-color: #e5e7eb;
  box-shadow: 0 18px 42px -32px rgba(15,23,42,0.38);
  color: #111827;
  padding: 0 0 16px;
}
.income-bridge-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 0 22px 12px;
}
.income-bridge-metric {
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
}
.income-bridge-metric-label {
  color: #64748b;
  font-size: 12px;
  margin-bottom: 4px;
}
.income-bridge-metric-value {
  color: #111827;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.income-bridge-panel .chart-title {
  color: #111827;
  font-size: 26px;
  line-height: 1.15;
  font-weight: 800;
  margin-bottom: 2px;
}
.income-bridge-panel .chart-title::before {
  display: none;
}
.income-bridge-head {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  border-bottom: 1px solid #eef2f7;
  padding: 18px 24px 14px;
  margin-bottom: 0;
}
.income-bridge-title-group {
  min-width: 0;
}
.income-bridge-subtitle {
  color: #7c8794;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.income-bridge-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 18px;
  flex-wrap: wrap;
}
.income-bridge-unit {
  color: #7c8794;
  font-size: 13px;
  white-space: nowrap;
  padding-top: 0;
}
.income-bridge-legend {
  display: flex;
  gap: 14px;
  align-items: center;
  color: #111827;
  font-size: 14px;
  margin: 0;
}
.income-bridge-legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.income-bridge-legend i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.income-bridge-legend .income { background: #35a9f4; }
.income-bridge-legend .expense { background: #f2c400; }
.income-bridge-legend .profit { background: #ff3548; }
.income-bridge-panel .chart-fallback {
  display: block;
  min-height: 560px;
  overflow-x: hidden;
  padding: 12px 18px 0;
}
.income-bridge-panel .chart-fallback svg {
  display: block;
  width: 100%;
  min-width: 0;
  max-height: 560px;
}
.income-bridge-panel .chart-fallback svg text,
.income-bridge-panel .chart-fallback svg rect,
.income-bridge-panel .chart-fallback svg path {
  transition: opacity 160ms ease, stroke-width 160ms ease, filter 160ms ease;
}
.ib-interactive {
  cursor: pointer;
  outline: none;
}
.ib-interactive:hover .ib-flow,
.ib-interactive:focus-visible .ib-flow {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.18));
}
.ib-interactive:focus-visible .ib-hit {
  stroke: rgba(37,99,235,0.55);
  stroke-width: 1.5;
}
.income-bridge-panel.ib-has-active .ib-interactive {
  opacity: 0.24;
}
.income-bridge-panel.ib-has-active .ib-interactive.is-active,
.income-bridge-panel.ib-has-active .ib-interactive.is-neighbor {
  opacity: 1;
}
.income-bridge-panel .ib-interactive.is-active .ib-flow,
.income-bridge-panel .ib-interactive.is-neighbor .ib-flow {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.18));
}
.income-bridge-tooltip {
  position: fixed;
  z-index: 500;
  max-width: 280px;
  pointer-events: none;
  background: rgba(17,24,39,0.96);
  color: #ffffff;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 16px 34px -24px rgba(15,23,42,0.9);
  opacity: 0;
  transform: translate(-50%, -120%);
  transition: opacity 120ms ease;
  font-size: 12px;
  line-height: 1.5;
}
.income-bridge-tooltip.visible {
  opacity: 1;
}
.income-bridge-tooltip strong {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}
.income-bridge-tooltip span {
  display: block;
  color: #d1d5db;
}
.income-bridge-footnotes {
  margin: 10px 22px 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.65;
}
.income-bridge-footnotes span {
  display: inline-block;
  margin-right: 14px;
}

/* Grid layouts for charts */
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
  gap: 16px;
}
.chart-grid-3 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}

/* Risk indicators */
.risk-card {
  background: rgba(239,68,68,0.05);
  border: 1px solid rgba(239,68,68,0.15);
  border-radius: var(--radius-sm);
  padding: 16px;
  margin: 8px 0;
}
.risk-card.warning {
  background: rgba(245,158,11,0.05);
  border-color: rgba(245,158,11,0.15);
}
.risk-card.info {
  background: rgba(59,130,246,0.05);
  border-color: rgba(59,130,246,0.15);
}
.risk-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-red);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.risk-card.warning .risk-card-title { color: var(--accent-orange); }
.risk-card.info .risk-card-title { color: var(--accent-blue); }
.risk-card-content {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

/* Status badges */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.status-badge.success {
  background: rgba(16,185,129,0.15);
  color: var(--accent-green);
}
.status-badge.danger {
  background: rgba(239,68,68,0.15);
  color: var(--accent-red);
}
.status-badge.warning {
  background: rgba(245,158,11,0.15);
  color: var(--accent-orange);
}
.status-badge.info {
  background: rgba(59,130,246,0.15);
  color: var(--accent-blue);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  animation: pulse 2s infinite;
}

.source-legend {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: center;
  margin: 18px 0;
  padding: 14px 16px;
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.source-legend-title {
  color: #0f172a;
  font-size: 14px;
  font-weight: 750;
  margin-bottom: 4px;
}

.source-legend p {
  margin: 0;
  color: #475569;
  font-size: 13px;
  line-height: 1.55;
}

.source-legend-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  min-width: 360px;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Table styling */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 12px 0;
}
.data-table th {
  background: var(--bg-primary);
  color: var(--text-muted);
  font-weight: 600;
  text-align: left;
  padding: 10px 12px;
  border-bottom: 2px solid var(--border-color);
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.5px;
}
.data-table td {
  padding: 10px 12px;
  border-bottom: 1px solid rgba(51,65,85,0.3);
  color: var(--text-secondary);
}
.data-table tr:hover td {
  background: rgba(59,130,246,0.05);
  color: var(--text-primary);
}
.data-table .num { text-align: right; font-family: "SF Mono", monospace; }
.data-table .positive { color: var(--accent-green); }
.data-table .negative { color: var(--accent-red); }

.main-content {
  width: 100%;
}

/* Progress bar */
.progress-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--bg-secondary);
  z-index: 200;
}
.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
  transition: width 0.3s ease;
  width: 0%;
}

/* Footer */
.report-footer {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  padding: 24px 0;
  margin-top: 48px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

/* Print styles */
@media print {
  body { background: white; color: #1f2937; }
  .progress-bar { display: none !important; }
  .main-content { margin-left: 0 !important; }
  .section { border: 1px solid #e5e7eb; break-inside: avoid; }
  .section-content { display: block !important; }
  .chart-container { break-inside: avoid; }
  .kpi-card { border: 1px solid #e5e7eb; }
}

/* Responsive */
@media (max-width: 768px) {
  .container { padding: 0 16px; }
  .report-title { font-size: 24px; }
  .kpi-grid { grid-template-columns: 1fr; }
  .chart-grid, .chart-grid-3 { grid-template-columns: 1fr; }
  .chart-area { height: 250px; }
  .chart-area.hero { height: 360px; }
}

/* Tooltip */
.tooltip {
  position: relative;
}
.tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-primary);
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
}
.tooltip:hover::after { opacity: 1; }
"""

PROFESSIONAL_REPORT_CSS = """
:root {
  --bg-primary: #f5f7fb;
  --bg-secondary: #0f172a;
  --bg-card: #ffffff;
  --bg-card-hover: #f8fafc;
  --text-primary: #111827;
  --text-secondary: #334155;
  --text-muted: #64748b;
  --border-color: #dbe3ef;
  --accent-blue: #2563eb;
  --accent-cyan: #0891b2;
  --accent-green: #059669;
  --accent-red: #dc2626;
  --accent-orange: #d97706;
  --accent-purple: #7c3aed;
  --accent-pink: #be185d;
  --paper: #ffffff;
  --paper-soft: #f8fafc;
  --ink-soft: #475569;
  --shadow: 0 10px 30px -28px rgba(15, 23, 42, 0.45);
  --shadow-lg: 0 24px 52px -42px rgba(15, 23, 42, 0.55);
  --radius: 8px;
  --radius-sm: 6px;
}

body {
  background:
    linear-gradient(180deg, #eef3f8 0, #f8fafc 280px, #f5f7fb 100%);
  color: var(--text-primary);
  font-size: 16px;
}

.container {
  max-width: 1180px;
  padding: 0 28px;
}

.report-header {
  background:
    linear-gradient(135deg, rgba(15, 23, 42, 0.98), rgba(30, 41, 59, 0.94)),
    linear-gradient(90deg, rgba(8, 145, 178, 0.16), rgba(217, 119, 6, 0.12));
  padding: 36px 0 30px;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}

.stock-badge {
  background: rgba(255,255,255,0.09);
  border-color: rgba(255,255,255,0.22);
  color: #e0f2fe;
  border-radius: 999px;
  letter-spacing: 0;
}

.report-title {
  color: #f8fafc;
  background: none;
  -webkit-text-fill-color: currentColor;
  font-size: 34px;
  letter-spacing: 0;
}

.report-subtitle {
  max-width: 760px;
  color: #cbd5e1;
}

.report-meta {
  gap: 12px;
}

.meta-item {
  min-height: 32px;
  padding: 6px 10px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 6px;
  color: #dbeafe;
  background: rgba(255,255,255,0.05);
}

.status-badge {
  min-height: 30px;
  border-radius: 999px;
  background: var(--paper);
  border: 1px solid var(--border-color);
}

.kpi-grid {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0 22px;
}

.kpi-card {
  min-height: 132px;
  background: var(--paper);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 16px;
}

.kpi-card:hover {
  transform: translateY(-1px);
  border-color: #bfdbfe;
}

.kpi-label {
  color: var(--text-muted);
  letter-spacing: 0;
  text-transform: none;
  font-weight: 650;
}

.kpi-value {
  color: var(--text-primary);
  font-size: 25px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

.kpi-change {
  font-size: 12px;
  line-height: 1.35;
}

.chart-container {
  background: var(--paper);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 0;
  overflow: hidden;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.chart-container:hover {
  border-color: #bfdbfe;
  box-shadow: var(--shadow-lg);
}

.chart-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 16px 18px 10px;
  border-bottom: 1px solid #eef2f7;
}

.chart-title {
  color: var(--text-primary);
  font-size: 15px;
  line-height: 1.35;
  margin-bottom: 0;
}

.chart-note {
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
  text-align: right;
  max-width: 260px;
}

.chart-area {
  padding: 4px 14px 12px;
}

.chart-fallback {
  padding: 14px 16px 16px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
}

.chart-fallback svg {
  min-width: 660px;
}

.chart-fallback-empty {
  min-width: 0;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
}

.section {
  background: var(--paper);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: var(--shadow);
  margin: 22px 0;
}

.section-header {
  padding: 18px 22px;
  background: linear-gradient(180deg, #ffffff, #f8fafc);
  border-bottom: 1px solid #e2e8f0;
}

.section-number {
  width: 34px;
  height: 34px;
  border-radius: 7px;
  background: #0f172a;
  color: #f8fafc;
}

.section-title {
  font-size: 20px;
  color: #0f172a;
}

.section-content {
  padding: 22px;
}

.subsection {
  padding: 18px 0;
  border-bottom: 1px solid #edf2f7;
}

.subsection:last-child {
  border-bottom: 0;
}

.subsection-title {
  color: #0f172a;
  font-size: 15px;
  letter-spacing: 0;
  text-transform: none;
  margin-bottom: 12px;
}

.subsection-title::before {
  width: 3px;
  background: #2563eb;
}

.role-badge,
.source-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.4;
  letter-spacing: 0;
  border: 1px solid transparent;
  white-space: nowrap;
}

.role-badge {
  margin-left: 8px;
  color: #475569;
  background: #f1f5f9;
  border-color: #e2e8f0;
}

.role-synthesis .role-badge,
.role-badge.role-synthesis {
  color: #075985;
  background: #e0f2fe;
  border-color: #bae6fd;
}

.narrative-items {
  display: grid;
  gap: 12px;
}

.narrative-item {
  position: relative;
  padding: 14px 16px 15px;
  border: 1px solid #e2e8f0;
  border-left: 4px solid #94a3b8;
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 252, 0.8)),
    #ffffff;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
}

.narrative-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin-bottom: 8px;
}

.evidence-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  color: #475569;
  background: #f8fafc;
  border: 1px solid #dbe3ef;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.4;
}

.narrative-copy {
  max-width: 82ch;
}

.narrative-item p {
  margin: 8px 0 0;
  color: #243044;
  font-size: 15px;
  line-height: 1.78;
  overflow-wrap: anywhere;
}

.narrative-item p:first-child {
  margin-top: 0;
}

.narrative-item.synthesis {
  background: #f8fbff;
  border-color: #bfdbfe;
  border-left-color: #2563eb;
}

.narrative-item.synthesis p {
  color: #172554;
  font-weight: 500;
}

.narrative-item.diagnosis,
.narrative-item.bridge {
  border-left-color: #0891b2;
}

.narrative-item.model,
.narrative-item.table {
  border-left-color: #7c3aed;
}

.narrative-item.risk_chain,
.narrative-item.scenario {
  border-left-color: #dc2626;
  background: #fffafa;
}

.narrative-item.tracking {
  border-left-color: #d97706;
  background: #fffdf7;
}

.narrative-item.evidence,
.narrative-item.audit {
  border-left-color: #059669;
}

.source-badge {
  margin-right: 0;
}

.source-local,
.source-fact {
  color: #065f46;
  background: #ecfdf5;
  border-color: #a7f3d0;
}

.source-model {
  color: #5b21b6;
  background: #f3e8ff;
  border-color: #ddd6fe;
}

.source-external {
  color: #92400e;
  background: #fffbeb;
  border-color: #fde68a;
}

.source-risk {
  color: #991b1b;
  background: #fef2f2;
  border-color: #fecaca;
}

.source-tracking {
  color: #9a3412;
  background: #fff7ed;
  border-color: #fed7aa;
}

.source-review {
  color: #334155;
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.content-list li {
  color: var(--text-secondary);
  font-size: 15px;
}

.evidence-tag {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #334155;
  border-radius: 999px;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.report-summary {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(300px, 0.85fr);
  gap: 14px;
  margin: 18px 0 20px;
}

.summary-panel,
.section-toc,
.evidence-details {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.item-evidence {
  margin-top: 12px;
  border-top: 1px dashed #cbd5e1;
  padding-top: 10px;
}

.item-evidence summary {
  color: #475569;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
}

.item-evidence ul {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.65;
}

.summary-panel {
  padding: 18px;
}

.summary-eyebrow,
.toc-eyebrow {
  color: #64748b;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0;
  margin-bottom: 8px;
}

.summary-title {
  color: #0f172a;
  font-size: 20px;
  font-weight: 800;
  line-height: 1.35;
  margin-bottom: 10px;
}

.summary-body {
  display: grid;
  gap: 10px;
}

.summary-point {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 9px;
  color: #334155;
  font-size: 14px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.summary-point strong {
  color: #0f172a;
  font-weight: 800;
}

.summary-point::before {
  content: '';
  width: 7px;
  height: 7px;
  margin-top: 9px;
  border-radius: 50%;
  background: #2563eb;
}

.quality-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 18px 0 0;
}

.section-toc {
  padding: 16px;
}

.section-toc nav {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.section-toc a {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 9px 11px;
  border-radius: 7px;
  color: #334155;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.35;
  cursor: pointer;
  transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.section-toc a:hover,
.section-toc a:focus-visible {
  color: #1d4ed8;
  border-color: #bfdbfe;
  background: #eff6ff;
  outline: none;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.section-toc a.is-active {
  color: #1d4ed8;
  border-color: #93c5fd;
  background: #dbeafe;
}

.section.section-target {
  border-color: #60a5fa;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10), var(--shadow-lg);
}

.toc-index {
  color: #64748b;
  font-variant-numeric: tabular-nums;
  font-weight: 750;
}

.evidence-details {
  margin-top: 12px;
  padding: 0;
  box-shadow: none;
}

.evidence-details summary {
  cursor: pointer;
  padding: 10px 12px;
  color: #334155;
  font-size: 13px;
  font-weight: 700;
}

.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 0 12px 12px;
}

.income-bridge-panel {
  border-radius: 8px;
}

.dupont-panel .chart-area {
  display: none !important;
}

.dupont-panel .chart-fallback,
.charts-enhanced .dupont-panel .chart-fallback {
  display: block;
  min-height: 380px;
  overflow-x: auto;
  overflow-y: hidden;
}

.dupont-panel .chart-fallback svg {
  min-width: 760px;
}

.svg-axis { stroke: #94a3b8; }
.svg-grid { stroke: rgba(148, 163, 184, 0.34); }
.svg-label { fill: #475569; }
.svg-value { fill: #111827; }
.svg-muted { fill: #64748b; }
.svg-dot { fill: #ffffff; }

.report-footer {
  background: transparent;
  border-top: 1px solid #dbe3ef;
  color: var(--text-muted);
}

.progress-bar {
  background: rgba(226, 232, 240, 0.92);
}

.progress-bar-fill {
  background: linear-gradient(90deg, #2563eb, #0891b2, #d97706);
}

@media (max-width: 1180px) {
  .kpi-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .report-summary { grid-template-columns: 1fr; }
}

@media (max-width: 768px) {
  .container { padding: 0 16px; }
  .report-title { font-size: 25px; }
  .report-header { padding: 28px 0 24px; }
  .kpi-grid { grid-template-columns: 1fr; }
  .section-header { align-items: flex-start; }
  .section-content { padding: 16px; }
  .narrative-item { padding: 12px; }
  .narrative-item p { font-size: 14px; }
  .income-bridge-head { display: block; padding: 8px 0 10px; }
  .income-bridge-meta { justify-content: flex-start; gap: 10px; margin-top: 8px; }
  .income-bridge-legend { flex-wrap: wrap; }
  .income-bridge-panel .chart-title { font-size: 22px; }
  .source-legend { display: block; }
  .source-legend-badges { min-width: 0; justify-content: flex-start; margin-top: 10px; }
  .section-toc nav { grid-template-columns: 1fr; }
  .chart-head { display: block; }
  .chart-note { text-align: left; max-width: none; margin-top: 6px; }
  .chart-fallback svg { min-width: 620px; }
  .income-bridge-panel .chart-fallback { min-height: 0; overflow-x: auto; }
  .income-bridge-panel .chart-fallback svg { width: 1120px; min-width: 1120px; max-height: none; }
}
"""

CSS_STYLES = BASE_CSS_STYLES + PROFESSIONAL_REPORT_CSS

ECHARTS_SCRIPTS = """
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script>
// Chart theme colors
const chartColors = ['#2563eb', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed', '#be185d', '#0f766e'];
const chartBg = '#ffffff';
const chartText = '#475569';
const chartGrid = '#cbd5e1';

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function placeReportTooltip(tooltip, event, fallbackEl) {
  const rect = fallbackEl?.getBoundingClientRect?.() || { left: 0, top: 80, width: window.innerWidth };
  const x = event?.clientX ?? rect.left + rect.width / 2;
  const y = event?.clientY ?? rect.top + 80;
  tooltip.style.left = `${Math.min(window.innerWidth - 18, Math.max(18, x))}px`;
  tooltip.style.top = `${Math.max(24, y - 12)}px`;
}

function chartTooltipHtml(title, value, detail) {
  return `<strong>${escapeHtml(title)}</strong>${value ? `<span class="tooltip-value">${escapeHtml(value)}</span>` : ''}${detail ? `<span>${escapeHtml(detail)}</span>` : ''}`;
}

function reportTooltipBase(trigger = 'axis') {
  return {
    trigger,
    confine: true,
    appendToBody: true,
    enterable: false,
    backgroundColor: 'rgba(17,24,39,0.96)',
    borderColor: 'rgba(255,255,255,0.14)',
    borderWidth: 1,
    padding: [10, 12],
    textStyle: { color: '#f8fafc', fontSize: 12, lineHeight: 18 },
    extraCssText: 'border-radius:8px;box-shadow:0 16px 34px -24px rgba(15,23,42,0.9);'
  };
}

function reportValueLabel(position = 'top') {
  return {
    show: true,
    position,
    color: '#0f172a',
    fontSize: 11,
    fontWeight: 700,
    backgroundColor: 'rgba(241,245,249,0.92)',
    borderColor: 'rgba(148,163,184,0.4)',
    borderWidth: 1,
    borderRadius: 4,
    padding: [3, 6],
    formatter: function(p) {
      const value = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
      return Number(value) > 0 ? Number(value).toFixed(2) : String(value);
    }
  };
}

function initReportChartInteractions() {
  document.querySelectorAll('.chart-container:not(.income-bridge-panel)').forEach((panel) => {
    const items = Array.from(panel.querySelectorAll('.chart-interactive'));
    if (!items.length) return;
    let tooltip = panel.querySelector('.report-chart-tooltip');
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'report-chart-tooltip';
      tooltip.setAttribute('role', 'status');
      tooltip.setAttribute('aria-live', 'polite');
      panel.appendChild(tooltip);
    }
    let locked = null;
    const clear = () => {
      if (locked) return;
      panel.classList.remove('chart-has-active');
      items.forEach((el) => el.classList.remove('is-active'));
      tooltip.classList.remove('visible');
    };
    const activate = (item, event, force = false) => {
      if (!force && locked && locked !== item) return;
      panel.classList.add('chart-has-active');
      items.forEach((el) => el.classList.toggle('is-active', el === item));
      tooltip.innerHTML = chartTooltipHtml(item.dataset.title || '图表项目', item.dataset.value || '', item.dataset.detail || '');
      placeReportTooltip(tooltip, event, panel);
      tooltip.classList.add('visible');
    };
    items.forEach((item) => {
      item.addEventListener('mouseenter', (event) => activate(item, event));
      item.addEventListener('mousemove', (event) => placeReportTooltip(tooltip, event, panel));
      item.addEventListener('mouseleave', clear);
      item.addEventListener('focus', (event) => activate(item, event));
      item.addEventListener('blur', clear);
      item.addEventListener('click', (event) => {
        event.preventDefault();
        locked = locked === item ? null : item;
        if (locked) activate(item, event, true);
        else clear();
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }));
        }
        if (event.key === 'Escape') {
          locked = null;
          clear();
        }
      });
    });
    panel.addEventListener('mouseleave', clear);
  });
}

function initIncomeBridgeInteractions() {
  const panels = document.querySelectorAll('.income-bridge-panel');
  panels.forEach((panel) => {
    const tooltip = panel.querySelector('.income-bridge-tooltip');
    const items = Array.from(panel.querySelectorAll('.ib-interactive'));
    if (!tooltip || !items.length) return;
    let locked = null;

    const relatedIds = (item) => new Set(String(item.dataset.related || '').split(',').filter(Boolean));
    const clear = () => {
      if (locked) return;
      panel.classList.remove('ib-has-active');
      items.forEach((el) => el.classList.remove('is-active', 'is-neighbor'));
      tooltip.classList.remove('visible');
    };
    const placeTooltip = (event) => {
      const rect = panel.getBoundingClientRect();
      const x = event?.clientX ?? rect.left + rect.width / 2;
      const y = event?.clientY ?? rect.top + 80;
      tooltip.style.left = `${Math.min(window.innerWidth - 18, Math.max(18, x))}px`;
      tooltip.style.top = `${Math.max(24, y - 12)}px`;
    };
    const activate = (item, event, force = false) => {
      if (!force && locked && locked !== item) return;
      const id = item.dataset.ibId;
      const related = relatedIds(item);
      panel.classList.add('ib-has-active');
      items.forEach((el) => {
        const isActive = el === item;
        const isNeighbor = related.has(el.dataset.ibId) || relatedIds(el).has(id);
        el.classList.toggle('is-active', isActive);
        el.classList.toggle('is-neighbor', !isActive && isNeighbor);
      });
      const title = item.dataset.title || '收支拆解';
      const value = item.dataset.value || '';
      const detail = item.dataset.detail || '';
      tooltip.innerHTML = `<strong>${escapeHtml(title)}</strong>${value ? `<span class="tooltip-value">${escapeHtml(value)}</span>` : ''}${detail ? `<span>${escapeHtml(detail)}</span>` : ''}`;
      placeTooltip(event);
      tooltip.classList.add('visible');
    };
    items.forEach((item) => {
      item.addEventListener('mouseenter', (event) => activate(item, event));
      item.addEventListener('mousemove', placeTooltip);
      item.addEventListener('mouseleave', clear);
      item.addEventListener('focus', (event) => activate(item, event));
      item.addEventListener('blur', clear);
      item.addEventListener('click', (event) => {
        event.preventDefault();
        locked = locked === item ? null : item;
        if (locked) activate(item, event, true);
        else clear();
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }));
        }
        if (event.key === 'Escape') {
          locked = null;
          clear();
        }
      });
    });
    panel.addEventListener('mouseleave', clear);
  });
}

function initReportTocNavigation() {
  const tocLinks = Array.from(document.querySelectorAll('.section-toc a[href^="#section-"]'));
  if (!tocLinks.length) return;
  const clearTargets = () => document.querySelectorAll('.section.section-target').forEach((el) => el.classList.remove('section-target'));
  const setActive = (activeLink) => {
    tocLinks.forEach((link) => link.classList.toggle('is-active', link === activeLink));
  };
  tocLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      const targetId = decodeURIComponent(String(link.getAttribute('href') || '').slice(1));
      const target = document.getElementById(targetId);
      if (!target) return;
      event.preventDefault();
      setActive(link);
      clearTargets();
      target.classList.add('section-target');
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      target.focus({ preventScroll: true });
      history.pushState(null, '', `#${targetId}`);
      window.setTimeout(() => target.classList.remove('section-target'), 2200);
    });
  });
  const currentHash = decodeURIComponent(window.location.hash || '');
  if (currentHash) {
    const active = tocLinks.find((link) => link.getAttribute('href') === currentHash);
    if (active) setActive(active);
  }
}

// Common chart option base
function baseOption() {
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(37,99,235,0.1)' } }
    },
    legend: {
      textStyle: { color: chartText, fontSize: 12 },
      itemWidth: 14,
      itemHeight: 10,
      itemGap: 16,
      top: 0
    },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 40, containLabel: true },
  };
}

// Initialize all charts when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  if (typeof echarts !== 'undefined') {
    document.documentElement.classList.add('charts-enhanced');
    initRevenueProfitChart();
    initCashFlowChart();
    initAssetStructureChart();
    initDebtStructureChart();
    initDupontChart();
    initSolvencyGauges();
    initPeerComparisonChart();
    initIncomeBridgeChart();
    initProfitabilityWaterfall();
  }
  initIncomeBridgeInteractions();
  initReportChartInteractions();
  initReportTocNavigation();
  
  // Progress bar
  window.addEventListener('scroll', function() {
    const scrollTop = window.scrollY;
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const progress = (scrollTop / docHeight) * 100;
    const progressFill = document.querySelector('.progress-bar-fill');
    if (progressFill) progressFill.style.width = progress + '%';
    
  });
});

function fmtYi(value) {
  if (value === null || value === undefined || isNaN(Number(value))) return '未返回';
  const num = Number(value);
  return (Math.abs(num) >= 100 ? num.toFixed(1) : num.toFixed(2)) + ' 亿元';
}

function initIncomeBridgeChart() {
  // The hero income bridge is rendered as deterministic inline SVG so its
  // layout stays aligned with the supplied mobile-finance Sankey reference.
}

function bindEChartInteractions(chart, el, defaultDataIndex = 0) {
  if (!chart || !el) return;
  el.setAttribute('tabindex', '0');
  let locked = null;
  let lastParams = null;

  const downplayAll = () => chart.dispatchAction({ type: 'downplay' });
  const show = (params) => {
    if (!params) return;
    const payload = {
      type: 'showTip',
      seriesIndex: params.seriesIndex ?? 0,
      dataIndex: params.dataIndex ?? defaultDataIndex
    };
    if (params.componentType === 'xAxis' || params.axisIndex !== undefined) {
      payload.dataIndex = params.dataIndex ?? defaultDataIndex;
    }
    chart.dispatchAction(payload);
    chart.dispatchAction({
      type: 'highlight',
      seriesIndex: params.seriesIndex ?? 0,
      dataIndex: params.dataIndex ?? defaultDataIndex
    });
  };
  const clear = () => {
    if (locked) return;
    downplayAll();
    chart.dispatchAction({ type: 'hideTip' });
  };

  chart.on('mouseover', (params) => {
    lastParams = params;
    if (!locked) show(params);
  });
  chart.on('globalout', clear);
  chart.on('click', (params) => {
    const key = `${params.seriesIndex ?? 0}:${params.dataIndex ?? defaultDataIndex}`;
    if (locked === key) {
      locked = null;
      downplayAll();
      chart.dispatchAction({ type: 'hideTip' });
      return;
    }
    locked = key;
    downplayAll();
    show(params);
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const params = lastParams || { seriesIndex: 0, dataIndex: defaultDataIndex };
      const key = `${params.seriesIndex ?? 0}:${params.dataIndex ?? defaultDataIndex}`;
      if (locked === key) {
        locked = null;
        downplayAll();
        chart.dispatchAction({ type: 'hideTip' });
      } else {
        locked = key;
        downplayAll();
        show(params);
      }
    }
    if (event.key === 'Escape') {
      locked = null;
      downplayAll();
      chart.dispatchAction({ type: 'hideTip' });
    }
  });
}

function initRevenueProfitChart() {
  const el = document.getElementById('revenue-profit-chart');
  if (!el || !window.revenueProfitData) return;
  const data = window.revenueProfitData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: function(params) {
        const year = params[0]?.axisValue || '';
        const rows = params.map(p => `${p.marker}${p.seriesName}: ${fmtYi(p.value)}`).join('<br/>');
        return '<strong>' + escapeHtml(year) + '</strong><br/>' + rows;
      }
    },
    legend: { data: ['营业收入', '归母净利润'], textStyle: { color: chartText } },
    xAxis: {
      type: 'category',
      data: data.years,
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText }
    },
    yAxis: [
      {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: chartText },
        axisLine: { lineStyle: { color: chartGrid } },
        axisLabel: { color: chartText },
        splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
      },
      {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: chartText },
        axisLine: { lineStyle: { color: chartGrid } },
        axisLabel: { color: chartText },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '营业收入',
        type: 'bar',
        data: data.revenue,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#3b82f6' },
            { offset: 1, color: '#1d4ed8' }
          ]),
          borderRadius: [6, 6, 0, 0]
        },
        barWidth: '40%'
      },
      {
        name: '归母净利润',
        type: 'line',
        yAxisIndex: 1,
        data: data.profit,
        smooth: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { color: '#10b981', width: 3 },
        itemStyle: { color: '#10b981', borderWidth: 2, borderColor: '#0f172a' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(16,185,129,0.3)' },
            { offset: 1, color: 'rgba(16,185,129,0.02)' }
          ])
        },
        label: {
          ...reportValueLabel('top'),
          formatter: function(p) { return Number(p.value).toFixed(2); }
        }
      }
    ]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initCashFlowChart() {
  const el = document.getElementById('cashflow-chart');
  if (!el || !window.cashFlowData) return;
  const data = window.cashFlowData;
  const chart = echarts.init(el);
  const sourceLabel = (key) => {
    const item = data.sources && data.sources[key];
    if (!item) return '';
    if (item.source === 'three_statements') return `来源：合并现金流量表｜${item.row}`;
    if (item.source === 'metric_snapshot') return `来源：指标快照｜${item.row || key}`;
    return '来源：未匹配到原始科目';
  };
  
  const items = [
    { key: 'operating', name: '经营现金流', value: data.operating, color: '#10b981', detail: sourceLabel('operating') },
    { key: 'investing', name: '投资现金流', value: data.investing, color: '#ef4444', detail: sourceLabel('investing') },
    { key: 'financing', name: '筹资现金流', value: data.financing, color: '#f59e0b', detail: sourceLabel('financing') },
    { key: 'capex', name: '资本开支', value: data.capex === null || data.capex === undefined ? null : -data.capex, color: '#8b5cf6', detail: `${sourceLabel('capex')}｜按现金流出方向展示为负值` },
  ].filter(item => item.value !== null && item.value !== undefined && !Number.isNaN(Number(item.value)));
  if (data.free_cash_flow !== null && data.free_cash_flow !== undefined) {
    items.push({ key: 'free_cash_flow', name: '自由现金流', value: data.free_cash_flow, color: '#06b6d4', detail: '公式：经营现金流净额 - 资本开支；优先按原始三表现场重算' });
  }
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) {
        const item = items[p.dataIndex] || {};
        const direction = p.value >= 0 ? '现金流入或余额贡献' : '现金流出或资本投入';
        return chartTooltipHtml(p.name, fmtYi(p.value), [direction, item.detail].filter(Boolean).join('<br/>'));
      }
    },
    xAxis: {
      type: 'category',
      data: items.map(i => i.name),
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText, fontSize: 11, rotate: 15 }
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: chartText },
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText },
      splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
    },
    series: [{
      type: 'bar',
      data: items.map(i => ({
        value: i.value,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: i.color },
            { offset: 1, color: i.color + '66' }
          ]),
          borderRadius: i.value >= 0 ? [6, 6, 0, 0] : [0, 0, 6, 6]
        }
      })),
      barWidth: '50%',
      label: {
        ...reportValueLabel('top'),
        formatter: function(p) { return Number(p.value).toFixed(2); }
      }
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initAssetStructureChart() {
  const el = document.getElementById('asset-structure-chart');
  if (!el || !window.assetStructureData) return;
  const data = window.assetStructureData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) { return chartTooltipHtml(p.name, fmtYi(p.value), '占比 ' + p.percent + '%'); }
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { color: chartText, fontSize: 11 }
    },
    series: [{
      type: 'pie',
      radius: ['45%', '75%'],
      center: ['40%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: {
        borderRadius: 6,
        borderColor: chartBg,
        borderWidth: 2
      },
      label: {
        show: true,
        color: '#0f172a',
        fontSize: 11,
        backgroundColor: 'rgba(241,245,249,0.92)',
        borderColor: 'rgba(148,163,184,0.4)',
        borderWidth: 1,
        borderRadius: 4,
        padding: [3, 6],
        formatter: function(p) { return p.name + '\\n' + p.percent + '%'; }
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#0f172a', backgroundColor: 'rgba(241,245,249,0.95)' },
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' }
      },
      data: data.categories.map((item, i) => ({
        name: item.name,
        value: item.value,
        itemStyle: { color: chartColors[i % chartColors.length] }
      }))
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initDebtStructureChart() {
  const el = document.getElementById('debt-structure-chart');
  if (!el || !window.debtStructureData) return;
  const data = window.debtStructureData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) { return chartTooltipHtml(p.name, fmtYi(p.value), '占比 ' + p.percent + '%'); }
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { color: chartText, fontSize: 11 }
    },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['38%', '50%'],
      roseType: 'radius',
      itemStyle: {
        borderRadius: 6,
        borderColor: chartBg,
        borderWidth: 2
      },
      label: {
        show: true,
        color: '#0f172a',
        fontSize: 11,
        backgroundColor: 'rgba(241,245,249,0.92)',
        borderColor: 'rgba(148,163,184,0.4)',
        borderWidth: 1,
        borderRadius: 4,
        padding: [3, 6],
        formatter: function(p) { return p.name + '\\n' + p.percent + '%'; }
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#0f172a', backgroundColor: 'rgba(241,245,249,0.95)' }
      },
      data: data.categories.map((item, i) => ({
        name: item.name,
        value: item.value,
        itemStyle: { color: chartColors[(i + 3) % chartColors.length] }
      }))
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initDupontChart() {
  // DuPont uses deterministic SVG-first rendering. This keeps long Chinese
  // labels, original values, formulas, and normalized scores aligned in a
  // fixed report layout; generic ECharts radar labels are too fragile in the
  // compact two-column chart card.
  return;
  const el = document.getElementById('dupont-chart');
  if (!el || !window.dupontData) return;
  const data = window.dupontData;
  const chart = echarts.init(el);
  
  const fallbackDimensions = [
    { key: 'net_margin', name: '销售净利率', raw_display: `${Number(data.net_margin || 0).toFixed(2)}%`, score: Math.max(0, Math.min(100, ((data.net_margin || 0) + 10) / 22 * 100)), formula: '归母净利润 / 营业收入' },
    { key: 'asset_turnover', name: '资产周转率', raw_display: `${Number(data.asset_turnover || 0).toFixed(2)}x`, score: Math.max(0, Math.min(100, (data.asset_turnover || 0) / 1.5 * 100)), formula: '营业收入 / 资产总计' },
    { key: 'equity_multiplier', name: '权益乘数', raw_display: `${Number(data.equity_multiplier || 0).toFixed(2)}x`, score: Math.max(0, Math.min(100, ((data.equity_multiplier || 0) - 1) / 5 * 100)), formula: '资产总计 / 归母权益' },
    { key: 'roe', name: 'ROE', raw_display: `${Number(data.roe || 0).toFixed(2)}%`, score: Math.max(0, Math.min(100, ((data.roe || 0) + 20) / 45 * 100)), formula: '归母净利润 / 归母权益' },
  ];
  const dimensions = Array.isArray(data.dimensions) && data.dimensions.length ? data.dimensions : fallbackDimensions;
  const scores = dimensions.map((item) => Number(item.score || 0));
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      formatter: function(p) {
        const lines = dimensions.map((item, i) => `${item.name}: ${item.raw_display || '-'}（展示 ${Number(scores[i] || 0).toFixed(1)}/100）`);
        const formulas = dimensions.map((item) => `${item.name}=${item.formula || '-'}`).join('<br/>');
        return chartTooltipHtml('杜邦分析', lines.join('<br/>'), `${data.scale_note || '雷达使用归一化展示分。'}<br/>${formulas}`);
      }
    },
    radar: {
      indicator: dimensions.map(i => ({ name: `${i.name}\n${i.raw_display || '-'}`, max: 100 })),
      center: ['50%', '55%'],
      radius: '58%',
      axisName: { color: chartText, fontSize: 12, lineHeight: 16, fontWeight: 600 },
      splitArea: {
        areaStyle: {
          color: ['rgba(37,99,235,0.055)', 'rgba(14,165,233,0.025)']
        }
      },
      axisLine: { lineStyle: { color: 'rgba(148,163,184,0.52)' } },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,0.42)' } }
    },
    series: [{
      type: 'radar',
      data: [{
        value: scores,
        name: '杜邦分析',
        areaStyle: { color: 'rgba(37,99,235,0.20)' },
        lineStyle: { color: '#2563eb', width: 2.2 },
        itemStyle: { color: '#2563eb', borderColor: '#ffffff', borderWidth: 1.5 },
        symbol: 'circle',
        symbolSize: 7
      }]
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initSolvencyGauges() {
  const gauges = window.solvencyData;
  if (!gauges) return;
  
  const gaugeConfigs = [
    { id: 'gauge-debt-ratio', name: '资产负债率', value: gauges.debt_ratio, max: 100, unit: '%', threshold: 70 },
    { id: 'gauge-current', name: '流动比率', value: gauges.current_ratio, max: 3, unit: 'x', threshold: 1.5 },
    { id: 'gauge-quick', name: '速动比率', value: gauges.quick_ratio, max: 3, unit: 'x', threshold: 1 },
    { id: 'gauge-cash', name: '现金比率', value: gauges.cash_ratio, max: 1, unit: 'x', threshold: 0.3 },
  ];
  
  gaugeConfigs.forEach(cfg => {
    const el = document.getElementById(cfg.id);
    if (!el || cfg.value === null) return;
    const chart = echarts.init(el);
    const color = cfg.value > cfg.threshold ? '#ef4444' : cfg.value > cfg.threshold * 0.7 ? '#f59e0b' : '#10b981';
    
    const option = {
      series: [{
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: cfg.max,
        splitNumber: 5,
        itemStyle: { color: color },
        progress: { show: true, width: 12, roundCap: true },
        pointer: { show: false },
        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(51,65,85,0.3)']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        title: {
          offsetCenter: [0, '30%'],
          fontSize: 12,
          color: chartText
        },
        detail: {
          fontSize: 22,
          fontWeight: 'bold',
          offsetCenter: [0, '-10%'],
          formatter: function(value) { return value + cfg.unit; },
          color: color
        },
        data: [{ value: cfg.value, name: cfg.name }]
      }]
    };
    chart.setOption(option);
    bindEChartInteractions(chart, el);
    window.addEventListener('resize', () => chart.resize());
  });
}

function initPeerComparisonChart() {
  const el = document.getElementById('peer-comparison-chart');
  if (!el || !window.peerComparisonData) return;
  const data = window.peerComparisonData;
  const chart = echarts.init(el);
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      formatter: function(p) {
        const values = p.value || [];
        return chartTooltipHtml(p.name, values.map((v, i) => `${data.metrics[i]}: ${Number(v).toFixed(2)}`).join(' / '), `样本数 ${data.peer_count || 0}`);
      }
    },
    legend: {
      data: ['本公司', '行业中位数'],
      textStyle: { color: chartText }
    },
    radar: {
      indicator: data.metrics.map(m => ({ name: m, max: 100 })),
      center: ['50%', '55%'],
      radius: '65%',
      axisName: { color: chartText, fontSize: 11 },
      splitArea: {
        areaStyle: {
          color: ['rgba(59,130,246,0.03)', 'rgba(16,185,129,0.03)']
        }
      },
      axisLine: { lineStyle: { color: chartGrid } },
      splitLine: { lineStyle: { color: chartGrid } }
    },
    series: [{
      type: 'radar',
      data: [
        {
          value: data.company,
          name: '本公司',
          areaStyle: { color: 'rgba(59,130,246,0.2)' },
          lineStyle: { color: '#3b82f6', width: 2 },
          itemStyle: { color: '#3b82f6' }
        },
        {
          value: data.peer_median,
          name: '行业中位数',
          areaStyle: { color: 'rgba(245,158,11,0.15)' },
          lineStyle: { color: '#f59e0b', width: 2, type: 'dashed' },
          itemStyle: { color: '#f59e0b' }
        }
      ]
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initProfitabilityWaterfall() {
  const el = document.getElementById('profitability-waterfall');
  if (!el || !window.profitabilityData) return;
  const data = window.profitabilityData;
  const chart = echarts.init(el);
  const barRows = data.steps.map(s => {
    const base = Number(s.base || 0);
    const value = Number(s.value || 0);
    const end = Number((s.end !== undefined ? s.end : base + value) || 0);
    return {
      ...s,
      _plotBase: Math.min(base, end),
      _plotValue: Math.abs(end - base),
      _delta: value
    };
  });
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: function(params) {
        const p = params.find(item => item.seriesName === '项目');
        if (!p) return '';
        const row = barRows[p.dataIndex];
        return chartTooltipHtml(row.name, fmtYi(row._delta), `base ${row.base.toFixed(2)} 亿 / end ${row.end.toFixed(2)} 亿`);
      }
    },
    xAxis: {
      type: 'category',
      data: barRows.map(s => s.name),
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText, fontSize: 11, rotate: 20 }
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: chartText },
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText },
      splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
    },
    series: [{
      type: 'bar',
      stack: 'Total',
      itemStyle: { borderColor: 'transparent', color: 'transparent' },
      emphasis: { itemStyle: { borderColor: 'transparent', color: 'transparent' } },
      data: barRows.map(s => s._plotBase)
    }, {
      name: '项目',
      type: 'bar',
      stack: 'Total',
      data: barRows.map((s, i) => ({
        value: s._plotValue,
        itemStyle: {
          color: s._delta >= 0 
            ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: '#10b981' },
                { offset: 1, color: '#059669' }
              ])
            : new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: '#ef4444' },
                { offset: 1, color: '#dc2626' }
              ]),
          borderRadius: [4, 4, 4, 4]
        }
      })),
      label: {
        ...reportValueLabel('top'),
        formatter: function(p) {
          const value = barRows[p.dataIndex]._delta;
          return value > 0 ? '+' + value.toFixed(2) : value.toFixed(2);
        }
      }
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}
</script>
"""
