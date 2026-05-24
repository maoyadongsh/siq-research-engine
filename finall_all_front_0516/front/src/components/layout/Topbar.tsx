import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Bell, Sun, Moon, Menu, Loader2, FileText, CheckCheck, Download, Sparkles } from 'lucide-react'
import { useTheme } from '../../lib/hooks'

interface TopbarProps { sidebarCollapsed: boolean; onMenuClick: () => void }
interface SearchResult { id: string; type: string; typeLabel: string; code: string; name: string; filename: string; pageUrl: string; mtime: string }
interface PdfTask { task_id: string; filename: string; status: string; stage?: string; created_at: string; markdown_ready?: boolean }
interface DownloadedReport { id: string; company: string; category: string; filename: string; relativePath: string; mtime: string }

type NoticeKind = 'agent' | 'parse' | 'download'
type Notice = { id: string; kind: NoticeKind; title: string; body: string; time: string; to: string }

const READ_NOTICE_KEY = 'finsight_read_notice_ids'
const NOTICE_BASELINE_KEY = 'finsight_notice_baseline_ready'
const TERMINAL_DONE = new Set(['completed', 'success', 'done', 'finished'])

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

export default function Topbar({ sidebarCollapsed, onMenuClick }: TopbarProps) {
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()
  const searchBoxRef = useRef<HTMLDivElement>(null)
  const noticeBoxRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [openSearch, setOpenSearch] = useState(false)
  const [openNotices, setOpenNotices] = useState(false)
  const [readIds, setReadIds] = useState<string[]>(readNoticeIds)
  const [notices, setNotices] = useState<Notice[]>([])
  const [noticeLoading, setNoticeLoading] = useState(false)

  useEffect(() => { localStorage.setItem(READ_NOTICE_KEY, JSON.stringify(readIds)) }, [readIds])
  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(event.target as Node)) {
        setOpenSearch(false)
      }
      if (noticeBoxRef.current && !noticeBoxRef.current.contains(event.target as Node)) {
        setOpenNotices(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])
  useEffect(() => {
    let ignore = false

    async function loadTaskNotices() {
      setNoticeLoading(true)
      try {
        const [agentRes, parseRes, downloadRes] = await Promise.allSettled([
          fetch('/api/wiki/companies/recent-results?limit=40'),
          fetch('/pdfapi/tasks'),
          fetch('/api/downloads/reports?limit=40'),
        ])

        const next: Notice[] = []

        if (agentRes.status === 'fulfilled' && agentRes.value.ok) {
          const data = await agentRes.value.json()
          for (const item of data.results || []) {
            next.push({
              id: `agent:${item.id}`,
              kind: 'agent',
              title: `${item.name} ${item.typeLabel}已完成`,
              body: cleanFilename(item.filename),
              time: item.mtime,
              to: item.pageUrl,
            })
          }
        }

        if (parseRes.status === 'fulfilled' && parseRes.value.ok) {
          const data = await parseRes.value.json()
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

        if (downloadRes.status === 'fulfilled' && downloadRes.value.ok) {
          const data = await downloadRes.value.json()
          for (const report of (data.reports || []) as DownloadedReport[]) {
            next.push({
              id: `download:${report.id}`,
              kind: 'download',
              title: `${report.company} PDF 下载完成`,
              body: `${report.category} · ${cleanFilename(report.filename)}`,
              time: report.mtime,
              to: `/search?download=${encodeURIComponent(report.relativePath)}`,
            })
          }
        }

        next.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
        if (!ignore) {
          if (localStorage.getItem(NOTICE_BASELINE_KEY) !== '1') {
            setReadIds((current) => Array.from(new Set([...current, ...next.map((notice) => notice.id)])))
            localStorage.setItem(NOTICE_BASELINE_KEY, '1')
          }
          setNotices(next)
        }
      } catch {
        if (!ignore) setNotices([])
      } finally {
        if (!ignore) setNoticeLoading(false)
      }
    }

    loadTaskNotices()
    const timer = window.setInterval(loadTaskNotices, 30000)
    return () => { ignore = true; window.clearInterval(timer) }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    const text = query.trim()
    if (!text) { setResults([]); setSearching(false); return () => controller.abort() }
    setSearching(true)
    const timer = window.setTimeout(async () => {
      try {
        const res = await fetch(`/api/wiki/reports/search?q=${encodeURIComponent(text)}&limit=8`, { signal: controller.signal })
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        setResults(data.results || [])
      } catch {
        if (!controller.signal.aborted) setResults([])
      } finally {
        if (!controller.signal.aborted) setSearching(false)
      }
    }, 220)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [query])

  const unreadNotices = notices.filter((n) => !readIds.includes(n.id))
  const unread = unreadNotices.length
  const markRead = (id: string) => setReadIds((current) => current.includes(id) ? current : [...current, id])
  const markAllRead = () => setReadIds((current) => Array.from(new Set([...current, ...notices.map((notice) => notice.id)])))
  const openNotice = (notice: Notice) => {
    markRead(notice.id)
    setOpenNotices(false)
    navigate(notice.to)
  }
  const noticeIcon = (kind: NoticeKind) => {
    if (kind === 'parse') return <FileText className="h-4 w-4" />
    if (kind === 'download') return <Download className="h-4 w-4" />
    return <Sparkles className="h-4 w-4" />
  }
  const submitSearch = () => {
    if (results[0]) navigate(results[0].pageUrl)
    else if (query.trim()) navigate(`/analysis?search=${encodeURIComponent(query.trim())}`)
    setOpenSearch(false)
  }

  return (
    <header className={`fixed top-0 right-0 z-30 flex h-[72px] items-center gap-4 border-b border-white/70 bg-white/72 px-4 shadow-[0_1px_0_rgba(255,255,255,0.78)_inset] backdrop-blur-2xl transition-all duration-300 sm:px-6 ${sidebarCollapsed ? 'lg:left-20' : 'lg:left-72'} left-0`}>
      <button onClick={onMenuClick} className="icon-button lg:hidden" aria-label="打开侧边栏"><Menu className="h-5 w-5" /></button>
      <div ref={searchBoxRef} className="relative min-w-0 flex-1 max-w-3xl">
        <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-text-muted" />
        <input
          type="search"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpenSearch(true) }}
          onFocus={() => setOpenSearch(true)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
          placeholder="搜索公司、股票代码或已生成报告..."
          className="h-11 w-full rounded-2xl border border-border bg-white/82 pl-12 pr-12 text-sm text-text shadow-sm backdrop-blur placeholder:text-text-muted/70 focus:border-primary focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary/10"
        />
        {searching && <Loader2 className="absolute right-4 top-1/2 h-5 w-5 -translate-y-1/2 animate-spin text-primary" />}
        {openSearch && query.trim() && (
          <div className="absolute left-0 right-0 top-[calc(100%+10px)] z-50 overflow-hidden rounded-[22px] border border-border bg-white/96 shadow-[0_22px_70px_rgba(15,23,42,0.12)] backdrop-blur-xl">
            {results.length > 0 ? results.map((item) => (
              <Link key={item.id} to={item.pageUrl} onClick={() => setOpenSearch(false)} className="flex items-start gap-3 border-b border-border/70 px-4 py-3 last:border-0 hover:bg-primary/[0.035]">
                <span className="premium-icon mt-1 h-10 w-10 shrink-0 rounded-xl"><FileText className="h-5 w-5" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-semibold text-text">{item.name} {item.code}</span>
                  <span className="mt-1 block truncate text-sm text-text-muted">{item.typeLabel} · {item.filename}</span>
                  <span className="mt-1 block text-xs text-text-muted">{formatTime(item.mtime)}</span>
                </span>
              </Link>
            )) : <div className="px-4 py-5 text-sm text-text-muted">未找到已生成报告</div>}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={toggle} className="icon-button" title={theme === 'dark' ? '切换亮色' : '切换暗色'} aria-label={theme === 'dark' ? '切换亮色' : '切换暗色'}>
          {theme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </button>
        <div ref={noticeBoxRef} className="relative">
          <button onClick={() => setOpenNotices((v) => !v)} className="icon-button relative" aria-label="查看任务通知">
            <Bell className="h-5 w-5" />
            {unread > 0 && <span className="absolute right-2 top-2 flex min-h-5 min-w-5 items-center justify-center rounded-full bg-error px-1.5 text-xs font-bold text-white">{unread}</span>}
          </button>
          {openNotices && (
            <div className="absolute right-0 top-[calc(100%+10px)] w-[min(380px,calc(100vw-1.5rem))] overflow-hidden rounded-[22px] border border-border bg-white/96 shadow-[0_22px_70px_rgba(15,23,42,0.12)] backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div>
                  <span className="font-semibold text-text">任务通知</span>
                  <p className="mt-0.5 text-xs text-text-muted">非对话任务完成后在这里提醒</p>
                </div>
                <button
                  onClick={markAllRead}
                  disabled={unread === 0}
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-text-muted hover:bg-bg hover:text-text disabled:opacity-40"
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
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  暂无新的任务通知
                </div>
              ) : (
                <div className="max-h-[420px] overflow-y-auto">
                  {unreadNotices.map((notice) => (
                    <button
                      key={notice.id}
                      onClick={() => openNotice(notice)}
                      className="flex w-full items-start gap-3 border-b border-border/70 px-4 py-3 text-left last:border-0 hover:bg-primary/[0.035]"
                    >
                      <span className="premium-icon mt-0.5 h-9 w-9 shrink-0 rounded-xl">
                        {noticeIcon(notice.kind)}
                      </span>
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
          )}
        </div>
      </div>
    </header>
  )
}
