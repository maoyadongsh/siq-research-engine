import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  deriveAgentReadinessChips,
  deriveAgentHandoffRows,
  deriveAgentReadinessLine,
  buildMeetingEvents,
  buildSelectedAgentDryRunSummary,
  deriveCoordinatorNextActions,
  deriveMeetingAgentReadinessRows,
  deriveMeetingAgenda,
  deriveMeetingPreparationPlan,
  deriveMeetingEventQualityChips,
  deriveMeetingReceiptRows,
  deriveMeetingScoreRows,
  deriveMeetingScoringSummary,
  deriveR15DisputeBoard,
  deriveR2DeltaRows,
  deriveR3Timeline,
  deriveR4QualityObservability,
  deriveWorkflowPhaseObservability,
  deriveProjectMetrics,
  deriveProjectRow,
  isMaterialPolling,
  IC_AGENT_OPTIONS,
  IC_EXPERT_AGENT_IDS,
  materialCapabilities,
  materialFromResponse,
  materialStatusLabel,
  materialStatusTone,
  materialsFromResponse,
  mergeMaterialParseStatus,
  R1_AGENT_SEQUENCE,
  primaryMarketMeetingQuickQuestions,
  validateHumanConfirmationDraft,
  validateR3Action,
  withMaterialVersions,
} from './primaryMarketViewModel'
import type { DealStatusResponse, DealSummary, DealWorkflowResponse } from '@/lib/dealTypes'

test('meeting quick questions stay fixed at six items for every role and mode', () => {
  for (const agent of IC_AGENT_OPTIONS) {
    const questions = primaryMarketMeetingQuickQuestions(agent.value, 'single')
    assert.equal(questions.length, 6, agent.label)
    assert.equal(questions[0]?.label, '智能体简介', agent.label)
  }

  for (const mode of ['committee', 'workflow'] as const) {
    const questions = primaryMarketMeetingQuickQuestions('siq_ic_master_coordinator', mode)
    assert.equal(questions.length, 6, mode)
    assert.equal(questions[0]?.label, '智能体简介', mode)
  }
})

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
            sharedConnected: true,
            privateConnected: true,
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
          serviceReadyForChat: true,
          chatBlockingReasons: [],
          contentWarnings: [],
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
            sharedConnected: false,
            privateConnected: false,
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
          serviceReadyForChat: false,
          chatBlockingReasons: ['gateway_not_running', 'role_contract_unavailable'],
          contentWarnings: ['deal_scoped_shared_kb_empty'],
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
  assert.deepEqual(financeChips.map((chip) => chip.label), ['Hermes running', 'Chat ready', 'Milvus connected', 'Formal ready', 'Profile loaded'])
  assert.equal(legalChips.find((chip) => chip.id === 'runtime')?.tone, 'error')
  assert.equal(legalChips.find((chip) => chip.id === 'chat-service')?.label, 'Chat unavailable')
  assert.equal(legalChips.find((chip) => chip.id === 'dual-kb')?.label, 'Milvus degraded')
  assert.equal(legalChips.find((chip) => chip.id === 'profile')?.label, 'Profile missing')
  assert.equal(committeeChips.find((chip) => chip.id === 'chat-service')?.label, 'Chat 1/7')
  assert.equal(committeeChips.find((chip) => chip.id === 'formal-task')?.label, 'Formal 1/7')
  assert.match(deriveAgentReadinessLine(bundle, 'siq_ic_finance_auditor'), /Chat ready/)
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

test('meeting preparation plan routes private-knowledge retrieval by upcoming phase', () => {
  const startupReceipts = Object.fromEntries(R1_AGENT_SEQUENCE.map((agentId) => [
    agentId,
    { receipt: { receipt_id: `startup-${agentId}`, agent_id: agentId } },
  ]))
  const workflow = {
    workflow: { deal_id: 'DEAL-001', current_phase: 'R1' },
    agent_reports: R1_AGENT_SEQUENCE.map((agentId) => ({ agent_id: agentId, has_report: true })),
  }
  const resolvedDisputes = {
    deal_id: 'DEAL-001',
    status: 'pass',
    counts: { disputes: 0, unresolved: 0 },
    artifacts: { json: { available: true } },
    disputes: [],
  }
  const base = {
    preflight: { deal_id: 'DEAL-001', status: 'pass', checks: [] },
    workflow,
    startupReceipts,
    disputes: resolvedDisputes,
  }

  const r0 = deriveMeetingPreparationPlan({
    preflight: { deal_id: 'DEAL-001', status: 'fail', checks: [] },
  })
  const r1 = deriveMeetingPreparationPlan({
    preflight: { deal_id: 'DEAL-001', status: 'pass', checks: [] },
  })
  const r15 = deriveMeetingPreparationPlan({
    ...base,
    disputes: {
      ...resolvedDisputes,
      status: 'warn',
      counts: { disputes: 1, unresolved: 1 },
      disputes: [{ dispute_id: 'DSP-1', resolved: false }],
    },
  })
  const r2 = deriveMeetingPreparationPlan(base)
  const r3 = deriveMeetingPreparationPlan({
    ...base,
    phaseArtifacts: { phases: [{ phase: 'R2', status: 'pass', artifacts: { json: { available: true } } }] },
  })
  const r4 = deriveMeetingPreparationPlan({
    ...base,
    phaseArtifacts: {
      phases: [
        { phase: 'R2', status: 'pass', artifacts: { json: { available: true } } },
        { phase: 'R3', status: 'pass', artifacts: { json: { available: true } } },
      ],
    },
  })

  assert.deepEqual(r0.profileIds, ['siq_ic_master_coordinator'])
  assert.equal(r1.roundName, 'R1')
  assert.deepEqual(r1.profileIds, R1_AGENT_SEQUENCE)
  assert.deepEqual(r15.profileIds, ['siq_ic_chairman'])
  assert.deepEqual(r2.profileIds, IC_EXPERT_AGENT_IDS)
  assert.deepEqual(r3.profileIds, [...IC_EXPERT_AGENT_IDS, 'siq_ic_chairman'])
  assert.deepEqual(r4.profileIds, ['siq_ic_chairman'])
  assert.deepEqual(r4.individualProfileIds, ['siq_ic_chairman', 'siq_ic_master_coordinator'])
})

test('meeting agenda treats approved and overridden human decisions as closed', () => {
  for (const status of ['approved', 'overridden']) {
    const agenda = deriveMeetingAgenda({
      decision: {
        decision: {},
        report_path: 'decision.md',
        contract: {
          deal_id: 'DEAL-001',
          decision: { value: 'pass' },
          human_confirmation: { status, confirmed: false },
        },
      },
    })

    const human = agenda.find((item) => item.phase === 'HUMAN')

    assert.equal(human?.tone, status === 'approved' ? 'success' : 'warning')
    assert.equal(human?.blocking, false)
  }
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

test('prospectus material status separates parse and analysis-source states', () => {
  const queued = {
    document_id: 'DOC-001',
    deal_id: 'DEAL-001',
    filename: 'issuer.pdf',
    document_type: 'prospectus',
    document_status: 'active',
    parse_status: 'queued',
    analysis_source_status: 'pending',
  }
  const restricted = { ...queued, parse_status: 'succeeded', analysis_source_status: 'ready_with_restrictions' }
  const superseded = { ...restricted, document_status: 'superseded' }

  assert.equal(materialStatusLabel(queued), '排队中')
  assert.equal(materialStatusTone(queued), 'warning')
  assert.equal(isMaterialPolling(queued), true)
  assert.equal(materialStatusLabel(restricted), '可用于文本分析，财务受限')
  assert.equal(materialStatusTone(restricted), 'warning')
  assert.equal(isMaterialPolling(restricted), false)
  assert.equal(materialStatusLabel(superseded), '已被新版替代')
  assert.equal(materialStatusTone(superseded), 'neutral')
})

test('prospectus capabilities normalize booleans, strings and detailed contracts', () => {
  const rows = materialCapabilities({
    document_id: 'DOC-001',
    deal_id: 'DEAL-001',
    filename: 'issuer.pdf',
    capabilities: {
      text_evidence: true,
      source_page_trace: { status: 'ready', ready: true },
      financial_facts: { status: 'blocked', reason: '三年报表不完整' },
      semantic_index: 'indexing',
    },
  })

  assert.deepEqual(rows.map((row) => [row.id, row.status]), [
    ['text_evidence', 'ready'],
    ['source_trace', 'ready'],
    ['financial_facts', 'blocked'],
    ['semantic_index', 'indexing'],
  ])
  assert.equal(rows[2]?.detail, '三年报表不完整')
})

test('material facade response helpers tolerate documented response variants', () => {
  const materials = materialsFromResponse({
    deal_id: 'DEAL-001',
    materials: [
      { document_id: 'DOC-OLD', deal_id: 'DEAL-001', filename: 'old.pdf', created_at: '2026-07-01T00:00:00Z' },
      { document_id: 'DOC-NEW', deal_id: 'DEAL-001', filename: 'new.pdf', created_at: '2026-07-02T00:00:00Z' },
    ],
  })
  const uploaded = materialFromResponse({
    document: { document_id: 'DOC-NEW', deal_id: 'DEAL-001', filename: 'new.pdf', parse_status: 'queued' },
    parse_run: { parse_run_id: 'PRUN-001', status: 'queued' },
    status_url: '/status',
    reused: false,
    analysis_source: {
      source_id: 'PM:DEAL-001:DOC-NEW:PRUN-001',
      status: 'review_required',
      parse_run_id: 'PRUN-001',
      capabilities: { text_evidence: 'ready', financial_facts: 'review_required' },
    },
  })

  assert.deepEqual(materials.map((item) => item.document_id), ['DOC-NEW', 'DOC-OLD'])
  assert.equal(uploaded?.parse_run?.parse_run_id, 'PRUN-001')
  assert.equal(uploaded?.status_url, '/status')
  assert.equal(uploaded?.analysis_source_status, 'review_required')
  assert.equal(uploaded?.capabilities?.text_evidence, 'ready')

  const reconciled = mergeMaterialParseStatus(uploaded!, {
    document_id: 'DOC-NEW',
    parse_run: {
      parse_run_id: 'PRUN-001',
      status: 'succeeded',
      capabilities: { text_evidence: 'ready', financial_facts: 'review_required' },
    },
    document: {
      document_id: 'DOC-NEW',
      deal_id: 'DEAL-001',
      filename: 'new.pdf',
      analysis_source_status: 'review_required',
    },
  })

  assert.equal(reconciled.parse_status, 'succeeded')
  assert.equal(reconciled.analysis_source_status, 'review_required')
  assert.equal(materialStatusLabel(reconciled), '质量待确认')
})

test('prospectus version chain is derived without changing generic materials', () => {
  const materials = withMaterialVersions([
    { document_id: 'DOC-NEW', deal_id: 'DEAL-001', filename: 'new.pdf', document_type: 'prospectus', created_at: '2026-07-02T00:00:00Z', supersedes_document_id: 'DOC-OLD' },
    { document_id: 'DOC-BP', deal_id: 'DEAL-001', filename: 'bp.pdf', document_type: 'bp', created_at: '2026-07-03T00:00:00Z' },
    { document_id: 'DOC-OLD', deal_id: 'DEAL-001', filename: 'old.pdf', document_type: 'prospectus', created_at: '2026-07-01T00:00:00Z', superseded_by_document_id: 'DOC-NEW' },
  ])

  assert.equal(materials.find((item) => item.document_id === 'DOC-OLD')?.version, 1)
  assert.equal(materials.find((item) => item.document_id === 'DOC-NEW')?.version, 2)
  assert.equal(materials.find((item) => item.document_id === 'DOC-NEW')?.version_chain?.length, 2)
  assert.equal(materials.find((item) => item.document_id === 'DOC-BP')?.version, undefined)
})

test('agent readiness separates project Evidence from private background retrieval', () => {
  const rows = deriveMeetingAgentReadinessRows({
    meetingReadiness: {
      schemaVersion: 'siq_primary_market_meeting_readiness_v2',
      dealId: 'DEAL-001',
      profiles: [{
        profileId: 'siq_ic_finance_auditor',
        label: '财务审计委员',
        role: 'finance',
        runtime: { health: 'running', port: 18664 },
        contract: { version: 'siq_ic_profile_v2', responsibilities: [], sourceFiles: [], outputs: [], boundaries: [] },
        startupReceipt: {
          present: true,
          receiptId: 'receipt-finance',
          sharedHits: 8,
          privateHits: 3,
          evidenceHits: 8,
          gaps: [],
          collections: ['siq_deal_shared', 'siq_ic_finance_auditor'],
          physicalCollections: ['ic_collaboration_shared', 'ic_finance_auditor'],
          sharedCollection: 'ic_collaboration_shared',
          privateCollection: 'ic_finance_auditor',
          retrievalStatus: 'degraded',
          degradedReasons: ['rerank_unavailable'],
          blockingReasons: [],
          evidenceSnapshotHash: 'a'.repeat(64),
          capabilityRestrictions: ['financial_facts:review_required'],
          stale: false,
        },
        r1Report: { present: false, score: null, recommendation: '', artifactPath: '' },
        quality: { readyForFormalTask: true, blockingReasons: [], warnings: ['rerank_unavailable'], status: 'warning' },
        phaseTaskStatus: 'queued',
      }],
      summary: { runtimeRunning: 1, receiptPresent: 1, r1ReportsPresent: 0, blockingProfiles: [] },
    },
  })

  const finance = rows.find((row) => row.agentId === 'siq_ic_finance_auditor')
  assert.equal(finance?.projectEvidenceHits, 8)
  assert.equal(finance?.backgroundKnowledgeHits, 3)
  assert.equal(finance?.sharedCollection, 'ic_collaboration_shared')
  assert.equal(finance?.privateCollection, 'ic_finance_auditor')
  assert.equal(finance?.sharedConnected, false)
  assert.equal(finance?.privateConnected, false)
  assert.equal(finance?.retrievalStatus, 'degraded')
  assert.equal(finance?.retrievalTone, 'warning')
  assert.equal(finance?.contractVersion, 'siq_ic_profile_v2')
  assert.equal(finance?.phaseTaskStatus, 'queued')
  assert.deepEqual(finance?.capabilityRestrictions, ['financial_facts:review_required'])
})

test('connected collections with zero Deal hits keep chat service ready while formal task stays blocked', () => {
  const rows = deriveMeetingAgentReadinessRows({
    meetingReadiness: {
      schemaVersion: 'siq_primary_market_meeting_readiness_v2',
      dealId: 'DEAL-EMPTY',
      profiles: [{
        profileId: 'siq_ic_legal_scanner',
        label: '法务合规委员',
        role: 'legal',
        runtime: { health: 'running', port: 18665 },
        contract: {
          version: 'siq_ic_profile_matrix_v2',
          responsibilities: ['法律尽调'],
          sourceFiles: ['IDENTITY.md', 'AGENTS.md', 'SOUL.md', 'TOOLS.md'],
          outputs: ['法律红旗清单'],
          boundaries: ['不替主席下结论'],
        },
        startupReceipt: {
          present: false,
          receiptId: '',
          sharedConnected: true,
          privateConnected: true,
          sharedHits: 0,
          privateHits: 4,
          evidenceHits: 0,
          gaps: ['deal_scoped_shared_kb_empty'],
          physicalCollections: ['ic_collaboration_shared', 'ic_legal_scanner'],
          sharedCollection: 'ic_collaboration_shared',
          privateCollection: 'ic_legal_scanner',
          retrievalStatus: 'blocked',
          rerankReady: false,
          rerankStatus: 'skipped',
          rerankCandidateCount: 0,
          rerankResultCount: 0,
          retrievalStrategy: 'dense_bm25_rrf',
        },
        r1Report: { present: false, score: null, recommendation: '', artifactPath: '' },
        quality: {
          readyForFormalTask: false,
          blockingReasons: ['deal_scoped_shared_kb_empty'],
          warnings: [],
          status: 'blocked',
        },
        serviceReadyForChat: true,
        chatBlockingReasons: [],
        contentWarnings: ['deal_scoped_shared_kb_empty', 'evidence_snapshot_unavailable'],
      }],
      summary: {
        runtimeRunning: 1,
        receiptPresent: 0,
        r1ReportsPresent: 0,
        serviceReadyForChat: 1,
        formalTaskReady: 0,
        blockingProfiles: ['siq_ic_legal_scanner'],
      },
    },
  })

  const legal = rows.find((row) => row.agentId === 'siq_ic_legal_scanner')
  assert.equal(legal?.serviceReadyForChat, true)
  assert.equal(legal?.sharedConnected, true)
  assert.equal(legal?.privateConnected, true)
  assert.equal(legal?.projectEvidenceHits, 0)
  assert.equal(legal?.backgroundKnowledgeHits, 4)
  assert.equal(legal?.readyForFormalTask, false)
  assert.equal(legal?.rerankTone, 'neutral')
  assert.deepEqual(legal?.chatBlockingReasons, [])
  assert.deepEqual(legal?.contentWarnings, ['deal_scoped_shared_kb_empty', 'evidence_snapshot_unavailable'])
})

test('workflow observability exposes disputes, R2 delta, R3 timeline and R4 fallback quality', () => {
  const bundle = {
    preflight: { deal_id: 'DEAL-001', status: 'pass', checks: [] },
    workflow: {
      workflow: { deal_id: 'DEAL-001', current_phase: 'R4' },
      agent_reports: R1_AGENT_SEQUENCE.map((agentId) => ({ agent_id: agentId, has_report: true })),
    },
    disputes: {
      status: 'warn',
      counts: { disputes: 1, unresolved: 1 },
      generation_mode: 'chairman_model_v2',
      disputes: [{
        dispute_id: 'DSP-1',
        topic: '收入确认口径',
        severity: 'high',
        resolved: false,
        position_count: 2,
        agent_ids: ['siq_ic_finance_auditor', 'siq_ic_risk_controller'],
        evidence_ids: ['EVID-1'],
        required_followups: ['补充验收单'],
      }],
    },
    r2Reports: {
      counts: { reports: 1, revisions: 2 },
      agents: [{
        agent_id: 'siq_ic_finance_auditor',
        label: '财务审计委员',
        status: 'pass',
        has_report: true,
        r1_score: 78,
        r2_score: 72,
        score_change: -6,
        recommendation: 'review',
        revision_count: 2,
        summary: '新增回款证据后下调收入质量判断。',
        artifact_available: true,
      }],
    },
    r3Review: {
      status: 'pass',
      mode: 'full',
      generation_mode: 'model_red_blue_v1',
      counts: { reports: 1, challenges: 2 },
      reports: [{
        agent_id: 'siq_ic_risk_controller',
        label: '风控委员',
        status: 'pass',
        stance: 'red_rebuttal',
        summary: '回款集中仍未被充分回答。',
        challenge_count: 2,
        evidence_count: 1,
      }],
    },
    decision: {
      decision: {
        generation_mode: 'deterministic_siq_r4_finalize_v1',
        report_id: 'ICRPT-R4-OBS-001',
        revision: 2,
        workflow_run_id: 'ICRUN-R4-OBS-001',
      },
      quality: {
        status: 'warn',
        blocking_reasons: ['claim.evidence'],
        checks: [{ id: 'claim.evidence', status: 'fail', message: 'critical claim unsupported' }],
      },
      factcheck: {
        status: 'fail',
        unsupported_claims: [{ claim_id: 'CLM-001', severity: 'critical' }],
        required_repairs: [{ claim_id: 'CLM-001', action: 'add number trace' }],
      },
      report_path: 'decision/IC_DECISION_REPORT.md',
      contract: {
        status: 'warn',
        missing_required_fields: ['conditions'],
        missing_advisory_fields: ['monitoring_metrics'],
        human_confirmation: { status: 'pending', confirmed: false },
      },
    },
  }

  const phases = deriveWorkflowPhaseObservability(bundle)
  const disputes = deriveR15DisputeBoard(bundle)
  const deltas = deriveR2DeltaRows(bundle)
  const timeline = deriveR3Timeline(bundle)
  const r4 = deriveR4QualityObservability(bundle)

  assert.equal(phases.find((row) => row.phase === 'R1.5')?.blocking, true)
  assert.equal(phases.find((row) => row.phase === 'R4')?.deterministicFallback, true)
  assert.equal(disputes[0]?.ruling, '未裁决')
  assert.deepEqual(disputes[0]?.evidenceIds, ['EVID-1'])
  assert.equal(deltas[0]?.scoreChange, -6)
  assert.equal(timeline[0]?.stance, 'red_rebuttal')
  assert.equal(r4.fallback, true)
  assert.equal(r4.factcheckStatus, 'fail')
  assert.equal(r4.qualityStatus, 'warn')
  assert.equal(r4.attestationStatus, 'pending')
  assert.equal(r4.reportId, 'ICRPT-R4-OBS-001')
  assert.equal(r4.reportRevision, 2)
  assert.equal(r4.workflowRunId, 'ICRUN-R4-OBS-001')
  assert.deepEqual(r4.findings, [
    'claim.evidence',
    'critical claim unsupported',
    'CLM-001 · critical',
    'CLM-001 · add number trace',
  ])
})

test('R4 observability requires a complete canonical human attestation after confirmation', () => {
  const snapshot = 'a'.repeat(64)
  const base: Parameters<typeof deriveR4QualityObservability>[0] = {
    decision: {
      decision: {
        report_id: 'ICRPT-R4-BOUND-001',
        revision: 1,
        workflow_run_id: 'ICRUN-R4-BOUND-001',
        evidence_snapshot_hash: snapshot,
      },
      quality: {
        report_id: 'ICRPT-R4-BOUND-001',
        report_revision: 1,
        evidence_snapshot_hash: snapshot,
      },
      factcheck: {
        report_id: 'ICRPT-R4-BOUND-001',
        report_revision: 1,
        evidence_snapshot_hash: snapshot,
      },
      contract: {
        human_confirmation: {
          status: 'confirmed',
          confirmed: true,
          attestation_schema_version: 'siq_ic_human_confirmation_attestation_v1',
          report_id: 'ICRPT-R4-BOUND-001',
          report_revision: 1,
          workflow_run_id: 'ICRUN-R4-BOUND-001',
          evidence_snapshot_hash: snapshot,
          decision_sha256: 'b'.repeat(64),
          quality_sha256: 'c'.repeat(64),
          factcheck_sha256: 'd'.repeat(64),
        },
      },
    },
  }

  assert.equal(deriveR4QualityObservability(base).attestationStatus, 'bound')
  const incomplete = structuredClone(base)
  delete incomplete.decision!.contract!.human_confirmation!.factcheck_sha256
  assert.equal(deriveR4QualityObservability(incomplete).attestationStatus, 'incomplete')

  const mismatched = structuredClone(base)
  mismatched.decision!.contract!.human_confirmation!.workflow_run_id = 'ICRUN-R4-OTHER-001'
  assert.equal(deriveR4QualityObservability(mismatched).attestationStatus, 'incomplete')

  const malformedDigest = structuredClone(base)
  malformedDigest.decision!.contract!.human_confirmation!.quality_sha256 = 'not-a-sha256'
  assert.equal(deriveR4QualityObservability(malformedDigest).attestationStatus, 'incomplete')
})

test('handoff observability derives validated agent transfers from the audit chain', () => {
  const rows = deriveAgentHandoffRows({
    audit: {
      audit: {
        events: [
          { event_type: 'unrelated_event', created_at: '2026-07-13T08:00:00Z' },
          {
            event_type: 'ic_agent_handoff_persisted',
            handoff_id: 'ICHANDOFF-001',
            phase: 'R1B',
            from_agent_id: 'siq_ic_finance_auditor',
            to_agent_id: 'siq_ic_risk_controller',
            workflow_run_id: 'ICRUN-001',
            input_digest: 'abc123',
            created_at: '2026-07-13T08:01:00Z',
          },
        ],
      },
    },
  })

  assert.equal(rows.length, 1)
  assert.equal(rows[0]?.phase, 'R1B')
  assert.equal(rows[0]?.fromLabel, '财务审计委员')
  assert.equal(rows[0]?.toLabel, '风险管理委员')
  assert.equal(rows[0]?.inputDigest, 'abc123')
})
