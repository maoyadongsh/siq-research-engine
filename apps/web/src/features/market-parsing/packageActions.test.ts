/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { MarketPackageActionDeps } from './packageActions.ts'
import type { MarketDocumentFullImportResponse, MarketPackageActionResponse, MarketPackageBuildRequest, MarketCode } from './api.ts'

const {
  buildMarketPackageRequest,
  formatMarketDocumentFullImportOutput,
  formatMarketPackageImportOutput,
  formatMarketPackageVectorOutput,
  runMarketDocumentFullImportAction,
  runMarketPackageBuildAction,
  runMarketPackageImportAction,
  runMarketPackageVectorDryRunAction,
} = await import('./packageActions.ts')

function makeDeps(overrides: Partial<MarketPackageActionDeps> = {}): MarketPackageActionDeps {
  return {
    runBuild: async () => ({ ok: true }),
    runDocumentFullImport: async () => ({ ok: true }),
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

test('buildMarketPackageRequest prefers the portable download-relative path', () => {
  assert.deepEqual(
    buildMarketPackageRequest({
      sourcePath: '/srv/private/source.xhtml',
      downloadRelativePath: ' EU/DE/SAP/annual/2025/report.xhtml ',
      force: false,
    }),
    {
      download_relative_path: 'EU/DE/SAP/annual/2025/report.xhtml',
      parser_result: undefined,
      metadata_path: undefined,
      force: false,
    } satisfies MarketPackageBuildRequest,
  )
})

test('buildMarketPackageRequest rejects an empty package source', () => {
  assert.throws(() => buildMarketPackageRequest({ sourcePath: ' ', downloadRelativePath: ' ' }), /缺少待构建文件路径/)
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

test('runMarketPackageBuildAction surfaces a failed queued build result', async () => {
  const deps = makeDeps({
    runBuild: async () => ({ ok: true, job_id: 'job-failed' }),
    waitForJob: async () => ({ ok: false, stderr: 'ESEF taxonomy is invalid' }),
  })

  await assert.rejects(
    runMarketPackageBuildAction({
      market: 'EU',
      downloadRelativePath: 'EU/DE/SAP/annual/2025/report.zip',
      force: false,
    }, deps),
    /ESEF taxonomy is invalid/,
  )
})

test('runMarketPackageImportAction keeps parse_run fallback output only for explicit legacy import', async () => {
  const deps = makeDeps({
    runImport: async (market, packagePath, ddl) => {
      assert.equal(market, 'JP')
      assert.equal(packagePath, 'JP/pkg')
      assert.equal(ddl, true)
      return { ok: true, parse_run_id: 'parse-42' }
    },
  })

  const result = await runMarketPackageImportAction({ market: 'JP', packagePath: 'JP/pkg', legacyPackageImport: true }, deps)

  assert.equal(formatMarketPackageImportOutput(result.result), 'parse_run_id=parse-42')
  assert.equal(result.output, 'parse_run_id=parse-42')
})

test('runMarketPackageImportAction defaults explicit legacy HK imports to ddl enabled and preserves stdout', async () => {
  const calls: Array<{ market: string; packagePath: string; ddl?: boolean; force?: boolean }> = []
  const deps = makeDeps({
    runImport: async (market, packagePath, ddl, force) => {
      calls.push({ market, packagePath, ddl, force })
      return { ok: true, stdout: 'hk import ok\n' }
    },
  })

  const result = await runMarketPackageImportAction({ market: 'HK', packagePath: 'HK/pkg', force: true, legacyPackageImport: true }, deps)

  assert.equal(calls[0].market, 'HK')
  assert.equal(calls[0].packagePath, 'HK/pkg')
  assert.equal(calls[0].ddl, true)
  assert.equal(calls[0].force, true)
  assert.equal(result.output, 'hk import ok\n')
})

test('runMarketPackageImportAction rejects implicit package import without document_full path', async () => {
  let packageImportCalled = false
  const deps = makeDeps({
    runImport: async () => {
      packageImportCalled = true
      return { ok: true }
    },
  })

  await assert.rejects(
    runMarketPackageImportAction({ market: 'HK', packagePath: 'HK/pkg' }, deps),
    /缺少 document_full\.json 路径/,
  )
  assert.equal(packageImportCalled, false)
})

test('runMarketPackageImportAction prefers document_full import when path is provided', async () => {
  const calls: Array<{ kind: string; market: string; path: string; ddl?: boolean; force?: boolean }> = []
  const deps = makeDeps({
    runDocumentFullImport: async (market, documentFullPath, ddl, force) => {
      calls.push({ kind: 'document_full', market, path: documentFullPath, ddl, force })
      return { ok: true, stdout: 'document_full ok\n' } satisfies MarketDocumentFullImportResponse
    },
    runImport: async (market, packagePath, ddl, force) => {
      calls.push({ kind: 'package', market, path: packagePath, ddl, force })
      return { ok: true, stdout: 'package ok\n' }
    },
  })

  const result = await runMarketPackageImportAction({
    market: 'HK',
    packagePath: 'HK/pkg',
    documentFullPath: ' task-1/document_full.json ',
    force: true,
  }, deps)

  assert.deepEqual(calls, [{ kind: 'document_full', market: 'HK', path: 'task-1/document_full.json', ddl: true, force: true }])
  assert.equal(result.output, 'document_full ok\n')
})

test('runMarketDocumentFullImportAction calls market document_full importer', async () => {
  const calls: Array<{ market: string; path: string; ddl?: boolean; force?: boolean }> = []
  const deps = makeDeps({
    runDocumentFullImport: async (market, documentFullPath, ddl, force) => {
      calls.push({ market, path: documentFullPath, ddl, force })
      return { ok: true, parse_run_id: 'parse-doc-full', stdout: 'document_full imported\n' } satisfies MarketDocumentFullImportResponse
    },
  })

  const result = await runMarketDocumentFullImportAction({
    market: 'EU',
    documentFullPath: ' task-1/document_full.json ',
    force: true,
  }, deps)

  assert.deepEqual(calls, [{ market: 'EU', path: 'task-1/document_full.json', ddl: true, force: true }])
  assert.equal(formatMarketDocumentFullImportOutput(result.result), 'document_full imported\n')
  assert.equal(result.output, 'document_full imported\n')
})

test('runMarketPackageVectorDryRunAction serializes summary output', async () => {
  const deps = makeDeps({
    runVectorIngest: async (market, packagePath, dryRun, force) => {
      assert.equal(market, 'KR')
      assert.equal(packagePath, 'KR/pkg')
      assert.equal(dryRun, true)
      assert.equal(force, true)
      return { ok: true, summary: { chunks: 12, dry_run: true } }
    },
  })

  const result = await runMarketPackageVectorDryRunAction({ market: 'KR', packagePath: 'KR/pkg', force: true }, deps)

  assert.equal(result.output, formatMarketPackageVectorOutput({ summary: { chunks: 12, dry_run: true } }))
  assert.match(result.output, /"chunks": 12/)
})
