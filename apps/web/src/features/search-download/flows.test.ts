/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  annualFormsForMarket,
  identifierPayloadForSearch,
} = await import('./flows.ts')

test('identifierPayloadForSearch prefers explicit ticker and company id for foreign smart-search results', () => {
  assert.deepEqual(
    identifierPayloadForSearch({
      targetMarket: 'US',
      targetQuery: '苹果',
      targetTicker: 'AAPL',
      targetCompanyId: '0000320193',
    }),
    {
      ticker: 'AAPL',
      company_id: '0000320193',
    },
  )
})

test('identifierPayloadForSearch keeps EU country-qualified identifier from smart-search results', () => {
  assert.deepEqual(
    identifierPayloadForSearch({
      targetMarket: 'EU',
      targetQuery: '阿斯麦',
      targetTicker: 'ASML',
      targetCompanyId: 'NL:ASML',
      targetFilter: 'NL',
    }),
    {
      ticker: 'ASML',
      company_id: 'NL:ASML',
    },
  )
})

test('annualFormsForMarket requests JP statutory YUHO instead of IR fallback', () => {
  assert.deepEqual(annualFormsForMarket('JP'), ['yuho'])
})
