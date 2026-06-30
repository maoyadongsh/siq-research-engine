import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Bell, Loader2, FileText, CheckCheck, Download, Sparkles, Inbox } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'
import { apiJson } from '@/shared/api/client'
import { isAuthenticatedSourceLink, openAuthenticatedSourceLink } from '../../lib/authenticatedSourceLinks'

interface PdfTask {
  task_id: string
  filename: string
  status: string
  stage?: string
  created_at: string
  markdown_ready?: boolean
}

interface DownloadedReport {
  id: string
  company: string
  category: string
  filename: string
  relativePath: string
  mtime: string
  url?: string
}

interface WorkspaceArtifact {
  id: number | string
  type: string
  key?: string
  title: string
  path: string
  source?: string
  createdAt?: string
  created_at?: string
}

type NoticeKind = 'agent' | 'parse' | 'download' | 'document'
type Notice = { id: string; kind: NoticeKind; title: string; body: string; time: string; to: string }
type ReportSection = 'analysis' | 'factcheck' | 'tracking' | 'legal'

const READ_NOTICE_KEY = 'siq_read_notice_ids'
const NOTICE_BASELINE_KEY = 'siq_notice_baseline_ready'
const NOTICES_INITIAL_DELAY_MS = 5000
const TERMINAL_DONE = new Set(['completed', 'success', 'done', 'finished'])
const reportSectionRoutes: Record<ReportSection, string> = {
  analysis: '/analysis',
  factcheck: '/verify',
  tracking: '/tracking',
  legal: '/legal',
}

function scheduleTopbarIdleWork(callback: () => void) {
  if (typeof window === 'undefined') return () => {}
  let cancelled = false
  let idleId = 0
  const timerId = window.setTimeout(() => {
    if (cancelled) return
    if ('requestIdleCallback' in window) {
      idleId = window.requestIdleCallback(() => {
        if (!cancelled) callback()
      }, { timeout: 3000 })
    } else {
      callback()
    }
  }, NOTICES_INITIAL_DELAY_MS)

  return () => {
    cancelled = true
    window.clearTimeout(timerId)
    if (idleId && 'cancelIdleCallback' in window) window.cancelIdleCallback(idleId)
  }
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function readNoticeIds() {
  try {
    const saved = localStorage.getItem(READ_NOTICE_KEY)
    const parsed = saved ? JSON.parse(saved) : []
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

function cleanFilename(value = '') {
  return value.replace(/\.pdf$/i, '').replace(/\.html$/i, '')
}

function toStringValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function isAdminRole(role?: string) {
  return role === 'admin' || role === 'super_admin'
}

function artifactKind(item: WorkspaceArtifact): NoticeKind {
  const type = (item.type || '').toLowerCase()
  if (type.includes('download')) return 'download'
  if (type.includes('document_parse')) return 'document'
  if (type.includes('parse')) return 'parse'
  return 'agent'
}

function reportPageTargetFromApiPath(path?: string) {
  if (!path) return ''
  try {
    const url = new URL(path, window.location.origin)
    const parts = url.pathname.split('/').filter(Boolean)
    const companiesIndex = parts.indexOf('companies')
    const companyDir = companiesIndex >= 0 ? parts[companiesIndex + 1] : ''
    const section = companiesIndex >= 0 ? parts[companiesIndex + 2] : ''
    const filename = companiesIndex >= 0 ? parts[companiesIndex + 3] : ''
    if (
      parts[0] !== 'api' ||
      parts[1] !== 'wiki' ||
      !companyDir ||
      !filename ||
      !['analysis', 'factcheck', 'tracking', 'legal'].includes(section)
    ) {
      return ''
    }
    const route = reportSectionRoutes[section as ReportSection]
    const params = new URLSearchParams({
      company: decodeURIComponent(companyDir),
      result: decodeURIComponent(filename),
    })
    return `${route}?${params.toString()}`
  } catch {
    return ''
  }
}

function artifactTarget(item: WorkspaceArtifact) {
  const kind = artifactKind(item)
  if (kind === 'download') return `/api/downloads/report-file?path=${encodeURIComponent(item.path || item.key || '')}`
  if (kind === 'document') return `/documents?task=${encodeURIComponent(item.key || item.path || '')}`
  if (kind === 'parse') return `/parse?task=${encodeURIComponent(item.key || item.path || '')}`
  const reportPageTarget = reportPageTargetFromApiPath(item.path)
  if (reportPageTarget) return reportPageTarget
  if (item.path?.startsWith('/')) return item.path
  return '/analysis'
}

export default function NotificationMenu() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const noticeBoxRef = useRef<HTMLDivElement>(null)
  const noticePanelRef = useRef<HTMLDivElement>(null)
  const [openNotices, setOpenNotices] = useState(false)
  const [readIds, setReadIds] = useState<string[]>(readNoticeIds)
  const [notices, setNotices] = useState<Notice[]>([])
  const [noticeLoading, setNoticeLoading] = useState(false)
  const noticesLoadedRef = useRef(false)
  const noticeRequestIdRef = useRef(0)
  const globalNotices = isAdminRole(user?.role)

  useEffect(() => {
    localStorage.setItem(READ_NOTICE_KEY, JSON.stringify(readIds))
  }, [readIds])

  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (noticeBoxRef.current?.contains(target) || noticePanelRef.current?.contains(target)) return
      setOpenNotices(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [])

  const loadTaskNotices = useCallback(
    async (options: { showLoading?: boolean } = {}) => {
      const requestId = ++noticeRequestIdRef.current
      const showLoading = options.showLoading !== false
      if (showLoading) setNoticeLoading(true)

      try {
        if (!globalNotices) {
          const data = await apiJson<{ artifacts: WorkspaceArtifact[] }>('/api/workspace/artifacts')
          const next = (data.artifacts || []).map((item) => ({
            id: `workspace:${item.type}:${item.id}`,
            kind: artifactKind(item),
            title: item.title || '个人任务已完成',
            body: item.source || item.type || 'workspace',
            time: item.createdAt || item.created_at || '',
            to: artifactTarget(item),
          } satisfies Notice))

          next.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
          if (requestId !== noticeRequestIdRef.current) return
          noticesLoadedRef.current = true
          setNotices(next)
          return
        }

        const [agentRes, parseRes, downloadRes] = await Promise.allSettled([
          apiJson<{ results?: Array<Record<string, unknown>> }>('/api/wiki/companies/recent-results?limit=40'),
          apiJson<{ tasks?: PdfTask[] }>('/api/pdf/tasks'),
          apiJson<{ reports?: DownloadedReport[] }>('/api/downloads/reports?limit=40'),
        ])

        const next: Notice[] = []

        if (agentRes.status === 'fulfilled') {
          const data = agentRes.value
          for (const item of data.results || []) {
            next.push({
              id: `agent:${toStringValue(item.id, '')}`,
              kind: 'agent',
              title: `${toStringValue(item.name, '任务')} ${toStringValue(item.typeLabel, '')}已完成`,
              body: cleanFilename(toStringValue(item.filename, '')),
              time: toStringValue(item.mtime, ''),
              to: toStringValue(item.pageUrl, ''),
            })
          }
        }

        if (parseRes.status === 'fulfilled') {
          const data = parseRes.value
          for (const task of (data.tasks || []) as PdfTask[]) {
            const status = task.status || task.stage || ''
            if (!task.markdown_ready && !TERMINAL_DONE.has(status)) continue
            next.push({
              id: `parse:${task.task_id}`,
              kind: 'parse',
              title: 'PDF 解析已完成',
              body: cleanFilename(task.filename),
              time: task.created_at,
              to: `/parse?task=${encodeURIComponent(task.task_id)}`,
            })
          }
        }

        if (downloadRes.status === 'fulfilled') {
          const data = downloadRes.value
          for (const report of (data.reports || []) as DownloadedReport[]) {
            next.push({
              id: `download:${report.id}`,
              kind: 'download',
              title: `${report.company} PDF 下载完成`,
              body: `${report.category} · ${cleanFilename(report.filename)}`,
              time: report.mtime,
              to: report.url || `/api/downloads/report-file?path=${encodeURIComponent(report.relativePath)}`,
            })
          }
        }

        next.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())

        if (requestId !== noticeRequestIdRef.current) return

        if (localStorage.getItem(NOTICE_BASELINE_KEY) !== '1') {
          setReadIds((current) => Array.from(new Set([...current, ...next.map((notice) => notice.id)])))
          localStorage.setItem(NOTICE_BASELINE_KEY, '1')
        }
        noticesLoadedRef.current = true
        setNotices(next)
      } catch {
        if (requestId === noticeRequestIdRef.current) setNotices([])
      } finally {
        if (requestId === noticeRequestIdRef.current) setNoticeLoading(false)
      }
    },
    [globalNotices],
  )

  useEffect(() => {
    const runQuietly = () => {
      loadTaskNotices({ showLoading: false }).catch(() => {})
    }
    const timer = window.setInterval(runQuietly, 30000)
    const cancelInitialLoad = scheduleTopbarIdleWork(runQuietly)

    return () => {
      window.clearInterval(timer)
      cancelInitialLoad()
      noticeRequestIdRef.current += 1
    }
  }, [loadTaskNotices])

  useEffect(() => {
    if (!openNotices || noticeLoading || noticesLoadedRef.current) return
    loadTaskNotices().catch(() => {})
  }, [loadTaskNotices, noticeLoading, openNotices])

  const unreadNotices = notices.filter((n) => !readIds.includes(n.id))
  const unread = unreadNotices.length
  const markRead = (id: string) => setReadIds((current) => (current.includes(id) ? current : [...current, id]))
  const markAllRead = () => setReadIds((current) => Array.from(new Set([...current, ...notices.map((notice) => notice.id)])))
  const openNotice = (notice: Notice) => {
    markRead(notice.id)
    setOpenNotices(false)
    if (isAuthenticatedSourceLink(notice.to)) {
      void openAuthenticatedSourceLink(notice.to)
      return
    }
    navigate(notice.to)
  }
  const noticeIcon = (kind: NoticeKind) => {
    if (kind === 'document') return <FileText className="h-4 w-4" />
    if (kind === 'parse') return <FileText className="h-4 w-4" />
    if (kind === 'download') return <Download className="h-4 w-4" />
    return <Sparkles className="h-4 w-4" />
  }

  const panel = openNotices ? (
    <div
      ref={noticePanelRef}
      className="fixed left-3 right-3 z-[80] flex flex-col overflow-hidden rounded-[var(--radius-panel)] border border-border bg-white/98 shadow-[0_22px_70px_rgba(15,23,42,0.18)] backdrop-blur-xl animate-in fade-in zoom-in-95 duration-200 sm:left-auto sm:right-[max(1rem,env(safe-area-inset-right))] sm:w-[min(380px,calc(100vw-1.5rem))]"
      style={{
        top: 'calc(var(--app-topbar-height) + 0.5rem)',
        maxHeight: 'calc(100dvh - var(--app-topbar-height) - 1rem - env(safe-area-inset-bottom))',
      }}
      role="dialog"
      aria-label="任务通知"
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
        <div>
          <span className="font-semibold text-text">任务通知</span>
          <p className="mt-0.5 text-xs text-text-muted">非对话任务完成后在这里提醒</p>
        </div>
        <button
          onClick={markAllRead}
          disabled={unread === 0}
          className="inline-flex min-h-11 items-center gap-1 rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs font-semibold text-text-muted hover:bg-bg hover:text-text disabled:opacity-40"
        >
          <CheckCheck className="h-4 w-4 text-success" />
          全部已读
        </button>
      </div>
      {noticeLoading && unreadNotices.length === 0 ? (
        <div className="flex items-center gap-2 px-4 py-5 text-sm text-text-muted">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          正在同步任务状态...
        </div>
      ) : unreadNotices.length === 0 ? (
        <div className="flex flex-col items-center px-4 py-8 text-center text-sm text-text-muted">
          <Inbox className="mb-2 h-8 w-8 opacity-40" />
          <p>暂无新的任务通知</p>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {unreadNotices.map((notice) => (
            <button
              key={notice.id}
              onClick={() => openNotice(notice)}
              className="group relative flex w-full items-start gap-3 border-b border-border/70 px-4 py-3 text-left last:border-0 hover:bg-primary/[0.035] focus-visible:bg-primary/[0.055]"
            >
              <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-r-full bg-primary opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
              <span className="premium-icon mt-0.5 h-9 w-9 shrink-0 rounded-xl">{noticeIcon(notice.kind)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-text">{notice.title}</span>
                <span className="mt-1 line-clamp-2 text-sm leading-5 text-text-muted">{notice.body}</span>
                <span className="mt-1 block text-xs font-semibold text-text-muted">{formatTime(notice.time)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  ) : null

  return (
    <div ref={noticeBoxRef} className="relative">
      <button
        onClick={() => setOpenNotices((v) => !v)}
        className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent text-sm font-semibold text-text-muted transition hover:border-border hover:bg-white hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10"
        aria-label="查看任务通知"
        title="任务通知"
      >
        <span className="relative inline-flex h-5 w-5 items-center justify-center">
          <Bell className="h-[18px] w-[18px]" />
          {unread > 0 && (
            <span className="absolute -right-2.5 -top-2.5 flex min-h-5 min-w-5 items-center justify-center rounded-full bg-error px-1.5 text-xs font-bold text-white">
              {unread}
            </span>
          )}
        </span>
      </button>
      {panel && typeof document !== 'undefined' ? createPortal(panel, document.body) : null}
    </div>
  )
}
