const TIMEZONE_SUFFIX_RE = /(?:Z|[+-]\d{2}:?\d{2})$/i
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/
const ISO_LIKE_RE = /^\d{4}-\d{2}-\d{2}T/

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const yearDateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const fullDateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

function normalizeTimestamp(value: string) {
  const trimmed = value.trim()
  if (DATE_ONLY_RE.test(trimmed)) return `${trimmed}T00:00:00Z`
  if (ISO_LIKE_RE.test(trimmed) && !TIMEZONE_SUFFIX_RE.test(trimmed)) return `${trimmed}Z`
  return trimmed
}

export function parseChatTimestamp(value?: string | null) {
  if (!value) return null
  const date = new Date(normalizeTimestamp(value))
  return Number.isNaN(date.getTime()) ? null : date
}

function sameLocalDay(a: Date, b: Date) {
  return (
    a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
  )
}

export function formatChatMessageTime(value?: string | null) {
  const date = parseChatTimestamp(value)
  if (!date) return ''

  const now = new Date()
  if (sameLocalDay(date, now)) return `今天 ${timeFormatter.format(date)}`

  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameLocalDay(date, yesterday)) return `昨天 ${timeFormatter.format(date)}`

  if (date.getFullYear() === now.getFullYear()) return dateTimeFormatter.format(date)
  return yearDateTimeFormatter.format(date)
}

export function formatChatMessageTimeTitle(value?: string | null) {
  const date = parseChatTimestamp(value)
  return date ? fullDateTimeFormatter.format(date) : ''
}

export function formatChatSessionTime(value: string | null) {
  if (!value) return '空会话'
  return formatChatMessageTime(value) || '时间未知'
}

export function toChatDateTimeAttr(value?: string | null) {
  const date = parseChatTimestamp(value)
  return date?.toISOString()
}
