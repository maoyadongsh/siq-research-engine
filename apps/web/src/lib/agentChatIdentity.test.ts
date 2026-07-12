/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { mergeResearchIdentity } from './agentChatIdentity.ts'

test('mergeResearchIdentity retains explicit complete identity fields', () => {
  assert.deepEqual(mergeResearchIdentity(
    { market: 'HK', company_id: 'HK:00700' },
    { filing_id: 'HK:00700:2025-annual', parse_run_id: 'parse-hk-00700' },
  ), {
    market: 'HK',
    company_id: 'HK:00700',
    filing_id: 'HK:00700:2025-annual',
    parse_run_id: 'parse-hk-00700',
  })
})

test('mergeResearchIdentity does not infer identity from display-only fields', () => {
  assert.equal(mergeResearchIdentity({ code: '00700', name: 'Tencent', dir: 'HK:00700' }), undefined)
})

test('mergeResearchIdentity fails closed on conflicting explicit sources', () => {
  assert.equal(mergeResearchIdentity({ company_id: 'HK:00700' }, { company_id: 'HK:09988' }), undefined)
})
