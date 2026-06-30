export type EnhancedTableStructure = {
  expanded_rows?: number
  expanded_columns?: number
  header_row_count?: number
  has_colspan?: boolean
  has_rowspan?: boolean
  multi_level_header_candidate?: boolean
  header_preview?: string[]
}

export type EnhancedTable = {
  table_id?: string
  table_index?: number
  content_table_source_id?: number
  line?: number
  source?: string
  confidence?: string
  pdf_page_index?: number
  pdf_page_number?: number
  printed_page_number?: string
  bbox?: number[]
  source_image_path?: string
  source_caption?: string[]
  source_footnote?: string[]
  rows?: number
  cells?: number
  structure?: EnhancedTableStructure
  preview?: string
  report_year?: number | string
  heading?: string
  unit?: string
  matched_financial_names?: string[]
  missing_body?: boolean
}

export type TableRelationCandidate = {
  relationType: 'continuation' | 'candidate_continuation'
  confidence: number
  reasons: string[]
  fromTable: EnhancedTable
  toTable: EnhancedTable
  pageNumbers: [number, number]
}

export type PagePlanEntry = {
  pageNumber: number
  focusTableIndex?: number
  relation?: TableRelationCandidate
}

export type PageOverlayEntry = {
  tableIndex?: number
  blockId?: string
  blockType?: string
  pageNumber?: number
  bbox: number[]
  label: string
  detail: string
  tone: 'focused' | 'table' | 'trace' | 'block'
  source: 'table' | 'trace' | 'block'
}

export function blockFocusKey(page: number, blockId: string) {
  return `${page}:${blockId}`
}
