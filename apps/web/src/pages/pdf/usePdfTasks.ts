import { useCallback, useEffect, useRef, useState } from 'react'
import type { ArtifactsMap, DownloadedPdf, LogEntry, QualityReport, TaskItem } from '../../lib/pdfTypes'
import type { PdfMarket } from '../../lib/pdfTaskMarkets'
import {
  cancelTaskApi,
  checkHealth,
  deleteTaskApi,
  downloadedReportToFile,
  fetchQualityApi,
  fetchResultApi,
  fetchStatus,
  linkDownloadedReport,
  loadTasks as loadTasksApi,
  parseDownloadedReportFromDownload,
  reparseTaskApi,
  refetchTaskApi,
  uploadPdfs,
} from '../../features/pdf-parsing/api'
import { formatDuration, isTerminal, translateStatus } from '../../lib/pdfFormatting'
import { createTaskRequestScope } from './taskRequestScope'

const PARSE_PAGE_IDLE_DELAY_MS = 1800

function isMobileParseViewport(): boolean {
  if (typeof window === 'undefined') return false
  const ua = window.navigator.userAgent || ''
  return window.matchMedia('(max-width: 767px), (pointer: coarse)').matches || /MicroMessenger|Mobi|Android|iPhone|iPad/i.test(ua)
}

function scheduleParseIdleWork(callback: () => void): () => void {
  if (typeof window === 'undefined') return () => {}
  let cancelled = false
  let idleId = 0
  const timerId = window.setTimeout(() => {
    if (cancelled) return
    if ('requestIdleCallback' in window) {
      idleId = window.requestIdleCallback(
        () => {
          if (!cancelled) callback()
        },
        { timeout: 1800 },
      )
    } else {
      callback()
    }
  }, PARSE_PAGE_IDLE_DELAY_MS)

  return () => {
    cancelled = true
    window.clearTimeout(timerId)
    if (idleId && 'cancelIdleCallback' in window) window.cancelIdleCallback(idleId)
  }
}

function logEntryKey(entry: LogEntry): string {
  return `${entry.time || ''}\x1f${entry.level || ''}\x1f${entry.message || ''}`
}

function visibleErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export interface UsePdfTasksOptions {
  backend: string
  parseMethod: string
  startPage: string
  endPage: string
  formula: boolean
  table: boolean
  showToast: (msg: string) => void
  onError?: (msg: string | null) => void
  onWorkflowReload?: () => void
  taskFilter?: (task: TaskItem) => boolean
  market?: PdfMarket
  setSelectedFilesRef?: React.MutableRefObject<((files: File[]) => void) | null>
}

export function usePdfTasks(options: UsePdfTasksOptions) {
  const { backend, parseMethod, startPage, endPage, formula, table, showToast, onError, onWorkflowReload, taskFilter, market, setSelectedFilesRef } = options

  const taskIdRef = useRef<string | null>(null)
  const logCountRef = useRef(0)
  const cancelledRef = useRef(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollStatusRef = useRef<((scheduleNext?: boolean) => Promise<void>) | null>(null)
  const pollInFlightRef = useRef(false)
  const pollAbortRef = useRef<AbortController | null>(null)
  const logKeysRef = useRef<Set<string>>(new Set())
  const uploadRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const taskFilterRef = useRef<typeof taskFilter>(taskFilter)
  const [qualityRequestScope] = useState(createTaskRequestScope)
  const [resultRequestScope] = useState(createTaskRequestScope)
  const [resumeRequestScope] = useState(createTaskRequestScope)
  const [uploadRequestScope] = useState(createTaskRequestScope)
  const [tasksRequestScope] = useState(createTaskRequestScope)
  const [mutationRequestScope] = useState(createTaskRequestScope)

  const [logs, setLogs] = useState<LogEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  const [uploadActive, setUploadActive] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [uploadStatusText, setUploadStatusText] = useState('')
  const [uploadBadge, setUploadBadge] = useState({ cls: 'uploaded', text: '上传中' })

  const [parseActive, setParseActive] = useState(false)
  const [parsePct, setParsePct] = useState(0)
  const [parseStatusText, setParseStatusText] = useState('')
  const [parseBadge, setParseBadge] = useState({ cls: 'pending', text: '等待中' })
  const [queueInfo, setQueueInfo] = useState('')
  const [elapsedInfo, setElapsedInfo] = useState('')
  const [pagesInfo, setPagesInfo] = useState('')
  const [stageInfo, setStageInfo] = useState('')

  const [markdown, setMarkdown] = useState('')
  const [mdLines, setMdLines] = useState<string[]>([])
  const [focusedLine, setFocusedLine] = useState<number | null>(null)
  const [artifacts, setArtifacts] = useState<ArtifactsMap | null>(null)
  const [quality, setQuality] = useState<QualityReport | null>(null)
  const [resultDeferred, setResultDeferred] = useState(false)
  const [resultLoading, setResultLoading] = useState(false)

  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)

  const internalSetSelectedFilesRef = useRef<((files: File[]) => void) | null>(null)
  const selectedFilesSetterRef = setSelectedFilesRef ?? internalSetSelectedFilesRef

  useEffect(() => {
    taskFilterRef.current = taskFilter
  }, [taskFilter])

  const reportError = useCallback(
    (msg: string | null) => {
      setError(msg)
      onError?.(msg)
    },
    [onError],
  )

  const resetResult = useCallback(() => {
    setMarkdown('')
    setMdLines([])
    setArtifacts(null)
    setQuality(null)
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current)
      pollRef.current = null
    }
    pollAbortRef.current?.abort()
    pollAbortRef.current = null
    pollInFlightRef.current = false
  }, [])

  const resetAll = useCallback(() => {
    qualityRequestScope.invalidate()
    resultRequestScope.invalidate()
    resumeRequestScope.invalidate()
    uploadRequestScope.invalidate()
    tasksRequestScope.invalidate()
    mutationRequestScope.invalidate()
    selectedFilesSetterRef.current?.([])
    reportError(null)
    setUploadActive(false)
    setParseActive(false)
    setLogs([])
    logKeysRef.current.clear()
    resetResult()
    setResultDeferred(false)
    setResultLoading(false)
    setTasksLoading(false)
    setTasksError(null)
    setUploading(false)
    stopPolling()
    if (uploadRef.current) {
      clearInterval(uploadRef.current)
      uploadRef.current = null
    }
  }, [mutationRequestScope, qualityRequestScope, reportError, resetResult, resultRequestScope, resumeRequestScope, selectedFilesSetterRef, stopPolling, tasksRequestScope, uploadRequestScope])

  // Ref to latest callbacks that need to call each other (avoid circular stale closures).
  const callbacksRef = useRef<{
    fetchResult?: () => Promise<void>
    loadTasks?: (opts?: { autoResume?: boolean }) => Promise<void>
    resumeTask?: (taskId: string, filename: string, status: string) => Promise<void>
  }>({})

  const fetchQuality = useCallback(async (requestedTaskId?: string | null) => {
    const tid = requestedTaskId || taskIdRef.current
    if (!tid) return
    const request = qualityRequestScope.begin(tid)
    try {
      const d = await fetchQualityApi(tid)
      if (qualityRequestScope.isCurrent(request, taskIdRef.current) && d.quality) setQuality(d.quality as QualityReport)
    } catch {
      // ignore quality fetch errors
    }
  }, [qualityRequestScope])

  const fetchResult = useCallback(async () => {
    const tid = taskIdRef.current
    if (!tid) return
    const request = resultRequestScope.begin(tid)
    setResultLoading(true)
    setResultDeferred(false)
    reportError(null)
    try {
      const d = await fetchResultApi(tid)
      if (!resultRequestScope.isCurrent(request, taskIdRef.current)) return
      if (d.artifacts) setArtifacts(d.artifacts)
      if (d.markdown) {
        setMarkdown(d.markdown)
        setMdLines(d.markdown.split(/\r?\n/))
        await fetchQuality(tid)
        if (!resultRequestScope.isCurrent(request, taskIdRef.current)) return
      }
    } catch (error) {
      if (!resultRequestScope.isCurrent(request, taskIdRef.current)) return
      setResultDeferred(true)
      reportError(`解析结果拉取失败：${visibleErrorMessage(error, '请稍后重试')}`)
    } finally {
      if (resultRequestScope.isCurrent(request, taskIdRef.current)) setResultLoading(false)
    }
  }, [fetchQuality, reportError, resultRequestScope])

  const updateStatus = useCallback(
    (data: Record<string, unknown>) => {
      if (cancelledRef.current) return
      const stage = String(data.stage || data.status || 'pending')
      const status = String(data.status || 'pending')
      const isFail = ['failed', 'error', 'failure', 'completed_missing_artifact'].includes(status)
      const isDone = ['completed', 'success', 'done', 'finished'].includes(status)
      setParseBadge({ cls: stage, text: translateStatus(status) })
      setQueueInfo(
        data.local_queue_position
          ? `本地队列位置: 第 ${data.local_queue_position} 位`
          : data.queue_position != null
            ? `解析队列前方: ${data.queue_position} 任务`
            : '',
      )
      setElapsedInfo(!isFail && data.elapsed_seconds != null ? `已耗时: ${formatDuration(Number(data.elapsed_seconds))}` : '')
      if (!isFail && data.total_pages && data.processed_pages != null) {
        const rem = Number(data.total_pages) - Number(data.processed_pages)
        setPagesInfo(`已完成 ${data.processed_pages}/${data.total_pages} 页, 还剩 ${rem} 页`)
      } else if (!isFail && data.total_pages) {
        setPagesInfo(`共 ${data.total_pages} 页`)
      } else {
        setPagesInfo('')
      }
      const sMap: Record<string, string> = {
        queued: '已加入本地队列',
        uploaded: '文件已上传',
        submitting: '正在提交到解析服务',
        submitted: '已提交到解析服务',
        pending: '排队等待中',
        processing: '正在解析 PDF',
        completed: '解析完成',
        completed_missing_artifact: '结果缺失',
        failed: '解析失败',
        cancelled: '已停止查看',
      }
      setStageInfo(sMap[stage] || stage)
      let pct = 0
      if (isDone) {
        pct = 100
      } else if (stage === 'processing' && data.progress_percent != null) {
        pct = Math.max(0, Math.min(99, Number(data.progress_percent)))
      } else if (stage === 'processing' && data.total_pages && data.processed_pages != null) {
        pct = Math.round((Number(data.processed_pages) / Number(data.total_pages)) * 100)
      }
      setParsePct(pct)
      setParseStatusText(translateStatus(status))
      const incomingLogs = Array.isArray(data.logs) ? (data.logs as LogEntry[]) : []
      if (incomingLogs.length) {
        const uniqueLogs: LogEntry[] = []
        incomingLogs.forEach((entry) => {
          const key = logEntryKey(entry)
          if (logKeysRef.current.has(key)) return
          logKeysRef.current.add(key)
          uniqueLogs.push(entry)
        })
        if (logKeysRef.current.size > 600) {
          logKeysRef.current = new Set(Array.from(logKeysRef.current).slice(-300))
        }
        if (uniqueLogs.length) setLogs((prev) => [...prev, ...uniqueLogs])
      }
      logCountRef.current =
        typeof data.log_count === 'number'
          ? Math.max(logCountRef.current, data.log_count)
          : logCountRef.current + incomingLogs.length

      if (isDone) {
        setParsePct(100)
        setParseStatusText('解析完成!')
        setParseBadge({ cls: 'completed', text: '已完成' })
        stopPolling()
        if (isMobileParseViewport()) {
          setResultDeferred(true)
        } else {
          void callbacksRef.current.fetchResult?.()
        }
        void callbacksRef.current.loadTasks?.()
        onWorkflowReload?.()
      } else if (isFail) {
        setParsePct(0)
        setParseStatusText(status === 'completed_missing_artifact' ? '结果缺失' : '解析失败')
        setParseBadge({ cls: 'failed', text: translateStatus(status) })
        setElapsedInfo('')
        setPagesInfo('')
        stopPolling()
        reportError(String(data.error || '转换失败'))
      } else if (status === 'cancelled') {
        setParsePct(0)
        setParseStatusText('已停止查看')
        setParseBadge({ cls: 'cancelled', text: '已停止' })
        stopPolling()
      }
    },
    [onWorkflowReload, reportError, stopPolling],
  )

  const pollStatus = useCallback(
    async (scheduleNext = false) => {
      const tid = taskIdRef.current
      if (!tid || cancelledRef.current) return
      if (pollRef.current) {
        clearTimeout(pollRef.current)
        pollRef.current = null
      }
      if (pollInFlightRef.current) return
      pollInFlightRef.current = true
      pollAbortRef.current?.abort()
      const controller = new AbortController()
      pollAbortRef.current = controller
      let shouldScheduleNext = false
      try {
        const d = await fetchStatus(tid, logCountRef.current, { signal: controller.signal })
        if (controller.signal.aborted || taskIdRef.current !== tid || cancelledRef.current) return
        updateStatus(d)
        const latestStatus = String(d.status || d.stage || 'pending')
        shouldScheduleNext = !isTerminal(latestStatus) && !['completed', 'success', 'done', 'finished'].includes(latestStatus)
      } catch {
        if (!controller.signal.aborted && taskIdRef.current === tid && !cancelledRef.current) {
          setParseStatusText('状态查询失败，正在重试...')
          shouldScheduleNext = true
        }
      } finally {
        if (pollAbortRef.current === controller) pollAbortRef.current = null
        pollInFlightRef.current = false
        if (scheduleNext && shouldScheduleNext && taskIdRef.current === tid && !cancelledRef.current) {
          pollRef.current = setTimeout(() => {
            void pollStatusRef.current?.(true)
          }, 1000)
        }
      }
    },
    [updateStatus],
  )

  useEffect(() => {
    pollStatusRef.current = pollStatus
    return () => {
      if (pollStatusRef.current === pollStatus) pollStatusRef.current = null
    }
  }, [pollStatus])

  const startPolling = useCallback(() => {
    stopPolling()
    void pollStatus(true)
  }, [pollStatus, stopPolling])

  const loadTasks = useCallback(
    async (opts: { autoResume?: boolean } = {}) => {
      const autoResume = opts.autoResume !== false
      const request = tasksRequestScope.begin()
      setTasksLoading(true)
      setTasksError(null)
      try {
        const allTasks = (await loadTasksApi({ signal: request.signal })) as unknown as TaskItem[]
        if (!tasksRequestScope.isCurrent(request)) return
        const filter = taskFilterRef.current
        const list = filter ? allTasks.filter(filter) : allTasks
        setTasks(list)
        if (autoResume && !taskIdRef.current && list.length) {
          const latest =
            list.find(
              (t) => t.markdown_ready && ['completed', 'success', 'done', 'finished'].includes(String(t.status)),
            ) ||
            list.find((t) => ['processing', 'pending', 'submitted', 'submitting'].includes(String(t.status))) ||
            list.find((t) => t.status === 'queued') ||
            list.find((t) => !isTerminal(String(t.status)))
          if (latest) {
            await callbacksRef.current.resumeTask?.(latest.task_id, String(latest.filename || ''), String(latest.status))
          }
        }
      } catch (error) {
        if (!tasksRequestScope.isCurrent(request)) return
        setTasksError(visibleErrorMessage(error, '最近任务加载失败，请稍后重试'))
      } finally {
        if (tasksRequestScope.isCurrent(request)) setTasksLoading(false)
      }
    },
    [tasksRequestScope],
  )

  const resumeTask = useCallback(
    async (taskId: string, _filename: string, status: string) => {
      if (!taskId) return
      stopPolling()
      resultRequestScope.invalidate()
      qualityRequestScope.invalidate()
      uploadRequestScope.invalidate()
      taskIdRef.current = taskId
      const request = resumeRequestScope.begin(taskId)
      cancelledRef.current = false
      logCountRef.current = 0
      logKeysRef.current.clear()
      setLogs([])
      reportError(null)
      setUploadActive(false)
      setUploading(false)
      if (uploadRef.current) {
        clearInterval(uploadRef.current)
        uploadRef.current = null
      }
      setParseActive(true)
      resetResult()
      const deferResult = isMobileParseViewport()
      setResultDeferred(false)
      setResultLoading(false)
      setParsePct(0)
      setParseStatusText('正在恢复任务状态...')
      setParseBadge({ cls: status, text: translateStatus(status) })
      try {
        const latestData = await fetchStatus(taskId, 0).catch(() => null)
        if (!resumeRequestScope.isCurrent(request, taskIdRef.current)) return
        if (latestData) {
          updateStatus(latestData)
          const latestStatus = String(latestData.status || latestData.stage || status)
          if (!isTerminal(latestStatus)) {
            startPolling()
          }
          showToast('已恢复任务视图')
          return
        }

        if (['completed', 'success', 'done', 'finished'].includes(status)) {
          setParsePct(100)
          setParseStatusText('解析完成')
          setParseBadge({ cls: 'completed', text: '已完成' })
          onWorkflowReload?.()
          if (deferResult) setResultDeferred(true)
          else void callbacksRef.current.fetchResult?.()
          showToast('已恢复任务视图')
          return
        }

        await pollStatus()
        if (!resumeRequestScope.isCurrent(request, taskIdRef.current)) return
        const latest = await fetchStatus(taskId, 0).catch(() => null)
        if (!resumeRequestScope.isCurrent(request, taskIdRef.current)) return
        const latestStatus = String(latest?.status || status)
        if (!isTerminal(latestStatus)) {
          startPolling()
        } else if (['completed', 'success', 'done', 'finished'].includes(latestStatus)) {
          onWorkflowReload?.()
          if (deferResult) setResultDeferred(true)
          else void callbacksRef.current.fetchResult?.()
        }
        showToast('已恢复任务视图')
      } catch {
        if (resumeRequestScope.isCurrent(request, taskIdRef.current)) reportError('恢复任务状态失败')
      }
    },
    [onWorkflowReload, pollStatus, qualityRequestScope, reportError, resetResult, resultRequestScope, resumeRequestScope, showToast, startPolling, stopPolling, updateStatus, uploadRequestScope],
  )

  const startConvertWithFiles = useCallback(
    async (filesToUpload: File[]) => {
      if (!filesToUpload.length) return
      const request = uploadRequestScope.begin()
      await checkHealth().catch(() => null)
      if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
      stopPolling()
      resumeRequestScope.invalidate()
      resultRequestScope.invalidate()
      qualityRequestScope.invalidate()
      reportError(null)
      setUploading(true)
      cancelledRef.current = false
      logCountRef.current = 0
      logKeysRef.current.clear()
      setLogs([])
      setUploadActive(true)
      setUploadPct(0)
      setUploadStatusText('准备上传...')
      setUploadBadge({ cls: 'uploaded', text: '上传中' })
      setParseActive(false)
      resetResult()
      setResultDeferred(false)
      setResultLoading(false)

      let pct = 0
      const uploadTimer = setInterval(() => {
        if (cancelledRef.current || !uploadRequestScope.isCurrent(request, taskIdRef.current)) {
          clearInterval(uploadTimer)
          return
        }
        pct += Math.random() * 15
        if (pct > 90) pct = 90
        setUploadPct(pct)
        setUploadStatusText('正在上传并加入本地队列...')
      }, 300)
      uploadRef.current = uploadTimer

      const form = new FormData()
      filesToUpload.forEach((f) => form.append('files', f))
      form.append('backend', backend)
      form.append('parse_method', parseMethod)
      form.append('market', market || 'CN')
      form.append('start_page_id', startPage)
      form.append('end_page_id', endPage)
      form.append('formula_enable', formula ? 'true' : 'false')
      form.append('table_enable', table ? 'true' : 'false')

      try {
        const d = await uploadPdfs(form)
        clearInterval(uploadTimer)
        if (uploadRef.current === uploadTimer) uploadRef.current = null
        if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
        resumeRequestScope.invalidate()
        resultRequestScope.invalidate()
        qualityRequestScope.invalidate()
        taskIdRef.current = String(d.task_id)
        setUploadPct(100)
        setUploadStatusText('批量入队完成')
        setUploadBadge({ cls: 'completed', text: '已完成' })
        setParseActive(true)
        setParsePct(0)
        setParseStatusText('已加入本地队列，等待轮到当前任务...')
        setParseBadge({ cls: 'queued', text: '已排队' })
        setQueueInfo('')
        setElapsedInfo('')
        setPagesInfo('')
        setStageInfo('')
        showToast(`已加入队列: ${String(d.batch_count || filesToUpload.length)} 个 PDF`)
        selectedFilesSetterRef.current?.([])
        setUploading(false)
        startPolling()
        void loadTasks()
      } catch (e) {
        clearInterval(uploadTimer)
        if (uploadRef.current === uploadTimer) uploadRef.current = null
        if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
        setUploading(false)
        const err = e as Error & { response?: Record<string, unknown>; status?: number }
        if (err.status === 409) {
          const d = err.response || {}
          const existingTask = (d.existingTask as TaskItem) || (d.existing_task as TaskItem)
          setUploadPct(0)
          setUploadStatusText(String(d.message || '该文件已存在解析任务，请勿重复解析'))
          setUploadBadge({ cls: 'uploaded', text: '已存在' })
          if (existingTask?.task_id) {
            showToast(String(d.message || '该文件已存在解析任务'))
            await callbacksRef.current.resumeTask?.(
              String(existingTask.task_id),
              String(existingTask.filename || d.filename || ''),
              String(existingTask.status || 'pending'),
            )
            void loadTasks()
            return
          }
        }
        reportError(err.message)
        setUploadPct(0)
        setUploadStatusText('上传失败')
        setUploadBadge({ cls: 'failed', text: '失败' })
      }
    },
    [backend, endPage, formula, loadTasks, market, parseMethod, qualityRequestScope, reportError, resetResult, resultRequestScope, resumeRequestScope, selectedFilesSetterRef, showToast, startPolling, startPage, stopPolling, table, uploadRequestScope],
  )

  const startConvert = useCallback(
    async (selectedFiles: File[]) => {
      await startConvertWithFiles(selectedFiles)
    },
    [startConvertWithFiles],
  )

  const startConvertWithDownloadedReport = useCallback(
    async (report: DownloadedPdf) => {
      const request = uploadRequestScope.begin()
      await checkHealth().catch(() => null)
      if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
      stopPolling()
      resumeRequestScope.invalidate()
      resultRequestScope.invalidate()
      qualityRequestScope.invalidate()
      reportError(null)
      setUploading(true)
      cancelledRef.current = false
      logCountRef.current = 0
      logKeysRef.current.clear()
      setLogs([])
      setUploadActive(true)
      setUploadPct(25)
      setUploadStatusText('正在提交已下载财报到解析队列...')
      setUploadBadge({ cls: 'uploaded', text: '入队中' })
      setParseActive(false)
      resetResult()
      setResultDeferred(false)
      setResultLoading(false)

      try {
        const parseMarket = market || (report.market && report.market !== 'DOC' ? report.market : undefined) || 'CN'
        const d = await parseDownloadedReportFromDownload(report, {
          backend,
          parseMethod,
          market: parseMarket,
          startPage,
          endPage,
          formula,
          table,
        })
        if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
        resumeRequestScope.invalidate()
        resultRequestScope.invalidate()
        qualityRequestScope.invalidate()
        taskIdRef.current = String(d.task_id)
        setUploadPct(100)
        setUploadStatusText('服务端引用入队完成')
        setUploadBadge({ cls: 'completed', text: '已完成' })
        setParseActive(true)
        setParsePct(0)
        setParseStatusText('已加入本地队列，等待轮到当前任务...')
        setParseBadge({ cls: 'queued', text: '已排队' })
        setQueueInfo('')
        setElapsedInfo('')
        setPagesInfo('')
        setStageInfo('')
        showToast(`已加入队列: ${String(d.batch_count || 1)} 个 PDF`)
        selectedFilesSetterRef.current?.([])
        setUploading(false)
        startPolling()
        void loadTasks()
      } catch (e) {
        if (!uploadRequestScope.isCurrent(request, taskIdRef.current)) return
        setUploading(false)
        const err = e as Error & { response?: Record<string, unknown>; status?: number }
        if (err.status === 409) {
          const d = err.response || {}
          const existingTask = (d.existingTask as TaskItem) || (d.existing_task as TaskItem)
          setUploadPct(0)
          setUploadStatusText(String(d.message || '该文件已存在解析任务，请勿重复解析'))
          setUploadBadge({ cls: 'uploaded', text: '已存在' })
          if (existingTask?.task_id) {
            showToast(String(d.message || '该文件已存在解析任务'))
            await callbacksRef.current.resumeTask?.(
              String(existingTask.task_id),
              String(existingTask.filename || d.filename || ''),
              String(existingTask.status || 'pending'),
            )
            void loadTasks()
            return
          }
        }
        reportError(err.message)
        setUploadPct(0)
        setUploadStatusText('入队失败')
        setUploadBadge({ cls: 'failed', text: '失败' })
      }
    },
    [backend, endPage, formula, loadTasks, market, parseMethod, qualityRequestScope, reportError, resetResult, resultRequestScope, resumeRequestScope, selectedFilesSetterRef, showToast, startPage, startPolling, stopPolling, table, uploadRequestScope],
  )

  const cancelTask = useCallback(async () => {
    const tid = taskIdRef.current
    if (!tid) return
    if (!confirm('确定停止查看当前任务吗？\n如果解析服务支持取消，也会尝试通知后端停止处理。')) return
    try {
      const d = await cancelTaskApi(tid)
      if (taskIdRef.current !== tid) return
      if (d.success) {
        cancelledRef.current = true
        stopPolling()
        showToast(d.upstream_cancelled ? '任务已取消' : '已停止查看任务')
      }
    } catch {
      // ignore cancel errors
    }
  }, [showToast, stopPolling])

  const deleteTask = useCallback(
    async (taskId: string, status: string) => {
      if (!taskId) return
      if (!isTerminal(status)) {
        reportError('请先停止或等待任务结束后再删除')
        return
      }
      if (!confirm('确定删除这条最近任务记录吗？')) return
      const request = mutationRequestScope.begin(taskIdRef.current)
      try {
        await deleteTaskApi(taskId)
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        if (taskIdRef.current === taskId) {
          taskIdRef.current = null
          resetAll()
        }
        await loadTasks()
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        showToast('任务记录已删除')
      } catch (e) {
        if (mutationRequestScope.isCurrent(request, taskIdRef.current)) reportError((e as Error).message)
      }
    },
    [loadTasks, mutationRequestScope, reportError, resetAll, showToast],
  )

  const refetchTask = useCallback(
    async (taskId: string) => {
      if (!taskId) return
      const request = mutationRequestScope.begin(taskIdRef.current)
      try {
        await refetchTaskApi(taskId)
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        if (taskIdRef.current === taskId) {
          void callbacksRef.current.fetchResult?.()
        }
        await loadTasks()
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        showToast('结果已重新拉取')
      } catch (e) {
        if (mutationRequestScope.isCurrent(request, taskIdRef.current)) reportError((e as Error).message)
      }
    },
    [loadTasks, mutationRequestScope, reportError, showToast],
  )

  const reparseTask = useCallback(
    async (taskId: string) => {
      if (!taskId) return
      if (!confirm('确定基于原 PDF 创建一个重新解析任务吗？')) return
      const request = mutationRequestScope.begin(taskIdRef.current)
      try {
        const d = await reparseTaskApi(taskId)
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        await loadTasks()
        if (!mutationRequestScope.isCurrent(request, taskIdRef.current)) return
        await callbacksRef.current.resumeTask?.(d.task_id, d.filename, 'queued')
        showToast('重新解析任务已入队')
      } catch (e) {
        if (mutationRequestScope.isCurrent(request, taskIdRef.current)) reportError((e as Error).message)
      }
    },
    [loadTasks, mutationRequestScope, reportError, showToast],
  )

  const viewTaskResult = useCallback(
    async (task: TaskItem) => {
      await resumeTask(task.task_id, String(task.filename || ''), String(task.status))
    },
    [resumeTask],
  )

  const selectDownloadedReport = useCallback(
    async (report: DownloadedPdf, onBusy: (path: string) => void) => {
      onBusy(report.relativePath)
      reportError(null)
      try {
        await linkDownloadedReport(report)
        const file = await downloadedReportToFile(report)
        selectedFilesSetterRef.current?.([file])
        showToast('已选择已下载财报')
      } catch (e) {
        reportError((e as Error).message)
      } finally {
        onBusy('')
      }
    },
    [reportError, selectedFilesSetterRef, showToast],
  )

  const parseDownloadedReport = useCallback(
    async (report: DownloadedPdf, onBusy: (path: string) => void) => {
      onBusy(report.relativePath)
      reportError(null)
      try {
        await linkDownloadedReport(report)
        await startConvertWithDownloadedReport(report)
      } catch (e) {
        reportError((e as Error).message)
      } finally {
        onBusy('')
      }
    },
    [reportError, startConvertWithDownloadedReport],
  )

  const idleLoad = useCallback(() => {
    setTasksLoading(true)
    setTasksError(null)
    const cancel = scheduleParseIdleWork(() => {
      void loadTasks({ autoResume: false })
    })
    return cancel
  }, [loadTasks])

  // Sync callbacks that have circular dependencies into a ref after they are defined.
  useEffect(() => {
    callbacksRef.current.fetchResult = fetchResult
    callbacksRef.current.loadTasks = loadTasks
    callbacksRef.current.resumeTask = resumeTask
  }, [fetchResult, loadTasks, resumeTask])

  useEffect(() => {
    return () => {
      stopPolling()
      if (uploadRef.current) {
        clearInterval(uploadRef.current)
        uploadRef.current = null
      }
    }
  }, [stopPolling])

  return {
    taskIdRef,
    logs,
    setLogs,
    error,
    setError,
    uploadActive,
    uploadPct,
    uploadStatusText,
    uploadBadge,
    parseActive,
    parsePct,
    parseStatusText,
    parseBadge,
    queueInfo,
    elapsedInfo,
    pagesInfo,
    stageInfo,
    markdown,
    mdLines,
    focusedLine,
    setFocusedLine,
    artifacts,
    quality,
    resultDeferred,
    resultLoading,
    tasks,
    setTasks,
    tasksLoading,
    tasksError,
    uploading,
    setUploading,
    startConvert,
    startConvertWithFiles,
    cancelTask,
    resumeTask,
    deleteTask,
    refetchTask,
    reparseTask,
    fetchResult,
    viewTaskResult,
    loadTasks,
    idleLoad,
    resetAll,
    selectDownloadedReport,
    parseDownloadedReport,
    setSelectedFilesRef: selectedFilesSetterRef,
  }
}
