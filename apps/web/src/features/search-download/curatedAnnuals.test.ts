/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { ReportItem } from './model.ts'

const {
  buildCuratedAnnualsApplyResult,
  buildCuratedAnnualsRequestPlan,
  canLoadCuratedAnnuals,
} = await import('./curatedAnnuals.ts')

const toyotaAnnual = {
  title: 'Toyota annual report',
  report_type: 'annual',
  report_end: '2025-03-31',
  published_at: '2025-06-30',
  document_url: 'https://example.com/toyota.pdf',
} satisfies ReportItem

const sonyAnnual = {
  title: 'Sony annual report',
  report_type: 'annual',
  report_end: '2025-03-31',
  published_at: '2025-06-29',
  document_url: 'https://example.com/sony.pdf',
} satisfies ReportItem

test('canLoadCuratedAnnuals gates curated sample loading to JP and KR', () => {
  assert.equal(canLoadCuratedAnnuals('JP'), true)
  assert.equal(canLoadCuratedAnnuals('KR'), true)
  assert.equal(canLoadCuratedAnnuals('US'), false)
  assert.equal(canLoadCuratedAnnuals('CN'), false)
})

test('buildCuratedAnnualsRequestPlan centralizes request params and loading log', () => {
  const plan = buildCuratedAnnualsRequestPlan('JP', '2025')

  assert.equal(plan.params.toString(), 'market=JP&report_year=2025&limit=10')
  assert.equal(plan.loadingLog, '正在载入 日本市场 主流 10 家年报样本 (2025)')
})

test('buildCuratedAnnualsRequestPlan supports explicit sample limits', () => {
  const plan = buildCuratedAnnualsRequestPlan('KR', '2024', 3)

  assert.equal(plan.params.toString(), 'market=KR&report_year=2024&limit=3')
  assert.equal(plan.loadingLog, '正在载入 韩国市场 主流 3 家年报样本 (2024)')
})

test('buildCuratedAnnualsApplyResult dedupes reports and preselects downloads', () => {
  const result = buildCuratedAnnualsApplyResult('JP', [
    toyotaAnnual,
    sonyAnnual,
    { ...toyotaAnnual, title: 'Toyota duplicate annual report' },
  ])

  assert.deepEqual(result.reports, [toyotaAnnual, sonyAnnual])
  assert.deepEqual([...result.selected], [
    'https://example.com/toyota.pdf',
    'https://example.com/sony.pdf',
  ])
  assert.deepEqual(result.companyInfo, {
    name: '日本市场主流公司年报样本',
    ticker: '',
    curated: true,
  })
  assert.equal(result.successLog, '已载入 2 家日本市场主流公司年报，并自动勾选')
})
