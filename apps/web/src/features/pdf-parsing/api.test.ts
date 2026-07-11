/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { normalizeWorkflowStatus, runMarketDocumentFullWorkflowImportApi } = await import('./api.ts')

test('normalizeWorkflowStatus accepts market document_full database counters', () => {
  const status = normalizeWorkflowStatus({
    database: {
      status: 'ready',
      schema: 'pdf2md_hk',
      parse_run_id: 'parse-hk-1',
      parse_runs: 1,
      facts: 12,
      tables: 3,
      chunks: 8,
      evidence: 5,
    } as unknown as NonNullable<Parameters<typeof normalizeWorkflowStatus>[0]['database']>,
  })

  assert.equal(status.database?.statementItems, 12)
  assert.equal(status.database?.tables, 3)
  assert.equal(status.database?.message, 'schema pdf2md_hk / parse_run_id parse-hk-1；parse_runs 1 / facts 12 / tables 3 / chunks 8 / evidence 5')
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

test('runMarketDocumentFullWorkflowImportApi sends generic market task_id payloads', async () => {
  const originalFetch = globalThis.fetch
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init })
    const body = JSON.parse(String(init?.body || '{}')) as { market?: string }
    return new Response(JSON.stringify({ ok: true, parse_run_id: `parse-${String(body.market || '').toLowerCase()}` }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  try {
    const markets = ['HK', 'JP', 'KR', 'EU'] as const
    for (const market of markets) {
      const result = await runMarketDocumentFullWorkflowImportApi(market, `task-${market.toLowerCase()}-001`)
      assert.equal(result.parse_run_id, `parse-${market.toLowerCase()}`)
    }

    assert.equal(calls.length, markets.length)
    calls.forEach((call, index) => {
      const market = markets[index]
      assert.equal(call.input, '/api/market-reports/document-full/import')
      assert.equal(call.init?.method, 'POST')
      assert.deepEqual(JSON.parse(String(call.init?.body)), {
        market,
        task_id: `task-${market.toLowerCase()}-001`,
        ddl: true,
      })
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})
