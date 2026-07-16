/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { isMultiMarketResearchEnabled, resolveMultiMarketResearchEnabled } from './featureFlag.ts'

test('multi-market research is disabled by default', () => {
  assert.equal(resolveMultiMarketResearchEnabled({}), false)
})

test('an explicit environment flag overrides the deployment runtime fallback', () => {
  assert.equal(resolveMultiMarketResearchEnabled({ envValue: '0', runtimeValue: '1' }), false)
  assert.equal(resolveMultiMarketResearchEnabled({ envValue: 'true', runtimeValue: '0' }), true)
})

test('deployment runtime config supports controlled rollout without browser storage', () => {
  assert.equal(resolveMultiMarketResearchEnabled({ runtimeValue: 'on' }), true)
  assert.doesNotMatch(isMultiMarketResearchEnabled.toString(), /localStorage|sessionStorage/)
})
