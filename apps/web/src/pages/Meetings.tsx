import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  Archive,
  ArrowRight,
  CalendarClock,
  Download,
  FileUp,
  Loader2,
  Mic2,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UsersRound,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/useToast'
import {
  createMeetingExport,
  deleteMeeting,
  getMeetingCapabilities,
  listMeetings,
} from '@/features/meeting-transcription/api'
import {
  formatMeetingDate,
  formatMeetingDuration,
  meetingPostprocessStateLabel,
  meetingPostprocessStateTone,
  meetingDurationMs,
  meetingStateLabels,
} from '@/features/meeting-transcription/formatters'
import type { MeetingCapabilities, MeetingSession, MeetingSessionState } from '@/features/meeting-transcription/types'

function stateTone(state: MeetingSessionState) {
  if (state === 'live') return 'success' as const
  if (state === 'paused' || state === 'interrupted') return 'warning' as const
  if (state === 'stopping' || state === 'connecting' || state === 'reconnecting' || state === 'stopped') return 'info' as const
  if (state === 'deleted') return 'error' as const
  return 'neutral' as const
}

function meetingHref(meeting: MeetingSession) {
  return ['draft', 'connecting', 'live', 'paused', 'reconnecting', 'interrupted'].includes(meeting.state)
    ? `/meetings/${encodeURIComponent(meeting.id)}/live`
    : `/meetings/${encodeURIComponent(meeting.id)}`
}

export default function Meetings() {
  const { toast } = useToast()
  const [capabilities, setCapabilities] = useState<MeetingCapabilities | null>(null)
  const [meetings, setMeetings] = useState<MeetingSession[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [queryDraft, setQueryDraft] = useState('')
  const [query, setQuery] = useState('')
  const [stateFilter, setStateFilter] = useState('all')
  const [sort, setSort] = useState('started_at_desc')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const limit = 20

  const refresh = useCallback(() => setReloadKey((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    void getMeetingCapabilities(controller.signal).then(async (capabilityPayload) => {
      setCapabilities(capabilityPayload)
      if (!capabilityPayload.enabled) {
        setMeetings([])
        setTotal(0)
        setError('')
        return
      }
      const payload = await listMeetings({ q: query, state: stateFilter === 'all' ? '' : stateFilter, sort, offset, limit }, controller.signal)
      setMeetings(Array.isArray(payload.items) ? payload.items : [])
      setTotal(payload.total || 0)
      setError('')
    }).catch((loadError) => {
      if (!controller.signal.aborted) setError(loadError instanceof Error ? loadError.message : '会议列表加载失败')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [offset, query, reloadKey, sort, stateFilter])

  const activeCount = useMemo(() => meetings.filter((meeting) => ['live', 'paused', 'reconnecting'].includes(meeting.state)).length, [meetings])
  const completedCount = useMemo(() => meetings.filter((meeting) => meeting.postprocess_state === 'succeeded').length, [meetings])
  const meetingImportEnabled = import.meta.env.VITE_SIQ_MEETING_IMPORT_ENABLED === '1'

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setQuery(queryDraft.trim())
  }

  async function remove(meeting: MeetingSession) {
    if (!window.confirm(`删除“${meeting.title}”及其录音、逐字稿和产物？此操作不可撤销。`)) return
    setBusyId(meeting.id)
    try {
      await deleteMeeting(meeting.id)
      toast({ title: '会议已进入删除流程', type: 'success' })
      refresh()
    } catch (deleteError) {
      toast({ title: '删除失败', description: deleteError instanceof Error ? deleteError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyId('')
    }
  }

  async function exportMarkdown(meeting: MeetingSession) {
    setBusyId(meeting.id)
    try {
      const job = await createMeetingExport(meeting.id, { format: 'markdown', transcript_source: 'display' })
      toast({ title: '导出任务已创建', description: job.state === 'ready' ? '文件已可下载' : '可在会议详情的“文件与导出”中查看进度。', type: 'success' })
    } catch (exportError) {
      toast({ title: '创建导出失败', description: exportError instanceof Error ? exportError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyId('')
    }
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={Mic2}
        eyebrow="Meeting Transcription"
        title="会议转写"
        description="录制会议、查看实时逐字稿，并在会后复核纪要与识别订正。"
        actions={
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" onClick={refresh} disabled={loading}><RefreshCw className={loading ? 'animate-spin' : ''} />刷新</Button>
            {meetingImportEnabled ? <Button asChild variant="secondary"><Link to="/meetings/import"><FileUp />导入录音</Link></Button> : null}
            <Button asChild disabled={capabilities?.enabled === false}><Link to="/meetings/new"><Plus />开始实时会议</Link></Button>
          </div>
        }
      />

      {capabilities?.enabled === false ? (
        <PageSection><EmptyState icon={Mic2} title="会议转写暂未开放" description="功能开关已关闭，其他研究和聊天功能不受影响。" /></PageSection>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Surface kind="card"><p className="text-sm text-text-muted">我的会议</p><p className="mt-1 text-2xl font-semibold tabular-nums text-text">{total}</p></Surface>
            <Surface kind="card"><p className="text-sm text-text-muted">正在进行</p><p className="mt-1 text-2xl font-semibold tabular-nums text-text">{activeCount}</p></Surface>
            <Surface kind="card"><p className="text-sm text-text-muted">处理完成</p><p className="mt-1 text-2xl font-semibold tabular-nums text-text">{completedCount}</p></Surface>
          </div>

          <PageSection
            title="会议记录"
            description="列表仅加载摘要，不读取全文、声纹特征或大体积音频元数据。"
            actions={
              <form onSubmit={submitSearch} className="flex min-w-0 gap-2">
                <Input value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} placeholder="搜索会议标题" aria-label="搜索会议标题" className="w-48 sm:w-56" />
                <Button type="submit" variant="secondary" size="icon" className="max-sm:size-11" aria-label="搜索"><Search /></Button>
              </form>
            }
          >
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Select value={stateFilter} onValueChange={(value) => { setStateFilter(value); setOffset(0) }}>
                <SelectTrigger aria-label="按状态筛选" className="w-40"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="live">进行中</SelectItem>
                  <SelectItem value="paused">已暂停</SelectItem>
                  <SelectItem value="interrupted">异常中断</SelectItem>
                  <SelectItem value="stopped">已结束</SelectItem>
                  <SelectItem value="archived">已归档</SelectItem>
                </SelectContent>
              </Select>
              <Select value={sort} onValueChange={(value) => { setSort(value); setOffset(0) }}>
                <SelectTrigger aria-label="会议排序" className="w-40"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="started_at_desc">最近开始</SelectItem><SelectItem value="started_at_asc">最早开始</SelectItem><SelectItem value="updated_at_desc">最近更新</SelectItem></SelectContent>
              </Select>
            </div>

            {error ? (
              <EmptyState icon={Archive} title="会议列表加载失败" description={error} action={<Button onClick={refresh}>重试</Button>} />
            ) : loading ? (
              <div className="grid gap-3">{Array.from({ length: 5 }, (_, index) => <div key={index} className="h-28 animate-pulse rounded-md bg-muted/60" />)}</div>
            ) : meetings.length === 0 ? (
              <EmptyState icon={Mic2} title="暂无会议记录" description="开始会议后，录音、逐字稿和纪要会显示在这里。" action={<Button asChild><Link to="/meetings/new"><Plus />开始实时会议</Link></Button>} />
            ) : (
              <div className="divide-y divide-border/70">
                {meetings.map((meeting) => {
                  const duration = meeting.duration_ms ?? meetingDurationMs(meeting.started_at, meeting.stopped_at)
                  const continuing = ['draft', 'connecting', 'live', 'paused', 'reconnecting', 'interrupted'].includes(meeting.state)
                  return (
                    <article key={meeting.id} className="py-4 first:pt-0 last:pb-0">
                      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <h3 className="min-w-0 break-words text-base font-semibold text-text">{meeting.title}</h3>
                            <StatusBadge tone={stateTone(meeting.state)}>{meetingStateLabels[meeting.state] || meeting.state}</StatusBadge>
                            {meeting.state === 'stopped' || meeting.state === 'archived' || meeting.postprocess_state !== 'not_started' ? (
                              <StatusBadge tone={meetingPostprocessStateTone(meeting.postprocess_state)}>{meetingPostprocessStateLabel(meeting.postprocess_state)}</StatusBadge>
                            ) : null}
                          </div>
                          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs leading-5 text-text-muted">
                            <span className="inline-flex items-center gap-1.5"><CalendarClock className="h-3.5 w-3.5" />{formatMeetingDate(meeting.started_at || meeting.created_at)}</span>
                            <span className="font-mono tabular-nums">{formatMeetingDuration(duration)}</span>
                            <span className="inline-flex items-center gap-1.5"><UsersRound className="h-3.5 w-3.5" />{meeting.participant_count ?? meeting.speaker_count ?? 0} 位发言人</span>
                            <span>{meeting.model_label || (meeting.ai_enabled ? 'AI 模型已配置' : '仅录音和转写')}{meeting.model_locality ? ` · ${meeting.model_locality === 'cloud' ? '云端' : '本地'}` : ''}</span>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2 lg:justify-end">
                          {!continuing ? <Button type="button" variant="ghost" size="icon-sm" className="max-sm:size-11" onClick={() => void exportMarkdown(meeting)} disabled={busyId === meeting.id} aria-label="导出 Markdown"><Download /></Button> : null}
                          {!['live', 'connecting', 'reconnecting'].includes(meeting.state) ? <Button type="button" variant="ghost" size="icon-sm" className="max-sm:size-11" onClick={() => void remove(meeting)} disabled={busyId === meeting.id} aria-label="删除会议"><Trash2 /></Button> : null}
                          <Button asChild variant={continuing ? 'default' : 'secondary'} size="sm" className="max-sm:h-11">
                            <Link to={meetingHref(meeting)}>{busyId === meeting.id ? <Loader2 className="animate-spin" /> : <ArrowRight />}{continuing ? '继续会议' : '打开详情'}</Link>
                          </Button>
                        </div>
                      </div>
                    </article>
                  )
                })}
              </div>
            )}

            {total > limit ? (
              <div className="mt-5 flex items-center justify-between border-t border-border/70 pt-4">
                <Button type="button" variant="secondary" size="sm" className="max-sm:h-11" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</Button>
                <span className="text-xs tabular-nums text-text-muted">{offset + 1}-{Math.min(offset + limit, total)} / {total}</span>
                <Button type="button" variant="secondary" size="sm" className="max-sm:h-11" disabled={offset + limit >= total || loading} onClick={() => setOffset(offset + limit)}>下一页</Button>
              </div>
            ) : null}
          </PageSection>
        </>
      )}
    </PageShell>
  )
}
