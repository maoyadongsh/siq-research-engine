export type DealStatus =
  | 'draft'
  | 'r0_ready'
  | 'r1_in_progress'
  | 'r4_completed'
  | 'archived'
  | 'closed'
  | string

export interface DealSummary {
  deal_id: string
  legacy_project_id?: string | null
  company_name: string
  industry?: string
  stage?: string
  status?: DealStatus
  current_phase?: string | null
  final_decision?: string | null
  final_score?: number | null
  updated_at?: string | null
  package_path?: string
}

export interface DealStats {
  total: number
  active: number
  diligence: number
  highRisk: number
}

export interface DealListResponse {
  deals: DealSummary[]
  stats?: DealStats
}

export interface DealDetailResponse {
  summary: DealSummary
  project_meta: Record<string, unknown>
  manifest: Record<string, unknown>
  workflow: DealWorkflow
}

export interface DealWorkflow {
  schema_version?: string
  deal_id?: string
  legacy_project_id?: string | null
  company_name?: string
  industry?: string
  stage?: string
  status?: string
  current_phase?: string
  phases?: Record<string, Record<string, unknown>>
  final_decision?: string | null
  final_score?: number | null
  updated_at?: string | null
}

export interface DealWorkflowResponse {
  workflow: DealWorkflow
  r1_agent_sequence?: string[]
  agent_reports?: DealAgentReportSummary[]
  startup_receipts?: DealStartupReceiptSummary
  disputes?: DealDisputeSummary[]
  artifact_status?: Record<string, boolean>
}

export interface DealAgentReportSummary {
  agent_id: string
  role?: string
  label?: string
  r1_sequence_index?: number | null
  has_report?: boolean
  has_startup_receipt?: boolean
  score?: number | string | null
  recommendation?: string | null
  confidence?: string | null
  summary?: string | null
  verified_count?: number
  assumed_count?: number
  open_questions?: unknown[]
  risk_flags?: unknown[]
  artifact_path?: string | null
  startup_receipt_id?: string | null
  created_at?: string | null
}

export interface DealStartupReceiptSummary {
  count: number
  agents: string[]
}

export interface DealDisputeSummary {
  dispute_id?: string
  topic?: string | null
  dimension?: string | null
  severity?: string | null
  resolved?: boolean
  position_count?: number
  chairman_ruling?: Record<string, unknown> | null
}

export interface DealPreflightCheck {
  id: string
  label: string
  status: 'pass' | 'warn' | 'fail' | string
  message: string
  details?: Record<string, unknown>
}

export interface DealPreflight {
  deal_id: string
  status: 'pass' | 'warn' | 'fail' | string
  policy_version?: string | null
  counts?: Record<string, number>
  checks: DealPreflightCheck[]
}

export interface DealPreflightResponse {
  preflight: DealPreflight
}

export interface DealDecisionResponse {
  decision: Record<string, unknown>
  report_markdown?: string
  report_path?: string | null
}

export interface DealAuditEvent {
  event_type?: string
  created_at?: string
  [key: string]: unknown
}

export interface DealAuditResponse {
  audit: {
    events?: DealAuditEvent[]
    [key: string]: unknown
  }
}

export interface DealReportMeta {
  path: string
  title?: string | null
  category?: string | null
  format?: string | null
  status?: string | null
  size_bytes?: number | null
  sha256?: string | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface DealReportsResponse {
  schema_version?: string
  deal_id?: string
  generated_at?: string | null
  counts?: Record<string, number>
  available_categories?: string[]
  reports: DealReportMeta[]
  missing_expected?: DealReportMeta[]
}

export interface DealReportDetailResponse {
  schema_version?: string
  deal_id?: string
  report: DealReportMeta
  content?: string
  json?: unknown
  rows_preview?: unknown[]
  invalid_lines?: number | null
  parse_error?: string | null
}

export interface DealEvidenceItem {
  evidence_id: string
  deal_id: string
  document_id: string
  claim: string
  evidence_type: string
  dimension: string
  source_path: string
  source_url?: string | null
  artifact_url?: string | null
  parser_page_url?: string | null
  source_anchor?: string | Record<string, unknown> | null
  citation?: string | null
  confidence?: number | string | null
  quote?: string | null
  locator?: string | null
  role_hints?: string[] | null
  created_at?: string | null
}

export interface DealEvidenceQualityReport {
  status?: string | null
  item_count?: number
  verified_count?: number
  dimensions?: string[]
  missing_dimensions?: string[]
  warnings?: string[]
  counts?: Record<string, number>
  documents?: Record<string, unknown>[]
}

export interface DealEvidenceFilters {
  q?: string | null
  dimension?: string | null
  document_id?: string | null
  source_url?: string | null
  limit?: number | string | null
}

export interface DealEvidenceAvailableFilters {
  dimensions?: string[]
  document_ids?: string[]
  documents?: Array<{
    document_id?: string | null
    filename?: string | null
    original_filename?: string | null
    title?: string | null
    label?: string | null
    [key: string]: unknown
  }>
  limits?: Array<number | string>
  [key: string]: unknown
}

export interface DealEvidenceResponse {
  evidence_index: Record<string, unknown>
  quality_report: DealEvidenceQualityReport
  items_preview: DealEvidenceItem[]
  matched_count?: number
  total_item_count?: number
  applied_filters?: DealEvidenceFilters
  available_filters?: DealEvidenceAvailableFilters
}

export interface DealEvidenceIngestDryRun {
  schema_version?: string | null
  status?: string | null
  counts?: Record<string, number | string | boolean | null | undefined>
  postgres_written?: boolean
  milvus_written?: boolean
  target_postgres?: unknown
  target_milvus?: unknown
  errors?: unknown[]
  warnings?: unknown[]
  postgres_rows_preview?: unknown[]
  milvus_chunks_preview?: unknown[]
  [key: string]: unknown
}

export interface DealEvidenceIngestDryRunResponse {
  ingest_dry_run: DealEvidenceIngestDryRun
}

export interface DealDocument {
  document_id: string
  deal_id: string
  filename: string
  original_filename?: string | null
  content_type?: string | null
  size_bytes?: number | null
  sha256?: string | null
  document_type?: string | null
  source_note?: string | null
  storage_path?: string | null
  status?: string | null
  parse_task_id?: string | null
  parsed_artifact_path?: string | null
  parser_status_url?: string | null
  parser_result_url?: string | null
  parser_page_url?: string | null
  parse_bound_at?: string | null
  created_at?: string | null
  created_by?: { id?: number | string | null; username?: string | null } | null
}

export interface DealDocumentsResponse {
  documents: DealDocument[]
}

export interface DealDocumentResponse {
  document: DealDocument
}

export interface DeleteDealDocumentResponse {
  ok: true
  document_id: string
}

export interface DealQuery {
  q?: string
  status?: string
}

export interface OpenClawImportPayload {
  deal_id: string
  project_id?: string
  source_root?: string
  overwrite?: boolean
  metadata?: Record<string, unknown>
}

export interface OpenClawImportOptions {
  wait?: boolean
}

export interface DealJobStatus {
  ok?: boolean
  queued?: boolean
  job_id?: string
  status?: string
  kind?: string
  deal_id?: string
  project_id?: string
  result?: unknown
  error?: unknown
  message?: string
  detail?: string
  [key: string]: unknown
}

export type OpenClawImportResponse = DealJobStatus
