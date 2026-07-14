/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { fetchUsSecPackageByPath, runMarketDocumentFullImport } = await import('./api.ts')

test('fetchUsSecPackageByPath uses the US rich detail endpoint scoped by package_path', async () => {
  const originalFetch = globalThis.fetch
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init })
    return new Response(JSON.stringify({
      package_path: 'data/wiki/us/companies/ORCL/reports/2026-10-K',
      sections: [{ file: 'business.md' }],
      bridge_checks: { checks: [{ rule_id: 'balance-sheet' }] },
      counts: { dimension_facts: 1149 },
      dimension_facts: [
        {
          fact_id: 'fact-segment',
          concept: 'us-gaap:Revenue',
          dimensions: { 'srt:ProductOrServiceAxis': 'orcl:CloudMember' },
        },
      ],
      dimension_metrics: [{ metric_id: 'revenue' }],
      preview: { default_markdown: 'report_complete.md' },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch

  try {
    const detail = await fetchUsSecPackageByPath('data/wiki/us/companies/ORCL/reports/2026-10-K')

    assert.equal(detail.sections?.[0]?.file, 'business.md')
    assert.equal(detail.bridge_checks?.checks?.length, 1)
    assert.equal(detail.counts?.dimension_facts, 1149)
    assert.equal(detail.dimension_facts?.length, 1)
    assert.equal(detail.dimension_facts?.[0]?.fact_id, 'fact-segment')
    assert.equal(detail.dimension_metrics?.length, 1)
    assert.equal(calls.length, 1)
    assert.equal(
      calls[0].input,
      '/api/us-sec/package?package_path=data%2Fwiki%2Fus%2Fcompanies%2FORCL%2Freports%2F2026-10-K',
    )
    assert.doesNotMatch(String(calls[0].input), /market-reports\/package/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

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
