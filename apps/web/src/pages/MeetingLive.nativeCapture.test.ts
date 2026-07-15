import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MeetingLive.tsx'), 'utf8')

test('MeetingLive starts the server session before native capture and bypasses WebAudio transport', () => {
  const nativeBranchStart = source.indexOf('if (nativeSelected)')
  const nativeBranchEnd = source.indexOf("let storedDevice = ''", nativeBranchStart)
  const nativeBranch = source.slice(nativeBranchStart, nativeBranchEnd)
  assert.ok(nativeBranchStart > 0)
  assert.ok(nativeBranch.indexOf('await startMeeting(meetingId)') < nativeBranch.indexOf('await nativeCapture.start(current.stream_epoch)'))
  assert.match(nativeBranch, /await nativeCapture\.start\(current\.stream_epoch\)[\s\S]*return/)
  assert.doesNotMatch(nativeBranch, /realtime\.(prepareCapture|connect)/)
})

test('native stop seals locally and never enters the Web stop or finalize path', () => {
  const nativeStopStart = source.indexOf("if (nativeCapture.state.mode === 'native')", source.indexOf('async function finish'))
  const webStopStart = source.indexOf('await realtime.stop()', nativeStopStart)
  const nativeStopBranch = source.slice(nativeStopStart, webStopStart)
  assert.match(nativeStopBranch, /await nativeCapture\.stop\(\)/)
  assert.match(nativeStopBranch, /return/)
  assert.doesNotMatch(nativeStopBranch, /(stopMeeting|finalizeMeeting)\(/)
})

test('MeetingLive does not persist capture or bearer credentials', () => {
  assert.doesNotMatch(source, /(?:localStorage|sessionStorage)\.setItem\([^\n]*(?:token|bearer|secret)/i)
})

test('native pause keeps a connecting server session untouched and selection is recovery-only', () => {
  const pauseStart = source.indexOf('async function pause()')
  const resumeStart = source.indexOf('async function resume()', pauseStart)
  const pauseBranch = source.slice(pauseStart, resumeStart)
  assert.match(pauseBranch, /await nativeCapture\.pause\(\)/)
  assert.match(pauseBranch, /\['live', 'reconnecting'\]\.includes\(session\.state\)/)

  assert.match(source, /void selectNativeCapture\(capabilities\)/)
  const autoSelection = source.slice(
    source.indexOf('void selectNativeCapture(capabilities)'),
    source.indexOf('}, [capabilities, selectNativeCapture]'),
  )
  assert.doesNotMatch(autoSelection, /(prepareCapture|\.connect\(|\.start\()/)
})
