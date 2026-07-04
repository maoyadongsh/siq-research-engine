import { useCallback, useEffect, useMemo, useState } from 'react'
import { Database, ExternalLink, Loader2, RefreshCw, Search, UploadCloud } from 'lucide-react'
import {
  fetchMarketPackages,
  marketPackageFileUrl,
  type MarketCode,
  type MarketPackagesResponse,
} from '../../features/market-parsing/api'
import {
  runMarketPackageImportAction,
  runMarketPackageVectorDryRunAction,
} from '../../features/market-parsing/packageActions'
import { deriveMarketPackageRows, packagePrimaryFile } from '../../features/market-parsing/marketPackagesPanelModel'

export interface MarketEvidencePackagesPanelProps {
  market: MarketCode
}

export function MarketEvidencePackagesPanel({ market }: MarketEvidencePackagesPanelProps) {
  const [payload, setPayload] = useState<MarketPackagesResponse>({ market, packages: [] })
  const [query, setQuery] = useState('')
  const [busyPath, setBusyPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadPackages = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const next = await fetchMarketPackages(market, query)
      setPayload(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [market, query])

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => {
      if (!cancelled) void loadPackages()
    })
    return () => {
      cancelled = true
    }
  }, [loadPackages])

  const rows = useMemo(() => deriveMarketPackageRows(payload, busyPath), [payload, busyPath])

  const runImport = async (packagePath: string) => {
    setBusyPath(packagePath)
    setError('')
    setMessage('')
    try {
      const { output } = await runMarketPackageImportAction({ market, packagePath, ddl: true })
      setMessage(output || 'PostgreSQL 入库完成')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyPath('')
    }
  }

  const runVectorDryRun = async (packagePath: string) => {
    setBusyPath(packagePath)
    setError('')
    setMessage('')
    try {
      const { output } = await runMarketPackageVectorDryRunAction({ market, packagePath, dryRun: true })
      setMessage(output || '向量检索 dry-run 完成')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyPath('')
    }
  }

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-text">
            <Database className="h-4 w-4 text-primary" />
            Wiki 证据包
          </h3>
          <p className="mt-1 text-sm text-text-muted">读取 data/wiki/{market.toLowerCase()}/companies 下的报告包，关联 PostgreSQL 入库与检索证据。</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <label className="relative block min-w-[16rem]">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <input
              className="w-full rounded-xl border border-border bg-card px-9 py-2 text-sm text-text outline-none focus:border-primary"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void loadPackages()
              }}
              placeholder="股票代码 / 公司 / 报告"
            />
          </label>
          <button
            type="button"
            className="pdf-small-action inline-flex items-center gap-1"
            onClick={() => void loadPackages()}
            disabled={loading}
            title="刷新证据包列表"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新
          </button>
        </div>
      </div>

      {error ? <div className="mb-3 rounded-xl border border-error/20 bg-error/5 p-3 text-sm text-error">{error}</div> : null}
      {message ? <pre className="mb-3 max-h-48 overflow-auto rounded-xl border border-border bg-background p-3 text-xs text-text-muted">{message}</pre> : null}

      <div className="space-y-3">
        {rows.map((row) => {
          const packagePath = row.package_path || row.id
          const primaryFile = packagePrimaryFile(row)
          return (
            <div key={row.id} className="rounded-2xl border border-border bg-card p-4">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-text">{row.title}</div>
                  <div className="mt-1 text-xs text-text-muted">{row.summary || packagePath}</div>
                  <code className="mt-2 block break-all text-[11px] text-text-muted">{packagePath}</code>
                </div>
                <div className="flex flex-wrap gap-2">
                  <a
                    className="pdf-trace-btn inline-flex items-center gap-1"
                    href={marketPackageFileUrl(market, packagePath, primaryFile)}
                    target="_blank"
                    rel="noopener"
                    title="打开证据包主文件"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    打开
                  </a>
                  <button
                    type="button"
                    className="pdf-trace-btn inline-flex items-center gap-1"
                    onClick={() => void runImport(packagePath)}
                    disabled={!!busyPath}
                    title="导入 PostgreSQL"
                  >
                    {row.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
                    入库
                  </button>
                  <button
                    type="button"
                    className="pdf-trace-btn inline-flex items-center gap-1"
                    onClick={() => void runVectorDryRun(packagePath)}
                    disabled={!!busyPath}
                    title="生成检索 dry-run"
                  >
                    {row.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Database className="h-3.5 w-3.5" />}
                    检索
                  </button>
                </div>
              </div>
            </div>
          )
        })}
        {!loading && rows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-background p-4 text-sm text-text-muted">
            暂未发现 Wiki 证据包。
          </div>
        ) : null}
      </div>
    </div>
  )
}
