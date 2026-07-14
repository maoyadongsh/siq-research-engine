/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { selectFeatureRouteLoader } from './featureRouteGate.ts'

test('disabled feature routes cannot load the feature module or trigger its request side effect', async () => {
  let meetingModuleLoads = 0
  let meetingApiRequests = 0
  const loadMeetingPage = async () => {
    meetingModuleLoads += 1
    meetingApiRequests += 1
    return { default: 'meeting-page' }
  }
  const loadUnavailablePage = async () => ({ default: 'unavailable-page' })

  const loader = selectFeatureRouteLoader(false, loadMeetingPage, loadUnavailablePage)

  assert.equal(loader, loadUnavailablePage)
  assert.deepEqual(await loader(), { default: 'unavailable-page' })
  assert.equal(meetingModuleLoads, 0)
  assert.equal(meetingApiRequests, 0)
})

test('meeting unavailable page has no meeting feature or API dependency', () => {
  const source = readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '../pages/MeetingUnavailable.tsx'),
    'utf8',
  )

  assert.doesNotMatch(source, /meeting-transcription/)
  assert.doesNotMatch(source, /api\/meetings/)
})
