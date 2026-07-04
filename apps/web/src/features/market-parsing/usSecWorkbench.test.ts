/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import type { DownloadedPdf } from '../../lib/pdfTypes.ts'
import type { UsSecCaseSetStatus } from './api.ts'

const {
  deriveUsSecDownloadedRows,
  deriveUsSecParseStatus,
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
    package_path: 'data/wiki/us_sec/NVDA/2025/10-K_0001045810-25-000023',
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
  assert.equal(rows[0].packagePath, 'data/wiki/us_sec/NVDA/2025/10-K_0001045810-25-000023')
})
