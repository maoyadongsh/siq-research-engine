export interface DocumentLogEntry {
  id?: number
  time: string
  level: string
  message: string
}

export interface DocumentTaskItem {
  task_id: string
  taskId?: string
  filename?: string
  document_kind?: string
  documentKind?: string
  source_type?: string
  source_url?: string
  status?: string
  stage?: string
  progress_percent?: number
  file_size?: number
  parser_provider?: string
  quality_status?: string
  upstream_task_id?: string
  upstream_status?: string
  queue_position?: number
  local_queue_position?: number
  elapsed_seconds?: number
  total_pages?: number
  processed_pages?: number
  markdown_ready?: boolean
  created_at?: string
  updated_at?: string
  completed_at?: string
  error?: string
}

export interface DocumentArtifactInfo {
  exists?: boolean
  path?: string
  url?: string
  size?: number
}

export type DocumentArtifactsMap = Record<string, DocumentArtifactInfo>

export interface DocumentManifest {
  schema_version?: string
  task_id?: string
  filename?: string
  original_extension?: string
  mime_type?: string
  source_type?: string
  source_url?: string
  file_size?: number
  file_sha256?: string
  document_kind?: string
  parser_provider?: string
  parser_version?: string
  parse_config?: Record<string, unknown>
  status?: string
  quality_status?: string
  created_at?: string
  completed_at?: string
}

export interface DocumentResult {
  task?: DocumentTaskItem
  manifest?: DocumentManifest
  markdown?: string
  artifacts?: DocumentArtifactsMap
}

export interface DocumentQualityReport {
  overall_status?: string
  document_kind?: string
  page_count?: number
  block_count?: number
  table_count?: number
  image_count?: number
  equation_count?: number
  ocr_used?: boolean
  coverage?: Record<string, number>
  image_quality?: Record<string, number>
  warnings?: Array<{ code?: string; severity?: string; message?: string; page_number?: number }>
  ready_for_knowledge_base?: boolean
}

export interface DocumentBlock {
  block_id: string
  type?: string
  sub_type?: string
  text?: string
  markdown?: string
  page_number?: number
  bbox?: number[]
  bbox_unit?: string
  source_ref?: { evidence_id?: string; source_type?: string; path?: string }
}

export interface DocumentLayoutPage {
  page_number?: number
  page_index?: number
  width?: number
  height?: number
  page_size?: number[]
  bbox_unit?: string
  metadata_source?: string
}

export interface DocumentLayoutBlocksPayload {
  schema_version?: string
  task_id?: string
  pages?: DocumentLayoutPage[]
}

export interface DocumentBlocksPayload {
  schema_version?: string
  task_id?: string
  blocks?: DocumentBlock[]
}

export interface DocumentTable {
  table_id?: string
  block_id?: string
  title?: string
  caption?: string
  page_number?: number
  bbox?: number[]
  bbox_unit?: string
  sheet_name?: string
  html?: string
  markdown?: string
  quality?: { row_count?: number; column_count?: number; empty_cell_ratio?: number }
}

export interface DocumentTablesPayload {
  schema_version?: string
  task_id?: string
  tables?: DocumentTable[]
  physical_tables?: DocumentTable[]
}

export interface DocumentTableRelation {
  relation_id?: string
  id?: string
  source_table_id?: string
  target_table_id?: string
  table_id?: string
  next_table_id?: string
  fragment_table_ids?: string[]
  relation_type?: string
  merge_status?: string
  confidence?: number
  merge_confidence?: number
  reasons?: string[]
  merge_reasons?: string[]
  review_status?: string
  note?: string
  page_numbers?: number[]
  visual_connector?: {
    from_page?: number
    to_page?: number
    from_anchor?: number[]
    to_anchor?: number[]
  }
}

export interface DocumentTableRelationsPayload {
  schema_version?: string
  task_id?: string
  relations?: DocumentTableRelation[]
}

export interface DocumentFigure {
  image_id?: string
  block_id?: string
  type?: string
  page_number?: number
  bbox?: number[]
  bbox_unit?: string
  image_path?: string
  crop_path?: string
  thumbnail_path?: string
  caption?: string
  nearby_heading?: string
  ocr_text?: string
  alt_text?: string
  evidence_id?: string
  quality?: Record<string, unknown>
}

export interface DocumentFiguresPayload {
  schema_version?: string
  task_id?: string
  figures?: DocumentFigure[]
}

export interface DocumentSourceMapEntry {
  evidence_id?: string
  source_type?: string
  artifact?: string
  block_id?: string
  table_id?: string
  image_id?: string
  page_number?: number
  bbox?: number[]
  quote?: string
  open_source_url?: string
  open_artifact_url?: string
}

export interface DocumentSourceMapPayload {
  schema_version?: string
  task_id?: string
  sources?: DocumentSourceMapEntry[]
}

export interface DocumentExtractionTemplate {
  template_id: string
  name?: string
  description?: string
  instructions?: string
  schema?: Record<string, unknown>
}

export interface DocumentExtractionTemplatesPayload {
  schema_version?: string
  templates?: DocumentExtractionTemplate[]
}

export interface DocumentParseConfig {
  modelVersion: string
  ocr: string
  enableFormula: boolean
  enableTable: boolean
  language: string
  pageRanges: string
  extraFormats: string[]
  noCache: boolean
}

export interface DocumentMineruImportCandidate {
  source_dir?: string
  sourceDir?: string
  title?: string
  result_markdown?: string
  has_content_list?: boolean
  has_middle?: boolean
  updated_at?: number
}

export interface DocumentMineruImportCandidatesPayload {
  schema_version?: string
  allowed_roots?: string[]
  candidates?: DocumentMineruImportCandidate[]
}

export interface DocumentWorkflowTarget {
  status?: string
  message?: string
  path?: string
  manifestPath?: string
  collection?: string
  documentKey?: string
  schema?: string
  collectionName?: string
  script?: string
  packagePath?: string
  chunksPath?: string
  chunkCount?: number
  insertedCount?: number
  reportPath?: string
  batchTag?: string
  document_id?: string
  documentFullSha256?: string
  sourceDocumentFullSha256?: string
  stale?: boolean
}

export interface DocumentWorkflowStatus {
  taskId?: string
  artifacts?: {
    status?: string
    ready?: boolean
    readyCount?: number
    total?: number
    missing?: string[]
    message?: string
  }
  targets?: {
    wiki?: DocumentWorkflowTarget
    postgres?: DocumentWorkflowTarget
    milvus?: DocumentWorkflowTarget
    full_text?: DocumentWorkflowTarget
    object_storage?: DocumentWorkflowTarget
  }
}

export interface DocumentWikiImportResult {
  ok?: boolean
  taskId?: string
  collection?: string
  documentKey?: string
  packageDir?: string
  manifestPath?: string
  copiedFiles?: string[]
  copiedDirectories?: Record<string, number>
  wiki?: DocumentWorkflowTarget
  postgres?: DocumentWorkflowTarget
  milvus?: DocumentWorkflowTarget
  semanticMode?: 'chunks' | 'milvus'
  result?: Record<string, unknown>
}
