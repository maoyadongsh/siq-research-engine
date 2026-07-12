/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { US_SEC_SOURCE_IFRAME_SANDBOX } from './usSecFrameSandbox.ts'

test('SEC source iframe keeps DOM trace access without granting script execution', () => {
  const tokens = US_SEC_SOURCE_IFRAME_SANDBOX.split(/\s+/).filter(Boolean)

  assert.ok(tokens.includes('allow-same-origin'))
  assert.ok(tokens.includes('allow-popups'))
  assert.equal(tokens.includes('allow-scripts'), false)
  assert.equal(tokens.includes('allow-forms'), false)
  assert.equal(tokens.includes('allow-top-navigation'), false)
})
