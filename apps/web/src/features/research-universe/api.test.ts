/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { afterEach, test } from 'node:test'

import {
  artifactContentUrl,
  deleteGeneratedArtifact,
  fetchGeneratedArtifacts,
  fetchResearchCompanies,
  fetchSourceReports,
} from './api.ts'
import type { ArtifactScope } from './types.ts'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

test('company, source report and artifact requests encode opaque keys and exact report ids', async () => {
  const requests: string[] = []
  globalThis.fetch = (async (input) => {
    requests.push(String(input))
    const url = String(input)
    if (url.includes('/reports?')) return new Response(JSON.stringify({ market: 'US', company_key: 'AAPL/key', reports: [] }))
    if (url.includes('/artifacts?')) return new Response(JSON.stringify({ market: 'US', company_key: 'AAPL/key', report_id: '10-K/id', artifact_type: 'analysis', artifacts: [] }))
    return new Response(JSON.stringify({ market: 'US', companies: [] }))
  }) as typeof fetch

  await fetchResearchCompanies('US', 'analysis')
  await fetchSourceReports('US', 'AAPL/key', 'analysis')
  await fetchGeneratedArtifacts('US', 'AAPL/key', '10-K/id', 'analysis')

  assert.match(requests[0], /\/companies\?market=US&agent_type=analysis$/)
  assert.match(requests[1], /\/companies\/AAPL%2Fkey\/reports\?market=US&agent_type=analysis$/)
  assert.match(requests[2], /\/companies\/AAPL%2Fkey\/artifacts\?market=US&artifact_type=analysis&report_id=10-K%2Fid$/)
})

test('AbortSignal is passed through to cascading requests', async () => {
  const controller = new AbortController()
  let receivedSignal: AbortSignal | null | undefined
  globalThis.fetch = (async (_input, init) => {
    receivedSignal = init?.signal
    return new Response(JSON.stringify({ market: 'HK', companies: [] }))
  }) as typeof fetch

  await fetchResearchCompanies('HK', 'tracking', controller.signal)
  assert.equal(receivedSignal, controller.signal)
})

test('research page can defer baseline HTML integrity work until content selection', async () => {
  let requestUrl = ''
  globalThis.fetch = (async (input) => {
    requestUrl = String(input)
    return new Response(JSON.stringify({ market: 'US', company_key: 'us-aapl', reports: [] }))
  }) as typeof fetch

  await fetchSourceReports('US', 'us-aapl', 'analysis', undefined, {
    deferArtifactIntegrity: true,
  })
  assert.equal(
    requestUrl,
    '/api/research-universe/companies/us-aapl/reports?market=US&agent_type=analysis&defer_artifact_integrity=true',
  )
})

test('artifact content and delete operations accept only artifact ids', async () => {
  const requests: Array<{ url: string; method: string }> = []
  globalThis.fetch = (async (input, init) => {
    requests.push({ url: String(input), method: init?.method || 'GET' })
    return new Response(JSON.stringify({ deleted: true, artifact_id: '../unsafe id' }))
  }) as typeof fetch

  assert.equal(
    artifactContentUrl('../unsafe id'),
    '/api/research-universe/artifacts/..%2Funsafe%20id/content',
  )
  await deleteGeneratedArtifact('../unsafe id')
  assert.deepEqual(requests, [{
    url: '/api/research-universe/artifacts/..%2Funsafe%20id',
    method: 'DELETE',
  }])
})

test('first-page, cursor and scoped artifact requests do not expose browser paths', async () => {
  const requests: Array<{ url: string; method: string }> = []
  globalThis.fetch = (async (input, init) => {
    requests.push({ url: String(input), method: init?.method || 'GET' })
    return new Response(JSON.stringify({
      market: 'US', company_key: 'AAPL/key', report_id: '10-K/id', artifact_type: 'analysis', items: [],
    }))
  }) as typeof fetch
  const scope: ArtifactScope = {
    market: 'US',
    companyKey: 'AAPL/key',
    reportId: '10-K/id',
    artifactType: 'analysis',
  }

  await fetchGeneratedArtifacts('US', 'AAPL/key', '10-K/id', 'analysis', undefined, {
    limit: 1,
    cursor: 'exact:20',
    requestedArtifactId: 'analysis/aapl',
  })
  await deleteGeneratedArtifact('analysis/aapl', scope)

  assert.equal(
    requests[0].url,
    '/api/research-universe/companies/AAPL%2Fkey/artifacts?market=US&artifact_type=analysis&report_id=10-K%2Fid&limit=1&cursor=exact%3A20&requested_artifact_id=analysis%2Faapl',
  )
  assert.deepEqual(requests[1], {
    url: '/api/research-universe/artifacts/analysis%2Faapl?market=US&company_key=AAPL%2Fkey&report_id=10-K%2Fid&artifact_type=analysis',
    method: 'DELETE',
  })
  assert.equal(
    artifactContentUrl('analysis/aapl', scope),
    '/api/research-universe/artifacts/analysis%2Faapl/content?market=US&company_key=AAPL%2Fkey&report_id=10-K%2Fid&artifact_type=analysis',
  )
  assert.doesNotMatch(requests.map((request) => request.url).join('\n'), /(?:\/home\/|\/tmp\/|company_dir=)/)
})
