import assert from 'node:assert/strict'
import test from 'node:test'

import {
  earlierSegmentsFromPage,
  earlierTranscriptAfterOrdinal,
  initialTranscriptAfterOrdinal,
  mergeTranscriptSegments,
} from './transcriptPagination'
import type { MeetingTranscriptSegment } from './types'

function segment(ordinal: number, revision = 1, humanLocked = false): MeetingTranscriptSegment {
  return {
    id: `segment-${ordinal}`,
    meeting_id: 'meeting-1',
    ordinal,
    utterance_id: `utterance-${ordinal}`,
    start_ms: ordinal * 1_000,
    end_ms: ordinal * 1_000 + 800,
    raw_text: `raw ${ordinal}`,
    asr_final_text: `text ${ordinal}`,
    display_text: `text ${ordinal}`,
    revision_no: revision,
    text_state: humanLocked ? 'human_verified' : 'stable',
    human_locked: humanLocked,
  }
}

test('initial transcript request jumps directly to one latest bounded page', () => {
  assert.equal(initialTranscriptAfterOrdinal(5_000), 4_800)
  assert.equal(initialTranscriptAfterOrdinal(120), 0)
  assert.equal(initialTranscriptAfterOrdinal(5_000, 50), 4_950)
})

test('earlier transcript request targets only the preceding page', () => {
  assert.equal(earlierTranscriptAfterOrdinal(4_801), 4_600)
  assert.equal(earlierTranscriptAfterOrdinal(101), 0)
  assert.equal(earlierTranscriptAfterOrdinal(1), null)

  const response = [segment(4_601), segment(4_800), segment(4_801)]
  assert.deepEqual(earlierSegmentsFromPage(response, 4_801).map((item) => item.ordinal), [4_601, 4_800])
})

test('page merges retain realtime appends and protect newer human revisions', () => {
  const liveAppend = segment(5_001)
  const verified = { ...segment(4_999, 3, true), display_text: '人工确认' }
  const stalePageCopy = { ...segment(4_999, 1), display_text: '旧识别结果' }
  const merged = mergeTranscriptSegments(
    [verified, segment(5_000), liveAppend],
    [segment(4_801), stalePageCopy, segment(5_000)],
  )

  assert.deepEqual(merged.map((item) => item.ordinal), [4_801, 4_999, 5_000, 5_001])
  assert.equal(merged.find((item) => item.ordinal === 4_999)?.display_text, '人工确认')
  assert.equal(merged.at(-1)?.id, liveAppend.id)
})
