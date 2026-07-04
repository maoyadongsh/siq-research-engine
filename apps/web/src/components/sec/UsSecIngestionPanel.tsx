import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Copy,
  Database,
  FileDown,
  FileText,
  FileUp,
  FolderOpen,
  Loader2,
  Network,
  PackageCheck,
  RefreshCw,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState, PageSection } from '@/components/page'
import { copyText } from '../../lib/clipboard'
import { openAuthenticatedSourceLink } from '../../lib/authenticatedSourceLinks'
import { downloadAuthenticatedFile, useAuthenticatedBlobUrl } from '../../lib/authenticatedFiles'
import {
  buildUsSecPackage,
  fetchUsSecCaseSet,
  fetchUsSecPackage,
  fetchUsSecPackageText,
  rebuildUsSecPackage,
  runUsSecCaseSetIngest,
  usSecPackageFileUrl,
  uploadUsSecFiles,
  waitForMarketReportJob,
  type UsSecCaseSetStatus,
  type UsSecIngestResponse,
  type UsSecPackageBuildResponse,
  type UsSecPackageDetail,
  type UsSecUploadResult,
} from '../../features/market-parsing/api'
import type { DownloadedPdf } from '../../lib/pdfTypes'
import { loadDownloadedReports as loadDownloadedReportsApi } from '../../features/pdf-parsing/api'
import { UsSecDownloadedReportsPanel } from './UsSecDownloadedReportsPanel'
import { UsSecRecentTasksPanel } from './UsSecRecentTasksPanel'
import {
  deriveUsSecArtifactManifest,
  deriveUsSecDownloadedRows,
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
  const [selectedTaskId, setSelectedTaskId] = useState('')
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

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const next = await fetchUsSecCaseSet()
      setStatus(next)
      setSelectedTaskId((current) => (current && next.items?.some((item) => String(item.package_path || '') === current) ? current : ''))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const applyPackageDetail = useCallback(async (detail: UsSecPackageDetail) => {
    setPackageDetail(detail)
    const firstSectionFile = detail.sections?.[0]?.file
    const nextMd = detail.preview?.default_markdown || (firstSectionFile ? `sections/${String(firstSectionFile)}` : '')
    setMarkdownFile(nextMd)
    if (detail.package_path && nextMd) {
      try {
        setMarkdownText(await fetchUsSecPackageText(detail.package_path, nextMd))
      } catch {
        setMarkdownText('')
      }
    } else {
      setMarkdownText('')
    }
  }, [])

  const loadPackage = useCallback(async (ticker: string) => {
    if (!ticker) return
    setPackageLoading(true)
    setError('')
    try {
      const detail = await fetchUsSecPackage(ticker)
      await applyPackageDetail(detail)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载证据包失败')
      setPackageDetail(null)
      setMarkdownText('')
    } finally {
      setPackageLoading(false)
    }
  }, [applyPackageDetail])

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
    () => deriveUsSecDownloadedRows(downloadedReports, status, busy),
    [busy, downloadedReports, status],
  )
  const recentTasks = useMemo(() => deriveUsSecRecentTasks(status), [status])
  const selectedTask = useMemo(
    () => recentTasks.find((task) => task.id === selectedTaskId) || null,
    [recentTasks, selectedTaskId],
  )
  const includeFail = true
  const packagePath = packageDetail?.package_path || ''
  const rawHtmlFile = packageDetail?.preview?.raw_html || 'raw/filing.htm'
  const rawHtmlUrl = packagePath ? usSecPackageFileUrl(packagePath, rawHtmlFile) : ''
  const rawHtmlBlobUrl = useAuthenticatedBlobUrl(rawHtmlUrl)
  const sections = packageDetail?.sections || []
  const metrics = packageDetail?.metrics || []
  const dimensionMetrics = packageDetail?.dimension_metrics || []
  const bridgeChecks = packageDetail?.bridge_checks?.checks || []
  const bridgeSummary = packageDetail?.bridge_checks?.summary || {}
  const displayTicker = String(packageDetail?.manifest?.ticker || selectedTask?.ticker || '')
  const artifactManifest = useMemo(() => deriveUsSecArtifactManifest(packageDetail), [packageDetail])
  const workflowSummary = useMemo(() => deriveUsSecWorkflowSummary(status, packageDetail), [packageDetail, status])
  const qualitySummary = useMemo(() => deriveUsSecQualitySummary(packageDetail), [packageDetail])

  const openFilePicker = useCallback(() => {
    fileInput.current?.click()
  }, [])

  const handleUpload = useCallback(async () => {
    const files = fileInput.current?.files
    if (!files || !files.length) {
      setError('请先选择文件')
      return
    }
    setUploading(true)
    setError('')
    setUploadMessage('')
    setUploadResults([])
    try {
      const form = new FormData()
      Array.from(files).forEach((file) => form.append('files', file))
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
        if (result.ok === false) throw new Error(String(result.stderr || result.stdout || 'US 证据包构建失败'))
        setLastOutput(result.stdout || result.stderr || 'US 证据包已生成')
        setSelectedDownloadPath(report.relativePath)
        setSelectedTaskId('')
        setPackageDetail(null)
        setMarkdownText('')
        await load()
        await loadDownloads(downloadQuery)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'US 证据包构建失败')
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
    setPackageDetail(null)
    setMarkdownFile('')
    setMarkdownText('')
    try {
      await loadPackage(task.ticker)
      setSelectedTaskId(task.id)
    } finally {
      setBusy('')
    }
  }, [loadPackage])

  const onRebuildTask = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`rebuild:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const response = await rebuildUsSecPackage(task.ticker)
      const result = response.job_id
        ? await waitForMarketReportJob<{ ok?: boolean; package?: UsSecPackageDetail; stdout?: string; stderr?: string }>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : ''))
      await load()
      if (selectedTaskId === task.id) await loadPackage(task.ticker)
    } catch (err) {
      setError(err instanceof Error ? err.message : '重建失败')
    } finally {
      setBusy('')
    }
  }, [load, loadPackage, selectedTaskId])

  const onImportTaskPostgres = useCallback(async (task: UsSecRecentTaskRow) => {
    setBusy(`postgres:${task.id}`)
    setError('')
    setLastOutput('')
    try {
      const response = await runUsSecCaseSetIngest({
        dry_run: false,
        postgres: true,
        milvus: false,
        ddl: true,
        include_fail: includeFail,
        tickers: task.ticker,
        batch_tag: 'us-sec-case-set-50',
      })
      const result = response.job_id
        ? await waitForMarketReportJob<UsSecIngestResponse>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : ''))
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '执行失败')
    } finally {
      setBusy('')
    }
  }, [includeFail, load])

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
            <span className="text-xs text-text-muted">{uploadMessage || '支持 PDF / HTML / XHTML / XML / XBRL / ZIP'}</span>
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

      <UsSecRecentTasksPanel
        tasks={recentTasks}
        selectedTaskId={selectedTaskId}
        loading={loading}
        busyAction={busy}
        onViewResult={onViewTask}
        onRebuild={onRebuildTask}
        onImportPostgres={onImportTaskPostgres}
        onRefresh={load}
      />

      {selectedTask ? (
        <>
          <PageSection
            title="数据管线"
            description="PostgreSQL 与 results 目录保存全量解析信息；Wiki 保留报告入口、公司级知识资产和轻量产物清单。"
            actions={(
              <div className="flex flex-wrap gap-2">
                <button onClick={() => void load()} disabled={loading} className="pdf-small-action inline-flex items-center gap-1">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  刷新状态
                </button>
                <button onClick={() => void onImportTaskPostgres(selectedTask)} disabled={!!busy} className="pdf-small-action primary inline-flex items-center gap-1">
                  {busy === `postgres:${selectedTask.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                  继续入库
                </button>
              </div>
            )}
          >
            <div className="pdf-pipeline-note mb-4">
              <Database className="h-4 w-4" />
              <div>
                Wiki 不复制全量 SEC 证据包；<code>artifact_manifest.json</code> 只记录核心文件路径和状态，原始 HTML、Markdown、结构化事实与入库脚本仍直接读取 package 与 results 目录。
              </div>
            </div>

            <div className="mb-5 flex items-start gap-1 overflow-x-auto pb-2">
              {workflowSummary.steps.map((step, index) => {
                const completed = step.status === 'ready'
                const active = index === workflowSummary.steps.findIndex((item) => item.status !== 'ready') || (workflowSummary.steps.every((item) => item.status === 'ready') && index === workflowSummary.steps.length - 1)
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

            <div className="hidden gap-3 md:grid md:grid-cols-3 xl:grid-cols-3">
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
                  <div className="mt-1 text-xs leading-5 text-text-muted">这些文件共同支撑入库、质量校验和证据溯源；Wiki 仅引用清单，不重复保存全量包。</div>
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
                <button type="button" className="pdf-small-action primary inline-flex items-center gap-1" onClick={() => void onViewTask(selectedTask)} disabled={!!busy}>
                  {busy === `view:${selectedTask.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}
                  导入 Wiki
                </button>
                <button type="button" className="pdf-small-action inline-flex items-center gap-1" onClick={() => void onViewTask(selectedTask)} disabled={!!busy}>
                  <Network className="h-4 w-4" />
                  生成 Wiki 语义层
                </button>
                <button type="button" className="pdf-small-action primary inline-flex items-center gap-1" onClick={() => void onImportTaskPostgres(selectedTask)} disabled={!!busy}>
                  {busy === `postgres:${selectedTask.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                  导入 PostgreSQL
                </button>
              </div>
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
                    onClick={() => rawHtmlUrl && openAuthenticatedSourceLink(rawHtmlUrl).catch(() => null)}
                    disabled={!rawHtmlUrl}
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
            <div className="apple-card rounded-[24px] p-4 sm:p-6">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-text">溯源视图</h3>
                  <p className="mt-1 text-sm text-text-muted">HTML 原文与 Wiki Markdown 对照，替代 A 股 PDF 原页溯源。</p>
                </div>
                <select value={markdownFile} onChange={(event) => void changeMarkdownFile(event.target.value)} disabled={packageLoading || !sections.length} className="h-9 max-w-xs rounded-md border border-border bg-white px-2 text-xs disabled:cursor-not-allowed disabled:bg-surface-soft">
                  {sections.map((section) => (
                    <option key={String(section.file)} value={`sections/${section.file}`}>
                      {String(section.section_id)} · {String(section.file)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-3 xl:grid-cols-2">
                {rawHtmlBlobUrl ? (
                  <iframe title="SEC 原始 HTML" src={rawHtmlBlobUrl} className="h-[520px] w-full rounded-md border border-border bg-white" />
                ) : (
                  <div className="flex h-[520px] items-center justify-center gap-2 rounded-md border border-border bg-white text-sm text-text-muted">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在加载 SEC 原始 HTML
                  </div>
                )}
                <div className="h-[520px] overflow-auto rounded-md border border-border bg-white p-4 text-sm leading-6 text-text">
                  {markdownText.split('\n').slice(0, 260).map((line, index) => {
                    if (line.startsWith('# ')) return <h3 key={index} className="mb-2 mt-3 text-base font-semibold">{line.replace(/^#\s+/, '')}</h3>
                    if (line.startsWith('## ')) return <h4 key={index} className="mb-1 mt-3 text-sm font-semibold">{line.replace(/^##\s+/, '')}</h4>
                    if (line.trim() === '---') return <hr key={index} className="my-3 border-border" />
                    return <p key={index} className={line.trim() ? 'mb-1 whitespace-pre-wrap' : 'h-3'}>{line}</p>
                  })}
                </div>
              </div>
            </div>
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
      ) : (
        <PageSection>
          <EmptyState
            icon={FolderOpen}
            title="选择最近任务后查看结果"
            description="选择一条已解析 SEC 任务后查看证据包、勾稽校验和入库状态。"
            className="rounded-[18px] border border-dashed border-border bg-bg/50"
            size="sm"
          />
        </PageSection>
      )}

      {error ? <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}
      {lastOutput ? <pre className="mt-3 max-h-56 overflow-auto rounded-md border border-border bg-slate-950 p-3 text-xs text-slate-100">{lastOutput}</pre> : null}
    </div>
  )
}
