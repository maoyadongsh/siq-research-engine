import { renderInline } from './InlineRenderer'
import { inferNumericColumns, isNumericCell, type MarkdownTableData } from './rendererUtils'

export interface MarkdownTableRendererProps {
  data: MarkdownTableData
  keyPrefix: string
}

export function MarkdownTableRenderer({ data, keyPrefix }: MarkdownTableRendererProps) {
  const { header, alignments, rows } = data
  const numericColumns = inferNumericColumns(rows, header.length)

  return (
    <div key={keyPrefix} className="chat-table-wrap" role="region" aria-label="消息表格" tabIndex={0}>
      <table className="chat-md-table">
        <thead>
          <tr>
            {header.map((cell, cellIndex) => {
              const columnNumeric = numericColumns.has(cellIndex)
              return (
                <th key={cellIndex} scope="col" className={`align-${columnNumeric ? 'right' : alignments[cellIndex] || 'left'}`}>
                  {renderInline(cell, `th-${keyPrefix}-${cellIndex}`)}
                </th>
              )
            })}
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
                    {renderInline(cell, `td-${keyPrefix}-${rowIndex}-${cellIndex}`)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
