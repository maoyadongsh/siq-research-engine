import type { Page, Route } from '@playwright/test'

export const e2eUser = {
  id: 1,
  username: 'playwright',
  email: 'playwright@example.com',
  full_name: 'Playwright 验收用户',
  role: 'super_admin',
  approval_status: 'approved',
  is_active: true,
}

const fixedNow = '2026-06-27T08:00:00.000Z'
const demoDealId = 'DEAL-YUSHU-2026-001'
const demoLegacyProjectId = 'SIQ-YUSHU-2026-002'

const demoDealSummary = {
  deal_id: demoDealId,
  legacy_project_id: demoLegacyProjectId,
  company_name: '宇树科技',
  industry: '机器人',
  stage: 'Pre-IPO',
  deal_type: '股权投资',
  source: 'openclaw_import',
  status: 'r1_in_progress',
  current_phase: 'R1',
  final_decision: 'pass',
  final_score: 82,
  updated_at: fixedNow,
}

const demoWorkflow = {
  schema_version: 'siq_deal_workflow_state_v1',
  deal_id: demoDealId,
  legacy_project_id: demoLegacyProjectId,
  company_name: '宇树科技',
  industry: '机器人',
  stage: 'Pre-IPO',
  status: 'r1_in_progress',
  current_phase: 'R1',
  final_decision: 'pass',
  final_score: 82,
  phases: {
    R0: {
      status: 'completed',
      started_at: fixedNow,
      completed_at: fixedNow,
      evidence_gate: 'passed',
    },
    R1: {
      status: 'in_progress',
      active_agent: 'siq_ic_finance_auditor',
      submitted_agents: ['siq_ic_strategist', 'siq_ic_sector_expert'],
    },
    'R1.5': { status: 'pending' },
    R2: { status: 'pending' },
    R3: { status: 'pending' },
    R4: { status: 'pending' },
  },
}

const demoDealManifestSummary = {
  schema_version: 'siq_deal_manifest_v1',
  deal_id: demoDealId,
  legacy_project_id: demoLegacyProjectId,
  company_name: '宇树科技',
  counts: {
    documents: 3,
    evidence: 8,
    imported_files: 6,
    missing_files: 0,
    rejected_files: 0,
    hashes: 6,
    files_with_hash: 6,
    files_missing_hash: 0,
    archive_files: 6,
  },
}

const demoDealDetail = {
  summary: demoDealSummary,
  workflow: demoWorkflow,
  manifest: demoDealManifestSummary,
}

const demoDealStatus = {
  schema_version: 'siq_deal_status_summary_v1',
  deal_id: demoDealId,
  status: 'r1_in_progress',
  generated_at: fixedNow,
  ready_for_next_action: true,
  next_action: 'run_r1_serial',
  counts: { pass: 4, warn: 1, missing: 0, blocking: 0 },
  components: [
    {
      id: 'workflow',
      label: 'Workflow',
      status: 'pass',
      message: 'R0-R4 workflow state loaded.',
      href: 'workflow',
    },
    {
      id: 'agents',
      label: 'Agents',
      status: 'pass',
      message: 'IC profiles are available.',
      href: 'agents',
    },
    {
      id: 'decision',
      label: 'Decision',
      status: 'pass',
      message: 'R4 decision contract available.',
      href: 'decision',
    },
    {
      id: 'audit',
      label: 'Audit',
      status: 'pass',
      message: 'Audit chain present.',
      href: 'audit',
    },
  ],
}

const demoDealAgents = {
  schema_version: 'siq_deal_agents_v1',
  deal_id: demoDealId,
  generated_at: fixedNow,
  counts: {
    agents: 7,
    r1_agents: 6,
    ready: 5,
    blocked: 1,
    reports: 2,
    receipts: 3,
    runtime_enabled: 7,
  },
  agents: [
    {
      agent_id: 'siq_ic_master_coordinator',
      role: 'master_coordinator',
      label: 'SIQ IC Master Coordinator',
      profile_path: 'agents/hermes/profiles/siq_ic_master_coordinator',
      is_r1_agent: false,
      status: 'non_r1',
      runtime: { enabled: true, port: 18660 },
    },
    {
      agent_id: 'siq_ic_strategist',
      role: 'strategist',
      label: 'SIQ IC Strategist',
      profile_path: 'agents/hermes/profiles/siq_ic_strategist',
      r1_sequence_index: 0,
      is_r1_agent: true,
      status: 'ready',
      runtime: { enabled: true, port: 18662 },
      readiness: { allowed: true, preflight_status: 'pass', would_queue: false, has_report: true, has_startup_receipt: true },
      report: { status: 'ready', score: 82, recommendation: 'pass', artifact_available: true, artifact_path: 'phases/r1_reports.json' },
      receipt: { present: true, receipt_id: 'receipt-strategy' },
    },
    {
      agent_id: 'siq_ic_sector_expert',
      role: 'sector_expert',
      label: 'SIQ IC Sector Expert',
      profile_path: 'agents/hermes/profiles/siq_ic_sector_expert',
      r1_sequence_index: 1,
      is_r1_agent: true,
      status: 'ready',
      runtime: { enabled: true, port: 18663 },
      readiness: { allowed: true, preflight_status: 'pass', would_queue: false, has_report: true, has_startup_receipt: true },
      report: { status: 'ready', score: 79, recommendation: 'review', artifact_available: true, artifact_path: 'phases/r1_reports.json' },
      receipt: { present: true, receipt_id: 'receipt-sector' },
    },
    {
      agent_id: 'siq_ic_finance_auditor',
      role: 'finance_auditor',
      label: 'SIQ IC Finance Auditor',
      profile_path: 'agents/hermes/profiles/siq_ic_finance_auditor',
      r1_sequence_index: 2,
      is_r1_agent: true,
      status: 'blocked',
      runtime: { enabled: true, port: 18664 },
      readiness: {
        allowed: false,
        preflight_status: 'warn',
        would_queue: true,
        has_report: false,
        has_startup_receipt: false,
        blocking_reasons: ['startup_receipt_missing'],
      },
    },
  ],
}

const demoDealWorkflowResponse = {
  workflow: demoWorkflow,
  agent_reports: [
    {
      agent_id: 'siq_ic_strategist',
      label: 'SIQ IC Strategist',
      has_report: true,
      has_startup_receipt: true,
      score: 82,
      recommendation: 'pass',
      verified_count: 4,
      assumed_count: 1,
      open_questions: ['退出窗口'],
      risk_flags: ['估值敏感'],
      summary: '战略窗口清晰，但估值需要持续复核。',
    },
    {
      agent_id: 'siq_ic_finance_auditor',
      label: 'SIQ IC Finance Auditor',
      has_report: false,
      has_startup_receipt: false,
      score: null,
      recommendation: null,
      verified_count: 0,
      assumed_count: 0,
      open_questions: ['收入质量'],
      risk_flags: ['现金流'],
      summary: '等待 startup receipt 后进入财务尽调。',
    },
  ],
  r1_agent_readiness: {
    next_agent_id: 'siq_ic_finance_auditor',
    agents: [
      { agent_id: 'siq_ic_strategist', allowed: false, blocking_reasons: [], has_report: true, has_startup_receipt: true },
      { agent_id: 'siq_ic_finance_auditor', allowed: false, blocking_reasons: ['startup_receipt_missing'], has_report: false, has_startup_receipt: false },
    ],
  },
  startup_receipts: { count: 2 },
  disputes: [
    {
      dispute_id: 'DISP-001',
      topic: '收入质量与估值假设',
      dimension: 'finance',
      severity: 'high',
      resolved: false,
      position_count: 2,
      positions: [{ agent_id: 'siq_ic_strategist' }, { agent_id: 'siq_ic_finance_auditor' }],
      agent_ids: ['siq_ic_strategist', 'siq_ic_finance_auditor'],
      evidence_ids: ['EVID-001', 'EVID-002'],
      required_followups: ['补充 2025 现金流拆解'],
    },
  ],
}

const demoDealPreflight = {
  preflight: {
    status: 'warn',
    counts: { pass: 4, warn: 1, fail: 0 },
    checks: [
      { id: 'evidence_gate', label: 'Evidence Gate', status: 'pass', message: '核心证据已绑定。' },
      {
        id: 'startup_receipts',
        label: 'Startup Receipts',
        status: 'warn',
        message: '财务专家缺少 startup receipt。',
        details: { missing_agents: ['siq_ic_finance_auditor'] },
      },
    ],
  },
}

const demoDealPhaseArtifacts = {
  status: 'warn',
  counts: { json: 2, markdown: 2, missing: 1 },
  warnings: ['R1.5 pending'],
  phases: [
    {
      phase: 'R1',
      label: '专家尽调',
      status: 'pass',
      mode: 'openclaw_compat',
      artifacts: {
        json: { path: 'phases/r1_reports.json', available: true },
        markdown: { path: 'discussion/01_R1_尽调汇总.md', available: true },
      },
      counts: { reports: 2, receipts: 2 },
      items_preview: [
        { agent_id: 'siq_ic_strategist', score: 82, recommendation: 'pass', summary: '战略窗口清晰。' },
      ],
    },
    {
      phase: 'R1.5',
      label: '分歧识别',
      status: 'warn',
      mode: 'pending',
      blocking: false,
      artifacts: {
        json: { path: 'phases/r1_5_disputes.json', available: true },
        markdown: { path: 'discussion/02_R1.5_裁决记录.md', available: false },
      },
      counts: { disputes: 1, rulings: 0 },
      warnings: ['chairman ruling pending'],
    },
  ],
}

const demoDealDisputes = {
  status: 'warn',
  counts: { disputes: 1, resolved: 0, unresolved: 1, high_severity: 1, positions: 2, rulings: 0 },
  artifacts: {
    json: { path: 'phases/r1_5_disputes.json', available: true },
    markdown: { path: 'discussion/02_R1.5_裁决记录.md', available: false },
  },
  warnings: ['chairman ruling pending'],
  disputes: demoDealWorkflowResponse.disputes,
}

const demoGeneratedRuling = {
  dispute_id: 'DISP-001',
  dispute: demoDealWorkflowResponse.disputes[0],
  ruling: {
    dispute_id: 'DISP-001',
    decision: 'request_followup',
    rationale: '收入质量与估值假设存在高优先级分歧，需先补充现金流拆解后再进入下一轮裁决。',
    resolved: false,
    required_followups: ['补充 2025 现金流拆解'],
    evidence_ids: ['EVID-001', 'EVID-002'],
  },
}

const demoGenerateRulingsPreview = {
  schema_version: 'siq_deal_r1_5_dispute_ruling_generation_v1',
  deal_id: demoDealId,
  dry_run: true,
  would_write: false,
  written: false,
  overwrite: false,
  generation_mode: 'mock_dry_run',
  generated_count: 1,
  skipped_count: 0,
  warnings: [],
  skipped: [],
  rulings: [demoGeneratedRuling],
  summary: demoDealDisputes,
}

const demoGenerateRulingsWrite = {
  ...demoGenerateRulingsPreview,
  dry_run: false,
  would_write: true,
  written: true,
  generation_mode: 'mock_write',
  json_path: 'phases/r1_5_disputes.json',
  markdown_path: 'discussion/02_R1.5_裁决记录.md',
}

function demoR2RunResponse(dryRun: boolean) {
  return {
    schema_version: dryRun ? 'siq_ic_workflow_r2_run_dry_run_v1' : 'siq_ic_workflow_r2_run_v1',
    deal_id: demoDealId,
    workflow_action: 'run-r2',
    dry_run: dryRun,
    allowed: true,
    would_write: dryRun,
    queued: false,
    job_id: null,
    blocking_reasons: [],
    warnings: [],
    counts: { r1_reports: 5, resolved_disputes: 1, r2_reports: dryRun ? undefined : 5 },
    output_paths: {
      json: 'phases/r2_reports.json',
      markdown: 'discussion/03_R2_观点完善汇总.md',
    },
    reports_preview: {
      siq_ic_finance_auditor: {
        agent_id: 'siq_ic_finance_auditor',
        round_name: 'R2',
        r2_score: 80,
        recommendation: 'support_with_terms',
      },
    },
    reports: dryRun ? undefined : {
      siq_ic_finance_auditor: {
        agent_id: 'siq_ic_finance_auditor',
        round_name: 'R2',
        r2_score: 80,
        recommendation: 'support_with_terms',
      },
    },
    hermes_called: false,
    report_written: !dryRun,
    workflow_advanced: !dryRun,
  }
}

function demoR3RunResponse(body: Record<string, unknown>) {
  const dryRun = body.dry_run !== false
  const skip = body.skip === true
  const skipReason = typeof body.skip_reason === 'string' ? body.skip_reason : null
  return {
    schema_version: dryRun ? 'siq_ic_workflow_r3_run_dry_run_v1' : 'siq_ic_workflow_r3_run_v1',
    deal_id: demoDealId,
    workflow_action: 'run-r3',
    dry_run: dryRun,
    allowed: true,
    would_write: dryRun,
    queued: false,
    job_id: null,
    blocking_reasons: [],
    warnings: [],
    counts: { r2_reports: 5, r3_reports: skip ? 0 : 5 },
    mode: skip ? 'skip' : 'normal',
    skip_reason: skipReason,
    output_paths: {
      json: 'phases/r3_reports.json',
      markdown: 'discussion/04_R3_红蓝对抗.md',
    },
    payload_preview: {
      mode: skip ? 'skip' : 'normal',
      skip_reason: skipReason,
      reports: skip ? {} : { siq_ic_risk_controller: { challenge_count: 1 } },
    },
    payload: dryRun ? undefined : {
      mode: skip ? 'skip' : 'normal',
      skip_reason: skipReason,
      reports: skip ? {} : { siq_ic_risk_controller: { challenge_count: 1 } },
    },
    hermes_called: false,
    report_written: !dryRun,
    workflow_advanced: !dryRun,
  }
}

function demoR4FinalizeResponse(body: Record<string, unknown>) {
  const dryRun = body.dry_run !== false
  const overwrite = body.overwrite === true
  const decision = {
    schema_version: 'siq_ic_r4_decision_v1',
    deal_id: demoDealId,
    decision: 'pass',
    final_score: 82,
    weighted_agent_score: 80,
    chairman_dimension_score: 84,
    chairman_qualitative_decision: '有条件通过',
    human_confirmation: { status: 'pending' },
  }
  return {
    schema_version: dryRun ? 'siq_ic_workflow_r4_finalize_dry_run_v1' : 'siq_ic_workflow_r4_finalize_v1',
    deal_id: demoDealId,
    workflow_action: 'finalize-r4',
    dry_run: dryRun,
    allowed: true,
    would_write: dryRun,
    overwrite,
    queued: false,
    job_id: null,
    blocking_reasons: [],
    warnings: [],
    counts: { r2_reports: 5, r3_reports: 0 },
    output_paths: {
      json: 'phases/r4_decision.json',
      markdown: 'decision/IC_DECISION_REPORT.md',
      html: 'decision/IC_DECISION_REPORT.html',
      decision_payload: 'decision/decision_payload.json',
    },
    decision_preview: decision,
    decision: dryRun ? undefined : decision,
    hermes_called: false,
    report_written: !dryRun,
    workflow_advanced: !dryRun,
  }
}

const demoDealDecision = {
  decision: {
    decision: 'pass',
    final_score: 82,
    chairman_qualitative_decision: '有条件通过',
  },
  report_path: 'decision/IC_DECISION_REPORT.md',
  report_markdown: '# IC Decision\n\n宇树科技建议有条件通过，持续跟踪收入质量。',
  contract: {
    status: 'pass',
    generated_at: fixedNow,
    human_confirmation: { status: 'pending', confirmed: false },
    artifacts: {
      markdown: { path: 'decision/IC_DECISION_REPORT.md', available: true, size_bytes: 2048, sha256: 'abc123abc123abc123' },
      html: { path: 'decision/IC_DECISION_REPORT.html', available: true, size_bytes: 4096, sha256: 'def456def456def456' },
    },
    missing_required_fields: [],
    missing_advisory_fields: ['manual_confirmation'],
    scoring: { weighted_agent_score: 80, chairman_dimension_score: 84, final_score: 82 },
    decision: { value: 'pass', qualitative: '有条件通过' },
  },
}

const demoDealAudit = {
  audit: {
    events: [
      { event_type: 'openclaw_imported', created_at: fixedNow, actor: 'playwright', deal_id: demoDealId },
      { event_type: 'r1_agent_submitted', created_at: fixedNow, agent_id: 'siq_ic_strategist' },
      { event_type: 'r4_decision_generated', created_at: fixedNow, decision: 'pass' },
      { event_type: 'human_confirmation_previewed', created_at: fixedNow, status: 'pending' },
    ],
  },
  summary: {
    status: 'pass',
    generated_at: fixedNow,
    sources: {
      selected: 'primary',
      consistency: 'match',
      primary: { available: true, path: 'audit/audit_log.json' },
      fallback: { available: false },
    },
    counts: { events: 3, human_confirmation: 1, manual_override: 0 },
    latest_event: { event_type: 'human_confirmation_previewed', created_at: fixedNow },
    required_event_status: [
      { event_type: 'openclaw_imported', present: true, count: 1, required: true },
      { event_type: 'r1_agent_submitted', present: true, count: 1, required: true },
      { event_type: 'r4_decision_generated', present: true, count: 1, required: true },
    ],
    warnings: [],
  },
}

const demoDealManifest = {
  summary: {
    status: 'pass',
    generated_at: fixedNow,
    openclaw_import: {
      present: true,
      legacy_project_id: demoLegacyProjectId,
      metadata_present: true,
      file_count: 6,
    },
    counts: demoDealManifestSummary.counts,
    archive_manifest: {
      available: true,
      consistency: 'match',
      path: 'audit/archive_manifest.json',
      file_count: 6,
    },
    files: [
      { source: 'shared/projects/SIQ-YUSHU-2026-002/project_meta.json', target: 'project_meta.json', status: 'imported', sha256: 'abc123', hash_recorded: true, hash_matches: true },
      { source: 'shared/projects/SIQ-YUSHU-2026-002/phases/workflow_state.json', target: 'phases/workflow_state.json', status: 'imported', sha256: 'def456', hash_recorded: true, hash_matches: true },
    ],
    warnings: [],
  },
}

const workspaceArtifacts = [
  {
    id: 'download-cn-600519',
    type: 'download',
    title: '贵州茅台 2025 年度报告',
    path: 'CN/600519/annual-2025.pdf',
    source: 'CN',
    createdAt: fixedNow,
  },
  {
    id: 'parse-cn-600519',
    type: 'parse',
    title: '贵州茅台 2025 财报解析结果',
    path: 'task-cn-600519',
    source: 'pdf-parser',
    createdAt: fixedNow,
  },
  {
    id: 'analysis-cn-600519',
    type: 'analysis_report',
    title: '贵州茅台 智能分析报告',
    path: '/api/wiki/companies/600519-贵州茅台/analysis/report.html',
    source: 'agent',
    createdAt: fixedNow,
  },
  {
    id: 'fact-cn-600519',
    type: 'factchecker_report',
    title: '贵州茅台 事实核查报告',
    path: '/api/wiki/companies/600519-贵州茅台/factcheck/report.html',
    source: 'agent',
    createdAt: fixedNow,
  },
]

const workspaceSummary = {
  quotas: {
    agentQuestion: { used: 3, limit: 20, remaining: 17, resetAt: fixedNow },
    parseJob: { used: 1, limit: 10, remaining: 9, resetAt: fixedNow },
  },
  stats: { projects: 2, artifacts: 7, downloads: 2, parses: 2, reports: 3 },
  recentArtifacts: workspaceArtifacts,
  artifacts: workspaceArtifacts,
  projects: [
    {
      id: 1,
      name: '600519-贵州茅台',
      company_code: '600519',
      company_name: '贵州茅台',
      status: 'active',
      created_at: fixedNow,
      updated_at: fixedNow,
    },
    {
      id: 2,
      name: '00700-腾讯控股',
      company_code: '00700',
      company_name: '腾讯控股',
      status: 'active',
      created_at: fixedNow,
      updated_at: fixedNow,
    },
  ],
}

const dealListResponse = {
  deals: [demoDealSummary],
  stats: { total: 1, active: 1, diligence: 1, highRisk: 0 },
}

async function parseRequestJson(route: Route) {
  try {
    const body = route.request().postData()
    return body ? JSON.parse(body) as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function fulfillMockApi(route: Route) {
  const url = new URL(route.request().url())
  const pathname = decodeURIComponent(url.pathname)

  if (url.pathname === '/api/auth/me') {
    await route.fulfill(json(e2eUser))
    return
  }

  if (url.pathname === '/api/deals') {
    await route.fulfill(json(dealListResponse))
    return
  }

  if (pathname === `/api/deals/${demoDealId}`) {
    await route.fulfill(json(demoDealDetail))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/status`) {
    await route.fulfill(json(demoDealStatus))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/agents`) {
    await route.fulfill(json(demoDealAgents))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow`) {
    await route.fulfill(json(demoDealWorkflowResponse))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/run-r1-serial`) {
    await route.fulfill(json({
      schema_version: 'siq_deal_r1_serial_run_v1',
      deal_id: demoDealId,
      round_name: 'R1',
      workflow_action: 'run-r1-serial',
      dry_run: true,
      allowed: true,
      would_run: true,
      planned_agent_ids: ['siq_ic_finance_auditor'],
      next_agent_id: 'siq_ic_finance_auditor',
      stop_reason: null,
      blocking_reasons: [],
      hermes_called: false,
      report_written: false,
      workflow_advanced: false,
      agents: [
        {
          agent_id: 'siq_ic_finance_auditor',
          action: 'would_run',
          would_run: true,
          has_startup_receipt: false,
          startup_receipt_id: null,
          blocking_reasons: [],
        },
      ],
    }))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/run-r1-agent`) {
    await route.fulfill(json({
      schema_version: 'siq_deal_agent_task_dry_run_v1',
      deal_id: demoDealId,
      agent_id: 'siq_ic_finance_auditor',
      round_name: 'R1',
      allowed: false,
      blocking_reasons: ['startup_receipt_missing'],
      preflight_status: 'warn',
      dry_run: true,
      hermes_called: false,
      report_written: false,
      workflow_advanced: false,
      payload: {
        output_contract: {
          json_path: 'phases/r1_reports.json',
          json_key: 'siq_ic_finance_auditor',
          markdown_path: 'discussion/01_R1_尽调汇总.md',
        },
      },
    }))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/identify-disputes`) {
    await route.fulfill(json({
      schema_version: 'siq_deal_r1_5_disputes_identification_v1',
      deal_id: demoDealId,
      dry_run: true,
      would_write: false,
      written: false,
      preserve_rulings: true,
      dispute_count: demoDealWorkflowResponse.disputes.length,
      warnings: [],
      json_path: 'phases/r1_5_disputes.json',
      markdown_path: 'discussion/02_R1.5_裁决记录.md',
      payload: {
        schema_version: 'siq_deal_r1_5_disputes_v1',
        deal_id: demoDealId,
        phase: 'R1.5',
        disputes: demoDealWorkflowResponse.disputes,
        warnings: [],
      },
      summary: demoDealDisputes,
    }))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/generate-dispute-rulings`) {
    const body = await parseRequestJson(route)
    await route.fulfill(json(body.dry_run === false ? demoGenerateRulingsWrite : demoGenerateRulingsPreview))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/run-r2`) {
    const body = await parseRequestJson(route)
    await route.fulfill(json(demoR2RunResponse(body.dry_run !== false)))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/run-r3`) {
    const body = await parseRequestJson(route)
    await route.fulfill(json(demoR3RunResponse(body)))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/workflow/finalize-r4`) {
    const body = await parseRequestJson(route)
    await route.fulfill(json(demoR4FinalizeResponse(body)))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/preflight`) {
    await route.fulfill(json(demoDealPreflight))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/phase-artifacts`) {
    await route.fulfill(json(demoDealPhaseArtifacts))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/disputes`) {
    await route.fulfill(json(demoDealDisputes))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/decision`) {
    await route.fulfill(json(demoDealDecision))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/audit`) {
    await route.fulfill(json(demoDealAudit))
    return
  }

  if (pathname === `/api/deals/${demoDealId}/manifest`) {
    await route.fulfill(json(demoDealManifest))
    return
  }

  if (url.pathname === '/api/workspace/summary') {
    await route.fulfill(json(workspaceSummary))
    return
  }

  if (url.pathname === '/api/workspace/me') {
    await route.fulfill(json({
      user: e2eUser,
      quotas: workspaceSummary.quotas,
      stats: workspaceSummary.stats,
    }))
    return
  }

  if (url.pathname === '/api/workspace/artifacts') {
    await route.fulfill(json({ artifacts: workspaceArtifacts }))
    return
  }

  await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
}

export async function mockAuthenticatedWorkspace(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/pdfapi/')) {
      await fulfillMockApi(route)
      return
    }
    await route.continue()
  })
}
