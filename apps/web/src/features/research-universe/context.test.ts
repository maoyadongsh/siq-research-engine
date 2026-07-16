/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { buildResearchAgentContext } from './context.ts'
import type { GeneratedArtifactOption, ResearchCompanyOption, SourceReportOption } from './types.ts'

const company: ResearchCompanyOption = {
  company_key: 'us-aapl',
  market: 'US',
  company_id: 'US:0000320193',
  company_wiki_id: 'AAPL-Apple-Inc',
  display_code: 'AAPL',
  display_name: 'Apple Inc',
  parsed_report_count: 1,
  readiness: { parsed_ready: true },
  capabilities: {},
  degraded_reasons: [],
}

function sourceReport(baseline?: string): SourceReportOption {
  return {
    report_id: '2025-10-K-0000320193-25-000079',
    label: '2025 10-K',
    report_type: 'annual',
    form_type: '10-K',
    fiscal_year: 2025,
    period_end: '2025-09-27',
    published_at: '2025-10-31',
    quality_status: 'warning',
    source_family: 'sec_ixbrl',
    document_format: 'ixbrl_html',
    research_identity: {
      market: 'US',
      company_id: 'US:0000320193',
      filing_id: 'US:0000320193:0000320193-25-000079',
      parse_run_id: 'parse-us-aapl-2025',
    },
    readiness: { parsed_ready: true },
    capabilities: { analysis_input_ready: true },
    degraded_reasons: ['financial_checks_warning'],
    baseline_analysis_artifact_id: baseline,
  }
}

const factcheckArtifact: GeneratedArtifactOption = {
  artifact_id: 'factcheck-aapl',
  artifact_type: 'factcheck',
  status: 'completed',
  source_report_id: '2025-10-K-0000320193-25-000079',
  identity_status: 'exact',
}

test('source report identity remains available when no generated analysis artifact exists', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport(),
    reportType: 'analysis',
    reportTitle: '智能分析',
    pageTitle: '智能分析',
  })

  assert.deepEqual(context.research_identity, sourceReport().research_identity)
  assert.equal(context.market, 'US')
  assert.equal(context.source_report?.report_id, '2025-10-K-0000320193-25-000079')
  assert.equal(context.artifact, undefined)
  assert.equal(context.capabilities.analysis_baseline_available, false)
})

test('factcheck context uses only the exact source report analysis baseline', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport('analysis-aapl-exact'),
    artifact: factcheckArtifact,
    reportType: 'factcheck',
    reportTitle: '事实核查',
    pageTitle: '事实核查',
  })

  assert.equal(context.upstream_analysis_artifact_id, 'analysis-aapl-exact')
  assert.equal(context.source_report?.source_family, 'sec_ixbrl')
  assert.equal(context.artifact?.artifact_id, 'factcheck-aapl')
  assert.notEqual(context.upstream_analysis_artifact_id, factcheckArtifact.artifact_id)
  assert.equal(context.capabilities.analysis_baseline_available, true)
})

test('factcheck does not promote its current artifact when the analysis baseline is missing', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport(),
    artifact: factcheckArtifact,
    reportType: 'factcheck',
    reportTitle: '事实核查',
    pageTitle: '事实核查',
  })

  assert.equal(context.upstream_analysis_artifact_id, undefined)
  assert.equal(context.capabilities.analysis_baseline_available, false)
})

test('tracking context carries the exact analysis baseline independently of tracking output', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport('analysis-aapl-exact'),
    artifact: {
      ...factcheckArtifact,
      artifact_id: 'tracking-aapl-current',
      artifact_type: 'tracking',
    },
    reportType: 'tracking',
    reportTitle: '持续跟踪',
    pageTitle: '持续跟踪',
  })

  assert.equal(context.upstream_analysis_artifact_id, 'analysis-aapl-exact')
  assert.equal(context.artifact?.artifact_id, 'tracking-aapl-current')
  assert.notEqual(context.upstream_analysis_artifact_id, context.artifact?.artifact_id)
})

test('artifact context fails closed when identity status or source report id is missing', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport('analysis-aapl-exact'),
    artifact: { ...factcheckArtifact, identity_status: undefined, source_report_id: undefined },
    reportType: 'factcheck',
    reportTitle: '事实核查',
    pageTitle: '事实核查',
  })

  assert.equal(context.artifact, undefined)
})

test('legacy unbound artifact can be previewed but is excluded from Agent artifact context', () => {
  const context = buildResearchAgentContext({
    company,
    sourceReport: sourceReport('analysis-aapl-exact'),
    artifact: { ...factcheckArtifact, identity_status: 'legacy_unbound' },
    reportType: 'factcheck',
    reportTitle: '事实核查',
    pageTitle: '事实核查',
  })

  assert.equal(context.artifact, undefined)
  assert.equal(context.upstream_analysis_artifact_id, 'analysis-aapl-exact')
})
