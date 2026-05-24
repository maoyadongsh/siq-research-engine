import { useState, useRef, useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Brain, CheckCircle2, Database, Download, ExternalLink, FileText, FolderOpen, Loader2, PlayCircle, RefreshCw, Search, Settings2, UploadCloud } from 'lucide-react'
import { copyText } from '../lib/clipboard'

const PDF_API = '/pdfapi'

/* ── Types ── */
interface LogEntry { time: string; level: string; message: string }
interface PdfCtx {
  sourcePage: number; currentPage: number; pageCount: number
  bbox: number[]; bboxExtent: { width: number; height: number }
  selectedTrace: { pageNumber: number; bbox: number[]; source: string; confidence: string } | null
}
interface SrcCtx {
  selectedTableIndex: number; sourcePage: number
  readingMode: 'table' | 'page'; tableHtml: string; correctionText: string
  selectedCell: { rowIndex: number; cellIndex: number; text: string } | null
  pageCache: Record<number, any>
}
interface DownloadedPdf {
  id: string
  company: string
  category: string
  filename: string
  relativePath: string
  size: number
  mtime: string
  url: string
}

/* ── CSS (matches original index.html styles) ── */
const CSS = `
.pdf-drop-zone{border:1.5px dashed #d8e1ef;border-radius:18px;padding:42px 24px;text-align:center;cursor:pointer;transition:all .2s;background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:inset 0 1px 0 rgba(255,255,255,.8)}
.pdf-drop-zone:hover,.pdf-drop-zone.dragover{border-color:#0052ff;background:rgba(0,82,255,.04);box-shadow:0 14px 34px rgba(37,99,235,.08)}
.pdf-source-choice{border:1px solid #e2e8f0;border-radius:18px;background:linear-gradient(180deg,#fff,#fbfdff);padding:16px;box-shadow:0 8px 22px rgba(15,23,42,.035)}
.pdf-source-choice-head{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px}
.pdf-source-choice-head h3{margin:0;font-size:1rem;color:#0f172a}
.pdf-source-choice-head p{margin:4px 0 0;color:#64748b;font-size:.88rem;line-height:1.5}
.pdf-download-search{display:flex;align-items:center;gap:8px;min-width:min(100%,360px)}
.pdf-download-search label{position:relative;display:block;flex:1;min-width:200px}
.pdf-download-search svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#64748b}
.pdf-download-search input{width:100%;height:42px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;padding:0 12px 0 38px;color:#0f172a;font:inherit;font-size:.9rem;outline:none}
.pdf-download-search input:focus{border-color:#2563eb;background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.pdf-icon-btn{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;color:#334155;cursor:pointer;transition:all .15s}
.pdf-icon-btn:hover{border-color:#2563eb;color:#2563eb;background:#eff6ff}
.pdf-icon-btn:disabled{opacity:.55;cursor:not-allowed}
.pdf-download-list{display:grid;gap:8px;max-height:310px;overflow:auto;scrollbar-gutter:stable}
.pdf-download-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;border:1px solid #e2e8f0;border-radius:14px;background:#fff;padding:12px;transition:border-color .15s,background .15s,box-shadow .15s}
.pdf-download-item:hover{border-color:#bfdbfe;background:#f8fbff;box-shadow:0 8px 20px rgba(15,23,42,.04)}
.pdf-download-main{display:flex;gap:10px;align-items:flex-start;min-width:0}
.pdf-download-main svg{flex:0 0 auto;margin-top:2px;color:#2563eb}
.pdf-download-title{font-weight:700;color:#0f172a;word-break:break-word;line-height:1.45}
.pdf-download-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:5px;color:#64748b;font-size:.78rem;line-height:1.4}
.pdf-download-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.pdf-small-action{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:38px;border:1px solid #cbd5e1;border-radius:12px;background:#fff;color:#334155;padding:0 12px;font:inherit;font-size:.84rem;font-weight:700;cursor:pointer;transition:all .15s;white-space:nowrap}
.pdf-small-action:hover{border-color:#2563eb;color:#2563eb;background:#eff6ff}
.pdf-small-action.primary{border-color:#2563eb;background:#2563eb;color:#fff}
.pdf-small-action.primary:hover{background:#1d4ed8;color:#fff}
.pdf-small-action:disabled{opacity:.55;cursor:not-allowed}
.pdf-source-separator{display:flex;align-items:center;gap:12px;margin:16px 0;color:#64748b;font-size:.84rem;font-weight:700}
.pdf-source-separator:before,.pdf-source-separator:after{content:"";height:1px;background:#e2e8f0;flex:1}
.pdf-stage{border:1px solid #e2e8f0;border-radius:18px;padding:18px;margin-bottom:12px;background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:0 8px 22px rgba(15,23,42,.035)}
.pdf-pbar-wrap{height:10px;background:#e2e8f0;border-radius:5px;overflow:hidden}
.pdf-pbar{height:100%;width:0%;background:linear-gradient(90deg,#0052ff,#4d7cff);border-radius:5px;transition:width .4s ease}
.pdf-pbar.done{background:#16a34a}
.pdf-status-badge{display:inline-flex;align-items:center;gap:6px;min-height:28px;border:1px solid rgba(216,225,236,.92);background:rgba(255,255,255,.78);color:#475569;padding:4px 10px;border-radius:999px;font-size:.75rem;font-weight:750;text-transform:none;letter-spacing:0;white-space:nowrap}
.pdf-status-badge.queued,.pdf-status-badge.pending{border-color:rgba(202,138,4,.2);background:rgba(202,138,4,.085);color:#a16207}
.pdf-status-badge.uploading,.pdf-status-badge.processing,.pdf-status-badge.submitting{border-color:rgba(0,113,227,.18);background:rgba(0,113,227,.075);color:#0071e3}
.pdf-status-badge.uploaded,.pdf-status-badge.submitted,.pdf-status-badge.cancelled{border-color:rgba(216,225,236,.92);background:rgba(255,255,255,.78);color:#475569}
.pdf-status-badge.completed{border-color:rgba(22,163,74,.18);background:rgba(22,163,74,.075);color:#15803d}
.pdf-status-badge.failed,.pdf-status-badge.error{border-color:rgba(220,38,38,.18);background:rgba(220,38,38,.075);color:#b91c1c}
.pdf-log{max-height:240px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:14px;padding:12px;background:#fbfdff;font-family:"SF Mono",Monaco,"Cascadia Code",monospace;font-size:.82rem;line-height:1.6}
.pdf-md-preview{background:#ffffff;color:#0f172a;border:1px solid #e2e8f0;border-radius:16px;padding:16px;font-family:"JetBrains Mono","SF Mono",Monaco,"Cascadia Code",monospace;font-size:.9rem;line-height:1.7;max-height:500px;overflow:auto;white-space:normal;word-break:break-word;box-shadow:0 8px 24px rgba(15,23,42,.04)}
.pdf-md-line{display:grid;grid-template-columns:56px minmax(0,1fr);gap:12px;min-height:22px;border-radius:4px}
.pdf-md-line.focus{background:rgba(37,99,235,.35);outline:1px solid rgba(96,165,250,.8)}
.pdf-md-line-no{color:#7d8590;text-align:right;user-select:none;font-variant-numeric:tabular-nums}
.pdf-md-line-text{white-space:pre-wrap}
.pdf-quality-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
.pdf-quality-grid>div{border:1px solid #e2e8f0;border-radius:12px;padding:10px 12px;background:#fff}
.pdf-quality-grid strong{display:block;font-size:1.2rem;color:#2563eb}
.pdf-quality-grid span{display:block;color:#64748b;font-size:.82rem;margin-top:2px}
.pdf-quality-row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid #e2e8f0;font-size:.9rem}
.pdf-quality-row span{color:#64748b;flex-shrink:0}
.pdf-quality-row b{text-align:right;font-weight:600}
.pdf-quality-section{border-top:1px solid #e2e8f0;padding-top:10px;margin-top:8px}
.pdf-quality-section-title{color:#64748b;font-size:.86rem;font-weight:600;margin-bottom:8px}
.pdf-chip-row{display:flex;gap:8px;flex-wrap:wrap}
.pdf-chip{display:inline-flex;align-items:center;min-height:28px;border:1px solid rgba(216,225,236,.92);background:rgba(255,255,255,.78);color:#475569;border-radius:999px;padding:4px 10px;font-size:.75rem;font-weight:750;white-space:nowrap}
.pdf-chip-secondary{border-color:rgba(216,225,236,.92);background:rgba(255,255,255,.78);color:#475569}
.pdf-chip-missing{border-color:rgba(216,225,236,.92);background:rgba(248,250,252,.82);color:#64748b}
.pdf-chip.trace-chip{cursor:pointer}
.pdf-chip.trace-chip:hover,.pdf-trace-btn:hover{border-color:rgba(0,113,227,.24);background:rgba(0,113,227,.075);color:#0071e3}
.pdf-trace-btn{border:1px solid rgba(216,225,236,.92);background:rgba(255,255,255,.78);color:#475569;border-radius:999px;padding:4px 10px;cursor:pointer;font-size:.75rem;font-weight:750}
.pdf-source-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:12px}
.pdf-source-summary>div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px}
.pdf-source-summary strong{display:block;color:#2563eb;font-size:1.05rem}
.pdf-source-summary span{display:block;color:#64748b;font-size:.8rem;margin-top:2px}
.pdf-source-meta{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #e2e8f0;padding:8px 0;font-size:.9rem}
.pdf-source-meta span{color:#64748b;flex-shrink:0}
.pdf-source-meta b{text-align:right}
.pdf-source-block{border-top:1px solid #e2e8f0;padding-top:12px;margin-top:8px}
.pdf-source-block h4{margin:0 0 8px;font-size:.92rem}
.pdf-source-line{display:grid;grid-template-columns:48px minmax(0,1fr);gap:10px;font-family:"SF Mono",Monaco,"Cascadia Code",monospace;font-size:.82rem;padding:3px 6px;border-radius:4px}
.pdf-source-line.focus{background:#eff6ff;color:#1e40af}
.pdf-source-line span{color:#64748b;text-align:right;user-select:none}
.pdf-source-line code{white-space:pre-wrap;word-break:break-word}
.pdf-workbench{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(480px,.95fr);gap:16px;align-items:start}
.pdf-source-pane{border-top:none;padding-top:0;margin-top:0;display:flex;flex-direction:column;min-width:0}
.pdf-source-pane-head{display:flex;flex-direction:column;justify-content:flex-start;gap:6px;min-height:50px;margin-bottom:8px}
.pdf-source-pane-head h4{margin:0}
.pdf-reading-topline{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.pdf-reading-mode-switch{display:inline-flex;align-items:center;gap:6px}
.pdf-reading-mode-btn{border:1px solid #e2e8f0;background:#fff;color:#1e293b;border-radius:999px;padding:5px 10px;font:inherit;font-size:.78rem;cursor:pointer}
.pdf-reading-mode-btn.active,.pdf-reading-mode-btn:hover{border-color:#2563eb;background:#eff6ff;color:#2563eb}
.pdf-reading-body{height:640px;max-height:640px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;background:#fff;scrollbar-gutter:stable both-edges}
.pdf-table-wrap{max-height:620px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;background:#fff;scrollbar-gutter:stable both-edges}
.pdf-source-pane .pdf-table-wrap{height:640px;max-height:640px}
.pdf-table-wrap table{border-collapse:collapse;width:max-content;min-width:100%;font-size:.86rem;line-height:1.45}
.pdf-table-wrap th,.pdf-table-wrap td{border:1px solid #cbd5e1;padding:7px 9px;vertical-align:top;background:#fff}
.pdf-table-wrap tr:first-child td,.pdf-table-wrap th{background:#f1f5f9;font-weight:700;color:#0f172a;position:sticky;top:0;z-index:1}
.pdf-table-wrap td:first-child,.pdf-table-wrap th:first-child{position:sticky;left:0;z-index:2;background:#f8fafc;font-weight:600;min-width:160px}
.pdf-table-wrap tr:first-child td:first-child,.pdf-table-wrap th:first-child{z-index:3;background:#e2e8f0}
.pdf-editable th[contenteditable="true"],.pdf-editable td[contenteditable="true"]{cursor:text}
.pdf-editable th.selected-cell,.pdf-editable td.selected-cell{outline:2px solid #f97316;outline-offset:-2px;background:#ffedd5!important}
.pdf-editable th[contenteditable="true"]:focus,.pdf-editable td[contenteditable="true"]:focus{outline:2px solid #2563eb;outline-offset:-2px;background:#eff6ff}
.pdf-page-viewer{border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;background:#f8fafc;display:flex;flex-direction:column;height:640px}
.pdf-page-toolbar{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid #e2e8f0;font-size:.84rem;color:#64748b}
.pdf-page-topline{display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap}
.pdf-page-nav{display:inline-flex;align-items:center;gap:6px}
.pdf-nav-btn{border:1px solid #e2e8f0;background:#fff;color:#1e293b;border-radius:6px;padding:3px 8px;font:inherit;font-size:.78rem;cursor:pointer}
.pdf-nav-btn:hover{border-color:#2563eb;color:#2563eb;background:#eff6ff}
.pdf-nav-btn:disabled{opacity:.45;cursor:not-allowed}
.pdf-page-input{width:68px;border:1px solid #e2e8f0;border-radius:6px;padding:3px 6px;font:inherit;font-size:.78rem;color:#1e293b;background:#fff}
.pdf-zoom-controls{display:inline-flex;align-items:center;gap:4px}
.pdf-zoom-btn{border:1px solid #e2e8f0;background:#fff;color:#1e293b;border-radius:6px;padding:3px 7px;font:inherit;font-size:.78rem;cursor:pointer}
.pdf-zoom-btn.active,.pdf-zoom-btn:hover{border-color:#2563eb;color:#2563eb;background:#eff6ff}
.pdf-page-canvas{position:relative;flex:1;min-height:0;overflow:auto;background:#e5e7eb;scrollbar-gutter:stable both-edges}
.pdf-page-stage{position:relative;display:inline-block;min-width:100%;transform-origin:top left}
.pdf-page-canvas img{display:block;width:100%;height:auto}
.pdf-page-viewer[data-zoom="1"] .pdf-page-stage{min-width:960px}
.pdf-page-viewer[data-zoom="1.5"] .pdf-page-stage{min-width:1440px}
.pdf-page-viewer[data-zoom="2"] .pdf-page-stage{min-width:1920px}
.pdf-bbox{position:absolute;border:2px solid #ef4444;background:rgba(239,68,68,.12);box-shadow:0 0 0 9999px rgba(15,23,42,.04);pointer-events:none}
.pdf-bbox-selected{border-color:#f97316;background:rgba(249,115,22,.22);box-shadow:none;z-index:2}
.pdf-bbox-text{border-color:#2563eb;background:rgba(37,99,235,.18);box-shadow:none;z-index:2}
.pdf-correction-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.pdf-correction-toolbar label{display:inline-flex;align-items:center;gap:6px;color:#64748b;font-size:.86rem}
.pdf-correction-toolbar select{border:1px solid #e2e8f0;border-radius:8px;background:#fff;padding:5px 8px;color:#1e293b;font:inherit}
.pdf-correction-editor,.pdf-correction-note{width:100%;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;resize:vertical;color:#1e293b;background:#fff;font-family:"SF Mono",Monaco,"Cascadia Code",monospace;line-height:1.5}
.pdf-correction-editor{min-height:220px;font-size:.84rem}
.pdf-correction-note{min-height:74px;margin-top:8px;font-size:.86rem}
.pdf-artifact-row{display:grid;grid-template-columns:minmax(160px,200px) minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid #f1f5f9;font-size:.85rem}
.pdf-artifact-row.missing{opacity:.62}
.pdf-artifact-name{display:flex;flex-direction:column;gap:2px;min-width:0}
.pdf-artifact-name span{font-weight:700;color:#1e293b}
.pdf-artifact-name small{font-size:.76rem;color:#64748b;line-height:1.35}
.pdf-artifact-row code{color:#64748b;word-break:break-word}
.pdf-artifact-actions{display:flex;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap}
.pdf-pipeline-note{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:flex-start;border:1px solid #bfdbfe;background:#eff6ff;color:#1e3a8a;border-radius:14px;padding:12px 14px;font-size:.84rem;line-height:1.55}
.pdf-pipeline-note svg{margin-top:2px;color:#2563eb;flex:0 0 auto}
.pdf-preflight-list{display:grid;gap:8px;margin-top:12px}
.pdf-preflight-item{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px;align-items:start;border:1px solid #e2e8f0;border-radius:12px;background:#fff;padding:9px 10px;font-size:.82rem;line-height:1.45}
.pdf-preflight-dot{width:8px;height:8px;border-radius:999px;background:#16a34a;margin-top:6px}
.pdf-preflight-item.warn .pdf-preflight-dot{background:#ca8a04}
.pdf-preflight-item.error .pdf-preflight-dot{background:#dc2626}
.pdf-preflight-title{font-weight:750;color:#0f172a}
.pdf-preflight-message{color:#64748b;word-break:break-word}
.pdf-task-item{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;margin-bottom:8px;font-size:.9rem;cursor:pointer;transition:background .15s,border-color .15s}
.pdf-task-item:hover{background:#eff6ff;border-color:#bfdbfe}
.pdf-task-item .task-name{font-weight:500}
.pdf-task-item .task-meta{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}
.pdf-task-delete{border:1px solid #fecaca;background:#fff1f2;color:#b91c1c;border-radius:999px;padding:5px 10px;font-size:.78rem;cursor:pointer}
.pdf-task-delete:hover{background:#ffe4e6}
.pdf-task-action{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:999px;padding:5px 10px;font-size:.78rem;cursor:pointer}
.pdf-task-action:hover{background:#f1f5f9}
.pdf-page-reading-view{padding:12px;display:grid;gap:10px}
.pdf-page-reading-summary{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc}
.pdf-page-reading-summary strong{display:block;color:#2563eb;font-size:1rem}
.pdf-page-reading-summary span{display:block;color:#64748b;font-size:.8rem;margin-top:2px}
.pdf-page-block{border:1px solid #e2e8f0;border-radius:8px;background:#fff;padding:10px 12px}
.pdf-page-block.focus-table{border-color:#93c5fd;box-shadow:0 0 0 2px rgba(37,99,235,.08)}
.pdf-page-block-muted{background:#f8fafc}
.pdf-page-block-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px}
.pdf-page-block-type{display:inline-block;font-size:.78rem;font-weight:700;color:#2563eb}
.pdf-page-block-meta{display:block;margin-top:2px;color:#64748b;font-size:.76rem}
.pdf-page-block-tag-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.pdf-page-block-tag{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;font-size:.76rem}
.pdf-page-block-text{white-space:pre-wrap;word-break:break-word;font-size:.88rem;line-height:1.6}
.pdf-page-block-heading{font-weight:700;color:#0f172a}
.pdf-page-block-list{margin:0;padding-left:20px;font-size:.88rem;line-height:1.6}
.pdf-page-table-wrap{height:auto!important;max-height:420px!important}
@media(max-width:1200px){.pdf-workbench{grid-template-columns:1fr}}
@media(max-width:720px){.pdf-artifact-row,.pdf-download-item{grid-template-columns:1fr}.pdf-artifact-actions,.pdf-download-actions{justify-content:flex-start}.pdf-download-search{width:100%}}
`

/* ── Helpers ── */
function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDuration(seconds: number) {
  if (seconds == null || seconds < 0) return '--'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? m + '分' + s + '秒' : s + '秒'
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatFinancialNumber(value: number) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '--'
  const a = Math.abs(n)
  if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (a >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function escHtml(text: string) {
  const d = document.createElement('div')
  d.textContent = text
  return d.innerHTML
}

const statusLabels: Record<string, string> = {
  queued: '已排队', uploaded: '已上传', submitting: '提交中', submitted: '已提交',
  pending: '排队中', processing: '处理中', completed: '已完成',
  completed_missing_artifact: '结果缺失', failed: '失败', error: '错误', cancelled: '已停止',
}
function translateStatus(s: string) { return statusLabels[s] || s }
function isTerminal(s: string) {
  return ['completed','completed_missing_artifact','success','done','finished','failed','error','failure','cancelled'].includes(s)
}

function sanitizeTableHtml(html: string | null): string {
  if (!html) return ''
  const doc = new DOMParser().parseFromString(String(html), 'text/html')
  doc.querySelectorAll('script,style,iframe,object,embed,link,meta').forEach(n => n.remove())
  const ok = new Set(['TABLE','THEAD','TBODY','TFOOT','TR','TH','TD','CAPTION','COLGROUP','COL'])
  doc.body.querySelectorAll('*').forEach(node => {
    if (!ok.has(node.tagName)) { node.replaceWith(document.createTextNode(node.textContent || '')); return }
    Array.from(node.attributes).forEach(a => {
      if (!['rowspan','colspan','data-bbox','data-cell-bbox','bbox'].includes(a.name.toLowerCase())) node.removeAttribute(a.name)
    })
  })
  return doc.body.innerHTML
}

function makeEditableHtml(html: string): string {
  if (!html) return ''
  const doc = new DOMParser().parseFromString(sanitizeTableHtml(html), 'text/html')
  doc.querySelectorAll('th,td').forEach(c => {
    c.setAttribute('contenteditable','true'); c.setAttribute('spellcheck','false'); c.setAttribute('tabindex','0')
  })
  return doc.body.innerHTML
}

function serializeEditableTable(wrap: HTMLElement | null): string {
  const table = wrap?.querySelector('table')
  if (!table) return ''
  const clone = table.cloneNode(true) as HTMLElement
  clone.querySelectorAll('[contenteditable],[spellcheck],[tabindex]').forEach(n => {
    n.removeAttribute('contenteditable'); n.removeAttribute('spellcheck'); n.removeAttribute('tabindex')
  })
  clone.querySelectorAll('.selected-cell,.selected-row').forEach(n => {
    n.classList.remove('selected-cell','selected-row')
    if (!n.getAttribute('class')) n.removeAttribute('class')
  })
  return clone.outerHTML
}

function parseBbox(v: any): number[] | null {
  if (Array.isArray(v)) { const b = v.map(Number); return b.length===4 && b.every(Number.isFinite) ? b : null }
  if (!v) return null
  const b = String(v).replace(/[\[\]]/g,'').split(/[,\s]+/).filter(Boolean).map(Number)
  return b.length===4 && b.every(Number.isFinite) ? b : null
}

function normalizeBbox(bbox: number[]|null, extent?: {width:number;height:number}): number[]|null {
  if (!bbox || bbox.length!==4) return null
  if (bbox.every(v=>v>=0&&v<=1) && extent?.width && extent?.height)
    return [bbox[0]*extent.width, bbox[1]*extent.height, bbox[2]*extent.width, bbox[3]*extent.height]
  return bbox
}

function scopeName(s: string) {
  if (s==='consolidated') return '合并'
  if (s==='parent_company') return '母公司'
  return s||'--'
}

function candidateMeta(item: any): string {
  if (!item || item.status==='missing') return '需复核：未在表格中定位'
  if (!item.table_index) return item._source==='financial_data'?'已抽取，暂无表格定位':'需复核：暂无表格定位'
  const page = item.pdf_page_number ? ` / PDF ${item.pdf_page_number}页${item.pdf_page_source==='markdown_marker_inferred'?'(推断)':''}` : ''
  const cMap: Record<string,string> = {high:'高置信',medium:'中置信',low:'低置信'}
  const conf = item.confidence ? ` / ${cMap[item.confidence]||item.confidence}` : ''
  const line = item.line ? ` / 行 ${item.line}` : ''
  return `表 ${item.table_index}${line}${page}${conf}`
}

function suspectTableMeta(item: any): string {
  if (!item || !item.table_index) return '表 未定位'
  const line = item.line ? ` / 行 ${item.line}` : ''
  const page = item.pdf_page_number ? ` / PDF ${item.pdf_page_number}页${item.pdf_page_source==='markdown_marker_inferred'?'(推断)':''}` : ''
  return `表 ${item.table_index}${line}${page}`
}

function suspectReasons(reasons: string[]): string {
  const m: Record<string,string> = {single_row:'单行/空壳',many_empty_cells:'空单元格偏多',low_numeric_density:'数字密度偏低',
    key_table_too_short:'关键表过短',low_confidence_core_candidate:'低置信核心候选',medium_confidence_core_candidate:'中置信核心候选'}
  return (reasons||[]).map(r=>m[r]||r).join('、')
}

function normalizeCellText(t:string) { return String(t||'').replace(/\s+/g,'').replace(/[，,]/g,'').trim() }
function isUsefulTextAnchor(t:string) {
  const n = normalizeCellText(t)
  if (n.length<6) return false
  if (/^[\d.\-+()%（）/／—–_]+$/.test(n)) return false
  if (/^(--|-|不适用|无|否|是|0|0.00)$/.test(n)) return false
  return true
}

const WIKI_INPUT_ARTIFACTS = [
  'result.md',
  'result_complete.md',
  'document_full.json',
  'content_list_enhanced.json',
  'financial_data.json',
  'financial_checks.json',
  'quality_report.json',
  'table_index.json',
]

const artifactRoles: Record<string, string> = {
  'result.md': '原始 Markdown 文本',
  'result_complete.md': '增强 Markdown，含结构补充',
  'document_full.json': '总包索引，供 Wiki 与入库读取',
  'content_list_enhanced.json': '增强结构块与页码信息',
  'quality_report.json': '解析质量与表格索引来源',
  'table_index.json': '表格定位与溯源索引',
  'financial_data.json': '规则抽取的财务指标',
  'financial_checks.json': '财务抽取校验结果',
  'middle.json': 'MinerU 中间结构',
  'content_list.json': 'MinerU 原始内容块',
  'model_output.json': '模型输出原始结构',
  images: '页面图片与视觉溯源素材',
}

function parseBboxFromAttr(el: HTMLElement): number[]|null {
  return parseBbox(el.dataset.cellBbox || el.dataset.bbox || el.getAttribute('bbox'))
}

function artifactUrl(info: any) {
  const raw = String(info?.url || '')
  if (!raw) return ''
  if (raw.startsWith('/api/artifact/')) return raw.replace(/^\/api/, PDF_API)
  return raw
}

function artifactDownloadName(name: string) {
  if (name === 'images') return 'images.zip'
  return name
}

function artifactDownloadUrl(name: string, info: any) {
  const url = artifactUrl(info)
  if (name === 'images' && url) return `${url}/download`
  return url
}

function pipelineArtifactSummary(artifacts: Record<string, any> | null) {
  const ready = WIKI_INPUT_ARTIFACTS.filter(name => artifacts?.[name]?.exists)
  return {
    ready,
    total: WIKI_INPUT_ARTIFACTS.length,
    missing: WIKI_INPUT_ARTIFACTS.filter(name => !artifacts?.[name]?.exists),
  }
}

function workflowReady(status: any, key: string) {
  return status?.[key]?.status === 'ready'
}

function workflowStateLabel(status: any) {
  const s = String(status || 'missing')
  if (s === 'ready') return '已就绪'
  if (s === 'stale') return '需刷新'
  if (s === 'needs_review') return '需复核'
  if (s === 'unknown') return '待确认'
  return '待处理'
}

function workflowStateClass(status: any) {
  const s = String(status || 'missing')
  if (s === 'ready') return 'secondary-status-success'
  if (s === 'stale' || s === 'needs_review' || s === 'unknown') return 'secondary-status-warning'
  return 'secondary-status-warning'
}

/* ── Component ── */
export default function PdfParsing() {
  const [searchParams] = useSearchParams()
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [dragover, setDragover] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const [showConfig, setShowConfig] = useState(false)
  const [backend, setBackend] = useState('hybrid-http-client')
  const [parseMethod, setParseMethod] = useState('auto')
  const [startPage, setStartPage] = useState('')
  const [endPage, setEndPage] = useState('')
  const [formula, setFormula] = useState(true)
  const [table, setTable] = useState(true)

  const taskIdRef = useRef<string|null>(null)
  const logCountRef = useRef(0)
  const cancelledRef = useRef(false)
  const pollRef = useRef<ReturnType<typeof setInterval>|null>(null)
  const uploadRef = useRef<ReturnType<typeof setInterval>|null>(null)

  const [health, setHealth] = useState<{mineru:boolean;vlm:boolean;submit_ready:boolean;warning?:string}|null>(null)

  const [uploadActive, setUploadActive] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [uploadStatusText, setUploadStatusText] = useState('')
  const [uploadBadge, setUploadBadge] = useState({cls:'uploaded',text:'上传中'})

  const [parseActive, setParseActive] = useState(false)
  const [parsePct, setParsePct] = useState(0)
  const [parseStatusText, setParseStatusText] = useState('')
  const [parseBadge, setParseBadge] = useState({cls:'pending',text:'等待中'})
  const [queueInfo, setQueueInfo] = useState('')
  const [elapsedInfo, setElapsedInfo] = useState('')
  const [pagesInfo, setPagesInfo] = useState('')
  const [stageInfo, setStageInfo] = useState('')

  const [logs, setLogs] = useState<LogEntry[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  const [error, setError] = useState<string|null>(null)
  const [toast, setToast] = useState('')
  const toastRef = useRef<ReturnType<typeof setTimeout>|null>(null)

  const [markdown, setMarkdown] = useState('')
  const [mdLines, setMdLines] = useState<string[]>([])
  const [focusedLine, setFocusedLine] = useState<number|null>(null)
  const mdRef = useRef<HTMLDivElement>(null)

  const [artifacts, setArtifacts] = useState<Record<string,any>|null>(null)
  const [quality, setQuality] = useState<any>(null)
  const [financial, setFinancial] = useState<any>(null)

  const [sourceVisible, setSourceVisible] = useState(false)
  const pdfCtx = useRef<PdfCtx|null>(null)
  const srcCtx = useRef<SrcCtx|null>(null)
  const [pdfZoom, setPdfZoom] = useState('fit')
  const [pdfCurPage, setPdfCurPage] = useState(1)
  const [readingMode, setReadingMode] = useState<'table'|'page'>('page')
  const [readingHtml, setReadingHtml] = useState('')
  const editTableRef = useRef<HTMLDivElement>(null)
  const corrTextRef = useRef<HTMLTextAreaElement>(null)
  const corrNoteRef = useRef<HTMLTextAreaElement>(null)
  const corrStatusRef = useRef<HTMLSelectElement>(null)

  const [srcTable, setSrcTable] = useState<any>(null)
  const [srcMeta, setSrcMeta] = useState<{table:any;correction:any;excerpt:any[];artifacts:Record<string,any>;pdfPageImage:any}|null>(null)

  const [tasks, setTasks] = useState<any[]>([])
  const [uploading, setUploading] = useState(false)
  const [downloadedReports, setDownloadedReports] = useState<DownloadedPdf[]>([])
  const [downloadedLoading, setDownloadedLoading] = useState(false)
  const [downloadedQuery, setDownloadedQuery] = useState('')
  const [downloadedBusyPath, setDownloadedBusyPath] = useState('')
  const [workflowStatus, setWorkflowStatus] = useState<any>(null)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [workflowBusy, setWorkflowBusy] = useState('')
  const [workflowJob, setWorkflowJob] = useState<any>(null)
  const [workflowError, setWorkflowError] = useState('')
  const localArtifactSummary = pipelineArtifactSummary(artifacts)
  const backendArtifactSummary = workflowStatus?.artifactBundle
  const artifactReadyCount = backendArtifactSummary?.readyCount ?? localArtifactSummary.ready.length
  const artifactTotal = backendArtifactSummary?.total ?? localArtifactSummary.total
  const artifactMissing = backendArtifactSummary?.missing ?? localArtifactSummary.missing
  const llmSemanticCounts = workflowStatus?.semantic?.llm?.counts || {}
  const llmSemanticDesc = workflowStatus?.semantic?.llm?.status === 'ready'
    ? `LLM 增强 ${llmSemanticCounts.claims || 0} 条判断 / ${llmSemanticCounts.risks || 0} 条风险`
    : (workflowStatus?.semantic?.llm?.message || '本地模型增强待生成')

  const loadDownloadedReports = useCallback(async (text: string) => {
    setDownloadedLoading(true)
    try {
      const r = await fetch(`/api/downloads/reports?q=${encodeURIComponent(text.trim())}&limit=120`)
      if (!r.ok) throw new Error(String(r.status))
      const d = await r.json()
      setDownloadedReports(d.reports || [])
    } catch {
      setDownloadedReports([])
    } finally {
      setDownloadedLoading(false)
    }
  }, [])

  useEffect(()=>{
    checkHealth(); loadTasks(); loadDownloadedReports('')
    const h = setInterval(checkHealth, 10000)
    return ()=>{ clearInterval(h); if(pollRef.current) clearInterval(pollRef.current); if(uploadRef.current) clearInterval(uploadRef.current) }
  },[loadDownloadedReports])

  useEffect(() => {
    const taskId = searchParams.get('task')
    if (!taskId || taskIdRef.current === taskId) return
    fetch(`${PDF_API}/status/${encodeURIComponent(taskId)}?since=0`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data) return
        resumeTask(taskId, data.filename || '', data.status || data.stage || 'completed')
      })
      .catch(() => {})
  }, [searchParams])

  useEffect(()=>{
    if(logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  },[logs])

  const showToast = useCallback((msg:string)=>{
    setToast(msg)
    if(toastRef.current) clearTimeout(toastRef.current)
    toastRef.current = setTimeout(()=>setToast(''),2500)
  },[])

  async function checkHealth() {
    try {
      const r = await fetch(`${PDF_API}/health`)
      const d = await r.json()
      setHealth({mineru:!!d.mineru,vlm:!!d.vlm,submit_ready:!!d.submit_ready,warning:d.warning||undefined})
    } catch { setHealth(null) }
  }

  async function loadTasks() {
    try {
      const r = await fetch(`${PDF_API}/tasks`)
      const d = await r.json()
      setTasks(d.tasks||[])
      if(!taskIdRef.current && d.tasks?.length) {
        const latest = d.tasks.find((t:any)=>t.markdown_ready&&['completed','success','done','finished'].includes(t.status))
          || d.tasks.find((t:any)=>['processing','pending','submitted','submitting'].includes(t.status))
          || d.tasks.find((t:any)=>t.status==='queued')
          || d.tasks.find((t:any)=>!isTerminal(t.status))
        if(latest) resumeTask(latest.task_id, latest.filename, latest.status)
      }
    } catch {}
  }

  async function downloadedReportToFile(report: DownloadedPdf): Promise<File> {
    const url = report.url || `/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`
    const r = await fetch(url)
    if(!r.ok) throw new Error('读取已下载 PDF 失败')
    const blob = await r.blob()
    const lastModified = new Date(report.mtime).getTime()
    return new File([blob], report.filename, { type: blob.type || 'application/pdf', lastModified: Number.isFinite(lastModified) ? lastModified : Date.now() })
  }

  async function selectDownloadedReport(report: DownloadedPdf) {
    setDownloadedBusyPath(report.relativePath)
    setError(null)
    try {
      const file = await downloadedReportToFile(report)
      setSelectedFiles([file])
      showToast('已选择已下载财报')
    } catch(e) {
      setError((e as Error).message)
    } finally {
      setDownloadedBusyPath('')
    }
  }

  async function parseDownloadedReport(report: DownloadedPdf) {
    setDownloadedBusyPath(report.relativePath)
    setError(null)
    try {
      const file = await downloadedReportToFile(report)
      await startConvertWithFiles([file])
    } catch(e) {
      setError((e as Error).message)
    } finally {
      setDownloadedBusyPath('')
    }
  }

  async function startConvertWithFiles(filesToUpload: File[]) {
    if(!filesToUpload.length) return
    await checkHealth(); setError(null); setUploading(true)
    cancelledRef.current = false; logCountRef.current = 0; setLogs([])
    setUploadActive(true); setUploadPct(0); setUploadStatusText('准备上传...'); setUploadBadge({cls:'uploaded',text:'上传中'})
    setParseActive(false)
    setMarkdown(''); setMdLines([]); setArtifacts(null); setQuality(null); setFinancial(null); setSourceVisible(false)

    let pct = 0
    uploadRef.current = setInterval(()=>{
      if(cancelledRef.current){clearInterval(uploadRef.current!);return}
      pct += Math.random()*15; if(pct>90) pct=90
      setUploadPct(pct); setUploadStatusText('正在上传并加入本地队列...')
    },300)

    const form = new FormData()
    filesToUpload.forEach(f=>form.append('files',f))
    form.append('backend',backend); form.append('parse_method',parseMethod)
    form.append('start_page_id',startPage); form.append('end_page_id',endPage)
    form.append('formula_enable',formula?'true':'false'); form.append('table_enable',table?'true':'false')

    try {
      const r = await fetch(`${PDF_API}/upload`,{method:'POST',body:form})
      clearInterval(uploadRef.current!)
      const d = await r.json()
      if(!r.ok) throw new Error(d.error||'上传失败')
      taskIdRef.current = d.task_id
      setUploadPct(100); setUploadStatusText('批量入队完成'); setUploadBadge({cls:'completed',text:'已完成'})
      setParseActive(true); setParsePct(0); setParseStatusText('已加入本地队列，等待轮到当前任务...')
      setParseBadge({cls:'queued',text:'已排队'}); setQueueInfo(''); setElapsedInfo(''); setPagesInfo(''); setStageInfo('')
      showToast(`已加入队列: ${d.batch_count||filesToUpload.length} 个 PDF`)
      setSelectedFiles([]); setUploading(false)
      startPolling(); loadTasks()
    } catch(e) {
      clearInterval(uploadRef.current!); setUploading(false); setError((e as Error).message)
      setUploadPct(0); setUploadStatusText('上传失败'); setUploadBadge({cls:'failed',text:'失败'})
    }
  }

  async function startConvert() {
    await startConvertWithFiles(selectedFiles)
  }

  function startPolling() {
    if(pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(pollStatus,1000); pollStatus()
  }

  async function pollStatus() {
    const tid = taskIdRef.current; if(!tid||cancelledRef.current) return
    try {
      const r = await fetch(`${PDF_API}/status/${encodeURIComponent(tid)}?since=${logCountRef.current}`)
      const d = await r.json(); if(!r.ok) throw new Error(d.error||'状态查询失败')
      updateStatus(d)
    } catch { setParseStatusText('状态查询失败，正在重试...') }
  }

  function updateStatus(data:any) {
    if(cancelledRef.current) return
    const stage = data.stage||data.status||'pending'
    const isFail = ['failed','error','failure','completed_missing_artifact'].includes(data.status)
    const isDone = ['completed','success','done','finished'].includes(data.status)
    setParseBadge({cls:stage,text:translateStatus(data.status||'pending')})
    setQueueInfo(data.local_queue_position?`本地队列位置: 第 ${data.local_queue_position} 位`:(data.queue_position!=null?`MinerU 队列前方: ${data.queue_position} 任务`:''))
    setElapsedInfo(!isFail&&data.elapsed_seconds!=null?`已耗时: ${formatDuration(data.elapsed_seconds)}`:'')
    if(!isFail&&data.total_pages&&data.processed_pages!=null){const rem=data.total_pages-data.processed_pages;setPagesInfo(`已完成 ${data.processed_pages}/${data.total_pages} 页, 还剩 ${rem} 页`)}
    else if(!isFail&&data.total_pages) setPagesInfo(`共 ${data.total_pages} 页`)
    else setPagesInfo('')
    const sMap:Record<string,string> = {queued:'已加入本地队列',uploaded:'文件已上传',submitting:'正在提交到 MinerU',submitted:'已提交到 MinerU',
      pending:'排队等待中',processing:'正在解析 PDF',completed:'解析完成',completed_missing_artifact:'结果缺失',failed:'解析失败',cancelled:'已停止查看'}
    setStageInfo(sMap[stage]||stage)
    let pct=0
    if(isDone) pct=100
    else if(stage==='processing'&&data.progress_percent!=null) pct=Math.max(0,Math.min(99,Number(data.progress_percent)))
    else if(stage==='processing'&&data.total_pages&&data.processed_pages!=null) pct=Math.round((data.processed_pages/data.total_pages)*100)
    setParsePct(pct); setParseStatusText(translateStatus(data.status||'pending'))
    if(data.logs?.length) setLogs(prev=>[...prev,...data.logs])
    logCountRef.current = typeof data.log_count==='number'?data.log_count:logCountRef.current+(data.logs?.length||0)
    if(isDone){setParsePct(100);setParseStatusText('解析完成!');setParseBadge({cls:'completed',text:'已完成'});if(pollRef.current){clearInterval(pollRef.current);pollRef.current=null}fetchResult();loadTasks();loadWorkflowStatus()}
    else if(isFail){setParsePct(0);setParseStatusText(data.status==='completed_missing_artifact'?'结果缺失':'解析失败');setParseBadge({cls:'failed',text:translateStatus(data.status)});setElapsedInfo('');setPagesInfo('');if(pollRef.current){clearInterval(pollRef.current);pollRef.current=null}setError(data.error||'转换失败')}
    else if(data.status==='cancelled'){setParsePct(0);setParseStatusText('已停止查看');setParseBadge({cls:'cancelled',text:'已停止'});if(pollRef.current){clearInterval(pollRef.current);pollRef.current=null}}
  }

  async function fetchResult() {
    const tid=taskIdRef.current; if(!tid) return
    try {
      const r=await fetch(`${PDF_API}/result/${encodeURIComponent(tid)}`);const d=await r.json()
      if(d.artifacts) setArtifacts(d.artifacts)
      if(d.markdown){setMarkdown(d.markdown);setMdLines(d.markdown.split(/\r?\n/));fetchQuality();fetchFinancial()}
    } catch {}
  }
  async function fetchQuality() {
    const tid=taskIdRef.current; if(!tid) return
    try{const r=await fetch(`${PDF_API}/quality/${encodeURIComponent(tid)}`);const d=await r.json();if(d.quality) setQuality(d.quality)}catch{}
  }
  async function fetchFinancial() {
    const tid=taskIdRef.current; if(!tid) return
    try{const r=await fetch(`${PDF_API}/financial/${encodeURIComponent(tid)}`);const d=await r.json();if(d.financial_checks) setFinancial(d)}catch{}
  }

  async function cancelTask() {
    const tid=taskIdRef.current; if(!tid) return
    if(!confirm('确定停止查看当前任务吗？\n如果 MinerU 支持取消，也会尝试通知后端停止处理。')) return
    try{
      const r=await fetch(`${PDF_API}/cancel/${encodeURIComponent(tid)}`,{method:'POST'});const d=await r.json()
      if(d.success){cancelledRef.current=true;if(pollRef.current){clearInterval(pollRef.current);pollRef.current=null}showToast(d.upstream_cancelled?'任务已取消':'已停止查看任务')}
    }catch{}
  }

  async function resumeTask(taskId:string,_filename:string,status:string) {
    if(!taskId) return
    taskIdRef.current=taskId; cancelledRef.current=false; logCountRef.current=0
    setLogs([]);setError(null);setUploadActive(false);setParseActive(true)
    setMarkdown('');setMdLines([]);setArtifacts(null);setQuality(null);setFinancial(null);setSourceVisible(false)
    setParsePct(0);setParseStatusText('正在恢复任务状态...');setParseBadge({cls:status,text:translateStatus(status)})
    try {
      if(['completed','success','done','finished'].includes(status)){
        setParsePct(100);setParseStatusText('解析完成');setParseBadge({cls:'completed',text:'已完成'});fetchResult();loadWorkflowStatus();showToast('已恢复任务视图');return
      }
      await pollStatus()
      if(pollRef.current) clearInterval(pollRef.current)
      const latest = (await fetch(`${PDF_API}/status/${encodeURIComponent(taskId)}?since=0`).then(r=>r.json()).catch(()=>null))?.status||status
      if(!isTerminal(latest)) startPolling()
      else if(['completed','success','done','finished'].includes(latest)) { fetchResult(); loadWorkflowStatus() }
      showToast('已恢复任务视图')
    } catch { setError('恢复任务状态失败') }
  }

  async function deleteTask(taskId:string,status:string) {
    if(!taskId) return; if(!isTerminal(status)){setError('请先停止或等待任务结束后再删除');return}
    if(!confirm('确定删除这条最近任务记录吗？')) return
    try{
      const r=await fetch(`${PDF_API}/tasks/${encodeURIComponent(taskId)}`,{method:'DELETE'})
      if(!r.ok){const d=await r.json();throw new Error(d.error||'删除失败')}
      if(taskIdRef.current===taskId){taskIdRef.current=null;resetAll()}
      loadTasks();showToast('任务记录已删除')
    }catch(e){setError((e as Error).message)}
  }

  async function refetchTask(taskId:string) {
    if(!taskId) return
    try{
      const r=await fetch(`${PDF_API}/refetch/${encodeURIComponent(taskId)}`,{method:'POST'})
      if(!r.ok){const d=await r.json();throw new Error(d.error||'重新拉取失败')}
      if(taskIdRef.current===taskId) fetchResult(); loadTasks(); showToast('结果已重新拉取')
    }catch(e){setError((e as Error).message)}
  }

  async function reparseTask(taskId:string) {
    if(!taskId) return; if(!confirm('确定基于原 PDF 创建一个重新解析任务吗？')) return
    try{
      const r=await fetch(`${PDF_API}/reparse/${encodeURIComponent(taskId)}`,{method:'POST'});const d=await r.json()
      if(!r.ok) throw new Error(d.error||'重新解析失败')
      loadTasks(); resumeTask(d.task_id,d.filename,'queued'); showToast('重新解析任务已入队')
    }catch(e){setError((e as Error).message)}
  }

  const loadWorkflowStatus = useCallback(async () => {
    const tid = taskIdRef.current
    if(!tid) return
    setWorkflowLoading(true)
    setWorkflowError('')
    try {
      const r = await fetch(`/api/workflow/task/${encodeURIComponent(tid)}/status`)
      const d = await r.json()
      if(!r.ok) throw new Error(d.detail || '状态查询失败')
      setWorkflowStatus(d)
    } catch(e) {
      setWorkflowStatus({error:(e as Error).message})
    } finally {
      setWorkflowLoading(false)
    }
  }, [])

  async function runWorkflowStep(step:'wiki-import'|'semantic'|'db-import') {
    const tid = taskIdRef.current
    if(!tid) return
    setWorkflowBusy(step)
    setWorkflowError('')
    try {
      const r = await fetch(`/api/workflow/task/${encodeURIComponent(tid)}/${step}`, {method:'POST'})
      const d = await r.json()
      if(!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail || d))
      showToast(step==='wiki-import'?'已导入 Wiki':step==='semantic'?'语义层已生成':'已导入 PostgreSQL')
      await loadWorkflowStatus()
    } catch(e) {
      const message = (e as Error).message
      setWorkflowError(message)
      setError(message)
      showToast(step==='db-import'?'PostgreSQL 导入失败':step==='semantic'?'语义层生成失败':'Wiki 导入失败')
    } finally {
      setWorkflowBusy('')
    }
  }

  async function runRemainingWorkflow() {
    const tid = taskIdRef.current
    if(!tid) return
    setWorkflowBusy('run-remaining')
    setWorkflowError('')
    setWorkflowJob(null)
    try {
      const r = await fetch(`/api/workflow/task/${encodeURIComponent(tid)}/run-remaining`, {method:'POST'})
      const d = await r.json()
      if(!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail || d))
      setWorkflowJob(d)
      showToast('数据管道已开始运行')
    } catch(e) {
      const message = (e as Error).message
      setWorkflowError(message)
      setError(message)
      showToast('数据管道启动失败')
      setWorkflowBusy('')
    }
  }

  useEffect(() => {
    const jobId = workflowJob?.jobId
    if(!jobId || !['queued','running'].includes(workflowJob?.status)) return
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`/api/workflow/job/${encodeURIComponent(jobId)}`)
        const d = await r.json()
        if(!r.ok) throw new Error(d.detail || '任务状态查询失败')
        setWorkflowJob(d)
        if(['succeeded','failed'].includes(d.status)) {
          setWorkflowBusy('')
          await loadWorkflowStatus()
          showToast(d.status === 'succeeded' ? '数据管道已完成' : '数据管道运行失败')
        }
      } catch(e) {
        setWorkflowBusy('')
        setWorkflowError((e as Error).message)
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [workflowJob?.jobId, workflowJob?.status, loadWorkflowStatus, showToast])

  function resetAll() {
    setSelectedFiles([]);setError(null);setUploadActive(false);setParseActive(false)
    setLogs([]);setMarkdown('');setMdLines([]);setArtifacts(null);setQuality(null);setFinancial(null);setSourceVisible(false)
    if(pollRef.current){clearInterval(pollRef.current);pollRef.current=null}
    if(uploadRef.current){clearInterval(uploadRef.current);uploadRef.current=null}
  }

  /* ── Source traceability ── */
  async function showTableSource(tableIndex:number,line?:number) {
    const tid=taskIdRef.current; if(!tid||!tableIndex) return
    focusMarkdownLine(Number(line))
    try{
      const r=await fetch(`${PDF_API}/source/${encodeURIComponent(tid)}/table/${encodeURIComponent(tableIndex)}`)
      const d=await r.json(); if(!r.ok) throw new Error(d.error||'溯源失败')
      const tbl=d.table||{}
      const rendered=sanitizeTableHtml(d.table_html||'')
      const corr=d.correction||{}
      const corrText=corr.table_markdown||d.table_html||''
      pdfCtx.current = d.pdf_page_image?.url ? {
        sourcePage:Number(d.pdf_page_image.page_number||1),currentPage:Number(d.pdf_page_image.page_number||1),
        pageCount:Number(d.pdf_page_image.page_count||d.pdf_page_image.page_number||1),
        bbox:d.pdf_page_image.bbox||[],bboxExtent:d.pdf_page_image.bbox_extent||{},selectedTrace:null
      } : null
      srcCtx.current = {
        selectedTableIndex:Number(tbl.table_index||tableIndex||0),
        sourcePage:Number(d.pdf_page_image?.page_number||tbl.pdf_page_number||1),
        readingMode:'page',tableHtml:rendered,correctionText:corrText,selectedCell:null,
        pageCache:d.page_content?.page_number?{[d.page_content.page_number]:d.page_content}:{}
      }
      setSrcTable(tbl); setSrcMeta({table:tbl,correction:corr,excerpt:d.markdown_excerpt||[],artifacts:d.artifacts||{},pdfPageImage:d.pdf_page_image})
      setReadingMode('page'); setPdfCurPage(pdfCtx.current?.currentPage||1); setPdfZoom('fit'); setSourceVisible(true)
      setTimeout(()=>{
        if(corrStatusRef.current) corrStatusRef.current.value=corr.review_status||'unreviewed'
        if(corrTextRef.current) corrTextRef.current.value=corrText
        if(corrNoteRef.current) corrNoteRef.current.value=corr.note||''
        renderReadingPane()
      },50)
    }catch(e){setError((e as Error).message)}
  }

  function focusMarkdownLine(line:number) {
    if(!line) return; setFocusedLine(line)
    setTimeout(()=>{mdRef.current?.querySelector(`[data-line="${line}"]`)?.scrollIntoView({behavior:'smooth',block:'center'})},100)
  }

  function getPdfUrl(page:number) {
    const tid=taskIdRef.current; if(!tid) return ''
    return `${PDF_API}/pdf_page/${encodeURIComponent(tid)}/${encodeURIComponent(page)}`
  }

  function updatePdfViewer(page:number) {
    const ctx=pdfCtx.current; if(!ctx) return
    const next=Math.max(1,Math.min(ctx.pageCount,page)); ctx.currentPage=next; setPdfCurPage(next)
    if(srcCtx.current&&srcCtx.current.readingMode==='page') renderReadingPane()
  }

  async function renderReadingPane() {
    const ctx=srcCtx.current; if(!ctx) return
    if(ctx.readingMode==='table'){
      const e=makeEditableHtml(ctx.correctionText||ctx.tableHtml||'')
      setReadingHtml(e||'')
      setTimeout(bindEditableTable,50); return
    }
    const pageNum=pdfCtx.current?.currentPage||ctx.sourcePage
    const cached=ctx.pageCache[pageNum]
    if(cached){setReadingHtml(renderPageReading(cached));setTimeout(bindPageReadActions,50)}
    else{
      setReadingHtml('')
      try{
        const r=await fetch(`${PDF_API}/source/${encodeURIComponent(taskIdRef.current!)}/page/${encodeURIComponent(pageNum)}?focus_table=${encodeURIComponent(String(ctx.selectedTableIndex||''))}`)
        const d=await r.json(); if(!r.ok) throw new Error(d.error||'加载失败')
        ctx.pageCache[pageNum]=d
        if(pdfCtx.current?.currentPage===pageNum&&ctx.readingMode==='page'){setReadingHtml(renderPageReading(d));setTimeout(bindPageReadActions,50)}
      }catch{setReadingHtml('')}
    }
  }

  function renderPageReading(pd:any): string {
    if(!pd) return ''
    const pT=pd.page_tables||[]
    const pTH=pT.length?pT.map((t:any)=>`<button class="pdf-chip trace-chip" data-ptidx="${t.table_index}">表 ${t.table_index}${(t.matched_financial_names||[]).length?' · '+t.matched_financial_names.join('、'):''}</button>`).join(''):'<span style="color:#64748b">这一页没有可定位的表格。</span>'
    const blks=(pd.blocks||[]).map((b:any)=>renderBlock(b)).join('')
    return `<div class="pdf-page-reading-view"><div class="pdf-page-reading-summary"><div><strong>PDF 第 ${pd.page_number||pdfCtx.current?.currentPage||1}</strong><span>${pd.block_count||0} 个解析块 / ${pd.table_count||0} 张表</span></div><div class="pdf-chip-row">${pTH}</div></div>${blks||'<div style="padding:20px;color:#64748b">没有可展示的解析内容。</div>'}</div>`
  }

  function renderBlock(b:any): string {
    const type=b?.type||'unknown'
    const bb=Array.isArray(b?.bbox)&&b.bbox.length===4?`bbox: ${b.bbox.join(', ')}`:''
    if(type==='table'){
      const label=b.table_index?`表 ${b.table_index}`:'表格块'
      const tags=[].concat(b.heading||[]).concat(b.matched_financial_names||[]).filter(Boolean).slice(0,3).map((t:string)=>`<span class="pdf-page-block-tag">${escHtml(t)}</span>`).join('')
      const act=b.table_index?`<button class="pdf-trace-btn" data-ptidx="${b.table_index}">打开该表</button>`:''
      return `<section class="pdf-page-block ${b.is_focus_table?'focus-table':''}"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(label)}</span><span class="pdf-page-block-meta">${escHtml(bb||'表格解析块')}</span></div>${act}</div><div class="pdf-page-block-tag-row">${tags}</div><div class="pdf-table-wrap pdf-page-table-wrap">${b.table_html?sanitizeTableHtml(b.table_html):'<div style="color:#64748b">表格区域，无可用 HTML。</div>'}</div></section>`
    }
    if(type==='list'){
      const items=(b.list_items||[]).map((i:string)=>`<li>${escHtml(i||'')}</li>`).join('')
      return `<section class="pdf-page-block"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">列表</span><span class="pdf-page-block-meta">${escHtml(bb||'列表解析块')}</span></div></div><ul class="pdf-page-block-list">${items}</ul></section>`
    }
    if(type==='image') return `<section class="pdf-page-block pdf-page-block-muted"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">图片</span><span class="pdf-page-block-meta">${escHtml(bb||'图片解析块')}</span></div></div><div class="pdf-page-block-text" style="color:#64748b">来源图像：${escHtml(b.image_path||'未提供路径')}</div></section>`
    const hLike=type==='header'||Number(b.text_level||0)>0
    const tLabel=type==='header'?'页眉':type==='page_number'?'页码':hLike?'标题':'文本'
    return `<section class="pdf-page-block ${type==='page_number'||type==='header'?'pdf-page-block-muted':''}"><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(tLabel)}</span><span class="pdf-page-block-meta">${escHtml(bb||'文本解析块')}</span></div></div><div class="pdf-page-block-text ${hLike?'pdf-page-block-heading':''}">${escHtml(b.text||' ')}</div></section>`
  }

  function bindPageReadActions() {
    document.querySelectorAll('[data-ptidx]').forEach(btn=>{
      btn.addEventListener('click',()=>{
        const idx=Number((btn as HTMLElement).dataset.ptidx||0)
        if(idx&&idx!==srcCtx.current?.selectedTableIndex) showTableSource(idx)
      })
    })
  }

  function bindEditableTable() {
    const wrap=editTableRef.current; if(!wrap) return
    wrap.querySelectorAll('th,td').forEach(cell=>{
      cell.addEventListener('click',()=>selectCell(cell as HTMLTableCellElement))
      cell.addEventListener('focus',()=>selectCell(cell as HTMLTableCellElement))
      cell.addEventListener('input',syncEditable)
      cell.addEventListener('blur',syncEditable)
    })
  }

  function selectCell(cell:HTMLTableCellElement) {
    if(!srcCtx.current) return
    const table=cell.closest('table');const row=cell.closest('tr');if(!table||!row) return
    const ri=Array.from(table.rows).indexOf(row);const ci=Array.from(row.cells).indexOf(cell)
    srcCtx.current.selectedCell={rowIndex:ri,cellIndex:ci,text:cell.textContent||''}
    document.querySelectorAll('.selected-cell').forEach(n=>n.classList.remove('selected-cell'))
    cell.classList.add('selected-cell')
    if(pdfCtx.current){
      pdfCtx.current.selectedTrace=traceCell(cell)
      if(pdfCtx.current.selectedTrace&&Number(pdfCtx.current.currentPage)!==Number(pdfCtx.current.selectedTrace.pageNumber))
        updatePdfViewer(pdfCtx.current.selectedTrace.pageNumber)
    }
  }

  function traceCell(cell:HTMLElement) {
    if(!pdfCtx.current) return null
    const direct=parseBbox(parseBboxFromAttr(cell))
    if(direct) return {pageNumber:pdfCtx.current.sourcePage,bbox:normalizeBbox(direct,pdfCtx.current.bboxExtent),source:'cell_bbox',confidence:'high'}
    const text=normalizeCellText(cell.textContent||''); if(!isUsefulTextAnchor(text)) return null
    const pd=srcCtx.current?.pageCache[pdfCtx.current.sourcePage]; if(!pd?.blocks) return null
    const matches:any[]=[]
    pd.blocks.forEach((b:any)=>{
      if(!b||b.type==='table') return
      const bt=normalizeCellText(Array.isArray(b.list_items)?b.list_items.join(''):b.text||'')
      if(bt===text||(text.length>=10&&bt.includes(text))){const bbox=normalizeBbox(parseBbox(b.bbox),pdfCtx.current!.bboxExtent);if(bbox) matches.push({bbox,block:b})}
    })
    if(matches.length===1) return {pageNumber:pdfCtx.current.sourcePage,bbox:matches[0].bbox,source:'text_anchor',confidence:'medium'}
    return null
  }

  function syncEditable() {
    const wrap=editTableRef.current;const text=corrTextRef.current;if(!wrap||!text) return
    const html=serializeEditableTable(wrap)
    if(html){text.value=html;if(srcCtx.current) srcCtx.current.correctionText=html;if(corrStatusRef.current?.value==='unreviewed') corrStatusRef.current.value='fixed'}
  }

  async function saveCorrection() {
    const tid=taskIdRef.current;const idx=srcCtx.current?.selectedTableIndex;if(!tid||!idx) return
    syncEditable()
    try{
      const r=await fetch(`${PDF_API}/source/${encodeURIComponent(tid)}/table/${encodeURIComponent(idx)}/correction`,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({review_status:corrStatusRef.current?.value,table_markdown:corrTextRef.current?.value,note:corrNoteRef.current?.value})
      });const d=await r.json();if(!r.ok) throw new Error(d.error||'保存失败');showToast('人工修正已保存')
    }catch(e){setError((e as Error).message)}
  }

  function handleFiles(files:FileList|File[]) {
    const inc=Array.from(files);if(!inc.length) return
    if(inc.length>5){setError('一次最多选择 5 个 PDF');return}
    for(const f of inc){if(!f.name.toLowerCase().endsWith('.pdf')){setError('仅支持 PDF 文件');return}if(f.size>100*1024*1024){setError('文件超过 100 MB: '+f.name);return}}
    setSelectedFiles(inc);setError(null)
  }

  const sbc=(s:string)=>{const m:Record<string,string>={queued:'queued',uploaded:'uploaded',submitting:'submitting',submitted:'submitted',pending:'pending',processing:'processing',completed:'completed',completed_missing_artifact:'failed',failed:'failed',error:'error',cancelled:'cancelled',success:'completed',done:'completed',finished:'completed'};return m[s]||'pending'}

  return (
    <div className="secondary-page">
      <style>{CSS}</style>

      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <FileText className="h-3.5 w-3.5" />
              Report Parsing
            </div>
            <h1 className="secondary-title">智能解析</h1>
            <p className="secondary-description">上传财报 PDF，生成 Markdown、表格、财务数据抽取和可视化溯源结果。</p>
          </div>
          <div className="secondary-step-row">
            <span className="secondary-step-chip is-active">解析</span>
            <span className="secondary-step-chip">抽取</span>
            <span className="secondary-step-chip">校验</span>
          </div>
        </div>
      </section>

      {/* Health */}
      <div className="flex flex-wrap items-center gap-3">
        <div className={`secondary-status ${health?.mineru?'secondary-status-success':health?'secondary-status-error':''}`}>MinerU</div>
        <div className={`secondary-status ${health?.vlm?'secondary-status-success':health?'secondary-status-error':''}`}>VLM</div>
      </div>
      {health?.warning&&<div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-sm text-warning">{health.warning}</div>}
      {health&&!health.submit_ready&&<div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-sm text-warning">服务暂未就绪，无法提交新任务</div>}

      {/* Upload */}
      <div className="secondary-panel p-5">
        <div className="pdf-source-choice">
          <div className="pdf-source-choice-head">
            <div>
              <h3 className="flex items-center gap-2"><FolderOpen className="h-5 w-5 text-primary"/>已下载财报</h3>
              <p>优先从搜索下载阶段保存的 PDF 中选择，也可以直接发起解析。</p>
            </div>
            <div className="pdf-download-search">
              <label>
                <Search className="h-4 w-4" />
                <input
                  value={downloadedQuery}
                  onChange={e=>setDownloadedQuery(e.target.value)}
                  onKeyDown={e=>{if(e.key==='Enter') loadDownloadedReports(downloadedQuery)}}
                  placeholder="搜索公司、类型或文件名"
                />
              </label>
              <button className="pdf-icon-btn" onClick={()=>loadDownloadedReports(downloadedQuery)} disabled={downloadedLoading} aria-label="刷新已下载财报">
                {downloadedLoading?<Loader2 className="h-4 w-4 animate-spin"/>:<RefreshCw className="h-4 w-4"/>}
              </button>
            </div>
          </div>
          {downloadedReports.length>0?<div className="pdf-download-list">
            {downloadedReports.slice(0,10).map(report=>{
              const busy = downloadedBusyPath === report.relativePath
              return (
                <div key={report.id} className="pdf-download-item">
                  <div className="pdf-download-main">
                    <FileText className="h-5 w-5" />
                    <div className="min-w-0">
                      <div className="pdf-download-title">{report.filename}</div>
                      <div className="pdf-download-meta">
                        <span>{report.company || '未知公司'}</span>
                        <span>{report.category || '未分类'}</span>
                        <span>{formatSize(report.size)}</span>
                        <span>{formatDateTime(report.mtime)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="pdf-download-actions">
                    <button className="pdf-small-action" onClick={()=>selectDownloadedReport(report)} disabled={busy||uploading}>
                      {busy?<Loader2 className="h-4 w-4 animate-spin"/>:<CheckCircle2 className="h-4 w-4"/>}选择
                    </button>
                    <button className="pdf-small-action primary" onClick={()=>parseDownloadedReport(report)} disabled={busy||uploading||(health?!health.submit_ready:false)}>
                      {busy?<Loader2 className="h-4 w-4 animate-spin"/>:<FileText className="h-4 w-4"/>}解析
                    </button>
                  </div>
                </div>
              )
            })}
          </div>:<div className="rounded-[18px] border border-dashed border-border bg-bg/50 px-4 py-6 text-center text-sm text-text-muted">
            {downloadedLoading?'正在读取已下载财报...':'暂无已下载财报，可继续使用本地上传。'}
          </div>}
        </div>
        <div className="pdf-source-separator">或上传本地 PDF</div>
        <div className={`pdf-drop-zone ${dragover?'dragover':''}`} onClick={()=>fileInput.current?.click()}
          onDragOver={e=>{e.preventDefault();setDragover(true)}} onDragLeave={()=>setDragover(false)}
          onDrop={e=>{e.preventDefault();setDragover(false);if(e.dataTransfer.files.length) handleFiles(e.dataTransfer.files)}}>
          <UploadCloud className="mx-auto mb-3 h-10 w-10 text-slate-500" />
          <p><strong>点击选择 PDF</strong> 或拖拽文件到此处</p>
          <p style={{color:'#64748b',marginTop:4}}>一次最多 5 个 PDF，单个最大 100 MB</p>
          {selectedFiles.length===1&&<div style={{marginTop:12,fontWeight:600}}>{selectedFiles[0].name} ({formatSize(selectedFiles[0].size)})</div>}
          {selectedFiles.length>1&&<div style={{marginTop:12,fontWeight:600}}>已选择 {selectedFiles.length} 个 PDF</div>}
        </div>
        <input ref={fileInput} type="file" accept=".pdf" multiple className="hidden" onChange={e=>{if(e.target.files) handleFiles(e.target.files);e.target.value=''}}/>
        {selectedFiles.length>0&&<div style={{marginTop:14}}><div style={{fontSize:'.84rem',color:'#64748b',marginBottom:8,fontWeight:600}}>本次入队文件</div>
          <div style={{display:'grid',gap:8}}>{selectedFiles.map((f,i)=>(
            <div key={i} style={{display:'flex',justifyContent:'space-between',gap:10,alignItems:'center',border:'1px solid #e2e8f0',borderRadius:12,background:'#fff',padding:'8px 10px',fontSize:'.88rem'}}>
              <b style={{fontWeight:600,wordBreak:'break-word'}}>{escHtml(f.name)}</b><span style={{color:'#64748b',whiteSpace:'nowrap',fontSize:'.8rem'}}>{formatSize(f.size)}</span></div>))}</div></div>}
        <div className="mt-4 flex flex-wrap gap-2.5">
          <button onClick={startConvert} disabled={uploading||selectedFiles.length===0||(health?!health.submit_ready:false)}
            className="flex h-11 items-center gap-2 rounded-xl accent-gradient px-5 text-sm font-semibold text-white shadow-md shadow-blue-900/12 hover:brightness-110 disabled:opacity-60 disabled:cursor-not-allowed">
            {uploading&&<span className="inline-block h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin"/>}批量入队</button>
          {selectedFiles.length>0&&<button onClick={()=>{setSelectedFiles([]);setError(null)}} className="rounded-xl border border-border bg-card px-4 py-3 text-sm font-semibold text-text shadow-sm hover:bg-bg">清除</button>}
          {taskIdRef.current&&!isTerminal(parseBadge.cls)&&<button onClick={cancelTask} className="rounded-lg border border-error/20 bg-error/5 px-4 py-2.5 text-sm font-semibold text-error hover:bg-error/10">停止查看</button>}
        </div>
        {error&&<div className="mt-4 rounded-lg border border-error/20 bg-error/5 px-4 py-3 text-sm text-error">{error}</div>}
      </div>

      {/* Config */}
      <div className="overflow-hidden rounded-[18px] border border-border bg-card shadow-sm">
        <button onClick={()=>setShowConfig(!showConfig)} className="flex w-full items-center justify-between px-6 py-4">
          <span className="flex items-center gap-2 text-sm font-semibold text-text"><Settings2 className="h-4 w-4 text-primary" />高级配置</span>
          <span className="text-sm font-semibold text-primary">{showConfig?'收起 ▴':'展开 ▴'}</span></button>
        {showConfig&&<div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4 border-t border-border px-6 py-4">
          <div className="flex flex-col gap-1.5"><label className="text-sm font-semibold text-text-muted">后端模式</label>
            <select value={backend} onChange={e=>setBackend(e.target.value)} className="form-control px-4 text-base">
              <option value="hybrid-http-client">hybrid-http-client (推荐)</option><option value="pipeline">pipeline (快速)</option><option value="vlm-http-client">vlm-http-client (高精度)</option></select></div>
          <div className="flex flex-col gap-1.5"><label className="text-sm font-semibold text-text-muted">解析方式</label>
            <select value={parseMethod} onChange={e=>setParseMethod(e.target.value)} className="form-control px-4 text-base">
              <option value="auto">auto (自动判断)</option><option value="txt">txt (文本提取)</option><option value="ocr">ocr (OCR 识别)</option></select></div>
          <div className="flex flex-col gap-1.5"><label className="text-sm font-semibold text-text-muted">起始页码 (0-based)</label>
            <input type="number" min="0" value={startPage} onChange={e=>setStartPage(e.target.value)} placeholder="0" className="form-control px-4 text-base"/></div>
          <div className="flex flex-col gap-1.5"><label className="text-sm font-semibold text-text-muted">结束页码 (0-based)</label>
            <input type="number" min="0" value={endPage} onChange={e=>setEndPage(e.target.value)} placeholder="可选" className="form-control px-4 text-base"/></div>
          <div className="flex items-center gap-3 pt-5"><label className="relative inline-flex h-6 w-11 cursor-pointer items-center"><input type="checkbox" checked={formula} onChange={e=>setFormula(e.target.checked)} className="peer sr-only"/><div className="h-6 w-11 rounded-full bg-gray-200 peer-checked:bg-primary after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-5"/></label><span className="text-sm font-medium">启用公式识别</span></div>
          <div className="flex items-center gap-3 pt-5"><label className="relative inline-flex h-6 w-11 cursor-pointer items-center"><input type="checkbox" checked={table} onChange={e=>setTable(e.target.checked)} className="peer sr-only"/><div className="h-6 w-11 rounded-full bg-gray-200 peer-checked:bg-primary after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-5"/></label><span className="text-sm font-medium">启用表格识别</span></div>
        </div>}
      </div>

      {/* Upload Stage */}
      {uploadActive&&<div className="pdf-stage">
        <div className="flex items-center justify-between mb-2.5"><div className="font-semibold text-[.95rem] flex items-center gap-2"><UploadCloud className="h-4 w-4 text-primary" />文件上传</div><span className={`pdf-status-badge ${uploadBadge.cls}`}>{uploadBadge.text}</span></div>
        <div className="pdf-pbar-wrap"><div className="pdf-pbar" style={{width:`${uploadPct}%`}}/></div>
        <div className="flex justify-between mt-2 text-[.85rem] text-text-muted"><span>{uploadStatusText}</span><span>{Math.round(uploadPct)}%</span></div>
      </div>}

      {/* Parse Stage */}
      {parseActive&&<div className="pdf-stage">
        <div className="flex items-center justify-between mb-2.5"><div className="font-semibold text-[.95rem] flex items-center gap-2"><FileText className="h-4 w-4 text-primary" />财报解析</div><span className={`pdf-status-badge ${parseBadge.cls}`}>{parseBadge.text}</span></div>
        <div className="pdf-pbar-wrap"><div className={`pdf-pbar ${parseBadge.cls==='completed'?'done':''}`} style={{width:`${parsePct}%`}}/></div>
        <div className="flex justify-between mt-2 text-[.85rem] text-text-muted"><span>{parseStatusText}</span><span>{Math.round(parsePct)}%</span></div>
        <div className="flex flex-wrap gap-4 mt-2 text-[.85rem] text-text-muted">
          {queueInfo&&<span>{queueInfo}</span>}{elapsedInfo&&<span>{elapsedInfo}</span>}{pagesInfo&&<span style={{fontWeight:600,color:'#2563eb'}}>{pagesInfo}</span>}{stageInfo&&<span>{stageInfo}</span>}</div>
      </div>}

      {/* Logs */}
      {logs.length>0&&<div className="apple-card rounded-[24px] p-6"><h3 className="text-base font-semibold text-text mb-3">处理日志</h3>
        <div className="pdf-log" ref={logRef}>{logs.map((l,i)=><div key={i} className="flex gap-2.5 py-0.5 border-b border-gray-100 last:border-0">
          <span className="text-text-muted shrink-0">{new Date(l.time).toLocaleTimeString('zh-CN',{hour12:false})}</span>
          <span className={l.level==='error'?'text-error':l.level==='success'?'text-success':l.level==='warn'?'text-warning':'text-text'}>{l.message}</span></div>)}</div></div>}

      {/* Data Pipeline */}
      {taskIdRef.current&&parseBadge.cls==='completed'&&<div className="apple-card rounded-[24px] p-6">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h3 className="text-base font-semibold text-text">数据管道</h3>
            <p className="mt-1 text-sm text-text-muted">PostgreSQL 与 results 目录保存全量解析信息；Wiki 只保留报告入口、公司级知识资产和轻量产物清单。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={runRemainingWorkflow} disabled={workflowBusy!=='' || !backendArtifactSummary?.ready} className="inline-flex items-center gap-2 rounded-xl accent-gradient px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
              {workflowBusy==='run-remaining'?<Loader2 className="h-4 w-4 animate-spin"/>:<PlayCircle className="h-4 w-4"/>}运行剩余步骤
            </button>
            <button onClick={loadWorkflowStatus} disabled={workflowLoading} className="rounded-xl border border-border bg-card px-4 py-2 text-sm font-semibold text-text hover:bg-bg disabled:opacity-60">
              {workflowLoading?'刷新中':'刷新状态'}
            </button>
          </div>
        </div>
        <div className="pdf-pipeline-note mb-4">
          <Database className="h-4 w-4" />
          <div>Wiki 不复制全量解析包；`artifact_manifest.json` 只记录核心文件路径、hash 和版本，用于判断是否过期。全量结构化内容由 PostgreSQL 入库和原始解析目录承担。</div>
        </div>
        {workflowStatus?.semantic?.llm&&<div className="pdf-pipeline-note mb-4">
          <Brain className="h-4 w-4" />
          <div>模型语义增强默认调用本地 Qwen3.6，输出到 `semantic/llm/{workflowStatus.semantic.reportId || 'report'}/`，不覆盖规则层事实和证据。</div>
        </div>}
        {workflowStatus?.error?<div className="rounded-xl border border-error/20 bg-error/5 px-4 py-3 text-sm text-error">{workflowStatus.error}</div>:<div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[
            ['解析产物包', workflowStatus?.artifactBundle?.status, backendArtifactSummary?.message || (workflowStatus?.documentFull?.status==='ready'?`${artifactReadyCount}/${artifactTotal} 个核心文件已生成`:'等待 document_full.json')],
            ['Wiki 入库', workflowStatus?.wiki?.status, workflowStatus?.wiki?.status==='ready'?workflowStatus?.wiki?.companyDir:(workflowStatus?.wiki?.message||'未导入 Wiki')],
            ['语义层', workflowStatus?.semantic?.status, workflowReady(workflowStatus,'semantic')?`规则事实 ${workflowStatus?.semantic?.counts?.facts||0} / 证据 ${workflowStatus?.semantic?.counts?.evidence||0}；${llmSemanticDesc}`:(workflowStatus?.semantic?.message||llmSemanticDesc||'未生成或不完整')],
            ['PostgreSQL', workflowStatus?.database?.status, workflowReady(workflowStatus,'database')?`指标 ${workflowStatus?.database?.statementItems||0} / 表格 ${workflowStatus?.database?.tables||0}`:(workflowStatus?.database?.message||'未入库')],
          ].map(([label,status,desc]:any)=><div key={label} className="rounded-[16px] border border-border bg-bg/50 p-4">
            <div className="flex items-center justify-between gap-3"><span className="text-sm font-semibold text-text">{label}</span><span className={`secondary-status ${workflowStateClass(status)}`}>{workflowStateLabel(status)}</span></div>
            <p className="mt-2 break-all text-sm leading-6 text-text-muted">{desc}</p>
          </div>)}
        </div>}
        {workflowJob&&<div className="mt-4 rounded-[16px] border border-border bg-bg/50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold text-text">流水线任务</div>
            <span className={`secondary-status ${workflowJob.status==='succeeded'?'secondary-status-success':workflowJob.status==='failed'?'secondary-status-warning':'secondary-status-info'}`}>{workflowJob.status}</span>
          </div>
          <div className="pdf-preflight-list">
            {(workflowJob.steps||[]).map((step:any)=><div key={step.step} className={`pdf-preflight-item ${step.status==='failed'?'error':step.status==='skipped'?'warn':''}`}>
              <span className="pdf-preflight-dot" />
              <div><div className="pdf-preflight-title">{step.step} · {step.status}</div><div className="pdf-preflight-message">{step.message||step.error||''}</div></div>
            </div>)}
          </div>
          {workflowJob.error&&<div className="mt-3 text-sm text-error">{workflowJob.error}</div>}
        </div>}
        {workflowError&&<div className="mt-4 rounded-xl border border-error/20 bg-error/5 px-4 py-3 text-sm leading-6 text-error">
          {workflowError}
        </div>}
        <div className="mt-4 rounded-[16px] border border-border bg-bg/50 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-text">核心解析产物清单</div>
              <div className="mt-1 text-xs leading-5 text-text-muted">这些文件共同支撑入库、质量校验和证据溯源；Wiki 仅引用清单，不重复保存全量包。</div>
            </div>
            <span className="secondary-status secondary-status-info">{artifactReadyCount}/{artifactTotal}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {WIKI_INPUT_ARTIFACTS.map(name => {
              const ok = backendArtifactSummary?.artifacts?.[name]?.exists ?? !!artifacts?.[name]?.exists
              return <span key={name} className={`secondary-status ${ok?'secondary-status-success':''}`}>{name}</span>
            })}
          </div>
          {artifactMissing.length>0&&<div className="mt-3 text-xs leading-5 text-text-muted">未生成：{artifactMissing.join('、')}</div>}
          {workflowStatus?.preflight?.checks?.length>0&&<div className="pdf-preflight-list">
            {workflowStatus.preflight.checks.map((check:any)=><div key={check.id} className={`pdf-preflight-item ${check.blocking?'error':check.ok?'':'warn'}`}>
              <span className="pdf-preflight-dot" />
              <div><div className="pdf-preflight-title">{check.label} · {check.status}</div><div className="pdf-preflight-message">{check.message}</div></div>
            </div>)}
          </div>}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={()=>runWorkflowStep('wiki-import')} disabled={workflowBusy!=='' || !backendArtifactSummary?.ready} className="rounded-xl accent-gradient px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
            {workflowBusy==='wiki-import'?'导入中...':'导入 Wiki'}
          </button>
          <button onClick={()=>runWorkflowStep('semantic')} disabled={workflowBusy!=='' || !['ready','stale'].includes(workflowStatus?.wiki?.status)} className="rounded-xl accent-gradient px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
            {workflowBusy==='semantic'?'生成中...':'生成 Wiki 语义层'}
          </button>
          <button onClick={()=>runWorkflowStep('db-import')} disabled={workflowBusy!=='' || !backendArtifactSummary?.ready} className="rounded-xl accent-gradient px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50">
            {workflowBusy==='db-import'?'导入中...':'导入 PostgreSQL'}
          </button>
        </div>
      </div>}

      {/* Markdown Preview */}
      {markdown&&<div className="apple-card rounded-[24px] p-6">
        <div className="flex items-center justify-between mb-3"><h3 className="text-base font-semibold text-text">Markdown 预览</h3>
          <div className="flex gap-2">
            <button onClick={async()=>showToast(await copyText(markdown)?'已复制到剪贴板!':'复制失败，请手动选中文本复制')} className="rounded-xl border border-border bg-card px-3 py-2 text-sm font-semibold text-text hover:bg-bg">复制</button>
            {taskIdRef.current&&<>
              <a href={`${PDF_API}/download/${encodeURIComponent(taskIdRef.current)}`} className="rounded-xl border border-border bg-card px-3 py-2 text-sm font-semibold text-text hover:bg-bg">下载原始 MD</a>
              <a href={`${PDF_API}/download_complete/${encodeURIComponent(taskIdRef.current)}`} className="rounded-xl border border-border bg-card px-3 py-2 text-sm font-semibold text-text hover:bg-bg">下载完整增强版 MD</a>
              <a href={`${PDF_API}/download_corrected/${encodeURIComponent(taskIdRef.current)}`} className="rounded-xl accent-gradient px-3 py-2 text-sm font-semibold text-white hover:brightness-110">下载修正版 MD</a>
            </>}
          </div></div>
        <div className="pdf-md-preview" ref={mdRef}>{mdLines.map((line,idx)=>{const n=idx+1;return <div key={n} data-line={n} className={`pdf-md-line ${focusedLine===n?'focus':''}`}>
          <span className="pdf-md-line-no">{n}</span><span className="pdf-md-line-text">{escHtml(line||' ')}</span></div>})}</div>
      </div>}

      {/* Artifacts */}
      {artifacts&&Object.keys(artifacts).length>0&&<div className="apple-card rounded-[24px] p-6"><h3 className="text-base font-semibold text-text mb-3">产物文件</h3>
        <div className="text-sm text-text-muted mb-3">以下为本次解析生成的产物包，核心文件会共同进入 Wiki/语义抽取与 PostgreSQL 入库流程。</div>
        {Object.entries(artifacts).map(([name,info]:[string,any])=>{
          const url = artifactUrl(info)
          const downloadUrl = artifactDownloadUrl(name, info)
          return <div key={name} className={`pdf-artifact-row ${info.exists?'ok':'missing'}`}>
            <div className="pdf-artifact-name">
              <span>{name}</span>
              <small>{artifactRoles[name] || '解析辅助产物'}</small>
            </div>
            <code>{info.path||'未生成'}</code>
            <div className="pdf-artifact-actions">
              {info.exists&&url?<a className="pdf-trace-btn inline-flex items-center gap-1" href={url} target="_blank" rel="noopener" title="打开产物">
                <ExternalLink size={13}/>打开
              </a>:null}
              {info.exists&&downloadUrl?<a className="pdf-trace-btn inline-flex items-center gap-1" href={downloadUrl} download={artifactDownloadName(name)} title={name === 'images' ? '打包下载图片' : '下载产物'}>
                <Download size={13}/>下载
              </a>:null}
            </div>
          </div>
        })}</div>}

      {/* Quality */}
      {quality&&<div className="apple-card rounded-[24px] p-6"><h3 className="text-base font-semibold text-text mb-3">解析质量报告</h3>
        <div className="pdf-quality-grid">
          <div><strong>{quality.table_count||0}</strong><span>表格</span></div>
          <div><strong>{quality.single_row_table_count||0}</strong><span>单行/空壳表</span></div>
          <div><strong>{Math.round((quality.single_row_table_ratio||0)*1000)/10}%</strong><span>空壳比例</span></div>
          <div><strong>{quality.image_ref_count||0}</strong><span>图片引用</span></div>
          <div><strong>{(quality.suspicious_tables||[]).length}</strong><span>可疑表样本</span></div>
        </div>
        <div className="pdf-quality-row"><span>核心章节</span><b>{(quality.found_sections||[]).length}/{((quality.found_sections||[]).length+(quality.missing_sections||[]).length)}</b></div>
        {(()=>{const core=quality.core_financial_table_candidates||[];const key=quality.key_table_candidates||{};const ind=(quality.indicator_table_candidates||[]).filter((i:any)=>i.status==='found');const cFound=core.filter((i:any)=>i.status==='found');const susp=quality.suspicious_tables||[];return<>
          <div className="pdf-quality-row"><span>财报核心表</span><b>{core.length?`${cFound.length}/${core.length} · ${cFound.map((i:any)=>i.name).join('、')||'未识别'}`:(quality.found_financial_tables||[]).join('、')||'未识别'}</b></div>
          <div className="pdf-quality-section"><div className="pdf-quality-section-title">关键表候选</div><div className="pdf-chip-row">
            {core.length?core.map((c:any,i:number)=>c.table_index&&c.status!=='missing'?<button key={i} className="pdf-chip trace-chip" onClick={()=>showTableSource(c.table_index,c.line)}>{c.name} · {candidateMeta(c)}</button>:<span key={i} className="pdf-chip pdf-chip-missing">{c.name||'候选表'} · {candidateMeta(c)}</span>)
            :Object.keys(key).length?Object.keys(key).slice(0,8).map(name=>{const first=key[name]?.[0];return first?.table_index?<button key={name} className="pdf-chip trace-chip" onClick={()=>showTableSource(first.table_index,first.line)}>{name} · {candidateMeta(first)}</button>:<span key={name} className="pdf-chip">{name}</span>})
            :<span className="text-text-muted text-sm">未定位到候选表</span>}</div></div>
          <div className="pdf-quality-section"><div className="pdf-quality-section-title">指标/经营分析候选</div><div className="pdf-chip-row">
            {ind.length?ind.map((c:any,i:number)=><button key={i} className="pdf-chip trace-chip pdf-chip-secondary" onClick={()=>showTableSource(c.table_index,c.line)}>{c.name||'候选表'} · {candidateMeta(c)}</button>):<span className="text-text-muted text-sm">未定位到指标/经营分析候选表</span>}</div></div>
          <div className="pdf-quality-section"><div className="pdf-quality-section-title">优先复核表</div><ul className="list-disc pl-5 text-sm text-text">
            {susp.length?susp.map((s:any,i:number)=><li key={i}>{s.table_index?<button className="pdf-trace-btn" onClick={()=>showTableSource(s.table_index,s.line)}>{suspectTableMeta(s)}</button>:<span className="text-text-muted">{suspectTableMeta(s)}</span>}{' · '}{suspectReasons(s.suspect_reasons||[])}</li>):<li>未发现可疑表样本</li>}</ul></div>
        </>})()}
        {quality.warnings?.length?<ul className="list-disc pl-5 mt-2.5 text-sm text-warning">{quality.warnings.map((w:string,i:number)=><li key={i}>{w}</li>)}</ul>:<ul className="list-disc pl-5 mt-2.5 text-sm text-warning"><li>未发现明显质量告警</li></ul>}
      </div>}

      {/* Financial */}
      {financial&&(()=>{const checks=financial.financial_checks||{};const fData=financial.financial_data||{};const summary=checks.summary||{};const dSummary=fData.summary||{};const status=checks.overall_status||'skipped';const failures=(checks.checks||[]).filter((c:any)=>c.status==='fail').slice(0,8);const warnings=(checks.warnings||[]).slice(0,8);const stmtCount=dSummary.statement_count||(fData.statements||[]).length||0;const keyMetricCount=dSummary.key_metric_count||(fData.key_metrics||[]).length||0;const scopes=(dSummary.scopes||[]).map(scopeName).join('、')||'--';const statusText=status==='pass'?'通过':status==='fail'?'存在异常':status==='error'?'生成失败':'未生成';return <div className="apple-card rounded-[24px] p-6"><h3 className="text-base font-semibold text-text mb-3">财务勾稽校验</h3>
        <div className="pdf-quality-grid"><div><strong>{statusText}</strong><span>整体状态</span></div><div><strong>{summary.pass||0}</strong><span>通过</span></div><div><strong>{summary.fail||0}</strong><span>失败</span></div><div><strong>{summary.skipped||0}</strong><span>跳过</span></div><div><strong>{stmtCount}</strong><span>结构化报表</span></div></div>
        <div className="pdf-quality-row"><span>识别范围</span><b>{scopes}</b></div><div className="pdf-quality-row"><span>关键指标</span><b>{keyMetricCount}</b></div><div className="pdf-quality-row"><span>报告年份</span><b>{fData.report_year||'--'}</b></div>
        <div className="pdf-quality-section"><div className="pdf-quality-section-title">失败项</div><ul className="list-disc pl-5 text-sm text-text">
          {failures.length?failures.map((f:any,i:number)=><li key={i}><b>{f.rule_name||f.rule_id||'校验失败'}</b> · {scopeName(f.scope)} · {f.period||'--'}{f.diff!==undefined?` · 差异 ${formatFinancialNumber(f.diff)}`:''}{f.tolerance!==undefined?` / 容差 ${formatFinancialNumber(f.tolerance)}`:''}</li>):<li>未发现失败项</li>}</ul></div>
        <div className="pdf-quality-section"><div className="pdf-quality-section-title">提示</div><ul className="list-disc pl-5 text-sm text-warning">{warnings.length?warnings.map((w:string,i:number)=><li key={i}>{w}</li>):<li>无额外提示</li>}</ul></div>
        <div className="pdf-quality-section"><div className="pdf-chip-row">{taskIdRef.current&&<a className="pdf-trace-btn" href={`${PDF_API}/financial/${encodeURIComponent(taskIdRef.current)}`} target="_blank" rel="noopener">打开 JSON</a>}</div></div>
      </div>})()}

      {/* Source Traceability */}
      {sourceVisible&&srcTable&&srcMeta&&(()=>{const tbl=srcTable;const corr=srcMeta.correction||{};const excerpt=srcMeta.excerpt||[];const sArt=srcMeta.artifacts||{};const img=srcMeta.pdfPageImage;const statusOpts=[['unreviewed','未复核'],['correct','确认无误'],['needs_fix','需要修正'],['fixed','已修正'],['ignored','忽略']];return <div className="apple-card rounded-[24px] p-6"><h3 className="text-base font-semibold text-text mb-3">可视化溯源</h3>
        <div className="pdf-source-summary">
          <div><strong>表 {tbl.table_index}</strong><span>Markdown 行 {tbl.line||'-'}</span></div>
          <div><strong>{tbl.rows||0}</strong><span>行</span></div>
          <div><strong>{tbl.pdf_page_number||'--'}</strong><span>PDF 页码{tbl.pdf_page_source==='markdown_marker_inferred'?'（推断）':''}</span></div>
          <div><strong>{tbl.cells||0}</strong><span>单元格</span></div>
          <div><strong>{Math.round((tbl.empty_ratio||0)*1000)/10}%</strong><span>空单元格</span></div>
          <div><strong>{Math.round((tbl.numeric_ratio||0)*1000)/10}%</strong><span>数字密度</span></div></div>
        <div className="pdf-source-meta"><span>附近标题</span><b>{tbl.heading||'未识别'}</b></div>
        <div className="pdf-source-meta"><span>单位</span><b>{tbl.unit||'未识别'}</b></div>
        <div className="pdf-source-meta"><span>命中类别</span><b>{(tbl.matched_financial_names||[]).join('、')||'普通表'}</b></div>
        <div className="pdf-source-meta"><span>PDF 坐标 bbox</span><b>{(tbl.bbox||[]).join(', ')||'未识别'}</b></div>
        <div className="pdf-source-meta"><span>页面截图</span><b>{tbl.source_image_path||'未识别'}</b></div>

        <div className="pdf-workbench">
          <div className="pdf-source-block pdf-source-pane"><div className="pdf-source-pane-head">
            <div className="pdf-reading-topline"><h4>阅读视图</h4><div className="pdf-reading-mode-switch">
              <button className={`pdf-reading-mode-btn ${readingMode==='page'?'active':''}`} onClick={()=>{if(srcCtx.current){srcCtx.current.readingMode='page';setReadingMode('page');renderReadingPane()}}}>当前PDF页</button>
              <button className={`pdf-reading-mode-btn ${readingMode==='table'?'active':''}`} onClick={()=>{if(srcCtx.current){srcCtx.current.readingMode='table';setReadingMode('table');renderReadingPane()}}}>当前表格</button></div></div>
            <div className="text-[.86rem] text-text-muted" style={{minHeight:20}}>{readingMode==='table'?'当前表格模式：便于直接编辑并同步到下方修正文本。':`当前PDF页模式：阅读视图随 PDF 翻页同步显示第 ${pdfCurPage} 页解析内容。`}</div></div>
            {readingMode==='table'?<div className="pdf-table-wrap pdf-editable" ref={editTableRef} dangerouslySetInnerHTML={{__html:makeEditableHtml(srcCtx.current?.correctionText||srcCtx.current?.tableHtml||'')}}/>:<div className="pdf-reading-body" dangerouslySetInnerHTML={{__html:readingHtml}}/>}
          </div>
          <div className="pdf-source-block pdf-source-pane"><div className="pdf-source-pane-head"><h4>PDF 原页</h4><div className="text-[.86rem] text-text-muted" style={{minHeight:20}}>支持上下翻页与缩放，定位框仅显示在来源页。</div></div>
            {img?.url?<div className="pdf-page-viewer" data-zoom={pdfZoom}>
              <div className="pdf-page-toolbar"><div className="pdf-page-topline"><span>PDF 第 {pdfCurPage} / {pdfCtx.current?.pageCount||pdfCurPage} 页</span>
                <div className="pdf-page-nav"><button className="pdf-nav-btn" disabled={pdfCurPage<=1} onClick={()=>updatePdfViewer(pdfCurPage-1)}>上一页</button>
                  <input className="pdf-page-input" type="number" min={1} max={pdfCtx.current?.pageCount||1} value={pdfCurPage} onChange={e=>updatePdfViewer(Number(e.target.value))} onKeyDown={e=>{if(e.key==='Enter') updatePdfViewer(Number((e.target as HTMLInputElement).value))}}/>
                  <button className="pdf-nav-btn" disabled={pdfCurPage>=(pdfCtx.current?.pageCount||1)} onClick={()=>updatePdfViewer(pdfCurPage+1)}>下一页</button></div></div>
                <div className="pdf-zoom-controls">{(['fit','1','1.5','2'] as const).map(z=><button key={z} className={`pdf-zoom-btn ${pdfZoom===z?'active':''}`} onClick={()=>setPdfZoom(z)}>{z==='fit'?'适应宽度':z==='1'?'100%':z==='1.5'?'150%':'200%'}</button>)}</div>
                <a href={getPdfUrl(pdfCurPage)} target="_blank" rel="noopener" className="text-primary font-semibold text-sm no-underline">打开原页图片</a></div>
              <div className="pdf-page-canvas"><div className="pdf-page-stage">
                <img src={getPdfUrl(pdfCurPage)} alt="PDF page"/>
                {(()=>{const ctx=pdfCtx.current;if(!ctx?.bboxExtent?.width) return null;const ext=ctx.bboxExtent;const ov:React.ReactElement[]=[]
                  if(pdfCurPage===ctx.sourcePage&&ctx.bbox?.length===4){const b=ctx.bbox;const l=Math.max(0,Math.min(100,(b[0]/ext.width)*100));const t=Math.max(0,Math.min(100,(b[1]/ext.height)*100));const r=Math.max(0,Math.min(100,(b[2]/ext.width)*100));const bt=Math.max(0,Math.min(100,(b[3]/ext.height)*100));ov.push(<div key="tbl" className="pdf-bbox" title="表格区域" style={{left:`${l}%`,top:`${t}%`,width:`${Math.max(0,r-l)}%`,height:`${Math.max(0,bt-t)}%`}}/>)}
                  const trace=ctx.selectedTrace;if(trace&&pdfCurPage===trace.pageNumber&&trace.bbox){const b=trace.bbox;const l=Math.max(0,Math.min(100,(b[0]/ext.width)*100));const t=Math.max(0,Math.min(100,(b[1]/ext.height)*100));const r=Math.max(0,Math.min(100,(b[2]/ext.width)*100));const bt=Math.max(0,Math.min(100,(b[3]/ext.height)*100));ov.push(<div key="tr" className={`pdf-bbox ${trace.source==='text_anchor'?'pdf-bbox-text':'pdf-bbox-selected'}`} title={trace.source==='cell_bbox'?'单元格区域':'文本锚定区域'} style={{left:`${l}%`,top:`${t}%`,width:`${Math.max(0,r-l)}%`,height:`${Math.max(0,bt-t)}%`}}/>)}
                  return ov})()}</div></div></div>:<div className="text-text-muted">未识别 PDF 页码，无法展示原页。</div>}
          </div>
        </div>

        <div className="pdf-source-block"><h4>人工复核修正</h4>
          <div className="pdf-correction-toolbar"><label>状态<select ref={corrStatusRef} defaultValue={corr.review_status||'unreviewed'}>{statusOpts.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
            <button className="pdf-trace-btn" onClick={saveCorrection}>保存修正</button><span className="text-text-muted text-sm">{corr.updated_at?`上次保存: ${corr.updated_at}`:''}</span></div>
          <textarea ref={corrTextRef} className="pdf-correction-editor" spellCheck={false} defaultValue={corr.table_markdown||srcMeta.table?.table_html||''} onChange={e=>{if(srcCtx.current) srcCtx.current.correctionText=e.target.value}}/>
          <textarea ref={corrNoteRef} className="pdf-correction-note" placeholder="复核备注，例如：第 3 列金额错位，应以 PDF 第 67 页为准。" defaultValue={corr.note||''}/></div>

        {excerpt.length>0&&<div className="pdf-source-block"><h4>Markdown 上下文</h4>{excerpt.map((item:any,i:number)=><div key={i} className={`pdf-source-line ${item.focus?'focus':''}`}><span>{item.line}</span><code>{item.text||' '}</code></div>)}</div>}
        {Object.keys(sArt).length>0&&<div className="pdf-source-block"><h4>产物文件</h4>{Object.entries(sArt).map(([name,info]:[string,any])=><div key={name} className={`pdf-artifact-row ${info.exists?'ok':'missing'}`}><span>{name}</span><code>{info.path||'未生成'}</code>{info.exists&&info.url?<a className="pdf-trace-btn" href={info.url} target="_blank" rel="noopener">打开</a>:null}</div>)}</div>}
      </div>})()}

      {/* Tasks */}
      {tasks.length>0&&<div className="apple-card rounded-[24px] p-6"><div className="flex items-center justify-between mb-4"><h3 className="text-base font-semibold text-text">最近任务</h3><button onClick={()=>loadTasks()} className="text-sm font-semibold text-text-muted hover:text-text">刷新</button></div>
        {tasks.map((task:any)=><div key={task.task_id} className="pdf-task-item" onClick={()=>resumeTask(task.task_id,task.filename,task.status)}>
          <span className="task-name">{task.filename}</span><div className="task-meta">
            <span className={`pdf-status-badge ${sbc(task.status)}`}>{translateStatus(task.status)}</span>
            {task.local_queue_position&&<span className="text-text-muted text-xs">本地队列第 {task.local_queue_position} 位</span>}
            <span className="text-text-muted text-xs">{new Date(task.created_at).toLocaleString('zh-CN')}</span>
            <button className="pdf-task-delete" onClick={e=>{e.stopPropagation();deleteTask(task.task_id,task.status)}}>删除</button>
            {['completed','completed_missing_artifact'].includes(task.status)&&<button className="pdf-task-action" onClick={e=>{e.stopPropagation();refetchTask(task.task_id)}}>补拉</button>}
            {isTerminal(task.status)&&<button className="pdf-task-action" onClick={e=>{e.stopPropagation();reparseTask(task.task_id)}}>重跑</button>}
          </div></div>)}</div>}

      {/* Empty */}
      {!markdown&&!parseActive&&tasks.length===0&&<div className="rounded-[24px] border border-dashed border-border bg-card px-6 py-12 text-center text-text-muted shadow-sm">
        <FileText className="mx-auto mb-3 h-10 w-10 opacity-40" /><p className="text-sm font-semibold text-text">选择一份财报后开始解析</p><p className="mt-1 text-xs">可从已下载列表直接解析，也支持批量上传最多 5 个 PDF。</p></div>}

      {toast&&<div className="fixed bottom-5 right-5 rounded-lg bg-text px-5 py-3 text-sm text-white shadow-lg transition-all z-50">{toast}</div>}
    </div>
  )
}
