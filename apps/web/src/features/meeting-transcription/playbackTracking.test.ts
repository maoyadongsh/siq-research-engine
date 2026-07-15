/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  activePlaybackSegmentIds,
  playbackTranscriptLookupBucket,
  playbackTranscriptWindowMissing,
  samePlaybackSegments,
} from './playbackTracking'

const segments = [
  { id: 'first', start_ms: 1_000, end_ms: 3_000 },
  { id: 'overlap-a', start_ms: 4_000, end_ms: 7_000 },
  { id: 'overlap-b', start_ms: 5_000, end_ms: 8_000 },
]

test('playback tracking selects every overlapping active segment with end-exclusive boundaries', () => {
  assert.deepEqual(activePlaybackSegmentIds(segments, 1_000), ['first'])
  assert.deepEqual(activePlaybackSegmentIds(segments, 3_000), [])
  assert.deepEqual(activePlaybackSegmentIds(segments, 5_500), ['overlap-a', 'overlap-b'])
  assert.deepEqual(activePlaybackSegmentIds(segments, Number.NaN), [])
})

test('playback tracking distinguishes ordinary silence from an unloaded transcript window', () => {
  assert.equal(playbackTranscriptWindowMissing(segments, 3_500), false)
  assert.equal(playbackTranscriptWindowMissing(segments, 90_000), true)
  assert.equal(playbackTranscriptWindowMissing([], 0), true)
  assert.equal(playbackTranscriptWindowMissing(segments, -1), false)
})

test('playback lookup buckets and active id equality suppress redundant work', () => {
  assert.equal(playbackTranscriptLookupBucket(0), 0)
  assert.equal(playbackTranscriptLookupBucket(29_999), 0)
  assert.equal(playbackTranscriptLookupBucket(30_000), 1)
  assert.equal(samePlaybackSegments(['a', 'b'], ['a', 'b']), true)
  assert.equal(samePlaybackSegments(['a', 'b'], ['b', 'a']), false)
})
