import type { MeetingSessionState, MeetingTranscriptSegment } from './types'

export const meetingStateLabels: Record<MeetingSessionState, string> = {
  draft: '未开始',
  connecting: '连接中',
  live: '进行中',
  paused: '已暂停',
  reconnecting: '重连中',
  stopping: '结束中',
  stopped: '已结束',
  archived: '已归档',
  interrupted: '异常中断',
  deleted: '已删除',
}

export const meetingPostprocessStateLabels: Record<string, string> = {
  not_started: '未开始',
  queued: '排队中',
  running: '处理中',
  succeeded: '已完成',
  failed: '处理失败',
}

export function meetingPostprocessStateTone(state?: string | null) {
  if (state === 'succeeded') return 'success' as const
  if (state === 'failed') return 'error' as const
  if (state === 'queued' || state === 'running') return 'info' as const
  return 'neutral' as const
}

export function meetingPostprocessStateLabel(state?: string | null) {
  return meetingPostprocessStateLabels[state || 'not_started'] || state || '未开始'
}

export function formatMeetingDuration(durationMs?: number | null) {
  const totalSeconds = Math.max(0, Math.floor((durationMs || 0) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const parts = [minutes, seconds].map((part) => String(part).padStart(2, '0'))
  return hours > 0 ? `${String(hours).padStart(2, '0')}:${parts.join(':')}` : parts.join(':')
}

export function formatMeetingTimestamp(offsetMs: number) {
  return formatMeetingDuration(offsetMs)
}

export function parseMeetingDate(value?: string | null) {
  if (!value) return null
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value.trim()) ? value.trim() : `${value.trim()}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatMeetingDate(value?: string | null) {
  const date = parseMeetingDate(value)
  if (!date) return '暂无'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function segmentDisplayText(segment: MeetingTranscriptSegment) {
  return segment.display_text || segment.text || segment.normalized_text || segment.asr_final_text || segment.raw_text
}

export function defaultMeetingTitle(now = new Date()) {
  const date = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(now)
  return `${date.replace(/\//g, '-')} 会议`
}

export function meetingDurationMs(startedAt?: string | null, stoppedAt?: string | null, now = Date.now()) {
  const started = parseMeetingDate(startedAt)?.getTime()
  if (started == null) return 0
  const stopped = stoppedAt ? parseMeetingDate(stoppedAt)?.getTime() : now
  return Math.max(0, (stopped != null ? stopped : now) - started)
}
