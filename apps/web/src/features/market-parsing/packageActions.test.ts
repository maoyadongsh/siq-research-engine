/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { MarketPackageActionDeps } from './packageActions.ts'
import type { MarketPackageActionResponse, MarketPackageBuildRequest, MarketCode } from './api.ts'

const {
  buildMarketPackageRequest,
  formatMarketPackageImportOutput,
  formatMarketPackageVectorOutput,
  runMarketPackageBuildAction,
  runMarketPackageImportAction,
  runMarketPackageVectorDryRunAction,
} = await import('./packageActions.ts')

function makeDeps(overrides: Partial<MarketPackageActionDeps> = {}): MarketPackageActionDeps {
  return {
    runBuild: async () => ({ ok: true }),
    runImport: async () => ({ ok: true }),
    runVectorIngest: async () => ({ ok: true }),
    waitForJob: async () => ({ ok: true }),
    ...overrides,
  }
}

test('buildMarketPackageRequest trims optional build paths', () => {
  assert.deepEqual(
    buildMarketPackageRequest({
      sourcePath: '  /tmp/source.html  ',
      parserResult: '  ',
      metadataPath: ' /tmp/meta.json ',
      force: true,
    }),
    {
      source_path: '/tmp/source.html',
      parser_result: undefined,
      metadata_path: '/tmp/meta.json',
      force: true,
    } satisfies MarketPackageBuildRequest,
  )
})

test('runMarketPackageBuildAction waits for queued job and returns built package path', async () => {
  let buildBody: MarketPackageBuildRequest | undefined
  let waitedJob = ''
  const deps = makeDeps({
    runBuild: async (market: MarketCode, body: MarketPackageBuildRequest) => {
      assert.equal(market, 'EU')
      buildBody = body
      return { ok: true, job_id: 'job-1' }
    },
    waitForJob: async (jobId: string) => {
      waitedJob = jobId
      return {
        ok: true,
        stdout: 'built ok',
        package: { package_path: 'EU/example/package' },
      } satisfies MarketPackageActionResponse
    },
  })

  const result = await runMarketPackageBuildAction({
    market: 'EU',
    sourcePath: ' /tmp/source.html ',
    parserResult: '',
    metadataPath: '',
  }, deps)

  assert.equal(waitedJob, 'job-1')
  assert.equal(buildBody?.source_path, '/tmp/source.html')
  assert.equal(result.output, 'built ok')
  assert.equal(result.builtPath, 'EU/example/package')
})

test('runMarketPackageImportAction keeps parse_run fallback output', async () => {
  const deps = makeDeps({
    runImport: async (market, packagePath, ddl) => {
      assert.equal(market, 'JP')
      assert.equal(packagePath, 'JP/pkg')
      assert.equal(ddl, true)
      return { ok: true, parse_run_id: 'parse-42' }
    },
  })

  const result = await runMarketPackageImportAction({ market: 'JP', packagePath: 'JP/pkg' }, deps)

  assert.equal(formatMarketPackageImportOutput(result.result), 'parse_run_id=parse-42')
  assert.equal(result.output, 'parse_run_id=parse-42')
})

test('runMarketPackageVectorDryRunAction serializes summary output', async () => {
  const deps = makeDeps({
    runVectorIngest: async (market, packagePath, dryRun) => {
      assert.equal(market, 'KR')
      assert.equal(packagePath, 'KR/pkg')
      assert.equal(dryRun, true)
      return { ok: true, summary: { chunks: 12, dry_run: true } }
    },
  })

  const result = await runMarketPackageVectorDryRunAction({ market: 'KR', packagePath: 'KR/pkg' }, deps)

  assert.equal(result.output, formatMarketPackageVectorOutput({ summary: { chunks: 12, dry_run: true } }))
  assert.match(result.output, /"chunks": 12/)
})
