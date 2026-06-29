import type { TaskItem } from './pdfTypes'

export type PdfMarket = 'CN' | 'HK' | 'US' | 'JP' | 'KR' | 'EU'
type InternalPdfMarket = PdfMarket | 'DOC'

const SUPPORTED_MARKETS: InternalPdfMarket[] = ['CN', 'HK', 'US', 'JP', 'KR', 'EU', 'DOC']

function normalizeMarket(value: unknown): InternalPdfMarket | '' {
  const market = String(value || '').trim().toUpperCase()
  return SUPPORTED_MARKETS.includes(market as InternalPdfMarket) ? (market as InternalPdfMarket) : ''
}

function inferTaskMarket(task: TaskItem): InternalPdfMarket | '' {
  const taskWithMarket = task as TaskItem & {
    market?: unknown
    submit_config?: { market?: unknown } | null
  }
  const taskId = String(task.task_id || '')
  if (/^doc[-_]/i.test(taskId)) return 'DOC'

  const explicitMarket = normalizeMarket(taskWithMarket.market) || normalizeMarket(taskWithMarket.submit_config?.market)
  if (explicitMarket) return explicitMarket

  const filename = String(task.filename || '')
  const match = filename.match(/(?:^|_)(CN|HK|US|JP|KR|EU|DOC)(?:_|$)/i)
  return match ? (match[1].toUpperCase() as InternalPdfMarket) : ''
}

export function taskMatchesMarket(task: TaskItem, market?: PdfMarket | null): boolean {
  if (!market) return true
  const taskMarket = inferTaskMarket(task)
  if (taskMarket === 'DOC') return false
  if (taskMarket) return taskMarket === market
  return market === 'CN'
}
