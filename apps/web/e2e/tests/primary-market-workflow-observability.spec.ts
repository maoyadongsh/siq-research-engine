import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

const agentIds = [
  'siq_ic_master_coordinator',
  'siq_ic_chairman',
  'siq_ic_strategist',
  'siq_ic_sector_expert',
  'siq_ic_finance_auditor',
  'siq_ic_legal_scanner',
  'siq_ic_risk_controller',
]

const privateCollections: Record<string, string> = {
  siq_ic_master_coordinator: 'ic_master_coordinator',
  siq_ic_chairman: 'ic_chairman',
  siq_ic_strategist: 'ic_strategist',
  siq_ic_sector_expert: 'ic_sector_expert',
  siq_ic_finance_auditor: 'ic_finance_auditor',
  siq_ic_legal_scanner: 'ic_legal_scanner',
  siq_ic_risk_controller: 'ic_risk_controller',
}

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function mockWorkflowObservabilityApis(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/auth/me') {
      await route.fulfill(json(e2eUser))
      return
    }
    if (path === '/api/primary-market/projects') {
      await route.fulfill(json({ deals: [{ deal_id: 'DEAL-IC-001', company_name: '示例机器人', current_phase: 'R4' }] }))
      return
    }
    if (path === '/api/primary-market/projects/DEAL-IC-001') {
      await route.fulfill(json({ summary: { deal_id: 'DEAL-IC-001', company_name: '示例机器人', current_phase: 'R4' }, project_meta: {}, manifest: {}, workflow: { deal_id: 'DEAL-IC-001' } }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/workflow') {
      await route.fulfill(json({
        workflow: { deal_id: 'DEAL-IC-001', company_name: '示例机器人', status: 'r1_in_progress', current_phase: 'R4' },
        agent_reports: agentIds.slice(1).map((agentId) => ({ agent_id: agentId, has_report: true, score: 76, recommendation: 'review' })),
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/preflight') {
      await route.fulfill(json({ preflight: { deal_id: 'DEAL-IC-001', status: 'pass', checks: [{ id: 'identity', label: 'Identity', status: 'pass', message: 'ok' }] } }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/disputes') {
      await route.fulfill(json({
        status: 'warn',
        generation_mode: 'chairman_model_v2',
        counts: { disputes: 1, unresolved: 1, positions: 2 },
        disputes: [{ dispute_id: 'DSP-1', topic: '收入确认口径', severity: 'high', resolved: false, position_count: 2, agent_ids: ['siq_ic_finance_auditor', 'siq_ic_risk_controller'], evidence_ids: ['EVID-001'], required_followups: ['补充验收单'] }],
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/phase-artifacts') {
      await route.fulfill(json({
        status: 'warn',
        phases: [
          { phase: 'R1', status: 'pass', mode: 'normal', artifacts: { json: { available: true } }, counts: { items: 6 } },
          { phase: 'R1.5', status: 'warn', mode: 'normal', artifacts: { json: { available: true } }, counts: { items: 1 } },
          { phase: 'R2', status: 'pass', mode: 'normal', artifacts: { json: { available: true } }, counts: { items: 1 } },
          { phase: 'R3', status: 'pass', mode: 'normal', artifacts: { json: { available: true } }, counts: { items: 1 } },
          { phase: 'R4', status: 'warn', mode: 'normal', artifacts: { json: { available: true } }, counts: { items: 1 } },
        ],
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/reports/r2-agents') {
      await route.fulfill(json({
        generation_mode: 'model_expert_revision_v2',
        counts: { reports: 1, revisions: 2 },
        agents: [{ agent_id: 'siq_ic_finance_auditor', label: '财务审计委员', status: 'pass', has_report: true, r1_score: 78, r2_score: 72, score_change: -6, recommendation: 'review', revision_count: 2, summary: '回款证据导致收入质量评分下调。', artifact_available: true }],
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/reports/r3-review') {
      await route.fulfill(json({
        status: 'pass', mode: 'full', generation_mode: 'model_red_blue_v1', skipped: false,
        counts: { reports: 1, challenges: 2 },
        reports: [{ agent_id: 'siq_ic_risk_controller', label: '风控委员', status: 'pass', stance: 'red_rebuttal', summary: '客户回款集中风险仍未被充分回答。', challenge_count: 2, evidence_count: 1 }],
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/agents') {
      await route.fulfill(json({ agents: agentIds.map((agentId) => ({ agent_id: agentId, label: agentId, runtime: { enabled: true }, readiness: { allowed: true }, receipt: { present: true }, report: { has_report: agentId !== 'siq_ic_master_coordinator' } })) }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/decision') {
      await route.fulfill(json({
        decision: {
          generation_mode: 'deterministic_siq_r4_finalize_v1',
          report_id: 'ICRPT-R4-DEMO-001',
          revision: 1,
          workflow_run_id: 'ICRUN-R4-DEMO-001',
        },
        quality: {
          status: 'warn',
          blocking_reasons: ['claim.evidence'],
          checks: [{ id: 'claim.evidence', status: 'fail', message: 'critical claim unsupported' }],
        },
        factcheck: {
          status: 'fail',
          unsupported_claims: [{ claim_id: 'CLM-DEMO-001', severity: 'critical' }],
          required_repairs: [{ claim_id: 'CLM-DEMO-001', action: 'add number trace' }],
        },
        report_path: 'decision/IC_DECISION_REPORT.md',
        contract: { status: 'warn', missing_required_fields: ['conditions'], missing_advisory_fields: ['monitoring_metrics'], human_confirmation: { status: 'pending', confirmed: false }, artifacts: { markdown: { available: true, path: 'decision/IC_DECISION_REPORT.md' } } },
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/audit') {
      await route.fulfill(json({
        audit: {
          events: [{
            event_type: 'ic_agent_handoff_persisted', handoff_id: 'ICHANDOFF-DEMO', phase: 'R1B',
            from_agent_id: 'siq_ic_finance_auditor', to_agent_id: 'siq_ic_risk_controller',
            workflow_run_id: 'ICRUN-DEMO', input_digest: 'abc123', created_at: '2026-07-13T08:00:00Z',
          }],
        },
        summary: { status: 'pass' },
      }))
      return
    }
    if (path === '/api/deals/DEAL-IC-001/evidence') {
      await route.fulfill(json({ quality_report: { status: 'pass', item_count: 20, verified_count: 18, dimensions: ['business', 'finance', 'legal', 'risk'], missing_dimensions: [] }, evidence_index: {}, items_preview: [] }))
      return
    }
    if (path === '/api/primary-market/meeting/DEAL-IC-001/agents/readiness') {
      await route.fulfill(json({
        schema_version: 'siq_primary_market_meeting_readiness_v2', deal_id: 'DEAL-IC-001',
        agents: agentIds.map((agentId, index) => ({
          agent_id: agentId, label: agentId, role: agentId.replace('siq_ic_', ''),
          runtime: { status: 'running', enabled: true, port: 18660 + index },
          contract: { contract_version: 'siq_ic_profile_v2', source_files: ['AGENTS.md'], responsibilities: ['formal analysis'] },
          startup_receipt: {
            present: true, receipt_id: `receipt-${agentId}`, shared_hits: 8, private_hits: index === 5 ? 0 : 3,
            collections: ['siq_deal_shared', agentId], physical_collections: ['ic_collaboration_shared', privateCollections[agentId]],
            shared_collection: 'ic_collaboration_shared', private_collection: privateCollections[agentId],
            retrieval_status: index === 5 ? 'degraded' : 'ready', degraded_reasons: index === 5 ? ['private_background_hits_missing'] : [],
            evidence_snapshot_hash: 'a'.repeat(64), capability_restrictions: index === 5 ? ['legal_authority:review_required'] : [],
          },
          report: { has_report: agentId !== 'siq_ic_master_coordinator', score: 76, recommendation: 'review' },
          workflow: { phase_task_status: 'completed' },
          quality: { ready_for_formal_task: true, status: index === 5 ? 'warning' : 'ready', blocking_reasons: [], warnings: [] },
        })),
        summary: { runtime_running: 7, receipt_present: 7, r1_reports_present: 6, blocking_profiles: [] },
      }))
      return
    }
    if (path === '/api/primary-market/meeting/DEAL-IC-001/agents/prepare-all') {
      await route.fulfill(json({
        deal_id: 'DEAL-IC-001',
        results: [{ agent_id: 'siq_ic_chairman', status: 'completed', receipt_id: 'receipt-chairman-r15' }],
      }))
      return
    }
    if (/\/api\/deals\/DEAL-IC-001\/agents\/[^/]+\/startup-retrieval$/.test(path)) {
      const agentId = decodeURIComponent(path.split('/').at(-2) || '')
      await route.fulfill(json({ deal_id: 'DEAL-IC-001', agent_id: agentId, receipt: { receipt_id: `receipt-${agentId}`, agent_id: agentId, shared_hits: 8, private_hits: 3, collections: ['siq_deal_shared', agentId], physical_collections: ['ic_collaboration_shared', privateCollections[agentId]], evidence_snapshot_hash: 'a'.repeat(64) } }))
      return
    }
    if (path === '/api/primary-market/projects/DEAL-IC-001/meeting-transcript') {
      await route.fulfill(json({ deal_id: 'DEAL-IC-001', lane: 'agent-siq_ic_master_coordinator', events: [] }))
      return
    }
    await route.fulfill(json({ messages: [], sessions: [], artifacts: [], items: [] }))
  })
}

test.describe('一级市场 workflow 可观测验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.name} 双库、阶段 delta 和 fallback 无页面溢出`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockWorkflowObservabilityApis(page)
      await page.goto('/primary-market/meeting?dealId=DEAL-IC-001')

      await expect(page.getByRole('heading', { name: '投研决策' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Agent Readiness 与双库检索' })).toBeVisible()
      await expect(page.getByText('项目 Evidence').first()).toBeVisible()
      await expect(page.getByText('私有背景知识').first()).toBeVisible()
      await expect(page.getByText('ic_finance_auditor').first()).toBeVisible()
      await expect(page.getByText('R2 观点与评分 Delta')).toBeVisible()
      await expect(page.getByText('R3 对抗时间线')).toBeVisible()
      await expect(page.getByRole('heading', { name: '结构化 Agent Handoff' })).toBeVisible()
      await expect(page.getByText('财务审计委员 → 风险管理委员')).toBeVisible()
      await expect(page.getByText('deterministic fallback').first()).toBeVisible()
      await expect(page.getByText('factcheck fail')).toBeVisible()
      await expect(page.getByText('attestation pending')).toBeVisible()
      await expect(page.getByText('critical claim unsupported')).toBeVisible()
      await expect(page.getByText('检索目标 R1.5 · 1 roles')).toBeVisible()
      await expect(page.getByRole('button', { name: '准备 R1.5 智能体' })).toBeDisabled()

      const prepareRequest = page.waitForRequest((request) => {
        if (!request.url().includes('/api/primary-market/meeting/DEAL-IC-001/agents/prepare-all')) return false
        const body = request.postDataJSON()
        return body.round_name === 'R1.5'
          && body.include_vector === true
          && JSON.stringify(body.profile_ids) === JSON.stringify(['siq_ic_chairman'])
      })
      await page.getByRole('button', { name: '准备 R1.5 参与角色' }).click()
      await prepareRequest

      const layout = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        mainRight: document.querySelector('main')?.getBoundingClientRect().right || 0,
      }))
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
      expect(layout.mainRight).toBeLessThanOrEqual(layout.viewportWidth + 1)
    })
  }
})
