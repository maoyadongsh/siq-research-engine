export interface LogEntry {
  time: string
  level: string
  message: string
}

export interface BboxExtent {
  width: number
  height: number
}

export interface SelectedTrace {
  pageNumber: number
  bbox: number[]
  source: string
  confidence: string
}

export interface PdfCtx {
  sourcePage: number
  currentPage: number
  pageCount: number
  bbox: number[]
  bboxExtent: BboxExtent
  selectedTrace: SelectedTrace | null
}

export interface SelectedCell {
  rowIndex: number
  cellIndex: number
  text: string
}

export interface SrcCtx {
  selectedTableIndex: number
  sourcePage: number
  readingMode: 'table' | 'page'
  tableHtml: string
  correctionText: string
  selectedCell: SelectedCell | null
  pageCache: Record<number, PageContent>
}

export interface DownloadedPdf {
  id: string
  market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP' | 'DOC'
  company: string
  companyName?: string
  ticker?: string | null
  category: string
  filename: string
  relativePath: string
  size: number
  mtime: string
  url: string
  contentType?: string
  isPdf?: boolean
  form?: string | null
  reportType?: string | null
  reportFamily?: string | null
  reportEnd?: string | null
  publishedAt?: string | null
  metadataPath?: string | null
  accessionNumber?: string | null
  sourceId?: string | null
  downloadedFile?: {
    file_name?: string
    saved_path?: string
    size_bytes?: number
    content_type?: string | null
    content_sha256?: string | null
  } | null
}

export interface HealthStatus {
  mineru: boolean
  vlm: boolean
  submit_ready: boolean
  warning?: string
}

export interface ArtifactInfo {
  exists?: boolean
  path?: string
  url?: string
}

export type ArtifactsMap = Record<string, ArtifactInfo>

export interface WorkflowArtifactBundle {
  status?: string
  ready?: boolean
  readyCount?: number
  total?: number
  missing?: string[]
  artifacts?: ArtifactsMap
  message?: string
}

export interface WorkflowStatus {
  artifactBundle?: WorkflowArtifactBundle
  documentFull?: { status?: string }
  wiki?: { status?: string; companyDir?: string; message?: string }
  semantic?: {
    status?: string
    counts?: { facts?: number; evidence?: number }
    llm?: { status?: string; counts?: { claims?: number; risks?: number }; message?: string }
    message?: string
    reportId?: string
  }
  database?: {
    status?: string
    statementItems?: number
    tables?: number
    message?: string
  }
  preflight?: { checks?: Array<{ id: string; label: string; status: string; ok?: boolean; blocking?: boolean; message?: string }> }
  error?: string
}

export interface WorkflowJob {
  jobId?: string
  status?: string
  steps?: Array<{ step: string; status: string; message?: string; error?: string }>
  error?: string
}

export interface QualityReport {
  market_profile?: string
  market?: string
  accounting_standard?: string
  industry_profile?: string
  detected_currencies?: string[]
  currency?: string
  unit?: string
  table_count?: number
  single_row_table_count?: number
  single_row_table_ratio?: number
  image_ref_count?: number
  suspicious_tables?: Array<Record<string, unknown>>
  found_sections?: string[]
  missing_sections?: string[]
  core_financial_table_candidates?: Array<Record<string, unknown>>
  key_table_candidates?: Record<string, Array<Record<string, unknown>>>
  hk_key_table_candidates?: Record<string, Array<Record<string, unknown>>>
  indicator_table_candidates?: Array<Record<string, unknown>>
  found_financial_tables?: string[]
  warnings?: string[]
}

export interface FinancialData {
  market?: string
  market_profile?: string
  accounting_standard?: string
  industry_profile?: string
  report_year?: string
  currency?: string
  unit?: string
  detected_currencies?: string[]
  summary?: { statement_count?: number; key_metric_count?: number; operating_metric_count?: number; scopes?: string[] }
  statements?: unknown[]
  key_metrics?: unknown[]
  operating_metrics?: unknown[]
}

export interface FinancialChecks {
  market?: string
  market_profile?: string
  accounting_standard?: string
  currency?: string
  unit?: string
  detected_currencies?: string[]
  overall_status?: string
  summary?: { total?: number; pass?: number; fail?: number; warning?: number; skipped?: number }
  checks?: Array<Record<string, unknown>>
  warnings?: string[]
}

export interface FinancialResult {
  financial_checks?: FinancialChecks
  financial_data?: FinancialData
}

export interface TaskItem {
  task_id: string
  filename?: string
  status?: string
  created_at?: string
  local_queue_position?: number
  markdown_ready?: boolean
  market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP' | 'DOC'
  submit_config?: {
    market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP' | 'DOC'
    [key: string]: unknown
  }
}

export interface SourceTable {
  table_index?: number
  line?: number
  rows?: number
  cells?: number
  pdf_page_number?: number
  pdf_page_source?: string
  empty_ratio?: number
  numeric_ratio?: number
  heading?: string
  unit?: string
  matched_financial_names?: string[]
  bbox?: number[]
  source_image_path?: string
  table_html?: string
}

export interface SourceCorrection {
  review_status?: string
  table_markdown?: string
  note?: string
  updated_at?: string
}

export interface SourceMeta {
  table: SourceTable
  correction: SourceCorrection
  excerpt: Array<{ line?: number; text?: string; focus?: boolean }>
  artifacts: ArtifactsMap
  pdfPageImage?: {
    url?: string
    page_number?: number
    page_count?: number
    printed_page_number?: string
    bbox?: number[]
    bbox_extent?: BboxExtent
  }
}

export interface PageTable {
  table_index?: number
  source_table_index?: number
  line?: number
  heading?: string
  printed_page_number?: string
  matched_financial_names?: string[]
  is_focus_table?: boolean
}

export interface PageBlock {
  block_id?: string
  type?: string
  bbox?: number[] | string
  bbox_unit?: string
  page_number?: number
  pdf_page_number?: number
  table_index?: number
  source_table_index?: number
  sub_type?: string
  heading?: string[] | string
  caption?: string[] | string
  footnote?: string[] | string
  matched_financial_names?: string[]
  table_html?: string
  is_focus_table?: boolean
  missing_body?: boolean
  list_items?: string[]
  image_path?: string
  text_level?: number
  text?: string
  markdown?: string
  line?: number
  reading_order?: number
  printed_page_number?: string
  raw?: unknown
}

export interface PageContent {
  page_number?: number
  pdf_page_number?: number
  printed_page_number?: string
  page_index?: number
  block_count?: number
  table_count?: number
  page_tables?: PageTable[]
  blocks?: PageBlock[]
}

export const WIKI_INPUT_ARTIFACTS = [
  'result.md',
  'result_complete.md',
  'document_full.json',
  'content_list_enhanced.json',
  'financial_data.json',
  'financial_checks.json',
  'quality_report.json',
  'table_relations.json',
  'table_index.json',
]

export const artifactRoles: Record<string, string> = {
  'result.md': '原始 Markdown 文本',
  'result_complete.md': '增强 Markdown，含结构补充',
  'document_full.json': '总包索引，供 Wiki 与入库读取',
  'content_list_enhanced.json': '增强结构块与页码信息',
  'quality_report.json': '解析质量与表格索引来源',
  'table_relations.json': '跨页断表关系与合并候选',
  'table_index.json': '表格定位与溯源索引',
  'financial_data.json': '规则抽取的财务指标',
  'financial_checks.json': '财务抽取校验结果',
  'middle.json': 'MinerU 中间结构',
  'content_list.json': 'MinerU 原始内容块',
  'model_output.json': '模型输出原始结构',
  images: '页面图片与视觉溯源素材',
}
