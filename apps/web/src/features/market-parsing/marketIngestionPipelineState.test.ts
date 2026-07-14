/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { WIKI_INPUT_ARTIFACTS, type ArtifactsMap, type WorkflowStatus } from '../../lib/pdfTypes.ts'

const {
  derivePdfGenericMarketIngestionPipelineState,
  deriveMarketDocumentFullPostgresSummary,
  deriveUsSecMarketIngestionPipelineState,
} = await import('./marketIngestionPipelineState.ts')

function readyArtifacts(): ArtifactsMap {
  return Object.fromEntries(WIKI_INPUT_ARTIFACTS.map((name) => [name, { exists: true }]))
}

test('derivePdfGenericMarketIngestionPipelineState exposes four shared market steps', () => {
  const workflowStatus: WorkflowStatus = {
    artifactBundle: { status: 'ready', ready: true, readyCount: 5, total: 5 },
    documentFull: { status: 'ready' },
    wiki: { status: 'ready' },
    semantic: { status: 'pending' },
    database: { status: 'pending' },
  }

  const state = derivePdfGenericMarketIngestionPipelineState({
    workflowStatus,
    artifacts: readyArtifacts(),
  })

  assert.deepEqual(state.steps.map((step) => step.key), ['artifacts', 'wiki', 'semantic', 'postgres'])
  assert.deepEqual(state.steps.map((step) => step.label), ['解析产物', 'LLM-Wiki', 'Wiki语义增强', 'PostgreSQL'])
  assert.equal(state.artifactsReady, true)
  assert.equal(state.runAll.disabled, false)
  assert.equal(state.runAll.key, 'runAll')
  assert.deepEqual(state.actions.map((action) => action.key), ['wiki', 'semantic', 'postgres'])
  assert.equal(state.actions.find((action) => action.key === 'semantic')?.disabled, false)
  assert.equal(state.actions.find((action) => action.key === 'postgres')?.disabled, false)
  assert.equal(state.postgresSummary.ready, false)
})

test('derivePdfGenericMarketIngestionPipelineState derives generic button disabled and busy states', () => {
  const state = derivePdfGenericMarketIngestionPipelineState({
    workflowStatus: {
      artifactBundle: { status: 'ready', ready: true },
      wiki: { status: 'pending' },
      semantic: { status: 'pending' },
      database: { status: 'pending' },
    },
    artifacts: readyArtifacts(),
    workflowBusy: 'semantic-generic',
  })

  const wikiAction = state.actions.find((action) => action.key === 'wiki')
  const semanticAction = state.actions.find((action) => action.key === 'semantic')
  const postgresAction = state.actions.find((action) => action.key === 'postgres')

  assert.equal(state.activeStepIndex, 2)
  assert.equal(state.runAll.disabled, true)
  assert.equal(state.runAll.busy, false)
  assert.match(String(state.runAll.disabledReason), /Wiki语义增强入库正在执行/)
  assert.equal(wikiAction?.disabled, true)
  assert.match(String(wikiAction?.disabledReason), /Wiki语义增强入库正在执行/)
  assert.equal(semanticAction?.disabled, true)
  assert.equal(semanticAction?.busy, true)
  assert.match(String(semanticAction?.disabledReason), /Wiki语义增强入库正在执行/)
  assert.equal(postgresAction?.disabled, true)
  assert.match(String(postgresAction?.disabledReason), /Wiki语义增强入库正在执行/)
})

test('derivePdfGenericMarketIngestionPipelineState blocks generic imports until artifacts exist', () => {
  const state = derivePdfGenericMarketIngestionPipelineState({
    workflowStatus: { wiki: { status: 'ready' } },
    artifacts: {},
  })

  assert.equal(state.artifactsReady, false)
  assert.equal(state.runAll.disabled, true)
  assert.equal(state.runAll.key, 'runAll')
  assert.match(String(state.runAll.disabledReason), /核心 artifact/)
  assert.equal(state.actions.every((action) => action.disabled), true)
  assert.equal(state.actions.every((action) => /核心 artifact/.test(String(action.disabledReason))), true)
})

test('derivePdfGenericMarketIngestionPipelineState explains semantic dependency on LLM-Wiki', () => {
  const state = derivePdfGenericMarketIngestionPipelineState({
    workflowStatus: {
      artifactBundle: { status: 'ready', ready: true },
      wiki: { status: 'pending' },
      semantic: { status: 'pending' },
    },
    artifacts: readyArtifacts(),
  })

  const wikiAction = state.actions.find((action) => action.key === 'wiki')
  const semanticAction = state.actions.find((action) => action.key === 'semantic')

  assert.equal(state.runAll.disabled, false)
  assert.equal(state.runAll.disabledReason, undefined)
  assert.equal(wikiAction?.disabled, false)
  assert.equal(wikiAction?.disabledReason, undefined)
  assert.equal(semanticAction?.disabled, true)
  assert.match(String(semanticAction?.disabledReason), /LLM-Wiki/)
})

test('deriveUsSecMarketIngestionPipelineState disables PostgreSQL and run-all without document_full_path', () => {
  const state = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: true,
    artifactReadyCount: 4,
    artifactTotal: 4,
    wikiReady: true,
    semanticEvidence: 12,
    documentFullPath: '',
    taskId: 'nvda-task',
  })

  const postgresAction = state.actions.find((action) => action.key === 'postgres')

  assert.deepEqual(state.steps.map((step) => step.key), ['artifacts', 'wiki', 'semantic', 'postgres'])
  assert.equal(state.runAll.key, 'runAll')
  assert.equal(state.runAll.disabled, true)
  assert.match(String(state.runAll.disabledReason), /document_full\.json/)
  assert.equal(postgresAction?.disabled, true)
  assert.match(String(postgresAction?.disabledReason), /document_full\.json/)
  assert.equal(state.actions.find((action) => action.key === 'wiki')?.disabled, false)
  assert.equal(state.actions.find((action) => action.key === 'semantic')?.disabled, false)
})

test('deriveUsSecMarketIngestionPipelineState blocks wiki and run-all until artifacts exist', () => {
  const state = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: false,
    artifactReadyCount: 1,
    artifactTotal: 4,
    wikiReady: false,
    semanticEvidence: 0,
    documentFullPath: 'data/parser-results/us-sec/nvda/document_full.json',
    taskId: 'nvda-task',
  })

  const wikiAction = state.actions.find((action) => action.key === 'wiki')
  const semanticAction = state.actions.find((action) => action.key === 'semantic')

  assert.equal(wikiAction?.disabled, true)
  assert.match(String(wikiAction?.disabledReason), /解析产物/)
  assert.equal(semanticAction?.disabled, true)
  assert.match(String(semanticAction?.disabledReason), /LLM-Wiki/)
  assert.equal(state.runAll.disabled, true)
  assert.match(String(state.runAll.disabledReason), /解析产物/)
})

test('deriveUsSecMarketIngestionPipelineState blocks semantic until wiki is ready', () => {
  const state = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: true,
    artifactReadyCount: 4,
    artifactTotal: 4,
    wikiReady: false,
    semanticEvidence: 0,
    documentFullPath: 'data/parser-results/us-sec/nvda/document_full.json',
    taskId: 'nvda-task',
  })

  const wikiAction = state.actions.find((action) => action.key === 'wiki')
  const semanticAction = state.actions.find((action) => action.key === 'semantic')

  assert.equal(wikiAction?.disabled, false)
  assert.equal(semanticAction?.disabled, true)
  assert.match(String(semanticAction?.disabledReason), /LLM-Wiki/)
  assert.equal(state.runAll.disabled, false)
})

test('deriveUsSecMarketIngestionPipelineState enables document_full PostgreSQL actions with a path', () => {
  const state = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: true,
    artifactReadyCount: 4,
    artifactTotal: 4,
    wikiReady: true,
    semanticEvidence: 12,
    postgresStatus: {
      status: 'postgres_ready',
      schema: 'sec_us',
      parse_run_id: 'parse-us-1',
      parse_runs: 1,
      facts: 9,
      tables: 2,
      chunks: 3,
      evidence: 4,
    },
    documentFullPath: 'data/parser-results/us-sec/nvda/document_full.json',
    busyAction: 'postgres:nvda-task',
    taskId: 'nvda-task',
  })

  const postgresAction = state.actions.find((action) => action.key === 'postgres')

  assert.equal(state.steps[3].status, 'ready')
  assert.equal(state.steps[3].description, 'schema sec_us / parse_run_id parse-us-1；parse_runs 1 / facts 9 / tables 2 / chunks 3 / evidence 4')
  assert.equal(state.activeStepIndex, 3)
  assert.equal(postgresAction?.busy, true)
  assert.equal(postgresAction?.disabled, true)
  assert.equal(postgresAction?.disabledReason, undefined)
  assert.equal(state.runAll.disabledReason, undefined)
  assert.equal(state.runAll.key, 'runAll')
  assert.equal(state.postgresSummary.ready, true)
})

test('deriveUsSecMarketIngestionPipelineState accepts persisted semantic readiness without legacy chunks', () => {
  const state = deriveUsSecMarketIngestionPipelineState({
    artifactsReady: true,
    artifactReadyCount: 4,
    artifactTotal: 4,
    wikiReady: true,
    semanticEvidence: 0,
    semanticReady: true,
    semanticDescription: '规则语义 segments 5 / facts 0 / evidence 8',
    documentFullPath: 'data/parser-results/us-sec/nvda/document_full.json',
  })

  assert.equal(state.steps[2].status, 'ready')
  assert.equal(state.steps[2].description, '规则语义 segments 5 / facts 0 / evidence 8')
})

test('deriveMarketDocumentFullPostgresSummary requires parse runs facts tables chunks and evidence', () => {
  const summary = deriveMarketDocumentFullPostgresSummary({
    status: 'postgres_ready',
    schema: 'eu_ifrs',
    parse_run_id: 'parse-eu-1',
    parse_runs: 1,
    facts: 12,
    tables: 3,
    chunks: 8,
    evidence: 0,
    missing_counts: ['evidence'],
  })

  assert.equal(summary.ready, false)
  assert.equal(summary.status, 'warning')
  assert.deepEqual(summary.missingCounts, ['evidence'])
  assert.match(summary.description, /缺少 evidence/)
  assert.match(summary.description, /schema eu_ifrs/)
})

test('deriveMarketDocumentFullPostgresSummary accepts workflow camelCase selectors', () => {
  const summary = deriveMarketDocumentFullPostgresSummary({
    status: 'ready',
    schema: 'pdf2md_hk',
    parseRunId: 'parse-hk-1',
    parseRuns: 1,
    statementItems: 6,
    tables: 2,
    chunks: 3,
    evidence: 1,
  })

  assert.equal(summary.ready, true)
  assert.equal(summary.status, 'ready')
  assert.equal(summary.facts, 6)
  assert.equal(summary.parseRunId, 'parse-hk-1')
  assert.equal(summary.description, 'schema pdf2md_hk / parse_run_id parse-hk-1；parse_runs 1 / facts 6 / tables 2 / chunks 3 / evidence 1')
})

test('deriveMarketDocumentFullPostgresSummary treats hash mismatch as stale even when counts are complete', () => {
  const summary = deriveMarketDocumentFullPostgresSummary({
    status: 'stale',
    artifact_status: 'stale',
    schema: 'sec_us',
    parse_run_id: 'parse-us-old',
    parse_runs: 1,
    facts: 12,
    tables: 3,
    chunks: 8,
    evidence: 4,
    message: 'PostgreSQL contains an older document_full artifact',
  })

  assert.equal(summary.ready, false)
  assert.equal(summary.stale, true)
  assert.equal(summary.status, 'warning')
  assert.deepEqual(summary.missingCounts, [])
  assert.match(summary.description, /older document_full artifact/)
})
