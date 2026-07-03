/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  canWriteGeneratedRulingDrafts,
  disputeCountsFor,
  disputePositionCount,
  generatedRulingDraftsFor,
} = await import('./workflowViewModel.ts')

test('disputePositionCount prefers explicit position_count over positions length', () => {
  assert.equal(disputePositionCount({ position_count: 4, positions: [{}, {}] }), 4)
  assert.equal(disputePositionCount({ positions: [{}, {}, {}] }), 3)
  assert.equal(disputePositionCount({}), 0)
})

test('disputeCountsFor derives OpenClaw R1.5 dispute display counts', () => {
  const counts = disputeCountsFor([
    {
      dispute_id: 'DISP-001',
      topic: '估值是否支撑 Pre-IPO 定价',
      dimension: 'finance',
      severity: 'High',
      resolved: true,
      position_count: 2,
      positions: [{ agent_id: 'siq_ic_finance_auditor' }],
      chairman_ruling: {
        agent_id: 'siq_ic_chairman',
        decision: 'resolved_with_conditions',
      },
    },
    {
      dispute_id: 'DISP-002',
      topic: '核心客户续约确定性',
      dimension: 'business',
      severity: 'medium',
      resolved: false,
      positions: [
        { agent_id: 'siq_ic_sector_expert' },
        { agent_id: 'siq_ic_risk_controller' },
        { agent_id: 'siq_ic_chairman' },
      ],
    },
  ])

  assert.deepEqual(counts, {
    disputes: 2,
    resolved: 1,
    unresolved: 1,
    high_severity: 1,
    positions: 5,
    rulings: 1,
  })
})

test('disputeCountsFor returns zero counts for empty dispute lists', () => {
  assert.deepEqual(disputeCountsFor([]), {
    disputes: 0,
    resolved: 0,
    unresolved: 0,
    high_severity: 0,
    positions: 0,
    rulings: 0,
  })
})

test('generatedRulingDraftsFor normalizes chairman ruling generation payloads', () => {
  const drafts = generatedRulingDraftsFor({
    rulings: [
      {
        dispute_id: 'DISP-001',
        dispute: {
          topic: '估值与回购条款分歧',
        },
        ruling: {
          decision: 'resolved_with_conditions',
          rationale: 'Chairman requires valuation guardrails.',
          resolved: 'true',
          required_followups: ['Add valuation cap', 'Archive founder confirmation'],
          evidence_ids: 'EVID-001',
        },
      },
    ],
  })

  assert.deepEqual(drafts, [
    {
      dispute_id: 'DISP-001',
      topic: '估值与回购条款分歧',
      decision: 'resolved_with_conditions',
      rationale: 'Chairman requires valuation guardrails.',
      resolved: true,
      required_followups: ['Add valuation cap', 'Archive founder confirmation'],
      evidence_ids: ['EVID-001'],
    },
  ])
})

test('canWriteGeneratedRulingDrafts requires preview drafts, artifact readiness, confirmation, and idle state', () => {
  const preview = {
    rulings: [
      {
        dispute_id: 'DISP-001',
        ruling: { decision: 'resolved_with_conditions' },
      },
    ],
  }

  assert.equal(canWriteGeneratedRulingDrafts({
    preview,
    confirmed: true,
    busy: false,
    canPreviewRulings: true,
  }), true)
  assert.equal(canWriteGeneratedRulingDrafts({
    preview,
    confirmed: false,
    busy: false,
    canPreviewRulings: true,
  }), false)
  assert.equal(canWriteGeneratedRulingDrafts({
    preview,
    confirmed: true,
    busy: true,
    canPreviewRulings: true,
  }), false)
  assert.equal(canWriteGeneratedRulingDrafts({
    preview: { rulings: [] },
    confirmed: true,
    busy: false,
    canPreviewRulings: true,
  }), false)
  assert.equal(canWriteGeneratedRulingDrafts({
    preview,
    confirmed: true,
    busy: false,
    canPreviewRulings: false,
  }), false)
})
