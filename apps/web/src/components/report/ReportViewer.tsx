import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, BarChart3, ChartCandlestick, Loader2, Scale, ShieldCheck, TrendingUp } from 'lucide-react'
import PageWithAgentChat from '../agent/PageWithAgentChat'
import { useToast } from '../../hooks/useToast'
import { copyText } from '../../lib/clipboard'
import { apiBlob, apiJson, apiText } from '@/shared/api/client'
import { useAuth } from '../../hooks/useAuth'
import type { Company, ReportItem, ReportViewerProps } from '@/lib/reportTypes'
import { companyHasReportForType, reportUrlFor } from '@/lib/reportTypes'
import { buildReportSrcDoc } from './buildReportSrcDoc'
import { mergeResearchIdentity } from '../../lib/agentChatIdentity'
import ReportEmptyState from './ReportEmptyState'
import ReportFrame from './ReportFrame'
import ReportSelector from './ReportSelector'
import ReportToolbar from './ReportToolbar'

const REPORT_TYPE_META = {
  analysis: {
    label: '智能分析',
    english: 'Smart Analysis',
    Icon: BarChart3,
    accent: 'from-primary to-primary-light',
    steps: ['分析', '洞察', '结论'],
  },
  factcheck: {
    label: '事实核查',
    english: 'Fact Check',
    Icon: ShieldCheck,
    accent: 'from-primary to-primary-light',
    steps: ['核查', '证据', '溯源'],
  },
  tracking: {
    label: '持续跟踪',
    english: 'Continuous Tracking',
    Icon: TrendingUp,
    accent: 'from-primary to-primary-light',
    steps: ['跟踪', '预警', '复盘'],
  },
  legal: {
    label: '法务合规',
    english: 'Legal Compliance',
    Icon: Scale,
    accent: 'from-primary to-primary-light',
    steps: ['检索', '审查', '意见'],
  },
} as const

export default function ReportViewer({ agentConfig, pageTitle, reportType, reportApiSuffix, iframeTitle, emptyTitle, emptyDescription, infoFields }: ReportViewerProps) {
  const { toast } = useToast()
  const { hasPermission } = useAuth()
  const [searchParams] = useSearchParams()
  const requestedCompany = searchParams.get('company') || ''
  const requestedResult = searchParams.get('result') || ''
  const [companies, setCompanies] = useState<Company[]>([])
  const [selectedDir, setSelectedDir] = useState('')
  const [reports, setReports] = useState<ReportItem[]>([])
  const [selectedReportUrl, setSelectedReportUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [reportLoading, setReportLoading] = useState(false)
  const [contentLoading, setContentLoading] = useState(false)
  const [reportHtml, setReportHtml] = useState('')
  const [reportError, setReportError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    apiJson<{ companies?: Company[] }>('/api/wiki/companies/list')
      .then((data) => {
        const list: Company[] = data.companies || []
        setCompanies(list)
        const requested = list.find((c) => c.dir === requestedCompany)
        const first = list.find((c) => companyHasReportForType(c, reportType))
          || list.find((c) => c.hasReport || c.hasFactcheck || c.hasTracking || c.hasLegal)
        if (requested) setSelectedDir(requested.dir)
        else if (first) setSelectedDir(first.dir)
        else if (list[0]) setSelectedDir(list[0].dir)
        setLoading(false)
      })
      .catch(() => { setError('无法加载公司列表，请确认后端服务正常运行。'); setLoading(false) })
  }, [requestedCompany, reportType])

  useEffect(() => {
    if (!selectedDir) return
    let ignore = false
    async function loadReports() {
      setConfirmDelete(false)
      setReports([]); setSelectedReportUrl(''); setReportHtml('')
      setReportLoading(true)
      setReportError(null)
      try {
        const data = await apiJson<Record<string, ReportItem[]>>(`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportApiSuffix}`)
        if (ignore) return
        const list: ReportItem[] = data[reportApiSuffix] || data.reports || []
        setReports(list)
        if (list.length > 0) {
          const selected = list.find((r) => r.filename === requestedResult) || list[0]
          setSelectedReportUrl(reportUrlFor(selectedDir, reportType, selected))
        }
      } catch (err) {
        if (!ignore) {
          setReports([])
          setReportError(err instanceof Error ? err.message : '报告列表加载失败')
        }
      } finally {
        if (!ignore) setReportLoading(false)
      }
    }
    loadReports()
    return () => { ignore = true }
  }, [selectedDir, requestedResult, reportApiSuffix, reportType])

  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    let ignore = false
    async function loadContent() {
      if (!selectedReportUrl) {
        setReportHtml('')
        setContentLoading(false)
        return
      }
      setReportHtml('')
      setContentLoading(true)
      setReportError(null)
      try {
        const html = await apiText(selectedReportUrl)
        if (!ignore) setReportHtml(html)
      } catch (err) {
        if (!ignore) setReportError(err instanceof Error ? err.message : '报告内容加载失败')
      } finally {
        if (!ignore) setContentLoading(false)
      }
    }
    loadContent()
    return () => { ignore = true }
  }, [selectedReportUrl, retryKey])

  const retryContent = () => setRetryKey((k) => k + 1)

  const selectedCompany = companies.find((c) => c.dir === selectedDir)
  const selectedReport = reports.find((report) => reportUrlFor(selectedDir, reportType, report) === selectedReportUrl)
  const reportSrcDoc = useMemo(() => buildReportSrcDoc(reportHtml, selectedReportUrl), [reportHtml, selectedReportUrl])
  const hasReports = reports.length > 0
  const meta = REPORT_TYPE_META[reportType]
  const KickerIcon = meta.Icon
  const cleanCompanyName = selectedCompany?.name.split('_')[0] || '请选择公司'
  const agentContext = useMemo(() => ({
    company: selectedCompany
      ? {
          code: selectedCompany.code,
          name: cleanCompanyName,
          dir: selectedCompany.dir,
          market: selectedCompany.market,
          company_id: selectedCompany.company_id,
          filing_id: selectedCompany.filing_id,
          parse_run_id: selectedCompany.parse_run_id,
        }
      : undefined,
    report: selectedReport
      ? {
          type: reportType,
          title: meta.label,
          filename: selectedReport.filename,
          url: selectedReportUrl,
          mtime: selectedReport.mtime,
          market: selectedReport.market,
          company_id: selectedReport.company_id,
          filing_id: selectedReport.filing_id,
          parse_run_id: selectedReport.parse_run_id,
        }
      : {
          type: reportType,
          title: meta.label,
        },
    page: {
      title: pageTitle,
    },
    research_identity: mergeResearchIdentity(selectedReport, selectedCompany),
  }), [cleanCompanyName, meta.label, pageTitle, reportType, selectedCompany, selectedReport, selectedReportUrl])
  const updatedAt = selectedReport?.mtime
    ? new Date(selectedReport.mtime).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '--'
  const canDeleteReport = hasPermission('report.delete')

  const share = async () => {
    const pageUrl = `${window.location.origin}${window.location.pathname}?company=${encodeURIComponent(selectedDir)}&result=${encodeURIComponent(selectedReport?.filename || '')}`
    if (await copyText(pageUrl)) {
      toast({ type: 'success', title: '链接已复制', description: '可以直接粘贴给协作者查看这份报告。' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动复制地址栏链接。' })
    }
  }

  const downloadSelectedReport = async () => {
    if (!selectedReport || !selectedReportUrl) return
    try {
      const blob = await apiBlob(selectedReportUrl)
      const href = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = href
      link.download = selectedReport.filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(href), 1000)
    } catch (err) {
      toast({ type: 'error', title: '下载失败', description: (err as Error).message })
    }
  }

  const deleteSelectedReport = async () => {
    if (!selectedReport) return
    setDeleting(true)
    try {
      await apiJson(`/api/wiki/companies/${encodeURIComponent(selectedDir)}/${reportType}/${encodeURIComponent(selectedReport.filename)}`, { method: 'DELETE' })
      const nextReports = reports.filter((report) => report.filename !== selectedReport.filename)
      setReports(nextReports)
      setSelectedReportUrl(nextReports[0] ? reportUrlFor(selectedDir, reportType, nextReports[0]) : '')
      setConfirmDelete(false)
      toast({ type: 'success', title: '报告已删除', description: selectedReport.filename })
      apiJson<{ companies?: Company[] }>('/api/wiki/companies/list').then((data) => {
        if (data?.companies) setCompanies(data.companies)
      }).catch(() => {})
    } catch (err) {
      toast({ type: 'error', title: '删除失败', description: (err as Error).message })
    } finally {
      setDeleting(false)
    }
  }

  const handleSelectReportUrl = (url: string) => {
    setSelectedReportUrl(url)
    setConfirmDelete(false)
  }

  if (loading) {
    return (
      <PageWithAgentChat {...agentConfig}>
        <div className="flex items-center justify-center py-32">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <span className="ml-3 text-text-muted">加载公司列表...</span>
        </div>
      </PageWithAgentChat>
    )
  }

  if (error) {
    return (
      <PageWithAgentChat {...agentConfig}>
        <div className="rounded-2xl border border-error/20 bg-error/5 p-6 text-center">
          <AlertCircle className="mx-auto mb-3 h-8 w-8 text-error" />
          <p className="text-base text-error">{error}</p>
        </div>
      </PageWithAgentChat>
    )
  }

  return (
    <PageWithAgentChat {...agentConfig} context={agentContext}>
      <div className="secondary-page">
        <section className="secondary-hero">
          <div className="secondary-hero-inner">
            <div className="min-w-0">
              <div className="secondary-kicker">
                <KickerIcon className="h-3.5 w-3.5" />
                {meta.english}
              </div>
              <h1 className="secondary-title">{pageTitle}</h1>
              <p className="secondary-description">选择公司和报告版本，系统会以统一阅读样式展示 HTML 结果。</p>
            </div>
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center lg:flex-col lg:items-end">
              <div className="secondary-step-row">
                {meta.steps.map((step, index) => (
                  <span key={step} className={`secondary-step-chip ${index === 0 ? 'is-active' : ''}`}>{step}</span>
                ))}
              </div>
              {selectedCompany && (
                <div className="secondary-company-card" title={`${cleanCompanyName} ${selectedCompany.code}`}>
                  <div className="secondary-company-icon">
                    <ChartCandlestick className="h-4 w-4" />
                  </div>
                  <div className="secondary-company-text">
                    <span className="secondary-company-name">{cleanCompanyName}</span>
                    <span className="secondary-company-code">{selectedCompany.code}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
        <div className="page-toolbar flex flex-col gap-4 px-4 py-4 sm:px-5 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between">
          <ReportSelector
            companies={companies}
            selectedDir={selectedDir}
            onSelectDir={setSelectedDir}
            reports={reports}
            selectedReportUrl={selectedReportUrl}
            onSelectReportUrl={handleSelectReportUrl}
            reportType={reportType}
            hasReports={hasReports}
          />
          <ReportToolbar
            selectedReportUrl={selectedReportUrl}
            canDeleteReport={canDeleteReport}
            confirmDelete={confirmDelete}
            deleting={deleting}
            onConfirmDeleteChange={setConfirmDelete}
            onShare={share}
            onDownload={downloadSelectedReport}
            onDelete={deleteSelectedReport}
          />
        </div>
        {reportLoading ? (
          <div className="secondary-panel flex items-center justify-center px-5 py-20 text-text-muted">
            <Loader2 className="mr-3 h-6 w-6 animate-spin text-primary" />
            正在加载报告列表...
          </div>
        ) : reportError ? (
          <div className="rounded-2xl border border-error/20 bg-error/5 p-6 text-center">
            <AlertCircle className="mx-auto mb-3 h-8 w-8 text-error" />
            <p className="text-base text-error">报告加载失败：{reportError}</p>
          </div>
        ) : (
          <>
            {selectedReportUrl ? (
              <ReportFrame
                selectedReportUrl={selectedReportUrl}
                selectedReport={selectedReport}
                reportSrcDoc={reportSrcDoc}
                contentLoading={contentLoading}
                iframeTitle={iframeTitle}
                updatedAt={updatedAt}
                accent={meta.accent}
                error={reportError}
                onRetry={retryContent}
              />
            ) : (
              <ReportEmptyState
                selectedDir={selectedDir}
                hasReports={hasReports}
                selectedReportUrl={selectedReportUrl}
                companyName={selectedCompany?.name || '该公司'}
                emptyTitle={emptyTitle}
                emptyDescription={emptyDescription}
              />
            )}
          </>
        )}
        {selectedCompany && (
          <div className="secondary-panel px-5 py-4">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {infoFields(selectedCompany).map((field) => (
                <div key={field.label}>
                  <span className="secondary-label">{field.label}</span>
                  <p className="mt-1 text-base font-semibold text-text">{field.value}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </PageWithAgentChat>
  )
}
