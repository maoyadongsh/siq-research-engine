import { isSafeChatAssetHref, normalizeChatAssetUrl } from '@/lib/chatAssets'

// Stop bare URLs before citation punctuation.  The previous matcher included
// the comma in `source_url=https://...htm, source_anchor=...`, producing a
// broken href even though the underlying SEC URL was valid.
export const INLINE_URL_RE = /https?:\/\/[^\s)\]}>，。；;,"']+/

export interface MessageRendererProps {
  content: string
  streaming?: boolean
  variant?: 'assistant' | 'user'
  auditTraceApiPrefix?: string
  auditTraceId?: string
}

export type TextBlock =
  | { type: 'markdown'; lines: string[] }
  | { type: 'code'; language: string; code: string }

export function unwrapRuntimeCitationFences(content: string) {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const output: string[] = []
  let index = 0

  while (index < lines.length) {
    if (!/^```[\w+-]*\s*$/.test(lines[index])) {
      output.push(lines[index])
      index += 1
      continue
    }

    const closingIndex = lines.findIndex((line, candidateIndex) => candidateIndex > index && /^```\s*$/.test(line))
    if (closingIndex < 0) {
      output.push(...lines.slice(index))
      break
    }

    const fencedLines = lines.slice(index + 1, closingIndex)
    if (hasRuntimeCitationLines(fencedLines.join('\n'))) {
      output.push(...fencedLines)
    } else {
      output.push(...lines.slice(index, closingIndex + 1))
    }
    index = closingIndex + 1
  }

  return output.join('\n')
}

export function splitFencedCode(content: string): TextBlock[] {
  const lines = unwrapRuntimeCitationFences(content).split('\n')
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

export function splitAlignedTableRow(line: string) {
  return line.trim().split(/\s{2,}/).map((cell) => cell.trim())
}

export function isLikelyAlignedTableStart(lines: string[], index: number) {
  const current = splitAlignedTableRow(lines[index] || '')
  const next = splitAlignedTableRow(lines[index + 1] || '')
  return current.length >= 2 && next.length >= 2 && current.length <= 8 && next.length <= 8
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

export function hasRuntimeCitationLines(content: string) {
  return content
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .some((line) => /^\[(?:[A-Z]+)?\d+\]\s+source_type=/i.test(line.trim()))
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

export function parseAlignedTable(lines: string[], startIndex: number): MarkdownTableData | null {
  const header = splitAlignedTableRow(lines[startIndex] || '')
  if (header.length < 2) return null
  const rows: string[][] = []
  let i = startIndex + 1
  while (i < lines.length && lines[i].trim()) {
    const cells = splitAlignedTableRow(lines[i])
    if (cells.length < 2 || cells.length > 8) break
    rows.push(cells)
    i += 1
  }
  if (!rows.length) return null
  const width = Math.max(header.length, ...rows.map((row) => row.length))
  return {
    header: [...header, ...Array(Math.max(0, width - header.length)).fill('')],
    alignments: Array.from({ length: width }, (_, column) => column === 0 ? 'left' : 'right'),
    rows: rows.map((row) => [...row, ...Array(Math.max(0, width - row.length)).fill('')]),
    lineIndex: i,
  }
}

export function isCitationHeading(trimmed: string) {
  return /^(?:#{1,4}\s+)?引用来源[:：]?$/.test(trimmed)
}

export function isAuditHeading(trimmed: string) {
  return /^(?:#{1,4}\s+)?(?:审计详情|证据链审计详情|计算器校验|勾稽校验|校验失败详情)(?:[（(][^\n）)]*[）)])?[:：]?$/.test(trimmed)
}

export function auditHeadingTitle(trimmed: string) {
  const title = trimmed.replace(/^#{1,4}\s+/, '').replace(/[:：]$/, '')
  return title === '审计详情' ? '证据链审计详情' : title
}

export function extractAnswerAuditTraceId(lines: string[]) {
  for (const line of lines) {
    const match = line.match(/\baat_[a-f0-9]{32}\b/i)
    if (match) return match[0]
  }
  return ''
}

export function collectHeadingSectionLines(
  lines: string[],
  startIndex: number,
  isSameSectionHeading: (trimmed: string) => boolean,
) {
  const sectionLines: string[] = []
  let nextIndex = startIndex + 1
  while (nextIndex < lines.length) {
    const nextTrimmed = lines[nextIndex].trim()
    if (/^(#{1,6})\s+/.test(nextTrimmed) && !isSameSectionHeading(nextTrimmed)) break
    sectionLines.push(lines[nextIndex])
    nextIndex += 1
  }
  return { lines: sectionLines, nextIndex }
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
  if (/\/api\/documents\/source\/[^/]+\/table\//.test(href) || /\/api\/documents\/artifact\/[^/]+\/tables/.test(href)) return 'table'
  if (/\/api\/documents\/source\//.test(href) || /\/api\/documents\/artifact\//.test(href)) return 'source'
  if (/\/api\/source\/[^/]+\/table\//.test(href) || /(?:表格|可读表格)/.test(label)) return 'table'
  if (/\/api\/pdf_page\//.test(href) || /PDF/.test(label)) return 'pdf'
  if (/\/api\/source\/[^/]+\/page\//.test(href) || /(?:页来源|定位页|来源|披露原文)/.test(label) || /(?:^|\.)sec\.gov\//i.test(href)) return 'source'
  return 'other'
}

export function parseCitationActions(item: string) {
  const actions: CitationAction[] = []
  const addAction = (label: string, href: string) => {
    const kind = citationActionKind(label, href)
    if (!actions.some((action) => action.href === href || action.kind === kind)) {
      actions.push({ label, href, kind })
    }
  }
  const secUrlPattern = 'https://(?:www\\.)?sec\\.gov/[^\\s)\\]}>，。；;,"\']+'
  const explicitTarget = item.match(new RegExp(`打开披露原文\\s*=\\s*(${secUrlPattern})`, 'i'))?.[1]
  const sourceUrl = item.match(new RegExp(`\\b(?:source_url|url)=(${secUrlPattern})`, 'i'))?.[1]
  const sourceAnchor = item.match(/\b(?:source_anchor|html_anchor|anchor)=([^,，。;；\s]+)/i)?.[1]
  const secTarget = explicitTarget || (
    sourceUrl
      ? sourceAnchor && sourceAnchor !== '未返回' && !sourceUrl.includes('#')
        ? `${sourceUrl}#${sourceAnchor}`
        : sourceUrl
      : ''
  )
  if (secTarget) addAction('打开披露原文', secTarget)

  const text = item.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/g, (match, label, href) => {
    if (
      /\/api\/(?:pdf_page|source|documents\/source|documents\/artifact)\//.test(href) ||
      /(?:打开PDF|PDF页|页来源|定位页|查看表格|可读表格|查看页来源|打开来源|打开产物|文档来源|打开披露原文|披露原文)/.test(label)
    ) {
      addAction(label, href)
      return ''
    }
    return match
  })
    .replace(/(打开PDF页|打开PDF定位页[0-9]*|查看页来源|查看定位页[0-9]*来源|查看表格|查看可读表格[0-9]*|打开披露原文)\s*[:：=]\s*(https?:\/\/[^\s，,。；;]+|\/api\/(?:pdf_page|source)\/[^\s，,。；;]+)/g, (_match, label, href) => {
      addAction(label, href)
      return ''
    })
    .replace(/(?:打开PDF页|打开PDF定位页[0-9]*|查看页来源|查看定位页[0-9]*来源|查看表格|查看可读表格[0-9]*)\((https?:\/\/[^)\s]+|\/api\/(?:pdf_page|source)\/[^)\s]+)\)/g, (match, href) => {
      const label = match.slice(0, match.indexOf('('))
      addAction(label, href)
      return ''
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

export function matchBoldHeading(trimmed: string) {
  const match = trimmed.match(/^\*\*([^*\n]+)\*\*[:：]?\s*$/)
  if (!match) return null
  const value = match[1].trim().replace(/[:：]\s*$/, '').trim()
  if (!value || value.length > 64 || /[。！？.!?]$/.test(value)) return null
  return value
}

export function isMarkdownBoundary(lines: string[], index: number) {
  const trimmed = lines[index]?.trim() || ''
  return (
    !trimmed ||
    isLikelyTableStart(lines, index) ||
    /^(#{1,6})\s+/.test(trimmed) ||
    /^[-*_]{3,}$/.test(trimmed) ||
    /^>\s?/.test(trimmed) ||
    /^◆\s+/.test(trimmed) ||
    /^[▸›]\s+/.test(trimmed) ||
    /^[-*+]\s+/.test(trimmed) ||
    /^\d+\.\s+/.test(trimmed) ||
    Boolean(matchBoldHeading(trimmed)) ||
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
