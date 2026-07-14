/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingLexicon.tsx'), 'utf-8')

test('current-meeting lexicon entries require and submit the route meeting id', () => {
  assert.match(source, /searchParams\.get\('meeting_id'\)/)
  assert.match(source, /meeting_id: effectiveScope === 'current_meeting' \? meetingId : undefined/)
  assert.match(source, /<SelectItem value="current_meeting" disabled=\{!meetingId\}>/)
  assert.match(source, /getMeetingLexicon\(meetingId \|\| undefined, signal\)/)
})
