import type { SrcCtx } from '../../lib/pdfTypes'
import { makeEditableHtml } from '../../lib/pdfSanitize'

export interface PdfReadingPaneProps {
  readingMode: 'table' | 'page'
  srcCtx: React.MutableRefObject<SrcCtx | null>
  editTableRef: React.RefObject<HTMLDivElement | null>
  readingHtml: string
  onTableClick: (e: React.MouseEvent<HTMLDivElement>) => void
  onTableFocus: (e: React.FocusEvent<HTMLDivElement>) => void
  onTableInput: () => void
  onReadingClick: (e: React.MouseEvent<HTMLDivElement>) => void
}

export function PdfReadingPane({
  readingMode,
  srcCtx,
  editTableRef,
  readingHtml,
  onTableClick,
  onTableFocus,
  onTableInput,
  onReadingClick,
}: PdfReadingPaneProps) {
  if (readingMode === 'table') {
    const html = makeEditableHtml(srcCtx.current?.correctionText || srcCtx.current?.tableHtml || '')
    return (
      <div
        className="pdf-table-wrap pdf-editable"
        ref={editTableRef}
        onClick={onTableClick}
        onFocus={onTableFocus}
        onInput={onTableInput}
        onBlur={onTableInput}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    )
  }
  return <div className="pdf-reading-body" onClick={onReadingClick} dangerouslySetInnerHTML={{ __html: readingHtml }} />
}
