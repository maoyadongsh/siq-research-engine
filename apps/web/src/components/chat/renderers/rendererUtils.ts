import { isSafeChatAssetHref, normalizeChatAssetUrl } from '@/lib/chatAssets'

export interface MessageRendererProps {
  content: string
  streaming?: boolean
  variant?: 'assistant' | 'user'
}

export type TextBlock =
  | { type: 'markdown'; lines: string[] }
  | { type: 'code'; language: string; code: string }

export function splitFencedCode(content: string): TextBlock[] {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: TextBlock[] = []
  let markdownLines: string[] = []
  let codeLines: string[] = []
  let language = ''
  let inCode = false

  const flushMarkdown = () => {
    if (markdownLines.length) {
      blocks.push({ type: 'markdown', lines: markdownLines })
      markdownLines = []
    }
  }

  for (const line of lines) {
    const fence = line.match(/^```([\w+-]*)\s*$/)

    if (fence) {
      if (inCode) {
        blocks.push({ type: 'code', language, code: codeLines.join('\n') })
        codeLines = []
        language = ''
        inCode = false
      } else {
        flushMarkdown()
        language = fence[1] || 'text'
        inCode = true
      }
      continue
    }

    if (inCode) {
      codeLines.push(line)
    } else {
      markdownLines.push(line)
    }
  }

  if (inCode) {
    markdownLines.push(`\`\`\`${language}`)
    markdownLines.push(...codeLines)
  }
  flushMarkdown()

  return blocks
}

export function splitTableRow(line: string) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  const cells: string[] = []
  let current = ''

  for (let i = 0; i < trimmed.length; i += 1) {
    const char = trimmed[i]
    const previous = trimmed[i - 1]

    if (char === '|' && previous !== '\\') {
      cells.push(current.trim().replace(/\\\|/g, '|'))
      current = ''
    } else {
      current += char
    }
  }

  cells.push(current.trim().replace(/\\\|/g, '|'))
  return cells
}

export function isTableSeparator(line: string) {
  const cells = splitTableRow(line)
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, '')))
}

export function isLikelyTableStart(lines: string[], index: number) {
  return Boolean(lines[index]?.includes('|') && lines[index + 1]?.includes('|') && isTableSeparator(lines[index + 1]))
}

export function tableAlignment(value: string): 'left' | 'center' | 'right' {
  const cell = value.replace(/\s/g, '')
  if (cell.startsWith(':') && cell.endsWith(':')) return 'center'
  if (cell.endsWith(':')) return 'right'
  return 'left'
}

export function isNumericCell(value: string) {
  const normalized = value
    .trim()
    .replace(/^\((.+)\)$/, '-$1')
    .replace(/[,\s]/g, '')
    .replace(/[亿元万千%％倍次]/g, '')

  return normalized === '--' || normalized === '-' || /^[-+]?\d+(\.\d+)?$/.test(normalized)
}

export function isSafeLinkHref(href: string) {
  return isSafeChatAssetHref(href)
}

export function normalizeLinkHref(href: string) {
  return normalizeChatAssetUrl(href)
}

const LATEX_SYMBOLS: Record<string, string> = {
  div: '÷',
  times: '×',
  cdot: '·',
  pm: '±',
  percent: '%',
  pi: 'π',
  le: '≤',
  leq: '≤',
  ge: '≥',
  geq: '≥',
  neq: '≠',
  ne: '≠',
  approx: '≈',
  infty: '∞',
  to: '→',
  rightarrow: '→',
  longrightarrow: '→',
  leftarrow: '←',
  longleftarrow: '←',
  leftrightarrow: '↔',
  longleftrightarrow: '↔',
  uparrow: '↑',
  downarrow: '↓',
  Rightarrow: '⇒',
  Leftarrow: '⇐',
  Leftrightarrow: '⇔',
  implies: '⇒',
}

export function isLikelyInlineMathExpression(value: string) {
  const trimmed = value.trim()
  if (!trimmed || trimmed.length > 160 || /[\u4e00-\u9fff]/.test(trimmed)) return false
  if (/\s/.test(trimmed) && !/[\\^_{}+\-*/=<>%()[\]!.,]/.test(trimmed)) return false
  return /^[0-9A-Za-z\\{}_^+\-*/=<>()[\].,%!| ]+$/.test(trimmed)
}

export function normalizeInlineMathExpression(value: string) {
  return value
    .trim()
    .replace(/\\sqrt\[([^\]]+)\]\{([^{}]+)\}/g, '√[$1]($2)')
    .replace(/\\sqrt\{([^{}]+)\}/g, '√($1)')
    .replace(/(\d+)\^\{(st|nd|rd|th)\}/gi, '$1$2')
    .replace(/\^\{([^{}]+)\}/g, '^$1')
    .replace(/_\{([^{}]+)\}/g, '_$1')
    .replace(/\\([A-Za-z]+|[%$#&{}])/g, (match, command: string) => LATEX_SYMBOLS[command] ?? (command === '%' ? '%' : match.replace(/^\\/, '')))
    .replace(/\s+/g, ' ')
}

export function isMarkdownCodeLanguage(language: string) {
  return /^(?:md|markdown|gfm)$/i.test(language.trim())
}

export function hasMarkdownTable(content: string) {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  return lines.some((_, index) => isLikelyTableStart(lines, index))
}

export function hasHtmlTable(content: string) {
  return /<table(?:\s|>)/i.test(content) && /<\/table>/i.test(content)
}

export function normalizeHtmlText(value: string) {
  return value.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim()
}

export function elementText(element: Element) {
  return normalizeHtmlText(element.textContent || '')
}

export function htmlTableStateClasses(element: Element, text = elementText(element)) {
  const rawClass = element.getAttribute('class') || ''
  const signature = `${rawClass} ${text}`
  const classes: string[] = []

  if (/\b(?:up|positive|increase|gain|rise|red)\b/i.test(signature) || /(?:▲|增长|增加|改善|转正)/.test(signature)) {
    classes.push('is-positive')
  }
  if (/\b(?:down|negative|decrease|loss|green)\b/i.test(signature) || /(?:▼|下降|减少|转负|亏损)/.test(signature)) {
    classes.push('is-negative')
  }
  if (/\b(?:highlight|emphasis|key)\b/i.test(rawClass)) {
    classes.push('is-highlight')
  }
  if (/\b(?:total|subtotal|summary)\b/i.test(rawClass) || /(?:合计|小计|净额|整体画像)/.test(text)) {
    classes.push('is-total')
  }

  return classes
}

export function htmlTextAlignment(element: Element): 'left' | 'center' | 'right' | null {
  const align = (element.getAttribute('align') || '').toLowerCase()
  if (align === 'left' || align === 'center' || align === 'right') return align

  const style = element.getAttribute('style') || ''
  const match = style.match(/text-align\s*:\s*(left|center|right)/i)
  return match ? (match[1].toLowerCase() as 'left' | 'center' | 'right') : null
}

export function spanAttribute(element: Element, name: 'colspan' | 'rowspan') {
  const parsed = Number(element.getAttribute(name))
  return Number.isFinite(parsed) && parsed > 1 ? Math.min(parsed, 20) : undefined
}

export function inferNumericColumns(rows: string[][], columnCount: number) {
  return new Set(
    Array.from({ length: columnCount }, (_, columnIndex) => columnIndex).filter((columnIndex) => {
      const values = rows.map((row) => row[columnIndex] ?? '').filter((value) => value.trim())
      if (!values.length) return false
      const numericCount = values.filter(isNumericCell).length
      return numericCount >= Math.max(1, Math.ceil(values.length * 0.6))
    }),
  )
}

export function inferHtmlNumericColumns(rows: HTMLTableRowElement[]) {
  const columnCount = Math.max(0, ...rows.map((row) => row.cells.length))
  const textRows = rows.map((row) => Array.from(row.cells).map((cell) => elementText(cell)))
  return inferNumericColumns(textRows, columnCount)
}

export interface MarkdownTableData {
  header: string[]
  alignments: Array<'left' | 'center' | 'right'>
  rows: string[][]
  lineIndex: number
}

export function parseMarkdownTable(lines: string[], startIndex: number): MarkdownTableData | null {
  const header = splitTableRow(lines[startIndex])
  const separator = lines[startIndex + 1]
  if (!separator || !splitTableRow(separator).every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, '')))) {
    return null
  }

  const alignments = splitTableRow(separator).map(tableAlignment)
  const rows: string[][] = []
  let i = startIndex + 2

  while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
    rows.push(splitTableRow(lines[i]))
    i += 1
  }

  return { header, alignments, rows, lineIndex: i }
}

export function isCitationHeading(trimmed: string) {
  return /^(?:#{1,4}\s+)?引用来源[:：]?$/.test(trimmed)
}

export function headingTone(text: string) {
  if (/(?:核心|关键|主要)?(?:结论|观点|摘要|小结|综合判断|整体画像)/.test(text)) return 'summary'
  if (/(?:风险|警示|注意|红线|异常|存疑)/.test(text)) return 'warning'
  return null
}

export type CitationAction = {
  label: string
  href: string
  kind: 'pdf' | 'source' | 'table' | 'other'
}

export function citationActionKind(label: string, href: string): CitationAction['kind'] {
  if (/\/api\/source\/[^/]+\/table\//.test(href) || /(?:表格|可读表格)/.test(label)) return 'table'
  if (/\/api\/source\/[^/]+\/page\//.test(href) || /(?:页来源|定位页|来源)/.test(label)) return 'source'
  if (/\/api\/pdf_page\//.test(href) || /PDF/.test(label)) return 'pdf'
  return 'other'
}

export function parseCitationActions(item: string) {
  const actions: CitationAction[] = []
  const text = item.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/g, (match, label, href) => {
    if (
      /\/api\/(?:pdf_page|source)\//.test(href) ||
      /(?:打开PDF|PDF页|页来源|定位页|查看表格|可读表格|查看页来源)/.test(label)
    ) {
      actions.push({ label, href, kind: citationActionKind(label, href) })
      return ''
    }
    return match
  })
    .replace(/[，,、\s]+$/g, '')
    .replace(/[，,、]\s*[，,、]+/g, '，')
    .trim()

  return { text, actions }
}

export function matchCjkHeading(trimmed: string) {
  return (
    trimmed.match(/^([一二三四五六七八九十百]+)[、.．]\s*(.+)$/) ||
    trimmed.match(/^[(（]([一二三四五六七八九十百]+)[)）]\s*(.+)$/)
  )
}

export function isMarkdownBoundary(lines: string[], index: number) {
  const trimmed = lines[index]?.trim() || ''
  return (
    !trimmed ||
    isLikelyTableStart(lines, index) ||
    /^(#{1,4})\s+/.test(trimmed) ||
    /^[-*_]{3,}$/.test(trimmed) ||
    /^>\s?/.test(trimmed) ||
    /^◆\s+/.test(trimmed) ||
    /^[▸›]\s+/.test(trimmed) ||
    /^[-*+]\s+/.test(trimmed) ||
    /^\d+\.\s+/.test(trimmed) ||
    Boolean(matchCjkHeading(trimmed))
  )
}

export function normalizeParagraph(lines: string[]) {
  const compactLines = lines.map((line) => line.trim()).filter(Boolean)
  if (compactLines.length <= 1) return compactLines.join('\n')

  const shouldKeepBreaks = compactLines.some((line) => (
    /[：:]$/.test(line) ||
    /^[-*+]?\s*(?:\d+[.)、]|[A-Za-z][.)])\s+/.test(line) ||
    /\s{2,}/.test(line)
  ))

  return shouldKeepBreaks ? compactLines.join('\n') : compactLines.join(' ')
}
