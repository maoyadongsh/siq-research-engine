/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { meetingDurationMs, parseMeetingDate } from './formatters.ts'

test('naive meeting timestamps are interpreted as UTC', () => {
  const now = Date.parse('2026-07-13T14:43:00Z')
  assert.equal(meetingDurationMs('2026-07-13T14:42:00', null, now), 60_000)
  assert.equal(parseMeetingDate('2026-07-13T14:42:00')?.toISOString(), '2026-07-13T14:42:00.000Z')
})

test('meeting timestamps with an explicit offset keep that offset', () => {
  const now = Date.parse('2026-07-13T14:43:00Z')
  assert.equal(meetingDurationMs('2026-07-13T22:42:00+08:00', null, now), 60_000)
})
