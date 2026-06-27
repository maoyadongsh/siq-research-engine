import { useCallback, useEffect, useMemo, useState } from 'react'
import { Database, FileText, Loader2, PackageCheck, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import {
  fetchMarketPackageDetail,
  fetchMarketPackages,
  marketPackageFileUrl,
  runMarketPackageBuild,
  runMarketPackageImport,
  runMarketPackageVectorIngest,
  waitForMarketReportJob,
  type MarketCode,
  type MarketPackageActionResponse,
  type MarketPackageDetail,
  type MarketPackageSummary,
} from '../../lib/secApi'

function numberText(value: unknown): string {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0'
}

function statusClass(status?: string): string {
  if (status === 'pass') return 'bg-green-50 text-green-700 border-green-200'
  if (status === 'warning') return 'bg-blue-50 text-blue-700 border-blue-200'
  if (status === 'fail') return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-slate-50 text-slate-600 border-slate-200'
}

function StatTile({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md bg-surface-soft p-3 text-xs text-text-muted">
      <b className="block text-text">{label}</b>
      <span className="text-base font-semibold tabular-nums text-text">{numberText(value)}</span>
    </div>
  )
}

export function MarketEvidencePackagesPanel({ market }: { market: MarketCode }) {
  const [packages, setPackages] = useState<MarketPackageSummary[]>([])
  const [selectedPath, setSelectedPath] = useState('')
  const [detail, setDetail] = useState<MarketPackageDetail | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [output, setOutput] = useState('')
  const [buildSource, setBuildSource] = useState('')
  const [buildParserResult, setBuildParserResult] = useState('')
  const [buildMetadata, setBuildMetadata] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchMarketPackages(market, query)
      const rows = data.packages || []
      setPackages(rows)
      if (rows[0]?.package_path) setSelectedPath((current) => current || String(rows[0].package_path || ''))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载证据包失败')
    } finally {
      setLoading(false)
    }
  }, [market, query])

  const loadDetail = useCallback(async (packagePath: string) => {
    if (!packagePath) return
    setDetailLoading(true)
    setError('')
    try {
      setDetail(await fetchMarketPackageDetail(market, packagePath))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载证据包详情失败')
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [market])

  useEffect(() => {
    queueMicrotask(() => {
      void load()
    })
  }, [load])

  useEffect(() => {
    if (!selectedPath) return
    queueMicrotask(() => {
      void loadDetail(selectedPath)
    })
  }, [loadDetail, selectedPath])

  const selected = useMemo(
    () => packages.find((item) => item.package_path === selectedPath),
    [packages, selectedPath],
  )

  const sourceMap = detail?.source_map || []
  const metrics = detail?.metrics || []
  const tables = detail?.tables || []
  const paths = detail?.paths || selected?.paths || {}

  const runPostgres = useCallback(async () => {
    if (!selectedPath) return
    setBusy('postgres')
    setError('')
    setOutput('')
    try {
      const response = await runMarketPackageImport(market, selectedPath, true)
      const result = response.job_id
        ? await waitForMarketReportJob<MarketPackageActionResponse>(response.job_id)
        : response
      setOutput(result.stdout || result.stderr || `parse_run_id=${result.parse_run_id || ''}`)
      await loadDetail(selectedPath)
    } catch (err) {
      setError(err instanceof Error ? err.message : '入库失败')
    } finally {
      setBusy('')
    }
  }, [loadDetail, market, selectedPath])

  const runVectorDryRun = useCallback(async () => {
    if (!selectedPath) return
    setBusy('vector')
    setError('')
    setOutput('')
    try {
      const response = await runMarketPackageVectorIngest(market, selectedPath, true)
      const result = response.job_id
        ? await waitForMarketReportJob<MarketPackageActionResponse>(response.job_id)
        : response
      setOutput(JSON.stringify(result.summary || { stdout: result.stdout, stderr: result.stderr }, null, 2))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Milvus dry-run 失败')
    } finally {
      setBusy('')
    }
  }, [market, selectedPath])

  const runBuild = useCallback(async () => {
    if (!buildSource.trim()) {
      setError('source_path 不能为空')
      return
    }
    setBusy('build')
    setError('')
    setOutput('')
    try {
      const response = await runMarketPackageBuild(market, {
        source_path: buildSource.trim(),
        parser_result: buildParserResult.trim() || undefined,
        metadata_path: buildMetadata.trim() || undefined,
        force: true,
      })
      const result = response.job_id
        ? await waitForMarketReportJob<MarketPackageActionResponse>(response.job_id)
        : response
      setOutput(result.stdout || result.stderr || 'package built')
      await load()
      const builtPath = result.package?.package_path
      if (builtPath) setSelectedPath(String(builtPath))
    } catch (err) {
      setError(err instanceof Error ? err.message : '构建证据包失败')
    } finally {
      setBusy('')
    }
  }, [buildMetadata, buildParserResult, buildSource, load, market])

  return (
    <section className="rounded-lg border border-border bg-card p-4 shadow-sm sm:p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-primary">
            <PackageCheck className="h-4 w-4" />
            {market} Evidence Packages
          </div>
          <h2 className="mt-1 text-lg font-semibold text-text">统一证据包状态</h2>
          <p className="mt-1 text-sm text-text-muted">package、quality、evidence、PostgreSQL 与 Milvus chunk 状态入口。</p>
        </div>
        <button onClick={() => void load()} disabled={loading} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-white px-3 text-sm font-semibold text-text hover:bg-surface-soft disabled:opacity-60">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新
        </button>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[23rem_minmax(0,1fr)]">
        <aside className="rounded-lg border border-border bg-surface-soft p-4">
          <label className="flex h-10 items-center gap-2 rounded-md border border-border bg-white px-3 text-sm">
            <Search className="h-4 w-4 text-text-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void load()
              }}
              placeholder="ticker / filing_id"
              className="min-w-0 flex-1 bg-transparent outline-none"
            />
          </label>
          <div className="mt-3 max-h-[34rem] overflow-auto rounded-md border border-border bg-white">
            {packages.map((item) => {
              const active = item.package_path === selectedPath
              return (
                <button
                  key={item.package_path}
                  onClick={() => setSelectedPath(String(item.package_path || ''))}
                  className={`block w-full border-b border-border px-3 py-2 text-left text-xs last:border-0 ${active ? 'bg-primary/10' : 'hover:bg-surface-soft'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm font-semibold text-text">{item.ticker || '-'}</span>
                    <span className={`rounded-full border px-2 py-0.5 ${statusClass(item.quality_status)}`}>{item.quality_status || 'unknown'}</span>
                  </div>
                  <div className="mt-1 truncate text-text-muted">{item.company_name || item.filing_id}</div>
                  <div className="mt-1 text-[.72rem] text-text-muted">
                    {[item.country, item.document_format, item.fiscal_year || '-'].filter(Boolean).join(' / ')} / metrics {numberText(item.counts?.metrics)} / evidence {numberText(item.counts?.evidence)}
                  </div>
                </button>
              )
            })}
            {!packages.length && (
              <div className="px-3 py-8 text-center text-xs text-text-muted">{loading ? '加载中...' : '暂无证据包'}</div>
            )}
          </div>
        </aside>

        <div className="min-w-0 space-y-4">
          <div className="rounded-lg border border-border bg-white p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <div className="text-sm font-semibold text-text">Build Package</div>
                <p className="mt-1 text-xs text-text-muted">source_path / parser_result / metadata_path</p>
              </div>
              <button onClick={() => void runBuild()} disabled={!!busy || !buildSource.trim()} className="h-9 rounded-md bg-primary px-3 text-xs font-semibold text-white disabled:opacity-60">
                {busy === 'build' ? '构建中...' : 'Build'}
              </button>
            </div>
            <div className="mt-3 grid gap-2 xl:grid-cols-3">
              <input
                value={buildSource}
                onChange={(event) => setBuildSource(event.target.value)}
                placeholder="source_path"
                className="h-9 min-w-0 rounded-md border border-border bg-surface-soft px-3 text-xs outline-none focus:border-primary"
              />
              <input
                value={buildParserResult}
                onChange={(event) => setBuildParserResult(event.target.value)}
                placeholder="parser_result"
                className="h-9 min-w-0 rounded-md border border-border bg-surface-soft px-3 text-xs outline-none focus:border-primary"
              />
              <input
                value={buildMetadata}
                onChange={(event) => setBuildMetadata(event.target.value)}
                placeholder="metadata_path"
                className="h-9 min-w-0 rounded-md border border-border bg-surface-soft px-3 text-xs outline-none focus:border-primary"
              />
            </div>
          </div>

          <div className="rounded-lg border border-border bg-white p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-semibold text-text">
                  <FileText className="h-4 w-4 text-primary" />
                  {String(detail?.ticker || selected?.ticker || '-')} · {String(detail?.form || selected?.form || detail?.report_type || selected?.report_type || '-')}
                  {detailLoading && <Loader2 className="h-4 w-4 animate-spin text-text-muted" />}
                </div>
                <p className="mt-1 truncate text-xs text-text-muted">{selectedPath || '未选择 package'}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button onClick={() => void runVectorDryRun()} disabled={!!busy || !selectedPath} className="h-9 rounded-md border border-border bg-white px-3 text-xs font-semibold hover:bg-surface-soft disabled:opacity-60">
                  {busy === 'vector' ? '生成中...' : 'Milvus Dry Run'}
                </button>
                <button onClick={() => void runPostgres()} disabled={!!busy || !selectedPath} className="h-9 rounded-md bg-primary px-3 text-xs font-semibold text-white disabled:opacity-60">
                  {busy === 'postgres' ? '入库中...' : 'PostgreSQL'}
                </button>
              </div>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <StatTile label="Sections" value={detail?.counts?.sections} />
              <StatTile label="Tables" value={detail?.counts?.tables || tables.length} />
              <StatTile label="Raw Facts" value={detail?.counts?.raw_facts} />
              <StatTile label="Metrics" value={detail?.counts?.metrics || metrics.length} />
              <StatTile label="Evidence" value={detail?.counts?.evidence || sourceMap.length} />
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-text">
                <ShieldCheck className="h-4 w-4 text-primary" />
                Quality
              </div>
              <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-surface-soft p-3 text-xs leading-5 text-text">{JSON.stringify(detail?.quality || {}, null, 2)}</pre>
            </div>
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-text">
                <Database className="h-4 w-4 text-primary" />
                Package Files
              </div>
              <div className="mt-3 space-y-2 text-xs">
                {Object.entries(paths).map(([name, path]) => {
                  const file = path.replace(/^\/+/, '')
                  return (
                    <a
                      key={name}
                      href={selectedPath ? marketPackageFileUrl(market, selectedPath, file) : '#'}
                      target="_blank"
                      rel="noreferrer"
                      className="block truncate rounded-md border border-border bg-surface-soft px-3 py-2 text-primary hover:bg-white"
                    >
                      {name}: {file}
                    </a>
                  )
                })}
              </div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="text-sm font-semibold text-text">Evidence 路径</div>
              <div className="mt-3 max-h-80 overflow-auto rounded-md border border-border">
                {sourceMap.slice(0, 80).map((item, index) => (
                  <div key={String(item.evidence_id || index)} className="border-b border-border p-3 text-xs last:border-0">
                    <div className="font-mono text-[.72rem] text-text">{String(item.evidence_id || '')}</div>
                    <div className="mt-1 text-text-muted">{String(item.source_type || '')} · {String(item.local_path || item.target || '')}</div>
                  </div>
                ))}
                {!sourceMap.length && <div className="px-3 py-8 text-center text-xs text-text-muted">暂无 evidence</div>}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-white p-4">
              <div className="text-sm font-semibold text-text">指标样例</div>
              <div className="mt-3 max-h-80 overflow-auto rounded-md border border-border">
                {metrics.slice(0, 80).map((item, index) => (
                  <div key={String(item.metric_id || index)} className="border-b border-border p-3 text-xs last:border-0">
                    <div className="font-semibold text-text">{String(item.canonical_name || item.local_name || '')}</div>
                    <div className="mt-1 text-text-muted">{String(item.value ?? '')} {String(item.unit || item.currency || '')} · {String(item.period_key || '')}</div>
                    <div className="mt-1 font-mono text-[.72rem] text-text-muted">{String(item.evidence_id || '')}</div>
                  </div>
                ))}
                {!metrics.length && <div className="px-3 py-8 text-center text-xs text-text-muted">暂无指标</div>}
              </div>
            </div>
          </div>
        </div>
      </div>

      {error && <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {output && <pre className="mt-3 max-h-56 overflow-auto rounded-md border border-border bg-slate-950 p-3 text-xs text-slate-100">{output}</pre>}
    </section>
  )
}
