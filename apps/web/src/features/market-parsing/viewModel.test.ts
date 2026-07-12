/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  MARKET_PARSING_SECTION_IDS,
  buildMarketParsingPageViewModel,
  buildMarketParsingStateScopeKey,
  hasMarketParsingLogIssues,
} = await import('./viewModel.ts')

test('market parsing section ids preserve mobile tab order', () => {
  assert.deepEqual(MARKET_PARSING_SECTION_IDS, ['pdf-upload', 'pdf-status', 'pdf-result', 'pdf-source', 'pdf-tasks'])
})

test('market parsing state scope prefers document and package identity over task cache keys', () => {
  assert.equal(buildMarketParsingStateScopeKey({ market: 'HK', taskId: 'task-1' }), 'HK::task-1')
  assert.equal(buildMarketParsingStateScopeKey({
    market: 'US',
    taskId: 'task-older',
    packagePath: 'data/wiki/us/companies/NVDA/reports/2025-10-K-0001045810-25-000023',
  }), 'US::data/wiki/us/companies/NVDA/reports/2025-10-K-0001045810-25-000023')
  assert.equal(buildMarketParsingStateScopeKey({
    market: 'US',
    taskId: 'task-older',
    packagePath: 'data/wiki/us/companies/NVDA/reports/2025-10-K-0001045810-25-000023',
    documentFullPath: 'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json',
  }), 'US::data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json')
  assert.equal(buildMarketParsingStateScopeKey({ market: 'EU' }), '')
})

test('view model derives completed workflow, result gate, and empty state flags', () => {
  const completed = buildMarketParsingPageViewModel({
    market: 'CN',
    parseBadgeClass: 'completed',
    resultDeferred: true,
    markdown: '',
    parseActive: false,
    taskCount: 0,
    logs: [],
    logsExpanded: false,
  })

  assert.equal(completed.isCompleted, true)
  assert.equal(completed.shouldShowWorkflow, true)
  assert.equal(completed.shouldShowResultGate, true)
  assert.equal(completed.shouldShowEmptyState, true)
  assert.equal(completed.canBuildDownloadedPackage, false)

  const running = buildMarketParsingPageViewModel({
    market: 'EU',
    parseBadgeClass: 'running',
    resultDeferred: false,
    markdown: '# parsed',
    parseActive: true,
    taskCount: 2,
    logs: [],
    logsExpanded: false,
  })

  assert.equal(running.isCompleted, false)
  assert.equal(running.shouldShowWorkflow, false)
  assert.equal(running.shouldShowResultGate, false)
  assert.equal(running.shouldShowEmptyState, false)
  assert.equal(running.canBuildDownloadedPackage, true)

  for (const market of ['HK', 'JP', 'KR'] as const) {
    const pdfOnly = buildMarketParsingPageViewModel({
      market,
      parseBadgeClass: 'queued',
      resultDeferred: false,
      markdown: '',
      parseActive: false,
      taskCount: 0,
      logs: [],
      logsExpanded: false,
    })
    assert.equal(pdfOnly.canBuildDownloadedPackage, false, `${market} must stay PDF-only`)
  }
})

test('view model keeps log risk and expanded display derived together', () => {
  const okLogs = [{ time: '2026-07-03T01:00:00Z', level: 'info', message: 'ok' }]
  const warnLogs = [...okLogs, { time: '2026-07-03T01:01:00Z', level: 'warn', message: 'check source' }]

  assert.equal(hasMarketParsingLogIssues(okLogs), false)
  assert.equal(hasMarketParsingLogIssues(warnLogs), true)

  const collapsed = buildMarketParsingPageViewModel({
    parseBadgeClass: 'queued',
    resultDeferred: false,
    markdown: '',
    parseActive: false,
    taskCount: 1,
    logs: okLogs,
    logsExpanded: false,
  })
  assert.equal(collapsed.hasLogIssues, false)
  assert.equal(collapsed.shouldShowLogs, false)
  assert.equal(collapsed.logToggleText, '展开 1 条')
  assert.equal(collapsed.logDescription, '默认收起，保留解析过程可审计记录。')

  const withWarning = buildMarketParsingPageViewModel({
    parseBadgeClass: 'queued',
    resultDeferred: false,
    markdown: '',
    parseActive: false,
    taskCount: 1,
    logs: warnLogs,
    logsExpanded: false,
  })
  assert.equal(withWarning.hasLogIssues, true)
  assert.equal(withWarning.shouldShowLogs, true)
  assert.equal(withWarning.logToggleText, '收起日志')
  assert.equal(withWarning.logDescription, '解析过程中有需要关注的提示。')
})
