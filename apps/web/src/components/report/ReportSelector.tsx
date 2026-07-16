import { Building2, Globe2 } from 'lucide-react'
import type { Company, ReportItem, ReportType } from '@/lib/reportTypes'
import { reportUrlFor } from '@/lib/reportTypes'
import { DISCLOSURE_MARKETS, type DisclosureMarketCode } from '@/lib/marketMetadata'
import type {
  GeneratedArtifactOption,
  ResearchCompanyOption,
  ResearchMarketOption,
  SourceReportOption,
} from '@/features/research-universe/types'

interface LegacyReportSelectorProps {
  mode?: 'legacy'
  companies: Company[]
  selectedDir: string
  onSelectDir: (dir: string) => void
  reports: ReportItem[]
  selectedReportUrl: string
  onSelectReportUrl: (url: string) => void
  reportType: ReportType
  hasReports: boolean
}

interface ResearchUniverseReportSelectorProps {
  mode: 'research-universe'
  markets: ResearchMarketOption[]
  selectedMarket: DisclosureMarketCode | ''
  onSelectMarket: (market: DisclosureMarketCode) => void
  companies: ResearchCompanyOption[]
  selectedCompanyKey: string
  onSelectCompanyKey: (companyKey: string) => void
  sourceReports: SourceReportOption[]
  selectedReportId: string
  onSelectReportId: (reportId: string) => void
  artifacts: GeneratedArtifactOption[]
  selectedArtifactId: string
  onSelectArtifactId: (artifactId: string) => void
  reportType: Exclude<ReportType, 'legal'>
  loadingCompanies?: boolean
  loadingReports?: boolean
  loadingArtifacts?: boolean
  canLoadMoreArtifacts?: boolean
  loadingMoreArtifacts?: boolean
  onRequestMoreArtifacts?: () => void
}

type ReportSelectorProps = LegacyReportSelectorProps | ResearchUniverseReportSelectorProps

const ARTIFACT_LABELS: Record<Exclude<ReportType, 'legal'>, string> = {
  analysis: '分析结果',
  factcheck: '核查结果',
  tracking: '跟踪结果',
}

function sourceReportLabel(report: SourceReportOption) {
  if (report.label) return report.label
  const parts = [report.fiscal_year, report.form_type || report.report_type]
  if (report.period_end) parts.push(`截止 ${report.period_end}`)
  if (report.quality_status === 'warning') parts.push('warning')
  return parts.filter(Boolean).join(' · ') || report.report_id
}

function artifactLabel(artifact: GeneratedArtifactOption) {
  const createdAt = artifact.created_at
    ? new Date(artifact.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : ''
  const legacy = artifact.identity_status === 'legacy_unbound' ? ' · 历史未绑定' : ''
  return `${artifact.filename || artifact.artifact_id}${createdAt ? ` · ${createdAt}` : ''}${legacy}`
}

function ResearchUniverseSelector(props: ResearchUniverseReportSelectorProps) {
  const selectedSourceReport = props.sourceReports.find((report) => report.report_id === props.selectedReportId)
  const artifactLabelText = ARTIFACT_LABELS[props.reportType]

  return (
    <div className="grid w-full min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 xl:w-auto xl:grid-cols-4">
      <label className="min-w-0 space-y-1">
        <span className="secondary-label">市场</span>
        <span className="relative block">
          <Globe2 className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <select
            value={props.selectedMarket}
            onChange={(event) => props.onSelectMarket(event.target.value as DisclosureMarketCode)}
            className="form-control w-full appearance-none py-0 pl-10 pr-9 text-base font-medium sm:text-sm xl:min-w-[180px]"
            aria-label="市场"
          >
            {props.markets.map((market) => (
              <option key={market.market} value={market.market} disabled={!market.enabled}>
                {DISCLOSURE_MARKETS[market.market].label}
              </option>
            ))}
          </select>
        </span>
      </label>

      <label className="min-w-0 space-y-1">
        <span className="secondary-label">公司</span>
        <span className="relative block">
          <Building2 className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <select
            value={props.selectedCompanyKey}
            onChange={(event) => props.onSelectCompanyKey(event.target.value)}
            className="form-control w-full appearance-none py-0 pl-10 pr-9 text-base font-medium sm:text-sm xl:min-w-[220px]"
            disabled={!props.selectedMarket || props.loadingCompanies || props.companies.length === 0}
            aria-label="公司"
          >
            {props.loadingCompanies ? <option value="">正在加载公司...</option> : null}
            {!props.loadingCompanies && props.companies.length === 0 ? <option value="">当前市场暂无可用公司</option> : null}
            {props.companies.map((company) => (
              <option key={company.company_key} value={company.company_key}>
                {company.display_code} {company.display_name}
              </option>
            ))}
          </select>
        </span>
      </label>

      <label className="min-w-0 space-y-1">
        <span className="secondary-label">源报告</span>
        <select
          value={props.selectedReportId}
          onChange={(event) => props.onSelectReportId(event.target.value)}
          className="form-control w-full appearance-none px-3 pr-9 text-base sm:text-sm xl:min-w-[260px]"
          disabled={!props.selectedCompanyKey || props.loadingReports || props.sourceReports.length === 0}
          aria-label="源报告"
        >
          {props.loadingReports ? <option value="">正在加载源报告...</option> : null}
          {!props.loadingReports && props.sourceReports.length === 0 ? <option value="">该公司暂无可用源报告</option> : null}
          {props.sourceReports.map((report) => (
            <option key={report.report_id} value={report.report_id}>
              {sourceReportLabel(report)}
            </option>
          ))}
        </select>
        {selectedSourceReport?.quality_status === 'warning' ? (
          <span className="block text-xs leading-5 text-warning" role="status">该源报告存在质量警告，可继续使用</span>
        ) : null}
      </label>

      <label className="min-w-0 space-y-1">
        <span className="secondary-label">{artifactLabelText}</span>
        <select
          value={props.selectedArtifactId}
          onChange={(event) => props.onSelectArtifactId(event.target.value)}
          className="form-control w-full appearance-none px-3 pr-9 text-base sm:text-sm xl:min-w-[240px]"
          disabled={!props.selectedReportId || props.loadingArtifacts || props.artifacts.length === 0}
          aria-label={artifactLabelText}
          onFocus={() => {
            if (props.canLoadMoreArtifacts && !props.loadingMoreArtifacts) props.onRequestMoreArtifacts?.()
          }}
        >
          {props.loadingArtifacts ? <option value="">正在加载{artifactLabelText}...</option> : null}
          {!props.loadingArtifacts && props.artifacts.length === 0 ? <option value="">暂无{artifactLabelText}</option> : null}
          {props.artifacts.map((artifact) => (
            <option key={artifact.artifact_id} value={artifact.artifact_id}>
              {artifactLabel(artifact)}
            </option>
          ))}
        </select>
        {props.canLoadMoreArtifacts || props.loadingMoreArtifacts ? (
          <button
            type="button"
            className="text-xs font-medium text-primary hover:text-primary-dark disabled:cursor-wait disabled:text-text-muted"
            disabled={props.loadingMoreArtifacts}
            onClick={props.onRequestMoreArtifacts}
          >
            {props.loadingMoreArtifacts ? '正在加载更多结果...' : '加载更多结果'}
          </button>
        ) : null}
      </label>
    </div>
  )
}

export default function ReportSelector(props: ReportSelectorProps) {
  if (props.mode === 'research-universe') return <ResearchUniverseSelector {...props} />

  const {
    companies,
    selectedDir,
    onSelectDir,
    reports,
    selectedReportUrl,
    onSelectReportUrl,
    reportType,
    hasReports,
  } = props
  return (
    <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:flex lg:flex-wrap lg:items-center">
      <label className="min-w-0 space-y-1">
        <span className="secondary-label">公司</span>
        <span className="relative block">
          <Building2 className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <select
            value={selectedDir}
            onChange={(e) => onSelectDir(e.target.value)}
            className="form-control w-full appearance-none py-0 pl-10 pr-9 text-sm font-medium lg:min-w-[240px]"
          >
            {companies.map((c) => (
              <option key={c.dir} value={c.dir}>
                {c.code} {c.name}
              </option>
            ))}
          </select>
        </span>
      </label>
      {hasReports && (
        <label className="min-w-0 space-y-1">
          <span className="secondary-label">报告版本</span>
          <select
            value={selectedReportUrl}
            onChange={(e) => onSelectReportUrl(e.target.value)}
            className="form-control w-full appearance-none px-3 pr-9 text-sm lg:min-w-[280px]"
          >
            {reports.map((r) => (
              <option key={r.filename} value={reportUrlFor(selectedDir, reportType, r)}>
                {r.filename}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}
