import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Ban,
  CheckCircle2,
  DatabaseZap,
  ExternalLink,
  FileSearch,
  FileText,
  FolderOpen,
  History,
  Link2,
  Loader2,
  PackageCheck,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Upload,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/shared/api/client'
import type {
  DealEvidenceIngestDryRun,
  DealEvidenceMilvusIndexReceipt,
  DealEvidenceQualityReport,
  DealEvidenceResponse,
  DealEvidenceSnapshot,
  DealSummary,
  PrimaryMarketMaterial,
  PrimaryMarketMaterialParseStatusResponse,
  PrimaryMarketMaterialResponse,
  PrimaryMarketWikiProjection,
} from '@/lib/dealTypes'
import {
  bindPrimaryMarketDocumentParserTask,
  buildPrimaryMarketEvidence,
  disablePrimaryMarketAnalysisSource,
  dryRunPrimaryMarketEvidenceIngest,
  fetchPrimaryMarketEvidence,
  fetchPrimaryMarketMaterial,
  fetchPrimaryMarketMaterialParseStatus,
  fetchPrimaryMarketMaterials,
  fetchPrimaryMarketProjects,
  fetchPrimaryMarketWiki,
  indexPrimaryMarketEvidenceMilvus,
  parsePrimaryMarketMaterial,
  primaryMarketMaterialArtifactUrl,
  primaryMarketMaterialOriginalUrl,
  primaryMarketMaterialSourcePageUrl,
  reparsePrimaryMarketMaterial,
  reviewPrimaryMarketAnalysisSource,
  supersedePrimaryMarketMaterial,
  uploadPrimaryMarketDocument,
  uploadPrimaryMarketProspectus,
} from '@/features/primary-market/primaryMarketApi'
import {
  DOCUMENT_TYPE_OPTIONS,
  EVIDENCE_DIMENSIONS,
  PROSPECTUS_BOARD_OPTIONS,
  PROSPECTUS_EXCHANGE_OPTIONS,
  PROSPECTUS_FILING_STAGE_OPTIONS,
  boardLabel,
  coverageText,
  createdByText,
  dimensionLabel,
  documentTitle,
  documentTypeLabel,
  exchangeLabel,
  evidenceDocumentMap,
  filingStageLabel,
  formatSize,
  formatTime,
  isMaterialPolling,
  materialCapabilities,
  materialFromResponse,
  materialStatusLabel,
  materialStatusTone,
  materialVersionLabel,
  materialsFromResponse,
  mergeMaterialParseStatus,
  sortedDimensions,
  sortedMissingDimensions,
  statusTone,
  text,
  withMaterialVersions,
} from '@/features/primary-market/primaryMarketViewModel'
import type { ParserBindDraft } from '@/features/primary-market/primaryMarketViewModel'
import {
  materialMilvusStage,
  materialWikiStage,
  projectWikiStage,
} from '@/features/primary-market/primaryMarketMaterialPipeline'

const POLL_INTERVAL_MS = 2500

function updateDealParam(setSearchParams: ReturnType<typeof useSearchParams>[1], dealId: string) {
  const next = new URLSearchParams()
  if (dealId) next.set('dealId', dealId)
  setSearchParams(next, { replace: true })
}

function materialTypeCounts(materials: PrimaryMarketMaterial[]) {
  const counts = new Map<string, number>()
  for (const material of materials) {
    const type = material.document_type || 'uncategorized'
    counts.set(type, (counts.get(type) || 0) + 1)
  }
  return counts
}

function dryRunStatus(dryRun?: DealEvidenceIngestDryRun | null) {
  if (!dryRun) return '未执行'
  if (dryRun.errors?.length) return 'error'
  return dryRun.status || 'preview'
}

function isProspectus(material: PrimaryMarketMaterial) {
  return material.document_type === 'prospectus' || material.document_profile === 'cn_a_share_prospectus'
}

function isPdfMaterial(material: PrimaryMarketMaterial) {
  const filename = material.original_filename || material.filename || ''
  return material.content_type === 'application/pdf' || filename.toLowerCase().endsWith('.pdf')
}

type PipelinePayload = PrimaryMarketMaterialResponse | PrimaryMarketMaterialParseStatusResponse | DealEvidenceResponse
type PipelinePromotion = NonNullable<PrimaryMarketMaterialParseStatusResponse['promotion']>

function parseStageStatus(material: PrimaryMarketMaterial) {
  if (material.parsed_artifact_path && !material.parse_status) return 'succeeded'
  return String(material.parse_status || material.current_parse_run?.status || material.parse_run?.status || 'not_started')
}

function materialParseRetryable(material: PrimaryMarketMaterial) {
  const run = material.current_parse_run || material.parse_run
  return material.parse_retryable !== false && run?.retryable !== false && run?.non_retryable !== true
}

function parseFailureState(error: unknown) {
  const payload = error instanceof ApiError && error.payload && typeof error.payload === 'object'
    ? error.payload as Record<string, unknown>
    : {}
  const detail = payload.detail && typeof payload.detail === 'object'
    ? payload.detail as Record<string, unknown>
    : payload
  const message = String(detail.message || (error instanceof Error ? error.message : '') || '解析启动失败')
  const retryable = typeof detail.retryable === 'boolean'
    ? detail.retryable
    : !(error instanceof ApiError) || error.status >= 500
  return { message, retryable }
}

function mergeMaterialPipelineStatus(
  material: PrimaryMarketMaterial,
  payload: PrimaryMarketMaterialParseStatusResponse | PrimaryMarketMaterialResponse,
) {
  const merged = mergeMaterialParseStatus(
    material,
    payload as PrimaryMarketMaterialParseStatusResponse,
  )
  const parseStage = payload.pipeline?.stages?.parse
  return {
    ...merged,
    parse_status: parseStage?.status || merged.parse_status,
    parse_retryable: parseStage?.retryable ?? merged.parse_retryable,
    parse_error: parseStage?.error || merged.parse_error,
  } satisfies PrimaryMarketMaterial
}

function payloadHasWikiProjection(payload: PrimaryMarketMaterialParseStatusResponse) {
  const directWiki = payload.wiki
  const promotedWiki = payload.promotion?.wiki
  return payload.document?.wiki_status === 'ready'
    || Boolean(directWiki?.wiki_path || promotedWiki?.wiki_path)
}

function pipelineStatusTone(value?: string | null) {
  const status = String(value || '').toLowerCase()
  if (['indexed', 'unchanged', 'succeeded', 'completed', 'ready', 'projected'].includes(status)) return 'success' as const
  if (['failed', 'blocked', 'error'].includes(status)) return 'error' as const
  if (['queued', 'parsing', 'processing', 'building', 'pending', 'stale'].includes(status)) return 'warning' as const
  return 'neutral' as const
}

function shortHash(value?: string | null) {
  const hash = String(value || '')
  return hash ? `${hash.slice(0, 10)}${hash.length > 10 ? '...' : ''}` : '-'
}

function mergeMaterial(current: PrimaryMarketMaterial[], material: PrimaryMarketMaterial) {
  return [material, ...current.filter((item) => item.document_id !== material.document_id)]
    .sort((a, b) => new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime())
}

function materialRunId(material: PrimaryMarketMaterial) {
  return material.current_parse_run_id
    || material.current_parse_run?.parse_run_id
    || material.parse_run?.parse_run_id
    || ''
}

function materialWarnings(material: PrimaryMarketMaterial) {
  const quality = material.quality_report || material.current_parse_run?.quality_report || material.parse_run?.quality_report
  return [
    ...(quality?.blocking_reasons || []),
    ...(quality?.failures || []),
    ...(quality?.warnings || []),
  ].filter(Boolean)
}

export default function PrimaryMarketMaterials() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedDealId = searchParams.get('dealId') || ''
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const requestGenerationRef = useRef(0)
  const [deals, setDeals] = useState<DealSummary[]>([])
  const [dealsLoading, setDealsLoading] = useState(true)
  const [dealsError, setDealsError] = useState('')
  const [materials, setMaterials] = useState<PrimaryMarketMaterial[]>([])
  const [evidenceQuality, setEvidenceQuality] = useState<DealEvidenceQualityReport | null>(null)
  const [evidenceSnapshot, setEvidenceSnapshot] = useState<DealEvidenceSnapshot | null>(null)
  const [milvusIndex, setMilvusIndex] = useState<DealEvidenceMilvusIndexReceipt | null>(null)
  const [wikiProjection, setWikiProjection] = useState<PrimaryMarketWikiProjection | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState('prospectus')
  const [exchange, setExchange] = useState('SSE')
  const [board, setBoard] = useState('main')
  const [filingStage, setFilingStage] = useState('application_draft')
  const [documentDate, setDocumentDate] = useState('')
  const [sourceNote, setSourceNote] = useState('')
  const [supersedesDocumentId, setSupersedesDocumentId] = useState('')
  const [activeType, setActiveType] = useState('all')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [actionId, setActionId] = useState('')
  const [actionError, setActionError] = useState('')
  const [buildingEvidence, setBuildingEvidence] = useState(false)
  const [evidenceError, setEvidenceError] = useState('')
  const [ingestDryRun, setIngestDryRun] = useState<DealEvidenceIngestDryRun | null>(null)
  const [ingestBusy, setIngestBusy] = useState(false)
  const [ingestError, setIngestError] = useState('')
  const [indexingMilvus, setIndexingMilvus] = useState(false)
  const [expandedParserId, setExpandedParserId] = useState('')
  const [bindingId, setBindingId] = useState('')
  const [parserDrafts, setParserDrafts] = useState<Record<string, ParserBindDraft>>({})
  const [parserErrors, setParserErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setDealsLoading(true)
      setDealsError('')
      try {
        const payload = await fetchPrimaryMarketProjects({}, controller.signal)
        const nextDeals = Array.isArray(payload.deals) ? payload.deals : []
        setDeals(nextDeals)
        if (!selectedDealId && nextDeals[0]?.deal_id) {
          updateDealParam(setSearchParams, nextDeals[0].deal_id)
        }
      } catch (err) {
        if (!controller.signal.aborted) setDealsError(err instanceof Error ? err.message : '项目列表加载失败')
      } finally {
        if (!controller.signal.aborted) setDealsLoading(false)
      }
    })()
    return () => controller.abort()
  }, [selectedDealId, setSearchParams])

  const selectedDeal = deals.find((deal) => deal.deal_id === selectedDealId) || null

  const applyPipelinePayload = useCallback((payload: PipelinePayload) => {
    const promotion = (
      'promotion' in payload
      && payload.promotion
      && typeof payload.promotion === 'object'
    ) ? payload.promotion as PipelinePromotion : null
    const evidence = ('evidence' in payload ? payload.evidence : null) || promotion?.evidence
    const nextQuality = evidence?.quality_report
      || ('quality_report' in payload ? payload.quality_report : null)
    if (nextQuality) setEvidenceQuality(nextQuality)

    const nextSnapshot = ('evidence_snapshot' in payload ? payload.evidence_snapshot : null)
      || promotion?.evidence_snapshot
      || evidence?.evidence_snapshot
    if (nextSnapshot) setEvidenceSnapshot(nextSnapshot)

    const nextMilvus = ('milvus_index' in payload ? payload.milvus_index : null)
      || promotion?.milvus_index
      || evidence?.milvus_index
    if (nextMilvus) setMilvusIndex(nextMilvus)

    const nextWiki = ('wiki_projection' in payload ? payload.wiki_projection : null)
      || ('wiki' in payload ? payload.wiki : null)
      || promotion?.wiki
      || evidence?.wiki_projection
      || evidence?.wiki
    if (nextWiki) {
      setWikiProjection((current) => ({
        ...current,
        ...nextWiki,
        counts: nextWiki.counts || current?.counts,
        entries: nextWiki.entries || current?.entries,
      }))
    }
  }, [])

  const loadMaterials = useCallback(async (signal?: AbortSignal) => {
    if (!selectedDealId) return
    const generation = ++requestGenerationRef.current
    setLoading(true)
    setError('')
    setEvidenceError('')
    try {
      const [materialsPayload, evidencePayload, wikiPayload] = await Promise.all([
        fetchPrimaryMarketMaterials(selectedDealId, signal),
        fetchPrimaryMarketEvidence(selectedDealId, signal).catch(() => null),
        fetchPrimaryMarketWiki(selectedDealId, signal).catch(() => null),
      ])
      if (signal?.aborted || requestGenerationRef.current !== generation) return
      const listedMaterials = materialsFromResponse(materialsPayload)
      const hydratedResults = await Promise.all(listedMaterials.map(async (material) => {
        const [detailPayload, statusPayload] = await Promise.all([
          isProspectus(material)
            ? fetchPrimaryMarketMaterial(selectedDealId, material.document_id, signal).catch(() => null)
            : Promise.resolve(null),
          fetchPrimaryMarketMaterialParseStatus(selectedDealId, material.document_id, signal).catch(() => null),
        ])
        const detailed = detailPayload ? materialFromResponse(detailPayload) : null
        const hydrated = detailed ? { ...material, ...detailed } : material
        return {
          material: statusPayload ? mergeMaterialPipelineStatus(hydrated, statusPayload) : hydrated,
          statusPayload,
        }
      }))
      if (signal?.aborted || requestGenerationRef.current !== generation) return
      const refreshedWikiPayload = hydratedResults.some((result) => (
        result.statusPayload && payloadHasWikiProjection(result.statusPayload)
      ))
        ? await fetchPrimaryMarketWiki(selectedDealId, signal).catch(() => wikiPayload)
        : wikiPayload
      if (signal?.aborted || requestGenerationRef.current !== generation) return
      setMaterials(hydratedResults.map((result) => result.material))
      setEvidenceQuality(evidencePayload?.quality_report || null)
      if (evidencePayload?.evidence_snapshot) setEvidenceSnapshot(evidencePayload.evidence_snapshot)
      if (evidencePayload?.milvus_index) setMilvusIndex(evidencePayload.milvus_index)
      if (refreshedWikiPayload) setWikiProjection(refreshedWikiPayload)
      for (const result of hydratedResults) {
        if (result.statusPayload) applyPipelinePayload(result.statusPayload)
      }
    } catch (err) {
      if (!signal?.aborted && requestGenerationRef.current === generation) {
        setError(err instanceof Error ? err.message : '材料加载失败')
      }
    } finally {
      if (!signal?.aborted && requestGenerationRef.current === generation) setLoading(false)
    }
  }, [applyPipelinePayload, selectedDealId])

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => void loadMaterials(controller.signal), 0)
    return () => {
      window.clearTimeout(timer)
      requestGenerationRef.current += 1
      controller.abort()
    }
  }, [loadMaterials])

  const handleDealChange = (dealId: string) => {
    requestGenerationRef.current += 1
    setMaterials([])
    setEvidenceQuality(null)
    setEvidenceSnapshot(null)
    setMilvusIndex(null)
    setWikiProjection(null)
    setIngestDryRun(null)
    setIngestError('')
    setActionError('')
    setExpandedParserId('')
    setParserDrafts({})
    setParserErrors({})
    updateDealParam(setSearchParams, dealId)
  }

  useEffect(() => {
    const pending = materials.filter(isMaterialPolling)
    if (!selectedDealId || pending.length === 0) return
    const dealId = selectedDealId
    const generation = requestGenerationRef.current
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void Promise.all(pending.map(async (material) => {
        try {
          const payload = await fetchPrimaryMarketMaterialParseStatus(dealId, material.document_id, controller.signal)
          return { documentId: material.document_id, payload }
        } catch {
          return null
        }
      })).then((results) => {
        if (controller.signal.aborted || requestGenerationRef.current !== generation) return
        for (const result of results) {
          if (result?.payload) applyPipelinePayload(result.payload)
        }
        if (results.some((result) => result?.payload && payloadHasWikiProjection(result.payload))) {
          void fetchPrimaryMarketWiki(dealId, controller.signal).then((payload) => {
            if (!controller.signal.aborted && requestGenerationRef.current === generation) {
              setWikiProjection(payload)
            }
          }).catch(() => undefined)
        }
        setMaterials((current) => current.map((material) => {
          const result = results.find((item) => item?.documentId === material.document_id)
          return result ? mergeMaterialPipelineStatus(material, result.payload) : material
        }))
      })
    }, POLL_INTERVAL_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [applyPipelinePayload, materials, selectedDealId])

  const displayMaterials = useMemo(() => withMaterialVersions(materials), [materials])
  const typeCounts = useMemo(() => materialTypeCounts(displayMaterials), [displayMaterials])
  const filteredMaterials = activeType === 'all'
    ? displayMaterials
    : displayMaterials.filter((material) => (material.document_type || 'uncategorized') === activeType)
  const prospectuses = displayMaterials.filter(isProspectus)
  const availableDimensions = sortedDimensions(evidenceQuality)
  const missingDimensions = sortedMissingDimensions(evidenceQuality)
  const evidenceByDocument = useMemo(() => evidenceDocumentMap(evidenceQuality), [evidenceQuality])
  const wikiEntryByDocument = useMemo(() => new Map(
    (wikiProjection?.entries || [])
      .filter((entry) => typeof entry.document_id === 'string')
      .map((entry) => [String(entry.document_id), entry]),
  ), [wikiProjection])
  const parsedCount = displayMaterials.filter((material) => ['succeeded', 'completed'].includes(parseStageStatus(material))).length
  const parsingCount = displayMaterials.filter(isMaterialPolling).length
  const parseFailedCount = displayMaterials.filter((material) => parseStageStatus(material) === 'failed').length
  const wikiPipelineStatus = projectWikiStage(wikiProjection, parsedCount)
  const parsePipelineStatus = parseFailedCount
    ? 'failed'
    : parsingCount
      ? 'processing'
      : displayMaterials.length > 0 && parsedCount === displayMaterials.length
        ? 'completed'
        : 'pending'
  const evidencePipelineStatus = evidenceQuality?.status || 'pending'
  const currentEvidenceSnapshotHash = evidenceSnapshot?.snapshot_hash || wikiProjection?.evidence_snapshot_hash
  const rawMilvusPipelineStatus = milvusIndex?.status || 'pending'
  const milvusPipelineStatus = ['indexed', 'unchanged'].includes(rawMilvusPipelineStatus)
    && (!currentEvidenceSnapshotHash || milvusIndex?.snapshot_hash !== currentEvidenceSnapshotHash)
    ? 'stale'
    : rawMilvusPipelineStatus
  const boardOptions = exchange === 'SSE'
    ? PROSPECTUS_BOARD_OPTIONS.filter((option) => ['main', 'star'].includes(option.value))
    : exchange === 'SZSE'
      ? PROSPECTUS_BOARD_OPTIONS.filter((option) => ['main', 'chinext'].includes(option.value))
      : PROSPECTUS_BOARD_OPTIONS.filter((option) => option.value === 'beijing')

  const resetUploadForm = () => {
    setSelectedFile(null)
    setSourceNote('')
    setDocumentDate('')
    setSupersedesDocumentId('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleExchangeChange = (value: string) => {
    setExchange(value)
    setBoard(value === 'SSE' ? 'main' : value === 'SZSE' ? 'main' : 'beijing')
  }

  const handleUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedDealId || !selectedFile) {
      setUploadError(!selectedDealId ? '请选择项目' : '请选择要上传的文件')
      return
    }
    if (documentType === 'prospectus' && !selectedFile.name.toLowerCase().endsWith('.pdf')) {
      setUploadError('招股书仅支持 PDF 文件')
      return
    }
    setUploading(true)
    setUploadError('')
    setActionError('')
    let shouldReload = true
    try {
      if (documentType === 'prospectus') {
        const payload = await uploadPrimaryMarketProspectus(selectedDealId, {
          file: selectedFile,
          exchange,
          board,
          filingStage,
          documentDate,
          sourceNote,
          supersedesDocumentId,
        })
        applyPipelinePayload(payload)
        const material = materialFromResponse(payload)
        if (material) setMaterials((current) => mergeMaterial(current, material))
      } else {
        const payload = await uploadPrimaryMarketDocument(selectedDealId, {
          file: selectedFile,
          documentType,
          sourceNote,
        })
        const uploaded = payload.document as PrimaryMarketMaterial
        setMaterials((current) => mergeMaterial(current, uploaded))
        try {
          const parsePayload = await parsePrimaryMarketMaterial(selectedDealId, uploaded.document_id)
          applyPipelinePayload(parsePayload)
          const parsingMaterial = materialFromResponse(parsePayload)
          if (parsingMaterial) {
            setMaterials((current) => mergeMaterial(
              current,
              mergeMaterialPipelineStatus(parsingMaterial, parsePayload),
            ))
          }
        } catch (err) {
          shouldReload = false
          const failure = parseFailureState(err)
          setMaterials((current) => current.map((material) => (
            material.document_id === uploaded.document_id
              ? {
                  ...material,
                  parse_status: 'failed',
                  parse_retryable: failure.retryable,
                  parse_error: failure.message,
                }
              : material
          )))
          setActionError(`材料已上传，但解析启动失败：${failure.message}`)
        }
      }
      setEvidenceQuality(null)
      setEvidenceSnapshot(null)
      setMilvusIndex(null)
      setIngestDryRun(null)
      resetUploadForm()
      if (shouldReload) void loadMaterials()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : '文件上传失败')
    } finally {
      setUploading(false)
    }
  }

  const runMaterialAction = async (
    material: PrimaryMarketMaterial,
    action: () => Promise<unknown>,
    markParseFailure = false,
  ) => {
    setActionId(material.document_id)
    setActionError('')
    try {
      const payload = await action() as Parameters<typeof materialFromResponse>[0]
      applyPipelinePayload(payload)
      const response = payload as PrimaryMarketMaterialResponse
      const materialResponse = materialFromResponse(response)
      const updated = materialResponse ? mergeMaterialPipelineStatus(materialResponse, response) : null
      if (updated) setMaterials((current) => mergeMaterial(current, updated))
      setEvidenceQuality(null)
      setIngestDryRun(null)
      void loadMaterials()
    } catch (err) {
      if (markParseFailure) {
        const failure = parseFailureState(err)
        setMaterials((current) => current.map((item) => (
          item.document_id === material.document_id
            ? {
                ...item,
                parse_status: 'failed',
                parse_retryable: failure.retryable,
                parse_error: failure.message,
              }
            : item
        )))
      }
      setActionError(err instanceof Error ? err.message : '材料操作失败')
    } finally {
      setActionId('')
    }
  }

  const handleReparse = (material: PrimaryMarketMaterial) => {
    if (!selectedDealId || !window.confirm(`确认重新解析：${documentTitle(material)}？`)) return
    void runMaterialAction(material, () => reparsePrimaryMarketMaterial(selectedDealId, material.document_id, {
      reason: material.parse_status === 'failed' ? 'quality_retry' : 'manual',
      parseMethod: 'auto',
      formulaEnable: true,
      tableEnable: true,
    }))
  }

  const handleParseOrdinaryMaterial = (material: PrimaryMarketMaterial) => {
    if (!selectedDealId) return
    void runMaterialAction(
      material,
      () => parsePrimaryMarketMaterial(selectedDealId, material.document_id),
      true,
    )
  }

  const toggleParserEditor = (material: PrimaryMarketMaterial) => {
    setExpandedParserId((current) => {
      const next = current === material.document_id ? '' : material.document_id
      if (next) {
        setParserDrafts((drafts) => ({
          ...drafts,
          [material.document_id]: drafts[material.document_id] || {
            taskId: material.parse_task_id || '',
            artifactPath: material.parsed_artifact_path || '',
            note: '',
          },
        }))
        setParserErrors((errors) => ({ ...errors, [material.document_id]: '' }))
      }
      return next
    })
  }

  const updateParserDraft = (material: PrimaryMarketMaterial, patch: Partial<ParserBindDraft>) => {
    setParserDrafts((current) => {
      const previous = current[material.document_id]
      return {
        ...current,
        [material.document_id]: {
          taskId: patch.taskId ?? previous?.taskId ?? material.parse_task_id ?? '',
          artifactPath: patch.artifactPath ?? previous?.artifactPath ?? material.parsed_artifact_path ?? '',
          note: patch.note ?? previous?.note ?? '',
        },
      }
    })
  }

  const handleBindParserTask = async (event: FormEvent<HTMLFormElement>, material: PrimaryMarketMaterial) => {
    event.preventDefault()
    if (!selectedDealId) return
    const draft = parserDrafts[material.document_id]
    if (!draft?.taskId.trim()) {
      setParserErrors((current) => ({ ...current, [material.document_id]: '请输入 parser task_id' }))
      return
    }
    setBindingId(material.document_id)
    setParserErrors((current) => ({ ...current, [material.document_id]: '' }))
    try {
      const payload = await bindPrimaryMarketDocumentParserTask(selectedDealId, material.document_id, {
        taskId: draft.taskId,
        artifactPath: draft.artifactPath,
        note: draft.note,
      })
      setMaterials((current) => mergeMaterial(current, payload.document as PrimaryMarketMaterial))
      setEvidenceQuality(null)
      setEvidenceSnapshot(null)
      setMilvusIndex(null)
      setExpandedParserId('')
      void loadMaterials()
    } catch (err) {
      setParserErrors((current) => ({
        ...current,
        [material.document_id]: err instanceof Error ? err.message : '解析任务绑定失败',
      }))
    } finally {
      setBindingId('')
    }
  }

  const handleReview = (material: PrimaryMarketMaterial, decision: 'activate' | 'block') => {
    if (!selectedDealId) return
    const verb = decision === 'activate' ? '审核并启用' : '阻止该分析源启用'
    if (!window.confirm(`确认${verb}：${documentTitle(material)}？`)) return
    void runMaterialAction(material, () => reviewPrimaryMarketAnalysisSource(selectedDealId, material.document_id, {
      decision,
      capabilityOverrides: {},
      note: decision === 'activate' ? '材料中心人工复核通过' : '材料中心人工复核阻断',
    }))
  }

  const handleDisable = (material: PrimaryMarketMaterial) => {
    if (!selectedDealId || !window.confirm(`确认停用分析源：${documentTitle(material)}？历史引用将保留。`)) return
    void runMaterialAction(material, () => disablePrimaryMarketAnalysisSource(
      selectedDealId,
      material.document_id,
      '材料中心人工停用',
    ))
  }

  const handleSupersede = (material: PrimaryMarketMaterial) => {
    if (!selectedDealId) return
    const replacement = prospectuses.find((candidate) => (
      candidate.document_id !== material.document_id
      && candidate.document_status === 'active'
      && ['ready', 'ready_with_restrictions'].includes(candidate.analysis_source_status || '')
      && new Date(candidate.created_at || '').getTime() > new Date(material.created_at || '').getTime()
    ))
    if (!replacement) {
      setActionError('没有可用于分析的新版招股书。请先上传新版，并等待解析和质量门禁完成。')
      return
    }
    if (!window.confirm(`确认由 ${documentTitle(replacement)} 替代 ${documentTitle(material)}？`)) return
    void runMaterialAction(material, () => supersedePrimaryMarketMaterial(
      selectedDealId,
      material.document_id,
      replacement.document_id,
    ))
  }

  const handleBuildEvidence = async () => {
    if (!selectedDealId) return
    setBuildingEvidence(true)
    setEvidenceError('')
    setIngestDryRun(null)
    try {
      const payload = await buildPrimaryMarketEvidence(selectedDealId)
      setEvidenceQuality(payload.quality_report || null)
      applyPipelinePayload(payload)
      const wiki = await fetchPrimaryMarketWiki(selectedDealId).catch(() => null)
      if (wiki) setWikiProjection(wiki)
    } catch (err) {
      setEvidenceError(err instanceof Error ? err.message : '证据包构建失败')
    } finally {
      setBuildingEvidence(false)
    }
  }

  const handleRetryMilvusIndex = async () => {
    if (!selectedDealId) return
    setIndexingMilvus(true)
    setEvidenceError('')
    try {
      const payload = await indexPrimaryMarketEvidenceMilvus(selectedDealId)
      setMilvusIndex(payload.milvus_index)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Milvus 索引失败'
      setMilvusIndex((current) => ({
        ...current,
        status: 'failed',
        deal_id: selectedDealId,
        snapshot_hash: current?.snapshot_hash || evidenceSnapshot?.snapshot_hash,
        error: message,
      }))
      setEvidenceError(message)
    } finally {
      setIndexingMilvus(false)
    }
  }

  const handleIngestDryRun = async () => {
    if (!selectedDealId) return
    setIngestBusy(true)
    setIngestError('')
    try {
      const payload = await dryRunPrimaryMarketEvidenceIngest(selectedDealId)
      setIngestDryRun(payload.ingest_dry_run)
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : 'Evidence 入库 dry-run 失败')
    } finally {
      setIngestBusy(false)
    }
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FolderOpen}
        eyebrow="Primary Market Materials"
        title="一级市场材料中心"
        description="项目材料、解析质量与分析源状态"
        actions={
          <Button type="button" variant="secondary" onClick={() => void loadMaterials()} disabled={loading || !selectedDealId}>
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </Button>
        }
      />

      <PageSection title="项目概览" compact>
        <div className="primary-market-project-context-grid grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(220px,0.35fr)_minmax(220px,0.35fr)]">
          <label className="min-w-0 space-y-1.5">
            <span className="text-xs font-semibold text-text-muted">项目</span>
            <select
              value={selectedDealId}
              onChange={(event) => handleDealChange(event.target.value)}
              disabled={dealsLoading || !deals.length}
              className="h-10 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              aria-label="选择一级市场项目"
            >
              <option value="">选择项目</option>
              {deals.map((deal) => (
                <option key={deal.deal_id} value={deal.deal_id}>{deal.company_name || deal.deal_id}</option>
              ))}
            </select>
            {dealsError ? <p className="text-xs text-destructive">{dealsError}</p> : null}
          </label>
          <Surface kind="muted" padding="sm">
            <p className="text-xs text-text-muted">Evidence 覆盖</p>
            <p className="mt-1 text-xl font-semibold text-text">{coverageText(evidenceQuality)}</p>
            <StatusBadge className="mt-2" tone={statusTone(evidenceQuality?.status)}>{text(evidenceQuality?.status, '未构建')}</StatusBadge>
          </Surface>
          <Surface kind="muted" padding="sm">
            <p className="text-xs text-text-muted">材料 / 招股书</p>
            <p className="mt-1 text-xl font-semibold text-text">{materials.length} / {prospectuses.length}</p>
            <p className="mt-2 truncate text-xs text-text-muted">{selectedDeal?.industry || '行业未设置'} · {selectedDeal?.stage || '阶段未设置'}</p>
          </Surface>
        </div>
      </PageSection>

      {selectedDealId ? (
        <PageSection title="Wiki-first 研究链路" compact>
          <div className="primary-market-pipeline-grid grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="min-w-0 rounded-md border border-border/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-text">1. 解析</p>
                <StatusBadge tone={pipelineStatusTone(parsePipelineStatus)}>{parsePipelineStatus}</StatusBadge>
              </div>
              <p className="mt-2 text-xs leading-5 text-text-muted">完成 {parsedCount}/{displayMaterials.length} · 处理中 {parsingCount} · 失败 {parseFailedCount}</p>
            </div>
            <div className="min-w-0 rounded-md border border-border/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-text">2. 项目 Wiki</p>
                <StatusBadge tone={pipelineStatusTone(wikiPipelineStatus)}>{wikiPipelineStatus}</StatusBadge>
              </div>
              <p className="mt-2 truncate text-xs leading-5 text-text-muted">投影 {wikiProjection?.counts?.company_wiki_projections ?? 0}/{parsedCount} · wiki/wiki_tree.json</p>
            </div>
            <div className="min-w-0 rounded-md border border-border/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-text">3. Evidence</p>
                <StatusBadge tone={pipelineStatusTone(evidencePipelineStatus)}>{evidencePipelineStatus}</StatusBadge>
              </div>
              <p className="mt-2 text-xs leading-5 text-text-muted">{evidenceQuality?.item_count ?? evidenceQuality?.counts?.items ?? 0} items · snapshot {shortHash(evidenceSnapshot?.snapshot_hash || wikiProjection?.evidence_snapshot_hash)}</p>
            </div>
            <div className="min-w-0 rounded-md border border-border/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-text">4. Milvus</p>
                <StatusBadge tone={pipelineStatusTone(milvusPipelineStatus)}>{milvusPipelineStatus}</StatusBadge>
              </div>
              <p className="mt-2 break-all text-xs leading-5 text-text-muted">
                {milvusIndex?.physical_collection || 'ic_collaboration_shared'} · items {milvusIndex?.counts?.items ?? 0}
              </p>
              <p className="mt-1 break-all text-xs leading-5 text-text-muted">
                snapshot {shortHash(milvusIndex?.snapshot_hash)} · inserted {milvusIndex?.counts?.inserted ?? 0} · existing {milvusIndex?.counts?.existing ?? 0}
              </p>
              {['failed', 'stale'].includes(milvusPipelineStatus) ? (
                <Button className="mt-2" type="button" variant="secondary" size="sm" onClick={() => void handleRetryMilvusIndex()} disabled={indexingMilvus}>
                  {indexingMilvus ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                  {milvusPipelineStatus === 'stale' ? '重新索引' : '重试索引'}
                </Button>
              ) : null}
            </div>
          </div>
          {milvusIndex?.error ? <p className="mt-3 break-words text-xs text-destructive">{milvusIndex.error}</p> : null}
        </PageSection>
      ) : null}

      {!selectedDealId ? (
        <PageSection>
          <EmptyState icon={FolderOpen} title="请选择项目" description="选择项目后显示材料与分析源状态。" />
        </PageSection>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[200px_minmax(0,1fr)_280px]">
          <PageSection title="材料类型" compact contentClassName="primary-market-material-types space-y-2">
            <button
              type="button"
              onClick={() => setActiveType('all')}
              className={`flex min-h-10 w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-semibold transition-colors ${activeType === 'all' ? 'bg-primary/10 text-primary' : 'text-text hover:bg-muted/60'}`}
            >
              <span>全部材料</span>
              <StatusBadge tone="neutral">{materials.length}</StatusBadge>
            </button>
            {DOCUMENT_TYPE_OPTIONS.map((option) => {
              const count = typeCounts.get(option.value) || 0
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setActiveType(option.value)}
                  className={`flex min-h-10 w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-semibold transition-colors ${activeType === option.value ? 'bg-primary/10 text-primary' : 'text-text hover:bg-muted/60'}`}
                >
                  <span>{option.label}</span>
                  <StatusBadge tone={count ? 'info' : 'neutral'}>{count}</StatusBadge>
                </button>
              )
            })}
          </PageSection>

          <div className="min-w-0 space-y-5">
            <PageSection title={documentType === 'prospectus' ? '上传招股书' : '上传材料'}>
              <form onSubmit={handleUpload} className="space-y-4">
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_190px]">
                  <label className="min-w-0 space-y-1.5">
                    <span className="text-xs font-semibold text-text-muted">文件</span>
                    <Input
                      ref={fileInputRef}
                      type="file"
                      accept={documentType === 'prospectus' ? 'application/pdf,.pdf' : undefined}
                      disabled={uploading}
                      onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                    />
                  </label>
                  <label className="min-w-0 space-y-1.5">
                    <span className="text-xs font-semibold text-text-muted">材料类型</span>
                    <select
                      value={documentType}
                      onChange={(event) => setDocumentType(event.target.value)}
                      disabled={uploading}
                      className="h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                    >
                      {DOCUMENT_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                </div>

                {documentType === 'prospectus' ? (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <label className="min-w-0 space-y-1.5">
                      <span className="text-xs font-semibold text-text-muted">交易所</span>
                      <select value={exchange} onChange={(event) => handleExchangeChange(event.target.value)} disabled={uploading} className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm">
                        {PROSPECTUS_EXCHANGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="min-w-0 space-y-1.5">
                      <span className="text-xs font-semibold text-text-muted">板块</span>
                      <select value={board} onChange={(event) => setBoard(event.target.value)} disabled={uploading} className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm">
                        {boardOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="min-w-0 space-y-1.5">
                      <span className="text-xs font-semibold text-text-muted">文件阶段</span>
                      <select value={filingStage} onChange={(event) => setFilingStage(event.target.value)} disabled={uploading} className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm">
                        {PROSPECTUS_FILING_STAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <label className="min-w-0 space-y-1.5">
                      <span className="text-xs font-semibold text-text-muted">文件日期</span>
                      <Input type="date" value={documentDate} onChange={(event) => setDocumentDate(event.target.value)} disabled={uploading} />
                    </label>
                    <label className="min-w-0 space-y-1.5 sm:col-span-2">
                      <span className="text-xs font-semibold text-text-muted">替代已有版本</span>
                      <select value={supersedesDocumentId} onChange={(event) => setSupersedesDocumentId(event.target.value)} disabled={uploading} className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm">
                        <option value="">不替代</option>
                        {prospectuses.filter((item) => item.document_status !== 'superseded').map((item) => (
                          <option key={item.document_id} value={item.document_id}>{materialVersionLabel(item)} · {documentTitle(item)}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : null}

                <label className="block min-w-0 space-y-1.5">
                  <span className="text-xs font-semibold text-text-muted">来源说明</span>
                  <Textarea value={sourceNote} onChange={(event) => setSourceNote(event.target.value)} disabled={uploading} rows={2} />
                </label>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="min-w-0 break-all text-sm text-text-muted">
                    {selectedFile ? `${selectedFile.name} · ${formatSize(selectedFile.size)}` : documentType === 'prospectus' ? 'PDF' : '未选择文件'}
                  </p>
                  <Button type="submit" disabled={uploading || !selectedFile} className="min-w-28">
                    {uploading ? <Loader2 className="animate-spin" /> : <Upload />}
                    上传并解析
                  </Button>
                </div>
                {uploadError ? <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{uploadError}</div> : null}
              </form>
            </PageSection>

            <PageSection
              title="材料清单"
              description={`${filteredMaterials.length}/${materials.length}`}
              actions={
                <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={() => void handleBuildEvidence()} disabled={loading || buildingEvidence || uploading || Boolean(actionId)}>
                    {buildingEvidence ? <Loader2 className="animate-spin" /> : <PackageCheck />}
                    构建 Evidence
                  </Button>
                  <Button type="button" variant="secondary" onClick={() => void handleIngestDryRun()} disabled={loading || ingestBusy || buildingEvidence}>
                    {ingestBusy ? <Loader2 className="animate-spin" /> : <DatabaseZap />}
                    入库 Dry-run
                  </Button>
                </div>
              }
            >
              {evidenceError ? <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{evidenceError}</div> : null}
              {error || actionError ? <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error || actionError}</div> : null}
              {loading ? (
                <div className="grid gap-3">{Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-24 animate-pulse rounded-md bg-muted/60" />)}</div>
              ) : filteredMaterials.length === 0 ? (
                <EmptyState icon={FileText} title="暂无材料" description="当前筛选没有材料。" />
              ) : (
                <div className="divide-y divide-border/70">
                  {filteredMaterials.map((material) => {
                    const prospectus = isProspectus(material)
                    const pdfMaterial = isPdfMaterial(material)
                    const busy = actionId === material.document_id
                    const parserBinding = bindingId === material.document_id
                    const parserExpanded = expandedParserId === material.document_id
                    const parserDraft = parserDrafts[material.document_id] || {
                      taskId: material.parse_task_id || '',
                      artifactPath: material.parsed_artifact_path || '',
                      note: '',
                    }
                    const runId = materialRunId(material)
                    const warnings = materialWarnings(material)
                    const capabilities = prospectus ? materialCapabilities(material) : []
                    const parseStatus = parseStageStatus(material)
                    const wikiEntry = wikiEntryByDocument.get(material.document_id)
                    const wikiStatus = materialWikiStage(material, wikiEntry)
                    const evidenceRow = evidenceByDocument.get(material.document_id)
                    const evidenceStatus = evidenceRow?.status || (evidenceRow?.items ? 'ready' : 'pending')
                    const materialMilvusStatus = materialMilvusStage(
                      evidenceRow,
                      milvusIndex,
                      currentEvidenceSnapshotHash,
                    )
                    const sourceReady = ['ready', 'ready_with_restrictions'].includes(material.analysis_source_status || '')
                    const sourceReviewable = ['pending', 'review_required', 'blocked'].includes(material.analysis_source_status || '')
                    const originalUrl = String(
                      material.raw_file_url
                      || material.original_url
                      || (prospectus ? primaryMarketMaterialOriginalUrl(selectedDealId, material.document_id) : ''),
                    )
                    return (
                      <article key={material.document_id} className="grid min-w-0 gap-4 py-5 first:pt-0 last:pb-0 lg:grid-cols-[minmax(0,1.15fr)_minmax(250px,0.85fr)]">
                        <div className="min-w-0 space-y-3">
                          <div className="flex min-w-0 items-start gap-3">
                            <span className="premium-icon mt-0.5 h-8 w-8 shrink-0 rounded-md"><FileText className="h-4 w-4" /></span>
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="min-w-0 break-all text-sm font-semibold text-text">{documentTitle(material)}</h3>
                                <StatusBadge tone={materialStatusTone(material)}>{materialStatusLabel(material)}</StatusBadge>
                                {prospectus ? <StatusBadge tone="info">{materialVersionLabel(material)}</StatusBadge> : null}
                                {material.is_active_source ? <StatusBadge tone="success">Active source</StatusBadge> : null}
                              </div>
                              <p className="mt-1 break-all font-mono text-xs text-text-muted">{material.document_id}{runId ? ` · ${runId}` : ''}</p>
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
                            <span>{documentTypeLabel(material.document_type)}</span>
                            {prospectus ? <span>{exchangeLabel(material.exchange)} · {boardLabel(material.board)}</span> : null}
                            {prospectus ? <span>{filingStageLabel(material.filing_stage)}{material.document_date ? ` · ${material.document_date}` : ''}</span> : null}
                            <span>{formatSize(material.size_bytes)}</span>
                            <span>{formatTime(material.created_at)}{createdByText(material) ? ` · ${createdByText(material)}` : ''}</span>
                          </div>
                          {prospectus && (material.supersedes_document_id || material.superseded_by_document_id) ? (
                            <p className="break-all text-xs text-text-muted">
                              {material.supersedes_document_id ? `替代 ${material.supersedes_document_id}` : ''}
                              {material.supersedes_document_id && material.superseded_by_document_id ? ' · ' : ''}
                              {material.superseded_by_document_id ? `由 ${material.superseded_by_document_id} 替代` : ''}
                            </p>
                          ) : null}
                          {material.source_note ? <p className="break-words text-xs leading-5 text-text-muted">{material.source_note}</p> : null}
                          {material.parse_error || material.current_parse_run?.failure_message || material.parse_run?.failure_message ? (
                            <p className="break-words text-xs leading-5 text-destructive">{material.parse_error || material.current_parse_run?.failure_message || material.parse_run?.failure_message}</p>
                          ) : null}
                          {warnings.length ? (
                            <div className="rounded-md border border-warning/30 bg-warning/5 p-3 text-xs text-warning">
                              {warnings.slice(0, 3).join(' / ')}
                            </div>
                          ) : null}
                          {(material.stale_receipt_count || material.stale_report_count) ? (
                            <p className="flex items-center gap-1.5 text-xs text-warning"><History className="h-3.5 w-3.5" />已有 {material.stale_receipt_count || 0} 个 receipt、{material.stale_report_count || 0} 份报告待更新</p>
                          ) : null}
                        </div>

                        <div className="min-w-0 space-y-3">
                          <div className="grid grid-cols-2 gap-2">
                            {[
                              ['解析', parseStatus],
                              ['项目 Wiki', wikiStatus],
                              ['Evidence', evidenceStatus],
                              ['Milvus', materialMilvusStatus],
                            ].map(([label, value]) => (
                              <div key={label} className="min-w-0 rounded-md bg-muted/45 px-2.5 py-2">
                                <p className="truncate text-xs font-semibold text-text">{label}</p>
                                <StatusBadge className="mt-1 max-w-full" tone={pipelineStatusTone(value)}>{value}</StatusBadge>
                              </div>
                            ))}
                          </div>
                          {prospectus ? (
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-2 2xl:grid-cols-4">
                              {capabilities.map((capability) => (
                                <div key={capability.id} className="min-w-0 rounded-md bg-muted/45 px-2.5 py-2">
                                  <p className="truncate text-xs font-semibold text-text">{capability.label}</p>
                                  <StatusBadge className="mt-1 max-w-full" tone={capability.tone}>{text(capability.status, 'pending')}</StatusBadge>
                                </div>
                              ))}
                            </div>
                          ) : null}
                          <div className="flex flex-wrap gap-2">
                            {originalUrl ? <Button asChild variant="secondary" size="sm"><a href={originalUrl} target="_blank" rel="noreferrer"><ExternalLink />原件</a></Button> : null}
                            {prospectus && material.parse_status === 'succeeded' ? (
                              <>
                                <Button asChild variant="secondary" size="sm"><a href={primaryMarketMaterialArtifactUrl(selectedDealId, material.document_id, 'result.md')} target="_blank" rel="noreferrer"><FileSearch />解析结果</a></Button>
                                <Button asChild variant="secondary" size="sm"><a href={primaryMarketMaterialArtifactUrl(selectedDealId, material.document_id, 'quality_report.json')} target="_blank" rel="noreferrer"><ShieldAlert />质量报告</a></Button>
                                <Button asChild variant="secondary" size="sm"><a href={primaryMarketMaterialSourcePageUrl(selectedDealId, material.document_id, 1)} target="_blank" rel="noreferrer"><ExternalLink />原文页</a></Button>
                              </>
                            ) : null}
                            {prospectus && material.document_status !== 'superseded' ? (
                              <Button type="button" variant="secondary" size="sm" onClick={() => handleReparse(material)} disabled={Boolean(actionId)}>
                                {busy ? <Loader2 className="animate-spin" /> : <RotateCcw />}重新解析
                              </Button>
                            ) : null}
                            {!prospectus && !isMaterialPolling(material) && !['succeeded', 'completed'].includes(parseStatus) ? (
                              <Button type="button" variant="secondary" size="sm" onClick={() => handleParseOrdinaryMaterial(material)} disabled={Boolean(actionId) || !materialParseRetryable(material)}>
                                {busy ? <Loader2 className="animate-spin" /> : <RotateCcw />}
                                {!materialParseRetryable(material) ? '需绑定 Parser' : parseStatus === 'failed' ? '重试解析' : '开始解析'}
                              </Button>
                            ) : null}
                            {!prospectus && material.parser_result_url ? (
                              <Button asChild variant="secondary" size="sm"><a href={material.parser_result_url} target="_blank" rel="noreferrer"><FileSearch />解析结果</a></Button>
                            ) : null}
                            {!prospectus && !pdfMaterial ? (
                              <Button type="button" variant="secondary" size="sm" onClick={() => toggleParserEditor(material)} disabled={Boolean(bindingId) && !parserBinding}>
                                <Link2 />{parserExpanded ? '收起绑定' : material.parse_task_id ? '改绑 Parser' : '绑定 Parser'}
                              </Button>
                            ) : null}
                            {prospectus && sourceReviewable ? (
                              <>
                                <Button type="button" size="sm" onClick={() => handleReview(material, 'activate')} disabled={Boolean(actionId) || material.parse_status !== 'succeeded'}>
                                  <CheckCircle2 />审核并启用
                                </Button>
                                <Button type="button" variant="secondary" size="sm" onClick={() => handleReview(material, 'block')} disabled={Boolean(actionId)}>
                                  <ShieldAlert />阻止启用
                                </Button>
                              </>
                            ) : null}
                            {prospectus && sourceReady ? (
                              <Button type="button" variant="secondary" size="sm" onClick={() => handleDisable(material)} disabled={Boolean(actionId)}><Ban />停用分析源</Button>
                            ) : null}
                            {prospectus && material.document_status === 'active' ? (
                              <Button type="button" variant="secondary" size="sm" onClick={() => handleSupersede(material)} disabled={Boolean(actionId)}><History />由新版替代</Button>
                            ) : null}
                          </div>
                          {!prospectus && !pdfMaterial && parserExpanded ? (
                            <form className="space-y-2 rounded-md border border-border/70 p-3" onSubmit={(event) => void handleBindParserTask(event, material)}>
                              <label className="block space-y-1">
                                <span className="text-xs font-semibold text-text-muted">Parser task_id</span>
                                <Input value={parserDraft.taskId} onChange={(event) => updateParserDraft(material, { taskId: event.target.value })} disabled={parserBinding} />
                              </label>
                              <label className="block space-y-1">
                                <span className="text-xs font-semibold text-text-muted">解析产物路径（可选）</span>
                                <Input value={parserDraft.artifactPath} onChange={(event) => updateParserDraft(material, { artifactPath: event.target.value })} disabled={parserBinding} />
                              </label>
                              {parserErrors[material.document_id] ? <p className="text-xs text-destructive">{parserErrors[material.document_id]}</p> : null}
                              <Button type="submit" size="sm" disabled={parserBinding || !parserDraft.taskId.trim()}>
                                {parserBinding ? <Loader2 className="animate-spin" /> : <Link2 />}确认绑定
                              </Button>
                            </form>
                          ) : null}
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </PageSection>
          </div>

          <div className="min-w-0 space-y-5">
            <PageSection title="证据准备状态" compact>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3 xl:grid-cols-1">
                  <Surface kind="muted" padding="sm"><p className="text-xs text-text-muted">Evidence items</p><p className="mt-1 text-xl font-semibold text-text">{evidenceQuality?.item_count ?? evidenceQuality?.counts?.items ?? 0}</p></Surface>
                  <Surface kind="muted" padding="sm"><p className="text-xs text-text-muted">Verified</p><p className="mt-1 text-xl font-semibold text-text">{evidenceQuality?.verified_count ?? evidenceQuality?.counts?.verified ?? 0}</p></Surface>
                </div>
                <div className="space-y-2">
                  {EVIDENCE_DIMENSIONS.map((dimension) => {
                    const covered = availableDimensions.includes(dimension.value)
                    const missing = missingDimensions.includes(dimension.value)
                    return (
                      <div key={dimension.value} className="flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2 text-sm">
                        <span className="font-semibold text-text">{dimension.label}</span>
                        <StatusBadge tone={covered ? 'success' : missing ? 'warning' : 'neutral'}>{covered ? 'covered' : missing ? 'missing' : 'unknown'}</StatusBadge>
                      </div>
                    )
                  })}
                </div>
              </div>
            </PageSection>

            <PageSection title="入库预检" compact>
              <div className="space-y-3">
                <StatusBadge tone={statusTone(dryRunStatus(ingestDryRun))}>{dryRunStatus(ingestDryRun)}</StatusBadge>
                {ingestError ? <div className="rounded-md border border-warning/30 bg-warning/5 p-3 text-sm text-warning">{ingestError}</div> : null}
                {ingestDryRun ? (
                  <div className="grid gap-2">
                    {Object.entries(ingestDryRun.counts || {}).slice(0, 6).map(([key, value]) => (
                      <div key={key} className="flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2 text-sm"><span className="truncate text-text-muted">{key}</span><span className="font-semibold text-text">{text(value, '0')}</span></div>
                    ))}
                  </div>
                ) : <p className="text-sm text-text-muted">尚无预检结果</p>}
              </div>
            </PageSection>

            <Surface kind="muted" padding="sm">
              <p className="break-all text-xs leading-5 text-text-muted">{selectedDealId}</p>
              {availableDimensions.length ? <p className="mt-2 text-xs leading-5 text-text-muted">{availableDimensions.map((dimension) => dimensionLabel(dimension)).join(' / ')}</p> : null}
            </Surface>
          </div>
        </div>
      )}
    </PageShell>
  )
}
