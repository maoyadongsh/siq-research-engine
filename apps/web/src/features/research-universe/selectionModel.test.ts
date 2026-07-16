/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { DISCLOSURE_MARKET_ORDER } from '@/lib/marketMetadata.ts'
import {
  applyResearchSelectionToSearchParams,
  buildResearchShareUrl,
  orderedResearchMarkets,
  readRequestedResearchSelection,
  researchSelectionReducer,
  resolveInitialArtifact,
  resolveInitialCompany,
  resolveInitialMarket,
} from './selectionModel.ts'
import type { ResearchCompanyOption, ResearchMarketOption } from './types.ts'

function marketOption(market: ResearchMarketOption['market'], enabled = true): ResearchMarketOption {
  return {
    market,
    label: `${market} server label`,
    order: 99,
    enabled,
    company_count: 1,
    capabilities: {},
    degraded_reasons: [],
  }
}

function companyOption(companyKey: string, wikiId: string): ResearchCompanyOption {
  const isSaic = wikiId === '600104-上汽集团'
  return {
    company_key: companyKey,
    market: 'CN',
    company_id: `CN:${companyKey}`,
    company_wiki_id: wikiId,
    display_code: isSaic ? '600104' : '000333',
    display_name: isSaic ? '上汽集团' : '美的集团',
    parsed_report_count: 1,
    readiness: {},
    capabilities: {},
    degraded_reasons: [],
  }
}

test('market, company and report changes clear every stale downstream selection atomically', () => {
  const full = { market: 'US' as const, companyKey: 'us-aapl', reportId: '2025-10-k', artifactId: 'analysis-aapl' }

  assert.deepEqual(researchSelectionReducer(full, { type: 'select-market', market: 'HK' }), {
    market: 'HK', companyKey: '', reportId: '', artifactId: '',
  })
  assert.deepEqual(researchSelectionReducer(full, { type: 'select-company', companyKey: 'us-msft' }), {
    market: 'US', companyKey: 'us-msft', reportId: '', artifactId: '',
  })
  assert.deepEqual(researchSelectionReducer(full, { type: 'select-report', reportId: '2025-10-q' }), {
    market: 'US', companyKey: 'us-aapl', reportId: '2025-10-q', artifactId: '',
  })
})

test('markets use the shared disclosure order instead of API response order', () => {
  const reversed = DISCLOSURE_MARKET_ORDER.toReversed().map((market) => marketOption(market))

  assert.deepEqual(orderedResearchMarkets(reversed).map((option) => option.market), DISCLOSURE_MARKET_ORDER)
})

test('secondary-market first screen defaults to CN SAIC when no URL selection is present', () => {
  const requested = readRequestedResearchSelection(new URLSearchParams())
  const markets = [marketOption('US'), marketOption('CN')]
  const companies = [
    companyOption('cn-midea-key', '000333-美的集团'),
    companyOption('cn-saic-key', '600104-上汽集团'),
  ]

  assert.equal(resolveInitialMarket(markets, requested), 'CN')
  assert.equal(resolveInitialCompany(companies, requested, 'CN'), 'cn-saic-key')
})

test('explicit URL selection wins over the SAIC first-screen default', () => {
  const requested = readRequestedResearchSelection(new URLSearchParams('market=US&company_key=us-aapl'))
  const markets = [marketOption('CN'), marketOption('US')]
  const companies = [{ ...companyOption('us-aapl', 'AAPL-Apple-Inc'), market: 'US' as const }]

  assert.equal(resolveInitialMarket(markets, requested), 'US')
  assert.equal(resolveInitialCompany(companies, requested, 'US'), 'us-aapl')
})

test('legacy company and result params are scoped to CN and resolve by CN wiki id', () => {
  const requested = readRequestedResearchSelection(new URLSearchParams('company=000333-%E7%BE%8E%E7%9A%84%E9%9B%86%E5%9B%A2&result=old.html'))
  const markets = [marketOption('US'), marketOption('CN')]
  const companies = [companyOption('cn-midea-key', '000333-美的集团')]

  assert.equal(resolveInitialMarket(markets, requested), 'CN')
  assert.equal(resolveInitialCompany(companies, requested, 'CN'), 'cn-midea-key')
  assert.equal(resolveInitialArtifact([{
    artifact_id: 'legacy_opaque_id',
    artifact_type: 'analysis',
    status: 'legacy_unbound',
    identity_status: 'legacy_unbound',
    filename: 'old.html',
  }], requested), 'legacy_opaque_id')
})

test('new share params preserve exact source identity selection and remove legacy params', () => {
  const current = new URLSearchParams('company=legacy&result=old.html&keep=1')
  const selection = { market: 'US' as const, companyKey: 'us-aapl', reportId: '2025-10-k', artifactId: 'analysis-aapl' }

  const next = applyResearchSelectionToSearchParams(current, selection)
  assert.equal(next.get('market'), 'US')
  assert.equal(next.get('company_key'), 'us-aapl')
  assert.equal(next.get('report_id'), '2025-10-k')
  assert.equal(next.get('artifact_id'), 'analysis-aapl')
  assert.equal(next.get('company'), null)
  assert.equal(next.get('result'), null)
  assert.equal(next.get('keep'), '1')
  assert.equal(
    buildResearchShareUrl('https://siq.example', '/analysis', selection),
    'https://siq.example/analysis?market=US&company_key=us-aapl&report_id=2025-10-k&artifact_id=analysis-aapl',
  )
})
