import { type ReactNode } from 'react'
import { renderInline } from './InlineRenderer'
import {
  normalizeHtmlText,
  elementText,
  htmlTableStateClasses,
  htmlTextAlignment,
  spanAttribute,
  inferHtmlNumericColumns,
  hasHtmlTable,
  isNumericCell,
} from './rendererUtils'

function renderHtmlTableCell(cell: HTMLTableCellElement, keyPrefix: string, forceHeader = false, numericColumn = false) {
  const text = elementText(cell)
  const isHeader = forceHeader || cell.tagName.toLowerCase() === 'th'
  const alignment = htmlTextAlignment(cell)
  const numeric = isNumericCell(text)
  const classNames = [
    numeric ? 'is-number' : '',
    alignment ? `align-${alignment}` : numericColumn ? 'align-right' : '',
    ...htmlTableStateClasses(cell, text),
  ].filter(Boolean).join(' ')
  const children = text ? renderInline(text, keyPrefix) : '\u00a0'
  const colSpan = spanAttribute(cell, 'colspan')
  const rowSpan = spanAttribute(cell, 'rowspan')

  if (isHeader) {
    return (
      <th key={keyPrefix} scope="col" className={classNames || undefined} colSpan={colSpan} rowSpan={rowSpan}>
        {children}
      </th>
    )
  }

  return (
    <td key={keyPrefix} className={classNames || undefined} colSpan={colSpan} rowSpan={rowSpan}>
      {children}
    </td>
  )
}

function renderHtmlTableRows(rows: HTMLTableRowElement[], keyPrefix: string, forceHeader = false, numericColumns = new Set<number>()) {
  return rows.map((row, rowIndex) => {
    const cells = Array.from(row.cells)
    const text = normalizeHtmlText(cells.map((cell) => cell.textContent || '').join(' '))
    const rowClassName = htmlTableStateClasses(row, text).join(' ') || undefined

    return (
      <tr key={`${keyPrefix}-row-${rowIndex}`} className={rowClassName}>
        {cells.map((cell, cellIndex) => renderHtmlTableCell(cell, `${keyPrefix}-${rowIndex}-${cellIndex}`, forceHeader, numericColumns.has(cellIndex)))}
      </tr>
    )
  })
}

function renderHtmlTableElement(table: HTMLTableElement, keyPrefix: string) {
  const rows = Array.from(table.rows)
  if (!rows.length) return null

  const explicitHeadRows = table.tHead ? Array.from(table.tHead.rows) : []
  const explicitFootRows = table.tFoot ? Array.from(table.tFoot.rows) : []
  const hasExplicitHead = explicitHeadRows.length > 0
  const inferredHeadRows = hasExplicitHead || !rows[0]
    ? []
    : Array.from(rows[0].cells).some((cell) => cell.tagName.toLowerCase() === 'th')
      ? [rows[0]]
      : []
  const headRows = hasExplicitHead ? explicitHeadRows : inferredHeadRows
  const headSet = new Set(headRows)
  const footSet = new Set(explicitFootRows)
  const bodyRows = rows.filter((row) => !headSet.has(row) && !footSet.has(row))
  const numericColumns = inferHtmlNumericColumns(bodyRows)
  const caption = table.caption ? elementText(table.caption) : ''

  return (
    <div key={keyPrefix} className="chat-table-wrap chat-html-table-wrap" role="region" aria-label="消息表格" tabIndex={0}>
      <table className="chat-md-table chat-html-table">
        {caption && <caption>{renderInline(caption, `${keyPrefix}-caption`)}</caption>}
        {headRows.length > 0 && (
          <thead>{renderHtmlTableRows(headRows, `${keyPrefix}-head`, true, numericColumns)}</thead>
        )}
        <tbody>{renderHtmlTableRows(bodyRows, `${keyPrefix}-body`, false, numericColumns)}</tbody>
        {explicitFootRows.length > 0 && (
          <tfoot>{renderHtmlTableRows(explicitFootRows, `${keyPrefix}-foot`, false, numericColumns)}</tfoot>
        )}
      </table>
    </div>
  )
}

function renderSafeHtmlNode(node: ChildNode, keyPrefix: string): ReactNode | ReactNode[] | null {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = normalizeHtmlText(node.textContent || '')
    return text ? <p key={keyPrefix} className="chat-paragraph">{renderInline(text, keyPrefix)}</p> : null
  }

  if (node.nodeType !== Node.ELEMENT_NODE) return null

  const element = node as Element
  const tagName = element.tagName.toLowerCase()

  if (tagName === 'style' || tagName === 'script' || tagName === 'iframe' || tagName === 'svg') return null
  if (tagName === 'table') return renderHtmlTableElement(element as HTMLTableElement, keyPrefix)

  if (element.querySelector('table')) {
    return Array.from(element.childNodes)
      .map((child, index) => renderSafeHtmlNode(child, `${keyPrefix}-${index}`))
      .flat()
      .filter(Boolean)
  }

  const text = elementText(element)
  if (!text) return null

  if (/^h[1-6]$/.test(tagName) || /\b(?:fs-section|section|heading|title)\b/i.test(element.getAttribute('class') || '')) {
    return (
      <h4 key={keyPrefix} className="chat-heading chat-heading-2">
        {renderInline(text, keyPrefix)}
      </h4>
    )
  }

  return (
    <p key={keyPrefix} className="chat-paragraph">
      {renderInline(text, keyPrefix)}
    </p>
  )
}

export function renderSafeHtmlFragment(content: string, keyPrefix: string) {
  if (!hasHtmlTable(content) || typeof DOMParser === 'undefined' || typeof Node === 'undefined') return null

  const document = new DOMParser().parseFromString(content, 'text/html')
  const nodes = Array.from(document.body.childNodes)
    .map((node, index) => renderSafeHtmlNode(node, `${keyPrefix}-${index}`))
    .flat()
    .filter(Boolean)

  return nodes.length ? nodes : null
}
