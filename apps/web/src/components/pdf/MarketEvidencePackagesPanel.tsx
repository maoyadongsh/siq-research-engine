import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Database, ExternalLink, Loader2, RefreshCw, Search, ShieldCheck, UploadCloud } from 'lucide-react'
import {
  fetchMarketPackages,
  marketPackageFileUrl,
  type MarketCode,
  type MarketPackageQualityGates,
  type MarketPackagesResponse,
} from '../../features/market-parsing/api'
import {
  runMarketPackageImportAction,
  runMarketPackageVectorDryRunAction,
} from '../../features/market-parsing/packageActions'
import { deriveMarketPackageRows, packagePrimaryFile, type MarketPackageRow } from '../../features/market-parsing/marketPackagesPanelModel'

export interface MarketEvidencePackagesPanelProps {
  market: MarketCode
}

function qualityTone(status?: string) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'pass') return 'secondary-status-success'
  if (normalized === 'fail' || normalized === 'error') return 'secondary-status-error'
  if (normalized === 'warning') return 'secondary-status-warning'
  return 'secondary-status-info'
}

function percentText(value?: number | null) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

function countWarnings(gates?: MarketPackageQualityGates) {
  return (gates?.critical_warnings?.length || 0) + (gates?.parser_warnings?.length || 0) + (gates?.rule_warnings?.length || 0)
}

function requiredStatementText(gates?: MarketPackageQualityGates) {
  const status = gates?.required_statement_status || {}
  const values = Object.values(status)
  if (!values.length) return '-'
  const present = values.filter((value) => ['present', 'pass', 'ok'].includes(String(value).toLowerCase())).length
  return `${present}/${values.length}`
}

function forceConfirmed(gates: MarketPackageQualityGates | undefined, actionLabel: string, blockedKey: 'import_blocked' | 'vector_ingest_blocked') {
  if (!gates?.[blockedKey]) return false
  const reasons = gates.block_reasons?.length ? gates.block_reasons.join('\n- ') : gates.overall_status || 'unknown'
  const forceAllowed =
    gates.force_allowed === false
      ? '否'
      : gates.force_allowed === true
        ? '是'
        : '未声明（按旧合同兼容处理）'
  const message = [
    '质量门禁未通过。',
    `Gate reason:\n- ${reasons}`,
    `force_allowed: ${forceAllowed}`,
    '审计后果：确认后将以 force=true 提交，后端应记录可审计动作；force 不会改变原始 gate 结果，也不会消除上述风险原因。',
  ].join('\n\n')

  if (gates.force_allowed === false) {
    window.alert(`${message}\n\n当前质量门禁不允许 force，操作已取消。`)
    return false
  }

  return window.confirm(`${message}\n\n确认仍要${actionLabel}吗？`)
}

export function MarketEvidencePackagesPanel({ market }: MarketEvidencePackagesPanelProps) {
  const [payload, setPayload] = useState<MarketPackagesResponse>({ market, packages: [] })
  const [draftQuery, setDraftQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [busyPath, setBusyPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const requestAbortRef = useRef<AbortController | null>(null)

  const loadPackages = useCallback(async (query: string) => {
    requestAbortRef.current?.abort()
    const controller = new AbortController()
    requestAbortRef.current = controller
    setLoading(true)
    setError('')
    try {
      const next = await fetchMarketPackages(market, query, controller.signal)
      if (controller.signal.aborted) return
      setPayload(next)
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (requestAbortRef.current === controller) {
        requestAbortRef.current = null
        setLoading(false)
      }
    }
  }, [market])

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (active) void loadPackages(submittedQuery)
    })
    return () => {
      active = false
      requestAbortRef.current?.abort()
    }
  }, [loadPackages, submittedQuery])

  const submitQuery = useCallback(() => {
    const nextQuery = draftQuery.trim()
    if (nextQuery === submittedQuery) {
      void loadPackages(nextQuery)
      return
    }
    setSubmittedQuery(nextQuery)
  }, [draftQuery, loadPackages, submittedQuery])

  const rows = useMemo(() => deriveMarketPackageRows(payload, busyPath), [payload, busyPath])

  const runImport = async (row: MarketPackageRow) => {
    const packagePath = row.package_path || row.id
    const force = forceConfirmed(row.quality_gates, '导入 PostgreSQL', 'import_blocked')
    if (row.quality_gates?.import_blocked && !force) return
    setBusyPath(packagePath)
    setError('')
    setMessage('')
    try {
      const { output } = await runMarketPackageImportAction({ market, packagePath, ddl: true, force })
      setMessage(output || 'PostgreSQL 入库完成')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyPath('')
    }
  }

  const runVectorDryRun = async (row: MarketPackageRow) => {
    const packagePath = row.package_path || row.id
    const force = forceConfirmed(row.quality_gates, '生成检索 dry-run', 'vector_ingest_blocked')
    if (row.quality_gates?.vector_ingest_blocked && !force) return
    setBusyPath(packagePath)
    setError('')
    setMessage('')
    try {
      const { output } = await runMarketPackageVectorDryRunAction({ market, packagePath, dryRun: true, force })
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
              value={draftQuery}
              onChange={(event) => setDraftQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') submitQuery()
              }}
              placeholder="股票代码 / 公司 / 报告"
            />
          </label>
          <button
            type="button"
            className="pdf-small-action inline-flex items-center gap-1"
            onClick={submitQuery}
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
          const gates = row.quality_gates
          const warningCount = countWarnings(gates)
          return (
            <div key={row.id} className="rounded-2xl border border-border bg-card p-4">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-text">{row.title}</div>
                  <div className="mt-1 text-xs text-text-muted">{row.summary || packagePath}</div>
                  <code className="mt-2 block break-all text-[11px] text-text-muted">{packagePath}</code>
                  {gates ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      <span className={`secondary-status ${qualityTone(gates.overall_status)}`}>
                        {gates.overall_status === 'pass' ? <ShieldCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
                        质量 {gates.overall_status || 'unknown'}
                      </span>
                      <span className="secondary-status secondary-status-info">证据 {percentText(gates.evidence_coverage_ratio)}</span>
                      <span className="secondary-status secondary-status-info">报表 {requiredStatementText(gates)}</span>
                      <span className={`secondary-status ${gates.artifact_hash_status === 'ok' ? 'secondary-status-success' : 'secondary-status-warning'}`}>
                        hash {gates.artifact_hash_status || 'unknown'}
                      </span>
                      {warningCount ? <span className="secondary-status secondary-status-warning">warnings {warningCount}</span> : null}
                    </div>
                  ) : null}
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
                    onClick={() => void runImport(row)}
                    disabled={!!busyPath}
                    title={row.quality_gates?.import_blocked ? '质量门禁需确认后导入 PostgreSQL' : '导入 PostgreSQL'}
                  >
                    {row.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
                    {row.quality_gates?.import_blocked ? '强制入库' : '入库'}
                  </button>
                  <button
                    type="button"
                    className="pdf-trace-btn inline-flex items-center gap-1"
                    onClick={() => void runVectorDryRun(row)}
                    disabled={!!busyPath}
                    title={row.quality_gates?.vector_ingest_blocked ? '质量门禁需确认后生成检索 dry-run' : '生成检索 dry-run'}
                  >
                    {row.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Database className="h-3.5 w-3.5" />}
                    {row.quality_gates?.vector_ingest_blocked ? '强制检索' : '检索'}
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
