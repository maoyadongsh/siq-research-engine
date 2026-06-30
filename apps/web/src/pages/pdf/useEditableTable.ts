/* eslint-disable react-hooks/immutability */
import { useCallback } from 'react'
import type { FocusEvent, MouseEvent, MutableRefObject, RefObject } from 'react'
import type { PdfCtx, SrcCtx } from '../../lib/pdfTypes'
import { saveCorrectionApi } from '../../features/pdf-parsing/api'
import { serializeEditableTable } from '../../lib/pdfSanitize'

export interface UseEditableTableOptions {
  taskIdRef: MutableRefObject<string | null>
  srcCtx: MutableRefObject<SrcCtx | null>
  pdfCtx: MutableRefObject<PdfCtx | null>
  editTableRef: RefObject<HTMLDivElement | null>
  corrTextRef: RefObject<HTMLTextAreaElement | null>
  corrStatusRef: RefObject<HTMLSelectElement | null>
  corrNoteRef: RefObject<HTMLTextAreaElement | null>
  traceCell: (cell: HTMLElement) => { pageNumber: number; bbox: number[]; source: string; confidence: string } | null
  updatePdfViewer: (page: number) => void
  reportError: (msg: string | null) => void
  showToast: (msg: string) => void
}

export function useEditableTable(options: UseEditableTableOptions) {
  const { taskIdRef, srcCtx, pdfCtx, editTableRef, corrTextRef, corrStatusRef, corrNoteRef, traceCell, updatePdfViewer, reportError, showToast } = options

  const syncEditable = useCallback(() => {
    const wrap = editTableRef.current
    if (!wrap) return
    const html = serializeEditableTable(wrap)
    const text = corrTextRef.current
    if (!text) return
    if (html) {
      text.value = html
      if (srcCtx.current) srcCtx.current.correctionText = html
      if (corrStatusRef.current?.value === 'unreviewed') corrStatusRef.current.value = 'fixed'
    }
  }, [editTableRef, corrTextRef, srcCtx, corrStatusRef])

  const saveCorrection = useCallback(async () => {
    const tid = taskIdRef.current
    const idx = srcCtx.current?.selectedTableIndex
    if (!tid || !idx) return
    const html = editTableRef.current ? serializeEditableTable(editTableRef.current) : ''
    const status = corrStatusRef.current?.value || 'unreviewed'
    const note = corrNoteRef.current?.value || ''
    try {
      await saveCorrectionApi(tid, idx, { table_markdown: html, review_status: status, note })
      if (srcCtx.current) srcCtx.current.correctionText = html
      showToast('表格校正已保存')
    } catch (e) {
      reportError((e as Error).message)
    }
  }, [taskIdRef, srcCtx, editTableRef, corrStatusRef, corrNoteRef, showToast, reportError])

  const handleTableClick = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      const cell = (e.target as HTMLElement).closest<HTMLElement>('th, td')
      if (!cell || !srcCtx.current) return
      editTableRef.current?.querySelectorAll('.selected-cell').forEach((node) => node.classList.remove('selected-cell'))
      cell.classList.add('selected-cell')
      const trace = traceCell(cell)
      if (trace && pdfCtx.current) {
        pdfCtx.current.selectedTrace = trace
        updatePdfViewer(trace.pageNumber)
      }
    },
    [srcCtx, editTableRef, pdfCtx, traceCell, updatePdfViewer],
  )

  const handleTableFocus = useCallback(
    (e: FocusEvent<HTMLDivElement>) => {
      const cell = (e.target as HTMLElement).closest<HTMLTableCellElement>('th, td')
      if (cell && srcCtx.current) {
        editTableRef.current?.querySelectorAll('.selected-cell').forEach((node) => node.classList.remove('selected-cell'))
        cell.classList.add('selected-cell')
        const row = cell.parentElement instanceof HTMLTableRowElement ? cell.parentElement : null
        srcCtx.current.selectedCell = { rowIndex: row?.rowIndex ?? -1, cellIndex: cell.cellIndex, text: cell.textContent || '' }
      }
    },
    [srcCtx, editTableRef],
  )

  const handleTableInput = useCallback(() => {
    syncEditable()
  }, [syncEditable])

  return {
    syncEditable,
    saveCorrection,
    handleTableClick,
    handleTableFocus,
    handleTableInput,
  }
}
