import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Brain,
  Copy,
  Database,
  FileDown,
  FileText,
  FileUp,
  Loader2,
  Network,
  PackageCheck,
  RefreshCw,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageSection } from '@/components/page'
import { copyText } from '../../lib/clipboard'
import { downloadAuthenticatedFile, useAuthenticatedReadingHtmlUrl } from '../../lib/authenticatedFiles'
import {
  buildUsSecPackage,
  fetchMarketDocumentFullStatus,
  fetchUsSecCaseSet,
  fetchUsSecPackage,
  fetchUsSecPackageByPath,
  fetchUsSecPackageText,
  rebuildUsSecPackage,
  runMarketDocumentFullImport,
  runUsSecCaseSetIngest,
  usSecPackageFileUrl,
  uploadUsSecFiles,
  waitForMarketReportJob,
  type MarketDocumentFullPostgresStatus,
  type MarketDocumentFullImportResponse,
  type UsSecCaseSetStatus,
  type UsSecIngestResponse,
  type UsSecPackageBuildResponse,
  type UsSecPackageDetail,
  type UsSecUploadResult,
} from '../../features/market-parsing/api'
import { validateUsSecUploadFiles } from '../../features/market-parsing/uploadFiles'
import type { DownloadedPdf } from '../../lib/pdfTypes'
import { loadDownloadedReports as loadDownloadedReportsApi } from '../../features/pdf-parsing/api'
import { UsSecDownloadedReportsPanel } from './UsSecDownloadedReportsPanel'
import { UsSecRecentTasksPanel } from './UsSecRecentTasksPanel'
import { UsSecSourceWorkbench } from './UsSecSourceWorkbench'
import {
  deriveUsSecArtifactManifest,
  deriveUsSecDocumentFullImportPath,
  deriveUsSecDownloadedRows,
  deriveUsSecPackageRebuildRequest,
  deriveUsSecQualitySummary,
  deriveUsSecRecentTasks,
  deriveUsSecWorkflowSummary,
  type UsSecDownloadedRow,
  type UsSecRecentTaskRow,
} from '../../features/market-parsing/usSecWorkbench'

function numberText(value: unknown): string {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0'
}

function statusClass(status?: string): string {
  if (status === 'pass' || status === 'ready' || status === 'postgres_ready' || status === 'package_ready') return 'secondary-status-success'
  if (status === 'warning' || status === 'fail' || status === 'failed') return 'secondary-status-warning'
  if (status === 'building' || status === 'pending' || status === 'unknown') return 'secondary-status-info'
  return ''
}

function CheckStatusPill({ status }: { status?: string }) {
  return <span className={`secondary-status ${statusClass(status)}`}>{status || 'unknown'}</span>
}

export function UsSecIngestionPanel() {
  const [status, setStatus] = useState<UsSecCaseSetStatus | null>(null)
  const [documentFullPostgres, setDocumentFullPostgres] = useState<Record<string, MarketDocumentFullPostgresStatus | undefined>>({})
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const selectedTaskIdRef = useRef('')
  const packageLoadRequestRef = useRef(0)
  const [packageDetail, setPackageDetail] = useState<UsSecPackageDetail | null>(null)
  const [markdownFile, setMarkdownFile] = useState('')
  const [markdownText, setMarkdownText] = useState('')
  const [loading, setLoading] = useState(false)
  const [packageLoading, setPackageLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [downloadQuery, setDownloadQuery] = useState('')
  const [lastOutput, setLastOutput] = useState('')
  const [downloadedReports, setDownloadedReports] = useState<DownloadedPdf[]>([])
  const [downloadedLoading, setDownloadedLoading] = useState(false)
  const [selectedDownloadPath, setSelectedDownloadPath] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadResults, setUploadResults] = useState<UsSecUploadResult[]>([])
  const [uploadTicker, setUploadTicker] = useState('')
  const [uploadCompanyName, setUploadCompanyName] = useState('')
  const [uploadReportType, setUploadReportType] = useState('10-K')
  const [uploadFiscalYear, setUploadFiscalYear] = useState('')
  const [uploadPeriodEnd, setUploadPeriodEnd] = useState('')
  const [uploadFilingDate, setUploadFilingDate] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  const loadDocumentFullPostgresStatuses = useCallback(async (next: UsSecCaseSetStatus) => {
    const tasks = deriveUsSecRecentTasks(next)
    const paths = Array.from(new Set(tasks.map((task) => task.documentFullPath).filter(Boolean)))
    if (!paths.length) {
      setDocumentFullPostgres({})
      return
    }
    const entries = await Promise.all(paths.map(async (path) => {
      try {
        const statusPayload = await fetchMarketDocumentFullStatus('US', { documentFullPath: path })
        return [path, statusPayload.markets?.US?.postgres] as const
      } catch {
        return [path, {
          status: 'unknown',
          message: 'document_full status 查询失败，PostgreSQL 状态不可确认',
        } satisfies MarketDocumentFullPostgresStatus] as const
      }
    }))
    setDocumentFullPostgres(Object.fromEntries(entries))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const next = await fetchUsSecCaseSet()
      setStatus(next)
      setSelectedTaskId((current) => {
        const selected = current && next.items?.some((item) => String(item.package_path || '') === current) ? current : ''
        selectedTaskIdRef.current = selected
        return selected
      })
      await loadDocumentFullPostgresStatuses(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [loadDocumentFullPostgresStatuses])

  const loadPackage = useCallback(async ({ ticker, packagePath }: { ticker?: string; packagePath?: string }) => {
    if (!ticker && !packagePath) return
    const requestId = ++packageLoadRequestRef.current
    setPackageLoading(true)
    setError('')
    try {
      const detail = packagePath
        ? await fetchUsSecPackageByPath(packagePath)
        : await fetchUsSecPackage(ticker || '')
      const firstSectionFile = detail.sections?.[0]?.file
      const nextMarkdownFile = detail.preview?.default_markdown || (firstSectionFile ? `sections/${String(firstSectionFile)}` : '')
      let nextMarkdownText = ''
      if (detail.package_path && nextMarkdownFile) {
        try {
          nextMarkdownText = await fetchUsSecPackageText(detail.package_path, nextMarkdownFile)
        } catch {
          nextMarkdownText = ''
        }
      }
      if (packageLoadRequestRef.current !== requestId) return
      if (packagePath && selectedTaskIdRef.current !== packagePath) return
      setPackageDetail(detail)
      setMarkdownFile(nextMarkdownFile)
      setMarkdownText(nextMarkdownText)
    } catch (err) {
      if (packageLoadRequestRef.current !== requestId) return
      setError(err instanceof Error ? err.message : '加载解析产物包失败')
      setPackageDetail(null)
      setMarkdownFile('')
      setMarkdownText('')
    } finally {
      if (packageLoadRequestRef.current === requestId) setPackageLoading(false)
    }
  }, [])

  const loadDownloads = useCallback(async (text: string) => {
    setDownloadedLoading(true)
    try {
      const d = await loadDownloadedReportsApi(text, 'US')
      const reports = d.reports || []
      setDownloadedReports(reports)
      setSelectedDownloadPath((current) => {
        if (current && reports.some((item) => item.relativePath === current)) return current
        return reports[0]?.relativePath || ''
      })
    } catch {
      setDownloadedReports([])
      setSelectedDownloadPath('')
    } finally {
      setDownloadedLoading(false)
    }
  }, [])

  useEffect(() => {
    queueMicrotask(() => {
      void load()
      void loadDownloads('')
    })
  }, [load, loadDownloads])

  const downloadedRows = useMemo(
    () => deriveUsSecDownloadedRows(downloadedReports, status, busy, documentFullPostgres),
    [busy, documentFullPostgres, downloadedReports, status],
  )
  const recentTasks = useMemo(() => deriveUsSecRecentTasks(status, documentFullPostgres), [documentFullPostgres, status])
  const selectedTask = useMemo(
    () => recentTasks.find((task) => task.id === selectedTaskId) || null,
    [recentTasks, selectedTaskId],
  )
  const includeFail = true
  const packagePath = packageDetail?.package_path || ''
  const rawHtmlFile = packageDetail?.preview?.raw_html || 'raw/filing.htm'
  const rawHtmlUrl = packagePath ? usSecPackageFileUrl(packagePath, rawHtmlFile) : ''
  const rawHtmlBlobUrl = useAuthenticatedReadingHtmlUrl(rawHtmlUrl)
  const sections = packageDetail?.sections || []
  const tables = packageDetail?.tables || []
  const metrics = packageDetail?.metrics || []
  const dimensionMetrics = packageDetail?.dimension_metrics || []
  const bridgeChecks = packageDetail?.bridge_checks?.checks || []
  const bridgeSummary = packageDetail?.bridge_checks?.summary || {}
  const displayTicker = String(packageDetail?.manifest?.ticker || selectedTask?.ticker || '')
  const artifactManifest = useMemo(() => deriveUsSecArtifactManifest(packageDetail), [packageDetail])
  const selectedDocumentFullPath = useMemo(
    () => selectedTask ? deriveUsSecDocumentFullImportPath(selectedTask, packageDetail) || selectedTask.documentFullPath : '',
    [packageDetail, selectedTask],
  )
  const selectedPostgresStatus = selectedDocumentFullPath ? documentFullPostgres[selectedDocumentFullPath] : undefined
  const workflowSummary = useMemo(
    () => deriveUsSecWorkflowSummary(status, packageDetail, selectedPostgresStatus, {
      documentFullPath: selectedDocumentFullPath,
      busyAction: busy,
      taskId: selectedTask?.id,
    }),
    [busy, packageDetail, selectedDocumentFullPath, selectedPostgresStatus, selectedTask?.id, status],
  )
  const qualitySummary = useMemo(() => deriveUsSecQualitySummary(packageDetail), [packageDetail])
  const wikiIngestAction = workflowSummary.actions.find((action) => action.key === 'wiki')
  const semanticIngestAction = workflowSummary.actions.find((action) => action.key === 'semantic')
  const postgresIngestAction = workflowSummary.actions.find((action) => action.key === 'postgres')
  const runAllDisabledReasonId = 'us-sec-run-all-disabled-reason'
  const postgresDisabledReasonId = 'us-sec-postgres-disabled-reason'

  const openFilePicker = useCallback(() => {
    fileInput.current?.click()
  }, [])

  const handleUpload = useCallback(async () => {
    const files = fileInput.current?.files
    if (!files || !files.length) {
      setError('请先选择文件')
      return
    }
    const validation = validateUsSecUploadFiles(files)
    if (validation.error) {
      setError(validation.error)
      setUploadMessage('')
      setUploadResults([])
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    setUploading(true)
    setError('')
    setUploadMessage('')
    setUploadResults([])
    try {
      const form = new FormData()
      validation.files.forEach((file) => form.append('files', file))
      if (uploadTicker.trim()) form.append('ticker', uploadTicker.trim().toUpperCase())
      if (uploadCompanyName.trim()) form.append('company_name', uploadCompanyName.trim())
      if (uploadReportType.trim()) form.append('report_type', uploadReportType.trim())
      if (uploadFiscalYear.trim()) form.append('fiscal_year', uploadFiscalYear.trim())
      if (uploadPeriodEnd.trim()) form.append('period_end', uploadPeriodEnd.trim())
      if (uploadFilingDate.trim()) form.append('filing_date', uploadFilingDate.trim())
      const result = await uploadUsSecFiles(form)
      setUploadResults(result.files || [])
      setUploadMessage(`已上传 ${result.count || result.files?.length || 0} 个文件`)
      await loadDownloads(downloadQuery)
      const first = result.files?.[0]?.relative_path
      if (first) setSelectedDownloadPath(first)
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }, [downloadQuery, loadDownloads, uploadCompanyName, uploadFilingDate, uploadFiscalYear, uploadPeriodEnd, uploadReportType, uploadTicker])

  const onSelectDownloaded = useCallback(async (report: DownloadedPdf) => {
    setSelectedDownloadPath(report.relativePath)
  }, [])

  const onBuildDownloadedPackage = useCallback(
    async (report: DownloadedPdf) => {
      setBusy(report.relativePath)
      setError('')
      setLastOutput('')
      try {
        const response = await buildUsSecPackage({ download_relative_path: report.relativePath, force: true })
        const result = response.job_id
          ? await waitForMarketReportJob<UsSecPackageBuildResponse>(response.job_id, { timeoutMs: 15 * 60 * 1000 })
          : response
        if (result.ok === false) throw new Error(String(result.stderr || result.stdout || 'US 解析产物包构建失败'))
        setLastOutput(result.stdout || result.stderr || 'US 解析产物包已生成')
        setSelectedDownloadPath(report.relativePath)
        selectedTaskIdRef.current = ''
        packageLoadRequestRef.current += 1
        setSelectedTaskId('')
        setPackageDetail(null)
        setMarkdownFile('')
        setMarkdownText('')
        await load()
        await loadDownloads(downloadQuery)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'US 解析产物包构建失败')
      } finally {
        setBusy('')
      }
    },
    [downloadQuery, load, loadDownloads],
  )

  const onSelectDownloadedRow = useCallback(async (row: UsSecDownloadedRow) => {
    await onSelectDownloaded(row.report)
  }, [onSelectDownloaded])

  const onParseDownloadedRow = useCallback(async (row: UsSecDownloadedRow) => {
    await onBuildDownloadedPackage(row.report)
  }, [onBuildDownloadedPackage])

  const onViewTask = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`view:${task.id}`)
    setError('')
    selectedTaskIdRef.current = task.id
    packageLoadRequestRef.current += 1
    setPackageDetail(null)
    setMarkdownFile('')
    setMarkdownText('')
    try {
      setSelectedTaskId(task.id)
      await loadPackage({ packagePath: task.packagePath, ticker: task.ticker })
    } finally {
      setBusy((current) => current === `view:${task.id}` ? '' : current)
    }
  }, [loadPackage])

  const onRebuildTask = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`rebuild:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const rebuildRequest = deriveUsSecPackageRebuildRequest(task, packageDetail)
      const response = rebuildRequest
        ? await buildUsSecPackage(rebuildRequest)
        : await rebuildUsSecPackage(task.ticker)
      const result = response.job_id
        ? await waitForMarketReportJob<{ ok?: boolean; package?: UsSecPackageDetail; stdout?: string; stderr?: string }>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : ''))
      await load()
      if (selectedTaskIdRef.current === task.id) await loadPackage({ packagePath: task.packagePath, ticker: task.ticker })
    } catch (err) {
      setError(err instanceof Error ? err.message : '重建失败')
    } finally {
      setBusy((current) => current === `rebuild:${task.id}` ? '' : current)
    }
  }, [load, loadPackage, packageDetail])

  const onImportTaskPostgres = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`postgres:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const documentFullPath = deriveUsSecDocumentFullImportPath(task, packageDetail)
      if (!documentFullPath) {
        throw new Error('缺少 SEC parser result document_full.json 路径，请先刷新结果包')
      }
      const response = await runMarketDocumentFullImport('US', documentFullPath, true, false)
      const result = response.job_id
        ? await waitForMarketReportJob<MarketDocumentFullImportResponse>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : ''))
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '执行失败')
    } finally {
      setBusy('')
    }
  }, [load, packageDetail])

  const onBuildTaskWiki = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`wiki:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const response = await runUsSecCaseSetIngest({
        dry_run: false,
        postgres: false,
        semantic: false,
        ddl: false,
        include_fail: includeFail,
        tickers: task.ticker,
        package_path: task.packagePath,
        batch_tag: 'us-sec-case-set-50',
      })
      const result = response.job_id
        ? await waitForMarketReportJob<UsSecIngestResponse>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : 'US SEC LLM-Wiki入库完成'))
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'LLM-Wiki入库失败')
    } finally {
      setBusy('')
    }
  }, [includeFail, load])

  const onBuildTaskSemantic = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`semantic:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const response = await runUsSecCaseSetIngest({
        dry_run: false,
        postgres: false,
        semantic: true,
        ddl: false,
        include_fail: includeFail,
        tickers: task.ticker,
        package_path: task.packagePath,
        batch_tag: 'us-sec-case-set-50',
      })
      const result = response.job_id
        ? await waitForMarketReportJob<UsSecIngestResponse>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : 'US SEC Wiki语义增强入库完成'))
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Wiki语义增强入库失败')
    } finally {
      setBusy('')
    }
  }, [includeFail, load])

  const onContinueTaskPipeline = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`remaining:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const wikiResponse = await runUsSecCaseSetIngest({
        dry_run: false,
        postgres: false,
        semantic: false,
        ddl: false,
        include_fail: includeFail,
        tickers: task.ticker,
        package_path: task.packagePath,
        batch_tag: 'us-sec-case-set-50',
      })
      const wiki = wikiResponse.job_id
        ? await waitForMarketReportJob<UsSecIngestResponse>(wikiResponse.job_id)
        : wikiResponse
      const semanticResponse = await runUsSecCaseSetIngest({
        dry_run: false,
        postgres: false,
        semantic: true,
        ddl: false,
        include_fail: includeFail,
        tickers: task.ticker,
        package_path: task.packagePath,
        batch_tag: 'us-sec-case-set-50',
      })
      const semantic = semanticResponse.job_id
        ? await waitForMarketReportJob<UsSecIngestResponse>(semanticResponse.job_id)
        : semanticResponse
      const documentFullPath = deriveUsSecDocumentFullImportPath(task, packageDetail)
      if (!documentFullPath) {
        throw new Error('缺少 SEC parser result document_full.json 路径，请先刷新结果包')
      }
      const response = await runMarketDocumentFullImport('US', documentFullPath, true, false)
      const postgres = response.job_id
        ? await waitForMarketReportJob<MarketDocumentFullImportResponse>(response.job_id)
        : response
      setLastOutput([
        'LLM-Wiki入库',
        wiki.stdout || wiki.stderr || (wikiResponse.job_id ? `后台任务 ${wikiResponse.job_id} 已完成` : 'US SEC LLM-Wiki入库完成'),
        '',
        'Wiki语义增强入库',
        semantic.stdout || semantic.stderr || (semanticResponse.job_id ? `后台任务 ${semanticResponse.job_id} 已完成` : 'US SEC Wiki语义增强入库完成'),
        '',
        'PostgreSQL入库',
        postgres.stdout || postgres.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : 'US SEC PostgreSQL入库完成'),
      ].join('\n'))
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '数据管线执行失败')
    } finally {
      setBusy('')
    }
  }, [includeFail, load, packageDetail])

  const changeMarkdownFile = useCallback(async (file: string) => {
    if (!packagePath || !file) return
    setMarkdownFile(file)
    setPackageLoading(true)
    try {
      setMarkdownText(await fetchUsSecPackageText(packagePath, file))
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取 Markdown 失败')
    } finally {
      setPackageLoading(false)
    }
  }, [packagePath])

  const recentTasksPanel = (
    <UsSecRecentTasksPanel
      tasks={recentTasks}
      selectedTaskId={selectedTaskId}
      loading={loading}
      busyAction={busy}
      onViewResult={onViewTask}
      onRebuild={onRebuildTask}
      onRefresh={load}
    />
  )

  return (
    <div className="space-y-4">
      <UsSecDownloadedReportsPanel
        rows={downloadedRows}
        query={downloadQuery}
        loading={downloadedLoading}
        busyPath={busy}
        selectedPath={selectedDownloadPath}
        onQueryChange={(value) => {
          setDownloadQuery(value)
          void loadDownloads(value)
        }}
        onRefresh={() => loadDownloads(downloadQuery)}
        onSelect={onSelectDownloadedRow}
        onParse={onParseDownloadedRow}
        onUploadClick={openFilePicker}
      />

      <section className="surface-panel">
        <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-4 sm:px-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <h2 className="text-lg font-bold text-text sm:text-xl">上传附件</h2>
            <p className="mt-1 text-sm leading-6 text-text-muted">用于 SEC 附件、补充 HTML/XBRL 文件或临时样本入库；主链路仍建议从已下载财报启动。</p>
          </div>
          <div className="shrink-0">
            <Link to="/parse?market=US" className="pdf-small-action inline-flex items-center gap-1">
              <FileText className="h-4 w-4" />
              美股 PDF 兼容入口
            </Link>
          </div>
        </div>
        <div className="p-4 sm:p-5">
          <input ref={fileInput} type="file" multiple accept=".pdf,.html,.htm,.xhtml,.xml,.xbrl,.zip" className="hidden" onChange={() => void handleUpload()} />
          <div className="grid gap-2 md:grid-cols-3">
            <input value={uploadTicker} onChange={(e) => setUploadTicker(e.target.value)} placeholder="Ticker" className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary" />
            <input value={uploadCompanyName} onChange={(e) => setUploadCompanyName(e.target.value)} placeholder="Company name" className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary" />
            <select value={uploadReportType} onChange={(e) => setUploadReportType(e.target.value)} className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary">
              <option value="10-K">10-K / 年报</option>
              <option value="10-Q">10-Q / 季报</option>
              <option value="20-F">20-F</option>
              <option value="6-K">6-K</option>
              <option value="file">附件</option>
            </select>
            <input value={uploadFiscalYear} onChange={(e) => setUploadFiscalYear(e.target.value)} placeholder="Fiscal year" className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary" />
            <input value={uploadPeriodEnd} onChange={(e) => setUploadPeriodEnd(e.target.value)} placeholder="Period end (YYYY-MM-DD)" className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary" />
            <input value={uploadFilingDate} onChange={(e) => setUploadFilingDate(e.target.value)} placeholder="Filing date (YYYY-MM-DD)" className="h-10 rounded-[var(--radius-control)] border border-border bg-white px-3 text-sm outline-none focus:border-primary" />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button onClick={handleUpload} disabled={uploading} className="pdf-small-action primary inline-flex items-center gap-1">
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
              发送到 US 目录
            </button>
            <span className="text-xs text-text-muted">{uploadMessage || '支持 PDF / HTML / XHTML / XML / XBRL / ZIP；最多 5 个，单个 100 MB，总计 200 MB'}</span>
          </div>
          {uploadResults.length ? (
            <div className="mt-3 rounded-2xl border border-border bg-card p-3 text-xs text-text-muted">
              {uploadResults.map((item) => (
                <div key={item.saved_path} className="flex items-center justify-between gap-2 py-1">
                  <span className="truncate">{item.file_name}</span>
                  <span className="shrink-0">{numberText(item.size_bytes)} bytes</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      {selectedTask ? null : recentTasksPanel}

      {selectedTask ? (
        <>
          <PageSection
            title="数据管线"
            description="SEC HTML/iXBRL 解析产物生成结构化结果包，LLM-Wiki、Wiki语义增强和 PostgreSQL 入库继续读取同一套证据产物。"
            actions={(
              <div className="flex flex-wrap gap-2">
                <button onClick={() => void load()} disabled={loading} className="pdf-small-action inline-flex items-center gap-1">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  刷新状态
                </button>
                <button
                  onClick={() => void onContinueTaskPipeline(selectedTask)}
                  disabled={workflowSummary.runAll.disabled}
                  title={workflowSummary.runAll.disabledReason}
                  aria-describedby={workflowSummary.runAll.disabledReason ? runAllDisabledReasonId : undefined}
                  className="pdf-small-action primary inline-flex items-center gap-1"
                >
                  {workflowSummary.runAll.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                  {workflowSummary.runAll.busy ? workflowSummary.runAll.loadingLabel : workflowSummary.runAll.label}
                </button>
              </div>
            )}
          >
            <div className="pdf-pipeline-note mb-4">
              <Database className="h-4 w-4" />
              <div>
                当前链路为解析产物、LLM-Wiki、Wiki语义增强、PostgreSQL；PostgreSQL 和 Wiki语义增强都读取同一套 SEC HTML/iXBRL 解析产物。
              </div>
            </div>
            {workflowSummary.runAll.disabledReason ? (
              <div id={runAllDisabledReasonId} className="mb-4 text-xs leading-5 text-text-muted">
                {workflowSummary.runAll.disabledReason}
              </div>
            ) : null}

            <div className="pdf-pipeline-note mb-4">
              <Network className="h-4 w-4" />
              <div>
                Wiki语义增强使用当前项目设置中的模型，可切换本地或云端大模型；只更新 Wiki 语义资产，不触发向量入库。
              </div>
            </div>

            <div className="mb-5 flex items-start gap-1 overflow-x-auto pb-2">
              {workflowSummary.steps.map((step, index) => {
                const completed = step.status === 'ready'
                const active = index === workflowSummary.activeStepIndex
                const isLast = index === workflowSummary.steps.length - 1
                return (
                  <div key={step.label} className="relative flex min-w-[5.5rem] flex-1 flex-col items-center px-1">
                    {!isLast ? <div className={`absolute left-1/2 top-3.5 h-0.5 w-full ${completed ? 'bg-primary/40' : 'bg-border'}`} /> : null}
                    <div className={`relative z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 text-xs font-bold ${completed ? 'border-green-600 bg-green-600 text-white' : active ? 'border-primary bg-primary text-white shadow-md shadow-primary/25' : 'border-border bg-card text-text-muted'}`}>
                      {completed ? '✓' : index + 1}
                    </div>
                    <div className={`mt-2 text-center text-xs font-semibold ${active ? 'text-text' : 'text-text-muted'}`}>{step.label}</div>
                    <div className="mt-0.5 max-w-[8rem] text-center text-[11px] leading-tight text-text-muted line-clamp-2">{step.description}</div>
                  </div>
                )
              })}
            </div>

            <div className="hidden gap-3 md:grid md:grid-cols-2 xl:grid-cols-4">
              {workflowSummary.cards.map((card) => (
                <div key={card.label} className="rounded-2xl border border-border bg-card p-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold text-text">{card.label}</span>
                    <span className={`secondary-status ${statusClass(card.status)}`}>{card.status}</span>
                  </div>
                  <p className="mt-2 break-all text-sm leading-6 text-text-muted">{card.description}</p>
                </div>
              ))}
            </div>

            <div className="mt-4 rounded-2xl border border-border bg-card p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold text-text">核心解析产物清单</div>
                  <div className="mt-1 text-xs leading-5 text-text-muted">这些文件共同组成解析产物包，支撑入库、质量校验、勾稽校验和证据溯源。</div>
                </div>
                <span className="secondary-status secondary-status-info">{artifactManifest.readyCount}/{artifactManifest.total}</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {artifactManifest.chips.map((chip) => (
                  <span key={chip.name} className={`secondary-status ${chip.ready ? 'secondary-status-success' : ''}`}>{chip.name}</span>
                ))}
              </div>
              <div className="pdf-preflight-list mt-3">
                {artifactManifest.checks.map((check) => (
                  <div key={check.label} className={`pdf-preflight-item ${check.status === 'missing' ? 'warn' : ''}`}>
                    <span className="pdf-preflight-dot" />
                    <div>
                      <div className="pdf-preflight-title">{check.label} · {check.status}</div>
                      <div className="pdf-preflight-message">{check.description}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="button" className="pdf-small-action primary inline-flex items-center gap-1" onClick={() => void onRebuildTask(selectedTask)} disabled={!!busy}>
                  {busy === `rebuild:${selectedTask.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}
                  刷新结果包
                </button>
                <button type="button" className="pdf-small-action primary inline-flex items-center gap-1" onClick={() => void onBuildTaskWiki(selectedTask)} disabled={wikiIngestAction?.disabled ?? !!busy}>
                  {wikiIngestAction?.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
                  {wikiIngestAction?.busy ? wikiIngestAction.loadingLabel : (wikiIngestAction?.label || 'LLM-Wiki入库')}
                </button>
                <button type="button" className="pdf-small-action primary inline-flex items-center gap-1" onClick={() => void onBuildTaskSemantic(selectedTask)} disabled={semanticIngestAction?.disabled ?? !!busy}>
                  {semanticIngestAction?.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Network className="h-4 w-4" />}
                  {semanticIngestAction?.busy ? semanticIngestAction.loadingLabel : (semanticIngestAction?.label || 'Wiki语义增强入库')}
                </button>
                <button
                  type="button"
                  className="pdf-small-action primary inline-flex items-center gap-1"
                  onClick={() => void onImportTaskPostgres(selectedTask)}
                  disabled={postgresIngestAction?.disabled ?? !!busy}
                  title={postgresIngestAction?.disabledReason}
                  aria-describedby={postgresIngestAction?.disabledReason ? postgresDisabledReasonId : undefined}
                >
                  {postgresIngestAction?.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                  {postgresIngestAction?.busy ? postgresIngestAction.loadingLabel : (postgresIngestAction?.label || 'PostgreSQL入库')}
                </button>
              </div>
              {postgresIngestAction?.disabledReason ? (
                <div id={postgresDisabledReasonId} className="mt-3 text-xs leading-5 text-text-muted">
                  {postgresIngestAction.disabledReason}
                </div>
              ) : null}
            </div>
          </PageSection>

          <PageSection title="解析结果" description="Markdown 原文、核心证据文件与结构化结果。">
            <div className="apple-card rounded-[24px] p-4 sm:p-6">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-text">Markdown 结果</h3>
                  <p className="text-xs text-text-muted">{selectedTask.companyName} · {selectedTask.form} · {selectedTask.periodEnd}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className="pdf-small-action inline-flex items-center gap-1" onClick={() => void copyText(markdownText || '')}>
                    <Copy className="h-4 w-4" />
                    复制全文
                  </button>
                  <button type="button" className="pdf-small-action inline-flex items-center gap-1" onClick={() => markdownFile && downloadAuthenticatedFile(usSecPackageFileUrl(packagePath, markdownFile), `${displayTicker || 'us-sec'}.md`).catch(() => null)} disabled={!packagePath || !markdownFile}>
                    <FileDown className="h-4 w-4" />
                    下载 Markdown
                  </button>
                  <button
                    type="button"
                    className="pdf-small-action primary inline-flex items-center gap-1"
                    onClick={() => rawHtmlBlobUrl && window.open(rawHtmlBlobUrl, '_blank', 'noopener,noreferrer')}
                    disabled={!rawHtmlBlobUrl}
                  >
                    <FileText className="h-4 w-4" />
                    打开原始 HTML
                  </button>
                </div>
              </div>
              <div className="pdf-markdown-body">
                {packageLoading ? (
                  <div className="flex items-center gap-2 text-text-muted">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在加载 Markdown 内容
                  </div>
                ) : markdownText.split(/\r?\n/).filter(Boolean).length ? markdownText.split(/\r?\n/).map((line, index) => (
                  <div key={index + 1} className="pdf-markdown-line">
                    <span className="pdf-markdown-line-number">{index + 1}</span>
                    <span>{line || ' '}</span>
                  </div>
                )) : (
                  <div className="flex items-center gap-2 text-text-muted">
                    <FileText className="h-4 w-4" />
                    暂无 Markdown 内容
                  </div>
                )}
              </div>
            </div>
          </PageSection>

          <PageSection title="解析质量报告" description="核心章节、结构化指标、缺失章节与勾稽摘要。">
            <div className="apple-card rounded-[24px] p-4 sm:p-6">
              <h3 className="mb-3 text-base font-semibold text-text">质量摘要</h3>
              <div className="pdf-quality-grid">
                {qualitySummary.tiles.map((tile) => (
                  <div key={tile.label}>
                    <strong>{tile.value}</strong>
                    <span>{tile.label}</span>
                  </div>
                ))}
              </div>
              <div className="pdf-quality-section">
                <div className="pdf-quality-section-title">缺失核心章节</div>
                <div className="pdf-chip-row">
                  {qualitySummary.missingCoreSections.length ? qualitySummary.missingCoreSections.map((item) => <span key={item} className="pdf-chip pdf-chip-missing">{item}</span>) : <span className="text-text-muted text-sm">未发现缺失核心章节</span>}
                </div>
              </div>
              <div className="pdf-quality-section">
                <div className="pdf-quality-section-title">财务勾稽摘要</div>
                <ul className="list-disc pl-5 text-sm text-text">
                  <li>状态：{qualitySummary.bridgeStatus || 'unknown'}</li>
                  <li>通过：{qualitySummary.bridgeCounts.pass}</li>
                  <li>警告：{qualitySummary.bridgeCounts.warning}</li>
                  <li>失败：{qualitySummary.bridgeCounts.fail}</li>
                  <li>跳过：{qualitySummary.bridgeCounts.skipped}</li>
                </ul>
              </div>
            </div>
          </PageSection>

          <PageSection title="HTML/iXBRL 可视化溯源" description="左侧查看 SEC 原始 HTML，右侧查看渲染后的 Markdown section 与表格上下文。">
            <UsSecSourceWorkbench
              packagePath={packagePath}
              rawHtmlBlobUrl={rawHtmlBlobUrl}
              sections={sections}
              tables={tables}
              markdownFile={markdownFile}
              markdownText={markdownText}
              packageLoading={packageLoading}
              onMarkdownFileChange={changeMarkdownFile}
            />
          </PageSection>

          <PageSection title="财务勾稽校验" description="三大表按 consolidated 口径校验；dimensions 相关附注单独标注。">
            <div className="apple-card rounded-[24px] p-4 sm:p-6">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-text">勾稽明细</h3>
                  <p className="mt-1 text-sm text-text-muted">带 XBRL dimensions 的子公司、分部、被投资方附注事实不混入 consolidated 硬勾稽。</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <CheckStatusPill status={packageDetail?.bridge_checks?.overall_status} />
                  <span className="secondary-status secondary-status-info">pass {numberText(bridgeSummary.pass)}</span>
                  <span className="secondary-status secondary-status-info">warning {numberText(bridgeSummary.warning)}</span>
                  <span className="secondary-status secondary-status-info">fail {numberText(bridgeSummary.fail)}</span>
                </div>
              </div>
              <div className="mt-3 max-h-72 overflow-auto rounded-md border border-border">
                <table className="w-full min-w-[760px] text-left text-xs">
                  <thead className="sticky top-0 bg-surface-soft text-text-muted">
                    <tr>
                      <th className="px-3 py-2">规则</th>
                      <th className="px-3 py-2">期间</th>
                      <th className="px-3 py-2">状态</th>
                      <th className="px-3 py-2">差异</th>
                      <th className="px-3 py-2">容差</th>
                      <th className="px-3 py-2">原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bridgeChecks.slice(0, 80).map((check, index) => (
                      <tr key={`${String(check.rule_id)}-${index}`} className="border-t border-border">
                        <td className="px-3 py-2">
                          <div className="font-semibold text-text">{String(check.rule_name || check.rule_id || '')}</div>
                          <div className="mt-0.5 text-[.72rem] text-text-muted">{String(check.rule_id || '')}</div>
                        </td>
                        <td className="px-3 py-2 tabular-nums">{String(check.period || '')}</td>
                        <td className="px-3 py-2"><CheckStatusPill status={String(check.status || '')} /></td>
                        <td className="px-3 py-2 tabular-nums">{String(check.diff ?? '')}</td>
                        <td className="px-3 py-2 tabular-nums">{String(check.tolerance ?? '')}</td>
                        <td className="px-3 py-2 text-text-muted">{String(check.reason || '')}</td>
                      </tr>
                    ))}
                    {!bridgeChecks.length ? (
                      <tr><td className="px-3 py-6 text-center text-text-muted" colSpan={6}>暂无勾稽校验结果</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          </PageSection>

          <PageSection title="指标证据样例" description="标准指标与维度事实样例，用于核查 concept、period 与 dimension。">
            <div className="grid gap-4 xl:grid-cols-2">
              <div className="apple-card rounded-[24px] p-4 sm:p-6">
                <h3 className="text-base font-semibold text-text">指标证据样例</h3>
                <div className="mt-3 max-h-72 overflow-auto rounded-md border border-border">
                  <table className="w-full min-w-[680px] text-left text-xs">
                    <thead className="sticky top-0 bg-surface-soft text-text-muted">
                      <tr>
                        <th className="px-3 py-2">指标</th>
                        <th className="px-3 py-2">值</th>
                        <th className="px-3 py-2">期间</th>
                        <th className="px-3 py-2">Concept</th>
                      </tr>
                    </thead>
                    <tbody>
                      {metrics.slice(0, 80).map((metric, index) => (
                        <tr key={String(metric.metric_id || index)} className="border-t border-border">
                          <td className="px-3 py-2 font-semibold text-text">{String(metric.canonical_name || '')}</td>
                          <td className="px-3 py-2 tabular-nums">{String(metric.value || '')} {String(metric.unit || '')}</td>
                          <td className="px-3 py-2">{String(metric.period_key || '')}</td>
                          <td className="px-3 py-2 text-text-muted">{String(metric.concept || '')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="apple-card rounded-[24px] p-4 sm:p-6">
                <h3 className="text-base font-semibold text-text">主体 / 子公司 / 分部维度</h3>
                <div className="mt-3 max-h-72 overflow-auto rounded-md border border-border">
                  {(dimensionMetrics.length ? dimensionMetrics : metrics.slice(0, 20)).map((metric, index) => (
                    <div key={String(metric.metric_id || index)} className="border-b border-border p-3 text-xs last:border-0">
                      <div className="font-semibold text-text">{String(metric.canonical_name || metric.label || '')}</div>
                      <div className="mt-1 text-text-muted">{String(metric.concept || '')} · {String(metric.period_key || '')}</div>
                      <pre className="mt-2 overflow-auto rounded bg-surface-soft p-2 text-[.72rem] leading-5">{JSON.stringify(metric.dimensions || {}, null, 2)}</pre>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </PageSection>
        </>
      ) : null}

      {error ? <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}
      {lastOutput ? <pre className="mt-3 max-h-56 overflow-auto rounded-md border border-border bg-slate-950 p-3 text-xs text-slate-100">{lastOutput}</pre> : null}

      {selectedTask ? recentTasksPanel : null}
    </div>
  )
}
