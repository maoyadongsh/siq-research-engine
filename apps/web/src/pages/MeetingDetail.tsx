import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CheckSquare,
  Download,
  FileJson,
  FileText,
  Loader2,
  MessageSquareQuote,
  RefreshCw,
  RotateCw,
  Sparkles,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/useToast'
import { downloadAuthenticatedFile } from '@/lib/authenticatedFiles'
import {
  correctMeetingSegment,
  createMeetingExport,
  createMeetingExportTicket,
  createVoiceprint,
  enrollMeetingVoiceprint,
  getMeeting,
  getMeetingCapabilities,
  getMeetingArtifacts,
  getMeetingJobs,
  getMeetingSpeakers,
  getMeetingTranscript,
  listMeetingExports,
  mergeMeetingSpeakers,
  regenerateMeetingArtifact,
  renameMeetingSegmentSpeaker,
  renameMeetingSpeaker,
  retryMeetingJob,
  revertMeetingSegment,
} from '@/features/meeting-transcription/api'
import { MeetingArtifacts, MeetingMinutesSection } from '@/features/meeting-transcription/components/MeetingArtifacts'
import { MeetingAudioPlayer } from '@/features/meeting-transcription/components/MeetingAudioPlayer'
import { SpeakerPanel } from '@/features/meeting-transcription/components/SpeakerPanel'
import { TranscriptTimeline } from '@/features/meeting-transcription/components/TranscriptTimeline'
import {
  formatMeetingDate,
  formatMeetingDuration,
  formatMeetingTimestamp,
  meetingDurationMs,
  meetingPostprocessStateLabel,
  meetingPostprocessStateTone,
  meetingStateLabels,
} from '@/features/meeting-transcription/formatters'
import {
  isMinutesArtifact,
  parseMeetingMinutes,
  selectPreferredMinutesArtifact,
} from '@/features/meeting-transcription/meetingArtifacts'
import {
  earlierSegmentsFromPage,
  earlierTranscriptAfterOrdinal,
  earliestTranscriptOrdinal,
  initialTranscriptAfterOrdinal,
  MEETING_TRANSCRIPT_PAGE_SIZE,
  mergeTranscriptSegments,
} from '@/features/meeting-transcription/transcriptPagination'
import type {
  MeetingArtifact,
  MeetingExport,
  MeetingExportFormat,
  MeetingJob,
  MeetingSession,
  MeetingSpeakerTrack,
  MeetingSpeakerRenameScope,
  MeetingTranscriptSegment,
  SegmentCorrectionRequest,
} from '@/features/meeting-transcription/types'

const exportStateLabels: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  ready: '可下载',
  failed: '生成失败',
}

export default function MeetingDetail() {
  const { meetingId = '' } = useParams()
  const { toast } = useToast()
  const [session, setSession] = useState<MeetingSession | null>(null)
  const [segments, setSegments] = useState<MeetingTranscriptSegment[]>([])
  const [speakers, setSpeakers] = useState<MeetingSpeakerTrack[]>([])
  const [artifacts, setArtifacts] = useState<MeetingArtifact[]>([])
  const [jobs, setJobs] = useState<MeetingJob[]>([])
  const [exports, setExports] = useState<MeetingExport[]>([])
  const [exportFormat, setExportFormat] = useState<MeetingExportFormat>('markdown')
  const [exportContent, setExportContent] = useState<'transcript' | 'minutes'>('transcript')
  const [exportTranscriptSource, setExportTranscriptSource] = useState<'display' | 'asr'>('display')
  const [correctionLearningEnabled, setCorrectionLearningEnabled] = useState(false)
  const [hasEarlierSegments, setHasEarlierSegments] = useState(false)
  const [nextTranscriptOrdinal, setNextTranscriptOrdinal] = useState<number | null>(null)
  const [transcriptPageBusy, setTranscriptPageBusy] = useState<'earlier' | 'later' | null>(null)
  const [seekToMs, setSeekToMs] = useState<number | null>(null)
  const [scrollToSegmentId, setScrollToSegmentId] = useState('')
  const [activeTab, setActiveTab] = useState('minutes')
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')
  const minutesArtifact = useMemo(() => selectPreferredMinutesArtifact(artifacts), [artifacts])
  const minutesContent = useMemo(() => parseMeetingMinutes(minutesArtifact?.content_json), [minutesArtifact])
  const segmentById = useMemo(() => new Map(segments.map((segment) => [segment.id, segment])), [segments])

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!meetingId) return
    setLoading(true)
    try {
      const meetingPromise = getMeeting(meetingId, signal)
      const meetingTranscriptPromise = meetingPromise.then(async (meeting) => ({
        meeting,
        transcript: await getMeetingTranscript(meetingId, {
          afterOrdinal: initialTranscriptAfterOrdinal(meeting.last_segment_ordinal),
          limit: MEETING_TRANSCRIPT_PAGE_SIZE,
        }, signal),
      }))
      const [{ meeting, transcript }, [speakerItems, artifactItems, jobItems, exportItems, capabilities]] = await Promise.all([
        meetingTranscriptPromise,
        Promise.all([
          getMeetingSpeakers(meetingId, signal),
          getMeetingArtifacts(meetingId, signal),
          getMeetingJobs(meetingId, signal),
          listMeetingExports(meetingId, signal),
          getMeetingCapabilities(signal).catch(() => null),
        ]),
      ])
      setSession(meeting)
      setSegments(transcript.items || [])
      setSpeakers(speakerItems)
      setArtifacts(artifactItems)
      setJobs(jobItems)
      setExports(exportItems)
      setCorrectionLearningEnabled(Boolean(capabilities?.correction_learning?.available))
      setHasEarlierSegments((transcript.items[0]?.ordinal ?? 1) > 1)
      setNextTranscriptOrdinal(transcript.next_ordinal ?? null)
      setError('')
    } catch (loadError) {
      if (!signal?.aborted) setError(loadError instanceof Error ? loadError.message : '会议详情加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [meetingId])

  const refreshExports = useCallback(async (signal?: AbortSignal) => {
    if (!meetingId) return []
    const items = await listMeetingExports(meetingId, signal)
    if (!signal?.aborted) setExports(items)
    return items
  }, [meetingId])

  const refreshProcessing = useCallback(async (signal?: AbortSignal) => {
    if (!meetingId) return []
    const [meeting, artifactItems, jobItems] = await Promise.all([
      getMeeting(meetingId, signal),
      getMeetingArtifacts(meetingId, signal),
      getMeetingJobs(meetingId, signal),
    ])
    if (!signal?.aborted) {
      setSession(meeting)
      setArtifacts(artifactItems)
      setJobs(jobItems)
    }
    return jobItems
  }, [meetingId])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void load(controller.signal)
    })
    return () => controller.abort()
  }, [load])

  const hasPendingExport = exports.some((item) => item.state === 'queued' || item.state === 'running')

  useEffect(() => {
    if (!hasPendingExport) return undefined
    const controller = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      try {
        const items = await refreshExports(controller.signal)
        if (!controller.signal.aborted && items.some((item) => item.state === 'queued' || item.state === 'running')) {
          timer = window.setTimeout(() => void poll(), 2_000)
        }
      } catch {
        if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 4_000)
      }
    }
    timer = window.setTimeout(() => void poll(), 1_000)
    return () => {
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [hasPendingExport, refreshExports])

  const hasPendingMeetingJob = jobs.some((item) => item.state === 'queued' || item.state === 'running')

  useEffect(() => {
    if (!hasPendingMeetingJob) return undefined
    const controller = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      try {
        const items = await refreshProcessing(controller.signal)
        if (!controller.signal.aborted && items.some((item) => item.state === 'queued' || item.state === 'running')) {
          timer = window.setTimeout(() => void poll(), 2_000)
        }
      } catch {
        if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 4_000)
      }
    }
    timer = window.setTimeout(() => void poll(), 1_000)
    return () => {
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [hasPendingMeetingJob, refreshProcessing])

  function invalidateMinutes() {
    setArtifacts((current) => current.map((artifact) => (
      isMinutesArtifact(artifact) && artifact.state === 'ready'
        ? { ...artifact, state: 'stale' }
        : artifact
    )))
  }

  async function correct(segment: MeetingTranscriptSegment, request: SegmentCorrectionRequest) {
    const updated = await correctMeetingSegment(meetingId, segment.id, request)
    setSegments((current) => mergeTranscriptSegments(current, [updated]))
    invalidateMinutes()
    toast({ title: '文字订正已保存', description: request.edit_intent === 'asr_error' && request.contribute_to_accuracy ? '订正已记录，可用于后续识别改进。' : '本次仅保存人工文字版本。', type: 'success' })
  }

  async function revert(segment: MeetingTranscriptSegment) {
    const updated = await revertMeetingSegment(meetingId, segment.id, Math.max(0, segment.revision_no - 1), segment.revision_no)
    setSegments((current) => mergeTranscriptSegments(current, [updated]))
    invalidateMinutes()
    toast({ title: '已撤销本次修改', type: 'success' })
  }

  async function loadEarlierSegments() {
    if (transcriptPageBusy) return
    const earliestOrdinal = earliestTranscriptOrdinal(segments)
    const afterOrdinal = earlierTranscriptAfterOrdinal(earliestOrdinal)
    if (earliestOrdinal == null || afterOrdinal == null) {
      setHasEarlierSegments(false)
      return
    }
    setTranscriptPageBusy('earlier')
    try {
      const page = await getMeetingTranscript(meetingId, { afterOrdinal, limit: MEETING_TRANSCRIPT_PAGE_SIZE })
      const earlier = earlierSegmentsFromPage(page.items, earliestOrdinal)
      setSegments((current) => mergeTranscriptSegments(current, earlier))
      const nextEarliest = earlier[0]?.ordinal ?? earliestOrdinal
      setHasEarlierSegments(earlier.length > 0 && nextEarliest > 1)
    } catch (pageError) {
      toast({ title: '更早逐字稿加载失败', description: pageError instanceof Error ? pageError.message : '请稍后重试。', type: 'error' })
    } finally {
      setTranscriptPageBusy(null)
    }
  }

  async function loadLaterSegments() {
    if (transcriptPageBusy || nextTranscriptOrdinal == null) return
    setTranscriptPageBusy('later')
    try {
      const page = await getMeetingTranscript(meetingId, {
        afterOrdinal: nextTranscriptOrdinal,
        limit: MEETING_TRANSCRIPT_PAGE_SIZE,
      })
      setSegments((current) => mergeTranscriptSegments(current, page.items))
      setNextTranscriptOrdinal(page.next_ordinal ?? null)
    } catch (pageError) {
      toast({ title: '后续逐字稿加载失败', description: pageError instanceof Error ? pageError.message : '请稍后重试。', type: 'error' })
    } finally {
      setTranscriptPageBusy(null)
    }
  }

  async function rename(speaker: MeetingSpeakerTrack, displayName: string, saveVoiceprint: boolean) {
    const renamed = await renameMeetingSpeaker(meetingId, speaker.id, displayName, speaker.version)
    setSpeakers((current) => current.map((item) => item.id === renamed.id ? renamed : item))
    invalidateMinutes()
    toast({ title: '发言人名称已更新', type: 'success' })
    if (saveVoiceprint) {
      const profile = speaker.voice_profile_id
        ? { id: speaker.voice_profile_id }
        : await createVoiceprint(displayName)
      await enrollMeetingVoiceprint(meetingId, speaker.id, {
        consent_accepted: true,
        policy_version: 'meeting-voiceprint-v1',
        voice_profile_id: profile.id,
        source_track_id: speaker.id,
      })
      toast({ title: '声纹注册已提交', type: 'success' })
    }
  }

  async function renameTranscriptSpeaker(
    segment: MeetingTranscriptSegment,
    displayName: string,
    scope: MeetingSpeakerRenameScope,
  ) {
    const speaker = speakers.find((item) => item.id === segment.speaker_track_id)
    if (!speaker) throw new Error('当前发言人信息已更新，请刷新后重试。')
    const result = await renameMeetingSegmentSpeaker(meetingId, segment.id, {
      display_name: displayName,
      scope,
      expected_speaker_version: speaker.version,
    })
    setSpeakers((current) => {
      const updates = new Map(result.tracks.map((item) => [item.id, item]))
      const next = current.map((item) => updates.get(item.id) || item)
      const known = new Set(current.map((item) => item.id))
      return [...next, ...result.tracks.filter((item) => !known.has(item.id))]
    })
    setSegments((current) => mergeTranscriptSegments(current, [result.segment]))
    invalidateMinutes()
    toast({
      title: scope === 'segment' ? '当前段发言人已更新' : '同一发言人的名称已批量更新',
      description: scope === 'speaker' ? `本场共 ${result.affected_segment_count} 段发言已统一显示为“${displayName}”。` : undefined,
      type: 'success',
    })
  }

  async function mergeSpeakers(target: MeetingSpeakerTrack, sources: MeetingSpeakerTrack[]) {
    const sourceIds = sources.map((speaker) => speaker.id)
    const sourceIdSet = new Set(sourceIds)
    const expectedVersions = Object.fromEntries(
      [target, ...sources].map((speaker) => [speaker.id, speaker.version]),
    )
    const result = await mergeMeetingSpeakers(
      meetingId,
      target.id,
      sourceIds,
      expectedVersions,
    )
    const updatedTarget = result.tracks.find((speaker) => speaker.id === target.id) || target
    const targetName = updatedTarget.display_name || updatedTarget.anonymous_label
    setSpeakers((current) => current
      .filter((speaker) => !sourceIdSet.has(speaker.id))
      .map((speaker) => speaker.id === target.id ? updatedTarget : speaker))
    setSegments((current) => current.map((segment) => (
      segment.speaker_track_id && sourceIdSet.has(segment.speaker_track_id)
        ? { ...segment, speaker_track_id: target.id, speaker_display_name: targetName }
        : segment
    )))
    invalidateMinutes()
    toast({
      title: '发言人已合并',
      description: `本场共 ${result.segment_ids.length} 段发言已并入“${targetName}”。`,
      type: 'success',
    })
  }

  async function regenerate(artifact: MeetingArtifact) {
    if (!session) return
    setBusyKey(`regenerate:${artifact.id}`)
    try {
      const result = await regenerateMeetingArtifact(meetingId, artifact.id, session.settings_version)
      setArtifacts((current) => [result.artifact, ...current.filter((item) => item.id !== result.artifact.id)])
      setJobs((current) => [result.job, ...current.filter((item) => item.id !== result.job.id)])
      toast({ title: '新纪要版本已开始生成', description: `旧版本 v${artifact.version} 将继续保留。`, type: 'success' })
    } catch (regenerateError) {
      toast({ title: '重新生成失败', description: regenerateError instanceof Error ? regenerateError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  function openEvidence(segmentId: string) {
    const segment = segmentById.get(segmentId)
    if (!segment) {
      toast({ title: '证据片段暂不可用', description: '刷新会议详情后重试。', type: 'error' })
      return
    }
    setSeekToMs(segment.start_ms)
    setScrollToSegmentId(segmentId)
    setActiveTab('transcript')
  }

  function evidenceLabel(segmentId: string) {
    const segment = segmentById.get(segmentId)
    return segment ? formatMeetingTimestamp(segment.start_ms) : '片段不可用'
  }

  async function retry(job: MeetingJob) {
    setBusyKey(job.id)
    try {
      const updated = await retryMeetingJob(meetingId, job.id)
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item))
      toast({ title: '处理步骤已重新排队', type: 'success' })
    } catch (retryError) {
      toast({ title: '重试失败', description: retryError instanceof Error ? retryError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function createExport() {
    setBusyKey('export')
    try {
      const job = await createMeetingExport(meetingId, {
        format: exportFormat,
        content: exportContent,
        transcript_source: exportTranscriptSource,
        artifact_id: exportContent === 'minutes' ? minutesArtifact?.id : null,
        artifact_version: exportContent === 'minutes' ? minutesArtifact?.version : null,
      })
      setExports((current) => [job, ...current.filter((item) => item.id !== job.id)])
      toast({ title: job.state === 'ready' ? '导出文件已生成' : '导出任务已创建', type: 'success' })
    } catch (exportError) {
      toast({ title: '导出失败', description: exportError instanceof Error ? exportError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  function changeExportContent(value: string) {
    const next = value as 'transcript' | 'minutes'
    setExportContent(next)
    if (next === 'minutes' && !['markdown', 'json', 'docx'].includes(exportFormat)) setExportFormat('markdown')
  }

  async function downloadExport(item: MeetingExport) {
    setBusyKey(`download:${item.id}`)
    try {
      const ticket = await createMeetingExportTicket(meetingId, item.id)
      await downloadAuthenticatedFile(ticket.download_url, item.filename || undefined)
    } catch (downloadError) {
      toast({ title: '下载失败', description: downloadError instanceof Error ? downloadError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function manuallyRefreshExports() {
    setBusyKey('exports-refresh')
    try {
      await refreshExports()
    } catch (refreshError) {
      toast({ title: '导出状态刷新失败', description: refreshError instanceof Error ? refreshError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function retryExport(item: MeetingExport) {
    if (!item.job_id) return
    setBusyKey(`export-retry:${item.id}`)
    try {
      await retryMeetingJob(meetingId, item.job_id)
      setExports((current) => current.map((value) => value.id === item.id ? { ...value, state: 'queued', error_code: null } : value))
      toast({ title: '导出任务已重新排队', type: 'success' })
    } catch (retryError) {
      toast({ title: '导出重试失败', description: retryError instanceof Error ? retryError.message : '请稍后再试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  const stale = minutesArtifact?.state === 'stale'
  const duration = useMemo(() => session?.duration_ms ?? meetingDurationMs(session?.started_at, session?.stopped_at), [session])

  if (loading) return <PageShell variant="secondary"><div className="h-[70dvh] animate-pulse rounded-md bg-muted/60" /></PageShell>
  if (!session) return <PageShell variant="secondary"><PageSection><EmptyState icon={AlertTriangle} title="无法打开会议" description={error || '会议不存在或无权访问'} action={<Button asChild variant="secondary"><Link to="/meetings">返回会议列表</Link></Button>} /></PageSection></PageShell>

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={Sparkles}
        eyebrow="Meeting Review"
        title={session.title}
        description={`${formatMeetingDate(session.started_at || session.created_at)} · ${formatMeetingDuration(duration)} · ${speakers.length} 位发言人`}
        meta={<><StatusBadge tone={session.state === 'stopped' ? 'info' : 'neutral'}>{meetingStateLabels[session.state] || session.state}</StatusBadge><StatusBadge tone={meetingPostprocessStateTone(session.postprocess_state)}>{meetingPostprocessStateLabel(session.postprocess_state)}</StatusBadge></>}
        actions={<div className="flex gap-2"><Button asChild variant="secondary"><Link to="/meetings"><ArrowLeft />返回列表</Link></Button><Button type="button" variant="secondary" onClick={() => void load()}><RefreshCw />刷新</Button></div>}
      />

      {error ? <div role="alert" className="rounded-md bg-error-soft px-4 py-3 text-sm text-error">{error}</div> : null}
      {stale ? <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning-soft/55 px-4 py-3 text-sm leading-6 text-text"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" /><span><strong>纪要基于旧版逐字稿。</strong> 已保存的版本不会被静默覆盖，可在确认逐字稿后显式重新生成。</span></div> : null}

      <PageSection title="会议录音" description="点击逐字稿时间戳可定位到对应音频。">
        <MeetingAudioPlayer meetingId={meetingId} seekToMs={seekToMs} />
      </PageSection>

      {jobs.length ? (
        <Surface kind="muted" padding="sm">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            {jobs.map((job) => (
              <div key={job.id} className="flex min-w-0 items-center gap-2 rounded-md bg-white/70 px-3 py-2">
                {job.state === 'running' ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" /> : job.state === 'failed' ? <AlertTriangle className="h-4 w-4 shrink-0 text-error" /> : <CheckSquare className="h-4 w-4 shrink-0 text-success" />}
                <div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-text">{job.job_type}</p><p className="text-[11px] text-text-muted">{job.state}{job.progress != null ? ` · ${Math.round(job.progress * 100)}%` : ''}</p></div>
                {job.state === 'failed' && job.retryable ? <Button type="button" size="icon-xs" variant="ghost" className="max-sm:size-11" onClick={() => void retry(job)} disabled={busyKey === job.id} aria-label="重试处理步骤"><RotateCw /></Button> : null}
              </div>
            ))}
          </div>
        </Surface>
      ) : null}

      <PageSection contentClassName="p-0 sm:p-0">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="min-w-0">
          <TabsList variant="line" className="w-full justify-start overflow-x-auto px-4 pt-3 sm:px-5 [&_[data-slot=tabs-trigger]]:flex-none sm:justify-center sm:[&_[data-slot=tabs-trigger]]:flex-1">
            <TabsTrigger value="minutes"><Sparkles />智能纪要</TabsTrigger>
            <TabsTrigger value="transcript"><FileText />逐字稿</TabsTrigger>
            <TabsTrigger value="viewpoints"><MessageSquareQuote />发言人观点</TabsTrigger>
            <TabsTrigger value="actions"><CheckSquare />待办</TabsTrigger>
            <TabsTrigger value="files"><Download />文件与导出</TabsTrigger>
          </TabsList>
          <TabsContent value="minutes" className="p-4 sm:p-5">
            <MeetingArtifacts
              artifacts={artifacts}
              onEvidence={openEvidence}
              evidenceLabel={evidenceLabel}
              onRegenerate={(artifact) => void regenerate(artifact)}
              regenerating={busyKey.startsWith('regenerate:')}
            />
          </TabsContent>
          <TabsContent value="transcript" className="p-4 sm:p-5"><TranscriptTimeline segments={segments} speakers={speakers} editable correctionLearningEnabled={correctionLearningEnabled} hasEarlierSegments={hasEarlierSegments} hasLaterSegments={nextTranscriptOrdinal != null} loadingPage={transcriptPageBusy} onLoadEarlier={loadEarlierSegments} onLoadLater={loadLaterSegments} scrollToSegmentId={scrollToSegmentId} onSeek={setSeekToMs} onCorrect={correct} onRevert={revert} onRenameSpeaker={renameTranscriptSpeaker} /></TabsContent>
          <TabsContent value="viewpoints" className="p-4 sm:p-5">
            <div className="grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)]">
              <div><h3 className="mb-3 text-sm font-semibold text-text">发言人</h3><SpeakerPanel speakers={speakers} editable voiceprintEnabled onRename={rename} onMerge={mergeSpeakers} /></div>
              <div className="min-w-0 border-t border-border/70 pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
                <MeetingMinutesSection content={minutesContent} section="speaker_viewpoints" onEvidence={openEvidence} evidenceLabel={evidenceLabel} />
              </div>
            </div>
          </TabsContent>
          <TabsContent value="actions" className="p-4 sm:p-5"><MeetingMinutesSection content={minutesContent} section="action_items" onEvidence={openEvidence} evidenceLabel={evidenceLabel} /></TabsContent>
          <TabsContent value="files" className="p-4 sm:p-5">
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-text">创建导出</h3>
                <p className="mt-1 text-xs leading-5 text-text-muted">可选择当前显示文字或 ASR 原文；操作会写入会议审计记录。</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Select value={exportContent} onValueChange={changeExportContent}>
                    <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="transcript">逐字稿</SelectItem><SelectItem value="minutes">会议纪要</SelectItem></SelectContent>
                  </Select>
                  <Select value={exportTranscriptSource} onValueChange={(value) => setExportTranscriptSource(value as 'display' | 'asr')}>
                    <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="display">当前显示文字</SelectItem><SelectItem value="asr">ASR 原文</SelectItem></SelectContent>
                  </Select>
                  <Select value={exportFormat} onValueChange={(value) => setExportFormat(value as MeetingExportFormat)}>
                    <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="markdown">Markdown</SelectItem>
                      <SelectItem value="docx">Word（DOCX）</SelectItem>
                      {exportContent === 'transcript' ? <><SelectItem value="txt">TXT</SelectItem><SelectItem value="srt">SRT 字幕</SelectItem><SelectItem value="vtt">VTT 字幕</SelectItem></> : null}
                      <SelectItem value="json">结构化 JSON</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button type="button" onClick={() => void createExport()} disabled={busyKey === 'export' || (exportContent === 'minutes' && !minutesArtifact)}>
                    {busyKey === 'export' ? <Loader2 className="animate-spin" /> : <Download />}创建导出
                  </Button>
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-text">导出任务</h3>
                  <Button type="button" size="icon-sm" variant="ghost" onClick={() => void manuallyRefreshExports()} disabled={busyKey === 'exports-refresh'} aria-label="刷新导出状态">
                    <RefreshCw className={busyKey === 'exports-refresh' ? 'animate-spin' : ''} />
                  </Button>
                </div>
                {exports.length ? (
                  <div className="mt-3 divide-y divide-border/70">
                    {exports.map((item) => (
                      <div key={item.id} className="flex items-center gap-3 py-3">
                        <span className="premium-icon h-9 w-9 rounded-md">{item.format === 'json' ? <FileJson className="h-4 w-4" /> : <FileText className="h-4 w-4" />}</span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-text">{item.filename || `${item.format.toUpperCase()} · ${item.content === 'minutes' ? '会议纪要' : '逐字稿'}`}</p>
                          <p className="text-xs text-text-muted">{exportStateLabels[item.state] || item.state}{item.artifact_version ? ` · 纪要 v${item.artifact_version}` : ''}{item.error_code ? ` · ${item.error_code}` : ''}</p>
                        </div>
                        {item.state === 'queued' || item.state === 'running' ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" aria-label="导出生成中" /> : null}
                        {item.state === 'failed' && item.job_id ? (
                          <Button type="button" size="sm" variant="secondary" onClick={() => void retryExport(item)} disabled={busyKey === `export-retry:${item.id}`}>
                            {busyKey === `export-retry:${item.id}` ? <Loader2 className="animate-spin" /> : <RotateCw />}重试
                          </Button>
                        ) : null}
                        {item.state === 'ready' ? (
                          <Button type="button" size="sm" variant="secondary" onClick={() => void downloadExport(item)} disabled={busyKey === `download:${item.id}`}>
                            {busyKey === `download:${item.id}` ? <Loader2 className="animate-spin" /> : <Download />}下载
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-3 text-sm text-text-muted">本页尚未创建导出任务。</p>}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </PageSection>
    </PageShell>
  )
}
