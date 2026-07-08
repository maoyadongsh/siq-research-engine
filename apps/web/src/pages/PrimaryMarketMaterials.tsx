import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  DatabaseZap,
  ExternalLink,
  FileText,
  FolderOpen,
  Link2,
  Loader2,
  PackageCheck,
  RefreshCw,
  Trash2,
  Upload,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { DealDocument, DealEvidenceIngestDryRun, DealEvidenceQualityReport, DealSummary } from '@/lib/dealTypes'
import {
  bindPrimaryMarketDocumentParserTask,
  buildPrimaryMarketEvidence,
  deletePrimaryMarketDocument,
  dryRunPrimaryMarketEvidenceIngest,
  fetchPrimaryMarketDocuments,
  fetchPrimaryMarketEvidence,
  fetchPrimaryMarketProjects,
  uploadPrimaryMarketDocument,
} from '@/features/primary-market/primaryMarketApi'
import {
  DOCUMENT_TYPE_OPTIONS,
  EVIDENCE_DIMENSIONS,
  coverageText,
  createdByText,
  dimensionLabel,
  documentTitle,
  documentTypeLabel,
  evidenceDocumentMap,
  formatSize,
  formatTime,
  parserDraftFromDocument,
  parserLinks,
  sortDocuments,
  sortedDimensions,
  sortedMissingDimensions,
  statusTone,
  text,
  type ParserBindDraft,
} from '@/features/primary-market/primaryMarketViewModel'

function updateDealParam(setSearchParams: ReturnType<typeof useSearchParams>[1], dealId: string) {
  const next = new URLSearchParams()
  if (dealId) next.set('dealId', dealId)
  setSearchParams(next, { replace: true })
}

function materialTypeCounts(documents: DealDocument[]) {
  const counts = new Map<string, number>()
  for (const document of documents) {
    const type = document.document_type || 'uncategorized'
    counts.set(type, (counts.get(type) || 0) + 1)
  }
  return counts
}

function dryRunStatus(dryRun?: DealEvidenceIngestDryRun | null) {
  if (!dryRun) return '未执行'
  if (dryRun.errors?.length) return 'error'
  return dryRun.status || 'preview'
}

export default function PrimaryMarketMaterials() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedDealId = searchParams.get('dealId') || ''
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [deals, setDeals] = useState<DealSummary[]>([])
  const [dealsLoading, setDealsLoading] = useState(true)
  const [dealsError, setDealsError] = useState('')
  const [documents, setDocuments] = useState<DealDocument[]>([])
  const [evidenceQuality, setEvidenceQuality] = useState<DealEvidenceQualityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState('bp')
  const [sourceNote, setSourceNote] = useState('')
  const [activeType, setActiveType] = useState('all')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [deletingId, setDeletingId] = useState('')
  const [bindingId, setBindingId] = useState('')
  const [expandedParserId, setExpandedParserId] = useState('')
  const [parserDrafts, setParserDrafts] = useState<Record<string, ParserBindDraft>>({})
  const [parserErrors, setParserErrors] = useState<Record<string, string>>({})
  const [buildingEvidence, setBuildingEvidence] = useState(false)
  const [evidenceError, setEvidenceError] = useState('')
  const [ingestDryRun, setIngestDryRun] = useState<DealEvidenceIngestDryRun | null>(null)
  const [ingestBusy, setIngestBusy] = useState(false)
  const [ingestError, setIngestError] = useState('')

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

  const loadMaterials = useCallback(async (signal?: AbortSignal) => {
    if (!selectedDealId) return
    setLoading(true)
    setError('')
    setEvidenceError('')
    try {
      const [documentsPayload, evidencePayload] = await Promise.all([
        fetchPrimaryMarketDocuments(selectedDealId, signal),
        fetchPrimaryMarketEvidence(selectedDealId, signal).catch(() => null),
      ])
      setDocuments(sortDocuments(Array.isArray(documentsPayload.documents) ? documentsPayload.documents : []))
      setEvidenceQuality(evidencePayload?.quality_report || null)
    } catch (err) {
      if (!signal?.aborted) setError(err instanceof Error ? err.message : '材料加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [selectedDealId])

  useEffect(() => {
    const controller = new AbortController()
    setDocuments([])
    setEvidenceQuality(null)
    setIngestDryRun(null)
    setIngestError('')
    void loadMaterials(controller.signal)
    return () => controller.abort()
  }, [loadMaterials])

  const evidenceByDocument = useMemo(() => evidenceDocumentMap(evidenceQuality), [evidenceQuality])
  const typeCounts = useMemo(() => materialTypeCounts(documents), [documents])
  const filteredDocuments = activeType === 'all'
    ? documents
    : documents.filter((document) => (document.document_type || 'uncategorized') === activeType)
  const availableDimensions = sortedDimensions(evidenceQuality)
  const missingDimensions = sortedMissingDimensions(evidenceQuality)

  const resetUploadForm = () => {
    setSelectedFile(null)
    setDocumentType('bp')
    setSourceNote('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedDealId) {
      setUploadError('请选择项目')
      return
    }
    if (!selectedFile) {
      setUploadError('请选择要上传的文件')
      return
    }
    setUploading(true)
    setUploadError('')
    try {
      const payload = await uploadPrimaryMarketDocument(selectedDealId, {
        file: selectedFile,
        documentType,
        sourceNote,
      })
      setDocuments((current) => sortDocuments([
        payload.document,
        ...current.filter((item) => item.document_id !== payload.document.document_id),
      ]))
      setEvidenceQuality(null)
      setIngestDryRun(null)
      resetUploadForm()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : '文件上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (document: DealDocument) => {
    if (!selectedDealId) return
    if (!window.confirm(`确认删除材料：${documentTitle(document)}？`)) return
    setDeletingId(document.document_id)
    setError('')
    try {
      await deletePrimaryMarketDocument(selectedDealId, document.document_id)
      setDocuments((current) => current.filter((item) => item.document_id !== document.document_id))
      setEvidenceQuality(null)
      setIngestDryRun(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '文档删除失败')
    } finally {
      setDeletingId('')
    }
  }

  const updateParserDraft = (document: DealDocument, patch: Partial<ParserBindDraft>) => {
    setParserDrafts((current) => ({
      ...current,
      [document.document_id]: {
        ...(current[document.document_id] || parserDraftFromDocument(document)),
        ...patch,
      },
    }))
  }

  const toggleParserEditor = (document: DealDocument) => {
    setExpandedParserId((current) => {
      const next = current === document.document_id ? '' : document.document_id
      if (next) {
        setParserDrafts((drafts) => ({
          ...drafts,
          [document.document_id]: drafts[document.document_id] || parserDraftFromDocument(document),
        }))
        setParserErrors((errors) => ({ ...errors, [document.document_id]: '' }))
      }
      return next
    })
  }

  const handleBindParserTask = async (event: FormEvent<HTMLFormElement>, document: DealDocument) => {
    event.preventDefault()
    if (!selectedDealId) return
    const draft = parserDrafts[document.document_id] || parserDraftFromDocument(document)
    if (!draft.taskId.trim()) {
      setParserErrors((current) => ({ ...current, [document.document_id]: '请输入 task_id' }))
      return
    }
    setBindingId(document.document_id)
    setParserErrors((current) => ({ ...current, [document.document_id]: '' }))
    try {
      const payload = await bindPrimaryMarketDocumentParserTask(selectedDealId, document.document_id, {
        taskId: draft.taskId,
        artifactPath: draft.artifactPath,
        note: draft.note,
      })
      setDocuments((current) => sortDocuments(current.map((item) => (
        item.document_id === payload.document.document_id ? payload.document : item
      ))))
      setEvidenceQuality(null)
      setIngestDryRun(null)
      setParserDrafts((current) => ({
        ...current,
        [payload.document.document_id]: parserDraftFromDocument(payload.document),
      }))
      setExpandedParserId('')
    } catch (err) {
      setParserErrors((current) => ({
        ...current,
        [document.document_id]: err instanceof Error ? err.message : '解析任务绑定失败',
      }))
    } finally {
      setBindingId('')
    }
  }

  const handleBuildEvidence = async () => {
    if (!selectedDealId) return
    setBuildingEvidence(true)
    setEvidenceError('')
    setIngestDryRun(null)
    try {
      const payload = await buildPrimaryMarketEvidence(selectedDealId)
      setEvidenceQuality(payload.quality_report || null)
    } catch (err) {
      setEvidenceError(err instanceof Error ? err.message : '证据包构建失败')
    } finally {
      setBuildingEvidence(false)
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
        description="按项目上传材料、绑定解析任务、构建 evidence，并检查投委会证据覆盖。"
        actions={
          <Button type="button" variant="secondary" onClick={() => void loadMaterials()} disabled={loading || !selectedDealId}>
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </Button>
        }
      />

      <PageSection title="项目与材料准备度" compact>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.45fr)_minmax(260px,0.45fr)]">
          <label className="min-w-0 space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">项目</span>
            <select
              value={selectedDealId}
              onChange={(event) => updateDealParam(setSearchParams, event.target.value)}
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
            <div className="mt-2">
              <StatusBadge tone={statusTone(evidenceQuality?.status)}>{text(evidenceQuality?.status, '未构建')}</StatusBadge>
            </div>
          </Surface>
          <Surface kind="muted" padding="sm">
            <p className="text-xs text-text-muted">材料数量</p>
            <p className="mt-1 text-xl font-semibold text-text">{documents.length}</p>
            <p className="mt-2 text-xs text-text-muted">{selectedDeal?.industry || '行业未设置'} · {selectedDeal?.stage || '阶段未设置'}</p>
          </Surface>
        </div>
      </PageSection>

      {!selectedDealId ? (
        <PageSection>
          <EmptyState icon={FolderOpen} title="请选择项目" description="选择一个一级市场项目后即可上传材料和构建 evidence。" />
        </PageSection>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[220px_minmax(0,1.4fr)_minmax(300px,0.6fr)]">
          <PageSection title="材料类型" compact contentClassName="space-y-2">
            <button
              type="button"
              onClick={() => setActiveType('all')}
              className={`flex min-h-10 w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm font-semibold transition-colors ${activeType === 'all' ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60 text-text'}`}
            >
              <span>全部材料</span>
              <StatusBadge tone="neutral">{documents.length}</StatusBadge>
            </button>
            {DOCUMENT_TYPE_OPTIONS.map((option) => {
              const count = typeCounts.get(option.value) || 0
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setActiveType(option.value)}
                  className={`flex min-h-10 w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm font-semibold transition-colors ${activeType === option.value ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60 text-text'}`}
                >
                  <span>{option.label}</span>
                  <StatusBadge tone={count ? 'info' : 'neutral'}>{count}</StatusBadge>
                </button>
              )
            })}
          </PageSection>

          <div className="space-y-5">
            <PageSection title="上传材料" description="材料会进入当前 deal 项目包，由后端负责写入 data room。">
              <form onSubmit={handleUpload} className="space-y-4">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(180px,0.45fr)]">
                  <label className="min-w-0 space-y-1.5">
                    <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">文件</span>
                    <Input
                      ref={fileInputRef}
                      type="file"
                      disabled={uploading}
                      onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                    />
                  </label>
                  <label className="min-w-0 space-y-1.5">
                    <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">材料类型</span>
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
                <label className="block min-w-0 space-y-1.5">
                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">来源说明</span>
                  <Textarea
                    value={sourceNote}
                    onChange={(event) => setSourceNote(event.target.value)}
                    disabled={uploading}
                    rows={2}
                    placeholder="上传来源、版本、访谈对象或条款轮次"
                  />
                </label>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 text-sm text-text-muted">
                    {selectedFile ? (
                      <span className="break-all">已选择 {selectedFile.name} · {formatSize(selectedFile.size)}</span>
                    ) : (
                      <span>请选择一个文件上传。</span>
                    )}
                  </div>
                  <Button type="submit" disabled={uploading || !selectedFile} className="min-w-28">
                    {uploading ? <Loader2 className="animate-spin" /> : <Upload />}
                    上传
                  </Button>
                </div>
                {uploadError ? <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{uploadError}</div> : null}
              </form>
            </PageSection>

            <PageSection
              title="材料清单"
              description={`${filteredDocuments.length}/${documents.length} 个文档`}
              actions={
                <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={() => void handleBuildEvidence()} disabled={loading || buildingEvidence || uploading || Boolean(bindingId) || Boolean(deletingId)}>
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
              {evidenceError ? <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{evidenceError}</div> : null}
              {error ? <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div> : null}
              {loading ? (
                <div className="grid gap-3">
                  {Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-lg bg-muted/60" />)}
                </div>
              ) : filteredDocuments.length === 0 ? (
                <EmptyState icon={FileText} title="暂无材料" description="上传或切换材料类型后，项目文件会显示在这里。" />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[1080px] border-separate border-spacing-0 text-left text-sm">
                    <thead>
                      <tr className="text-xs uppercase tracking-wide text-text-muted">
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">材料</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">类型</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">状态</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">Parser</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">Evidence</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">上传</th>
                        <th className="border-b border-border/70 px-3 py-3 font-semibold">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredDocuments.map((document) => {
                        const links = parserLinks(document)
                        const evidenceRow = evidenceByDocument.get(document.document_id)
                        const draft = parserDrafts[document.document_id] || parserDraftFromDocument(document)
                        const isBinding = bindingId === document.document_id
                        const isParserExpanded = expandedParserId === document.document_id
                        return (
                          <Fragment key={document.document_id}>
                            <tr className="align-top transition-colors hover:bg-muted/20">
                              <td className="border-b border-border/50 px-3 py-3">
                                <div className="flex min-w-0 gap-3">
                                  <span className="premium-icon mt-0.5 h-8 w-8 shrink-0 rounded-lg"><FileText className="h-4 w-4" /></span>
                                  <div className="min-w-0">
                                    <p className="break-all font-semibold text-text">{documentTitle(document)}</p>
                                    <p className="mt-1 break-all font-mono text-xs text-text-muted">{document.document_id}</p>
                                    {document.source_note ? <p className="mt-2 text-xs leading-5 text-text-muted">{document.source_note}</p> : null}
                                  </div>
                                </div>
                              </td>
                              <td className="border-b border-border/50 px-3 py-3 text-text-muted">
                                {documentTypeLabel(document.document_type)}
                                <div className="mt-1 text-xs">{document.content_type || '-'}</div>
                              </td>
                              <td className="border-b border-border/50 px-3 py-3">
                                <StatusBadge tone={statusTone(document.status)}>{text(document.status, 'unknown')}</StatusBadge>
                              </td>
                              <td className="border-b border-border/50 px-3 py-3">
                                <div className="min-w-44 space-y-2">
                                  {document.parse_task_id ? (
                                    <div>
                                      <p className="break-all font-mono text-xs text-text">{document.parse_task_id}</p>
                                      <p className="mt-1 text-xs text-text-muted">{formatTime(document.parse_bound_at)}</p>
                                    </div>
                                  ) : <p className="text-xs text-text-muted">未绑定</p>}
                                  {links.length ? (
                                    <div className="flex flex-wrap gap-1.5">
                                      {links.map((link) => (
                                        <Button key={link.label} asChild variant="secondary" size="xs">
                                          <a href={link.url} target="_blank" rel="noreferrer"><ExternalLink />{link.label}</a>
                                        </Button>
                                      ))}
                                    </div>
                                  ) : null}
                                  <Button type="button" variant="secondary" size="sm" onClick={() => toggleParserEditor(document)} disabled={Boolean(bindingId) && !isBinding}>
                                    <Link2 />
                                    {isParserExpanded ? '收起' : document.parse_task_id ? '改绑任务' : '绑定任务'}
                                  </Button>
                                </div>
                              </td>
                              <td className="border-b border-border/50 px-3 py-3">
                                {evidenceRow ? (
                                  <div className="space-y-1">
                                    <StatusBadge tone={statusTone(String(evidenceRow.status || ''))}>{text(evidenceRow.status, 'unknown')}</StatusBadge>
                                    <p className="text-xs text-text-muted">{Number(evidenceRow.items || 0)} items</p>
                                    {evidenceRow.reason ? <p className="max-w-44 break-words text-xs text-text-muted">{text(evidenceRow.reason)}</p> : null}
                                  </div>
                                ) : <p className="text-xs text-text-muted">未构建</p>}
                              </td>
                              <td className="border-b border-border/50 px-3 py-3 text-text-muted">
                                {formatTime(document.created_at)}
                                <div className="mt-1 text-xs">{formatSize(document.size_bytes)}{createdByText(document) ? ` · ${createdByText(document)}` : ''}</div>
                              </td>
                              <td className="border-b border-border/50 px-3 py-3">
                                <Button variant="danger" size="sm" onClick={() => void handleDelete(document)} disabled={Boolean(deletingId) || uploading || Boolean(bindingId)}>
                                  {deletingId === document.document_id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                                  删除
                                </Button>
                              </td>
                            </tr>
                            {isParserExpanded ? (
                              <tr>
                                <td colSpan={7} className="border-b border-border/50 bg-muted/25 px-3 py-3">
                                  <form onSubmit={(event) => void handleBindParserTask(event, document)} className="space-y-3">
                                    <div className="grid gap-3 lg:grid-cols-[minmax(180px,0.7fr)_minmax(220px,1fr)_minmax(220px,1fr)_auto] lg:items-end">
                                      <label className="min-w-0 space-y-1.5">
                                        <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Task ID</span>
                                        <Input value={draft.taskId} onChange={(event) => updateParserDraft(document, { taskId: event.target.value })} disabled={isBinding} placeholder="parser task id" />
                                      </label>
                                      <label className="min-w-0 space-y-1.5">
                                        <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Artifact Path</span>
                                        <Input value={draft.artifactPath} onChange={(event) => updateParserDraft(document, { artifactPath: event.target.value })} disabled={isBinding} placeholder="可选" />
                                      </label>
                                      <label className="min-w-0 space-y-1.5">
                                        <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Note</span>
                                        <Input value={draft.note} onChange={(event) => updateParserDraft(document, { note: event.target.value })} disabled={isBinding} placeholder="可选" />
                                      </label>
                                      <Button type="submit" size="sm" disabled={isBinding || !draft.taskId.trim()}>
                                        {isBinding ? <Loader2 className="animate-spin" /> : <Link2 />}
                                        保存绑定
                                      </Button>
                                    </div>
                                    {parserErrors[document.document_id] ? <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">{parserErrors[document.document_id]}</div> : null}
                                  </form>
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </PageSection>
          </div>

          <div className="space-y-5">
            <PageSection title="证据准备状态" compact>
              <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Evidence items</p>
                    <p className="mt-1 text-xl font-semibold text-text">{evidenceQuality?.item_count ?? evidenceQuality?.counts?.items ?? 0}</p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Verified</p>
                    <p className="mt-1 text-xl font-semibold text-text">{evidenceQuality?.verified_count ?? evidenceQuality?.counts?.verified ?? 0}</p>
                  </Surface>
                </div>
                <div className="space-y-2">
                  {EVIDENCE_DIMENSIONS.map((dimension) => {
                    const covered = availableDimensions.includes(dimension.value)
                    const missing = missingDimensions.includes(dimension.value)
                    return (
                      <div key={dimension.value} className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2 text-sm">
                        <span className="font-semibold text-text">{dimension.label}</span>
                        <StatusBadge tone={covered ? 'success' : missing ? 'warning' : 'neutral'}>{covered ? 'covered' : missing ? 'missing' : 'unknown'}</StatusBadge>
                      </div>
                    )
                  })}
                </div>
                {evidenceQuality?.warnings?.length ? (
                  <Surface kind="muted" padding="sm">
                    <p className="text-sm font-semibold text-text">Warnings</p>
                    <div className="mt-2 grid gap-1">
                      {evidenceQuality.warnings.slice(0, 5).map((warning, index) => (
                        <p key={`${warning}-${index}`} className="break-words font-mono text-xs text-warning">{warning}</p>
                      ))}
                    </div>
                  </Surface>
                ) : null}
              </div>
            </PageSection>

            <PageSection title="入库预检" compact>
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge tone={statusTone(dryRunStatus(ingestDryRun))}>{dryRunStatus(ingestDryRun)}</StatusBadge>
                  {ingestDryRun ? <StatusBadge tone={ingestDryRun.milvus_written ? 'warning' : 'success'}>Milvus dry-run</StatusBadge> : null}
                </div>
                {ingestError ? <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning">{ingestError}</div> : null}
                {ingestDryRun ? (
                  <div className="grid gap-2">
                    {Object.entries(ingestDryRun.counts || {}).slice(0, 8).map(([key, value]) => (
                      <div key={key} className="flex items-center justify-between gap-3 rounded-lg bg-muted/40 px-3 py-2 text-sm">
                        <span className="text-text-muted">{key}</span>
                        <span className="font-semibold text-text">{text(value, '0')}</span>
                      </div>
                    ))}
                    {ingestDryRun.errors?.length ? <p className="break-words text-xs text-destructive">{ingestDryRun.errors.slice(0, 3).map((item) => text(item)).join(' / ')}</p> : null}
                    {ingestDryRun.warnings?.length ? <p className="break-words text-xs text-warning">{ingestDryRun.warnings.slice(0, 3).map((item) => text(item)).join(' / ')}</p> : null}
                  </div>
                ) : <p className="text-sm text-text-muted">执行 dry-run 可预览 PostgreSQL 与 Milvus 入库计划。</p>}
              </div>
            </PageSection>

            <Surface kind="muted" padding="sm">
              <p className="text-xs leading-5 text-text-muted">
                当前项目：<span className="font-mono text-text">{selectedDealId || '-'}</span>
              </p>
              {availableDimensions.length ? (
                <p className="mt-2 text-xs leading-5 text-text-muted">
                  覆盖维度：{availableDimensions.map((dimension) => dimensionLabel(dimension)).join(' / ')}
                </p>
              ) : null}
            </Surface>
          </div>
        </div>
      )}
    </PageShell>
  )
}
