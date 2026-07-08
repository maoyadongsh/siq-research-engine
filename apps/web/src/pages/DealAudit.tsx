import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ShieldCheck } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { fetchDealAudit, fetchDealManifest } from '@/lib/dealApi'
import type { DealAuditEvent, DealAuditResponse, DealManifestFileSummary, DealManifestResponse } from '@/lib/dealTypes'

function formatEventTime(value?: string) {
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
function eventTitle(event: DealAuditEvent) {
  return String(event.event_type || event.type || 'audit_event')
}

function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function statusTone(status?: string | null): 'neutral' | 'info' | 'success' | 'warning' | 'error' {
  if (status === 'pass' || status === 'match' || status === 'primary' || status === 'fallback' || status === 'imported') return 'success'
  if (status === 'warn' || status === 'single_source' || status === 'pending') return 'warning'
  if (status === 'missing' || status === 'mismatch' || status === 'none' || status === 'failed' || status === 'rejected') return 'error'
  return 'neutral'
}

function compactList(values?: string[], limit = 3) {
  if (!Array.isArray(values) || values.length === 0) return '无'
  const shown = values.slice(0, limit).join(', ')
  return values.length > limit ? `${shown} +${values.length - limit}` : shown
}

function booleanText(value?: boolean | null) {
  if (value === true) return '是'
  if (value === false) return '否'
  return '未记录'
}

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

function fileKey(file: DealManifestFileSummary, index: number) {
  return `${file.source || file.target || file.sha256 || 'manifest-file'}-${index}`
}

export default function DealAudit() {
  const { dealId = '' } = useParams()
  const [auditData, setAuditData] = useState<DealAuditResponse | null>(null)
  const [manifestData, setManifestData] = useState<DealManifestResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [auditError, setAuditError] = useState('')
  const [manifestError, setManifestError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setLoading(true)
      setAuditError('')
      setManifestError('')
      setAuditData(null)
      setManifestData(null)

      const [auditResult, manifestResult] = await Promise.allSettled([
        fetchDealAudit(dealId, controller.signal),
        fetchDealManifest(dealId, controller.signal),
      ])

      if (controller.signal.aborted) return

      if (auditResult.status === 'fulfilled') {
        setAuditData(auditResult.value)
      } else {
        setAuditError(errorMessage(auditResult.reason, '审计链加载失败'))
      }

      if (manifestResult.status === 'fulfilled') {
        setManifestData(manifestResult.value)
      } else {
        setManifestError(errorMessage(manifestResult.reason, 'Manifest summary 加载失败'))
      }

      setLoading(false)
    })()
    return () => controller.abort()
  }, [dealId])

  const events = Array.isArray(auditData?.audit.events) ? auditData.audit.events : []
  const summary = auditData?.summary
  const manifestSummary = manifestData?.summary
  const manifestCounts = manifestSummary?.counts
  const legacyImport = manifestSummary?.openclaw_import
  const archiveManifest = manifestSummary?.archive_manifest
  const manifestFiles = Array.isArray(manifestSummary?.files) ? manifestSummary.files : []
  const shownManifestFiles = manifestFiles.slice(0, 8)

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={ShieldCheck}
        eyebrow="Deal Audit"
        title="审计链"
        description="项目导入、阶段推进、人工确认和 override 的可追溯事件。"
        actions={
          <Button asChild variant="secondary">
            <Link to={`/deals/${encodeURIComponent(dealId)}`}>
              <ArrowLeft />
              返回项目
            </Link>
          </Button>
        }
      />

      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted/60" />
      ) : (
        <>
          <PageSection
            title="归档摘要"
            description={
              manifestSummary ? `Generated: ${text(manifestSummary.generated_at)}` : '归档摘要用于核对历史来源、文件清单和文件哈希。'
            }
            actions={
              manifestSummary ? <StatusBadge tone={statusTone(manifestSummary.status)}>{text(manifestSummary.status)}</StatusBadge> : null
            }
          >
            {manifestError ? (
              <EmptyState title="Manifest summary 加载失败" description={manifestError} size="sm" />
            ) : manifestSummary ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">历史来源</p>
                    <p className="mt-1 text-2xl font-semibold text-text">{booleanText(legacyImport?.present)}</p>
                    <p className="mt-1 break-all text-xs text-text-muted">
                      legacy {text(legacyImport?.legacy_project_id)} · metadata {booleanText(legacyImport?.metadata_present)}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">归档文件</p>
                    <p className="mt-1 text-2xl font-semibold text-text">{manifestCounts?.imported_files ?? legacyImport?.file_count ?? 0}</p>
                    <p className="mt-1 text-xs text-text-muted">
                      missing {manifestCounts?.missing_files ?? 0} · rejected {manifestCounts?.rejected_files ?? 0}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Hashes</p>
                    <p className="mt-1 text-2xl font-semibold text-text">{manifestCounts?.hashes ?? 0}</p>
                    <p className="mt-1 text-xs text-text-muted">
                      with {manifestCounts?.files_with_hash ?? 0} · missing {manifestCounts?.files_missing_hash ?? 0}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Archive Consistency</p>
                    <div className="mt-2">
                      <StatusBadge tone={statusTone(archiveManifest?.consistency)}>
                        {text(archiveManifest?.consistency)}
                      </StatusBadge>
                    </div>
                    <p className="mt-2 break-all text-xs text-text-muted">
                      {archiveManifest?.available ? 'available' : 'unavailable'} · {text(archiveManifest?.path)}
                    </p>
                  </Surface>
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Archive Manifest</p>
                    <p className="mt-2 break-all text-sm text-text-muted">
                      files {archiveManifest?.file_count ?? manifestCounts?.archive_files ?? 0} · path {text(archiveManifest?.path)}
                    </p>
                  </Surface>
                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Warnings</p>
                    <p className="mt-3 break-words text-sm text-text-muted">{compactList(manifestSummary.warnings, 6)}</p>
                  </Surface>
                </div>

                <Surface kind="row" padding="sm">
                  <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                    <p className="text-sm font-semibold text-text">Files Preview</p>
                    <p className="text-xs text-text-muted">
                      Showing {shownManifestFiles.length} of {manifestFiles.length}
                    </p>
                  </div>
                  {shownManifestFiles.length ? (
                    <div className="mt-3 space-y-2">
                      {shownManifestFiles.map((file, index) => (
                        <div key={fileKey(file, index)} className="rounded-md border border-border bg-bg p-3">
                          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                            <div className="min-w-0 space-y-1">
                              <p className="break-all text-sm font-semibold text-text">{text(file.target || file.source, '未记录路径')}</p>
                              <p className="break-all text-xs text-text-muted">source {text(file.source)}</p>
                              <p className="break-all text-xs text-text-muted">sha256 {text(file.sha256)}</p>
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2">
                              <StatusBadge tone={statusTone(file.status)}>{text(file.status)}</StatusBadge>
                              <StatusBadge tone={file.hash_recorded ? 'success' : 'warning'}>
                                hash {file.hash_recorded ? 'recorded' : 'missing'}
                              </StatusBadge>
                              <StatusBadge tone={file.hash_matches === false ? 'error' : file.hash_matches ? 'success' : 'neutral'}>
                                match {booleanText(file.hash_matches)}
                              </StatusBadge>
                            </div>
                          </div>
                          {file.reason ? <p className="mt-2 break-words text-xs text-text-muted">{file.reason}</p> : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState title="暂无文件条目" description="Manifest summary 未返回 files 列表。" size="sm" />
                  )}
                </Surface>
              </div>
            ) : (
              <EmptyState title="暂无 manifest summary" description="后端尚未返回归档摘要。" size="sm" />
            )}
          </PageSection>

          {auditError ? (
            <PageSection>
              <EmptyState title="审计链加载失败" description={auditError} />
            </PageSection>
          ) : !auditData ? (
        <PageSection>
          <EmptyState title="暂无审计链" description="项目包中没有 audit_log.json。" />
        </PageSection>
      ) : (
        <>
          <PageSection
            title="Audit Summary"
            description={summary ? `Generated: ${text(summary.generated_at)}` : '后端尚未返回审计摘要。'}
            actions={summary ? <StatusBadge tone={statusTone(summary.status)}>{text(summary.status)}</StatusBadge> : null}
          >
            {summary ? (
              <div className="space-y-3">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Selected Source</p>
                    <div className="mt-2">
                      <StatusBadge tone={statusTone(summary.sources?.selected)}>{text(summary.sources?.selected)}</StatusBadge>
                    </div>
                    <p className="mt-2 break-all text-xs text-text-muted">
                      {text(summary.sources?.primary?.path)}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Consistency</p>
                    <div className="mt-2">
                      <StatusBadge tone={statusTone(summary.sources?.consistency)}>
                        {text(summary.sources?.consistency)}
                      </StatusBadge>
                    </div>
                    <p className="mt-2 text-xs text-text-muted">
                      primary {summary.sources?.primary?.available ? 'on' : 'off'} · fallback{' '}
                      {summary.sources?.fallback?.available ? 'on' : 'off'}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Events</p>
                    <p className="mt-1 text-2xl font-semibold text-text">{summary.counts?.events ?? 0}</p>
                    <p className="mt-1 text-xs text-text-muted">
                      confirmation {summary.counts?.human_confirmation ?? 0} · override {summary.counts?.manual_override ?? 0}
                    </p>
                  </Surface>
                  <Surface kind="muted" padding="sm">
                    <p className="text-xs text-text-muted">Latest Event</p>
                    <p className="mt-1 break-all text-sm font-semibold text-text">
                      {summary.latest_event ? eventTitle(summary.latest_event) : '未记录'}
                    </p>
                    <p className="mt-1 text-xs text-text-muted">{formatEventTime(summary.latest_event?.created_at)}</p>
                  </Surface>
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Tracked Events</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(summary.required_event_status || []).map((item) => (
                        <StatusBadge key={item.event_type} tone={item.present ? 'success' : item.required ? 'error' : 'warning'}>
                          {item.event_type}: {item.present ? item.count ?? 1 : 'missing'}
                        </StatusBadge>
                      ))}
                    </div>
                  </Surface>
                  <Surface kind="row" padding="sm">
                    <p className="text-sm font-semibold text-text">Warnings</p>
                    <p className="mt-3 break-words text-sm text-text-muted">{compactList(summary.warnings, 5)}</p>
                  </Surface>
                </div>
              </div>
            ) : (
              <EmptyState title="暂无审计摘要" description="当前响应只包含原始审计事件。" size="sm" />
            )}
          </PageSection>

          <PageSection title="事件列表">
            {events.length ? (
              <div className="space-y-3">
                {events.map((event, index) => (
                  <Surface key={`${eventTitle(event)}-${index}`} kind="row" padding="sm">
                    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                      <div className="min-w-0">
                        <p className="font-semibold text-text">{eventTitle(event)}</p>
                        <p className="mt-1 text-xs text-text-muted">{formatEventTime(event.created_at)}</p>
                      </div>
                      <pre className="max-h-36 min-w-0 overflow-auto rounded-md bg-muted/60 p-2 text-xs text-text-muted md:max-w-xl">
                        {JSON.stringify(event, null, 2)}
                      </pre>
                    </div>
                  </Surface>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无审计事件" description="后续导入、运行 agent 和人工确认会写入这里。" />
            )}
          </PageSection>

          <PageSection title="原始审计 JSON">
            <pre className="max-h-[520px] overflow-auto rounded-lg bg-muted/60 p-3 text-xs text-text-muted">
              {JSON.stringify(auditData.audit, null, 2)}
            </pre>
          </PageSection>
        </>
      )}
        </>
      )}
    </PageShell>
  )
}
