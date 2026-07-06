import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FileText, Loader2 } from 'lucide-react'
import { EmptyState } from '@/components/page'
import { PageSection } from '../components/page/PageShell'
import { checkHealth, fetchStatus, loadDownloadedReports as loadDownloadedReportsApi } from '../features/pdf-parsing/api'
import { PDF_CSS } from './pdf/pdfStyles'
import { useEditableTable } from './pdf/useEditableTable'
import { usePdfSourceTrace } from './pdf/usePdfSourceTrace'
import { usePdfTasks } from './pdf/usePdfTasks'
import { usePdfWorkflow } from './pdf/usePdfWorkflow'
import { PdfArtifactList } from '../components/pdf/PdfArtifactList'
import { PdfHealthStrip } from '../components/pdf/PdfHealthStrip'
import { PdfMarkdownPreview } from '../components/pdf/PdfMarkdownPreview'
import { PdfQualityPanel } from '../components/pdf/PdfQualityPanel'
import { PdfSourceWorkbench } from '../components/pdf/PdfSourceWorkbench'
import { PdfTaskList } from '../components/pdf/PdfTaskList'
import { PdfUploadPanel } from '../components/pdf/PdfUploadPanel'
import { PdfWorkflowPanel } from '../components/pdf/PdfWorkflowPanel'
import { MarketParsingTabs } from '../components/pdf/MarketParsingTabs'
import { useToast } from '../hooks/useToast'
import {
  buildMarketParsingPageViewModel,
  type MarketParsingCode,
} from '../features/market-parsing/viewModel'
import { validateMarketParsingUploadFiles } from '../features/market-parsing/uploadFiles'
import { taskMatchesMarket } from '../lib/pdfTaskMarkets'
import type { DownloadedPdf, HealthStatus, TaskItem } from '../lib/pdfTypes'

export type { MarketParsingCode }

function MobileTabStrip({
  activeSection,
  sectionLabelEntries,
}: {
  activeSection: string
  sectionLabelEntries: [string, string][]
}) {
  return (
    <div className="mobile-tab-strip">
      {sectionLabelEntries.map(([id, label]) => (
        <a key={id} href={`#${id}`} className={activeSection === id ? 'is-active' : ''}>
          {label}
        </a>
      ))}
    </div>
  )
}

function useActiveSection(ids: string[]) {
  const [active, setActive] = useState(ids[0] || '')
  useEffect(() => {
    if (ids.length === 0) return
    const ratios = new Map<string, number>()
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => ratios.set(entry.target.id, entry.intersectionRatio))
        let best = ids[0]
        let bestRatio = 0
        ids.forEach((id) => {
          const r = ratios.get(id) || 0
          if (r > bestRatio) {
            bestRatio = r
            best = id
          }
        })
        if (bestRatio > 0) setActive(best)
      },
      { rootMargin: '-12% 0px -55% 0px', threshold: [0, 0.25, 0.5, 0.75, 1] },
    )
    ids.forEach((id) => {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [ids])
  return active
}

export interface MarketParsingPageProps {
  market?: MarketParsingCode
  title?: string
  description?: string
  kicker?: string
  steps?: string[]
  workflowMode?: 'standard' | 'generic'
  workflowTitle?: string
  workflowDescription?: string
  emptyTitle?: string
  emptyDescription?: string
  extraPanel?: React.ReactNode
}

export function MarketParsingPage({
  market,
  title = '智能解析',
  description = '上传财报 PDF，生成 Markdown、表格、财务数据抽取和可视化溯源结果。',
  kicker = 'Report Parsing',
  steps = ['解析', '抽取', '校验'],
  workflowMode = 'standard',
  workflowTitle,
  workflowDescription,
  emptyTitle = '选择一份财报后开始解析',
  emptyDescription = '可从已下载列表直接解析，也支持批量上传最多 5 个 PDF。',
  extraPanel,
}: MarketParsingPageProps) {
  const [searchParams] = useSearchParams()
  const { toast } = useToast()

  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [dragover, setDragover] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const [showConfig, setShowConfig] = useState(false)
  const [backend, setBackend] = useState('hybrid-http-client')
  const [parseMethod, setParseMethod] = useState('auto')
  const [startPage, setStartPage] = useState('')
  const [endPage, setEndPage] = useState('')
  const [formula, setFormula] = useState(true)
  const [table, setTable] = useState(true)

  const [health, setHealth] = useState<HealthStatus | null>(null)

  const [downloadedReports, setDownloadedReports] = useState<DownloadedPdf[]>([])
  const [downloadedLoading, setDownloadedLoading] = useState(false)
  const [downloadedQuery, setDownloadedQuery] = useState('')
  const [downloadedBusyPath, setDownloadedBusyPath] = useState('')
  const [logsExpanded, setLogsExpanded] = useState(false)

  const logRef = useRef<HTMLDivElement>(null)
  const mdRef = useRef<HTMLDivElement>(null)
  const corrStatusRef = useRef<HTMLSelectElement>(null)
  const corrTextRef = useRef<HTMLTextAreaElement>(null)
  const corrNoteRef = useRef<HTMLTextAreaElement>(null)
  const setSelectedFilesRef = useRef<((files: File[]) => void) | null>(null)

  const workflowRef = useRef<{ loadWorkflowStatus: () => Promise<void> }>({ loadWorkflowStatus: async () => {} })

  const showToast = useCallback((msg: string) => {
    toast({ type: 'info', title: msg })
  }, [toast])

  const taskMatchesCurrentMarket = useCallback((task: TaskItem) => taskMatchesMarket(task, market), [market])

  const tasks = usePdfTasks({
    backend,
    parseMethod,
    startPage,
    endPage,
    formula,
    table,
    showToast,
    onWorkflowReload: () => workflowRef.current.loadWorkflowStatus(),
    taskFilter: taskMatchesCurrentMarket,
    market,
    setSelectedFilesRef,
  })

  const workflow = usePdfWorkflow(tasks.taskIdRef, showToast, (msg: string | null) => tasks.setError(msg))

  const viewModel = useMemo(() => buildMarketParsingPageViewModel({
    market,
    parseBadgeClass: tasks.parseBadge.cls,
    resultDeferred: Boolean(tasks.resultDeferred),
    markdown: tasks.markdown,
    parseActive: tasks.parseActive,
    taskCount: tasks.tasks.length,
    logs: tasks.logs,
    logsExpanded,
  }), [
    market,
    tasks.parseBadge.cls,
    tasks.resultDeferred,
    tasks.markdown,
    tasks.parseActive,
    tasks.tasks.length,
    tasks.logs,
    logsExpanded,
  ])
  const activeSection = useActiveSection(viewModel.sectionIds)

  useEffect(() => {
    workflowRef.current.loadWorkflowStatus = workflow.loadWorkflowStatus
  }, [workflow.loadWorkflowStatus])

  const { setFocusedLine, idleLoad, resumeTask, taskIdRef, logs } = tasks

  const focusMarkdownLine = useCallback(
    (line: number) => {
      if (!line) return
      setFocusedLine(line)
      setTimeout(() => {
        mdRef.current?.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    },
    [setFocusedLine],
  )

  const sourceTrace = usePdfSourceTrace({
    taskIdRef: tasks.taskIdRef,
    corrStatusRef,
    corrTextRef,
    corrNoteRef,
    focusMarkdownLine,
    reportError: (msg: string | null) => tasks.setError(msg),
  })

  const editable = useEditableTable({
    taskIdRef: tasks.taskIdRef,
    srcCtx: sourceTrace.srcCtx,
    pdfCtx: sourceTrace.pdfCtx,
    editTableRef: sourceTrace.editTableRef,
    corrTextRef,
    corrStatusRef,
    corrNoteRef,
    traceCell: sourceTrace.traceCell,
    updatePdfViewer: sourceTrace.updatePdfViewer,
    reportError: (msg: string | null) => tasks.setError(msg),
    showToast,
  })

  const loadDownloadedReports = useCallback(async (text: string) => {
    setDownloadedLoading(true)
    try {
      const d = await loadDownloadedReportsApi(text, market)
      setDownloadedReports(d.reports || [])
    } catch {
      setDownloadedReports([])
    } finally {
      setDownloadedLoading(false)
    }
  }, [market])

  const refreshHealth = useCallback(async () => {
    try {
      const h = await checkHealth()
      setHealth(h)
    } catch {
      setHealth(null)
    }
  }, [])

  useEffect(() => {
    setSelectedFilesRef.current = setSelectedFiles
  }, [setSelectedFiles])

  useEffect(() => {
    const cancelIdle = idleLoad()
    queueMicrotask(() => {
      void refreshHealth()
      void loadDownloadedReports('')
    })
    const h = setInterval(() => void refreshHealth(), 10000)
    return () => {
      cancelIdle()
      clearInterval(h)
    }
  }, [idleLoad, refreshHealth, loadDownloadedReports])

  useEffect(() => {
    const taskId = searchParams.get('task')
    if (!taskId || taskIdRef.current === taskId) return
    fetchStatus(taskId, 0)
      .then((data) => {
        if (!data) return
        void resumeTask(taskId, String(data.filename || ''), String(data.status || data.stage || 'completed'))
      })
      .catch(() => {
        // ignore url task resume errors
      })
  }, [searchParams, resumeTask, taskIdRef])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logs])

  const handleFiles = useCallback(
    (files: FileList | File[]) => {
      const result = validateMarketParsingUploadFiles(files)
      if (!result.files.length && !result.error) return
      if (result.error) {
        tasks.setError(result.error)
        return
      }
      setSelectedFiles(result.files)
      tasks.setError(null)
    },
    [tasks],
  )

  const startConvert = useCallback(async () => {
    sourceTrace.setSourceVisible(false)
    await tasks.startConvert(selectedFiles)
  }, [sourceTrace, tasks, selectedFiles])

  const onSelectDownloaded = useCallback(
    async (report: DownloadedPdf, onBusy: (path: string) => void) => {
      sourceTrace.setSourceVisible(false)
      await tasks.selectDownloadedReport(report, onBusy)
    },
    [sourceTrace, tasks],
  )

  const onParseDownloaded = useCallback(
    async (report: DownloadedPdf, onBusy: (path: string) => void) => {
      sourceTrace.setSourceVisible(false)
      await tasks.parseDownloadedReport(report, onBusy)
    },
    [sourceTrace, tasks],
  )


  const onTaskResume = useCallback(
    async (task: TaskItem) => {
      sourceTrace.setSourceVisible(false)
      await tasks.resumeTask(task.task_id, String(task.filename || ''), String(task.status))
    },
    [sourceTrace, tasks],
  )

  const onTaskViewResult = useCallback(
    async (task: TaskItem) => {
      await tasks.viewTaskResult(task)
    },
    [tasks],
  )

  const handleReadingClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const btn = (e.target as HTMLElement).closest<HTMLElement>('[data-ptidx]')
      if (!btn) return
      const idx = Number(btn.dataset.ptidx || 0)
      if (idx) sourceTrace.showTableSource(idx)
    },
    [sourceTrace],
  )

  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
      <style>{PDF_CSS}</style>

      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <FileText className="h-3.5 w-3.5" />
              {kicker}
            </div>
            <h1 className="secondary-title">{title}</h1>
            <p className="secondary-description">{description}</p>
          </div>
          <div className="secondary-step-row">
            {steps.map((step, index) => (
              <span key={step} className={index === 0 ? 'secondary-step-chip is-active' : 'secondary-step-chip'}>{step}</span>
            ))}
          </div>
        </div>
      </section>

      {market ? <MarketParsingTabs active={market} /> : null}

      <PdfHealthStrip health={health} />

      {extraPanel}

      <MobileTabStrip activeSection={activeSection} sectionLabelEntries={viewModel.sectionLabelEntries} />

      <div id="pdf-upload">
        <PdfUploadPanel
        health={health}
        selectedFiles={selectedFiles}
        setSelectedFiles={setSelectedFiles}
        fileInput={fileInput}
        startConvert={startConvert}
        uploading={tasks.uploading}
        uploadActive={tasks.uploadActive}
        parseBadge={tasks.parseBadge}
        taskId={tasks.taskIdRef.current}
        cancelTask={tasks.cancelTask}
        error={tasks.error}
        downloadedReports={downloadedReports}
        downloadedQuery={downloadedQuery}
        setDownloadedQuery={setDownloadedQuery}
        downloadedLoading={downloadedLoading}
        downloadedBusyPath={downloadedBusyPath}
        setDownloadedBusyPath={setDownloadedBusyPath}
        loadDownloadedReports={loadDownloadedReports}
        selectDownloadedReport={onSelectDownloaded}
        parseDownloadedReport={onParseDownloaded}
        backend={backend}
        setBackend={setBackend}
        parseMethod={parseMethod}
        setParseMethod={setParseMethod}
        startPage={startPage}
        setStartPage={setStartPage}
        endPage={endPage}
        setEndPage={setEndPage}
        formula={formula}
        setFormula={setFormula}
        table={table}
        setTable={setTable}
        showConfig={showConfig}
        setShowConfig={setShowConfig}
        handleFiles={handleFiles}
        dragover={dragover}
        setDragover={setDragover}
      />
      </div>

      <div id="pdf-status" className="grid gap-3">
      {tasks.uploadActive && (
        <div className="pdf-stage">
          <div className="flex items-center justify-between mb-2.5">
            <div className="font-semibold text-[.95rem] flex items-center gap-2">
              <span className="inline-block h-4 w-4 text-primary" />
              文件上传
            </div>
            <span className={`pdf-status-badge ${tasks.uploadBadge.cls}`}>{tasks.uploadBadge.text}</span>
          </div>
          <div className="pdf-pbar-wrap">
            <div className="pdf-pbar" style={{ width: `${tasks.uploadPct}%` }} />
          </div>
          <div className="flex justify-between mt-2 text-[.85rem] text-text-muted">
            <span>{tasks.uploadStatusText}</span>
            <span>{Math.round(tasks.uploadPct)}%</span>
          </div>
        </div>
      )}

      {tasks.parseActive && (
        <div className="pdf-stage">
          <div className="flex items-center justify-between mb-2.5">
            <div className="font-semibold text-[.95rem] flex items-center gap-2">
              <span className="inline-block h-4 w-4 text-primary" />
              财报解析
            </div>
            <span className={`pdf-status-badge ${tasks.parseBadge.cls}`}>{tasks.parseBadge.text}</span>
          </div>
          <div className="pdf-pbar-wrap">
            <div className={`pdf-pbar ${tasks.parseBadge.cls === 'completed' ? 'done' : ''}`} style={{ width: `${tasks.parsePct}%` }} />
          </div>
          <div className="flex justify-between mt-2 text-[.85rem] text-text-muted">
            <span>{tasks.parseStatusText}</span>
            <span>{Math.round(tasks.parsePct)}%</span>
          </div>
          <div className="flex flex-wrap gap-4 mt-2 text-[.85rem] text-text-muted">
            {tasks.queueInfo && <span>{tasks.queueInfo}</span>}
            {tasks.elapsedInfo && <span>{tasks.elapsedInfo}</span>}
            {tasks.pagesInfo && <span style={{ fontWeight: 600, color: '#2563eb' }}>{tasks.pagesInfo}</span>}
            {tasks.stageInfo && <span>{tasks.stageInfo}</span>}
          </div>
        </div>
      )}

      {tasks.taskIdRef.current && viewModel.shouldShowResultGate && (
        <div className="pdf-mobile-result-gate">
          <div>
            <h3>解析结果已就绪</h3>
            <p>点击后加载 Markdown、质量报告和财务校验。</p>
          </div>
          <button onClick={() => void tasks.fetchResult()} disabled={tasks.resultLoading} className="pdf-small-action primary">
            {tasks.resultLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}查看结果
          </button>
        </div>
      )}

      </div>

      {tasks.logs.length > 0 && (
        <div id="pdf-logs" className="apple-card rounded-[var(--radius-panel)] p-4 sm:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-text">处理日志</h3>
              <p className="mt-1 text-sm text-text-muted">{viewModel.logDescription}</p>
            </div>
            <button
              type="button"
              onClick={() => setLogsExpanded((value) => !value)}
              className="inline-flex h-10 items-center justify-center rounded-[var(--radius-control)] border border-border bg-card px-4 text-sm font-semibold text-text-muted hover:bg-bg hover:text-text"
            >
              {viewModel.logToggleText}
            </button>
          </div>
          {viewModel.shouldShowLogs && (
            <div className="pdf-log mt-3" ref={logRef}>
              {tasks.logs.map((l, i) => (
                <div key={i} className="flex gap-2.5 py-0.5 border-b border-gray-100 last:border-0">
                  <span className="text-text-muted shrink-0">
                    {new Date(l.time).toLocaleTimeString('zh-CN', { hour12: false })}
                  </span>
                  <span
                    className={
                      l.level === 'error'
                        ? 'text-error'
                        : l.level === 'success'
                          ? 'text-success'
                          : l.level === 'warn'
                            ? 'text-warning'
                            : 'text-text'
                    }
                  >
                    {l.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {viewModel.shouldShowWorkflow && (
        <div id="pdf-pipeline">
          <PdfWorkflowPanel
          workflowStatus={workflow.workflowStatus}
          workflowLoading={workflow.workflowLoading}
          workflowBusy={workflow.workflowBusy}
          workflowJob={workflow.workflowJob}
          workflowError={workflow.workflowError}
          artifacts={tasks.artifacts}
          mode={workflowMode}
          title={workflowTitle}
          description={workflowDescription}
          loadWorkflowStatus={workflow.loadWorkflowStatus}
          runRemainingWorkflow={() => workflow.runRemainingWorkflow(workflowMode)}
          runWorkflowStep={workflow.runWorkflowStep}
        />
        </div>
      )}

      <PageSection
        id="pdf-result"
        title="解析结果"
        description="Markdown 原文、产物清单与质量报告。"
        className="border-0 bg-transparent shadow-none"
      >
        <div className="grid gap-4">
          <PdfMarkdownPreview
            markdown={tasks.markdown}
            mdLines={tasks.mdLines}
            focusedLine={tasks.focusedLine}
            taskId={tasks.taskIdRef.current}
            mdRef={mdRef}
            showToast={showToast}
          />

          <PdfArtifactList artifacts={tasks.artifacts || {}} />

          {tasks.quality && <PdfQualityPanel quality={tasks.quality} market={market} onShowTableSource={sourceTrace.showTableSource} />}
        </div>
      </PageSection>

      <PageSection
        id="pdf-source"
        title="可视化溯源"
        description="表格阅读视图与 PDF 原页对照，支持人工复核修正。"
        className="border-0 bg-transparent shadow-none"
      >
        <PdfSourceWorkbench
          sourceVisible={sourceTrace.sourceVisible}
          srcTable={sourceTrace.srcTable}
          srcMeta={sourceTrace.srcMeta}
          readingMode={sourceTrace.readingMode}
          switchReadingMode={sourceTrace.switchReadingMode}
          readingHtml={sourceTrace.readingHtml}
          pdfCtx={sourceTrace.pdfCtx}
          editTableRef={sourceTrace.editTableRef}
          pdfCurPage={sourceTrace.pdfCurPage}
          setPdfCurPage={sourceTrace.setPdfCurPage}
          pdfZoom={sourceTrace.pdfZoom}
          setPdfZoom={sourceTrace.setPdfZoom}
          getPdfUrl={sourceTrace.getPdfUrl}
          updatePdfViewer={sourceTrace.updatePdfViewer}
          onTableClick={editable.handleTableClick}
          onTableFocus={editable.handleTableFocus}
          onTableInput={editable.handleTableInput}
          onReadingClick={handleReadingClick}
          corrStatusRef={corrStatusRef}
          corrTextRef={corrTextRef}
          corrNoteRef={corrNoteRef}
          saveCorrection={editable.saveCorrection}
        />
      </PageSection>

      <PageSection
        id="pdf-tasks"
        title="最近任务"
        description="本地解析任务列表，可查看结果、补拉、重跑或删除。"
        className="border-0 bg-transparent shadow-none"
      >
        <PdfTaskList
          tasks={tasks.tasks}
          taskId={tasks.taskIdRef.current}
          resultLoading={tasks.resultLoading}
          onResume={onTaskResume}
          onViewResult={onTaskViewResult}
          onDelete={tasks.deleteTask}
          onRefetch={tasks.refetchTask}
          onReparse={tasks.reparseTask}
          onRefresh={() => void tasks.loadTasks()}
        />
      </PageSection>

      {viewModel.shouldShowEmptyState && (
        <EmptyState
          icon={FileText}
          title={emptyTitle}
          description={emptyDescription}
          className="surface-card border-dashed py-12"
        />
      )}
    </div>
  )
}
