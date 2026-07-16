import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, BarChart3, Building2, ChartCandlestick, FileText, Loader2, Share2, ShieldCheck, TrendingUp } from 'lucide-react'
import PageWithAgentChat from '@/components/agent/PageWithAgentChat'
import { EmptyState } from '@/components/page'
import { Button } from '@/components/ui'
import ReportEmptyState from '@/components/report/ReportEmptyState'
import ReportFrame from '@/components/report/ReportFrame'
import ReportSelector from '@/components/report/ReportSelector'
import ReportToolbar from '@/components/report/ReportToolbar'
import { buildReportSrcDoc } from '@/components/report/buildReportSrcDoc'
import { useToast } from '@/hooks/useToast'
import { useAuth } from '@/hooks/useAuth'
import { copyText } from '@/lib/clipboard'
import { DISCLOSURE_MARKETS } from '@/lib/marketMetadata'
import type { ReportItem, ReportType, ReportViewerProps } from '@/lib/reportTypes'
import {
  artifactContentUrl,
  deleteGeneratedArtifact,
  downloadArtifactContent,
  fetchArtifactContent,
  fetchGeneratedArtifacts,
  fetchResearchCompanies,
  fetchResearchMarkets,
  fetchSourceReports,
} from './api'
import { ArtifactContentCache } from './artifactContentCache'
import { buildResearchAgentContext } from './context'
import {
  applyResearchSelectionToSearchParams,
  buildResearchShareUrl,
  EMPTY_RESEARCH_SELECTION,
  orderedResearchMarkets,
  readRequestedResearchSelection,
  researchSelectionReducer,
  resolveInitialArtifact,
  resolveInitialCompany,
  resolveInitialMarket,
  resolveInitialReport,
  type ResearchSelection,
} from './selectionModel'
import { analysisBaselineId, type ArtifactScope, type GeneratedArtifactOption, type GeneratedArtifactsResponse, type ResearchCompanyOption, type ResearchMarketOption, type SourceReportOption } from './types'

type MultiMarketReportType = Exclude<ReportType, 'legal'>
type ResearchUniverseReportViewerProps = Omit<ReportViewerProps, 'reportType'> & {
  reportType: MultiMarketReportType
}

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
} as const

const EMPTY_MARKETS: ResearchMarketOption[] = []
const EMPTY_COMPANIES: ResearchCompanyOption[] = []
const EMPTY_SOURCE_REPORTS: SourceReportOption[] = []
const EMPTY_ARTIFACTS: GeneratedArtifactOption[] = []
const ARTIFACT_PAGE_SIZE = 20

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

function uniqueArtifacts(artifacts: GeneratedArtifactOption[]) {
  const byId = new Map<string, GeneratedArtifactOption>()
  for (const artifact of artifacts) byId.set(artifact.artifact_id, artifact)
  return [...byId.values()]
}

interface AsyncResource<T> {
  key: string
  data: T
  error: string | null
}

interface ArtifactPaginationResource {
  key: string
  nextCursor: string | null
  targeted: boolean
  loading: boolean
}

function responseArtifacts(response: GeneratedArtifactsResponse, market: ResearchSelection['market']) {
  if (response.items) return uniqueArtifacts(response.items)
  const legacyArtifacts = market === 'CN' ? response.legacy_artifacts || [] : []
  return uniqueArtifacts([...(response.artifacts || []), ...legacyArtifacts])
}

function downstreamEmptyDescription(reportType: MultiMarketReportType, hasBaseline: boolean, fallback: string) {
  if (reportType === 'factcheck' && !hasBaseline) return '该源报告暂无匹配的分析基线。仍可围绕源报告与核查助手对话，生成正式核查前需先完成智能分析。'
  if (reportType === 'tracking' && !hasBaseline) return '该源报告暂无匹配的分析基线。仍可围绕源报告与跟踪助手对话，启动正式跟踪前需先完成智能分析。'
  return fallback
}

export default function ResearchUniverseReportViewer({
  agentConfig,
  pageTitle,
  reportType,
  iframeTitle,
  emptyTitle,
  emptyDescription,
}: ResearchUniverseReportViewerProps) {
  const { toast } = useToast()
  const { hasPermission } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = useMemo(
    () => readRequestedResearchSelection(searchParams),
    [searchParams],
  )
  const requestedRef = useRef(requested)
  const [selection, dispatch] = useReducer(researchSelectionReducer, EMPTY_RESEARCH_SELECTION)
  const [marketResource, setMarketResource] = useState<AsyncResource<ResearchMarketOption[]>>({ key: '', data: [], error: null })
  const [companyResource, setCompanyResource] = useState<AsyncResource<ResearchCompanyOption[]>>({ key: '', data: [], error: null })
  const [sourceReportResource, setSourceReportResource] = useState<AsyncResource<SourceReportOption[]>>({ key: '', data: [], error: null })
  const [artifactResource, setArtifactResource] = useState<AsyncResource<GeneratedArtifactOption[]>>({ key: '', data: [], error: null })
  const [artifactPagination, setArtifactPagination] = useState<ArtifactPaginationResource>({
    key: '', nextCursor: null, targeted: false, loading: false,
  })
  const [contentResource, setContentResource] = useState<AsyncResource<string>>({ key: '', data: '', error: null })
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [retryKey, setRetryKey] = useState(0)
  const artifactPageControllerRef = useRef<AbortController | null>(null)
  const [contentCache] = useState(() => new ArtifactContentCache())
  const requestedMarket = requested.market || (requested.legacyCompany || requested.legacyResult ? 'CN' : undefined)
  const selectedMarket = selection.market
  const companyRequestKey = selectedMarket
  const sourceReportRequestKey = selectedMarket && selection.companyKey
    ? `${selectedMarket}\u0000${selection.companyKey}`
    : ''
  const artifactRequestKey = sourceReportRequestKey && selection.reportId
    ? `${sourceReportRequestKey}\u0000${selection.reportId}\u0000${reportType}`
    : ''
  const contentRequestKey = artifactRequestKey && selection.artifactId
    ? `${artifactRequestKey}\u0000${selection.artifactId}\u0000${retryKey}`
    : ''
  const markets = marketResource.key === reportType ? marketResource.data : EMPTY_MARKETS
  const companies = companyResource.key === companyRequestKey ? companyResource.data : EMPTY_COMPANIES
  const sourceReports = sourceReportResource.key === sourceReportRequestKey ? sourceReportResource.data : EMPTY_SOURCE_REPORTS
  const artifacts = artifactResource.key === artifactRequestKey ? artifactResource.data : EMPTY_ARTIFACTS
  const reportHtml = contentResource.key === contentRequestKey ? contentResource.data : ''
  const reportError = contentResource.key === contentRequestKey ? contentResource.error : null
  const marketLoading = marketResource.key !== reportType
  const companyLoading = Boolean(companyRequestKey) && companyResource.key !== companyRequestKey
  const sourceReportLoading = Boolean(sourceReportRequestKey) && sourceReportResource.key !== sourceReportRequestKey
  const artifactLoading = Boolean(artifactRequestKey) && artifactResource.key !== artifactRequestKey
  const contentLoading = Boolean(contentRequestKey) && contentResource.key !== contentRequestKey
  const artifactScope = useMemo<ArtifactScope | undefined>(() => (
    selectedMarket && selection.companyKey && selection.reportId
      ? {
          market: selectedMarket,
          companyKey: selection.companyKey,
          reportId: selection.reportId,
          artifactType: reportType,
        }
      : undefined
  ), [reportType, selectedMarket, selection.companyKey, selection.reportId])
  const contentCacheKey = artifactScope && selection.artifactId
    ? `${artifactScope.market}\u0000${artifactScope.companyKey}\u0000${artifactScope.reportId}\u0000${artifactScope.artifactType}\u0000${selection.artifactId}`
    : ''
  const listError = marketResource.key === reportType && marketResource.error
    || companyResource.key === companyRequestKey && companyResource.error
    || sourceReportResource.key === sourceReportRequestKey && sourceReportResource.error
    || artifactResource.key === artifactRequestKey && artifactResource.error
    || null

  useEffect(() => {
    requestedRef.current = requested
  }, [requested])

  useEffect(() => () => {
    artifactPageControllerRef.current?.abort()
    artifactPageControllerRef.current = null
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchResearchMarkets(reportType, controller.signal)
      .then((response) => setMarketResource({
        key: reportType,
        data: orderedResearchMarkets(response.markets || []),
        error: null,
      }))
      .catch((error) => {
        if (!isAbortError(error)) setMarketResource({
          key: reportType,
          data: [],
          error: error instanceof Error ? error.message : '无法加载市场列表',
        })
      })
    return () => controller.abort()
  }, [reportType])

  useEffect(() => {
    if (!markets.length || selection.market) return
    const market = resolveInitialMarket(markets, { ...requestedRef.current, market: requestedMarket })
    if (market) dispatch({ type: 'select-market', market })
  }, [markets, requestedMarket, selection.market])

  useEffect(() => {
    if (!selectedMarket) return
    const controller = new AbortController()
    fetchResearchCompanies(selectedMarket, reportType, controller.signal)
      .then((response) => {
        const list = response.companies || []
        setCompanyResource({ key: selectedMarket, data: list, error: null })
        dispatch({
          type: 'select-company',
          companyKey: resolveInitialCompany(list, requestedRef.current, selectedMarket),
        })
      })
      .catch((error) => {
        if (!isAbortError(error)) setCompanyResource({
          key: selectedMarket,
          data: [],
          error: error instanceof Error ? error.message : '无法加载公司列表',
        })
      })
    return () => controller.abort()
  }, [reportType, selectedMarket])

  useEffect(() => {
    if (!selectedMarket || !selection.companyKey || !sourceReportRequestKey) return
    const selectedCompanyKey = selection.companyKey
    const controller = new AbortController()
    fetchSourceReports(
      selectedMarket,
      selectedCompanyKey,
      reportType,
      controller.signal,
      { deferArtifactIntegrity: true },
    )
      .then((response) => {
        const list = (response.reports || []).filter((report) => report.quality_status !== 'fail')
        setSourceReportResource({ key: sourceReportRequestKey, data: list, error: null })
        dispatch({ type: 'select-report', reportId: resolveInitialReport(list, requestedRef.current.reportId) })
      })
      .catch((error) => {
        if (!isAbortError(error)) setSourceReportResource({
          key: sourceReportRequestKey,
          data: [],
          error: error instanceof Error ? error.message : '无法加载源报告列表',
        })
      })
    return () => controller.abort()
  }, [reportType, selectedMarket, selection.companyKey, sourceReportRequestKey])

  useEffect(() => {
    if (!selectedMarket || !selection.companyKey || !selection.reportId || !artifactRequestKey) return
    const selectedMarketCode = selectedMarket
    const selectedCompanyKey = selection.companyKey
    const selectedReportId = selection.reportId
    const controller = new AbortController()
    artifactPageControllerRef.current?.abort()
    const requestedArtifactId = requestedRef.current.artifactId || undefined
    const legacyFilename = selectedMarketCode === 'CN' ? requestedRef.current.legacyResult || undefined : undefined
    const targeted = Boolean(requestedArtifactId || legacyFilename)

    async function loadInitialArtifacts() {
      let response = await fetchGeneratedArtifacts(
        selectedMarketCode,
        selectedCompanyKey,
        selectedReportId,
        reportType,
        controller.signal,
        {
          limit: 1,
          requestedArtifactId,
          legacyFilename,
        },
      )
      let list = responseArtifacts(response, selectedMarketCode)
      let restoredTarget = targeted && list.length > 0
      if (targeted && list.length === 0) {
        response = await fetchGeneratedArtifacts(
          selectedMarketCode,
          selectedCompanyKey,
          selectedReportId,
          reportType,
          controller.signal,
          { limit: 1 },
        )
        list = responseArtifacts(response, selectedMarketCode)
        restoredTarget = false
      }
      return { response, list, restoredTarget }
    }

    loadInitialArtifacts()
      .then(({ response, list, restoredTarget }) => {
        setArtifactResource({ key: artifactRequestKey, data: list, error: null })
        setArtifactPagination({
          key: artifactRequestKey,
          nextCursor: response.pagination?.next_cursor || null,
          targeted: restoredTarget,
          loading: false,
        })
        dispatch({ type: 'select-artifact', artifactId: resolveInitialArtifact(list, requestedRef.current) })
      })
      .catch((error) => {
        if (!isAbortError(error)) setArtifactResource({
          key: artifactRequestKey,
          data: [],
          error: error instanceof Error ? error.message : '无法加载生成结果列表',
        })
      })
    return () => controller.abort()
  }, [artifactRequestKey, reportType, selectedMarket, selection.companyKey, selection.reportId])

  useEffect(() => {
    if (!selection.artifactId || !contentRequestKey || !artifactScope || !contentCacheKey) return
    const selectedArtifactId = selection.artifactId
    const cached = contentCache.get(contentCacheKey)
    if (cached !== undefined) {
      let cancelled = false
      queueMicrotask(() => {
        if (!cancelled) setContentResource({ key: contentRequestKey, data: cached, error: null })
      })
      return () => { cancelled = true }
    }
    const controller = new AbortController()
    fetchArtifactContent(selectedArtifactId, artifactScope, controller.signal)
      .then((html) => {
        contentCache.set(contentCacheKey, html)
        setContentResource({ key: contentRequestKey, data: html, error: null })
      })
      .catch((error) => {
        if (!isAbortError(error)) setContentResource({
          key: contentRequestKey,
          data: '',
          error: error instanceof Error ? error.message : '报告内容加载失败',
        })
      })
    return () => controller.abort()
  }, [artifactScope, contentCache, contentCacheKey, contentRequestKey, selection.artifactId])

  const selectionSettled = Boolean(selectedMarket)
    && companyResource.key === companyRequestKey
    && (!selection.companyKey || (
      sourceReportResource.key === sourceReportRequestKey
      && (!selection.reportId || artifactResource.key === artifactRequestKey)
    ))

  useEffect(() => {
    if (!selectionSettled) return
    const next = applyResearchSelectionToSearchParams(searchParams, selection)
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true })
  }, [searchParams, selection, selectionSettled, setSearchParams])

  const selectedCompany = companies.find((company) => company.company_key === selection.companyKey)
  const selectedSourceReport = sourceReports.find((report) => report.report_id === selection.reportId)
  const selectedArtifact = artifacts.find((artifact) => artifact.artifact_id === selection.artifactId)
  const selectedMarketOption = markets.find((market) => market.market === selection.market)
  const selectedArtifactUrl = selectedArtifact && artifactScope
    ? artifactContentUrl(selectedArtifact.artifact_id, artifactScope)
    : ''
  const meta = REPORT_TYPE_META[reportType]
  const KickerIcon = meta.Icon
  const agentContext = buildResearchAgentContext({
    company: selectedCompany,
    sourceReport: selectedSourceReport,
    artifact: selectedArtifact,
    reportType,
    reportTitle: meta.label,
    pageTitle,
  })
  const selectedReportItem: ReportItem | undefined = selectedArtifact
    ? {
        filename: selectedArtifact.filename || `${selectedArtifact.artifact_id}.html`,
        url: selectedArtifactUrl,
        size: 0,
        mtime: selectedArtifact.created_at || '',
        market: selectedSourceReport?.research_identity.market,
        company_id: selectedSourceReport?.research_identity.company_id,
        filing_id: selectedSourceReport?.research_identity.filing_id,
        parse_run_id: selectedSourceReport?.research_identity.parse_run_id,
      }
    : undefined
  const reportSrcDoc = buildReportSrcDoc(reportHtml, selectedArtifactUrl)
  const updatedAt = selectedArtifact?.created_at
    ? new Date(selectedArtifact.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '--'
  const canDeleteReport = hasPermission('report.delete')
  const listLoading = companyLoading || sourceReportLoading || artifactLoading
  const canLoadMoreArtifacts = artifactPagination.key === artifactRequestKey
    && (artifactPagination.targeted || Boolean(artifactPagination.nextCursor))
  const hasAnalysisBaseline = Boolean(analysisBaselineId(selectedSourceReport))
  const degradedReasons = [...new Set([
    ...(selectedMarketOption?.degraded_reasons || []),
    ...(selectedCompany?.degraded_reasons || []),
    ...(selectedSourceReport?.degraded_reasons || []),
  ])]

  function replaceUrlSelection(next: ResearchSelection) {
    setSearchParams(applyResearchSelectionToSearchParams(searchParams, next), { replace: true })
  }

  function abortArtifactPagination() {
    artifactPageControllerRef.current?.abort()
    artifactPageControllerRef.current = null
    setArtifactPagination({ key: '', nextCursor: null, targeted: false, loading: false })
  }

  function selectMarket(market: ResearchSelection['market']) {
    const next = { market, companyKey: '', reportId: '', artifactId: '' }
    abortArtifactPagination()
    dispatch({ type: 'select-market', market })
    setContentResource({ key: '', data: '', error: null })
    setConfirmDelete(false)
    replaceUrlSelection(next)
  }

  function selectCompany(companyKey: string) {
    const next = { ...selection, companyKey, reportId: '', artifactId: '' }
    abortArtifactPagination()
    dispatch({ type: 'select-company', companyKey })
    setContentResource({ key: '', data: '', error: null })
    setConfirmDelete(false)
    replaceUrlSelection(next)
  }

  function selectSourceReport(reportId: string) {
    const next = { ...selection, reportId, artifactId: '' }
    abortArtifactPagination()
    dispatch({ type: 'select-report', reportId })
    setContentResource({ key: '', data: '', error: null })
    setConfirmDelete(false)
    replaceUrlSelection(next)
  }

  function selectArtifact(artifactId: string) {
    const next = { ...selection, artifactId }
    dispatch({ type: 'select-artifact', artifactId })
    setConfirmDelete(false)
    replaceUrlSelection(next)
  }

  async function loadMoreArtifacts() {
    if (
      !selectedMarket
      || !selection.companyKey
      || !selection.reportId
      || !artifactRequestKey
      || artifactPagination.key !== artifactRequestKey
      || artifactPagination.loading
      || !canLoadMoreArtifacts
    ) return
    const controller = new AbortController()
    artifactPageControllerRef.current?.abort()
    artifactPageControllerRef.current = controller
    const cursor = artifactPagination.targeted ? undefined : artifactPagination.nextCursor || undefined
    setArtifactPagination((current) => current.key === artifactRequestKey
      ? { ...current, loading: true }
      : current)
    try {
      const response = await fetchGeneratedArtifacts(
        selectedMarket,
        selection.companyKey,
        selection.reportId,
        reportType,
        controller.signal,
        { limit: ARTIFACT_PAGE_SIZE, cursor },
      )
      const nextPage = responseArtifacts(response, selectedMarket)
      setArtifactResource((current) => current.key === artifactRequestKey
        ? { ...current, data: uniqueArtifacts([...current.data, ...nextPage]), error: null }
        : current)
      setArtifactPagination((current) => current.key === artifactRequestKey
        ? {
            key: artifactRequestKey,
            nextCursor: response.pagination?.next_cursor || null,
            targeted: false,
            loading: false,
          }
        : current)
    } catch (error) {
      if (isAbortError(error)) return
      setArtifactPagination((current) => current.key === artifactRequestKey
        ? { ...current, loading: false }
        : current)
      toast({
        type: 'error',
        title: '结果列表加载失败',
        description: error instanceof Error ? error.message : '无法加载更多生成结果',
      })
    } finally {
      if (artifactPageControllerRef.current === controller) {
        artifactPageControllerRef.current = null
      }
    }
  }

  async function share() {
    const pageUrl = buildResearchShareUrl(window.location.origin, window.location.pathname, selection)
    if (await copyText(pageUrl)) {
      toast({ type: 'success', title: '链接已复制', description: '可以直接粘贴给协作者查看这份报告。' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动复制地址栏链接。' })
    }
  }

  async function downloadSelectedReport() {
    if (!selectedArtifact || !artifactScope) return
    try {
      const blob = await downloadArtifactContent(selectedArtifact.artifact_id, artifactScope)
      const href = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = href
      link.download = selectedArtifact.filename || `${selectedArtifact.artifact_id}.html`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(href), 1000)
    } catch (error) {
      toast({ type: 'error', title: '下载失败', description: error instanceof Error ? error.message : '下载失败' })
    }
  }

  async function deleteSelectedReport() {
    if (!selectedArtifact || !artifactScope) return
    setDeleting(true)
    try {
      await deleteGeneratedArtifact(selectedArtifact.artifact_id, artifactScope)
      if (contentCacheKey) contentCache.delete(contentCacheKey)
      const nextArtifacts = artifacts.filter((artifact) => artifact.artifact_id !== selectedArtifact.artifact_id)
      const nextArtifactId = nextArtifacts[0]?.artifact_id || ''
      setArtifactResource({ key: artifactRequestKey, data: nextArtifacts, error: null })
      dispatch({ type: 'select-artifact', artifactId: nextArtifactId })
      replaceUrlSelection({ ...selection, artifactId: nextArtifactId })
      setConfirmDelete(false)
      toast({ type: 'success', title: '报告已删除', description: selectedArtifact.filename || selectedArtifact.artifact_id })
    } catch (error) {
      toast({ type: 'error', title: '删除失败', description: error instanceof Error ? error.message : '删除失败' })
    } finally {
      setDeleting(false)
    }
  }

  if (marketLoading) {
    return (
      <PageWithAgentChat {...agentConfig}>
        <div className="flex items-center justify-center py-32">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <span className="ml-3 text-text-muted">加载市场列表...</span>
        </div>
      </PageWithAgentChat>
    )
  }

  if (listError && markets.length === 0) {
    return (
      <PageWithAgentChat {...agentConfig}>
        <div className="rounded-2xl border border-error/20 bg-error/5 p-6 text-center">
          <AlertCircle className="mx-auto mb-3 h-8 w-8 text-error" />
          <p className="text-base text-error">{listError}</p>
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
              <div className="secondary-title-row">
                <div className="secondary-kicker">
                  <KickerIcon className="h-3.5 w-3.5" />
                  {meta.english}
                </div>
                <h1 className="secondary-title">{pageTitle}</h1>
              </div>
              <p className="secondary-description">选择公司和报告版本，系统会以统一阅读样式展示 HTML 结果。</p>
            </div>
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center lg:flex-col lg:items-end">
              <div className="secondary-step-row">
                {meta.steps.map((step, index) => (
                  <span key={step} className={`secondary-step-chip ${index === 0 ? 'is-active' : ''}`}>{step}</span>
                ))}
              </div>
              {selectedCompany ? (
                <div className="secondary-company-card" title={`${selectedCompany.display_name} ${selectedCompany.display_code}`}>
                  <div className="secondary-company-icon">
                    <ChartCandlestick className="h-4 w-4" />
                  </div>
                  <div className="secondary-company-text">
                    <span className="secondary-company-name">{selectedCompany.display_name}</span>
                    <span className="secondary-company-code">{selectedCompany.display_code}</span>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </section>

        <div className="page-toolbar flex flex-col gap-4 px-4 py-4 sm:px-5 2xl:flex-row 2xl:flex-wrap 2xl:items-end 2xl:justify-between">
          <ReportSelector
            mode="research-universe"
            markets={markets}
            selectedMarket={selection.market}
            onSelectMarket={selectMarket}
            companies={companies}
            selectedCompanyKey={selection.companyKey}
            onSelectCompanyKey={selectCompany}
            sourceReports={sourceReports}
            selectedReportId={selection.reportId}
            onSelectReportId={selectSourceReport}
            artifacts={artifacts}
            selectedArtifactId={selection.artifactId}
            onSelectArtifactId={selectArtifact}
            reportType={reportType}
            loadingCompanies={companyLoading}
            loadingReports={sourceReportLoading}
            loadingArtifacts={artifactLoading}
            canLoadMoreArtifacts={canLoadMoreArtifacts}
            loadingMoreArtifacts={artifactPagination.loading}
            onRequestMoreArtifacts={loadMoreArtifacts}
          />
          <ReportToolbar
            selectedReportUrl={selectedArtifactUrl}
            canDeleteReport={canDeleteReport}
            confirmDelete={confirmDelete}
            deleting={deleting}
            onConfirmDeleteChange={setConfirmDelete}
            onShare={share}
            onDownload={downloadSelectedReport}
            onDelete={deleteSelectedReport}
          />
          {!selectedArtifact && selectedSourceReport ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-11 w-full min-w-0 px-3 sm:w-auto"
              leftIcon={<Share2 className="h-4 w-4" />}
              onClick={share}
            >
              分享
            </Button>
          ) : null}
        </div>

        {degradedReasons.length > 0 ? (
          <div className="border-x border-b border-warning/20 bg-warning/5 px-5 py-2 text-sm leading-6 text-warning" role="status">
            当前来源能力降级：{degradedReasons.join('；')}
          </div>
        ) : null}

        {listLoading ? (
          <div className="secondary-panel flex items-center justify-center px-5 py-20 text-text-muted" role="status">
            <Loader2 className="mr-3 h-6 w-6 animate-spin text-primary" />
            正在加载当前选择...
          </div>
        ) : listError ? (
          <div className="rounded-2xl border border-error/20 bg-error/5 p-6 text-center" role="alert">
            <AlertCircle className="mx-auto mb-3 h-8 w-8 text-error" />
            <p className="text-base text-error">数据加载失败：{listError}</p>
          </div>
        ) : !selectedCompany ? (
          <EmptyState
            icon={Building2}
            title={`${selection.market ? DISCLOSURE_MARKETS[selection.market].label : '当前市场'}暂无可用公司`}
            description="当前市场没有达到 parsed-ready 的公司，请选择其他市场。"
            size="lg"
          />
        ) : !selectedSourceReport ? (
          <EmptyState
            icon={FileText}
            title={`${selectedCompany.display_name} 暂无可用源报告`}
            description="该公司没有达到 parsed-ready 的源报告，不会自动回退到其他公司或最新报告。"
            size="lg"
          />
        ) : selectedArtifact ? (
          <ReportFrame
            selectedReportUrl={selectedArtifactUrl}
            selectedReport={selectedReportItem}
            reportSrcDoc={reportSrcDoc}
            contentLoading={contentLoading}
            iframeTitle={iframeTitle}
            updatedAt={updatedAt}
            accent={meta.accent}
            error={reportError}
            onRetry={() => {
              if (selectedArtifact && contentCacheKey) contentCache.delete(contentCacheKey)
              setRetryKey((key) => key + 1)
            }}
          />
        ) : (
          <ReportEmptyState
            selectedDir={selection.companyKey}
            hasReports={artifacts.length > 0}
            selectedReportUrl=""
            companyName={selectedCompany?.display_name || '该公司'}
            emptyTitle={emptyTitle}
            emptyDescription={downstreamEmptyDescription(reportType, hasAnalysisBaseline, emptyDescription)}
          />
        )}
      </div>
    </PageWithAgentChat>
  )
}
