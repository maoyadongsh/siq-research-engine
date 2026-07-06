import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  DocumentBlocksPayload,
  DocumentExtractionTemplate,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentLogEntry,
  DocumentMineruImportCandidate,
  DocumentParseConfig,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTableRelationsPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
} from '../../lib/documentTypes'
import {
  buildDocumentSemanticChunks,
  createDocumentTaskFromUrl,
  createDocumentTasks,
  deleteDocumentTask,
  documentBatchDownloadUrl,
  fetchDocumentBlocks,
  fetchDocumentExtractionTemplates,
  fetchDocumentFigures,
  fetchDocumentLayoutBlocks,
  fetchDocumentQuality,
  fetchDocumentResult,
  fetchDocumentSourceMap,
  fetchDocumentStatus,
  fetchDocumentTableRelations,
  fetchDocumentTables,
  fetchDocumentWorkflowStatus,
  fetchMineruImportCandidates,
  importDocumentFromMineru,
  importDocumentToDatabase,
  importDocumentToWiki,
  loadDocumentTasks,
  reviewDocumentTableRelation,
  retryDocumentTask,
  runDocumentExtraction,
} from '../../features/document-parser/api'
import { downloadAuthenticatedFile } from '../../lib/authenticatedFiles'

const terminalStatuses = new Set(['completed', 'completed_with_warnings', 'failed', 'cancelled'])

export function isDocumentTerminal(status?: string) {
  return terminalStatuses.has(String(status || '').toLowerCase())
}

export function useDocumentTasks(showToast: (message: string) => void) {
  const selectedTaskIdRef = useRef<string | null>(null)
  const pollRef = useRef<number | null>(null)
  const logCountRef = useRef(0)

  const [tasks, setTasks] = useState<DocumentTaskItem[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [result, setResult] = useState<DocumentResult | null>(null)
  const [quality, setQuality] = useState<DocumentQualityReport | null>(null)
  const [blocks, setBlocks] = useState<DocumentBlocksPayload | null>(null)
  const [layout, setLayout] = useState<DocumentLayoutBlocksPayload | null>(null)
  const [tables, setTables] = useState<DocumentTablesPayload | null>(null)
  const [tableRelations, setTableRelations] = useState<DocumentTableRelationsPayload | null>(null)
  const [figures, setFigures] = useState<DocumentFiguresPayload | null>(null)
  const [sourceMap, setSourceMap] = useState<DocumentSourceMapPayload | null>(null)
  const [logs, setLogs] = useState<DocumentLogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [bulkBusy, setBulkBusy] = useState('')
  const [selectedBulkTaskIds, setSelectedBulkTaskIds] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [extractionTemplates, setExtractionTemplates] = useState<DocumentExtractionTemplate[]>([])
  const [extractionResult, setExtractionResult] = useState<Record<string, unknown> | null>(null)
  const [workflowStatus, setWorkflowStatus] = useState<DocumentWorkflowStatus | null>(null)
  const [workflowBusy, setWorkflowBusy] = useState('')
  const [wikiImportResult, setWikiImportResult] = useState<DocumentWikiImportResult | null>(null)
  const [mineruCandidates, setMineruCandidates] = useState<DocumentMineruImportCandidate[]>([])

  const refreshTasks = useCallback(async () => {
    const items = await loadDocumentTasks()
    setTasks(items)
    setSelectedBulkTaskIds((prev) => {
      const available = new Set(items.map((item) => item.task_id))
      return prev.filter((taskId) => available.has(taskId))
    })
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
      const [resultData, qualityData, blocksData, layoutData, tablesData, tableRelationsData, figuresData, sourceMapData] = await Promise.all([
        fetchDocumentResult(taskId),
        fetchDocumentQuality(taskId).catch(() => null),
        fetchDocumentBlocks(taskId).catch(() => null),
        fetchDocumentLayoutBlocks(taskId).catch(() => null),
        fetchDocumentTables(taskId).catch(() => null),
        fetchDocumentTableRelations(taskId).catch(() => null),
        fetchDocumentFigures(taskId).catch(() => null),
        fetchDocumentSourceMap(taskId).catch(() => null),
      ])
      setResult(resultData)
      setQuality(qualityData)
      setBlocks(blocksData)
      setLayout(layoutData)
      setTables(tablesData)
      setTableRelations(tableRelationsData)
      setFigures(figuresData)
      setSourceMap(sourceMapData)
      setWorkflowStatus(await fetchDocumentWorkflowStatus(taskId).catch(() => null))
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
      if (status.status === 'completed' || status.status === 'completed_with_warnings') {
        setWikiImportResult(null)
        await loadArtifacts(taskId)
      }
    } else {
      setResult(null)
      setQuality(null)
      setBlocks(null)
      setLayout(null)
      setTables(null)
      setTableRelations(null)
      setFigures(null)
      setSourceMap(null)
      setWorkflowStatus(null)
      setWikiImportResult(null)
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

  const loadMineruCandidates = useCallback(async () => {
    const payload = await fetchMineruImportCandidates(20).catch(() => null)
    setMineruCandidates(payload?.candidates || [])
  }, [])

  const importMineruResult = useCallback(async (sourceDir: string, config: DocumentParseConfig) => {
    const target = sourceDir.trim()
    if (!target) return
    setUploading(true)
    setError(null)
    try {
      const data = await importDocumentFromMineru({
        source_dir: target,
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
      if (data.task?.task_id) await selectTask(data.task.task_id)
      showToast('已有解析产物已导入')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '解析产物导入失败')
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
      setTableRelations(null)
      setFigures(null)
      setSourceMap(null)
      setWorkflowStatus(null)
      setWikiImportResult(null)
    }
    await refreshTasks()
  }, [refreshTasks])

  const setBulkSelection = useCallback((taskId: string, selected: boolean) => {
    setSelectedBulkTaskIds((prev) => {
      if (selected) return prev.includes(taskId) ? prev : [...prev, taskId]
      return prev.filter((item) => item !== taskId)
    })
  }, [])

  const clearBulkSelection = useCallback(() => {
    setSelectedBulkTaskIds([])
  }, [])

  const retrySelectedTasks = useCallback(async () => {
    const selected = [...selectedBulkTaskIds]
    if (!selected.length) return
    setBulkBusy('retry')
    setError(null)
    try {
      for (const taskId of selected) {
        await retryDocumentTask(taskId)
      }
      await refreshTasks()
      showToast(`已重试 ${selected.length} 个任务`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量重试失败')
    } finally {
      setBulkBusy('')
    }
  }, [refreshTasks, selectedBulkTaskIds, showToast])

  const deleteSelectedTasks = useCallback(async () => {
    const selected = [...selectedBulkTaskIds]
    if (!selected.length) return
    setBulkBusy('delete')
    setError(null)
    try {
      for (const taskId of selected) {
        await deleteTask(taskId)
      }
      clearBulkSelection()
      showToast(`已删除 ${selected.length} 个任务`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量删除失败')
    } finally {
      setBulkBusy('')
    }
  }, [clearBulkSelection, deleteTask, selectedBulkTaskIds, showToast])

  const downloadSelectedTasks = useCallback(async () => {
    const selected = [...selectedBulkTaskIds]
    if (!selected.length) return
    setBulkBusy('download')
    setError(null)
    try {
      await downloadAuthenticatedFile(
        documentBatchDownloadUrl(),
        `document-parser-batch-${selected.length}.zip`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_ids: selected }),
        },
      )
      showToast(`已开始下载 ${selected.length} 个任务`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量下载失败')
    } finally {
      setBulkBusy('')
    }
  }, [selectedBulkTaskIds, showToast])

  const loadExtractionTemplates = useCallback(async () => {
    const payload = await fetchDocumentExtractionTemplates().catch(() => null)
    setExtractionTemplates(payload?.templates || [])
  }, [])

  const runExtraction = useCallback(async (schemaText: string, instructions: string, templateId = '') => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return
    setError(null)
    try {
      const schema = schemaText.trim() ? JSON.parse(schemaText) : {}
      const data = await runDocumentExtraction(taskId, {
        mode: templateId ? 'template' : 'schema',
        template_id: templateId,
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

  const refreshWorkflowStatus = useCallback(async () => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return null
    try {
      const status = await fetchDocumentWorkflowStatus(taskId)
      setWorkflowStatus(status)
      return status
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '入库状态加载失败')
      return null
    }
  }, [])

  const importWiki = useCallback(async () => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return
    setWorkflowBusy('wiki')
    setError(null)
    try {
      const data = await importDocumentToWiki(taskId)
      setWikiImportResult(data)
      await refreshWorkflowStatus()
      showToast('文档已归档到通用 Wiki')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '导入 Wiki 失败')
    } finally {
      setWorkflowBusy('')
    }
  }, [refreshWorkflowStatus, showToast])

  const importDatabase = useCallback(async () => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return
    setWorkflowBusy('postgres')
    setError(null)
    try {
      const data = await importDocumentToDatabase(taskId)
      setWikiImportResult(data)
      await refreshWorkflowStatus()
      showToast('文档已导入 PostgreSQL')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '导入 PostgreSQL 失败')
    } finally {
      setWorkflowBusy('')
    }
  }, [refreshWorkflowStatus, showToast])

  const buildSemanticChunks = useCallback(async (milvus = false) => {
    const taskId = selectedTaskIdRef.current
    if (!taskId) return
    setWorkflowBusy(milvus ? 'milvus-ingest' : 'milvus')
    setError(null)
    try {
      const data = await buildDocumentSemanticChunks(taskId, 'default', milvus)
      setWikiImportResult(data)
      await refreshWorkflowStatus()
      showToast(milvus ? '语义 chunks 已写入 Milvus' : '语义 chunks 已生成')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : (milvus ? '写入 Milvus 失败' : '生成语义 chunks 失败'))
    } finally {
      setWorkflowBusy('')
    }
  }, [refreshWorkflowStatus, showToast])

  const reviewTableRelation = useCallback(async (relationId: string, reviewStatus: string, note = '') => {
    const taskId = selectedTaskIdRef.current
    if (!taskId || !relationId) return
    setError(null)
    try {
      await reviewDocumentTableRelation(taskId, relationId, { review_status: reviewStatus, note })
      setTableRelations(await fetchDocumentTableRelations(taskId).catch(() => null))
      showToast('表格关系复核已保存')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '表格关系复核失败')
    }
  }, [showToast])

  useEffect(() => {
    queueMicrotask(() => {
      void refreshTasks()
      void loadExtractionTemplates()
      void loadMineruCandidates()
    })
    return () => stopPolling()
  }, [loadExtractionTemplates, loadMineruCandidates, refreshTasks, stopPolling])

  return {
    tasks,
    selectedTaskId,
    result,
    quality,
    blocks,
    layout,
    tables,
    tableRelations,
    figures,
    sourceMap,
    logs,
    loading,
    uploading,
    bulkBusy,
    selectedBulkTaskIds,
    error,
    extractionTemplates,
    extractionResult,
    workflowStatus,
    workflowBusy,
    wikiImportResult,
    mineruCandidates,
    setError,
    refreshTasks,
    selectTask,
    submitFiles,
    submitUrl,
    importMineruResult,
    loadMineruCandidates,
    retryTask,
    deleteTask,
    setBulkSelection,
    clearBulkSelection,
    retrySelectedTasks,
    deleteSelectedTasks,
    downloadSelectedTasks,
    loadArtifacts,
    loadExtractionTemplates,
    runExtraction,
    refreshWorkflowStatus,
    importWiki,
    importDatabase,
    buildSemanticChunks,
    reviewTableRelation,
  }
}
