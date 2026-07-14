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
  pagination?: { page: number; page_size: number; total: number; has_more: boolean }
  status_summary?: { by_status?: Record<string, number> }
  status_summaries?: Record<string, DealStatusResponse>
}

export interface DealDetailResponse {
  summary: DealSummary
  project_meta: Record<string, unknown>
  manifest: Record<string, unknown>
  workflow: DealWorkflow
}

export interface DealStatusComponent {
  id: string
  label?: string
  status?: 'pass' | 'warn' | 'fail' | 'missing' | string
  blocking?: boolean
  message?: string
  href?: string | null
  metrics?: Record<string, unknown>
  warnings?: string[]
  [key: string]: unknown
}

export interface DealStatusSummary {
  schema_version?: 'siq_deal_status_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  status?: 'pass' | 'warn' | 'fail' | 'missing' | string
  ready_for_next_action?: boolean
  next_action?: string | null
  counts?: {
    components?: number
    pass?: number
    warn?: number
    fail?: number
    missing?: number
    blocking?: number
    [key: string]: unknown
  }
  components?: DealStatusComponent[]
  sources?: Record<string, unknown>
  [key: string]: unknown
}

export type DealStatusResponse = DealStatusSummary

export type DealAgentStatus = 'ready' | 'blocked' | 'missing_report' | 'non_r1' | string

export interface DealAgentRuntimeSummary {
  enabled?: boolean
  port?: number
  base_url?: string
  model_name?: string
  [key: string]: unknown
}

export interface DealAgentReadinessSummary {
  allowed?: boolean
  would_queue?: boolean
  blocking_reasons?: string[]
  warnings?: string[]
  has_report?: boolean
  has_startup_receipt?: boolean
  startup_receipt_id?: string | null
  preflight_status?: string | null
  [key: string]: unknown
}

export interface DealAgentReportObservability {
  status?: string
  score?: number | string | null
  recommendation?: string | null
  artifact_path?: string | null
  artifact_available?: boolean
  [key: string]: unknown
}

export interface DealAgentReceiptSummary {
  receipt_id?: string | null
  present?: boolean
  [key: string]: unknown
}

export interface DealAgentSummary {
  agent_id: string
  role?: string | null
  label?: string | null
  profile_path?: string | null
  r1_sequence_index?: number | null
  is_r1_agent?: boolean
  runtime?: DealAgentRuntimeSummary
  readiness?: DealAgentReadinessSummary
  report?: DealAgentReportObservability
  receipt?: DealAgentReceiptSummary
  status?: DealAgentStatus
  [key: string]: unknown
}

export interface DealAgentsCounts {
  agents?: number
  r1_agents?: number
  ready?: number
  blocked?: number
  reports?: number
  receipts?: number
  runtime_enabled?: number
  [key: string]: unknown
}

export interface DealAgentsResponse {
  schema_version?: 'siq_deal_agents_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  counts?: DealAgentsCounts
  agents?: DealAgentSummary[]
  [key: string]: unknown
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
  r1_agent_readiness?: DealR1AgentReadiness
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

export interface DealStartupReceipt {
  receipt_id?: string
  agent_id?: string
  legacy_agent_id?: string | null
  round_name?: string
  query?: string
  project_tag?: string
  retrieval_mode?: string
  shared_hits?: number
  private_hits?: number
  project_evidence_hits?: number
  background_knowledge_hits?: number
  collections?: string[]
  physical_collections?: string[]
  shared_collection?: string | null
  private_collection?: string | null
  retrieval_status?: string | null
  degraded_reasons?: string[]
  blocking_reasons?: string[]
  evidence_snapshot_hash?: string | null
  capability_restrictions?: string[]
  stale?: boolean
  evidence_hits?: Array<Record<string, unknown>>
  evidence_hit_count?: number
  dimensions?: string[]
  workspace_rules_read?: string[]
  gaps?: string[]
  milvus_used?: boolean
  postgres_used?: boolean
  hermes_used?: boolean
  created_at?: string | null
  created_by?: { id?: number | string | null; username?: string | null } | null
  [key: string]: unknown
}

export interface DealStartupRetrievalResponse {
  deal_id?: string
  agent_id?: string
  receipt: DealStartupReceipt | null
}

export interface DealAgentTaskDryRunResponse {
  schema_version?: string
  deal_id?: string
  agent_id?: string
  round_name?: string
  allowed?: boolean
  blocking_reasons?: string[]
  warnings?: string[]
  preflight_status?: string | null
  receipt?: DealStartupReceipt | null
  payload?: Record<string, unknown>
  dry_run?: boolean
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
  [key: string]: unknown
}

export interface DealWorkflowRunR1AgentDryRunResponse extends DealAgentTaskDryRunResponse {
  workflow_action?: string
  queued?: boolean
  job_id?: string | null
  would_queue?: boolean
  agent_task?: DealAgentTaskDryRunResponse
}

export interface DealWorkflowRunR1SerialAgent {
  agent_id?: string
  action?: string
  would_run?: boolean
  submitted?: boolean
  has_startup_receipt?: boolean
  startup_receipt_id?: string | null
  blocking_reasons?: string[]
  warnings?: string[]
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
  [key: string]: unknown
}

export interface DealWorkflowRunR1SerialResponse {
  schema_version?: string
  deal_id?: string
  round_name?: string
  workflow_action?: 'run-r1-serial' | string
  dry_run?: boolean
  allowed?: boolean
  would_run?: boolean
  queued?: boolean
  job_id?: string | null
  planned_agent_ids?: string[]
  executed_agent_ids?: string[]
  submitted_agent_ids?: string[]
  next_agent_id?: string | null
  stop_reason?: string | null
  blocking_reasons?: string[]
  warnings?: string[]
  agents?: DealWorkflowRunR1SerialAgent[]
  agent_runs?: DealWorkflowRunR1AgentDryRunResponse[]
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
  [key: string]: unknown
}

export type DealWorkflowExecutionMode = 'model' | 'deterministic_fallback'

export interface DealWorkflowRunR15ChairmanRequest {
  dry_run?: boolean
  mode?: DealWorkflowExecutionMode
  overwrite?: boolean
  timeout?: number | null
  expected_evidence_snapshot_hash?: string | null
}

export interface DealWorkflowRunR2Request {
  dry_run?: boolean
  mode?: DealWorkflowExecutionMode
  timeout?: number | null
  expected_evidence_snapshot_hash?: string | null
}

export interface DealWorkflowRunR3Request {
  dry_run?: boolean
  mode?: DealWorkflowExecutionMode
  skip?: boolean
  skip_reason?: string | null
  timeout?: number | null
  expected_evidence_snapshot_hash?: string | null
}

export interface DealWorkflowFinalizeR4Request {
  dry_run?: boolean
  mode?: DealWorkflowExecutionMode
  overwrite?: boolean
  timeout?: number | null
  expected_evidence_snapshot_hash?: string | null
}

export interface DealWorkflowPhaseRunResponse {
  schema_version?: string
  deal_id?: string
  workflow_action?: 'run-r2' | 'run-r3' | 'finalize-r4' | string
  dry_run?: boolean
  allowed?: boolean
  would_write?: boolean
  written?: boolean
  overwrite?: boolean
  queued?: boolean
  job_id?: string | null
  blocking_reasons?: string[]
  warnings?: string[]
  counts?: Record<string, unknown>
  output_paths?: Record<string, string | null | undefined>
  mode?: string | null
  skip_reason?: string | null
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
  workflow?: DealWorkflow | null
  audit_event?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface DealWorkflowRunR2Response extends DealWorkflowPhaseRunResponse {
  reports_preview?: Record<string, unknown>
  reports?: Record<string, unknown>
}

export interface DealWorkflowRunR15ChairmanResponse extends DealWorkflowPhaseRunResponse {
  phase?: 'R1.5' | string
  chairman_task?: Record<string, unknown>
  generation_mode?: string
  fallback?: boolean
}

export interface DealWorkflowRunR3Response extends DealWorkflowPhaseRunResponse {
  payload_preview?: Record<string, unknown>
  payload?: Record<string, unknown>
}

export interface DealWorkflowFinalizeR4Response extends DealWorkflowPhaseRunResponse {
  decision_preview?: Record<string, unknown>
  decision?: Record<string, unknown>
}

export interface DealR1AgentReadinessItem {
  agent_id: string
  role?: string
  label?: string
  r1_sequence_index?: number | null
  round_name?: string
  allowed?: boolean
  would_queue?: boolean
  blocking_reasons?: string[]
  warnings?: string[]
  preflight_status?: string | null
  has_startup_receipt?: boolean
  startup_receipt_id?: string | null
  has_report?: boolean
  submitted?: boolean
  dry_run?: boolean
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
  [key: string]: unknown
}

export interface DealR1AgentReadiness {
  schema_version?: string
  deal_id?: string
  round_name?: string
  workflow_action?: string
  dry_run?: boolean
  current_phase?: string | null
  workflow_status?: string | null
  preflight_status?: string | null
  next_agent_id?: string | null
  ready_count?: number
  blocked_count?: number
  agents?: DealR1AgentReadinessItem[]
  hermes_called?: boolean
  report_written?: boolean
  workflow_advanced?: boolean
}

export interface DealDisputeSummary {
  dispute_id?: string
  topic?: string | null
  dimension?: string | null
  severity?: string | null
  resolved?: boolean
  position_count?: number
  agent_ids?: string[]
  evidence_ids?: string[]
  chairman_ruling?: Record<string, unknown> | null
  required_followups?: string[]
  [key: string]: unknown
}

export interface DealDisputesCounts {
  disputes?: number
  resolved?: number
  unresolved?: number
  positions?: number
  rulings?: number
  high_severity?: number
  artifacts?: number
  [key: string]: unknown
}

export interface DealDisputeArtifact {
  path?: string | null
  available?: boolean
  [key: string]: unknown
}

export interface DealDisputesArtifacts {
  json?: DealDisputeArtifact | null
  markdown?: DealDisputeArtifact | null
  [key: string]: unknown
}

export interface DealDisputesResponse {
  schema_version?: 'siq_deal_r1_5_disputes_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  status?: 'pass' | 'warn' | 'missing' | string
  counts?: DealDisputesCounts
  artifacts?: DealDisputesArtifacts
  disputes?: DealDisputeSummary[]
  warnings?: string[]
  [key: string]: unknown
}

export interface DealWorkflowIdentifyDisputesRequest {
  dry_run?: boolean
  preserve_rulings?: boolean
}

export interface DealWorkflowIdentifyDisputesResponse {
  schema_version?: 'siq_deal_r1_5_disputes_identification_v1' | string
  deal_id?: string
  dry_run?: boolean
  would_write?: boolean
  written?: boolean
  preserve_rulings?: boolean
  preserved_ruling_count?: number
  json_path?: string | null
  markdown_path?: string | null
  dispute_count?: number
  warnings?: string[]
  payload?: {
    schema_version?: string
    deal_id?: string
    phase?: string
    disputes?: DealDisputeSummary[]
    warnings?: string[]
    [key: string]: unknown
  } | null
  summary?: DealDisputesResponse | null
  [key: string]: unknown
}

export interface DealWorkflowDisputeRulingRequest {
  decision: string
  rationale?: string
  required_followups?: string[]
  evidence_ids?: string[]
  resolved?: boolean
  overwrite?: boolean
  dry_run?: boolean
}

export interface DealWorkflowDisputeRulingResponse {
  schema_version?: 'siq_deal_r1_5_dispute_ruling_response_v1' | string
  deal_id?: string
  dispute_id?: string
  dry_run?: boolean
  would_write?: boolean
  written?: boolean
  json_path?: string | null
  markdown_path?: string | null
  overwrite?: boolean
  ruling?: Record<string, unknown> | null
  dispute?: DealDisputeSummary | null
  payload?: {
    schema_version?: string
    deal_id?: string
    disputes?: DealDisputeSummary[]
    [key: string]: unknown
  } | null
  summary?: DealDisputesResponse | null
  [key: string]: unknown
}

export interface DealWorkflowGenerateDisputeRulingsRequest {
  dry_run?: boolean
  overwrite?: boolean
}

export interface DealWorkflowGenerateDisputeRulingsResponse {
  schema_version?: 'siq_deal_r1_5_dispute_ruling_generation_v1' | string
  deal_id?: string
  dry_run?: boolean
  would_write?: boolean
  written?: boolean
  overwrite?: boolean
  generation_mode?: string
  json_path?: string | null
  markdown_path?: string | null
  generated_count?: number
  skipped_count?: number
  warnings?: string[]
  skipped?: Array<Record<string, unknown>>
  rulings?: Array<Record<string, unknown>>
  payload?: {
    schema_version?: string
    deal_id?: string
    disputes?: DealDisputeSummary[]
    [key: string]: unknown
  } | null
  summary?: DealDisputesResponse | null
  [key: string]: unknown
}

export interface DealPhaseArtifact {
  path?: string | null
  available?: boolean
  [key: string]: unknown
}

export interface DealPhaseArtifactFiles {
  json?: DealPhaseArtifact | null
  markdown?: DealPhaseArtifact | null
  [key: string]: unknown
}

export interface DealPhaseArtifactCounts {
  items?: number
  warnings?: number
  [key: string]: unknown
}

export interface DealPhaseArtifactItemPreview {
  agent_id?: string | null
  summary?: string | null
  recommendation?: string | null
  score?: number | string | null
  [key: string]: unknown
}

export interface DealPhaseArtifactPhase {
  phase?: 'R0' | 'R1' | 'R1.5' | 'R2' | 'R3' | 'R4' | string
  label?: string | null
  status?: 'pass' | 'warn' | 'missing' | string
  blocking?: boolean
  mode?: 'normal' | 'skip' | 'unknown' | string
  skip_reason?: string | null
  artifacts?: DealPhaseArtifactFiles
  counts?: DealPhaseArtifactCounts
  items_preview?: DealPhaseArtifactItemPreview[]
  warnings?: string[]
  [key: string]: unknown
}

export interface DealPhaseArtifactsCounts {
  phases?: number
  pass?: number
  warn?: number
  missing?: number
  available_json?: number
  available_markdown?: number
  items?: number
  blocking?: number
  [key: string]: unknown
}

export interface DealPhaseArtifactsResponse {
  schema_version?: 'siq_deal_phase_artifacts_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  status?: 'pass' | 'warn' | 'missing' | string
  counts?: DealPhaseArtifactsCounts
  phases?: DealPhaseArtifactPhase[]
  warnings?: string[]
  [key: string]: unknown
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

export type DealDecisionContractStatus = 'pass' | 'warn' | 'missing' | string

export interface DealDecisionContractScoring {
  weighted_agent_score?: number | string | null
  chairman_dimension_score?: number | string | null
  final_score?: number | string | null
  [key: string]: unknown
}

export interface DealDecisionContractDecision {
  value?: string | null
  qualitative?: string | null
  [key: string]: unknown
}

export interface DealDecisionHumanConfirmation {
  status?: string | null
  confirmed?: boolean
  confirmed_at?: string | null
  confirmed_by?: Record<string, unknown> | null
  attestation_schema_version?: 'siq_ic_human_confirmation_attestation_v1' | string | null
  report_id?: string | null
  report_revision?: number | null
  workflow_run_id?: string | null
  evidence_snapshot_hash?: string | null
  decision_sha256?: string | null
  quality_sha256?: string | null
  factcheck_sha256?: string | null
  override_reason?: string | null
  override_decision?: string | null
  override_score?: number | string | null
  [key: string]: unknown
}

export interface DealDecisionHumanConfirmationPayload {
  status: string
  override_reason?: string | null
  override_decision?: string | null
  override_score?: number | string | null
  dry_run?: boolean
  [key: string]: unknown
}

export interface DealDecisionHumanConfirmationUpdateResponse {
  schema_version?: 'siq_deal_r4_human_confirmation_update_v2' | string
  deal_id?: string
  dry_run?: boolean
  would_write?: boolean
  decision_path?: string | null
  previous_human_confirmation?: DealDecisionHumanConfirmation | null
  human_confirmation?: DealDecisionHumanConfirmation | null
  decision_contract?: DealDecisionContract | null
  [key: string]: unknown
}

export interface DealDecisionContractArtifact {
  path?: string | null
  exists?: boolean | null
  available?: boolean | null
  size_bytes?: number | null
  sha256?: string | null
  [key: string]: unknown
}

export interface DealDecisionContractArtifacts {
  markdown?: DealDecisionContractArtifact | null
  html?: DealDecisionContractArtifact | null
  raw?: unknown
  [key: string]: unknown
}

export interface DealDecisionContract {
  schema_version?: 'siq_deal_r4_decision_summary_v1' | string
  deal_id?: string
  status?: DealDecisionContractStatus | null
  missing_required_fields?: string[]
  missing_advisory_fields?: string[]
  scoring?: DealDecisionContractScoring | null
  decision?: DealDecisionContractDecision | null
  human_confirmation?: DealDecisionHumanConfirmation | null
  artifacts?: DealDecisionContractArtifacts | null
  generated_at?: string | null
  [key: string]: unknown
}

export type DealR4GateStatus = 'pass' | 'warn' | 'fail' | string

export interface DealReportQualityCheck {
  id?: string | null
  status?: DealR4GateStatus | null
  message?: string | null
  [key: string]: unknown
}

export interface DealReportQuality {
  schema_version?: 'siq_ic_report_quality_v1' | string
  report_id?: string | null
  report_revision?: number | null
  deal_id?: string | null
  evidence_snapshot_hash?: string | null
  status?: DealR4GateStatus | null
  allowed_for_human_confirmation?: boolean | null
  blocking_reasons?: string[]
  checks?: DealReportQualityCheck[]
  metrics?: Record<string, number | string | null>
  findings?: unknown[]
  warnings?: unknown[]
  [key: string]: unknown
}

export interface DealReportFactcheck {
  schema_version?: 'siq_ic_report_factcheck_v1' | string
  report_id?: string | null
  report_revision?: number | null
  evidence_snapshot_hash?: string | null
  status?: DealR4GateStatus | null
  checked_at?: string | null
  claim_checks?: unknown[]
  numeric_checks?: unknown[]
  citation_checks?: unknown[]
  contradictions?: unknown[]
  unsupported_claims?: unknown[]
  required_repairs?: unknown[]
  findings?: unknown[]
  warnings?: unknown[]
  [key: string]: unknown
}

export interface DealDecisionResponse {
  decision: Record<string, unknown>
  report_markdown?: string
  report_path?: string | null
  contract?: DealDecisionContract | null
  quality?: DealReportQuality | null
  factcheck?: DealReportFactcheck | null
}

export interface DealAuditEvent {
  event_type?: string
  created_at?: string
  [key: string]: unknown
}

export interface DealAuditSourceSummary {
  path?: string
  available?: boolean
  event_count?: number
  size_bytes?: number
  sha256?: string
  updated_at?: string | null
  [key: string]: unknown
}

export interface DealAuditRequiredEventStatus {
  event_type: string
  present?: boolean
  count?: number
  required?: boolean
}

export interface DealAuditSummary {
  schema_version?: 'siq_deal_audit_summary_v1' | string
  deal_id?: string
  status?: 'pass' | 'warn' | 'missing' | string
  generated_at?: string | null
  sources?: {
    primary?: DealAuditSourceSummary
    fallback?: DealAuditSourceSummary
    selected?: 'primary' | 'fallback' | 'none' | string
    consistency?: 'match' | 'mismatch' | 'single_source' | 'missing' | string
    [key: string]: unknown
  }
  counts?: {
    events?: number
    event_types?: Record<string, number>
    human_confirmation?: number
    manual_override?: number
    [key: string]: unknown
  }
  latest_event?: DealAuditEvent | null
  required_event_status?: DealAuditRequiredEventStatus[]
  warnings?: string[]
  [key: string]: unknown
}

export interface DealAuditResponse {
  audit: {
    events?: DealAuditEvent[]
    [key: string]: unknown
  }
  summary?: DealAuditSummary | null
}

export interface DealManifestCounts {
  hashes?: number
  imported_files?: number
  missing_files?: number
  rejected_files?: number
  files_with_hash?: number
  files_missing_hash?: number
  archive_files?: number
  [key: string]: unknown
}

export interface DealManifestOpenClawImport {
  present?: boolean
  legacy_project_id?: string | null
  imported_at?: string | null
  file_count?: number
  metadata_present?: boolean
  [key: string]: unknown
}

export interface DealManifestFileSummary {
  source?: string | null
  target?: string | null
  status?: 'pass' | 'warn' | 'missing' | 'rejected' | string
  sha256?: string | null
  hash_recorded?: boolean
  hash_matches?: boolean | null
  reason?: string | null
  [key: string]: unknown
}

export interface DealManifestArchiveSummary {
  available?: boolean
  path?: string | null
  file_count?: number
  consistency?: 'match' | 'mismatch' | 'missing' | string
  [key: string]: unknown
}

export interface DealManifestSummary {
  schema_version?: 'siq_deal_manifest_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  status?: 'pass' | 'warn' | 'missing' | string
  counts?: DealManifestCounts
  openclaw_import?: DealManifestOpenClawImport
  files?: DealManifestFileSummary[]
  archive_manifest?: DealManifestArchiveSummary
  warnings?: string[]
  [key: string]: unknown
}

export interface DealManifestResponse {
  manifest: Record<string, unknown>
  summary?: DealManifestSummary | null
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

export interface DealR1AgentReportSummary {
  agent_id: string
  role?: string | null
  label?: string | null
  r1_sequence_index?: number | null
  status?: string | null
  has_report?: boolean
  has_startup_receipt?: boolean
  startup_receipt_id?: string | null
  expected_startup_receipt_id?: string | null
  startup_receipt_linkage?: string | null
  score?: number | string | null
  recommendation?: string | null
  confidence?: string | null
  summary?: string | null
  missing_required_fields?: string[]
  missing_advisory_fields?: string[]
  missing_contract_fields?: string[]
  artifact_path?: string | null
  artifact_available?: boolean
  markdown_section_status?: string | null
  missing_markdown_sections?: string[]
  markdown_chars?: number
  [key: string]: unknown
}

export interface DealR1ContractFieldGroup {
  field: string
  aliases: string[]
}

export interface DealR1AgentReportsResponse {
  schema_version?: string
  deal_id?: string
  generated_at?: string | null
  required_fields?: string[]
  advisory_fields?: string[]
  contract_field_groups?: DealR1ContractFieldGroup[]
  required_markdown_sections?: string[]
  counts?: Record<string, number>
  agents?: DealR1AgentReportSummary[]
}

export interface DealR2AgentReportSummary {
  agent_id: string
  role?: string | null
  label?: string | null
  r2_sequence_index?: number | null
  status?: string | null
  has_report?: boolean
  score?: number | string | null
  r1_score?: number | string | null
  r2_score?: number | string | null
  score_change?: number | string | null
  recommendation?: string | null
  confidence?: string | null
  summary?: string | null
  revision_count?: number
  verified_count?: number
  assumed_count?: number
  open_questions_count?: number
  key_points_count?: number
  missing_contract_fields?: string[]
  missing_advisory_fields?: string[]
  artifact_path?: string | null
  artifact_available?: boolean
  created_at?: string | null
  [key: string]: unknown
}

export interface DealR2ContractFieldGroup {
  field: string
  aliases?: string[]
  [key: string]: unknown
}

export interface DealR2AgentReportsResponse {
  schema_version?: string
  deal_id?: string
  generated_at?: string | null
  contract_field_groups?: DealR2ContractFieldGroup[]
  advisory_fields?: string[]
  artifact_path?: string | null
  artifact_available?: boolean
  counts?: Record<string, number>
  agents?: DealR2AgentReportSummary[]
  [key: string]: unknown
}

export interface DealR3ReviewReportSummary {
  agent_id: string
  role?: string | null
  label?: string | null
  status?: 'pass' | 'warn' | string | null
  stance?: string | null
  recommendation?: string | null
  summary?: string | null
  challenge_count?: number
  evidence_count?: number
  created_at?: string | null
  [key: string]: unknown
}

export interface DealR3ReviewArtifactSummary {
  path?: string | null
  available?: boolean
  [key: string]: unknown
}

export interface DealR3ReviewSummaryResponse {
  schema_version?: 'siq_deal_r3_review_summary_v1' | string
  deal_id?: string
  generated_at?: string | null
  status?: 'pass' | 'warn' | 'missing' | string
  mode?: 'normal' | 'skip' | 'unknown' | string
  skipped?: boolean
  skip_reason?: string | null
  artifacts?: {
    json?: DealR3ReviewArtifactSummary | null
    markdown?: DealR3ReviewArtifactSummary | null
    [key: string]: unknown
  }
  counts?: {
    reports?: number
    pass?: number
    warn?: number
    artifacts_available?: number
    warnings?: number
    challenges?: number
    [key: string]: unknown
  }
  reports?: DealR3ReviewReportSummary[]
  warnings?: string[]
  [key: string]: unknown
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

export type PrimaryMarketCapabilityStatus =
  | 'ready'
  | 'ready_with_restrictions'
  | 'review_required'
  | 'blocked'
  | 'pending'
  | 'not_requested'
  | 'queued'
  | 'indexing'
  | 'indexed'
  | 'failed'
  | string

export interface PrimaryMarketMaterialCapability {
  status?: PrimaryMarketCapabilityStatus | null
  ready?: boolean | null
  restricted?: boolean | null
  reason?: string | null
  warnings?: string[]
  [key: string]: unknown
}

export type PrimaryMarketMaterialCapabilityValue =
  | PrimaryMarketCapabilityStatus
  | boolean
  | PrimaryMarketMaterialCapability
  | null

export interface PrimaryMarketMaterialCapabilities {
  text_evidence?: PrimaryMarketMaterialCapabilityValue
  source_trace?: PrimaryMarketMaterialCapabilityValue
  source_page_trace?: PrimaryMarketMaterialCapabilityValue
  page_trace?: PrimaryMarketMaterialCapabilityValue
  financial_facts?: PrimaryMarketMaterialCapabilityValue
  semantic_index?: PrimaryMarketMaterialCapabilityValue
  indexing?: PrimaryMarketMaterialCapabilityValue
  [key: string]: PrimaryMarketMaterialCapabilityValue | undefined
}

export interface PrimaryMarketMaterialQualityReport {
  status?: string | null
  score?: number | null
  warnings?: string[]
  failures?: string[]
  blocking_reasons?: string[]
  capability_restrictions?: string[]
  reviewed_at?: string | null
  reviewed_by?: { id?: number | string | null; username?: string | null } | null
  review_note?: string | null
  [key: string]: unknown
}

export interface PrimaryMarketMaterialParseRun {
  parse_run_id: string
  status?: string | null
  parse_status?: string | null
  parser_task_id?: string | null
  parser_version?: string | null
  quality_status?: string | null
  quality_report?: PrimaryMarketMaterialQualityReport | null
  capabilities?: PrimaryMarketMaterialCapabilities | null
  current?: boolean | null
  archived_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  failure_code?: string | null
  failure_message?: string | null
  [key: string]: unknown
}

export interface PrimaryMarketMaterialVersionLink {
  document_id?: string | null
  supersedes_document_id?: string | null
  superseded_by_document_id?: string | null
  version?: number | string | null
  is_current?: boolean | null
  [key: string]: unknown
}

export interface PrimaryMarketMaterial extends DealDocument {
  schema_version?: string | null
  document_profile?: string | null
  document_status?: string | null
  parse_status?: string | null
  analysis_source_status?: string | null
  index_status?: string | null
  market?: string | null
  exchange?: string | null
  board?: string | null
  filing_stage?: string | null
  document_date?: string | null
  current_parse_run_id?: string | null
  parse_run?: PrimaryMarketMaterialParseRun | null
  current_parse_run?: PrimaryMarketMaterialParseRun | null
  parse_runs?: PrimaryMarketMaterialParseRun[]
  capabilities?: PrimaryMarketMaterialCapabilities | null
  quality_report?: PrimaryMarketMaterialQualityReport | null
  is_active_source?: boolean | null
  source_id?: string | null
  version?: number | string | null
  version_chain?: PrimaryMarketMaterialVersionLink[]
  supersedes_document_id?: string | null
  superseded_by_document_id?: string | null
  stale_receipt_count?: number | null
  stale_report_count?: number | null
  status_url?: string | null
  raw_file_url?: string | null
  original_url?: string | null
  reused?: boolean | null
}

export interface PrimaryMarketMaterialsResponse {
  schema_version?: string | null
  deal_id?: string | null
  materials?: PrimaryMarketMaterial[]
  documents?: PrimaryMarketMaterial[]
  evidence_snapshot_hash?: string | null
  [key: string]: unknown
}

export interface PrimaryMarketMaterialResponse {
  schema_version?: string | null
  document?: PrimaryMarketMaterial
  material?: PrimaryMarketMaterial
  parse_run?: PrimaryMarketMaterialParseRun | null
  current_parse_run?: PrimaryMarketMaterialParseRun | null
  analysis_source?: PrimaryMarketAnalysisSource | null
  evidence_snapshot?: Record<string, unknown> | null
  quality?: PrimaryMarketMaterialQualityReport | null
  status_url?: string | null
  reused?: boolean
  [key: string]: unknown
}

export interface PrimaryMarketAnalysisSource {
  source_id?: string | null
  status?: string | null
  is_active?: boolean | null
  capabilities?: PrimaryMarketMaterialCapabilities | null
  parse_run_id?: string | null
  [key: string]: unknown
}

export interface PrimaryMarketMaterialParseStatusResponse {
  schema_version?: string | null
  deal_id?: string | null
  document_id?: string | null
  document?: PrimaryMarketMaterial
  material?: PrimaryMarketMaterial
  parse_run?: PrimaryMarketMaterialParseRun | null
  parse_status?: string | null
  analysis_source_status?: string | null
  index_status?: string | null
  capabilities?: PrimaryMarketMaterialCapabilities | null
  quality_report?: PrimaryMarketMaterialQualityReport | null
  analysis_source?: PrimaryMarketAnalysisSource | null
  [key: string]: unknown
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
  page?: number
  page_size?: number
  include_status?: boolean
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
