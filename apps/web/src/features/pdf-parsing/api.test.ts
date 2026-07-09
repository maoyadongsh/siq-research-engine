/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { normalizeWorkflowStatus } = await import('./api.ts')

test('normalizeWorkflowStatus accepts market document_full database counters', () => {
  const status = normalizeWorkflowStatus({
    database: {
      status: 'ready',
      facts: 12,
      tables: 3,
      chunks: 8,
      evidence: 5,
    } as unknown as NonNullable<Parameters<typeof normalizeWorkflowStatus>[0]['database']>,
  })

  assert.equal(status.database?.statementItems, 12)
  assert.equal(status.database?.tables, 3)
  assert.equal(status.database?.message, '指标 12 / 表格 3 / chunks 8 / evidence 5')
})

test('normalizeWorkflowStatus preserves A-share statementItems counters', () => {
  const status = normalizeWorkflowStatus({
    database: {
      status: 'ready',
      statementItems: 7,
      tables: 2,
    },
  })

  assert.equal(status.database?.statementItems, 7)
  assert.equal(status.database?.tables, 2)
  assert.equal(status.database?.message, '指标 7 / 表格 2')
})
