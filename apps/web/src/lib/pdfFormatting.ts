export function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export function formatDuration(seconds: number): string {
  if (seconds == null || seconds < 0) return '--'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? m + '分' + s + '秒' : s + '秒'
}

export function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function formatFinancialNumber(value: number): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return '--'
  const a = Math.abs(n)
  if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (a >= 1e4) return (n / 1e4).toFixed(2) + '万'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function escHtml(text: string): string {
  const d = document.createElement('div')
  d.textContent = text
  return d.innerHTML
}

export const statusLabels: Record<string, string> = {
  queued: '已排队',
  uploaded: '已上传',
  submitting: '提交中',
  submitted: '已提交',
  pending: '排队中',
  processing: '处理中',
  completed: '已完成',
  completed_missing_artifact: '结果缺失',
  failed: '失败',
  error: '错误',
  cancelled: '已停止',
}

export function translateStatus(s: string): string {
  return statusLabels[s] || s
}

export function isTerminal(s: string): boolean {
  return ['completed', 'completed_missing_artifact', 'success', 'done', 'finished', 'failed', 'error', 'failure', 'cancelled'].includes(s)
}

export function scopeName(s: string): string {
  if (s === 'consolidated') return '合并'
  if (s === 'parent_company') return '母公司'
  return s || '--'
}

export function scopeNameForMarket(s: string, market?: string | null): string {
  const normalized = String(market || '').trim().toUpperCase()
  if (normalized && normalized !== 'CN') {
    if (s === 'consolidated') return 'Consolidated'
    if (s === 'parent_company') return 'Parent company'
  }
  return scopeName(s)
}

export function candidateMeta(item: Record<string, unknown> | null | undefined): string {
  if (isNonBlockingCandidateStatus(item?.status)) {
    return String(item?.display_note || item?.note || '未单独列示，不计入核心报表缺失')
  }
  if (!item || item.status === 'missing') return '需复核：未在表格中定位'
  if (!item.table_index) return item._source === 'financial_data' ? '已抽取，暂无表格定位' : '需复核：暂无表格定位'
  const page = item.pdf_page_number
    ? ` / PDF ${String(item.pdf_page_number)}页${item.pdf_page_source === 'markdown_marker_inferred' ? '(推断)' : ''}`
    : ''
  const cMap: Record<string, string> = { high: '高置信', medium: '中置信', low: '低置信' }
  const conf = item.confidence ? ` / ${cMap[String(item.confidence)] || String(item.confidence)}` : ''
  const line = item.line ? ` / 行 ${String(item.line)}` : ''
  return `表 ${String(item.table_index)}${line}${page}${conf}`
}

export function isNonBlockingCandidateStatus(status: unknown): boolean {
  return ['not_applicable', 'not_required', 'not_separately_presented', 'excluded'].includes(String(status || ''))
}

export function suspectTableMeta(item: Record<string, unknown> | null | undefined): string {
  if (!item || !item.table_index) return '表 未定位'
  const line = item.line ? ` / 行 ${String(item.line)}` : ''
  const page = item.pdf_page_number
    ? ` / PDF ${String(item.pdf_page_number)}页${item.pdf_page_source === 'markdown_marker_inferred' ? '(推断)' : ''}`
    : ''
  return `表 ${String(item.table_index)}${line}${page}`
}

export function suspectReasons(reasons: string[]): string {
  const m: Record<string, string> = {
    single_row: '单行/空壳',
    many_empty_cells: '空单元格偏多',
    low_numeric_density: '数字密度偏低',
    key_table_too_short: '关键表过短',
    low_confidence_core_candidate: '低置信核心候选',
    medium_confidence_core_candidate: '中置信核心候选',
  }
  return (reasons || []).map((r) => m[r] || r).join('、')
}

export function normalizeCellText(t: string): string {
  return String(t || '')
    .replace(/\s+/g, '')
    .replace(/[，,]/g, '')
    .trim()
}

export function isUsefulTextAnchor(t: string): boolean {
  const n = normalizeCellText(t)
  if (n.length < 6) return false
  if (/^[\d.\-+()%（）/／—–_]+$/.test(n)) return false
  if (/^(--|-|不适用|无|否|是|0|0.00)$/.test(n)) return false
  return true
}

export function workflowStateLabel(status: unknown): string {
  const s = String(status || 'missing')
  if (s === 'ready') return '已就绪'
  if (s === 'missing_optional') return '可选'
  if (s === 'stale_optional') return '可选刷新'
  if (s === 'unknown_optional') return '可选待确认'
  if (s === 'stale') return '需刷新'
  if (s === 'building') return '编译中'
  if (s === 'failed') return '失败'
  if (s === 'needs_review') return '需复核'
  if (s === 'unknown') return '待确认'
  return '待处理'
}

export function workflowStateClass(status: unknown): string {
  const s = String(status || 'missing')
  if (s === 'ready') return 'secondary-status-success'
  if (s === 'missing_optional' || s === 'stale_optional' || s === 'unknown_optional') return 'secondary-status-info'
  if (s === 'building') return 'secondary-status-info'
  if (s === 'failed') return 'secondary-status-error'
  if (s === 'stale' || s === 'needs_review' || s === 'unknown') return 'secondary-status-warning'
  return 'secondary-status-warning'
}

export function statusBadgeClass(s: string): string {
  const m: Record<string, string> = {
    queued: 'queued',
    uploaded: 'uploaded',
    submitting: 'submitting',
    submitted: 'submitted',
    pending: 'pending',
    processing: 'processing',
    completed: 'completed',
    completed_missing_artifact: 'failed',
    failed: 'failed',
    error: 'error',
    cancelled: 'cancelled',
    success: 'completed',
    done: 'completed',
    finished: 'completed',
  }
  return m[s] || 'pending'
}
