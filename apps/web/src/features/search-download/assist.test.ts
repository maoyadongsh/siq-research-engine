/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { AssistResult, CandidateExplanation } from './model.ts'

const {
  buildAssistIntentChips,
  buildAssistSearchPlan,
  recommendedCandidateUrls,
} = await import('./assist.ts')

test('buildAssistSearchPlan derives smart search targets from intent', () => {
  const result = {
    intent: {
      market: 'US',
      company_query: 'Apple Inc.',
      ticker: 'AAPL',
      company_id: '0000320193',
      report_year: 2025,
      report_types: ['annual', '10-K'],
    },
  } satisfies AssistResult

  const plan = buildAssistSearchPlan(result, {
    currentMarket: 'CN',
    currentYear: '2024',
    currentMarketFilter: '',
    smartPrompt: '找苹果年报',
  })

  assert.equal(plan.targetMarket, 'US')
  assert.equal(plan.targetYear, '2025')
  assert.equal(plan.nextQuery, 'AAPL')
  assert.equal(plan.targetQuery, 'Apple Inc.')
  assert.equal(plan.targetTicker, 'AAPL')
  assert.equal(plan.targetCompanyId, '0000320193')
  assert.equal(plan.targetCountry, undefined)
  assert.deepEqual(plan.reportTypes, ['annual', '10-K'])
  assert.equal(plan.understoodLog, '已理解: 美国市场 · Apple Inc. · AAPL / 0000320193 / annual+10-K')
})

test('buildAssistSearchPlan falls back to current market and prompt', () => {
  const result = {
    intent: {
      market: 'BAD',
    },
  } as unknown as AssistResult

  const plan = buildAssistSearchPlan(result, {
    currentMarket: 'HK',
    currentYear: '2026',
    currentMarketFilter: '',
    smartPrompt: '美团 2025 年报',
  })

  assert.equal(plan.targetMarket, 'HK')
  assert.equal(plan.targetYear, '2026')
  assert.equal(plan.nextQuery, '')
  assert.equal(plan.targetQuery, '美团 2025 年报')
  assert.equal(plan.understoodLog, '已理解: 香港市场 · 美团 2025 年报 / 年报')
})

test('buildAssistSearchPlan carries EU country filter only for EU searches', () => {
  const result = {
    intent: {
      market: 'EU',
      ticker: 'ASML',
    },
  } satisfies AssistResult

  const plan = buildAssistSearchPlan(result, {
    currentMarket: 'US',
    currentYear: '2025',
    currentMarketFilter: 'NL',
    smartPrompt: 'ASML 年报',
  })

  assert.equal(plan.targetCountry, 'NL')
})

test('buildAssistIntentChips formats market, report types, and assistant mode', () => {
  const chips = buildAssistIntentChips(
    {
      market: 'US',
      ticker: 'MSFT',
      report_year: 2025,
      report_types: ['annual', '10-K', 'custom'],
    },
    {
      currentMarketLabel: '中国市场',
      currentQuery: '',
      currentYear: '2024',
      assistantMode: 'llm:openai:gpt',
      typeLabels: {
        annual: '年报',
        '10-K': '10-K',
      },
    },
  )

  assert.deepEqual(chips, [
    '市场：美国市场',
    '公司：MSFT',
    '年份：2025',
    '报告：年报 / 10-K / custom',
    '模式：模型增强',
  ])
})

test('buildAssistIntentChips uses visible fallbacks for sparse intent', () => {
  const chips = buildAssistIntentChips(
    {},
    {
      currentMarketLabel: '日本市场',
      currentQuery: '',
      currentYear: '2026',
      assistantMode: 'rules',
      typeLabels: {},
    },
  )

  assert.deepEqual(chips, [
    '市场：日本市场',
    '公司：待确认',
    '年份：2026',
    '报告：年报',
    '模式：规则辅助',
  ])
})

test('recommendedCandidateUrls returns only recommended document urls', () => {
  const explanations = [
    { document_url: 'a.pdf', title_zh: 'A', report_type_zh: '年报', period_zh: '2025', recommendation: '优先', recommended: true },
    { document_url: 'b.pdf', title_zh: 'B', report_type_zh: '季报', period_zh: '2025', recommendation: '备选' },
  ] satisfies CandidateExplanation[]

  assert.deepEqual(recommendedCandidateUrls(explanations), ['a.pdf'])
})
