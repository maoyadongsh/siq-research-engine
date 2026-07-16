import type { DisclosureMarketCode } from '@/lib/marketMetadata'
import type { ReportType } from '@/lib/reportTypes'

export type MarketScope = 'cn-only' | 'all-parsed'
export type ResearchAgentType = ReportType
export type ResearchReadiness = Record<string, unknown>
export type ResearchCapabilities = Record<string, unknown>

export interface ResearchIdentity {
  market: DisclosureMarketCode
  company_id: string
  filing_id: string
  parse_run_id: string
}

export interface ResearchMarketOption {
  market: DisclosureMarketCode
  label: string
  order: number
  enabled: boolean
  company_count: number
  capabilities: ResearchCapabilities
  degraded_reasons: string[]
}

export interface ResearchCompanyOption {
  company_key: string
  market: DisclosureMarketCode
  company_id: string
  company_wiki_id: string
  display_code: string
  display_name: string
  parsed_report_count: number
  readiness: ResearchReadiness
  capabilities: ResearchCapabilities
  degraded_reasons: string[]
}

export type SourceReportQualityStatus = 'pass' | 'warning' | 'fail' | string

export interface SourceReportOption {
  report_id: string
  label: string
  report_type: string
  form_type?: string | null
  fiscal_year?: number | null
  period_end?: string | null
  published_at?: string | null
  quality_status: SourceReportQualityStatus
  source_family: string
  document_format?: string | null
  research_identity: ResearchIdentity
  readiness: ResearchReadiness
  capabilities: ResearchCapabilities
  degraded_reasons: string[]
  baseline_analysis_artifact_id?: string | null
  analysis_artifact_id?: string | null
  baseline_analysis_integrity_status?: 'deferred_until_content_or_workflow_request' | 'verified' | null | string
}

export interface ArtifactQuality {
  status?: string
  warnings?: string[]
  [key: string]: unknown
}

export interface GeneratedArtifactOption {
  artifact_id: string
  artifact_type: Exclude<ReportType, 'legal'>
  status: string
  created_at?: string | null
  source_report_id?: string | null
  source_family?: string | null
  quality?: ArtifactQuality | null
  identity_status?: string | null
  usable_as_baseline?: boolean
  content_url?: string | null
  filename?: string | null
  research_identity?: ResearchIdentity | null
  content_integrity_status?: 'deferred_until_content_request' | 'unavailable' | string
}

export interface ResearchMarketsResponse {
  markets: ResearchMarketOption[]
}

export interface ResearchCompaniesResponse {
  market: DisclosureMarketCode
  companies: ResearchCompanyOption[]
}

export interface SourceReportsResponse {
  market: DisclosureMarketCode
  company_key: string
  reports: SourceReportOption[]
}

export interface GeneratedArtifactsResponse {
  market: DisclosureMarketCode
  company_key: string
  report_id: string
  artifact_type: Exclude<ReportType, 'legal'>
  artifacts: GeneratedArtifactOption[]
  legacy_artifacts?: GeneratedArtifactOption[]
  items?: GeneratedArtifactOption[]
  pagination?: {
    limit: number
    next_cursor?: string | null
    has_more: boolean
    targeted?: boolean
  }
}

export interface ArtifactScope {
  market: DisclosureMarketCode
  companyKey: string
  reportId: string
  artifactType: Exclude<ReportType, 'legal'>
}

export interface DeleteArtifactResponse {
  deleted: boolean
  artifact_id: string
}

export function analysisBaselineId(report: SourceReportOption | null | undefined): string | undefined {
  const value = report?.baseline_analysis_artifact_id || report?.analysis_artifact_id
  return value?.trim() || undefined
}

export function isExactArtifactForReport(
  artifact: GeneratedArtifactOption | null | undefined,
  report: SourceReportOption | null | undefined,
) {
  if (!artifact || !report) return false
  if (artifact.identity_status !== 'exact') return false
  return artifact.source_report_id === report.report_id
}
