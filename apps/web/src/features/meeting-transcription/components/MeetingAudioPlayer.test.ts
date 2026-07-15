import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingAudioPlayer.tsx'), 'utf8')

test('meeting audio player resolves relative ticket URLs through the trusted API boundary', () => {
  assert.match(source, /setAudioUrl\(resolveSiqApiUrl\(ticket\.audio_url\)\)/)
  assert.doesNotMatch(source, /setAudioUrl\(ticket\.audio_url\)/)
})
