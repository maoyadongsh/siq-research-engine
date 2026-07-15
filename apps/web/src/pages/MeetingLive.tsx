import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  CircleStop,
  Cloud,
  Cpu,
  Loader2,
  Mic2,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Wifi,
  WifiOff,
} from 'lucide-react'

import { EmptyState, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useToast } from '@/hooks/useToast'
import {
  correctMeetingSegment,
  createVoiceprint,
  decideVoiceprintMatch,
  enrollMeetingVoiceprint,
  finalizeMeeting,
  getMeetingCapabilities,
  getMeeting,
  getMeetingArtifacts,
  getMeetingModels,
  getMeetingSpeakers,
  getMeetingTranscript,
  pauseMeeting,
  renameMeetingSpeaker,
  resumeMeeting,
  revertMeetingSegment,
  startMeeting,
  stopMeeting,
  updateMeetingModelSelection,
} from '@/features/meeting-transcription/api'
import { MeetingArtifacts } from '@/features/meeting-transcription/components/MeetingArtifacts'
import { MeetingModelSelector } from '@/features/meeting-transcription/components/MeetingModelSelector'
import { NativeCapturePanel } from '@/features/meeting-transcription/components/NativeCapturePanel'
import { SpeakerPanel } from '@/features/meeting-transcription/components/SpeakerPanel'
import { TranscriptTimeline } from '@/features/meeting-transcription/components/TranscriptTimeline'
import { formatMeetingDuration, meetingDurationMs, meetingStateLabels } from '@/features/meeting-transcription/formatters'
import {
  earlierSegmentsFromPage,
  earlierTranscriptAfterOrdinal,
  earliestTranscriptOrdinal,
  initialTranscriptAfterOrdinal,
  latestTranscriptOrdinal,
  MEETING_TRANSCRIPT_PAGE_SIZE,
} from '@/features/meeting-transcription/transcriptPagination'
import { useMeetingRealtime } from '@/features/meeting-transcription/useMeetingRealtime'
import { useNativeMeetingCapture } from '@/features/meeting-transcription/useNativeMeetingCapture'
import type {
  MeetingCapabilities,
  MeetingModel,
  MeetingSession,
  MeetingSessionState,
  MeetingSpeakerTrack,
  MeetingTranscriptSegment,
  SegmentCorrectionRequest,
} from '@/features/meeting-transcription/types'

const DESKTOP_WORKSPACE_QUERY = '(min-width: 900px)'
const WIDE_WORKSPACE_QUERY = '(min-width: 1280px)'

function connectionLabel(status: ReturnType<typeof useMeetingRealtime>['state']['connectionStatus']) {
  if (status === 'connected') return '已连接'
  if (status === 'connecting') return '连接中'
  if (status === 'reconnecting') return '重连中'
  if (status === 'error') return '连接异常'
  return '未连接'
}

function meetingDisplayStatus(
  serverStatus: MeetingSessionState,
  nativeMode: boolean,
  nativeLifecycle?: string,
): MeetingSessionState {
  if (!nativeMode) return serverStatus
  if (nativeLifecycle === 'recording') return 'live'
  if (nativeLifecycle === 'paused') return 'paused'
  if (nativeLifecycle === 'interrupted' || nativeLifecycle === 'error') return 'interrupted'
  if (nativeLifecycle === 'stopping') return 'stopping'
  if (nativeLifecycle === 'stopped') {
    return ['stopped', 'archived'].includes(serverStatus) ? serverStatus : 'stopping'
  }
  return serverStatus
}

export default function MeetingLive() {
  const { meetingId = '' } = useParams()
  const navigate = useNavigate()
  const { toast } = useToast()
  const realtime = useMeetingRealtime(meetingId)
  const nativeCapture = useNativeMeetingCapture(meetingId)
  const selectNativeCapture = nativeCapture.select
  const hydrateRealtime = realtime.hydrate
  const [session, setSession] = useState<MeetingSession | null>(null)
  const [models, setModels] = useState<MeetingModel[]>([])
  const [modelRef, setModelRef] = useState('auto')
  const [correctionLearningEnabled, setCorrectionLearningEnabled] = useState(false)
  const [capabilities, setCapabilities] = useState<MeetingCapabilities | null>(null)
  const [hasEarlierSegments, setHasEarlierSegments] = useState(false)
  const [nextTranscriptOrdinal, setNextTranscriptOrdinal] = useState<number | null>(null)
  const [transcriptPageBusy, setTranscriptPageBusy] = useState<'earlier' | 'later' | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [stopOpen, setStopOpen] = useState(false)
  const [now, setNow] = useState(0)
  const nativeTranscriptRefreshPending = useRef(false)
  const reconnectOnLoadAttempted = useRef(false)
  const connectRef = useRef<() => Promise<void>>(async () => undefined)
  const desktopWorkspace = useMediaQuery(DESKTOP_WORKSPACE_QUERY)
  const wideWorkspace = useMediaQuery(WIDE_WORKSPACE_QUERY)

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!meetingId) return
    setLoading(true)
    try {
      const meeting = await getMeeting(meetingId, signal)
      const [transcript, speakers, artifacts, availableModels, capabilities] = await Promise.all([
        getMeetingTranscript(meetingId, {
          afterOrdinal: initialTranscriptAfterOrdinal(meeting.last_segment_ordinal),
          limit: MEETING_TRANSCRIPT_PAGE_SIZE,
        }, signal),
        getMeetingSpeakers(meetingId, signal),
        getMeetingArtifacts(meetingId, signal),
        meeting.ai_enabled ? getMeetingModels(signal).catch(() => []) : Promise.resolve([]),
        getMeetingCapabilities(signal).catch(() => null),
      ])
      setSession(meeting)
      setModels(availableModels)
      setModelRef(meeting.selection_mode === 'auto' ? 'auto' : meeting.requested_model_ref || 'auto')
      setCorrectionLearningEnabled(Boolean(capabilities?.correction_learning?.available))
      setCapabilities(capabilities)
      setHasEarlierSegments((transcript.items[0]?.ordinal ?? 1) > 1)
      setNextTranscriptOrdinal(transcript.next_ordinal ?? null)
      hydrateRealtime({ segments: transcript.items || [], speakers, artifacts, sessionState: meeting.state })
      setError('')
    } catch (loadError) {
      if (!signal?.aborted) setError(loadError instanceof Error ? loadError.message : '会议工作台加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [hydrateRealtime, meetingId])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void load(controller.signal)
    })
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (!capabilities) return
    void selectNativeCapture(capabilities)
  }, [capabilities, selectNativeCapture])

  useEffect(() => {
    queueMicrotask(() => setNow(Date.now()))
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const warnBeforeLeave = (event: BeforeUnloadEvent) => {
      const nativeActive = nativeCapture.state.mode === 'native'
        && nativeCapture.state.bound
        && ['recording', 'paused', 'interrupted', 'stopping'].includes(nativeCapture.state.status?.state ?? '')
      if (nativeCapture.state.mode === 'native' ? !nativeActive : !['live', 'connecting', 'reconnecting'].includes(realtime.state.sessionState)) return
      event.preventDefault()
    }
    window.addEventListener('beforeunload', warnBeforeLeave)
    return () => window.removeEventListener('beforeunload', warnBeforeLeave)
  }, [nativeCapture.state.bound, nativeCapture.state.mode, nativeCapture.state.status?.state, realtime.state.sessionState])

  const nativeStableOrdinal = nativeCapture.state.checkpoints?.realtime.stableOrdinal ?? 0
  useEffect(() => {
    if (nativeCapture.state.mode !== 'native' || nativeStableOrdinal <= 0 || nativeTranscriptRefreshPending.current) return
    const latestOrdinal = latestTranscriptOrdinal(realtime.state.segments) ?? 0
    if (nativeStableOrdinal <= latestOrdinal) return
    nativeTranscriptRefreshPending.current = true
    void getMeetingTranscript(meetingId, {
      afterOrdinal: latestOrdinal,
      limit: MEETING_TRANSCRIPT_PAGE_SIZE,
    }).then((page) => {
      hydrateRealtime({ segments: page.items })
      setNextTranscriptOrdinal(page.next_ordinal ?? null)
    }).catch(() => undefined).finally(() => {
      nativeTranscriptRefreshPending.current = false
    })
  }, [hydrateRealtime, meetingId, nativeCapture.state.mode, nativeStableOrdinal, realtime.state.segments])

  useEffect(() => {
    if (
      nativeCapture.state.mode !== 'native'
      || nativeCapture.state.status?.state !== 'stopped'
      || ['stopped', 'archived'].includes(session?.state ?? '')
    ) return undefined
    const refreshSession = () => {
      void getMeeting(meetingId).then((updated) => {
        setSession(updated)
        hydrateRealtime({ sessionState: updated.state })
      }).catch(() => undefined)
    }
    refreshSession()
    const timer = window.setInterval(refreshSession, 2_000)
    return () => window.clearInterval(timer)
  }, [hydrateRealtime, meetingId, nativeCapture.state.mode, nativeCapture.state.status?.state, session?.state])

  async function connect() {
    if (!session) return
    setBusy(true)
    setError('')
    let capturePrepared = false
    try {
      const nativeSelected = await nativeCapture.select(capabilities)
      if (nativeSelected) {
        let current = session
        if (session.state === 'draft') {
          current = await startMeeting(meetingId)
          setSession(current)
          realtime.hydrate({ sessionState: current.state })
        }
        await nativeCapture.start(current.stream_epoch)
        return
      }
      let storedDevice = ''
      try { storedDevice = sessionStorage.getItem(`siq-meeting-device:${meetingId}`) || '' } catch { /* Ignore unavailable storage. */ }
      await realtime.prepareCapture({
        deviceId: storedDevice || undefined,
        audioSource: session.audio_source,
      })
      capturePrepared = true
      let current = session
      if (session.state === 'draft') {
        current = await startMeeting(meetingId)
        setSession(current)
        realtime.hydrate({ sessionState: current.state })
      }
      await realtime.connect({
        streamEpoch: current.stream_epoch,
        lastAckedSequence: current.last_audio_sequence,
        lastServerCursor: realtime.state.lastCursor,
        deviceId: storedDevice || undefined,
        audioSource: current.audio_source,
      })
    } catch (connectError) {
      if (capturePrepared) await realtime.disconnect().catch(() => undefined)
      setError(connectError instanceof Error ? connectError.message : '开始会议失败')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    connectRef.current = connect
  })

  useEffect(() => {
    if (
      reconnectOnLoadAttempted.current
      || session?.state !== 'reconnecting'
      || nativeCapture.state.mode !== 'web'
      || !['offline', 'error'].includes(realtime.state.connectionStatus)
    ) return
    reconnectOnLoadAttempted.current = true
    void connectRef.current()
  }, [nativeCapture.state.mode, realtime.state.connectionStatus, session?.state])

  async function pause() {
    if (!session) return
    setBusy(true)
    try {
      if (nativeCapture.state.mode === 'native') {
        await nativeCapture.pause()
        if (['live', 'reconnecting'].includes(session.state)) {
          const updated = await pauseMeeting(meetingId, session.version)
          setSession(updated)
          realtime.hydrate({ sessionState: updated.state })
        }
      } else {
        await realtime.pause()
        const updated = await pauseMeeting(meetingId, session.version)
        setSession(updated)
        realtime.hydrate({ sessionState: updated.state })
      }
    } catch (pauseError) {
      setError(pauseError instanceof Error ? pauseError.message : '暂停失败')
    } finally {
      setBusy(false)
    }
  }

  async function resume() {
    if (!session) return
    setBusy(true)
    try {
      if (nativeCapture.state.mode === 'native') {
        if (nativeCapture.state.bound) {
          if (['paused', 'interrupted'].includes(session.state)) {
            const updated = await resumeMeeting(meetingId, session.version)
            setSession(updated)
            realtime.hydrate({ sessionState: updated.state })
          }
          await nativeCapture.resume()
        } else {
          await connect()
        }
      } else {
        const updated = await resumeMeeting(meetingId, session.version)
        setSession(updated)
        realtime.hydrate({ sessionState: updated.state })
        if (realtime.state.connectionStatus === 'connected') await realtime.resume()
        else await connect()
      }
    } catch (resumeError) {
      setError(resumeError instanceof Error ? resumeError.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }

  async function finish() {
    if (!session) return
    setBusy(true)
    setError('')
    try {
      if (nativeCapture.state.mode === 'native') {
        await nativeCapture.stop()
        setStopOpen(false)
        toast({
          title: '本地录音已封存',
          description: '录音可立即回放，剩余批次会继续上传并由服务端自动完成转写。',
          type: 'success',
        })
        return
      }
      await realtime.stop()
      let stopped = await stopMeeting(meetingId, session.version)
      for (let attempt = 0; stopped.state === 'stopping' && attempt < 15; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 400))
        // A disconnected gateway releases its producer lease. Repeating the
        // idempotent stop lets the REST fallback pack durable PCM immediately.
        stopped = await stopMeeting(meetingId, stopped.version)
      }
      setSession(stopped)
      realtime.hydrate({ sessionState: stopped.state })
      if (stopped.state !== 'stopped' && stopped.state !== 'archived') {
        toast({ title: '录音已停止，正在完成收尾', description: '最终音频仍在后台合并，可稍后在会议详情中继续生成纪要。', type: 'info' })
        navigate(`/meetings/${encodeURIComponent(meetingId)}`)
        return
      }
      await finalizeMeeting(meetingId)
      toast({ title: '录音已结束', description: '最终转写、说话人整理和纪要会在后台继续处理。', type: 'success' })
      navigate(`/meetings/${encodeURIComponent(meetingId)}`)
    } catch (stopError) {
      setError(stopError instanceof Error ? stopError.message : '结束会议失败')
      setStopOpen(false)
    } finally {
      setBusy(false)
    }
  }

  async function correct(segment: MeetingTranscriptSegment, request: SegmentCorrectionRequest) {
    const updated = await correctMeetingSegment(meetingId, segment.id, request)
    realtime.hydrate({ segments: [updated] })
  }

  async function revert(segment: MeetingTranscriptSegment) {
    const updated = await revertMeetingSegment(meetingId, segment.id, Math.max(0, segment.revision_no - 1), segment.revision_no)
    realtime.hydrate({ segments: [updated] })
  }

  async function loadEarlierSegments() {
    if (transcriptPageBusy) return
    const earliestOrdinal = earliestTranscriptOrdinal(realtime.state.segments)
    const afterOrdinal = earlierTranscriptAfterOrdinal(earliestOrdinal)
    if (earliestOrdinal == null || afterOrdinal == null) {
      setHasEarlierSegments(false)
      return
    }
    setTranscriptPageBusy('earlier')
    try {
      const page = await getMeetingTranscript(meetingId, { afterOrdinal, limit: MEETING_TRANSCRIPT_PAGE_SIZE })
      const earlier = earlierSegmentsFromPage(page.items, earliestOrdinal)
      realtime.hydrate({ segments: earlier })
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
      realtime.hydrate({ segments: page.items })
      setNextTranscriptOrdinal(page.next_ordinal ?? null)
    } catch (pageError) {
      toast({ title: '后续逐字稿加载失败', description: pageError instanceof Error ? pageError.message : '请稍后重试。', type: 'error' })
    } finally {
      setTranscriptPageBusy(null)
    }
  }

  async function rename(speaker: MeetingSpeakerTrack, displayName: string, saveVoiceprint: boolean) {
    const renamed = await renameMeetingSpeaker(meetingId, speaker.id, displayName, speaker.version)
    realtime.hydrate({ speakers: realtime.state.speakers.map((item) => item.id === renamed.id ? renamed : item) })
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
      toast({ title: '声纹注册已提交', description: '系统会使用多个清晰、非重叠片段完成质量检查。', type: 'success' })
    }
  }

  async function decideMatch(speaker: MeetingSpeakerTrack, decision: 'confirm' | 'reject' | 'undo') {
    const matchId = speaker.voiceprint_match?.id
    if (!matchId) return
    await decideVoiceprintMatch(meetingId, matchId, decision)
    await load()
  }

  async function changeModel(value: string) {
    if (!session) return
    const selected = models.find((model) => model.model_ref === value)
    let cloudConfirmed = false
    if (selected?.locality === 'cloud') {
      cloudConfirmed = window.confirm('逐字稿文本将发送至所选云端模型；音频和声纹不会发送。确认切换吗？')
      if (!cloudConfirmed) return
    }
    setBusy(true)
    try {
      const setting = await updateMeetingModelSelection(meetingId, {
        mode: value === 'auto' ? 'auto' : 'pinned',
        model_ref: value === 'auto' ? null : value,
        fallback_policy: 'disabled',
        expected_settings_version: session.settings_version,
        cloud_data_boundary_confirmed: cloudConfirmed || undefined,
      })
      setSession((current) => current ? {
        ...current,
        settings_version: setting.settings_version,
        selection_mode: setting.selection_mode,
        requested_model_ref: setting.requested_model_ref || null,
        fallback_policy: setting.fallback_policy,
      } : current)
      setModelRef(value)
      toast({ title: '模型选择已更新', description: '已运行的任务保留原模型，新边界后的任务使用新选择。', type: 'success' })
    } catch (modelError) {
      setError(modelError instanceof Error ? modelError.message : '模型切换失败')
    } finally {
      setBusy(false)
    }
  }

  const duration = useMemo(() => meetingDurationMs(session?.started_at, session?.stopped_at, now), [now, session?.started_at, session?.stopped_at])
  const serverStatus = realtime.state.sessionState === 'draft' && session ? session.state : realtime.state.sessionState
  const nativeMode = nativeCapture.state.mode === 'native'
  const nativeLifecycle = nativeCapture.state.status?.state
  const webConnectionInterrupted = !nativeMode
    && serverStatus === 'reconnecting'
    && ['error', 'offline'].includes(realtime.state.connectionStatus)
  const status = webConnectionInterrupted
    ? 'interrupted'
    : meetingDisplayStatus(serverStatus, nativeMode, nativeLifecycle)
  const statusLabel = nativeMode && nativeLifecycle === 'recording'
    ? '录音中'
    : meetingStateLabels[status] || status
  const active = nativeMode
    ? nativeCapture.state.bound && nativeLifecycle === 'recording'
    : ['live', 'connecting', 'reconnecting'].includes(status)
  const captureConnected = nativeMode
    ? nativeCapture.state.bound && ['recording', 'paused', 'interrupted', 'stopping', 'stopped'].includes(nativeLifecycle ?? '')
    : realtime.state.connectionStatus === 'connected'
  const nativeNeedsStart = nativeMode && !nativeCapture.state.bound
  const webNeedsReconnect = !nativeMode
    && ['live', 'reconnecting', 'interrupted'].includes(serverStatus)
    && realtime.state.connectionStatus !== 'connected'
  const selectedModel = models.find((model) => model.model_ref === modelRef)
  const savedSegmentCount = Math.max(session?.last_segment_ordinal ?? 0, latestTranscriptOrdinal(realtime.state.segments) ?? 0)

  if (loading) return <PageShell variant="secondary"><div className="h-[70dvh] animate-pulse rounded-md bg-muted/60" /></PageShell>
  if (error && !session) return <PageShell variant="secondary"><Surface kind="panel"><EmptyState icon={AlertTriangle} title="无法打开会议工作台" description={error} action={<Button asChild variant="secondary"><Link to="/meetings">返回会议列表</Link></Button>} /></Surface></PageShell>
  if (!session) return null

  const speakerWorkspace = (
    <Surface kind="panel" className="min-w-0 self-start">
      <h2 className="mb-3 text-sm font-semibold text-text">发言人</h2>
      <SpeakerPanel
        speakers={realtime.state.speakers}
        editable
        voiceprintEnabled={session.voiceprint_enabled}
        onRename={rename}
        onMatchDecision={decideMatch}
      />
    </Surface>
  )
  const transcriptWorkspace = (
    <Surface kind="panel" className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-text">{nativeMode ? '会议逐字稿' : '实时逐字稿'}</h2>
        <span className="text-xs tabular-nums text-text-muted">{savedSegmentCount} 句已保存 · 已加载 {realtime.state.segments.length}</span>
      </div>
      <TranscriptTimeline
        segments={realtime.state.segments}
        partials={realtime.state.partials}
        speakers={realtime.state.speakers}
        live
        editable
        correctionLearningEnabled={correctionLearningEnabled}
        hasEarlierSegments={hasEarlierSegments}
        hasLaterSegments={nextTranscriptOrdinal != null}
        loadingPage={transcriptPageBusy}
        onLoadEarlier={loadEarlierSegments}
        onLoadLater={loadLaterSegments}
        onCorrect={correct}
        onRevert={revert}
      />
    </Surface>
  )
  const aiWorkspace = (
    <Surface kind="panel" className="min-w-0 self-start">
      <div className="mb-4">
        <MeetingModelSelector
          models={models}
          value={modelRef}
          onChange={(value) => void changeModel(value)}
          disabled={!session.ai_enabled || busy}
        />
      </div>
      <MeetingArtifacts artifacts={realtime.state.rollingArtifacts} compact />
    </Surface>
  )

  return (
    <PageShell variant="secondary" className="space-y-4 pb-4">
      <Surface kind="panel" padding="none" className="sticky top-0 z-20 overflow-hidden">
        <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Button asChild variant="ghost" size="icon-sm" className="max-sm:size-11"><Link to="/meetings" aria-label="返回会议列表"><ArrowLeft /></Link></Button>
            <div className="min-w-0"><h1 className="truncate text-base font-semibold text-text">{session.title}</h1><div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-muted"><StatusBadge tone={active ? 'success' : status === 'paused' ? 'warning' : 'neutral'} icon={active ? Radio : undefined}>{statusLabel}</StatusBadge><span className="font-mono tabular-nums">{formatMeetingDuration(duration)}</span><span className="inline-flex items-center gap-1">{captureConnected ? <Wifi className="h-3.5 w-3.5 text-success" /> : <WifiOff className="h-3.5 w-3.5" />}{nativeMode ? 'iPhone 原生采集' : connectionLabel(realtime.state.connectionStatus)}</span>{!nativeMode && realtime.state.asrLatencyMs != null ? <span>延迟 {(realtime.state.asrLatencyMs / 1000).toFixed(1)}s</span> : null}</div></div>
          </div>
          <div className="flex flex-wrap gap-2">
            {status === 'draft' || nativeNeedsStart || webNeedsReconnect ? <Button type="button" onClick={() => void connect()} disabled={busy || nativeCapture.state.busy}>{busy ? <Loader2 className="animate-spin" /> : <Mic2 />}{status === 'draft' ? '开始会议' : nativeMode && nativeLifecycle ? '载入原生采集' : nativeMode ? '启动原生采集' : '重新连接'}</Button> : null}
            {active && captureConnected ? <Button type="button" variant="secondary" onClick={() => void pause()} disabled={busy || nativeCapture.state.busy}><Pause />暂停</Button> : null}
            {(nativeMode && nativeCapture.state.bound && ['paused', 'interrupted'].includes(nativeLifecycle ?? '')) || (!nativeMode && ['paused', 'interrupted'].includes(status)) ? <Button type="button" onClick={() => void resume()} disabled={busy || nativeCapture.state.busy}><Play />恢复</Button> : null}
            {!['stopped', 'stopping', 'archived'].includes(status) && nativeLifecycle !== 'stopped' ? <Button type="button" variant="danger" onClick={() => setStopOpen(true)} disabled={busy || nativeCapture.state.busy}><CircleStop />结束</Button> : null}
          </div>
        </div>
      </Surface>

      {error || nativeCapture.state.error || (!nativeMode && realtime.streamError) ? <div role="alert" className="flex items-start gap-2 rounded-md border border-error/25 bg-error-soft px-4 py-3 text-sm text-error"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error || nativeCapture.state.error || realtime.streamError}<Button type="button" size="icon-xs" variant="ghost" className="ml-auto max-sm:size-11" onClick={() => void (nativeMode ? nativeCapture.state.bound ? nativeCapture.retryUploads() : connect() : status === 'interrupted' ? resume() : connect())} aria-label="重试"><RefreshCw /></Button></div> : null}
      {realtime.state.pipelineWarnings.some((warning) => !warning.recovered) ? <div className="rounded-md border border-warning/30 bg-warning-soft/55 px-4 py-3 text-sm text-text">可选处理能力暂时降级，录音与稳定逐字稿仍会继续保存。</div> : null}

      {nativeMode ? (
        <NativeCapturePanel
          state={nativeCapture.state}
          canCleanup={nativeCapture.canCleanup}
          onRetryUploads={() => void nativeCapture.retryUploads()}
          onTogglePlayback={() => void nativeCapture.togglePlayback()}
          onSeekPlayback={(positionMs) => void nativeCapture.seekPlayback(positionMs)}
          onDiscardLocal={() => void nativeCapture.discardLocal()}
        />
      ) : null}

      {wideWorkspace ? (
        <div className="grid grid-cols-[220px_minmax(0,1fr)_340px] gap-4">
          {speakerWorkspace}
          {transcriptWorkspace}
          {aiWorkspace}
        </div>
      ) : desktopWorkspace ? (
        <div className="grid grid-cols-[220px_minmax(0,1fr)] gap-4">
          {speakerWorkspace}
          <Tabs defaultValue="transcript" className="min-w-0">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="transcript">逐字稿</TabsTrigger>
              <TabsTrigger value="minutes">AI 要点</TabsTrigger>
            </TabsList>
            <TabsContent value="transcript" className="mt-2">{transcriptWorkspace}</TabsContent>
            <TabsContent value="minutes" className="mt-2">{aiWorkspace}</TabsContent>
          </Tabs>
        </div>
      ) : (
        <Tabs defaultValue="transcript" className="min-w-0">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="transcript">逐字稿</TabsTrigger>
            <TabsTrigger value="minutes">AI 要点</TabsTrigger>
            <TabsTrigger value="speakers">发言人</TabsTrigger>
          </TabsList>
          <TabsContent value="transcript" className="mt-2">{transcriptWorkspace}</TabsContent>
          <TabsContent value="minutes" className="mt-2">{aiWorkspace}</TabsContent>
          <TabsContent value="speakers" className="mt-2">{speakerWorkspace}</TabsContent>
        </Tabs>
      )}

      {!nativeMode ? <Surface kind="muted" padding="sm" className="grid gap-3 text-xs text-text-muted sm:grid-cols-4">
        <div><p>音频输入</p><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full origin-left bg-success transition-transform" style={{ transform: `scaleX(${realtime.inputLevel})` }} /></div></div>
        <div><p>网络状态</p><p className="mt-1 font-medium text-text">{connectionLabel(realtime.state.connectionStatus)}</p></div>
        <div><p>ASR 状态</p><p className="mt-1 font-medium text-text">{active ? '实时识别' : status === 'paused' ? '已暂停' : '等待开始'}</p></div>
        <div><p>AI 模型</p><p className="mt-1 truncate font-medium text-text" title={selectedModel?.label}>{session.ai_enabled ? selectedModel?.label || (modelRef === 'auto' ? '自动选择' : modelRef) : '已关闭'} {selectedModel?.locality === 'cloud' ? <Cloud className="inline h-3.5 w-3.5" /> : session.ai_enabled ? <Cpu className="inline h-3.5 w-3.5" /> : null}</p></div>
      </Surface> : null}

      <Dialog open={stopOpen} onOpenChange={(open) => { if (!busy) setStopOpen(open) }}>
        <DialogContent className="bg-card text-text sm:max-w-md">
          <DialogHeader><DialogTitle>结束本场会议？</DialogTitle><DialogDescription className="leading-6">{nativeMode ? 'iPhone 会先封存本地录音并立即开放回放，剩余批次会继续上传；服务端完整校验后自动生成最终转写与纪要。' : '系统会发送最后音频分片、关闭采集，并在后台继续最终转写、说话人整理和纪要生成。'}</DialogDescription></DialogHeader>
          <DialogFooter><Button type="button" variant="secondary" onClick={() => setStopOpen(false)} disabled={busy}>继续会议</Button><Button type="button" variant="danger" onClick={() => void finish()} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <CircleStop />}确认结束</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </PageShell>
  )
}
