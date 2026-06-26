import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FileText, Loader2 } from 'lucide-react'
import { checkHealth, fetchStatus, loadDownloadedReports as loadDownloadedReportsApi } from '../lib/pdfApi'
import { PDF_CSS } from './pdf/pdfStyles'
import { useEditableTable } from './pdf/useEditableTable'
import { usePdfSourceTrace } from './pdf/usePdfSourceTrace'
import { usePdfTasks } from './pdf/usePdfTasks'
import { usePdfWorkflow } from './pdf/usePdfWorkflow'
import { PdfArtifactList } from '../components/pdf/PdfArtifactList'
import { PdfFinancialPanel } from '../components/pdf/PdfFinancialPanel'
import { PdfHealthStrip } from '../components/pdf/PdfHealthStrip'
import { PdfMarkdownPreview } from '../components/pdf/PdfMarkdownPreview'
import { PdfQualityPanel } from '../components/pdf/PdfQualityPanel'
import { PdfSourceWorkbench } from '../components/pdf/PdfSourceWorkbench'
import { PdfTaskList } from '../components/pdf/PdfTaskList'
import { PdfUploadPanel } from '../components/pdf/PdfUploadPanel'
import { PdfWorkflowPanel } from '../components/pdf/PdfWorkflowPanel'
import { useToast } from '../hooks/useToast'
import type { DownloadedPdf, HealthStatus, TaskItem } from '../lib/pdfTypes'

export default function PdfParsing() {
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

  const tasks = usePdfTasks({
    backend,
    parseMethod,
    startPage,
    endPage,
    formula,
    table,
    showToast,
    onWorkflowReload: () => workflowRef.current.loadWorkflowStatus(),
    setSelectedFilesRef,
  })

  const workflow = usePdfWorkflow(tasks.taskIdRef, showToast, (msg: string | null) => tasks.setError(msg))

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
      const d = await loadDownloadedReportsApi(text)
      setDownloadedReports(d.reports || [])
    } catch {
      setDownloadedReports([])
    } finally {
      setDownloadedLoading(false)
    }
  }, [])

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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshHealth()
    void loadDownloadedReports('')
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
      const incoming = Array.from(files)
      if (!incoming.length) return
      if (incoming.length > 5) {
        tasks.setError('一次最多选择 5 个 PDF')
        return
      }
      for (const f of incoming) {
        if (!f.name.toLowerCase().endsWith('.pdf')) {
          tasks.setError('仅支持 PDF 文件')
          return
        }
        if (f.size > 100 * 1024 * 1024) {
          tasks.setError('文件超过 100 MB: ' + f.name)
          return
        }
      }
      setSelectedFiles(incoming)
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

  const isCompleted = tasks.parseBadge.cls === 'completed'

  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
      <style>{PDF_CSS}</style>

      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <FileText className="h-3.5 w-3.5" />
              Report Parsing
            </div>
            <h1 className="secondary-title">智能解析</h1>
            <p className="secondary-description">上传财报 PDF，生成 Markdown、表格、财务数据抽取和可视化溯源结果。</p>
          </div>
          <div className="secondary-step-row">
            <span className="secondary-step-chip is-active">解析</span>
            <span className="secondary-step-chip">抽取</span>
            <span className="secondary-step-chip">校验</span>
          </div>
        </div>
      </section>

      <PdfHealthStrip health={health} />

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

      {/* Upload Stage */}
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

      {/* Parse Stage */}
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

      {tasks.taskIdRef.current && isCompleted && tasks.resultDeferred && !tasks.markdown && (
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

      {/* Logs */}
      {tasks.logs.length > 0 && (
        <div className="apple-card rounded-[24px] p-4 sm:p-6">
          <h3 className="text-base font-semibold text-text mb-3">处理日志</h3>
          <div className="pdf-log" ref={logRef}>
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
        </div>
      )}

      {/* Data Pipeline */}
      {isCompleted && (
        <PdfWorkflowPanel
          workflowStatus={workflow.workflowStatus}
          workflowLoading={workflow.workflowLoading}
          workflowBusy={workflow.workflowBusy}
          workflowJob={workflow.workflowJob}
          workflowError={workflow.workflowError}
          artifacts={tasks.artifacts}
          loadWorkflowStatus={workflow.loadWorkflowStatus}
          runRemainingWorkflow={workflow.runRemainingWorkflow}
          runWorkflowStep={workflow.runWorkflowStep}
        />
      )}

      <PdfMarkdownPreview
        markdown={tasks.markdown}
        mdLines={tasks.mdLines}
        focusedLine={tasks.focusedLine}
        taskId={tasks.taskIdRef.current}
        mdRef={mdRef}
        showToast={showToast}
      />

      <PdfArtifactList artifacts={tasks.artifacts || {}} />

      {tasks.quality && <PdfQualityPanel quality={tasks.quality} onShowTableSource={sourceTrace.showTableSource} />}

      {tasks.financial && <PdfFinancialPanel financial={tasks.financial} taskId={tasks.taskIdRef.current} />}

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

      {!tasks.markdown && !tasks.parseActive && tasks.tasks.length === 0 && (
        <div className="rounded-[24px] border border-dashed border-border bg-card px-6 py-12 text-center text-text-muted shadow-sm">
          <FileText className="mx-auto mb-3 h-10 w-10 opacity-40" />
          <p className="text-sm font-semibold text-text">选择一份财报后开始解析</p>
          <p className="mt-1 text-xs">可从已下载列表直接解析，也支持批量上传最多 5 个 PDF。</p>
        </div>
      )}
    </div>
  )
}
