/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

test('meeting detail offers DOCX for transcripts and minutes while keeping PDF unavailable', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingDetail.tsx'), 'utf-8')

  assert.match(source, /<SelectItem value="docx">Word（DOCX）<\/SelectItem>/)
  assert.match(source, /\['markdown', 'json', 'docx'\]\.includes\(exportFormat\)/)
  assert.doesNotMatch(source, /<SelectItem value="pdf">/)
})

test('meeting detail polls only active exports and keeps manual refresh and retry actions', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingDetail.tsx'), 'utf-8')

  assert.match(source, /const hasPendingExport = exports\.some/)
  assert.match(source, /if \(!hasPendingExport\) return undefined/)
  assert.match(source, /window\.setTimeout\(\(\) => void poll\(\), 2_000\)/)
  assert.match(source, /aria-label="刷新导出状态"/)
  assert.match(source, /await retryMeetingJob\(meetingId, item\.job_id\)/)
})

test('meeting detail reads actions and viewpoints from the preferred minutes artifact', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingDetail.tsx'), 'utf-8')

  assert.match(source, /selectPreferredMinutesArtifact\(artifacts\)/)
  assert.match(source, /section="speaker_viewpoints"/)
  assert.match(source, /section="action_items"/)
  assert.doesNotMatch(source, /artifactFor\(artifacts, 'viewpoints'\)/)
  assert.doesNotMatch(source, /artifactFor\(artifacts, 'action_items'\)/)
})

test('meeting detail exposes explicit versioned regeneration and evidence navigation', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingDetail.tsx'), 'utf-8')

  assert.match(source, /regenerateMeetingArtifact\(meetingId, artifact\.id, session\.settings_version\)/)
  assert.match(source, /setActiveTab\('transcript'\)/)
  assert.match(source, /seekPlayback\(segment\.start_ms\)/)
  assert.match(source, /setScrollToSegmentId\(segmentId\)/)
  assert.match(source, /scrollToSegmentId=\{scrollToSegmentId\}/)
  assert.match(source, /isMinutesArtifact\(artifact\) && artifact\.state === 'ready'/)
  assert.match(source, /minutesArtifact\?\.state === 'stale'/)
})

test('meeting detail binds audio time to bounded transcript playback tracking', () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingDetail.tsx'), 'utf-8')

  assert.match(source, /onPlaybackPositionChange=\{handlePlaybackPosition\}/)
  assert.match(source, /activePlaybackSegmentIds=\{activePlaybackSegments\}/)
  assert.match(source, /followPlayback=\{followPlayback\}/)
  assert.match(source, /atMs: positionMs/)
  assert.match(source, /samePlaybackSegments\(current, active\)/)
})
