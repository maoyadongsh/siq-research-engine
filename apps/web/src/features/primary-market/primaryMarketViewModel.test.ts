import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  deriveAgentReadinessChips,
  deriveAgentReadinessLine,
  buildMeetingEvents,
  buildSelectedAgentDryRunSummary,
  deriveCoordinatorNextActions,
  deriveMeetingAgentReadinessRows,
  deriveMeetingAgenda,
  deriveMeetingEventQualityChips,
  deriveMeetingReceiptRows,
  deriveMeetingScoreRows,
  deriveMeetingScoringSummary,
  deriveProjectMetrics,
  deriveProjectRow,
  R1_AGENT_SEQUENCE,
  validateHumanConfirmationDraft,
  validateR3Action,
} from './primaryMarketViewModel'
import type { DealStatusResponse, DealSummary, DealWorkflowResponse } from '@/lib/dealTypes'

test('deriveProjectRow prioritizes blocking status and preserves next action', () => {
  const deal: DealSummary = {
    deal_id: 'DEAL-001',
    company_name: 'Alpha Robotics',
    status: 'r1_in_progress',
    current_phase: 'R1',
  }
  const status: DealStatusResponse = {
    status: 'fail',
    ready_for_next_action: false,
    next_action: '补充财务模型 evidence',
    counts: { blocking: 2, warn: 1, missing: 3 },
    components: [
      { id: 'finance', label: '财务', status: 'fail', blocking: true, message: '缺少财务模型' },
    ],
  }

  const row = deriveProjectRow(deal, status)

  assert.equal(row.category, 'blocked')
  assert.equal(row.statusTone, 'error')
  assert.equal(row.nextAction, '补充财务模型 evidence')
  assert.deepEqual(row.blockingMessages, ['缺少财务模型'])
})

test('deriveProjectMetrics counts ready, blocked and pending decision projects', () => {
  const rows = [
    deriveProjectRow({ deal_id: 'a', company_name: 'A', status: 'draft' }, { ready_for_next_action: true }),
    deriveProjectRow({ deal_id: 'b', company_name: 'B', status: 'r1_in_progress' }, { counts: { blocking: 1 } }),
    deriveProjectRow({ deal_id: 'c', company_name: 'C', status: 'r1_in_progress', final_decision: 'pass' }, {}),
    deriveProjectRow({ deal_id: 'd', company_name: 'D', status: 'r4_completed' }, {}),
  ]

  const metrics = deriveProjectMetrics(rows)

  assert.equal(metrics.total, 4)
  assert.equal(metrics.active, 3)
  assert.equal(metrics.ready, 1)
  assert.equal(metrics.blocked, 1)
  assert.equal(metrics.decisionPending, 1)
  assert.equal(metrics.completed, 1)
})

test('meeting view model builds agenda and transcript from existing deal artifacts', () => {
  const workflow: DealWorkflowResponse = {
    workflow: {
      deal_id: 'DEAL-001',
      company_name: 'Alpha Robotics',
      status: 'r1_in_progress',
      current_phase: 'R1',
    },
    agent_reports: [
      {
        agent_id: 'siq_ic_finance_auditor',
        label: '财务审计委员',
        has_report: true,
        score: 82,
        recommendation: 'SUPPORT',
        summary: '收入质量可接受，但应补充回款明细。',
      },
    ],
  }
  const bundle = {
    workflow,
    preflight: {
      deal_id: 'DEAL-001',
      status: 'pass',
      checks: [{ id: 'meta', label: '项目基础信息', status: 'pass', message: 'ok' }],
    },
    disputes: {
      status: 'warn',
      counts: { disputes: 1, unresolved: 1 },
      disputes: [{ dispute_id: 'D1', topic: '估值口径', severity: 'medium', resolved: false, position_count: 2 }],
    },
    phaseArtifacts: {
      status: 'warn',
      phases: [
        { phase: 'R1', label: 'R1 专家首轮', status: 'pass', artifacts: { json: { available: true, path: 'phases/r1_reports.json' } } },
        { phase: 'R4', label: 'R4 投决草案', status: 'missing' },
      ],
    },
    startupReceipts: {
      siq_ic_finance_auditor: {
        receipt: {
          receipt_id: 'startup-finance-R1-001',
          agent_id: 'siq_ic_finance_auditor',
          evidence_hit_count: 3,
          shared_hits: 2,
          private_hits: 1,
          collections: ['siq_deal_shared'],
          physical_collections: ['ic_collaboration_shared'],
        },
      },
    },
  }

  const agenda = deriveMeetingAgenda(bundle)
  const events = buildMeetingEvents(bundle)

  assert.equal(agenda.find((item) => item.phase === 'R1')?.status, 'pass')
  assert.equal(agenda.find((item) => item.phase === 'R1.5')?.blocking, true)
  assert.ok(events.some((event) => event.type === 'receipt_generated' && event.meta?.includes('ic_collaboration_shared')))
  assert.ok(events.some((event) => event.type === 'agent_speech' && event.speaker === '财务审计委员'))
  assert.ok(events.some((event) => event.type === 'dispute_detected' && event.title === '估值口径'))
})

test('meeting receipt rows combine fetched receipts with readiness fallback', () => {
  const rows = deriveMeetingReceiptRows({
    workflow: {
      workflow: { deal_id: 'DEAL-001' },
      r1_agent_readiness: {
        agents: [
          {
            agent_id: 'siq_ic_strategist',
            label: '战略专家',
            allowed: false,
            startup_receipt_id: 'startup-strategy-fallback',
            warnings: ['need evidence'],
          },
        ],
      },
    },
    startupReceipts: {
      siq_ic_strategist: {
        receipt: {
          receipt_id: 'startup-strategy-R1-001',
          agent_id: 'siq_ic_strategist',
          evidence_hit_count: 5,
          shared_hits: 4,
          private_hits: 1,
          collections: ['siq_deal_shared', 'siq_ic_strategist'],
          physical_collections: ['ic_collaboration_shared', 'ic_strategist'],
          gaps: ['team background'],
        },
      },
    },
  })

  const strategist = rows.find((row) => row.agentId === 'siq_ic_strategist')

  assert.equal(rows.length, 6)
  assert.equal(strategist?.present, true)
  assert.equal(strategist?.allowed, false)
  assert.equal(strategist?.evidenceHits, 5)
  assert.deepEqual(strategist?.physicalCollections, ['ic_collaboration_shared', 'ic_strategist'])
  assert.deepEqual(strategist?.gaps, ['team background'])
  assert.deepEqual(strategist?.warnings, ['need evidence'])
})

test('meeting readiness chips show present and missing formal task state', () => {
  const bundle = {
    meetingReadiness: {
      schemaVersion: 'siq_primary_market_meeting_readiness_v1',
      dealId: 'DEAL-001',
      profiles: [
        {
          profileId: 'siq_ic_finance_auditor',
          label: '财务审计委员',
          role: 'finance',
          runtime: { health: 'running', port: 18664 },
          contract: {
            responsibilities: ['核验收入质量'],
            sourceFiles: ['IDENTITY.md', 'AGENTS.md'],
            outputs: [],
            boundaries: [],
          },
          startupReceipt: {
            present: true,
            receiptId: 'startup-finance-R1-001',
            sharedHits: 6,
            privateHits: 1,
            evidenceHits: 7,
            gaps: [],
          },
          r1Report: {
            present: false,
            score: null,
            recommendation: '',
            artifactPath: '',
          },
          quality: {
            readyForFormalTask: true,
            blockingReasons: [],
            warnings: [],
          },
        },
        {
          profileId: 'siq_ic_legal_scanner',
          label: '法务合规委员',
          role: 'legal',
          runtime: { health: 'disabled', port: 18665 },
          contract: {
            responsibilities: [],
            sourceFiles: [],
            outputs: [],
            boundaries: [],
          },
          startupReceipt: {
            present: false,
            receiptId: '',
            sharedHits: 0,
            privateHits: 0,
            evidenceHits: 0,
            gaps: ['term sheet'],
          },
          r1Report: {
            present: false,
            score: null,
            recommendation: '',
            artifactPath: '',
          },
          quality: {
            readyForFormalTask: false,
            blockingReasons: ['startup_receipt_missing'],
            warnings: ['profile source missing'],
          },
        },
      ],
      summary: {
        runtimeRunning: 1,
        receiptPresent: 1,
        r1ReportsPresent: 0,
        blockingProfiles: ['siq_ic_legal_scanner'],
      },
    },
  }

  const rows = deriveMeetingAgentReadinessRows(bundle)
  const finance = rows.find((row) => row.agentId === 'siq_ic_finance_auditor')
  const legal = rows.find((row) => row.agentId === 'siq_ic_legal_scanner')
  const financeChips = deriveAgentReadinessChips(bundle, 'siq_ic_finance_auditor')
  const legalChips = deriveAgentReadinessChips(bundle, 'siq_ic_legal_scanner')
  const committeeChips = deriveAgentReadinessChips(bundle, 'siq_ic_finance_auditor', 'committee')

  assert.equal(finance?.receiptPresent, true)
  assert.equal(legal?.receiptPresent, false)
  assert.deepEqual(financeChips.map((chip) => chip.label), ['Hermes running', 'Receipt present', 'R1 report missing', 'Profile loaded'])
  assert.equal(legalChips.find((chip) => chip.id === 'runtime')?.tone, 'error')
  assert.equal(legalChips.find((chip) => chip.id === 'receipt')?.label, 'Receipt missing')
  assert.equal(legalChips.find((chip) => chip.id === 'profile')?.label, 'Profile missing')
  assert.equal(committeeChips.find((chip) => chip.id === 'receipt')?.label, 'Receipts 1/6')
  assert.match(deriveAgentReadinessLine(bundle, 'siq_ic_finance_auditor'), /Receipt present/)
})

test('meeting quality transcript events derive compact warning chips', () => {
  const chips = deriveMeetingEventQualityChips({
    id: 'quality-1',
    phase: 'R1',
    type: 'quality_check',
    speaker: '财务审计委员',
    title: '回答质量检查',
    body: 'role.boundary=fail; evidence.reference=warn; next_action=pass',
    tone: 'error',
    agentId: 'siq_ic_finance_auditor',
    quality: {
      status: 'fail',
      checks: [
        { id: 'role.boundary', status: 'fail', detail: '越权' },
        { id: 'evidence.reference', status: 'warn', detail: '缺证据' },
        { id: 'next_action', status: 'pass', detail: '有建议' },
      ],
    },
  })

  assert.deepEqual(chips.map((chip) => chip.label), ['boundary warning', 'needs evidence', 'next action ok'])
  assert.equal(chips[0]?.tone, 'error')
  assert.equal(chips[1]?.tone, 'warning')
  assert.equal(chips[2]?.tone, 'success')
})

test('meeting score summary derives average, range and recommendation counts', () => {
  const rows = deriveMeetingScoreRows({
    workflow: {
      workflow: { deal_id: 'DEAL-001' },
      agent_reports: [
        { agent_id: 'siq_ic_finance_auditor', has_report: true, score: 80, recommendation: 'support', verified_count: 4, assumed_count: 1 },
        { agent_id: 'siq_ic_risk_controller', has_report: true, score: '60', recommendation: 'watch', verified_count: 2, assumed_count: 3 },
        { agent_id: 'siq_ic_legal_scanner', has_report: true, score: null, recommendation: 'reject' },
      ],
    },
  })
  const summary = deriveMeetingScoringSummary(rows)

  assert.equal(summary.count, 3)
  assert.equal(summary.scoredCount, 2)
  assert.equal(summary.average, 70)
  assert.equal(summary.min, 60)
  assert.equal(summary.max, 80)
  assert.equal(summary.supportCount, 1)
  assert.equal(summary.watchCount, 1)
  assert.equal(summary.opposeCount, 1)
})

test('coordinator recommends R0 material repair when preflight blocks the meeting', () => {
  const actions = deriveCoordinatorNextActions({
    preflight: {
      deal_id: 'DEAL-001',
      status: 'fail',
      checks: [
        { id: 'evidence', label: '证据覆盖', status: 'fail', message: '缺少财务 evidence' },
      ],
    },
  })

  assert.equal(actions[0]?.id, 'review_materials')
  assert.equal(actions[0]?.tone, 'error')
  assert.equal(actions.some((action) => action.id === 'r1_write'), false)
})

test('coordinator asks to generate missing receipts before R1 reports', () => {
  const actions = deriveCoordinatorNextActions({
    preflight: {
      deal_id: 'DEAL-001',
      status: 'pass',
      checks: [],
    },
    startupReceipts: {
      [R1_AGENT_SEQUENCE[0]]: {
        receipt: {
          receipt_id: 'startup-001',
          agent_id: R1_AGENT_SEQUENCE[0],
        },
      },
    },
  })

  assert.equal(actions[0]?.id, 'generate_receipts')
  assert.equal(actions[1]?.id, 'r1_dry')
})

test('coordinator recommends dispute identification after complete R1 reports', () => {
  const startupReceipts = Object.fromEntries(R1_AGENT_SEQUENCE.map((agentId) => [
    agentId,
    { receipt: { receipt_id: `startup-${agentId}`, agent_id: agentId } },
  ]))
  const actions = deriveCoordinatorNextActions({
    preflight: {
      deal_id: 'DEAL-001',
      status: 'pass',
      checks: [],
    },
    workflow: {
      workflow: { deal_id: 'DEAL-001', current_phase: 'R1' },
      agent_reports: R1_AGENT_SEQUENCE.map((agentId) => ({
        agent_id: agentId,
        has_report: true,
        score: 70,
        recommendation: 'support',
      })),
    },
    startupReceipts,
  })

  assert.equal(actions[0]?.id, 'identify_disputes_dry')
  assert.equal(actions[1]?.id, 'identify_disputes_write')
})

test('coordinator prefers chairman rulings when unresolved disputes remain', () => {
  const startupReceipts = Object.fromEntries(R1_AGENT_SEQUENCE.map((agentId) => [
    agentId,
    { receipt: { receipt_id: `startup-${agentId}`, agent_id: agentId } },
  ]))
  const actions = deriveCoordinatorNextActions({
    workflow: {
      workflow: { deal_id: 'DEAL-001', current_phase: 'R1.5' },
      agent_reports: R1_AGENT_SEQUENCE.map((agentId) => ({ agent_id: agentId, has_report: true })),
    },
    startupReceipts,
    disputes: {
      deal_id: 'DEAL-001',
      status: 'warn',
      counts: { disputes: 1, unresolved: 1 },
      artifacts: { json: { available: true, path: 'phases/r1_5_disputes.json' } },
      disputes: [{ dispute_id: 'D1', topic: '估值口径', resolved: false }],
    },
  })

  assert.equal(actions[0]?.id, 'ruling_dry')
  assert.equal(actions[1]?.id, 'ruling_write')
})

test('coordinator closes the loop with human confirmation after R4 decision', () => {
  const startupReceipts = Object.fromEntries(R1_AGENT_SEQUENCE.map((agentId) => [
    agentId,
    { receipt: { receipt_id: `startup-${agentId}`, agent_id: agentId } },
  ]))
  const actions = deriveCoordinatorNextActions({
    decision: {
      decision: {},
      report_path: 'decision.md',
      contract: {
        deal_id: 'DEAL-001',
        decision: { value: 'pass' },
        human_confirmation: { status: 'pending', confirmed: false },
      },
    },
    phaseArtifacts: {
      deal_id: 'DEAL-001',
      phases: [
        { phase: 'R2', status: 'pass', artifacts: { json: { available: true } } },
        { phase: 'R3', status: 'pass', artifacts: { json: { available: true } } },
        { phase: 'R4', status: 'pass', artifacts: { json: { available: true } } },
      ],
    },
    startupReceipts,
    workflow: {
      workflow: { deal_id: 'DEAL-001', current_phase: 'R4' },
      agent_reports: R1_AGENT_SEQUENCE.map((agentId) => ({ agent_id: agentId, has_report: true })),
    },
    disputes: {
      deal_id: 'DEAL-001',
      status: 'pass',
      counts: { disputes: 0, unresolved: 0 },
      artifacts: { json: { available: true } },
      disputes: [],
    },
  })

  assert.equal(actions[0]?.id, 'human_dry')
  assert.equal(actions[1]?.id, 'human_write')
})

test('meeting agenda treats approved human confirmation as closed', () => {
  const agenda = deriveMeetingAgenda({
    decision: {
      decision: {},
      report_path: 'decision.md',
      contract: {
        deal_id: 'DEAL-001',
        decision: { value: 'pass' },
        human_confirmation: { status: 'approved', confirmed: false },
      },
    },
  })

  const human = agenda.find((item) => item.phase === 'HUMAN')

  assert.equal(human?.tone, 'success')
  assert.equal(human?.blocking, false)
})

test('meeting write validation guards R3 and final human actions', () => {
  assert.equal(validateR3Action(true, ''), 'R3 skip 必须填写 skip_reason，方便审计回放。')
  assert.equal(validateR3Action(true, 'covered in R2'), '')
  assert.equal(validateHumanConfirmationDraft({ status: 'rejected' }), '驳回或 override 必须填写理由。')
  assert.equal(validateHumanConfirmationDraft({ status: 'override', overrideReason: 'IC override' }), 'override 必须填写 override decision。')
  assert.equal(validateHumanConfirmationDraft({ status: 'override', overrideReason: 'IC override', overrideDecision: 'pass', overrideScore: '101' }), 'override score 必须是 0-100 的数字。')
  assert.equal(validateHumanConfirmationDraft({ status: 'confirmed' }), '')
})

test('selected agent dry-run summary preserves committee order and gate messages', () => {
  const summary = buildSelectedAgentDryRunSummary(
    ['siq_ic_finance_auditor', 'siq_ic_risk_controller'],
    [
      { agent_id: 'siq_ic_finance_auditor', allowed: true, warnings: ['receipt hit is low'] },
      { agent_id: 'siq_ic_risk_controller', allowed: false, blocking_reasons: ['startup_receipt_missing'] },
    ],
  )

  assert.equal(summary.schema_version, 'siq_primary_market_selected_agents_dry_run_v1')
  assert.equal(summary.workflow_action, 'selected-agents-dry-run')
  assert.equal(summary.allowed, false)
  assert.deepEqual(summary.planned_agent_ids, ['siq_ic_finance_auditor', 'siq_ic_risk_controller'])
  assert.deepEqual(summary.warnings, ['财务审计委员: receipt hit is low'])
  assert.deepEqual(summary.blocking_reasons, ['风险管理委员: startup_receipt_missing'])
  assert.equal(summary.hermes_called, false)
})
