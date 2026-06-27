import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  DocumentBlocksPayload,
  DocumentFiguresPayload,
  DocumentLogEntry,
  DocumentParseConfig,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
} from '../../lib/documentTypes'
import {
  createDocumentTaskFromUrl,
  createDocumentTasks,
  deleteDocumentTask,
  fetchDocumentBlocks,
  fetchDocumentFigures,
  fetchDocumentQuality,
  fetchDocumentResult,
  fetchDocumentSourceMap,
  fetchDocumentStatus,
  fetchDocumentTables,
  loadDocumentTasks,
  retryDocumentTask,
  runDocumentExtraction,
} from '../../lib/documentApi'

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'cancelled'])

export function isDocumentTerminal(status?: string) {
  return terminalStatuses.has(String(status || '').toLowerCase())
}

export function useDocumentTasks(showToast: (message: string) => void) {
  const selectedTaskIdRef = useRef<string | null>(null)
  const pollRef = useRef<ReturnType<typeof window.setInterval> | null>(null)
  const logCountRef = useRef(0)

  const [tasks, setTasks] = useState<DocumentTaskItem[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [result, setResult] = useState<DocumentResult | null>(null)
  const [quality, setQuality] = useState<DocumentQualityReport | null>(null)
  const [blocks, setBlocks] = useState<DocumentBlocksPayload | null>(null)
  const [tables, setTables] = useState<DocumentTablesPayload | null>(null)
  const [figures, setFigures] = useState<DocumentFiguresPayload | null>(null)
  const [sourceMap, setSourceMap] = useState<DocumentSourceMapPayload | null>(null)
  const [logs, setLogs] = useState<DocumentLogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [extractionResult, setExtractionResult] = useState<Record<string, unknown> | null>(null)

  const refreshTasks = useCallback(async () => {
    const items = await loadDocumentTasks()
    setTasks(items)
    return items
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const loadArtifacts = useCallback(async (taskId: string) => {
    setLoading(true)
    try {
      const [resultData, qualityData, blocksData, tablesData, figuresData, sourceMapData] = await Promise.all([
        fetchDocumentResult(taskId),
        fetchDocumentQuality(taskId).catch(() => null),
        fetchDocumentBlocks(taskId).catch(() => null),
        fetchDocumentTables(taskId).catch(() => null),
        fetchDocumentFigures(taskId).catch(() => null),
        fetchDocumentSourceMap(taskId).catch(() => null),
      ])
      setResult(resultData)
      setQuality(qualityData)
      setBlocks(blocksData)
      setTables(tablesData)
      setFigures(figuresData)
      setSourceMap(sourceMapData)
    } finally {
      setLoading(false)
    }
  }, [])

  const pollStatus = useCallback(async (taskId: string) => {
    try {
      const status = await fetchDocumentStatus(taskId, logCountRef.current)
      if (Array.isArray(status.logs) && status.logs.length) {
        setLogs((prev) => [...prev, ...(status.logs as DocumentLogEntry[])])
      }
      if (typeof status.log_count === 'number') logCountRef.current = status.log_count
      setTasks((prev) => prev.map((item) => (item.task_id === taskId ? { ...item, ...status } : item)))
      if (isDocumentTerminal(status.status)) {
        stopPolling()
        await refreshTasks()
        if (status.status === 'completed' || status.status === 'completed_with_warnings') {
          await loadArtifacts(taskId)
        }
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '状态查询失败')
    }
  }, [loadArtifacts, refreshTasks, stopPolling])

  const selectTask = useCallback(async (taskId: string) => {
    selectedTaskIdRef.current = taskId
    setSelectedTaskId(taskId)
    setError(null)
    setLogs([])
    logCountRef.current = 0
    stopPolling()
    const status = await fetchDocumentStatus(taskId, 0).catch(() => null)
    if (status?.logs?.length) setLogs(status.logs as DocumentLogEntry[])
    if (typeof status?.log_count === 'number') logCountRef.current = status.log_count
    if (status && isDocumentTerminal(status.status)) {
      if (status.status === 'completed' || status.status === 'completed_with_warnings') await loadArtifacts(taskId)
    } else {
      setResult(null)
      setQuality(null)
      setBlocks(null)
      setTables(null)
      setFigures(null)
      setSourceMap(null)
      pollRef.current = window.setInterval(() => void pollStatus(taskId), 1600)
      void pollStatus(taskId)
    }
  }, [loadArtifacts, pollStatus, stopPolling])

  const submitFiles = useCallback(async (files: File[], config: DocumentParseConfig) => {
    if (!files.length) return
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      files.forEach((file) => form.append('files', file))
      form.set('source_type', 'upload')
      form.set('model_version', config.modelVersion)
      form.set('ocr', config.ocr)
      form.set('enable_formula', String(config.enableFormula))
      form.set('enable_table', String(config.enableTable))
      form.set('language', config.language)
      form.set('page_ranges', config.pageRanges)
      form.set('extra_formats', config.extraFormats.join(','))
      form.set('no_cache', String(config.noCache))
      const data = await createDocumentTasks(form)
      await refreshTasks()
      const firstTask = data.tasks?.[0]
      if (firstTask?.task_id) await selectTask(firstTask.task_id)
      showToast(`已创建 ${data.tasks?.length || 0} 个文档解析任务`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }, [refreshTasks, selectTask, showToast])

  const submitUrl = useCallback(async (url: string, config: DocumentParseConfig) => {
    const target = url.trim()
    if (!target) return
    setUploading(true)
    setError(null)
    try {
      const data = await createDocumentTaskFromUrl({
        url: target,
        model_version: config.modelVersion,
        ocr: config.ocr,
        enable_formula: config.enableFormula,
        enable_table: config.enableTable,
        language: config.language,
        page_ranges: config.pageRanges,
        extra_formats: config.extraFormats,
        no_cache: config.noCache,
      })
      await refreshTasks()
      const firstTask = data.tasks?.[0]
      if (firstTask?.task_id) await selectTask(firstTask.task_id)
      showToast('URL 文档解析任务已创建')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'URL 解析失败')
    } finally {
      setUploading(false)
    }
  }, [refreshTasks, selectTask, showToast])

  const retryTask = useCallback(async (taskId: string) => {
    setError(null)
    await retryDocumentTask(taskId)
    await refreshTasks()
    await selectTask(taskId)
  }, [refreshTasks, selectTask])

  const deleteTask = useCallback(async (taskId: string) => {
    await deleteDocumentTask(taskId)
    if (selectedTaskIdRef.current === taskId) {
      selectedTaskIdRef.current = null
      setSelectedTaskId('')
      setResult(null)
      setQuality(null)
      setBlocks(null)
      setTables(null)
      setFigures(null)
      setSourceMap(null)
    }
    await refreshTasks()
  }, [refreshTasks])

  const runExtraction = useCallback(async (schemaText: string, instructions: string) => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return
    setError(null)
    try {
      const schema = schemaText.trim() ? JSON.parse(schemaText) : {}
      const data = await runDocumentExtraction(taskId, {
        mode: 'schema',
        schema,
        instructions,
        require_evidence: true,
      })
      setExtractionResult(data)
      showToast('结构化抽取已完成')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '结构化抽取失败')
    }
  }, [showToast])

  useEffect(() => {
    void refreshTasks()
    return () => stopPolling()
  }, [refreshTasks, stopPolling])

  return {
    tasks,
    selectedTaskId,
    result,
    quality,
    blocks,
    tables,
    figures,
    sourceMap,
    logs,
    loading,
    uploading,
    error,
    extractionResult,
    setError,
    refreshTasks,
    selectTask,
    submitFiles,
    submitUrl,
    retryTask,
    deleteTask,
    loadArtifacts,
    runExtraction,
  }
}
