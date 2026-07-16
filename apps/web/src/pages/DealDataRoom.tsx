import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink, FileSearch, FileText, FolderOpen, Link2, Loader2, PackageCheck, RefreshCw, Trash2, Upload } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { bindDealDocumentParserTask, buildDealEvidence, deleteDealDocument, fetchDealDocuments, fetchDealEvidence, uploadDealDocument } from '@/lib/dealApi'
import type { DealDocument, DealEvidenceQualityReport } from '@/lib/dealTypes'

interface ParserBindDraft {
  taskId: string
  artifactPath: string
  note: string
}

interface EvidenceDocumentRow {
  document_id?: string
  status?: string
  items?: number
  reason?: string
  [key: string]: unknown
}

function formatSize(value?: number | null) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatTime(value?: string | null) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function documentTitle(document: DealDocument) {
  return document.original_filename || document.filename || document.document_id
}

function createdByText(document: DealDocument) {
  const user = document.created_by
  if (!user) return ''
  return user.username || (user.id !== null && user.id !== undefined ? String(user.id) : '')
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  const value = String(status || '').toLowerCase()
  if (!value) return 'neutral'
  if (['ready', 'uploaded', 'available', 'completed', 'success', 'parse_bound'].includes(value)) return 'success'
  if (['failed', 'error', 'deleted'].includes(value)) return 'error'
  if (['processing', 'pending', 'queued'].includes(value)) return 'warning'
  return 'info'
}

function sortDocuments(documents: DealDocument[]) {
  return [...documents].sort((a, b) => {
    const aTime = new Date(a.created_at || '').getTime()
    const bTime = new Date(b.created_at || '').getTime()
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0)
  })
}

function parserLinks(document: DealDocument) {
  return [
    { label: '状态', url: document.parser_status_url },
    { label: '结果', url: document.parser_result_url },
    { label: '页面', url: document.parser_page_url },
  ].filter((item): item is { label: string; url: string } => Boolean(item.url))
}

function parserDraftFromDocument(document: DealDocument): ParserBindDraft {
  return {
    taskId: document.parse_task_id || '',
    artifactPath: document.parsed_artifact_path || '',
    note: '',
  }
}

function evidenceDocumentMap(report?: DealEvidenceQualityReport | null) {
  const rows = Array.isArray(report?.documents) ? report.documents as EvidenceDocumentRow[] : []
  const entries: Array<[string, EvidenceDocumentRow]> = []
  for (const row of rows) {
    const id = String(row.document_id || '')
    if (id) entries.push([id, row])
  }
  return new Map<string, EvidenceDocumentRow>(entries)
}

export default function DealDataRoom() {
  const { dealId = '' } = useParams()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [documents, setDocuments] = useState<DealDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState('')
  const [sourceNote, setSourceNote] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [deletingId, setDeletingId] = useState('')
  const [bindingId, setBindingId] = useState('')
  const [expandedParserId, setExpandedParserId] = useState('')
  const [parserDrafts, setParserDrafts] = useState<Record<string, ParserBindDraft>>({})
  const [parserErrors, setParserErrors] = useState<Record<string, string>>({})
  const [evidenceQuality, setEvidenceQuality] = useState<DealEvidenceQualityReport | null>(null)
  const [buildingEvidence, setBuildingEvidence] = useState(false)
  const [evidenceError, setEvidenceError] = useState('')

  const loadDocuments = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError('')
    try {
      const [payload, evidence] = await Promise.all([
        fetchDealDocuments(dealId, signal),
        fetchDealEvidence(dealId, signal).catch(() => null),
      ])
      setDocuments(sortDocuments(Array.isArray(payload.documents) ? payload.documents : []))
      setEvidenceQuality(evidence?.quality_report || null)
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : '文档清单加载失败')
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
      }
    }
  }, [dealId])

  useEffect(() => {
    const controller = new AbortController()
    void loadDocuments(controller.signal)
    return () => controller.abort()
  }, [dealId, loadDocuments])

  const handleUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedFile) {
      setUploadError('请选择要上传的文件')
      return
    }

    setUploading(true)
    setUploadError('')
    try {
      const payload = await uploadDealDocument(dealId, {
        file: selectedFile,
        documentType,
        sourceNote,
      })
      setDocuments((current) => sortDocuments([
        payload.document,
        ...current.filter((item) => item.document_id !== payload.document.document_id),
      ]))
      setEvidenceQuality(null)
      setSelectedFile(null)
      setDocumentType('')
      setSourceNote('')
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : '文件上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (document: DealDocument) => {
    setDeletingId(document.document_id)
    setError('')
    try {
      await deleteDealDocument(dealId, document.document_id)
      setDocuments((current) => current.filter((item) => item.document_id !== document.document_id))
      setEvidenceQuality(null)
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
    const draft = parserDrafts[document.document_id] || parserDraftFromDocument(document)
    if (!draft.taskId.trim()) {
      setParserErrors((current) => ({ ...current, [document.document_id]: '请输入 task_id' }))
      return
    }

    setBindingId(document.document_id)
    setParserErrors((current) => ({ ...current, [document.document_id]: '' }))
    try {
      const payload = await bindDealDocumentParserTask(dealId, document.document_id, {
        taskId: draft.taskId,
        artifactPath: draft.artifactPath,
        note: draft.note,
      })
      setDocuments((current) => sortDocuments(current.map((item) => (
        item.document_id === payload.document.document_id ? payload.document : item
      ))))
      setEvidenceQuality(null)
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
    setBuildingEvidence(true)
    setEvidenceError('')
    try {
      const payload = await buildDealEvidence(dealId)
      setEvidenceQuality(payload.quality_report || null)
    } catch (err) {
      setEvidenceError(err instanceof Error ? err.message : '证据包构建失败')
    } finally {
      setBuildingEvidence(false)
    }
  }

  const evidenceByDocument = evidenceDocumentMap(evidenceQuality)

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FolderOpen}
        eyebrow="Deal Data Room"
        title="Data Room"
        description="上传、查看和移除当前交易项目的数据室文档。"
        actions={
          <Button asChild variant="secondary">
            <Link to={`/deals/${encodeURIComponent(dealId)}`}>
              <ArrowLeft />
              返回项目
            </Link>
          </Button>
        }
      />

      <PageSection
        title="上传文档"
        description="文件会进入当前 deal 的数据室，可补充文档类型和来源说明。"
      >
        <form onSubmit={handleUpload} className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(180px,0.5fr)]">
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">File</span>
              <Input
                ref={fileInputRef}
                type="file"
                disabled={uploading}
                onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
              />
            </label>
            <label className="min-w-0 space-y-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Document Type</span>
              <Input
                value={documentType}
                onChange={(event) => setDocumentType(event.target.value)}
                disabled={uploading}
                placeholder="financial, legal, memo"
              />
            </label>
          </div>
          <label className="block min-w-0 space-y-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Source Note</span>
            <Textarea
              value={sourceNote}
              onChange={(event) => setSourceNote(event.target.value)}
              disabled={uploading}
              rows={2}
              placeholder="上传来源、版本或补充说明"
            />
          </label>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 text-sm text-text-muted">
              {selectedFile ? (
                <span className="break-all">
                  已选择 {selectedFile.name} · {formatSize(selectedFile.size)}
                </span>
              ) : (
                <span>请选择一个文件上传。</span>
              )}
            </div>
            <Button type="submit" disabled={uploading || !selectedFile} className="min-w-28">
              {uploading ? <Loader2 className="animate-spin" /> : <Upload />}
              上传
            </Button>
          </div>
          {uploadError ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              {uploadError}
            </div>
          ) : null}
        </form>
      </PageSection>

      <PageSection
        title="文档清单"
        description={`${documents.length} 个文档`}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={() => void handleBuildEvidence()}
              disabled={loading || uploading || buildingEvidence || Boolean(deletingId) || Boolean(bindingId)}
            >
              {buildingEvidence ? <Loader2 className="animate-spin" /> : <PackageCheck />}
              构建 Evidence
            </Button>
            <Button variant="secondary" onClick={() => void loadDocuments()} disabled={loading || uploading || Boolean(deletingId) || Boolean(bindingId)}>
              {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              刷新
            </Button>
          </div>
        }
      >
        {evidenceError ? (
          <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {evidenceError}
          </div>
        ) : null}
        {evidenceQuality ? (
          <Surface kind="muted" padding="sm" className="mb-3">
            <div className="flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                <FileSearch className="h-4 w-4 text-primary" />
                <span className="font-semibold text-text">Evidence</span>
                <StatusBadge tone={statusTone(evidenceQuality.status)}>{evidenceQuality.status || 'unknown'}</StatusBadge>
                <span className="text-text-muted">{evidenceQuality.item_count ?? evidenceQuality.counts?.items ?? 0} items</span>
              </div>
              <Link to={`/deals/${encodeURIComponent(dealId)}/evidence`} className="text-sm font-semibold text-primary hover:text-primary-dark">
                打开 Evidence
              </Link>
            </div>
          </Surface>
        ) : null}
        {error ? (
          <EmptyState title="文档操作失败" description={error} action={<Button onClick={() => void loadDocuments()}>重试</Button>} />
        ) : loading ? (
          <div className="grid gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-16 animate-pulse rounded-lg bg-muted/60" />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <EmptyState icon={FileText} title="暂无文档" description="上传后的 deal 文件会显示在这里。" />
        ) : (
          <div className="primary-market-table-scroll overflow-x-auto">
            <table className="w-full min-w-[1160px] border-separate border-spacing-0 text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-text-muted">
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">文档</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">类型</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">状态</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">Parser</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">Evidence</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">大小</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">上传时间</th>
                  <th className="border-b border-border/70 px-3 py-3 font-semibold">操作</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => {
                  const links = parserLinks(document)
                  const evidenceRow = evidenceByDocument.get(document.document_id)
                  const draft = parserDrafts[document.document_id] || parserDraftFromDocument(document)
                  const isBinding = bindingId === document.document_id
                  const isParserExpanded = expandedParserId === document.document_id
                  return (
                    <Fragment key={document.document_id}>
                      <tr className="align-top">
                        <td className="border-b border-border/50 px-3 py-3">
                          <div className="flex min-w-0 gap-3">
                            <span className="premium-icon mt-0.5 h-8 w-8 shrink-0 rounded-lg">
                              <FileText className="h-4 w-4" />
                            </span>
                            <div className="min-w-0">
                              <p className="break-all font-semibold text-text">{documentTitle(document)}</p>
                              <p className="mt-1 break-all font-mono text-xs text-text-muted">{document.document_id}</p>
                              {document.storage_path ? (
                                <p className="mt-1 break-all text-xs text-text-muted">{document.storage_path}</p>
                              ) : null}
                              {document.source_note ? (
                                <p className="mt-2 text-xs leading-5 text-text-muted">{document.source_note}</p>
                              ) : null}
                            </div>
                          </div>
                        </td>
                        <td className="border-b border-border/50 px-3 py-3 text-text-muted">
                          {document.document_type || '-'}
                          {document.content_type ? <div className="mt-1 text-xs">{document.content_type}</div> : null}
                        </td>
                        <td className="border-b border-border/50 px-3 py-3">
                          <StatusBadge tone={statusTone(document.status)}>{document.status || 'unknown'}</StatusBadge>
                        </td>
                        <td className="border-b border-border/50 px-3 py-3">
                          <div className="min-w-44 space-y-2">
                            {document.parse_task_id ? (
                              <div className="space-y-1">
                                <p className="break-all font-mono text-xs text-text">{document.parse_task_id}</p>
                                {document.parse_bound_at ? (
                                  <p className="text-xs text-text-muted">绑定于 {formatTime(document.parse_bound_at)}</p>
                                ) : null}
                              </div>
                            ) : (
                              <p className="text-xs text-text-muted">未绑定</p>
                            )}
                            {document.parsed_artifact_path ? (
                              <p className="break-all text-xs text-text-muted">{document.parsed_artifact_path}</p>
                            ) : null}
                            {links.length > 0 ? (
                              <div className="flex flex-wrap gap-1.5">
                                {links.map((link) => (
                                  <Button key={link.label} asChild variant="secondary" size="xs">
                                    <a href={link.url} target="_blank" rel="noreferrer">
                                      <ExternalLink />
                                      {link.label}
                                    </a>
                                  </Button>
                                ))}
                              </div>
                            ) : null}
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => toggleParserEditor(document)}
                              disabled={Boolean(bindingId) && !isBinding}
                            >
                              <Link2 />
                              {isParserExpanded ? '收起' : document.parse_task_id ? '改绑任务' : '绑定任务'}
                            </Button>
                          </div>
                        </td>
                        <td className="border-b border-border/50 px-3 py-3">
                          {evidenceRow ? (
                            <div className="min-w-32 space-y-1">
                              <StatusBadge tone={statusTone(String(evidenceRow.status || ''))}>{String(evidenceRow.status || 'unknown')}</StatusBadge>
                              <p className="text-xs text-text-muted">{Number(evidenceRow.items || 0)} items</p>
                              {evidenceRow.reason ? (
                                <p className="max-w-44 break-words text-xs text-text-muted">{String(evidenceRow.reason)}</p>
                              ) : null}
                            </div>
                          ) : (
                            <p className="text-xs text-text-muted">未构建</p>
                          )}
                        </td>
                        <td className="border-b border-border/50 px-3 py-3 text-text-muted">{formatSize(document.size_bytes)}</td>
                        <td className="border-b border-border/50 px-3 py-3 text-text-muted">
                          {formatTime(document.created_at)}
                          {createdByText(document) ? <div className="mt-1 text-xs">{createdByText(document)}</div> : null}
                        </td>
                        <td className="border-b border-border/50 px-3 py-3">
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => void handleDelete(document)}
                            disabled={Boolean(deletingId) || uploading || Boolean(bindingId)}
                          >
                            {deletingId === document.document_id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                            删除
                          </Button>
                        </td>
                      </tr>
                      {isParserExpanded ? (
                        <tr>
                          <td colSpan={8} className="border-b border-border/50 bg-muted/25 px-3 py-3">
                            <form onSubmit={(event) => void handleBindParserTask(event, document)} className="space-y-3">
                              <div className="grid gap-3 lg:grid-cols-[minmax(180px,0.75fr)_minmax(220px,1fr)_minmax(220px,1fr)_auto] lg:items-end">
                                <label className="min-w-0 space-y-1.5">
                                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Task ID</span>
                                  <Input
                                    value={draft.taskId}
                                    onChange={(event) => updateParserDraft(document, { taskId: event.target.value })}
                                    disabled={isBinding}
                                    placeholder="parser task id"
                                  />
                                </label>
                                <label className="min-w-0 space-y-1.5">
                                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Artifact Path</span>
                                  <Input
                                    value={draft.artifactPath}
                                    onChange={(event) => updateParserDraft(document, { artifactPath: event.target.value })}
                                    disabled={isBinding}
                                    placeholder="可选"
                                  />
                                </label>
                                <label className="min-w-0 space-y-1.5">
                                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Note</span>
                                  <Input
                                    value={draft.note}
                                    onChange={(event) => updateParserDraft(document, { note: event.target.value })}
                                    disabled={isBinding}
                                    placeholder="可选"
                                  />
                                </label>
                                <Button type="submit" size="sm" disabled={isBinding || !draft.taskId.trim()}>
                                  {isBinding ? <Loader2 className="animate-spin" /> : <Link2 />}
                                  保存绑定
                                </Button>
                              </div>
                              {parserErrors[document.document_id] ? (
                                <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                                  {parserErrors[document.document_id]}
                                </div>
                              ) : null}
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

      <Surface kind="muted" padding="sm">
        <p className="text-xs leading-5 text-text-muted">
          Deal ID: <span className="font-mono text-text">{dealId || '-'}</span>
        </p>
      </Surface>
    </PageShell>
  )
}
