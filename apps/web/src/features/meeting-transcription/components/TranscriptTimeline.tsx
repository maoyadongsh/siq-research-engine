import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useVirtualizer, type Range } from '@tanstack/react-virtual'
import { BookOpenText, Check, ChevronDown, ChevronUp, CornerDownLeft, Loader2, Pencil, RotateCcw, Save, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import { formatMeetingTimestamp, segmentDisplayText } from '../formatters'
import { latestStableAnnouncement } from '../eventReducer'
import {
  TRANSCRIPT_ESTIMATED_SEGMENT_HEIGHT,
  TRANSCRIPT_OVERSCAN,
  transcriptRangeExtractor,
} from '../transcriptVirtualization'
import type {
  MeetingEditIntent,
  MeetingPartialTranscript,
  MeetingSpeakerTrack,
  MeetingTranscriptSegment,
  SegmentCorrectionRequest,
} from '../types'

interface TranscriptTimelineProps {
  segments: MeetingTranscriptSegment[]
  partials?: Record<string, MeetingPartialTranscript>
  speakers?: MeetingSpeakerTrack[]
  live?: boolean
  editable?: boolean
  correctionLearningEnabled?: boolean
  hasEarlierSegments?: boolean
  hasLaterSegments?: boolean
  loadingPage?: 'earlier' | 'later' | null
  onLoadEarlier?: () => Promise<void> | void
  onLoadLater?: () => Promise<void> | void
  scrollToSegmentId?: string | null
  onSeek?: (offsetMs: number) => void
  onCorrect?: (segment: MeetingTranscriptSegment, request: SegmentCorrectionRequest) => Promise<void>
  onRevert?: (segment: MeetingTranscriptSegment) => Promise<void>
}

function DiffPreview({ segment }: { segment: MeetingTranscriptSegment }) {
  if (!segment.diff_ops?.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-x-1 text-xs leading-5" aria-label="修改差异">
      {segment.diff_ops.map((operation, index) => {
        const text = operation.text || operation.new_text || operation.old_text || ''
        if (!text) return null
        if (operation.op === 'delete') return <del key={index} className="rounded bg-error-soft px-1 text-error">{text}</del>
        if (operation.op === 'insert') return <ins key={index} className="rounded bg-success-soft px-1 text-success no-underline">{text}</ins>
        if (operation.op === 'replace') {
          return (
            <span key={index}>
              <del className="rounded bg-error-soft px-1 text-error">{operation.old_text}</del>
              <ins className="ml-1 rounded bg-success-soft px-1 text-success no-underline">{operation.new_text}</ins>
            </span>
          )
        }
        return <span key={index} className="text-text-muted">{text}</span>
      })}
    </div>
  )
}

function stateLabel(segment: MeetingTranscriptSegment) {
  if (segment.human_locked || segment.text_state === 'human_verified') return '已确认'
  if (segment.text_state === 'optimized') return '已优化'
  if (segment.text_state === 'review_required') return '待复核'
  return ''
}

export function TranscriptTimeline({
  segments,
  partials = {},
  speakers = [],
  live = false,
  editable = false,
  correctionLearningEnabled = false,
  hasEarlierSegments = false,
  hasLaterSegments = false,
  loadingPage = null,
  onLoadEarlier,
  onLoadLater,
  scrollToSegmentId,
  onSeek,
  onCorrect,
  onRevert,
}: TranscriptTimelineProps) {
  const [editingId, setEditingId] = useState('')
  const [draft, setDraft] = useState('')
  const [intent, setIntent] = useState<MeetingEditIntent>('asr_error')
  const [contribute, setContribute] = useState(false)
  const [addTerm, setAddTerm] = useState(false)
  const [term, setTerm] = useState('')
  const [misrecognition, setMisrecognition] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [following, setFollowing] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)
  const speakerById = useMemo(() => new Map(speakers.map((speaker) => [speaker.id, speaker])), [speakers])
  const partialList = useMemo(() => Object.values(partials), [partials])
  const announcement = latestStableAnnouncement(segments)
  const editingIndex = useMemo(() => segments.findIndex((segment) => segment.id === editingId), [editingId, segments])
  const rangeExtractor = useCallback((range: Range) => transcriptRangeExtractor(range, editingIndex), [editingIndex])
  const getItemKey = useCallback((index: number) => segments[index]?.id ?? index, [segments])
  // TanStack Virtual intentionally exposes mutable measurement methods.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => TRANSCRIPT_ESTIMATED_SEGMENT_HEIGHT,
    getItemKey,
    overscan: TRANSCRIPT_OVERSCAN,
    rangeExtractor,
  })
  const virtualSegments = virtualizer.getVirtualItems()

  useEffect(() => {
    if (!live || !following) return
    const frame = window.requestAnimationFrame(() => {
      if (segments.length) virtualizer.scrollToIndex(segments.length - 1, { align: 'end' })
      const container = containerRef.current
      if (container) container.scrollTop = container.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [announcement, following, live, partialList.length, segments.length, virtualizer])

  useEffect(() => {
    if (!scrollToSegmentId) return
    const index = segments.findIndex((segment) => segment.id === scrollToSegmentId)
    if (index < 0) return
    const frame = window.requestAnimationFrame(() => virtualizer.scrollToIndex(index, { align: 'center' }))
    return () => window.cancelAnimationFrame(frame)
  }, [scrollToSegmentId, segments, virtualizer])

  function beginEdit(segment: MeetingTranscriptSegment) {
    setEditingId(segment.id)
    setDraft(segmentDisplayText(segment))
    setMisrecognition(segment.asr_final_text || segment.raw_text)
    setIntent('asr_error')
    setContribute(false)
    setAddTerm(false)
    setTerm('')
    setError('')
  }

  async function save(segment: MeetingTranscriptSegment) {
    const text = draft.trim()
    if (!text || text === segmentDisplayText(segment)) {
      setError(text ? '文字没有变化' : '订正文字不能为空')
      return
    }
    if (!onCorrect) return
    setBusy(true)
    setError('')
    try {
      await onCorrect(segment, {
        text,
        expected_revision: segment.revision_no,
        edit_intent: intent,
        contribute_to_accuracy: correctionLearningEnabled && intent === 'asr_error' && contribute,
        candidate_terms: correctionLearningEnabled && contribute && addTerm && term.trim() ? [{
          canonical_term: term.trim(),
          misrecognition: misrecognition.trim() || null,
          promote_now: false,
        }] : [],
      })
      setEditingId('')
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '保存订正失败')
    } finally {
      setBusy(false)
    }
  }

  function handleScroll() {
    if (!live) return
    const container = containerRef.current
    if (!container) return
    const nextFollowing = container.scrollHeight - container.scrollTop - container.clientHeight < 80
    setFollowing((current) => current === nextFollowing ? current : nextFollowing)
  }

  return (
    <div className="relative min-h-0">
      <div aria-live="polite" aria-atomic="true" className="sr-only">{announcement}</div>
      {hasEarlierSegments ? (
        <div className="mb-2 flex justify-center">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="max-sm:h-11"
            disabled={loadingPage != null}
            onClick={() => void onLoadEarlier?.()}
          >
            {loadingPage === 'earlier' ? <Loader2 className="animate-spin" /> : <ChevronUp />}
            {loadingPage === 'earlier' ? '正在加载' : '加载更早段落'}
          </Button>
        </div>
      ) : null}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        role="region"
        aria-label="逐字稿时间线"
        data-testid="transcript-scroll"
        className={cn(
          'min-h-0 max-h-[min(70dvh,52rem)] overflow-y-auto overscroll-contain',
          live && 'max-h-[min(calc(100dvh-18rem),52rem)]',
        )}
      >
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualSegments.map((virtualRow) => {
          const segment = segments[virtualRow.index]
          if (!segment) return null
          const speaker = segment.speaker_track_id ? speakerById.get(segment.speaker_track_id) : undefined
          const label = segment.speaker_display_name || speaker?.display_name || speaker?.anonymous_label || '发言人'
          const status = stateLabel(segment)
          const editing = editingId === segment.id
          return (
            <article
              id={`meeting-segment-${segment.id}`}
              key={virtualRow.key}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              data-transcript-segment={segment.id}
              className="group absolute left-0 top-0 grid min-h-20 w-full grid-cols-[4.5rem_minmax(0,1fr)] gap-3 border-b border-border/70 px-1 py-4"
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <div>
                <button
                  type="button"
                  className="min-h-11 rounded-md px-1 font-mono text-xs tabular-nums text-primary hover:bg-primary/5 focus-visible:ring-3 focus-visible:ring-ring/50"
                  onClick={() => onSeek?.(segment.start_ms)}
                  aria-label={`跳转到 ${formatMeetingTimestamp(segment.start_ms)}`}
                >
                  {formatMeetingTimestamp(segment.start_ms)}
                </button>
              </div>
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-semibold text-text">{label}</span>
                  {status ? (
                    <span className={cn(
                      'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
                      status === '待复核' ? 'bg-warning-soft text-warning' : 'bg-success-soft text-success',
                    )}>
                      {status === '已确认' ? <Check className="mr-1 inline h-3 w-3" /> : null}{status}
                    </span>
                  ) : null}
                  {editable && !editing ? (
                    <span className="ml-auto flex shrink-0 items-center gap-1">
                      {segment.human_locked && segment.revision_no > 1 && onRevert ? (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => void onRevert(segment)} aria-label="撤销本次修改">
                                <RotateCcw />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>撤销本次修改</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : null}
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => beginEdit(segment)} aria-label="修改文字">
                              <Pencil />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>修改文字</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </span>
                  ) : null}
                </div>
                {editing ? (
                  <div className="mt-2 space-y-3 rounded-md border border-primary/25 bg-primary/5 p-3">
                    <label className="block text-xs font-semibold text-text" htmlFor={`segment-${segment.id}`}>订正文字</label>
                    <Textarea id={`segment-${segment.id}`} value={draft} onChange={(event) => setDraft(event.target.value)} className="min-h-24 bg-white" autoFocus />
                    <fieldset className="space-y-2">
                      <legend className="text-xs font-semibold text-text">修改类型</legend>
                      <div className="flex flex-wrap gap-3 text-sm text-text">
                        <label className="flex min-h-11 cursor-pointer items-center gap-2"><input type="radio" name={`intent-${segment.id}`} checked={intent === 'asr_error'} onChange={() => setIntent('asr_error')} />识别错误</label>
                        <label className="flex min-h-11 cursor-pointer items-center gap-2"><input type="radio" name={`intent-${segment.id}`} checked={intent === 'content_edit'} onChange={() => setIntent('content_edit')} />仅修改表述</label>
                      </div>
                    </fieldset>
                    {intent === 'asr_error' ? (
                      <div className="space-y-2">
                        <label className={cn('flex min-h-11 items-center gap-2 text-sm', correctionLearningEnabled ? 'cursor-pointer text-text' : 'cursor-not-allowed text-text-muted')}>
                          <input type="checkbox" checked={contribute} disabled={!correctionLearningEnabled} onChange={(event) => setContribute(event.target.checked)} />
                          使用本次订正提升后续识别{correctionLearningEnabled ? '' : '（未启用）'}
                        </label>
                        {correctionLearningEnabled && contribute ? (
                          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-text">
                            <input type="checkbox" checked={addTerm} onChange={(event) => setAddTerm(event.target.checked)} />
                            加入个人术语候选
                          </label>
                        ) : null}
                        {correctionLearningEnabled && addTerm && contribute ? (
                          <div className="grid gap-2 sm:grid-cols-2">
                            <div><label className="text-xs font-medium text-text" htmlFor={`term-${segment.id}`}>正确术语</label><Input id={`term-${segment.id}`} value={term} onChange={(event) => setTerm(event.target.value)} /></div>
                            <div><label className="text-xs font-medium text-text" htmlFor={`wrong-${segment.id}`}>常见误识别</label><Input id={`wrong-${segment.id}`} value={misrecognition} onChange={(event) => setMisrecognition(event.target.value)} /></div>
                          </div>
                        ) : null}
                        <Button asChild type="button" size="sm" variant="ghost">
                          <Link to={`/meetings/lexicon?meeting_id=${encodeURIComponent(segment.meeting_id)}`}><BookOpenText />管理本场术语</Link>
                        </Button>
                      </div>
                    ) : null}
                    {error ? <p role="alert" className="text-sm text-error">{error}</p> : null}
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button type="button" variant="ghost" size="sm" className="max-sm:h-11" onClick={() => setEditingId('')} disabled={busy}><X />取消</Button>
                      <Button type="button" size="sm" className="max-sm:h-11" onClick={() => void save(segment)} disabled={busy}><Save />{busy ? '保存中' : '保存'}</Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className={cn('mt-1 whitespace-pre-wrap break-words text-[15px] leading-7 text-text', segment.text_state === 'review_required' && 'decoration-warning underline decoration-wavy')}>{segmentDisplayText(segment)}</p>
                    <DiffPreview segment={segment} />
                  </>
                )}
              </div>
            </article>
          )
        })}
        </div>
        {partialList.map((partial) => {
          const speaker = partial.speaker_track_id ? speakerById.get(partial.speaker_track_id) : undefined
          return (
            <div key={partial.utterance_id} className="grid min-h-20 grid-cols-[4.5rem_minmax(0,1fr)] gap-3 border-b border-border/70 px-1 py-4 text-text-muted" aria-hidden="true">
              <span className="px-1 font-mono text-xs tabular-nums">{formatMeetingTimestamp(partial.start_ms || 0)}</span>
              <div className="min-w-0"><p className="text-sm font-medium">{speaker?.display_name || '发言人'}</p><p className="mt-1 break-words text-[15px] leading-7">{partial.text}<span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-primary align-middle" /></p></div>
            </div>
          )
        })}
      </div>
      {hasLaterSegments ? (
        <div className="mt-2 flex justify-center">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="max-sm:h-11"
            disabled={loadingPage != null}
            onClick={() => void onLoadLater?.()}
          >
            {loadingPage === 'later' ? <Loader2 className="animate-spin" /> : <ChevronDown />}
            {loadingPage === 'later' ? '正在加载' : '加载后续段落'}
          </Button>
        </div>
      ) : null}
      {live && !following ? (
        <Button type="button" size="sm" className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 shadow-lg max-sm:h-11" onClick={() => setFollowing(true)}>
          <CornerDownLeft />回到实时
        </Button>
      ) : null}
    </div>
  )
}
