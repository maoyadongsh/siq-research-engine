import assert from 'node:assert/strict'
import test from 'node:test'

import {
  TRANSCRIPT_MAX_RENDERED_SEGMENTS,
  transcriptRangeExtractor,
} from './transcriptVirtualization'

test('thousands of transcript segments retain a strict rendered range bound', () => {
  const indexes = transcriptRangeExtractor({
    startIndex: 2_500,
    endIndex: 2_512,
    overscan: 100,
    count: 5_000,
  })

  assert.ok(indexes.length <= TRANSCRIPT_MAX_RENDERED_SEGMENTS)
  assert.ok(indexes.includes(2_500))
  assert.ok(indexes.includes(2_512))
})

test('an editing segment remains mounted outside the visible range without exceeding the bound', () => {
  const indexes = transcriptRangeExtractor({
    startIndex: 4_950,
    endIndex: 4_960,
    overscan: 100,
    count: 5_000,
  }, 12)

  assert.ok(indexes.includes(12))
  assert.ok(indexes.includes(4_950))
  assert.ok(indexes.length <= TRANSCRIPT_MAX_RENDERED_SEGMENTS)
})
