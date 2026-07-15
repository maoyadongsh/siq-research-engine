/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), 'TranscriptTimeline.tsx'),
  'utf-8',
)

test('correction learning is fail-closed in the transcript editor', () => {
  assert.match(source, /correctionLearningEnabled = false/)
  assert.doesNotMatch(source, /getMeetingCapabilities/)
  assert.match(source, /disabled=\{!correctionLearningEnabled\}/)
  assert.match(source, /correctionLearningEnabled && intent === 'asr_error' && contribute/)
  assert.match(source, /correctionLearningEnabled && contribute && addTerm/)
})

test('timeline uses measured windowing while pinning the active editor and isolating announcements', () => {
  assert.match(source, /useVirtualizer\(\{/)
  assert.match(source, /transcriptRangeExtractor\(range, editingIndex\)/)
  assert.match(source, /ref=\{virtualizer\.measureElement\}/)
  assert.match(source, /data-transcript-segment=\{segment\.id\}/)
  assert.match(source, /aria-live="polite"/)
  assert.match(source, /aria-hidden="true"/)
})

test('timeline exposes accessible playback highlighting and resumable follow mode', () => {
  assert.match(source, /data-playback-active=\{playbackActive \? 'true' : undefined\}/)
  assert.match(source, /aria-current=\{playbackActive \? 'true' : undefined\}/)
  assert.match(source, /onWheel=\{pausePlaybackFollowing\}/)
  assert.match(source, /onTouchStart=\{pausePlaybackFollowing\}/)
  assert.match(source, /回到播放位置/)
  assert.match(source, /title="播放此段录音"/)
})
