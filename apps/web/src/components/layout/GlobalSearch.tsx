import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Loader2, FileText, X, ArrowLeft } from 'lucide-react'

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

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const MOBILE_QUERY = '(max-width: 639px)'

function ResultList({ results, onResultClick }: { results: SearchResult[]; onResultClick: () => void }) {
  return (
    <>
      {results.length > 0 ? (
        results.map((item) => (
          <Link
            key={item.id}
            to={item.pageUrl}
            onClick={onResultClick}
            className="flex items-start gap-3 border-b border-border/70 px-4 py-3 last:border-0 hover:bg-primary/[0.035] focus-visible:bg-primary/[0.055]"
          >
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
          </Link>
        ))
      ) : (
        <div className="px-4 py-5 text-sm text-text-muted">未找到已生成报告</div>
      )}
    </>
  )
}

export default function GlobalSearch() {
  const navigate = useNavigate()
  const searchBoxRef = useRef<HTMLDivElement>(null)
  const mobileInputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [openSearch, setOpenSearch] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY)
    const sync = (event: MediaQueryListEvent) => {
      if (!event.matches && mobileOpen) setMobileOpen(false)
    }
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [mobileOpen])

  useEffect(() => {
    if (mobileOpen && mobileInputRef.current) {
      mobileInputRef.current.focus()
    }
  }, [mobileOpen])

  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(event.target as Node)) {
        setOpenSearch(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && mobileOpen) {
        setMobileOpen(false)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mobileOpen])

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
        const res = await fetch(`/api/wiki/reports/search?q=${encodeURIComponent(text)}&limit=8`, {
          signal: controller.signal,
        })
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        setResults(data.results || [])
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
    if (results[0]) navigate(results[0].pageUrl)
    else if (query.trim()) navigate(`/analysis?search=${encodeURIComponent(query.trim())}`)
    setOpenSearch(false)
    setMobileOpen(false)
  }

  const handleResultClick = () => {
    setOpenSearch(false)
    setMobileOpen(false)
    setQuery('')
    setResults([])
  }

  return (
    <>
      {/* Desktop */}
      <div ref={searchBoxRef} className="global-search relative hidden min-w-0 flex-1 md:block">
        <Search className="absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-text-muted sm:left-4" />
        <input
          type="search"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpenSearch(true) }}
          onFocus={() => setOpenSearch(true)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
          placeholder="搜索公司、代码或报告"
          className="h-10 w-full rounded-2xl border border-border bg-white/82 pl-11 pr-10 text-sm text-text shadow-sm backdrop-blur placeholder:text-text-muted/70 focus:border-primary focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary/10 sm:h-11 sm:pl-12 sm:pr-12"
        />
        {searching && <Loader2 className="absolute right-4 top-1/2 h-5 w-5 -translate-y-1/2 animate-spin text-primary" />}
        {openSearch && query.trim() && (
          <div className="absolute left-0 right-0 top-[calc(100%+10px)] z-50 overflow-hidden rounded-[var(--radius-panel)] border border-border bg-white/96 shadow-[0_22px_70px_rgba(15,23,42,0.12)] backdrop-blur-xl">
            <div className="border-b border-border/70 px-4 py-2 text-xs font-bold uppercase tracking-wide text-text-muted">
              全局报告搜索
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

      {/* Mobile trigger */}
      <div className="flex min-w-0 flex-1 md:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="flex h-10 w-full items-center gap-2 rounded-2xl border border-border bg-white/82 px-4 text-left text-sm text-text-muted shadow-sm backdrop-blur sm:h-11"
          aria-label="打开搜索"
        >
          <Search className="h-5 w-5 shrink-0" />
          <span className="truncate">搜索公司、代码或报告</span>
        </button>
      </div>

      {/* Mobile fullscreen overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-bg md:hidden">
          <div
            className="flex items-center gap-3 border-b border-border bg-white/82 px-4 py-3 backdrop-blur-2xl"
            style={{
              paddingTop: 'max(0.75rem, env(safe-area-inset-top))',
              paddingRight: 'max(1rem, env(safe-area-inset-right))',
              paddingLeft: 'max(1rem, env(safe-area-inset-left))',
            }}
          >
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              className="icon-button shrink-0"
              aria-label="返回"
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
            <div className="relative min-w-0 flex-1">
              <Search className="absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-text-muted" />
              <input
                ref={mobileInputRef}
                type="search"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setOpenSearch(true) }}
                onKeyDown={(e) => { if (e.key === 'Enter') submitSearch() }}
                placeholder="搜索公司、代码或报告"
                className="h-10 w-full rounded-2xl border border-border bg-white pl-10 pr-10 text-sm text-text shadow-sm focus:border-primary focus:bg-white focus:outline-none focus:ring-4 focus:ring-primary/10"
              />
              {searching && <Loader2 className="absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2 animate-spin text-primary" />}
              {!searching && query && (
                <button
                  type="button"
                  onClick={() => { setQuery(''); setResults([]); mobileInputRef.current?.focus() }}
                  className="absolute right-3 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full text-text-muted hover:bg-bg hover:text-text"
                  aria-label="清空"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>
          <div
            className="flex-1 overflow-y-auto bg-bg px-4"
            style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
          >
            {query.trim() ? (
              <div className="mx-auto max-w-3xl">
                <div className="mt-3 overflow-hidden rounded-[var(--radius-panel)] border border-border bg-white/96 shadow-sm">
                  {searching ? (
                    <div className="flex items-center gap-2 px-4 py-5 text-sm text-text-muted">
                      <Loader2 className="h-5 w-5 animate-spin text-primary" />
                      正在搜索…
                    </div>
                  ) : (
                    <ResultList results={results} onResultClick={handleResultClick} />
                  )}
                </div>
                <button
                  type="button"
                  onClick={submitSearch}
                  className="mt-3 flex h-11 w-full items-center justify-center rounded-xl accent-gradient text-sm font-semibold text-white shadow-lg shadow-blue-900/15"
                >
                  {results[0] ? '查看首个结果' : `搜索 "${query.trim()}"`}
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-20 text-text-muted">
                <Search className="mb-3 h-10 w-10 opacity-30" />
                <p className="text-sm">输入公司名称、股票代码或报告名开始搜索</p>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
