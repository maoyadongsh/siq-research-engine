/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { CandidateExplanation, DownloadedPdf, MarketReportHealth, ReportItem } from './model.ts'

const {
  buildSearchDownloadViewModel,
  buildSearchDownloadYears,
} = await import('./viewModel.ts')

const annualReport = {
  title: 'Annual Report',
  report_type: 'annual',
  report_end: '2025-12-31',
  published_at: '2026-03-01',
  document_url: 'https://example.com/annual.pdf',
} satisfies ReportItem

const quarterlyReport = {
  title: 'Quarterly Report',
  report_type: 'quarterly',
  report_end: '2025-09-30',
  published_at: '2025-10-30',
  document_url: 'https://example.com/quarterly.pdf',
} satisfies ReportItem

test('buildSearchDownloadYears returns descending year options', () => {
  assert.deepEqual(buildSearchDownloadYears(2026, 4), ['2026', '2025', '2024', '2023'])
})

test('view model centralizes report counts, titles, logs, and candidate explanations', () => {
  const candidateExplanation = {
    document_url: annualReport.document_url,
    title_zh: '年度报告',
    report_type_zh: '年报',
    period_zh: '2025',
    recommendation: '优先下载',
    recommended: true,
  } satisfies CandidateExplanation

  const downloadedReport = {
    id: 'downloaded-1',
    company: 'Example',
    category: 'annual',
    filename: 'annual.pdf',
    relativePath: 'CN/annual.pdf',
    size: 1024,
    mtime: '2026-03-02',
    url: '/api/downloads/report-file?path=CN%2Fannual.pdf',
  } satisfies DownloadedPdf

  const viewModel = buildSearchDownloadViewModel({
    market: 'US',
    marketHealth: null,
    marketHealthLoading: false,
    annualReports: [annualReport],
    financialReports: [quarterlyReport],
    selected: new Set([annualReport.document_url]),
    downloadResults: [
      {
        title: 'Annual Report',
        report_type: 'annual',
        report_end: '2025-12-31',
        file_name: 'annual.pdf',
        saved_path: '/tmp/annual.pdf',
        size_bytes: 1024,
      },
    ],
    logs: [
      { time: '10:00:00', msg: 'ok', type: 'success' },
      { time: '10:01:00', msg: 'needs attention', type: 'warn' },
    ],
    visibleLogs: [
      { time: '10:01:00', msg: 'needs attention', type: 'warn' },
    ],
    downloadedReports: [downloadedReport],
    candidateExplanations: [candidateExplanation],
    currentYear: 2026,
  })

  assert.equal(viewModel.marketConfig.label, '美国市场')
  assert.deepEqual(viewModel.years.slice(0, 3), ['2026', '2025', '2024'])
  assert.equal(viewModel.annualTitle, '年度报告列表')
  assert.equal(viewModel.financialTitle, '定期披露列表（10-Q / 20-F / 6-K）')
  assert.equal(viewModel.totalCandidates, 2)
  assert.equal(viewModel.hasReports, true)
  assert.equal(viewModel.selectedCount, 1)
  assert.equal(viewModel.visibleDownloadResults.length, 1)
  assert.deepEqual(viewModel.visibleDownloadedReports, [downloadedReport])
  assert.equal(viewModel.candidateExplanationMap.get(annualReport.document_url), candidateExplanation)
  assert.deepEqual(viewModel.visibleLogs.map((log) => log.msg), ['needs attention'])
  assert.equal(viewModel.hasProblemLogs, true)
})

test('view model derives market source display for configurable official sources', () => {
  const marketHealth = {
    report_finder: {
      markets: {
        JP: {
          official_source: 'EDINET',
          report_search_ready: false,
          required_config: ['EDINET_API_KEY'],
        },
      },
    },
  } satisfies MarketReportHealth

  const viewModel = buildSearchDownloadViewModel({
    market: 'JP',
    marketHealth,
    marketHealthLoading: false,
    annualReports: [],
    financialReports: [],
    selected: new Set(),
    downloadResults: [],
    logs: [],
    downloadedReports: [],
    candidateExplanations: [],
    currentYear: 2026,
  })

  assert.equal(viewModel.activeMarketSource?.official_source, 'EDINET')
  assert.equal(viewModel.activeMarketSourceDisplay.searchBlocked, true)
  assert.equal(viewModel.activeMarketSourceDisplay.className, 'smart-search-source is-warning')
  assert.match(viewModel.activeMarketSourceDisplay.message, /EDINET_API_KEY/)
  assert.equal(viewModel.hasReports, false)
})
