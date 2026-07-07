/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import type { DownloadedPdf } from '../../lib/pdfTypes.ts'
import type { UsSecCaseSetStatus } from './api.ts'

const {
  deriveUsSecArtifactManifest,
  deriveUsSecDownloadedRows,
  deriveUsSecParseStatus,
  deriveUsSecQualitySummary,
  deriveUsSecRecentTasks,
  deriveUsSecWorkflowSummary,
  findUsSecCaseItem,
  usSecDocumentKind,
} = await import('./usSecWorkbench.ts')

function report(overrides: Partial<DownloadedPdf> = {}): DownloadedPdf {
  return {
    id: 'nvda-10k',
    market: 'US',
    company: 'NVIDIA',
    companyName: 'NVIDIA Corporation',
    ticker: 'NVDA',
    category: '10-K',
    filename: 'nvidia-2025-10k.htm',
    relativePath: 'US/NVIDIA/2025/nvidia-2025-10k.htm',
    size: 1234,
    mtime: '2026-06-27T08:00:00.000Z',
    url: '/api/downloads/report-file/US/NVIDIA/2025/nvidia-2025-10k.htm',
    contentType: 'text/html',
    isPdf: false,
    form: '10-K',
    reportType: '10-K',
    reportFamily: 'annual',
    reportEnd: '2025-01-31',
    publishedAt: '2025-03-18',
    accessionNumber: '0001045810-25-000023',
    ...overrides,
  }
}

const status: UsSecCaseSetStatus = {
  company_count: 1,
  items: [{
    ticker: 'NVDA',
    company_name: 'NVIDIA Corporation',
    fiscal_year: 2025,
    period_end: '2025-01-31',
    filing_date: '2025-03-18',
    quality_status: 'pass',
    package_path: 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023',
  }],
  ingest_report: {
    package_count: 1,
    summary: {
      xbrl_facts: 120,
      normalized_metrics: 20,
      sections: 8,
      tables: 5,
      evidence_items: 180,
      quality: { pass: 1 },
    },
  },
}

const packageDetail = {
  package_path: 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023',
  manifest: {
    ticker: 'NVDA',
    company_name: 'NVIDIA Corporation',
    form: '10-K',
    period_end: '2025-01-31',
    filing_date: '2025-03-18',
  },
  counts: {
    sections: 8,
    tables: 5,
    metrics: 20,
    evidence: 180,
    dimension_metrics: 3,
  },
  quality: {
    status: 'pass',
    missing_core_sections: ['risk_factors'],
    evidence_coverage_ratio: 0.875,
    evidence_resolvability_ratio: 0.75,
    unresolvable_evidence_count: 2,
  },
  bridge_checks: {
    overall_status: 'pass',
    summary: { pass: 10, warning: 1, fail: 0, skipped: 2 },
    checks: [],
  },
  sections: [
    { section_id: 'business', file: 'business.md' },
    { section_id: 'risk_factors', file: 'risk_factors.md' },
  ],
  metrics: [
    { metric_id: 'revenue', canonical_name: 'Revenue', concept: 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' },
  ],
  dimension_metrics: [{ metric_id: 'segment-revenue' }],
}

test('usSecDocumentKind labels SEC files', () => {
  assert.equal(usSecDocumentKind(report()), 'HTML')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.xhtml', contentType: 'application/xhtml+xml' })), 'iXBRL')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.xml', contentType: 'application/xml' })), 'XML')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.zip', contentType: 'application/zip' })), 'ZIP')
  assert.equal(usSecDocumentKind(report({ filename: 'proxy.pdf', contentType: 'application/pdf', isPdf: true })), 'PDF')
})

test('findUsSecCaseItem prefers accession, ticker, and period', () => {
  const item = findUsSecCaseItem(report(), status)
  assert.equal(item?.ticker, 'NVDA')
  assert.match(String(item?.package_path), /0001045810-25-000023/)
})

test('deriveUsSecParseStatus maps package and import states', () => {
  const matched = status.items?.[0]
  assert.equal(deriveUsSecParseStatus({ report: report(), item: null, status }), 'unparsed')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status: null }), 'package_ready')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status }), 'postgres_ready')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'warning' }, status }), 'warning')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'fail' }, status }), 'failed')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status, busyPath: report().relativePath }), 'building')
})

test('deriveUsSecDownloadedRows exposes list row metadata', () => {
  const rows = deriveUsSecDownloadedRows([report()], status, '')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].ticker, 'NVDA')
  assert.equal(rows[0].form, '10-K')
  assert.equal(rows[0].fileType, 'HTML')
  assert.equal(rows[0].parseStatus, 'postgres_ready')
  assert.equal(rows[0].packagePath, 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023')
})

test('deriveUsSecRecentTasks exposes parsed SEC packages as shared PDF-surface tasks', () => {
  const rows = deriveUsSecRecentTasks(status)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].id, 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023')
  assert.equal(rows[0].ticker, 'NVDA')
  assert.equal(rows[0].companyName, 'NVIDIA Corporation')
  assert.equal(rows[0].form, '10-K')
  assert.equal(rows[0].periodEnd, '2025-01-31')
  assert.equal(rows[0].status, 'postgres_ready')
  assert.equal(rows[0].statusText, 'PostgreSQL 已入库')
})

test('deriveUsSecArtifactManifest maps SEC package outputs to result chips', () => {
  const manifest = deriveUsSecArtifactManifest(packageDetail)
  assert.equal(manifest.readyCount, 8)
  assert.equal(manifest.total, 8)
  assert.deepEqual(manifest.chips.map((chip) => chip.name), [
    'manifest.json',
    'sections/*.md',
    'tables.json',
    'xbrl_facts.json',
    'normalized_metrics.json',
    'evidence_map.json',
    'quality_report.json',
    'bridge_checks.json',
  ])
  assert.equal(manifest.checks[0].label, 'SEC 解析产物包')
  assert.equal(manifest.checks[0].status, 'ready')
})

test('deriveUsSecWorkflowSummary exposes the four-stage US pipeline', () => {
  const workflow = deriveUsSecWorkflowSummary(status, packageDetail)
  assert.deepEqual(workflow.steps.map((step) => step.label), ['解析产物包', '派生知识资产', '语义层', 'PostgreSQL'])
  assert.equal(workflow.cards[0].status, 'ready')
  assert.equal(workflow.cards[1].status, 'ready')
  assert.equal(workflow.cards[2].status, 'pending')
  assert.equal(workflow.cards[3].status, 'ready')
})

test('deriveUsSecQualitySummary formats SEC quality metrics for the result panel', () => {
  const quality = deriveUsSecQualitySummary(packageDetail)
  assert.deepEqual(quality.tiles.map((tile) => tile.label), ['Sections', 'Tables', 'Metrics', 'Evidence', '证据字段覆盖', '证据可回链', 'Dimensions'])
  assert.equal(quality.tiles[0].value, '8')
  assert.equal(quality.tiles[4].value, '87.5%')
  assert.equal(quality.tiles[5].value, '75% · 不可回链 2')
  assert.equal(quality.bridgeStatus, 'pass')
  assert.equal(quality.bridgeCounts.pass, 10)
  assert.equal(quality.missingCoreSections[0], 'risk_factors')
})

test('deriveUsSecQualitySummary falls back to quality gates for resolvability metrics', () => {
  const quality = deriveUsSecQualitySummary({
    ...packageDetail,
    quality: { status: 'warning' },
    quality_gates: {
      evidence_coverage_ratio: 0.5,
      evidence_resolvability_ratio: 0,
      unresolvable_evidence_count: 4,
    },
  })

  assert.equal(quality.tiles.find((tile) => tile.label === '证据字段覆盖')?.value, '50%')
  assert.equal(quality.tiles.find((tile) => tile.label === '证据可回链')?.value, '0% · 不可回链 4')
})
