import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Loader2, FileText, X } from 'lucide-react'
import { isAuthenticatedSourceLink, openAuthenticatedSourceLink } from '../../lib/authenticatedSourceLinks'
import { apiJson } from '@/shared/api/client'

interface SearchResult {
  id: string
  type: string
  typeLabel: string
  code: string
  name: string
  filename: string
  pageUrl: string
  mtime: string
}

interface SearchPayload {
  results?: SearchResult[]
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function resultTime(value: string) {
  const time = new Date(value).getTime()
  return Number.isNaN(time) ? 0 : time
}

function normalizeSearchResults(results: SearchResult[] = [], source: string): SearchResult[] {
  return results.map((item) => ({
    ...item,
    id: `${source}:${item.id}`,
    code: item.code || '',
    name: item.name || item.filename || item.typeLabel || '未命名结果',
    filename: item.filename || item.name || item.typeLabel || '',
    pageUrl: item.pageUrl || '/',
    mtime: item.mtime || '',
  }))
}

function mergeSearchResults(reportResults: SearchResult[] = [], workspaceResults: SearchResult[] = []) {
  const seen = new Set<string>()
  const merged: SearchResult[] = []
  for (const item of [
    ...normalizeSearchResults(workspaceResults, 'workspace'),
    ...normalizeSearchResults(reportResults, 'report'),
  ]) {
    const key = item.pageUrl || item.id
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(item)
  }
  merged.sort((a, b) => resultTime(b.mtime) - resultTime(a.mtime))
  return merged.slice(0, 8)
}

function ResultRow({ item, onResultClick }: { item: SearchResult; onResultClick: () => void }) {
  const className = "flex w-full items-start gap-3 border-b border-border/70 px-4 py-3 text-left last:border-0 hover:bg-primary/[0.035] focus-visible:bg-primary/[0.055]"
  const content = (
    <>
      <span className="premium-icon mt-1 h-10 w-10 shrink-0 rounded-xl">
        <FileText className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-base font-semibold text-text">
          {item.name} {item.code}
        </span>
        <span className="mt-1 block truncate text-sm text-text-muted">
          {item.typeLabel} · {item.filename}
        </span>
        <span className="mt-1 block text-xs text-text-muted">{formatTime(item.mtime)}</span>
      </span>
    </>
  )

  if (isAuthenticatedSourceLink(item.pageUrl)) {
    return (
      <button
        key={item.id}
        type="button"
        onClick={() => {
          onResultClick()
          void openAuthenticatedSourceLink(item.pageUrl)
        }}
        className={className}
      >
        {content}
      </button>
    )
  }

  return (
    <Link
      key={item.id}
      to={item.pageUrl}
      onClick={onResultClick}
      className={className}
    >
      {content}
    </Link>
  )
}

function ResultList({ results, onResultClick }: { results: SearchResult[]; onResultClick: () => void }) {
  return (
    <>
      {results.length > 0 ? (
        results.map((item) => (
          <ResultRow key={item.id} item={item} onResultClick={onResultClick} />
        ))
      ) : (
        <div className="px-4 py-5 text-sm text-text-muted">未找到报告或文档</div>
      )}
    </>
  )
}

export default function GlobalSearch() {
  const navigate = useNavigate()
  const searchBoxRef = useRef<HTMLDivElement>(null)
  const mobileSearchBoxRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [openSearch, setOpenSearch] = useState(false)

  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (!searchBoxRef.current?.contains(target) && !mobileSearchBoxRef.current?.contains(target)) {
        setOpenSearch(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenSearch(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const text = query.trim()
    if (!text) {
      const clearTimer = window.setTimeout(() => {
        setResults([])
        setSearching(false)
      }, 0)
      return () => {
        window.clearTimeout(clearTimer)
        controller.abort()
      }
    }
    const timer = window.setTimeout(async () => {
      setSearching(true)
      try {
        const [reportResult, workspaceResult] = await Promise.allSettled([
          apiJson<SearchPayload>(`/api/wiki/reports/search?q=${encodeURIComponent(text)}&limit=8`, { signal: controller.signal }),
          apiJson<SearchPayload>(`/api/workspace/artifacts/search?q=${encodeURIComponent(text)}&limit=8`, { signal: controller.signal }),
        ])
        const reportResults = reportResult.status === 'fulfilled' ? reportResult.value.results || [] : []
        const workspaceResults = workspaceResult.status === 'fulfilled' ? workspaceResult.value.results || [] : []
        setResults(mergeSearchResults(reportResults, workspaceResults))
      } catch {
        if (!controller.signal.aborted) setResults([])
      } finally {
        if (!controller.signal.aborted) setSearching(false)
      }
    }, 220)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  const submitSearch = () => {
    if (results[0]) {
      if (isAuthenticatedSourceLink(results[0].pageUrl)) {
        void openAuthenticatedSourceLink(results[0].pageUrl)
      } else {
        navigate(results[0].pageUrl)
      }
    } else if (query.trim()) {
      navigate(`/analysis?search=${encodeURIComponent(query.trim())}`)
    }
    setOpenSearch(false)
  }

  const handleResultClick = () => {
    setOpenSearch(false)
    setQuery('')
    setResults([])
  }

  return (
    <>
      {/* Desktop */}
      <div ref={searchBoxRef} className="global-search relative hidden min-w-0 flex-1 md:block">
        <Search strokeWidth={2.8} className="pointer-events-none absolute left-4 top-1/2 z-10 h-5 w-5 -translate-y-1/2 text-primary sm:left-4" />
        <input
          type="search"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpenSearch(true) }}
          onFocus={() => setOpenSearch(true)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
          placeholder="搜索公司、代码、报告或文档"
          className="h-9 w-full rounded-xl border border-border bg-white/82 pl-12 pr-10 text-sm text-text shadow-sm backdrop-blur placeholder:text-text-muted/70 focus:border-primary focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary/10 sm:h-10 sm:pl-12 sm:pr-12"
        />
        {searching && <Loader2 className="absolute right-4 top-1/2 h-5 w-5 -translate-y-1/2 animate-spin text-primary" />}
        {openSearch && query.trim() && (
          <div className="absolute left-0 right-0 top-[calc(100%+10px)] z-50 overflow-hidden rounded-[var(--radius-panel)] border border-border bg-white/96 shadow-[0_22px_70px_rgba(15,23,42,0.12)] backdrop-blur-xl">
            <div className="border-b border-border/70 px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-text-muted">
              全局搜索
            </div>
            {searching ? (
              <div className="flex items-center gap-2 px-4 py-5 text-sm text-text-muted">
                <Loader2 className="h-5 w-5 animate-spin text-primary" />
                正在搜索...
              </div>
            ) : (
              <ResultList results={results} onResultClick={handleResultClick} />
            )}
          </div>
        )}
      </div>

      {/* Mobile inline search */}
      <div ref={mobileSearchBoxRef} className="relative min-w-0 flex-1 md:hidden">
        <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-[18px] w-[18px] -translate-y-1/2 text-primary" />
        <input
          type="search"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpenSearch(true) }}
          onFocus={() => setOpenSearch(true)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
          placeholder="搜索公司、代码或文档"
          className="mobile-search-field h-10 w-full min-w-0 rounded-[10px] border border-border bg-white pl-9 pr-9 text-sm text-text outline-none placeholder:text-text-muted/70 focus:border-primary focus:ring-2 focus:ring-primary/10"
          aria-label="全局搜索"
        />
        {searching ? <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-primary" /> : null}
        {!searching && query ? (
          <button
            type="button"
            onClick={() => { setQuery(''); setResults([]); setOpenSearch(false) }}
            className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full text-text-muted hover:bg-bg hover:text-text"
            aria-label="清空搜索"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
        {openSearch && query.trim() ? (
          <div className="fixed left-2 right-2 top-[calc(var(--app-topbar-height)+0.5rem)] z-50 max-h-[min(65dvh,32rem)] overflow-y-auto rounded-[10px] border border-border bg-white shadow-[0_16px_38px_rgba(15,23,42,0.14)]">
            {searching ? (
              <div className="flex items-center gap-2 px-4 py-5 text-sm text-text-muted">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                正在搜索...
              </div>
            ) : (
              <ResultList results={results} onResultClick={handleResultClick} />
            )}
          </div>
        ) : null}
      </div>
    </>
  )
}
