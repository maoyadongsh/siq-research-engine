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
    retrieval_status: 'ready',
    wiki_ready: true,
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
    artifacts: {
      document_full: 'parser/document_full.json',
      report_complete: 'parser/report_complete.md',
      content_list_enhanced: 'parser/content_list_enhanced.json',
      table_relations: 'parser/table_relations.json',
      wiki_report_complete: 'sections/report_complete.md',
      sections: 'sections.json',
      table_index: 'tables/table_index.json',
      xbrl_facts_raw: 'xbrl/facts_raw.json',
      xbrl_contexts: 'xbrl/contexts.json',
      xbrl_units: 'xbrl/units.json',
      xbrl_labels: 'xbrl/labels.json',
      xbrl_taxonomy_summary: 'xbrl/taxonomy_summary.json',
      financial_data: 'metrics/financial_data.json',
      financial_checks: 'metrics/financial_checks.json',
      normalized_metrics: 'metrics/normalized_metrics.json',
      operating_metrics: 'metrics/operating_metrics.json',
      quality_report: 'qa/quality_report.json',
      source_map: 'qa/source_map.json',
      extraction_warnings: 'qa/extraction_warnings.json',
    },
    artifact_hashes: {
      'parser/document_full.json': 'sha-document-full',
      'parser/report_complete.md': 'sha-report-complete',
      'parser/content_list_enhanced.json': 'sha-content-list-enhanced',
      'parser/table_relations.json': 'sha-table-relations',
      'sections/report_complete.md': 'sha-wiki-report-complete',
      'sections.json': 'sha-sections',
      'tables/table_index.json': 'sha-table-index',
      'xbrl/facts_raw.json': 'sha-facts',
      'xbrl/contexts.json': 'sha-contexts',
      'xbrl/units.json': 'sha-units',
      'xbrl/labels.json': 'sha-labels',
      'xbrl/taxonomy_summary.json': 'sha-taxonomy',
      'metrics/financial_data.json': 'sha-financial-data',
      'metrics/financial_checks.json': 'sha-financial-checks',
      'metrics/normalized_metrics.json': 'sha-normalized',
      'metrics/operating_metrics.json': 'sha-operating',
      'qa/quality_report.json': 'sha-quality',
      'qa/source_map.json': 'sha-source-map',
      'qa/extraction_warnings.json': 'sha-warnings',
    },
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
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'warning', retrieval_status: 'needs_review', wiki_ready: false }, status }), 'warning')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'warning', retrieval_status: 'ready', wiki_ready: true }, status }), 'postgres_ready')
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
  assert.equal(manifest.readyCount, 20)
  assert.equal(manifest.total, 20)
  assert.deepEqual(manifest.chips.map((chip) => chip.name), [
    'manifest.json',
    'parser/document_full.json',
    'parser/report_complete.md',
    'parser/content_list_enhanced.json',
    'parser/table_relations.json',
    'sections/report_complete.md',
    'sections.json',
    'tables/table_index.json',
    'xbrl/facts_raw.json',
    'xbrl/contexts.json',
    'xbrl/units.json',
    'xbrl/labels.json',
    'xbrl/taxonomy_summary.json',
    'metrics/financial_data.json',
    'metrics/financial_checks.json',
    'metrics/normalized_metrics.json',
    'metrics/operating_metrics.json',
    'qa/quality_report.json',
    'qa/source_map.json',
    'qa/extraction_warnings.json',
  ])
  assert.equal(manifest.checks[0].label, 'SEC 解析产物包')
  assert.equal(manifest.checks[0].status, 'ready')
})

test('deriveUsSecWorkflowSummary exposes the four-stage US pipeline', () => {
  const workflow = deriveUsSecWorkflowSummary(status, packageDetail)
  assert.deepEqual(workflow.steps.map((step) => step.label), ['解析产物包', '派生知识资产', 'Wiki 语义增强', 'PostgreSQL'])
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
