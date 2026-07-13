/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  formatVoiceDuration,
  selectVoiceRecorderMimeType,
  voiceRecordingExtension,
} from './useVoiceRecorder.ts'

test('voice MIME selection prefers Opus WebM and falls back to MP4', () => {
  assert.equal(selectVoiceRecorderMimeType(() => true), 'audio/webm;codecs=opus')
  assert.equal(selectVoiceRecorderMimeType((mimeType) => mimeType === 'audio/mp4'), 'audio/mp4')
  assert.equal(selectVoiceRecorderMimeType(() => false), '')
})

test('voice recording extension follows the actual recorder MIME type', () => {
  assert.equal(voiceRecordingExtension('audio/webm;codecs=opus'), 'webm')
  assert.equal(voiceRecordingExtension('audio/mp4'), 'm4a')
  assert.equal(voiceRecordingExtension('audio/ogg;codecs=opus'), 'ogg')
  assert.equal(voiceRecordingExtension('audio/wav'), 'wav')
})

test('voice duration uses stable minute and second labels', () => {
  assert.equal(formatVoiceDuration(0), '00:00')
  assert.equal(formatVoiceDuration(59_999), '00:59')
  assert.equal(formatVoiceDuration(60_000), '01:00')
})
