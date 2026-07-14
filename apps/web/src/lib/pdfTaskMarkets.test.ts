/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { inferTaskMarket, taskMatchesMarket, type PdfMarket } from './pdfTaskMarkets.ts'
import type { TaskItem } from './pdfTypes.ts'

const pdfMarkets: PdfMarket[] = ['CN', 'HK', 'US', 'JP', 'KR', 'EU']

test('inferTaskMarket recognizes every parser market_scope value', () => {
  for (const market of pdfMarkets) {
    const task: TaskItem = {
      task_id: `task-${market.toLowerCase()}`,
      filename: 'annual-report.pdf',
      market_scope: market,
    }

    assert.equal(inferTaskMarket(task), market)
    assert.equal(taskMatchesMarket(task, market), true)
    assert.equal(taskMatchesMarket(task, market === 'CN' ? 'HK' : 'CN'), false)
  }
})

test('explicit task market takes precedence over parser market_scope', () => {
  const task: TaskItem = {
    task_id: 'task-explicit-market',
    filename: 'annual-report.pdf',
    market: 'HK',
    market_scope: 'EU',
  }

  assert.equal(inferTaskMarket(task), 'HK')
})

test('unknown and document parser scopes keep the legacy isolation rules', () => {
  assert.equal(inferTaskMarket({ task_id: 'legacy-task', filename: 'legacy.pdf', market_scope: 'unknown' }), '')
  assert.equal(taskMatchesMarket({ task_id: 'legacy-task', filename: 'legacy.pdf', market_scope: 'unknown' }, 'CN'), true)
  assert.equal(taskMatchesMarket({ task_id: 'doc-task', filename: 'document.pdf', market_scope: 'DOC' }, 'CN'), false)
})
