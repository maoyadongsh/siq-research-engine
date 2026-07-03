/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { countEnabledServices } from './utils.ts'
import type { ServiceStatus } from './types.ts'

function service(partial: Partial<ServiceStatus>): ServiceStatus {
  return {
    id: partial.id || 'service',
    name: partial.name || 'Service',
    category: partial.category || 'agent',
    url: partial.url || 'http://127.0.0.1/health',
    required: partial.required ?? true,
    ok: partial.ok ?? true,
    statusCode: partial.statusCode ?? 200,
    latencyMs: partial.latencyMs ?? 1,
    detail: partial.detail ?? {},
    enabled: partial.enabled,
    status: partial.status,
  }
}

test('countEnabledServices ignores disabled services in availability and required-down counts', () => {
  const counts = countEnabledServices([
    service({ id: 'api', required: true, ok: true }),
    service({ id: 'required-down', required: true, ok: false, status: 'unavailable', statusCode: null }),
    service({ id: 'optional-down', required: false, ok: false, status: 'unavailable', statusCode: null }),
    service({ id: 'ic-disabled-flag', required: false, ok: false, enabled: false, status: 'disabled' }),
    service({ id: 'ic-disabled-status', required: false, ok: false, status: 'disabled' }),
  ])

  assert.deepEqual(counts, {
    total: 3,
    disabled: 2,
    ok: 1,
    requiredDown: 1,
  })
})

test('countEnabledServices returns zero counts for empty status payloads', () => {
  assert.deepEqual(countEnabledServices(), {
    total: 0,
    disabled: 0,
    ok: 0,
    requiredDown: 0,
  })
})
