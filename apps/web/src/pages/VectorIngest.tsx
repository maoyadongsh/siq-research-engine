import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  DatabaseZap,
  ExternalLink,
  Loader2,
  RefreshCw,
  Server,
  ShieldCheck,
  Terminal,
  Unplug,
  XCircle,
} from 'lucide-react'
import { PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui'
import { useApi } from '../lib/hooks'

type ServiceStatus = {
  id: string
  name: string
  category: string
  url: string
  required: boolean
  ok: boolean
  statusCode: number | null
  latencyMs: number
  detail: unknown
}

type SystemStatus = {
  checkedAt: string
  services: ServiceStatus[]
}

const DEFAULT_CONSOLE_URL = 'http://127.0.0.1:7862'
const START_COMMAND = `cd /home/maoyd/siq-research-engine
SIQ_START_VECTOR_INGEST=1 ./start_all.sh`
const STANDALONE_COMMAND = `cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
SIQ_MILVUS_COLLECTION=ic_collaboration_shared python3 ingest_final.py`
const STATUS_TIMEOUT_MS = 5000

function readStoredConsoleUrl() {
  try {
    return window.localStorage.getItem('vector_ingest_url') || DEFAULT_CONSOLE_URL
  } catch {
    return DEFAULT_CONSOLE_URL
  }
}

function formatTime(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function copyText(value: string) {
  if (!navigator.clipboard) return
  navigator.clipboard.writeText(value).catch(() => {})
}

function MetricTile({
  label,
  value,
  trend,
  tone = 'neutral',
}: {
  label: string
  value: string
  trend: string
  tone?: 'neutral' | 'success' | 'warning' | 'error'
}) {
  const toneClass = {
    neutral: 'text-text',
    success: 'text-success',
    warning: 'text-warning',
    error: 'text-error',
  }[tone]

  return (
    <Surface kind="card" padding="md">
      <p className="text-sm font-semibold text-text-muted">{label}</p>
      <p className={`mt-2 break-words text-2xl font-bold tabular-nums sm:text-3xl ${toneClass}`}>{value}</p>
      <p className="mt-2 text-sm leading-5 text-text-muted">{trend}</p>
    </Surface>
  )
}

function CommandCard({ title, command }: { title: string; command: string }) {
  return (
    <Surface kind="row" padding="md">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-bold text-text">{title}</h3>
        <Button type="button" variant="ghost" size="icon-sm" onClick={() => copyText(command)} aria-label={`复制${title}命令`}>
          <Copy className="h-4 w-4" />
        </Button>
      </div>
      <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-xl bg-slate-950 p-3 text-[0.76rem] leading-5 text-white">{command}</pre>
    </Surface>
  )
}

export default function VectorIngest() {
  const { apiUrl } = useApi()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [consoleUrl, setConsoleUrl] = useState(readStoredConsoleUrl)
  const [embedConsole, setEmbedConsole] = useState(false)

  const loadStatus = useCallback(async () => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)

    setLoading(true)
    setError('')
    try {
      const response = await fetch(apiUrl('/api/system/status'), { signal: controller.signal })
      if (!response.ok) throw new Error(await response.text())
      setStatus(await response.json())
    } catch (err) {
      setStatus(null)
      if (err instanceof DOMException && err.name === 'AbortError') {
        setError('系统状态检查超时，页面已保持可用。请稍后刷新或直接新窗口打开控制台。')
      } else {
        setError(err instanceof Error ? err.message : '无法获取系统状态')
      }
    } finally {
      window.clearTimeout(timeout)
      setLoading(false)
    }
  }, [apiUrl])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadStatus()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadStatus])

  const vectorService = useMemo(
    () => status?.services.find((service) => service.id === 'vector_ingest') || null,
    [status],
  )
  const effectiveConsoleUrl = vectorService?.url || consoleUrl || DEFAULT_CONSOLE_URL
  const isReady = Boolean(vectorService?.ok)

  const saveConsoleUrl = (value: string) => {
    setConsoleUrl(value)
    setEmbedConsole(false)
    try {
      localStorage.setItem('vector_ingest_url', value)
    } catch {
      // Storage can be blocked in hardened browser profiles; keep the page usable.
    }
  }

  return (
    <PageShell>
      <PageHeader
        icon={DatabaseZap}
        eyebrow="Vector Ingest"
        title="Milvus 向量入库"
        description="面向数据管理员的 Milvus 入库控制台入口，用于文档切片、向量化、写入 collection 和查看运行状态。"
        meta={[
          <StatusBadge key="admin" tone="warning">管理员功能</StatusBadge>,
          <StatusBadge key="milvus" tone="info">Milvus</StatusBadge>,
          <StatusBadge key="gradio" tone="neutral">Gradio 控制台</StatusBadge>,
        ]}
      />

      <section className="grid gap-4 md:grid-cols-3">
        <MetricTile
          label="控制台状态"
          value={loading ? '--' : isReady ? '运行中' : '未启动'}
          tone={isReady ? 'success' : 'warning'}
          trend={vectorService ? `${vectorService.latencyMs}ms · ${vectorService.statusCode || '无响应'}` : '可选服务'}
        />
        <MetricTile
          label="默认 Collection"
          value="ic_collaboration_shared"
          trend="可在 UI 或 SIQ_MILVUS_COLLECTION 中覆盖"
        />
        <MetricTile
          label="上次检查"
          value={formatTime(status?.checkedAt) || '--'}
          trend="来自 /api/system/status"
        />
      </section>

      {error && (
        <section className="rounded-[22px] border border-error/20 bg-error/5 p-5 text-sm font-semibold text-error">
          {error}
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <PageSection
          title="入库控制台"
          description="服务启动后可在这里嵌入操作，也可以新窗口打开。"
          className="min-h-[560px]"
          actions={<Server className="h-5 w-5 text-primary" />}
        >
          <Surface kind="row" padding="md" className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${isReady ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'}`}>
                {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : isReady ? <CheckCircle2 className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-bold text-text">{isReady ? '控制台可访问' : '控制台尚未启动'}</p>
                <p className="truncate font-mono text-xs text-text-muted">{effectiveConsoleUrl}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={loadStatus}
                variant="secondary"
                size="md"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                刷新
              </Button>
              <Button
                asChild
                variant="primary"
                size="md"
              >
                <a
                  href={effectiveConsoleUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink className="h-4 w-4" />
                  新窗口打开
                </a>
              </Button>
            </div>
          </Surface>

          <label className="mb-4 block space-y-2">
            <span className="text-sm font-bold text-text">控制台地址</span>
            <input
              value={consoleUrl}
              onChange={(event) => saveConsoleUrl(event.target.value)}
              placeholder={DEFAULT_CONSOLE_URL}
              className="form-control w-full px-4 font-mono text-sm"
            />
          </label>

          {isReady ? (
            embedConsole ? (
              <Surface kind="card" padding="none" className="overflow-hidden rounded-[var(--radius-panel)] bg-white">
                <iframe
                  title="Milvus 向量入库控制台"
                  src={effectiveConsoleUrl}
                  className="h-[680px] w-full"
                  loading="lazy"
                />
              </Surface>
            ) : (
              <Surface kind="muted" padding="lg" className="flex min-h-[360px] flex-col items-center justify-center border-dashed text-center">
                <span className="premium-icon h-12 w-12 rounded-2xl text-success">
                  <Server className="h-6 w-6" />
                </span>
                <h3 className="mt-4 text-base font-semibold text-text">控制台已就绪</h3>
                <p className="mt-2 max-w-xl text-sm leading-6 text-text-muted">
                  为避免 Gradio 嵌入页拖慢主应用，默认不自动加载 iframe。需要在本页操作时再嵌入。
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                  <Button type="button" variant="primary" size="md" onClick={() => setEmbedConsole(true)}>
                    <Server className="h-4 w-4" />
                    嵌入加载
                  </Button>
                  <Button asChild variant="secondary" size="md">
                    <a href={effectiveConsoleUrl} target="_blank" rel="noreferrer">
                      <ExternalLink className="h-4 w-4" />
                      新窗口打开
                    </a>
                  </Button>
                </div>
              </Surface>
            )
          ) : (
            <Surface kind="muted" padding="lg" className="flex min-h-[360px] flex-col items-center justify-center border-dashed text-center">
              <span className="premium-icon h-12 w-12 rounded-2xl text-warning">
                {error ? <Unplug className="h-6 w-6" /> : <AlertTriangle className="h-6 w-6" />}
              </span>
              <h3 className="mt-4 text-base font-semibold text-text">向量入库控制台未启动</h3>
              <p className="mt-2 max-w-xl text-sm leading-6 text-text-muted">
                这是可选高权限数据管理服务。按右侧命令启动后刷新状态，即可在本页嵌入 Gradio 控制台。
              </p>
            </Surface>
          )}
        </PageSection>

        <aside className="space-y-4">
          <PageSection
            title="启动方式"
            description="推荐需要时再启动。"
            actions={<Terminal className="h-5 w-5 text-primary" />}
          >
            <div className="space-y-3">
              <CommandCard title="随 SIQ 一起启动" command={START_COMMAND} />
              <CommandCard title="单独启动控制台" command={STANDALONE_COMMAND} />
            </div>
          </PageSection>

          <PageSection
            title="安全边界"
            description="该工具可写入和重建 Milvus collection。"
            actions={<ShieldCheck className="h-5 w-5 text-warning" />}
          >
            <div className="space-y-3">
              {[
                '仅 system.config 权限用户可访问本页。',
                '重置 collection 前确认数据可重建。',
                'API Key 只放环境变量或 Gradio 密码框。',
                'project_tag 建议使用稳定项目名，例如 SIQ-DAJIN-2026。',
                '大文件先小批量试跑，确认质量报告后再全量入库。',
              ].map((item) => (
                <Surface key={item} kind="row" padding="sm" className="text-sm leading-6 text-text-muted">
                  {item}
                </Surface>
              ))}
            </div>
          </PageSection>
        </aside>
      </section>
    </PageShell>
  )
}
