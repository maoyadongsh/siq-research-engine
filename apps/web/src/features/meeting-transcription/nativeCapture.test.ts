import assert from 'node:assert/strict'
import test from 'node:test'

import { MEETING_CAPTURE_PLUGIN_NAME, probeMeetingNativeRuntime } from './nativeCapture'

test('Capacitor runtime probe requires native iOS and the registered meeting plugin', () => {
  const requestedPlugins: string[] = []
  const runtime = probeMeetingNativeRuntime({
    isNativePlatform: () => true,
    getPlatform: () => 'ios',
    isPluginAvailable: (name) => {
      requestedPlugins.push(name)
      return true
    },
  })

  assert.deepEqual(runtime, { native: true, platform: 'ios', pluginAvailable: true })
  assert.deepEqual(requestedPlugins, [MEETING_CAPTURE_PLUGIN_NAME])
})

test('runtime probe fails closed for web, Android, missing plugins, and bridge errors', () => {
  assert.deepEqual(probeMeetingNativeRuntime(), { native: false, platform: 'web', pluginAvailable: false })
  assert.deepEqual(probeMeetingNativeRuntime({
    isNativePlatform: () => false,
    getPlatform: () => 'web',
    isPluginAvailable: () => true,
  }), { native: false, platform: 'web', pluginAvailable: false })
  assert.deepEqual(probeMeetingNativeRuntime({
    isNativePlatform: () => true,
    getPlatform: () => 'android',
    isPluginAvailable: () => true,
  }), { native: true, platform: 'android', pluginAvailable: false })
  assert.deepEqual(probeMeetingNativeRuntime({
    isNativePlatform: () => { throw new Error('bridge unavailable') },
    getPlatform: () => 'ios',
    isPluginAvailable: () => true,
  }), { native: false, platform: 'web', pluginAvailable: false })
})
