import { type ReactNode } from 'react'
import { renderInline, renderTextWithBreaks } from './InlineRenderer'
import { renderSafeHtmlFragment } from './HtmlTableRenderer'
import { MarkdownTableRenderer } from './MarkdownTableRenderer'
import { CitationBlock } from './CitationBlock'
import { AuditTraceBlock } from './AuditTraceBlock'
import {
  collectHeadingSectionLines,
  auditHeadingTitle,
  hasHtmlTable,
  isCitationHeading,
  isAuditHeading,
  headingTone,
  isLikelyTableStart,
  isLikelyAlignedTableStart,
  matchBoldHeading,
  matchCjkHeading,
  isMarkdownBoundary,
  normalizeParagraph,
  parseMarkdownTable,
  parseAlignedTable,
} from './rendererUtils'

export function MarkdownBlocks({
  lines,
  streaming,
  auditTraceApiPrefix = '/api',
}: {
  lines: string[]
  streaming?: boolean
  auditTraceApiPrefix?: string
}) {
  const elements: ReactNode[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i += 1
      continue
    }

    if (hasHtmlTable(lines.slice(i).join('\n'))) {
      const htmlLines: string[] = []
      while (i < lines.length) {
        htmlLines.push(lines[i])
        const joined = htmlLines.join('\n')
        i += 1
        if (/<\/table>/i.test(joined)) break
      }

      const htmlElements = renderSafeHtmlFragment(htmlLines.join('\n'), `html-${i}`)
      if (htmlElements) {
        elements.push(...htmlElements)
        continue
      }
    }

    if (isCitationHeading(trimmed)) {
      const section = collectHeadingSectionLines(lines, i, isCitationHeading)
      const citationLines = section.lines
      i = section.nextIndex
      elements.push(<CitationBlock key={`citation-${i}`} blockKey={`citation-${i}`} lines={citationLines} />)
      continue
    }

    if (/^(?:\[[A-Z]?\d+\]\s*)?source_type=/.test(trimmed)) {
      const citationLines: string[] = []
      while (i < lines.length) {
        const candidate = lines[i].trim()
        if (!candidate || /^(#{1,6})\s+/.test(candidate)) break
        citationLines.push(lines[i])
        i += 1
      }
      elements.push(<CitationBlock key={`citation-auto-${i}`} blockKey={`citation-auto-${i}`} lines={citationLines} />)
      continue
    }

    if (isAuditHeading(trimmed)) {
      const section = collectHeadingSectionLines(lines, i, isAuditHeading)
      const auditLines = section.lines
      i = section.nextIndex
      elements.push(
        <AuditTraceBlock
          key={`audit-${i}`}
          blockKey={`audit-${i}`}
          lines={auditLines}
          apiPrefix={auditTraceApiPrefix}
          title={auditHeadingTitle(trimmed)}
        />,
      )
      continue
    }

    if (isLikelyTableStart(lines, i)) {
      const tableData = parseMarkdownTable(lines, i)
      if (tableData) {
        i = tableData.lineIndex
        elements.push(<MarkdownTableRenderer key={`table-${i}`} data={tableData} keyPrefix={`table-${i}`} />)
        continue
      }
    }

    if (isLikelyAlignedTableStart(lines, i) && !streaming) {
      const tableData = parseAlignedTable(lines, i)
      if (tableData) {
        i = tableData.lineIndex
        elements.push(<MarkdownTableRenderer key={`aligned-table-${i}`} data={tableData} keyPrefix={`aligned-table-${i}`} />)
        continue
      }
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const tone = headingTone(heading[2])
      const className = `chat-heading chat-heading-${level}${tone ? ` chat-heading-${tone}` : ''}`
      const children = renderInline(heading[2], `heading-${i}`)
      const headingTags: Record<number, 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'> = {
        1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4', 5: 'h5', 6: 'h6',
      }
      const Tag = headingTags[level] ?? 'h6'
      elements.push(<Tag key={`heading-${i}`} className={className}>{children}</Tag>)
      i += 1
      continue
    }

    const boldHeading = matchBoldHeading(trimmed)
    if (boldHeading) {
      const tone = headingTone(boldHeading)
      elements.push(
        <h3 key={`bold-heading-${i}`} className={`chat-heading chat-heading-3${tone ? ` chat-heading-${tone}` : ''}`}>
          {renderInline(boldHeading, `bold-heading-${i}`)}
        </h3>,
      )
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

    const cjkHeading = matchCjkHeading(trimmed)
    if (cjkHeading) {
      elements.push(
        <h4 key={`cjk-heading-${i}`} className="chat-heading chat-heading-2">
          {renderInline(cjkHeading[2], `cjk-heading-${i}`)}
        </h4>,
      )
      i += 1
      continue
    }

    const symbolHeading = trimmed.match(/^◆\s*(.+)$/)
    if (symbolHeading) {
      const tone = headingTone(symbolHeading[1])
      elements.push(
        <h4 key={`symbol-heading-${i}`} className={`chat-heading chat-heading-2 chat-symbol-heading${tone ? ` chat-heading-${tone}` : ''}`}>
          {renderInline(symbolHeading[1], `symbol-heading-${i}`)}
        </h4>,
      )
      i += 1
      continue
    }

    const unordered = trimmed.match(/^[-*+]\s+(.+)$/)
    const ordered = trimmed.match(/^(\d+)\.\s+(.+)$/)
    const symbolItem = trimmed.match(/^[▸›]\s+(.+)$/)
    if (unordered || ordered || symbolItem) {
      const isOrdered = Boolean(ordered)
      const isSymbolList = Boolean(symbolItem)
      const items: Array<{ text: string; value?: number }> = []
      const itemRegex = isOrdered ? /^(\d+)\.\s+(.+)$/ : isSymbolList ? /^[▸›]\s+(.+)$/ : /^[-*+]\s+(.+)$/

      while (i < lines.length) {
        const item = lines[i].trim().match(itemRegex)
        if (!item) break
        items.push({
          value: isOrdered ? Number(item[1]) : undefined,
          text: isOrdered ? item[2] : item[1],
        })
        i += 1

        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !isMarkdownBoundary(lines, i)) {
          const last = items[items.length - 1]
          last.text = `${last.text}\n${lines[i].trim()}`
          i += 1
        }
      }

      const ListTag = isOrdered ? 'ol' : 'ul'
      const start = isOrdered ? items[0]?.value : undefined
      elements.push(
        <ListTag
          key={`list-${i}`}
          className={`chat-list ${isOrdered ? 'chat-list-ordered' : isSymbolList ? 'chat-list-symbol' : 'chat-list-unordered'}`}
          start={start}
        >
          {items.map((item, itemIndex) => (
            <li key={itemIndex} value={isOrdered ? item.value : undefined}>
              {renderTextWithBreaks(item.text, `list-${i}-${itemIndex}`)}
            </li>
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
      !isLikelyAlignedTableStart(lines, i) &&
      !/^(#{1,6})\s+/.test(lines[i].trim()) &&
      !/^[-*_]{3,}$/.test(lines[i].trim()) &&
      !/^>\s?/.test(lines[i].trim()) &&
      !/^◆\s+/.test(lines[i].trim()) &&
      !/^[▸›]\s+/.test(lines[i].trim()) &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !matchBoldHeading(lines[i].trim()) &&
      !matchCjkHeading(lines[i].trim())
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
