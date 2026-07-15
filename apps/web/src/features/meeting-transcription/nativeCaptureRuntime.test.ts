/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { MeetingCaptureCapabilityEnvelope } from './captureAdapter'
import { loadNativeCaptureRuntime } from './nativeCaptureRuntime'

const capabilities: MeetingCaptureCapabilityEnvelope = {
  audio: { capture_adapters: { ios_native: { available: true } } },
}

test('native runtime stays unloaded while the independent frontend flag is off', async () => {
  let loaded = false
  const result = await loadNativeCaptureRuntime(capabilities, {
    frontendEnabled: false,
    loadCapacitor: async () => {
      loaded = true
      throw new Error('must not load')
    },
  })
  assert.equal(result.adapter, null)
  assert.equal(result.reason, 'native_frontend_flag_disabled')
  assert.equal(loaded, false)
})

test('native runtime requires Capacitor iOS and registers the frozen plugin lazily', async () => {
  const registrations: string[] = []
  const plugin = {} as never
  const result = await loadNativeCaptureRuntime(capabilities, {
    frontendEnabled: true,
    loadCapacitor: async () => ({
      Capacitor: {
        isNativePlatform: () => true,
        getPlatform: () => 'ios',
        isPluginAvailable: () => true,
      },
      registerPlugin: <T>(name: string) => {
        registrations.push(name)
        return plugin as T
      },
    }),
  })
  assert.equal(result.reason, 'native_selected')
  assert.ok(result.adapter)
  assert.deepEqual(registrations, ['MeetingCapture'])
})

test('native runtime falls back without registering when the backend capability is unavailable', async () => {
  let registered = false
  const result = await loadNativeCaptureRuntime(null, {
    frontendEnabled: true,
    loadCapacitor: async () => ({
      Capacitor: {
        isNativePlatform: () => true,
        getPlatform: () => 'ios',
        isPluginAvailable: () => true,
      },
      registerPlugin: <T>() => {
        registered = true
        return {} as T
      },
    }),
  })
  assert.equal(result.adapter, null)
  assert.equal(result.reason, 'native_backend_capability_unavailable')
  assert.equal(registered, false)
})
