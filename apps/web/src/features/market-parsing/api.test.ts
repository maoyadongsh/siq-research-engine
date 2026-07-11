/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { runMarketDocumentFullImport } = await import('./api.ts')

test('runMarketDocumentFullImport sends US SEC document_full_path payload', async () => {
  const originalFetch = globalThis.fetch
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init })
    return new Response(JSON.stringify({ ok: true, parse_run_id: 'parse-us' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  try {
    const result = await runMarketDocumentFullImport(
      'US',
      'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json',
      true,
      false,
    )

    assert.equal(result.parse_run_id, 'parse-us')
    assert.equal(calls.length, 1)
    assert.equal(calls[0].input, '/api/market-reports/document-full/import')
    assert.equal(calls[0].init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      market: 'US',
      document_full_path: 'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json',
      ddl: true,
      force: false,
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})
