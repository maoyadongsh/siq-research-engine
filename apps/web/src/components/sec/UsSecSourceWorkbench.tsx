import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link2, Loader2, LocateFixed, Unlink } from 'lucide-react'
import {
  fetchUsSecPackageJson,
  type UsSecSourceMapEntry,
  type UsSecSourceMapPayload,
} from '../../features/market-parsing/api'
import {
  buildUsSecSectionScrollTargets,
  isUsSecSyncSuppressed,
  normalizeUsSecTraceSections,
  resolveUsSecActiveSection,
  US_SEC_SYNC_SUPPRESS_MS,
  type UsSecSectionScrollTarget,
  type UsSecSyncOrigin,
  type UsSecTraceSection,
} from '../../features/market-parsing/usSecSourceSync'

export interface UsSecSourceWorkbenchProps {
  packagePath: string
  rawHtmlBlobUrl: string
  sections: Array<Record<string, unknown>>
  tables?: Array<Record<string, unknown>>
  markdownFile: string
  markdownText: string
  packageLoading: boolean
  onMarkdownFileChange: (file: string) => void | Promise<void>
}

type MarkdownBlock =
  | { type: 'heading'; key: string; level: number; text: string }
  | { type: 'hr'; key: string }
  | { type: 'table'; key: string; rows: string[][] }
  | { type: 'paragraph'; key: string; text: string }
  | { type: 'space'; key: string }

const FULL_DOCUMENT_MARKDOWN_FILE = 'parser/report_complete.md'
const WIKI_FULL_DOCUMENT_MARKDOWN_FILE = 'sections/report_complete.md'

function frameDocument(frame: HTMLIFrameElement | null): Document | null {
  try {
    return frame?.contentDocument || frame?.contentWindow?.document || null
  } catch {
    return null
  }
}

function frameWindow(frame: HTMLIFrameElement | null): Window | null {
  try {
    return frame?.contentWindow || null
  } catch {
    return null
  }
}

function frameScrollTop(frame: HTMLIFrameElement | null): number {
  const win = frameWindow(frame)
  const doc = frameDocument(frame)
  return Number(win?.scrollY ?? doc?.documentElement?.scrollTop ?? doc?.body?.scrollTop ?? 0)
}

function frameScrollHeight(frame: HTMLIFrameElement | null): number {
  const doc = frameDocument(frame)
  return Math.max(
    Number(doc?.documentElement?.scrollHeight || 0),
    Number(doc?.body?.scrollHeight || 0),
  )
}

function frameViewportHeight(frame: HTMLIFrameElement | null): number {
  const win = frameWindow(frame)
  return Number(win?.innerHeight || frame?.clientHeight || 0)
}

function namedElement(doc: Document, value: string): HTMLElement | null {
  if (!value) return null
  const byId = doc.getElementById(value)
  if (byId instanceof HTMLElement) return byId
  const byName = doc.getElementsByName(value)[0]
  return byName instanceof HTMLElement ? byName : null
}

function sectionElement(doc: Document, section: UsSecTraceSection): HTMLElement | null {
  return namedElement(doc, section.htmlAnchor) || namedElement(doc, section.sectionId)
}

function injectTraceStyle(doc: Document) {
  if (doc.getElementById('siq-sec-trace-style')) return
  const style = doc.createElement('style')
  style.id = 'siq-sec-trace-style'
  style.textContent = `
    .siq-sec-trace-highlight {
      outline: 3px solid #2563eb !important;
      outline-offset: 3px !important;
      background: rgba(37, 99, 235, 0.12) !important;
      scroll-margin-top: 24px !important;
      transition: background-color 180ms ease-out, outline-color 180ms ease-out;
    }
  `
  ;(doc.head || doc.documentElement).appendChild(style)
}

function scrollElementTop(frame: HTMLIFrameElement | null, element: HTMLElement): number {
  return element.getBoundingClientRect().top + frameScrollTop(frame)
}

function lineKey(line: string, index: number): string {
  return `${index + 1}:${line.slice(0, 32)}`
}

function splitMarkdownTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isMarkdownTableLine(line: string): boolean {
  const trimmed = line.trim()
  return trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.includes('|')
}

function isMarkdownTableSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line)
}

function isMarkdownTableStart(lines: string[], index: number): boolean {
  return isMarkdownTableLine(lines[index] || '') && isMarkdownTableSeparator(lines[index + 1] || '')
}

function markdownBlocks(markdown: string): MarkdownBlock[] {
  const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const trimmed = line.trim()
    const key = lineKey(line, index)
    if (/^<!--\s*siq:[\s\S]*-->$/.test(trimmed)) {
      continue
    }
    if (isMarkdownTableStart(lines, index)) {
      const tableLines = [line]
      index += 1
      tableLines.push(lines[index])
      while (index + 1 < lines.length && isMarkdownTableLine(lines[index + 1])) {
        index += 1
        tableLines.push(lines[index])
      }
      blocks.push({
        type: 'table',
        key,
        rows: tableLines.filter((item) => !isMarkdownTableSeparator(item)).map(splitMarkdownTableRow),
      })
      continue
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      blocks.push({ type: 'heading', key, level: Math.min(6, heading[1].length), text: heading[2] })
      continue
    }
    if (trimmed === '---') {
      blocks.push({ type: 'hr', key })
      continue
    }
    if (!trimmed) {
      blocks.push({ type: 'space', key })
      continue
    }
    blocks.push({ type: 'paragraph', key, text: line })
  }
  return blocks
}

function renderMarkdownTable(rows: string[][], key: string) {
  if (!rows.length) return null
  const [head, ...body] = rows
  const columnCount = Math.max(...rows.map((row) => row.length), 1)
  const tableClassName = columnCount <= 4
    ? 'w-full min-w-full border-collapse text-left text-xs'
    : 'w-full min-w-[720px] border-collapse text-left text-xs'
  const cellsFor = (row: string[]) => Array.from({ length: columnCount }, (_, index) => row[index] || '')
  return (
    <div key={key} className="my-3 overflow-x-auto rounded-md border border-border">
      <table className={tableClassName}>
        <thead className="bg-surface-soft text-text-muted">
          <tr>
            {cellsFor(head).map((cell, index) => (
              <th key={`${key}-h-${index}`} className="border-b border-border px-3 py-2 font-semibold">{cell || ' '}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={`${key}-r-${rowIndex}`} className="border-t border-border">
              {cellsFor(row).map((cell, cellIndex) => (
                <td key={`${key}-r-${rowIndex}-${cellIndex}`} className="px-3 py-2 align-top">{cell || ' '}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function renderMarkdownBlock(block: MarkdownBlock) {
  if (block.type === 'heading') {
    const className = block.level === 1 ? 'mb-2 mt-3 text-base font-semibold' : 'mb-1 mt-3 text-sm font-semibold'
    if (block.level <= 1) return <h3 key={block.key} className={className}>{block.text}</h3>
    if (block.level === 2) return <h4 key={block.key} className={className}>{block.text}</h4>
    return <h5 key={block.key} className={className}>{block.text}</h5>
  }
  if (block.type === 'hr') return <hr key={block.key} className="my-3 border-border" />
  if (block.type === 'table') return renderMarkdownTable(block.rows, block.key)
  if (block.type === 'space') return <div key={block.key} className="h-3" />
  return <p key={block.key} className="mb-1 whitespace-pre-wrap">{block.text}</p>
}

function numberValue(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function tableFile(table: Record<string, unknown>): string {
  const index = numberValue(table.table_index)
  return index > 0 ? `tables/table_${String(index).padStart(4, '0')}.json` : ''
}

function tableRows(table: Record<string, unknown>): string[][] {
  const rows = table.rows
  if (!Array.isArray(rows)) return []
  return rows
    .filter(Array.isArray)
    .map((row) => row.map((cell) => String(cell ?? '')))
}

function renderSourceTable(table: Record<string, unknown>, index: number) {
  const rows = tableRows(table)
  const title = String(table.title || `Table ${table.table_index || index + 1}`)
  const tableKey = `source-table-${String(table.table_index || index)}`
  return (
    <div key={tableKey} className="rounded-md border border-border bg-surface-soft/40 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold text-text">{title}</div>
          <div className="mt-1 text-[11px] text-text-muted">
            table_{String(table.table_index || index + 1).padStart(4, '0')} · {String(table.row_count || rows.length || 0)} rows · {String(table.column_count || rows[0]?.length || 0)} cols
          </div>
        </div>
        {table.is_financial_statement_candidate ? <span className="secondary-status secondary-status-success">financial</span> : null}
      </div>
      {rows.length ? renderMarkdownTable(rows, tableKey) : <div className="text-xs text-text-muted">该表暂无可展示 rows。</div>}
    </div>
  )
}

function sectionTables(tables: Array<Record<string, unknown>>, sectionId: string): Array<Record<string, unknown>> {
  if (!sectionId) return []
  return tables
    .filter((table) => String(table.section_id || '') === sectionId)
    .sort((a, b) => {
      const aFinancial = a.is_financial_statement_candidate ? 0 : 1
      const bFinancial = b.is_financial_statement_candidate ? 0 : 1
      return aFinancial - bFinancial || numberValue(a.table_index) - numberValue(b.table_index)
    })
}

async function fetchTableDetail(packagePath: string, table: Record<string, unknown>): Promise<Record<string, unknown>> {
  const file = tableFile(table)
  if (!file) return table
  try {
    const detail = await fetchUsSecPackageJson<Record<string, unknown>>(packagePath, file)
    return { ...table, ...detail }
  } catch {
    return table
  }
}

export function UsSecSourceWorkbench({
  packagePath,
  rawHtmlBlobUrl,
  sections,
  tables = [],
  markdownFile,
  markdownText,
  packageLoading,
  onMarkdownFileChange,
}: UsSecSourceWorkbenchProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const rafRef = useRef<number | null>(null)
  const highlightTimerRef = useRef<number | null>(null)
  const scrollTargetsRef = useRef<UsSecSectionScrollTarget[]>([])
  const syncRef = useRef<{ origin: UsSecSyncOrigin | null; suppressUntil: number }>({ origin: null, suppressUntil: 0 })
  const lastRequestedFileRef = useRef('')
  const [sourceMapEntries, setSourceMapEntries] = useState<UsSecSourceMapEntry[]>([])
  const [frameReady, setFrameReady] = useState(0)
  const [syncEnabled, setSyncEnabled] = useState(true)
  const [activeSectionId, setActiveSectionId] = useState('')
  const [tableDetails, setTableDetails] = useState<Array<Record<string, unknown>>>([])
  const [tableLoading, setTableLoading] = useState(false)

  useEffect(() => {
    setSourceMapEntries([])
    if (!packagePath) return
    let cancelled = false
    fetchUsSecPackageJson<UsSecSourceMapPayload>(packagePath, 'qa/source_map.json')
      .then((payload) => {
        if (cancelled) return
        setSourceMapEntries(Array.isArray(payload.entries) ? payload.entries : [])
      })
      .catch(() => {
        if (!cancelled) setSourceMapEntries([])
      })
    return () => {
      cancelled = true
    }
  }, [packagePath])

  const traceSections = useMemo(
    () => normalizeUsSecTraceSections(sections, sourceMapEntries),
    [sections, sourceMapEntries],
  )
  const isFullDocumentMarkdown = markdownFile === FULL_DOCUMENT_MARKDOWN_FILE || markdownFile === WIKI_FULL_DOCUMENT_MARKDOWN_FILE
  const markdownOptions = useMemo(() => {
    const fullDocument = {
      sectionId: 'full_document',
      file: 'report_complete.md',
      filePath: markdownFile === WIKI_FULL_DOCUMENT_MARKDOWN_FILE ? WIKI_FULL_DOCUMENT_MARKDOWN_FILE : FULL_DOCUMENT_MARKDOWN_FILE,
      htmlAnchor: '',
    } as UsSecTraceSection
    return [fullDocument, ...traceSections]
  }, [markdownFile, traceSections])

  const selectedSection = useMemo(() => {
    if (isFullDocumentMarkdown) return traceSections.find((section) => section.sectionId === activeSectionId) || traceSections[0] || null
    return traceSections.find((section) => section.filePath === markdownFile)
      || traceSections.find((section) => section.file === markdownFile)
      || traceSections[0]
      || null
  }, [activeSectionId, isFullDocumentMarkdown, markdownFile, traceSections])

  const activeSection = useMemo(() => {
    return traceSections.find((section) => section.sectionId === activeSectionId) || selectedSection
  }, [activeSectionId, selectedSection, traceSections])

  const markSyncOrigin = useCallback((origin: UsSecSyncOrigin) => {
    const suppressUntil = Date.now() + US_SEC_SYNC_SUPPRESS_MS
    syncRef.current = { origin, suppressUntil }
    window.setTimeout(() => {
      if (syncRef.current.origin === origin && Date.now() >= syncRef.current.suppressUntil) {
        syncRef.current = { origin: null, suppressUntil: 0 }
      }
    }, US_SEC_SYNC_SUPPRESS_MS + 25)
  }, [])

  const measureTargets = useCallback(() => {
    const frame = iframeRef.current
    const doc = frameDocument(frame)
    if (!doc || !traceSections.length) {
      scrollTargetsRef.current = []
      return []
    }
    injectTraceStyle(doc)
    const anchorTops: Record<string, number> = {}
    for (const section of traceSections) {
      const element = sectionElement(doc, section)
      if (!element) continue
      const top = scrollElementTop(frame, element)
      anchorTops[section.sectionId] = top
      anchorTops[section.filePath] = top
    }
    const targets = buildUsSecSectionScrollTargets(
      traceSections,
      anchorTops,
      frameScrollHeight(frame),
      frameViewportHeight(frame),
    )
    scrollTargetsRef.current = targets
    return targets
  }, [traceSections])

  const clearHighlight = useCallback(() => {
    const doc = frameDocument(iframeRef.current)
    doc?.querySelectorAll('.siq-sec-trace-highlight').forEach((element) => {
      element.classList.remove('siq-sec-trace-highlight')
    })
  }, [])

  const highlightSection = useCallback((section: UsSecTraceSection) => {
    const doc = frameDocument(iframeRef.current)
    if (!doc) return
    clearHighlight()
    const element = sectionElement(doc, section)
    if (!element) return
    element.classList.add('siq-sec-trace-highlight')
    if (highlightTimerRef.current) window.clearTimeout(highlightTimerRef.current)
    highlightTimerRef.current = window.setTimeout(() => {
      element.classList.remove('siq-sec-trace-highlight')
    }, 1800)
  }, [clearHighlight])

  const scrollHtmlToSection = useCallback((
    section: UsSecTraceSection,
    behavior: ScrollBehavior = 'smooth',
    targetsOverride?: UsSecSectionScrollTarget[],
  ) => {
    const frame = iframeRef.current
    const win = frameWindow(frame)
    if (!win) return
    const currentTargets = scrollTargetsRef.current
    const targets = targetsOverride?.length ? targetsOverride : (currentTargets.length ? currentTargets : measureTargets())
    const target = targets.find((item) => item.sectionId === section.sectionId)
    if (!target) return
    markSyncOrigin('markdown')
    setActiveSectionId(section.sectionId)
    win.scrollTo({ top: target.top, behavior })
    highlightSection(section)
  }, [highlightSection, markSyncOrigin, measureTargets])

  const handleFrameLoad = useCallback(() => {
    setFrameReady((value) => value + 1)
  }, [])

  useEffect(() => {
    if (!frameReady) return
    const targets = measureTargets()
    if (syncEnabled && selectedSection) {
      window.setTimeout(() => scrollHtmlToSection(selectedSection, 'auto', targets), 50)
    }
  }, [frameReady, measureTargets, scrollHtmlToSection, selectedSection, syncEnabled])

  useEffect(() => {
    if (!syncEnabled || !frameReady || !selectedSection) return
    if (syncRef.current.origin === 'html' && isUsSecSyncSuppressed(syncRef.current.origin, Date.now(), syncRef.current.suppressUntil)) {
      setActiveSectionId(selectedSection.sectionId)
      return
    }
    scrollHtmlToSection(selectedSection)
  }, [frameReady, markdownFile, scrollHtmlToSection, selectedSection, syncEnabled])

  useEffect(() => {
    lastRequestedFileRef.current = ''
  }, [markdownFile])

  useEffect(() => {
    if (!frameReady || !syncEnabled) return
    const win = frameWindow(iframeRef.current)
    if (!win) return
    const handleScroll = () => {
      if (isUsSecSyncSuppressed(syncRef.current.origin, Date.now(), syncRef.current.suppressUntil)) return
      if (rafRef.current) return
      rafRef.current = window.requestAnimationFrame(() => {
        rafRef.current = null
        const activeTarget = resolveUsSecActiveSection(frameScrollTop(iframeRef.current), scrollTargetsRef.current)
        if (!activeTarget) return
        markSyncOrigin('html')
        setActiveSectionId(activeTarget.sectionId)
        if (isFullDocumentMarkdown || activeTarget.filePath === markdownFile || lastRequestedFileRef.current === activeTarget.filePath) return
        lastRequestedFileRef.current = activeTarget.filePath
        void onMarkdownFileChange(activeTarget.filePath)
      })
    }
    win.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      win.removeEventListener('scroll', handleScroll)
      if (rafRef.current) {
        window.cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [frameReady, isFullDocumentMarkdown, markdownFile, markSyncOrigin, onMarkdownFileChange, syncEnabled])

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) window.clearTimeout(highlightTimerRef.current)
    }
  }, [])

  const activeLabel = activeSection
    ? `${isFullDocumentMarkdown ? '完整报告' : activeSection.sectionId} · ${isFullDocumentMarkdown ? 'report_complete.md' : activeSection.file}`
    : '未选择'
  const visibleSectionTables = useMemo(
    () => sectionTables(tables, selectedSection?.sectionId || '').slice(0, 6),
    [selectedSection?.sectionId, tables],
  )

  useEffect(() => {
    setTableDetails([])
    if (!packagePath || !visibleSectionTables.length) {
      setTableLoading(false)
      return
    }
    let cancelled = false
    setTableLoading(true)
    Promise.all(visibleSectionTables.map((table) => fetchTableDetail(packagePath, table)))
      .then((details) => {
        if (!cancelled) setTableDetails(details)
      })
      .finally(() => {
        if (!cancelled) setTableLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [packagePath, visibleSectionTables])

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6" data-testid="us-sec-source-workbench">
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-text">溯源视图</h3>
          <p className="mt-1 text-sm text-text-muted">HTML 原文与解析 Markdown 对照，用于 SEC HTML/iXBRL 原文溯源。</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="secondary-status secondary-status-info" data-testid="us-sec-source-active-section">{activeLabel}</span>
            {sourceMapEntries.length ? <span className="secondary-status secondary-status-success">source_map {sourceMapEntries.length}</span> : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md border border-border bg-white px-3 text-xs font-semibold text-text shadow-sm">
            <input
              type="checkbox"
              checked={syncEnabled}
              onChange={(event) => setSyncEnabled(event.target.checked)}
              className="h-4 w-4 accent-primary"
              aria-label="切换左右联动"
            />
            {syncEnabled ? <Link2 className="h-4 w-4" /> : <Unlink className="h-4 w-4" />}
            联动
          </label>
          <button
            type="button"
            className="pdf-small-action inline-flex h-10 items-center gap-1"
            onClick={() => selectedSection && scrollHtmlToSection(selectedSection)}
            disabled={!selectedSection || !rawHtmlBlobUrl}
          >
            <LocateFixed className="h-4 w-4" />
            定位原文
          </button>
          <select
            aria-label="选择 SEC Markdown 文件"
            value={markdownFile}
            onChange={(event) => {
              const next = event.target.value
              if (next === FULL_DOCUMENT_MARKDOWN_FILE) lastRequestedFileRef.current = ''
              void onMarkdownFileChange(next)
            }}
            disabled={packageLoading || !markdownOptions.length}
            className="h-10 max-w-xs rounded-md border border-border bg-white px-2 text-xs disabled:cursor-not-allowed disabled:bg-surface-soft"
          >
            {markdownOptions.map((section) => (
              <option key={section.filePath} value={section.filePath}>
                {section.sectionId === 'full_document' ? '完整报告 · parser/report_complete.md' : `${section.sectionId} · ${section.file}`}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {rawHtmlBlobUrl ? (
          <iframe
            ref={iframeRef}
            title="SEC 原始 HTML"
            src={rawHtmlBlobUrl}
            onLoad={handleFrameLoad}
            className="h-[520px] w-full rounded-md border border-border bg-white"
            data-testid="us-sec-raw-html-frame"
          />
        ) : (
          <div className="flex h-[520px] items-center justify-center gap-2 rounded-md border border-border bg-white text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载 SEC 原始 HTML
          </div>
        )}
        <div
          className="h-[520px] overflow-auto rounded-md border border-border bg-white p-4 text-sm leading-6 text-text"
          data-testid="us-sec-source-markdown-pane"
        >
          {packageLoading ? (
            <div className="flex items-center gap-2 text-text-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载 Markdown 内容
            </div>
          ) : markdownText.trim() ? (
            markdownBlocks(markdownText).map(renderMarkdownBlock)
          ) : (
            <div className="text-sm text-text-muted">暂无 Markdown 内容</div>
          )}
          {tableLoading ? (
            <div className="mt-4 flex items-center gap-2 rounded-md border border-border bg-surface-soft/50 p-3 text-xs text-text-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载表格上下文
            </div>
          ) : tableDetails.length ? (
            <div className="mt-5 space-y-3 border-t border-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-text">表格上下文</h4>
                <span className="secondary-status secondary-status-info">{tableDetails.length}/{visibleSectionTables.length}</span>
              </div>
              {tableDetails.map(renderSourceTable)}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
