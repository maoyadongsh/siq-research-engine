import type { MeetingTranscriptSegment } from './types'

export const MEETING_TRANSCRIPT_PAGE_SIZE = 200

function positiveInteger(value: number, fallback: number) {
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback
}

export function initialTranscriptAfterOrdinal(
  lastSegmentOrdinal: number,
  pageSize = MEETING_TRANSCRIPT_PAGE_SIZE,
) {
  const normalizedPageSize = positiveInteger(pageSize, MEETING_TRANSCRIPT_PAGE_SIZE)
  const normalizedLastOrdinal = Math.max(0, Math.floor(lastSegmentOrdinal || 0))
  return Math.max(0, normalizedLastOrdinal - normalizedPageSize)
}

export function earlierTranscriptAfterOrdinal(
  earliestLoadedOrdinal: number | null | undefined,
  pageSize = MEETING_TRANSCRIPT_PAGE_SIZE,
) {
  if (earliestLoadedOrdinal == null || earliestLoadedOrdinal <= 1) return null
  const normalizedPageSize = positiveInteger(pageSize, MEETING_TRANSCRIPT_PAGE_SIZE)
  return Math.max(0, Math.floor(earliestLoadedOrdinal) - normalizedPageSize - 1)
}

export function mergeTranscriptSegments(
  current: MeetingTranscriptSegment[],
  incoming: MeetingTranscriptSegment[],
) {
  if (!incoming.length) return current

  const byId = new Map<string, MeetingTranscriptSegment>()
  const idByOrdinal = new Map<number, string>()
  for (const segment of current) {
    byId.set(segment.id, segment)
    idByOrdinal.set(segment.ordinal, segment.id)
  }

  for (const segment of incoming) {
    const existingId = byId.has(segment.id) ? segment.id : idByOrdinal.get(segment.ordinal)
    const existing = existingId ? byId.get(existingId) : undefined
    if (existing?.human_locked && !segment.human_locked) continue
    if (existing && segment.revision_no < existing.revision_no) continue

    if (existingId && existingId !== segment.id) byId.delete(existingId)
    if (existing && existing.ordinal !== segment.ordinal) idByOrdinal.delete(existing.ordinal)
    const merged = existing ? { ...existing, ...segment } : segment
    byId.set(segment.id, merged)
    idByOrdinal.set(segment.ordinal, segment.id)
  }

  return [...byId.values()].sort((left, right) => left.ordinal - right.ordinal)
}

export function earlierSegmentsFromPage(
  page: MeetingTranscriptSegment[],
  earliestLoadedOrdinal: number,
) {
  return page.filter((segment) => segment.ordinal < earliestLoadedOrdinal)
}

export function earliestTranscriptOrdinal(segments: MeetingTranscriptSegment[]) {
  return segments[0]?.ordinal ?? null
}

export function latestTranscriptOrdinal(segments: MeetingTranscriptSegment[]) {
  return segments.at(-1)?.ordinal ?? null
}
