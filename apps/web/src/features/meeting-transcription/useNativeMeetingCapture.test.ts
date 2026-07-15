import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'useNativeMeetingCapture.ts'), 'utf8')

test('native hook reconciles foreground checkpoints through the bounded recovery helper', () => {
  assert.match(source, /recoverNativeCaptureAfterForeground\(\{/)
  assert.match(source, /getStatus: \(\) => adapter\.getStatus\(\)/)
  assert.match(source, /getCheckpoints: \(\) => adapter\.getCheckpoints\(\)/)
  assert.match(source, /retryPendingUploads: \(\) => adapter\.retryPendingUploads\(\)/)
  assert.match(source, /const rollover = await adapter\.rollover\(\)/)
  assert.match(source, /foregroundRecoveryPendingRef\.current/)
  assert.match(source, /foregroundRecoveryRequestedRef\.current = retainNativeRecoveryRequest\(result\.outcome\)/)
  assert.match(source, /foregroundRecoveryRequestedRef\.current[\s\S]*await recoverAfterForeground\(\)/)
  assert.match(source, /document\.visibilityState === 'hidden'/)
  assert.match(source, /document\.addEventListener\('visibilitychange'/)
})

test('native hook discovers recovered captures without preparing a microphone', () => {
  const selectionStart = source.indexOf('const select = useCallback')
  const refreshStart = source.indexOf('const refresh = useCallback', selectionStart)
  const selection = source.slice(selectionStart, refreshStart)
  assert.match(selection, /recoverPendingCaptures\(\)/)
  assert.doesNotMatch(selection, /\.(prepare|start|resume)\(/)
})
