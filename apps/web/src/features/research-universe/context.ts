import type { ReportType } from '@/lib/reportTypes'
import {
  analysisBaselineId,
  isExactArtifactForReport,
  type GeneratedArtifactOption,
  type ResearchCompanyOption,
  type SourceReportOption,
} from './types'

export interface BuildResearchAgentContextInput {
  company?: ResearchCompanyOption
  sourceReport?: SourceReportOption
  artifact?: GeneratedArtifactOption
  reportType: Exclude<ReportType, 'legal'>
  reportTitle: string
  pageTitle: string
}

export function buildResearchAgentContext({
  company,
  sourceReport,
  artifact,
  reportType,
  reportTitle,
  pageTitle,
}: BuildResearchAgentContextInput) {
  const baselineAnalysisArtifactId = analysisBaselineId(sourceReport)
  const artifactMatchesSource = isExactArtifactForReport(artifact, sourceReport)
  const downstream = reportType === 'factcheck' || reportType === 'tracking'

  return {
    market: sourceReport?.research_identity.market || company?.market,
    company: company
      ? {
          market: company.market,
          company_id: company.company_id,
          company_key: company.company_key,
          code: company.display_code,
          name: company.display_name,
        }
      : undefined,
    source_report: sourceReport
      ? {
          report_id: sourceReport.report_id,
          filing_id: sourceReport.research_identity.filing_id,
          parse_run_id: sourceReport.research_identity.parse_run_id,
          source_family: sourceReport.source_family,
          report_type: sourceReport.report_type,
          form_type: sourceReport.form_type,
          fiscal_year: sourceReport.fiscal_year,
          period_end: sourceReport.period_end,
          quality_status: sourceReport.quality_status,
        }
      : undefined,
    artifact: artifact && artifactMatchesSource
      ? {
          artifact_id: artifact.artifact_id,
          artifact_type: artifact.artifact_type,
          source_report_id: artifact.source_report_id,
          identity_status: artifact.identity_status,
        }
      : undefined,
    report: {
      type: reportType,
      title: reportTitle,
      artifact_id: artifact && artifactMatchesSource ? artifact.artifact_id : undefined,
      report_id: sourceReport?.report_id,
      market: sourceReport?.research_identity.market,
      company_id: sourceReport?.research_identity.company_id,
      filing_id: sourceReport?.research_identity.filing_id,
      parse_run_id: sourceReport?.research_identity.parse_run_id,
    },
    page: { title: pageTitle },
    research_identity: sourceReport ? { ...sourceReport.research_identity } : undefined,
    upstream_analysis_artifact_id: downstream ? baselineAnalysisArtifactId : undefined,
    capabilities: {
      ...(sourceReport?.capabilities || {}),
      analysis_baseline_available: Boolean(baselineAnalysisArtifactId),
    },
  }
}
