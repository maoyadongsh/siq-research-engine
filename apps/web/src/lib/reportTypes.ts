import type { AgentQuickQuestionInput } from './quickQuestions'

export type ReportType = 'analysis' | 'factcheck' | 'tracking' | 'legal'

export interface Company {
  code: string
  name: string
  dir: string
  hasReport: boolean
  reportCount: number
  hasFactcheck?: boolean
  factcheckCount?: number
  hasTracking?: boolean
  trackingCount?: number
  hasLegal?: boolean
  legalCount?: number
}

export interface ReportItem {
  filename: string
  url?: string
  size: number
  mtime: string
}

export interface ReportViewerAgentConfig {
  apiPrefix: string
  title: string
  description: string
  quickQuestions: AgentQuickQuestionInput[]
  quickQuestionClassName?: string
}

export interface ReportViewerProps {
  agentConfig: ReportViewerAgentConfig
  pageTitle: string
  reportType: ReportType
  reportApiSuffix: string
  iframeTitle: string
  emptyTitle: (companyName: string) => string
  emptyDescription: string
  infoFields: (company: Company) => { label: string; value: string }[]
}

export function companyHasReportForType(company: Company, reportType: ReportType) {
  if (reportType === 'analysis') return company.hasReport
  if (reportType === 'factcheck') return Boolean(company.hasFactcheck)
  if (reportType === 'tracking') return Boolean(company.hasTracking)
  return Boolean(company.hasLegal)
}

export function reportUrlFor(companyDir: string, reportType: ReportType, report: ReportItem) {
  return report.url || `/api/wiki/companies/${encodeURIComponent(companyDir)}/${reportType}/${encodeURIComponent(report.filename)}`
}
