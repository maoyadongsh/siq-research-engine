import { useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import { copyText } from '../../lib/clipboard'

interface MessageRendererProps {
  content: string
  streaming?: boolean
  variant?: 'assistant' | 'user'
}

type TextBlock =
  | { type: 'markdown'; lines: string[] }
  | { type: 'code'; language: string; code: string }

function splitFencedCode(content: string): TextBlock[] {
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

function splitTableRow(line: string) {
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

function isTableSeparator(line: string) {
  const cells = splitTableRow(line)
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, '')))
}

function isLikelyTableStart(lines: string[], index: number) {
  return Boolean(lines[index]?.includes('|') && lines[index + 1]?.includes('|') && isTableSeparator(lines[index + 1]))
}

function tableAlignment(value: string): 'left' | 'center' | 'right' {
  const cell = value.replace(/\s/g, '')
  if (cell.startsWith(':') && cell.endsWith(':')) return 'center'
  if (cell.endsWith(':')) return 'right'
  return 'left'
}

function isNumericCell(value: string) {
  const normalized = value
    .trim()
    .replace(/^\((.+)\)$/, '-$1')
    .replace(/[,\s]/g, '')
    .replace(/[亿元万千%％倍次]/g, '')

  return normalized === '--' || normalized === '-' || /^[-+]?\d+(\.\d+)?$/.test(normalized)
}

function isSafeLinkHref(href: string) {
  return /^(https?:\/\/|\/|#)/.test(href)
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let remaining = text
  let index = 0

  const patterns = [
    { type: 'code', regex: /`([^`]+)`/ },
    { type: 'link', regex: /\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/ },
    { type: 'bold', regex: /\*\*([^*]+)\*\*/ },
    { type: 'italic', regex: /(^|[^*])\*([^*\n]+)\*/ },
    { type: 'url', regex: /https?:\/\/[^\s)]+/ },
    { type: 'api-url', regex: /\/api\/[^\s)，）]+/ },
  ]

  while (remaining) {
    let earliest: { type: string; match: RegExpExecArray } | null = null

    for (const pattern of patterns) {
      const match = pattern.regex.exec(remaining)
      if (!match) continue
      if (!earliest || match.index < earliest.match.index) {
        earliest = { type: pattern.type, match }
      }
    }

    if (!earliest) {
      nodes.push(remaining)
      break
    }

    const { type, match } = earliest
    if (match.index > 0) nodes.push(remaining.slice(0, match.index))

    const key = `${keyPrefix}-${index}`
    if (type === 'code') {
      nodes.push(<code key={key} className="chat-inline-code">{match[1]}</code>)
    } else if (type === 'link') {
      const href = match[2]
      nodes.push(isSafeLinkHref(href) ? (
        <a key={key} href={href} target="_blank" rel="noreferrer" className="chat-link">
          {match[1]}
        </a>
      ) : match[0])
    } else if (type === 'bold') {
      nodes.push(<strong key={key}>{match[1]}</strong>)
    } else if (type === 'italic') {
      nodes.push(
        <span key={key}>
          {match[1]}
          <em>{match[2]}</em>
        </span>,
      )
    } else if (type === 'url') {
      nodes.push(
        <a key={key} href={match[0]} target="_blank" rel="noreferrer" className="chat-link">
          {match[0]}
        </a>,
      )
    } else if (type === 'api-url') {
      nodes.push(
        <a key={key} href={match[0]} target="_blank" rel="noreferrer" className="chat-link">
          {match[0]}
        </a>,
      )
    }

    remaining = remaining.slice(match.index + match[0].length)
    index += 1
  }

  return nodes
}

function renderTextWithBreaks(text: string, keyPrefix: string) {
  return text.split('\n').map((line, index, arr) => (
    <span key={`${keyPrefix}-${index}`}>
      {renderInline(line, `${keyPrefix}-${index}`)}
      {index < arr.length - 1 && <br />}
    </span>
  ))
}

function normalizeParagraph(lines: string[]) {
  const compactLines = lines.map((line) => line.trim()).filter(Boolean)
  if (compactLines.length <= 1) return compactLines.join('\n')

  const shouldKeepBreaks = compactLines.some((line) => (
    /[：:]$/.test(line) ||
    /^[-*+]?\s*(?:\d+[.)、]|[A-Za-z][.)])\s+/.test(line) ||
    /\s{2,}/.test(line)
  ))

  return shouldKeepBreaks ? compactLines.join('\n') : compactLines.join(' ')
}

function MarkdownBlocks({ lines, streaming }: { lines: string[]; streaming?: boolean }) {
  const elements: ReactNode[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i += 1
      continue
    }

    if (isLikelyTableStart(lines, i) && !streaming) {
      const header = splitTableRow(lines[i])
      const alignments = splitTableRow(lines[i + 1]).map(tableAlignment)
      const rows: string[][] = []
      i += 2

      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitTableRow(lines[i]))
        i += 1
      }

      elements.push(
        <div key={`table-${i}`} className="chat-table-wrap" role="region" aria-label="消息表格" tabIndex={0}>
          <table className="chat-md-table">
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th key={cellIndex} scope="col" className={`align-${alignments[cellIndex] || 'left'}`}>
                    {renderInline(cell, `th-${i}-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {header.map((_, cellIndex) => {
                    const cell = row[cellIndex] ?? ''
                    const numeric = isNumericCell(cell)
                    return (
                      <td
                        key={cellIndex}
                        className={`${numeric ? 'is-number' : ''} align-${numeric ? 'right' : alignments[cellIndex] || 'left'}`.trim()}
                      >
                        {renderInline(cell, `td-${i}-${rowIndex}-${cellIndex}`)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const className = `chat-heading chat-heading-${level}`
      const children = renderInline(heading[2], `heading-${i}`)
      if (level === 1) {
        elements.push(<h3 key={`heading-${i}`} className={className}>{children}</h3>)
      } else if (level === 2) {
        elements.push(<h4 key={`heading-${i}`} className={className}>{children}</h4>)
      } else if (level === 3) {
        elements.push(<h5 key={`heading-${i}`} className={className}>{children}</h5>)
      } else {
        elements.push(<h6 key={`heading-${i}`} className={className}>{children}</h6>)
      }
      i += 1
      continue
    }

    if (/^[-*_]{3,}$/.test(trimmed)) {
      elements.push(<hr key={`hr-${i}`} className="chat-divider" />)
      i += 1
      continue
    }

    if (/^>\s?/.test(trimmed)) {
      const quote: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quote.push(lines[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      elements.push(
        <blockquote key={`quote-${i}`} className="chat-quote">
          {renderTextWithBreaks(quote.join('\n'), `quote-${i}`)}
        </blockquote>,
      )
      continue
    }

    const unordered = trimmed.match(/^[-*+]\s+(.+)$/)
    const ordered = trimmed.match(/^\d+\.\s+(.+)$/)
    if (unordered || ordered) {
      const isOrdered = Boolean(ordered)
      const items: string[] = []
      const itemRegex = isOrdered ? /^\d+\.\s+(.+)$/ : /^[-*+]\s+(.+)$/

      while (i < lines.length) {
        const item = lines[i].trim().match(itemRegex)
        if (!item) break
        items.push(item[1])
        i += 1
      }

      const ListTag = isOrdered ? 'ol' : 'ul'
      elements.push(
        <ListTag key={`list-${i}`} className={`chat-list ${isOrdered ? 'chat-list-ordered' : 'chat-list-unordered'}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, `list-${i}-${itemIndex}`)}</li>
          ))}
        </ListTag>,
      )
      continue
    }

    const paragraph: string[] = [line]
    i += 1
    while (
      i < lines.length &&
      lines[i].trim() &&
      !isLikelyTableStart(lines, i) &&
      !/^(#{1,4})\s+/.test(lines[i].trim()) &&
      !/^[-*_]{3,}$/.test(lines[i].trim()) &&
      !/^>\s?/.test(lines[i].trim()) &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim())
    ) {
      paragraph.push(lines[i])
      i += 1
    }

    elements.push(
      <p key={`p-${i}`} className="chat-paragraph">
        {renderTextWithBreaks(normalizeParagraph(paragraph), `p-${i}`)}
      </p>,
    )
  }

  return <>{elements}</>
}

export default function MessageRenderer({ content, streaming = false, variant = 'assistant' }: MessageRendererProps) {
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const blocks = splitFencedCode(content)

  const copyCode = async (code: string, key: string) => {
    if (await copyText(code)) {
      setCopiedCode(key)
      window.setTimeout(() => setCopiedCode(null), 1400)
    } else {
      setCopiedCode(null)
    }
  }

  return (
    <div className={`chat-rendered chat-rendered-${variant}`}>
      {blocks.map((block, index) => {
        if (block.type === 'code') {
          const copied = copiedCode === String(index)
          return (
            <div key={index} className="chat-code-block">
              <div className="chat-code-toolbar">
                <span>{block.language}</span>
                <button type="button" onClick={() => copyCode(block.code, String(index))} aria-label="复制代码">
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? '已复制' : '复制'}
                </button>
              </div>
              <pre>
                <code>{block.code}</code>
              </pre>
            </div>
          )
        }

        return <MarkdownBlocks key={index} lines={block.lines} streaming={streaming} />
      })}
    </div>
  )
}
