import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Database,
  FileDown,
  FileText,
  FileUp,
  Loader2,
  Network,
  PackageCheck,
  RefreshCw,
  SearchCheck,
  ShieldCheck,
  SplitSquareHorizontal,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  buildUsSecPackage,
  fetchUsSecCaseSet,
  fetchUsSecPackage,
  fetchUsSecPackageText,
  rebuildUsSecPackage,
  runUsSecCaseSetIngest,
  uploadUsSecFiles,
  usSecPackageFileUrl,
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
import { deriveUsSecDownloadedRows, type UsSecDownloadedRow } from '../../features/market-parsing/usSecWorkbench'

function numberText(value: unknown): string {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0'
}

function statusClass(status?: string): string {
  if (status === 'pass') return 'bg-green-50 text-green-700 border-green-200'
  if (status === 'warning') return 'bg-blue-50 text-blue-700 border-blue-200'
  if (status === 'fail') return 'bg-amber-50 text-amber-700 border-amber-200'
  if (status === 'uploaded') return 'bg-sky-50 text-sky-700 border-sky-200'
  return 'bg-slate-50 text-slate-600 border-slate-200'
}

function StatTile({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-text-muted">{label}</span>
        <span className="text-primary">{icon}</span>
      </div>
      <div className="mt-2 text-xl font-semibold tabular-nums text-text">{value}</div>
    </div>
  )
}

function CheckStatusPill({ status }: { status?: string }) {
  return <span className={`rounded-full border px-2 py-0.5 text-xs ${statusClass(status)}`}>{status || 'unknown'}</span>
}

export function UsSecIngestionPanel() {
  const [status, setStatus] = useState<UsSecCaseSetStatus | null>(null)
  const [selectedTicker, setSelectedTicker] = useState('AAPL')
  const [packageDetail, setPackageDetail] = useState<UsSecPackageDetail | null>(null)
  const [markdownFile, setMarkdownFile] = useState('')
  const [markdownText, setMarkdownText] = useState('')
  const [loading, setLoading] = useState(false)
  const [packageLoading, setPackageLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [downloadQuery, setDownloadQuery] = useState('')
  const [includeFail, setIncludeFail] = useState(true)
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
      if (!selectedTicker && next.items?.[0]?.ticker) setSelectedTicker(next.items[0].ticker)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [selectedTicker])

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

  useEffect(() => {
    if (!selectedTicker) return
    queueMicrotask(() => {
      void loadPackage(selectedTicker)
    })
  }, [loadPackage, selectedTicker])

  const selectedItem = useMemo(
    () => (status?.items || []).find((item) => item.ticker === selectedTicker),
    [selectedTicker, status?.items],
  )
  const selectedDownload = useMemo(
    () => downloadedReports.find((item) => item.relativePath === selectedDownloadPath) || downloadedReports[0] || null,
    [downloadedReports, selectedDownloadPath],
  )
  const ingestSummary = status?.ingest_report?.summary || {}
  const counts = status?.counts || {}
  const packagePath = packageDetail?.package_path || selectedItem?.package_path || ''
  const rawHtmlFile = packageDetail?.preview?.raw_html || 'raw/filing.htm'
  const rawHtmlUrl = packagePath ? usSecPackageFileUrl(packagePath, rawHtmlFile) : ''
  const sections = packageDetail?.sections || []
  const metrics = packageDetail?.metrics || []
  const dimensionMetrics = packageDetail?.dimension_metrics || []
  const bridgeChecks = packageDetail?.bridge_checks?.checks || []
  const bridgeSummary = packageDetail?.bridge_checks?.summary || {}
  const uploadCount = uploadResults.length
  const displayTicker = String(packageDetail?.manifest?.ticker || selectedTicker || '')
  const downloadedRows = useMemo(
    () => deriveUsSecDownloadedRows(downloadedReports, status, busy),
    [busy, downloadedReports, status],
  )

  const run = useCallback(async (mode: 'plan' | 'postgres') => {
    setBusy(mode)
    setError('')
    setLastOutput('')
    try {
      const response = await runUsSecCaseSetIngest({
        dry_run: mode === 'plan',
        postgres: mode === 'postgres',
        milvus: false,
        ddl: mode === 'postgres',
        include_fail: includeFail,
        tickers: selectedTicker,
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
  }, [includeFail, load, selectedTicker])

  const rebuild = useCallback(async () => {
    if (!selectedTicker) return
    setBusy('rebuild')
    setError('')
    setLastOutput('')
    try {
      const response = await rebuildUsSecPackage(selectedTicker)
      const result = response.job_id
        ? await waitForMarketReportJob<{ ok?: boolean; package?: UsSecPackageDetail; stdout?: string; stderr?: string }>(response.job_id)
        : response
      setLastOutput(result.stdout || result.stderr || (response.job_id ? `后台任务 ${response.job_id} 已完成` : ''))
      await load()
      await loadPackage(selectedTicker)
      await loadDownloads(downloadQuery)
    } catch (err) {
      setError(err instanceof Error ? err.message : '重建失败')
    } finally {
      setBusy('')
    }
  }, [downloadQuery, load, loadDownloads, loadPackage, selectedTicker])

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
  }, [downloadQuery, uploadCompanyName, uploadFilingDate, uploadFiscalYear, uploadPeriodEnd, uploadReportType, uploadTicker, loadDownloads])

  const openFilePicker = useCallback(() => {
    fileInput.current?.click()
  }, [])

  const onSelectDownloaded = useCallback(
    async (report: DownloadedPdf) => {
      setSelectedDownloadPath(report.relativePath)
      setError('')
      setBusy('download-select')
      try {
        const nextTicker = String(report.ticker || '').toUpperCase()
        if (nextTicker) {
          setSelectedTicker(nextTicker)
          if (nextTicker === selectedTicker) await loadPackage(nextTicker)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载失败')
      } finally {
        setBusy('')
      }
    },
    [loadPackage, selectedTicker],
  )

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
        if (result.package) await applyPackageDetail(result.package)
        await load()
        await loadDownloads(downloadQuery)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'US 证据包构建失败')
      } finally {
        setBusy('')
      }
    },
    [applyPackageDetail, downloadQuery, load, loadDownloads],
  )

  const onSelectDownloadedRow = useCallback(
    async (row: UsSecDownloadedRow) => {
      await onSelectDownloaded(row.report)
    },
    [onSelectDownloaded],
  )

  const onParseDownloadedRow = useCallback(
    async (row: UsSecDownloadedRow) => {
      await onBuildDownloadedPackage(row.report)
    },
    [onBuildDownloadedPackage],
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

      <section className="rounded-lg border border-border bg-card p-4 shadow-sm sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2 text-sm font-semibold text-primary">
            <Network className="h-4 w-4" />
            SEC 结构化解析与附件导入
          </div>
          <h2 className="mt-1 text-lg font-semibold text-text">美股 SEC 入库工作台</h2>
          <p className="mt-1 text-sm text-text-muted">
            已入库公司、US 下载目录、手动上传附件、Wiki 证据包与 PostgreSQL facts 在这里串联；向量化入口后续接回。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => void load()} disabled={loading} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-white px-3 text-sm font-semibold text-text hover:bg-surface-soft disabled:opacity-60">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
          <Link to="/parse" className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-white px-3 text-sm font-semibold text-text hover:bg-surface-soft">
            <FileText className="h-4 w-4" />
            仅在必要时打开通用 PDF
          </Link>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="公司数" value={numberText(status?.company_count)} icon={<ShieldCheck className="h-4 w-4" />} />
        <StatTile label="XBRL Facts" value={numberText(ingestSummary.xbrl_facts || counts.xbrl_fact_count)} icon={<Database className="h-4 w-4" />} />
        <StatTile label="标准指标" value={numberText(ingestSummary.normalized_metrics || counts.normalized_metric_count)} icon={<SearchCheck className="h-4 w-4" />} />
        <StatTile label="下载/上传" value={numberText(downloadedReports.length + uploadCount)} icon={<PackageCheck className="h-4 w-4" />} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="space-y-4">
          <div className="rounded-lg border border-border bg-surface-soft p-4">
            <div className="text-sm font-semibold text-text">上传入口</div>
            <input ref={fileInput} type="file" multiple accept=".pdf,.html,.htm,.xhtml,.xml,.xbrl,.zip" className="hidden" onChange={() => void handleUpload()} />
            <div className="mt-3 grid gap-2">
              <input value={uploadTicker} onChange={(e) => setUploadTicker(e.target.value)} placeholder="Ticker" className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary" />
              <input value={uploadCompanyName} onChange={(e) => setUploadCompanyName(e.target.value)} placeholder="Company name" className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary" />
              <select value={uploadReportType} onChange={(e) => setUploadReportType(e.target.value)} className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary">
                <option value="10-K">10-K / 年报</option>
                <option value="10-Q">10-Q / 季报</option>
                <option value="20-F">20-F</option>
                <option value="6-K">6-K</option>
                <option value="file">附件</option>
              </select>
              <input value={uploadFiscalYear} onChange={(e) => setUploadFiscalYear(e.target.value)} placeholder="Fiscal year" className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary" />
              <input value={uploadPeriodEnd} onChange={(e) => setUploadPeriodEnd(e.target.value)} placeholder="Period end (YYYY-MM-DD)" className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary" />
              <input value={uploadFilingDate} onChange={(e) => setUploadFilingDate(e.target.value)} placeholder="Filing date (YYYY-MM-DD)" className="h-9 rounded-md border border-border bg-white px-3 text-xs outline-none focus:border-primary" />
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button onClick={handleUpload} disabled={uploading} className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-xs font-semibold text-white disabled:opacity-60">
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
                发送到 US 目录
              </button>
              <span className="text-xs text-text-muted">{uploadMessage || '支持 PDF / HTML / XHTML / XML / XBRL / ZIP'}</span>
            </div>
            {uploadResults.length ? (
              <div className="mt-3 rounded-md border border-border bg-white p-3 text-xs text-text-muted">
                {uploadResults.map((item) => (
                  <div key={item.saved_path} className="flex items-center justify-between gap-2 py-1">
                    <span className="truncate">{item.file_name}</span>
                    <span className="shrink-0">{numberText(item.size_bytes)} bytes</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </aside>

        <div className="min-w-0 space-y-4">
          <div className="rounded-lg border border-border bg-white p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-text">
                  <FileText className="h-4 w-4 text-primary" />
                  {displayTicker || '-'} 证据包
                  {packageLoading && <Loader2 className="h-4 w-4 animate-spin text-text-muted" />}
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  {String(packageDetail?.manifest?.company_name || selectedItem?.company_name || '')} · {String(packageDetail?.manifest?.form || '10-K')} · {String(packageDetail?.manifest?.period_end || selectedItem?.period_end || '')}
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <button onClick={() => void rebuild()} disabled={!!busy || !selectedTicker} className="h-9 rounded-md border border-border bg-white px-3 text-xs font-semibold hover:bg-surface-soft disabled:opacity-60">
                  {busy === 'rebuild' ? '生成中...' : '生成 Wiki'}
                </button>
                <button onClick={() => void run('plan')} disabled={!!busy} className="h-9 rounded-md border border-border bg-white px-3 text-xs font-semibold hover:bg-surface-soft disabled:opacity-60">
                  {busy === 'plan' ? '规划中...' : 'Dry Run'}
                </button>
                <button onClick={() => void run('postgres')} disabled={!!busy} className="h-9 rounded-md border border-primary/30 bg-primary/10 px-3 text-xs font-semibold text-primary disabled:opacity-60">
                  {busy === 'postgres' ? '入库中...' : 'PostgreSQL'}
                </button>
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-5">
              <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted"><b className="block text-text">Sections</b>{numberText(packageDetail?.counts?.sections)}</div>
              <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted"><b className="block text-text">Tables</b>{numberText(packageDetail?.counts?.tables)}</div>
              <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted"><b className="block text-text">Metrics</b>{numberText(packageDetail?.counts?.metrics)}</div>
              <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted"><b className="block text-text">Evidence</b>{numberText(packageDetail?.counts?.evidence)}</div>
              <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted"><b className="block text-text">Dimensions</b>{numberText(packageDetail?.counts?.dimension_metrics)}</div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <CheckStatusPill status={selectedDownload?.reportFamily === 'annual' ? 'pass' : selectedDownload ? 'warning' : undefined} />
              <span className="text-xs text-text-muted">选中下载文件：{selectedDownload?.filename || '无'}</span>
            </div>

            <label className="mt-3 flex items-center gap-2 text-xs text-text-muted">
              <input type="checkbox" checked={includeFail} onChange={(event) => setIncludeFail(event.target.checked)} />
              包含质量失败包，作为低 quality_rank 召回候选
            </label>
          </div>

          <div className="rounded-lg border border-border bg-white p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="text-sm font-semibold text-text">财务勾稽校验</div>
                <p className="mt-1 text-xs text-text-muted">三大表公式按 consolidated 口径校验；带 XBRL dimensions 的子公司、分部、被投资方附注事实单独标注，不混入合并口径硬勾稽。</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <CheckStatusPill status={packageDetail?.bridge_checks?.overall_status} />
                <span className="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted">pass {numberText(bridgeSummary.pass)}</span>
                <span className="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted">warning {numberText(bridgeSummary.warning)}</span>
                <span className="rounded-full border border-border px-2 py-0.5 text-xs text-text-muted">fail {numberText(bridgeSummary.fail)}</span>
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
                  {!bridgeChecks.length && (
                    <tr><td className="px-3 py-6 text-center text-text-muted" colSpan={6}>暂无勾稽校验结果</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-text">
                  <SplitSquareHorizontal className="h-4 w-4 text-primary" />
                  原始报告 / Wiki Markdown 对照
                </div>
                <p className="mt-1 text-xs text-text-muted">左侧为 SEC HTML 原文，右侧为证据包已渲染 Markdown；切换 section 可检查附注、MD&A、财务报表等上下文。</p>
              </div>
              <select
                value={markdownFile}
                onChange={(event) => void changeMarkdownFile(event.target.value)}
                className="h-9 max-w-xs rounded-md border border-border bg-white px-2 text-xs"
              >
                {sections.map((section) => (
                  <option key={String(section.file)} value={`sections/${section.file}`}>
                    {String(section.section_id)} · {String(section.file)}
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-3 grid gap-3 xl:grid-cols-2">
              <iframe title="SEC 原始 HTML" src={rawHtmlUrl} className="h-[520px] w-full rounded-md border border-border bg-white" />
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

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="text-sm font-semibold text-text">指标证据样例</div>
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
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="text-sm font-semibold text-text">主体 / 子公司 / 分部维度</div>
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

          {selectedDownload ? (
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold text-text">
                    <FileDown className="h-4 w-4 text-primary" />
                    {selectedDownload.ticker || selectedDownload.companyName || selectedDownload.filename}
                  </div>
                  <p className="mt-1 text-xs text-text-muted">
                    {selectedDownload.companyName || selectedDownload.company} · {selectedDownload.form || selectedDownload.reportType || selectedDownload.category} · {selectedDownload.relativePath}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button onClick={() => void onSelectDownloaded(selectedDownload)} disabled={busy === 'download-select'} className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-white px-3 text-xs font-semibold hover:bg-surface-soft disabled:opacity-60">
                    {busy === 'download-select' ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    设为当前
                  </button>
                  <a href={selectedDownload.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-white px-3 text-xs font-semibold hover:bg-surface-soft">
                    <FileText className="h-4 w-4" />
                    打开源文件
                  </a>
                  <button onClick={() => void onBuildDownloadedPackage(selectedDownload)} disabled={busy === selectedDownload.relativePath} className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-xs font-semibold text-white disabled:opacity-60">
                    {busy === selectedDownload.relativePath ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}
                    生成证据包
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {error && <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {lastOutput && <pre className="mt-3 max-h-56 overflow-auto rounded-md border border-border bg-slate-950 p-3 text-xs text-slate-100">{lastOutput}</pre>}
      </section>
    </div>
  )
}
