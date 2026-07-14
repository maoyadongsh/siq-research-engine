/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { preferredMeetingModel } from './meetingModels'
import type { MeetingModel } from './types'

function model(modelRef: string, overrides: Partial<MeetingModel> = {}): MeetingModel {
  return {
    model_ref: modelRef,
    label: modelRef,
    provider_label: 'Hermes',
    locality: 'local',
    configured: true,
    available: true,
    capabilities: ['text'],
    data_boundary: 'local',
    ...overrides,
  }
}

test('preferred model uses the available configured default without hardcoding a model', () => {
  const selected = preferredMeetingModel([
    model('meeting:local:first'),
    model('meeting:cloud:configured-default', { locality: 'cloud', is_default: true }),
  ])

  assert.equal(selected?.model_ref, 'meeting:cloud:configured-default')
})

test('preferred model falls back when the declared default is unavailable', () => {
  const selected = preferredMeetingModel([
    model('meeting:cloud:offline-default', { is_default: true, available: false }),
    model('meeting:local:fallback'),
  ])

  assert.equal(selected?.model_ref, 'meeting:local:fallback')
})
