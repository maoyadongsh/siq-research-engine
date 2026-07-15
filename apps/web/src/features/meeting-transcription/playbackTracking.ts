import type { MeetingTranscriptSegment } from './types'

type PlaybackRange = Pick<MeetingTranscriptSegment, 'id' | 'start_ms' | 'end_ms'>

export const PLAYBACK_TRANSCRIPT_LOOKUP_BUCKET_MS = 30_000
export const PLAYBACK_TRANSCRIPT_MAX_SILENCE_MS = 30_000

export function activePlaybackSegmentIds(
  segments: PlaybackRange[],
  positionMs: number,
) {
  if (!Number.isFinite(positionMs) || positionMs < 0) return []
  return segments
    .filter((segment) => (
      Number.isFinite(segment.start_ms)
      && Number.isFinite(segment.end_ms)
      && segment.start_ms <= positionMs
      && positionMs < Math.max(segment.start_ms, segment.end_ms)
    ))
    .map((segment) => segment.id)
}

export function playbackTranscriptWindowMissing(
  segments: PlaybackRange[],
  positionMs: number,
  maxSilenceMs = PLAYBACK_TRANSCRIPT_MAX_SILENCE_MS,
) {
  if (!Number.isFinite(positionMs) || positionMs < 0) return false
  if (!segments.length) return true
  if (activePlaybackSegmentIds(segments, positionMs).length) return false

  let previousEnd = Number.NEGATIVE_INFINITY
  let nextStart = Number.POSITIVE_INFINITY
  for (const segment of segments) {
    if (segment.start_ms <= positionMs) previousEnd = Math.max(previousEnd, segment.end_ms)
    if (segment.start_ms > positionMs) nextStart = Math.min(nextStart, segment.start_ms)
  }
  if (!Number.isFinite(previousEnd) || !Number.isFinite(nextStart)) return true
  return nextStart - previousEnd > maxSilenceMs
}

export function playbackTranscriptLookupBucket(positionMs: number) {
  if (!Number.isFinite(positionMs) || positionMs < 0) return 0
  return Math.floor(positionMs / PLAYBACK_TRANSCRIPT_LOOKUP_BUCKET_MS)
}

export function samePlaybackSegments(left: string[], right: string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
