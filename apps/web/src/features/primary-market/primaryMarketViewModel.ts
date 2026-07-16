import type {
  DealAgentsResponse,
  DealAuditResponse,
  DealDecisionResponse,
  DealDetailResponse,
  DealDisputesResponse,
  DealDocument,
  DealEvidenceQualityReport,
  DealEvidenceResponse,
  DealPhaseArtifactPhase,
  DealPhaseArtifactsResponse,
  PrimaryMarketMaterial,
  PrimaryMarketMaterialCapabilities,
  PrimaryMarketMaterialCapabilityValue,
  PrimaryMarketMaterialParseStatusResponse,
  PrimaryMarketMaterialResponse,
  PrimaryMarketMaterialsResponse,
  DealPreflight,
  DealR2AgentReportsResponse,
  DealR3ReviewSummaryResponse,
  DealStatusResponse,
  DealStartupReceipt,
  DealStartupRetrievalResponse,
  DealSummary,
  DealWorkflowResponse,
} from '@/lib/dealTypes'
import type { AgentAttachment } from '@/lib/useAgentChat'
import type {
  PrimaryMarketMeetingAgentReadiness,
  PrimaryMarketMeetingReadinessProfile,
} from '@/features/primary-market/primaryMarketApi'

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'error'

export interface PrimaryMarketTab {
  id: 'workbench' | 'materials' | 'meeting' | 'postInvestment'
  label: string
  to: string
}

export const PRIMARY_MARKET_TABS: PrimaryMarketTab[] = [
  { id: 'workbench', label: '工作平台', to: '/primary-market' },
  { id: 'materials', label: '材料中心', to: '/primary-market/materials' },
  { id: 'meeting', label: '投研决策', to: '/primary-market/meeting' },
  { id: 'postInvestment', label: '投后管理', to: '/primary-market/post-investment' },
]

export const DOCUMENT_TYPE_OPTIONS = [
  { value: 'prospectus', label: '招股书' },
  { value: 'teaser', label: 'Teaser' },
  { value: 'bp', label: 'BP' },
  { value: 'financial_model', label: '财务模型' },
  { value: 'audit_report', label: '审计报告' },
  { value: 'legal_doc', label: '法务材料' },
  { value: 'industry_report', label: '行业报告' },
  { value: 'interview_note', label: '访谈纪要' },
  { value: 'term_sheet', label: '条款清单' },
  { value: 'meeting_note', label: '会议纪要' },
  { value: 'other', label: '其他' },
]

export const PROSPECTUS_EXCHANGE_OPTIONS = [
  { value: 'SSE', label: '上海证券交易所' },
  { value: 'SZSE', label: '深圳证券交易所' },
  { value: 'BSE', label: '北京证券交易所' },
]

export const PROSPECTUS_BOARD_OPTIONS = [
  { value: 'main', label: '主板' },
  { value: 'star', label: '科创板' },
  { value: 'chinext', label: '创业板' },
  { value: 'beijing', label: '北交所' },
]

export const PROSPECTUS_FILING_STAGE_OPTIONS = [
  { value: 'application_draft', label: '申报稿' },
  { value: 'pre_disclosure', label: '预披露' },
  { value: 'pre_disclosure_update', label: '预披露更新稿' },
  { value: 'inquiry_response_draft', label: '问询回复稿' },
  { value: 'registration_draft', label: '注册稿' },
  { value: 'registration_effective', label: '注册生效稿' },
  { value: 'final_prospectus', label: '正式招股书' },
  { value: 'issuance', label: '发行阶段' },
]

export const EVIDENCE_DIMENSIONS = [
  { value: 'business', label: '业务' },
  { value: 'finance', label: '财务' },
  { value: 'legal', label: '法务' },
  { value: 'risk', label: '风险' },
  { value: 'sector', label: '行业' },
  { value: 'strategy', label: '战略' },
  { value: 'team', label: '团队' },
  { value: 'terms', label: '条款' },
]

export const IC_AGENT_OPTIONS = [
  { value: 'siq_ic_master_coordinator', label: '投委会秘书', shortLabel: 'Coordinator', r1: false },
  { value: 'siq_ic_chairman', label: '投委会主席', shortLabel: 'Chairman', r1: true },
  { value: 'siq_ic_strategist', label: '战略专家', shortLabel: 'Strategy', r1: true },
  { value: 'siq_ic_sector_expert', label: '行业专家', shortLabel: 'Sector', r1: true },
  { value: 'siq_ic_finance_auditor', label: '财务审计委员', shortLabel: 'Finance', r1: true },
  { value: 'siq_ic_legal_scanner', label: '法务合规委员', shortLabel: 'Legal', r1: true },
  { value: 'siq_ic_risk_controller', label: '风险管理委员', shortLabel: 'Risk', r1: true },
]

export type PrimaryMarketMeetingMode = 'single' | 'committee' | 'workflow'

export interface PrimaryMarketMeetingQuickQuestion {
  label: string
  prompt?: string
  featured?: boolean
}

interface MeetingQuestionProfile {
  intro: string
  questions: PrimaryMarketMeetingQuickQuestion[]
}

const MEETING_INTRO_LABEL = '智能体简介'

const IC_MEETING_QUESTION_PROFILES: Record<string, MeetingQuestionProfile> = {
  siq_ic_master_coordinator: {
    intro: '我是投委会秘书，负责把 R0-R4 流程、证据门禁、委员发言和投决产物串成可推进的会议节奏。',
    questions: [
      { label: '同步状态', prompt: '请同步当前项目 R0-R4 状态，指出已完成、阻断、待人工确认和下一步优先级。' },
      { label: '流程推进', prompt: '请基于当前项目上下文判断应该推进到哪一步，并给出推进前的门禁条件。' },
      { label: '证据缺口', prompt: '请汇总当前证据缺口，按 R0、R1、R3、R4 对流程影响排序。' },
      { label: '委员分工', prompt: '请为各投委会委员分配下一轮核验任务，并说明每项任务的验收标准。' },
      { label: '投决产物', prompt: '请列出形成最终投决草案还缺哪些结构化产物和人工确认项。' },
    ],
  },
  siq_ic_chairman: {
    intro: '我是投委会主席，负责统一投决口径、裁决关键分歧，并把委员意见收敛成可表决结论。',
    questions: [
      { label: '决策口径', prompt: '请基于当前材料给出本项目投决讨论应采用的核心判断口径和不可突破底线。' },
      { label: '分歧裁决', prompt: '请识别当前委员意见中的关键分歧，并提出裁决顺序和裁决依据。' },
      { label: '投票建议', prompt: '请模拟投委会表决前的主席意见，说明支持、观察或反对的条件。' },
      { label: '人工确认', prompt: '请列出进入最终投决前必须由人类主席确认的事项和风险提示。' },
      { label: '会议结论', prompt: '请将当前讨论压缩成一版投委会会议结论草案。' },
    ],
  },
  siq_ic_strategist: {
    intro: '我是战略专家，负责检验投资逻辑、增长假设、竞争位置、估值边界和退出路径是否成立。',
    questions: [
      { label: '投资逻辑', prompt: '请梳理本项目最核心的投资逻辑，并指出哪些假设必须被证据验证。' },
      { label: '增长假设', prompt: '请拆解当前增长假设，判断哪些假设最脆弱、最需要补充材料。' },
      { label: '竞争格局', prompt: '请从竞争格局和护城河角度评价本项目的战略位置。' },
      { label: '估值边界', prompt: '请给出战略视角下可接受估值边界和触发降估值的条件。' },
      { label: '退出路径', prompt: '请评估本项目潜在退出路径、关键里程碑和不可忽视的退出风险。' },
    ],
  },
  siq_ic_sector_expert: {
    intro: '我是行业专家，负责判断市场空间、产业链位置、同业对标、技术壁垒和行业周期风险。',
    questions: [
      { label: '行业空间', prompt: '请评估本项目所在行业空间、增长驱动和未来三年的关键不确定性。' },
      { label: '同业对标', prompt: '请选取合适同业，对比本项目在规模、效率、壁垒和商业化阶段上的位置。' },
      { label: '需求验证', prompt: '请判断当前材料能否证明真实客户需求，并指出还需要哪些访谈或订单证据。' },
      { label: '技术壁垒', prompt: '请分析本项目技术壁垒、替代风险和产业链议价能力。' },
      { label: '周期风险', prompt: '请识别行业周期、政策、供需变化对本项目估值和经营的影响。' },
    ],
  },
  siq_ic_finance_auditor: {
    intro: '我是财务审计委员，负责核验收入质量、现金流、预测模型、审计疑点和估值敏感性。',
    questions: [
      { label: '财务质量', prompt: '请基于当前材料评价收入、毛利、费用和利润质量，并列出需要核验的科目。' },
      { label: '现金流核验', prompt: '请核查经营现金流、应收应付和回款节奏是否支持当前经营叙事。' },
      { label: '预测模型', prompt: '请审阅当前财务预测的关键假设，指出最影响估值的敏感变量。' },
      { label: '审计疑点', prompt: '请列出当前财务材料中的审计疑点、缺失底稿和补证优先级。' },
      { label: '估值敏感性', prompt: '请做一版估值敏感性分析框架，说明哪些财务指标触发降估值或否决。' },
    ],
  },
  siq_ic_legal_scanner: {
    intro: '我是法务合规委员，负责扫描主体资质、权属、合同条款、监管审批和交割条件风险。',
    questions: [
      { label: '合规清单', prompt: '请基于当前项目列出法务合规尽调清单，并按投决影响排序。' },
      { label: '条款风险', prompt: '请审阅交易条款中的控制权、回购、对赌、优先权和退出安排风险。' },
      { label: '权属核验', prompt: '请列出股权、知识产权、核心资产和重大合同权属需要核验的材料。' },
      { label: '监管审批', prompt: '请判断本项目是否涉及监管审批、资质限制、数据合规或行业准入问题。' },
      { label: '交割条件', prompt: '请提出签约到交割前必须完成的先决条件、陈述保证和补救机制。' },
    ],
  },
  siq_ic_risk_controller: {
    intro: '我是风险管理委员，负责构建下行情景、风险控制、投后监测指标和止损机制。',
    questions: [
      { label: '风险清单', prompt: '请按发生概率、影响程度和可控性排序当前项目的核心风险。' },
      { label: '下行情景', prompt: '请构建本项目三种下行情景，并说明每种情景下的估值和治理影响。' },
      { label: '风控措施', prompt: '请为当前主要风险设计交易文件、治理结构和投后管理中的控制措施。' },
      { label: '投后指标', prompt: '请设计投后监测指标、触发阈值和预警后的处置流程。' },
      { label: '退出预案', prompt: '请提出风险触发后的退出、止损或保护性条款执行预案。' },
    ],
  },
}

const COMMITTEE_MEETING_PROFILE: MeetingQuestionProfile = {
  intro: '当前是全体委员投研决策窗口，系统会按顺序召集委员发言，并保留前序观点供后续委员回应。',
  questions: [
    { label: '召集发言', prompt: '请按投委会顺序召集各委员围绕当前项目发表首轮意见。' },
    { label: '汇总共识', prompt: '请汇总当前委员观点中的共识、保留意见和需要主席裁决的问题。' },
    { label: '暴露分歧', prompt: '请要求各委员明确支持、观察或反对的理由，并暴露尚未解决的关键分歧。' },
    { label: '补证清单', prompt: '请让各委员分别提出进入下一阶段前必须补充的证据和材料。' },
    { label: '投决建议', prompt: '请基于全体委员意见形成一版投决建议和附带条件。' },
  ],
}

const WORKFLOW_MEETING_PROFILE: MeetingQuestionProfile = {
  intro: '当前是投委会秘书工作流窗口，用于按 R0-R4 程序预演、检查门禁并推进投研决策任务。',
  questions: [
    { label: '预演流程', prompt: '请预演当前项目从 R0 到 R4 的完整投研决策流程，并标出每一步门禁。' },
    { label: '推进下一步', prompt: '请判断当前最适合推进的下一步动作，并说明需要哪些人工确认。' },
    { label: '阻断检查', prompt: '请检查当前流程阻断项、证据缺口和不能继续推进的原因。' },
    { label: '产物校验', prompt: '请校验当前 R0-R4 产物是否齐备，并列出缺失文件或结构化字段。' },
    { label: '人工确认', prompt: '请列出本轮工作流必须由人类主持人确认后才能执行的事项。' },
  ],
}

function meetingProfileFor(agentId: string, mode: PrimaryMarketMeetingMode): MeetingQuestionProfile {
  if (mode === 'committee') return COMMITTEE_MEETING_PROFILE
  if (mode === 'workflow') return WORKFLOW_MEETING_PROFILE
  return IC_MEETING_QUESTION_PROFILES[agentId] || IC_MEETING_QUESTION_PROFILES.siq_ic_master_coordinator
}

export function primaryMarketMeetingIntro(agentId: string, mode: PrimaryMarketMeetingMode) {
  return meetingProfileFor(agentId, mode).intro
}

export function primaryMarketMeetingQuickQuestions(agentId: string, mode: PrimaryMarketMeetingMode): PrimaryMarketMeetingQuickQuestion[] {
  const profile = meetingProfileFor(agentId, mode)
  const label = mode === 'committee' ? '全体委员' : mode === 'workflow' ? '投委会秘书工作流' : agentLabel(agentId)
  return [
    {
      label: MEETING_INTRO_LABEL,
      featured: true,
      prompt: `请介绍你作为${label}在 SIQ 一级市场投研决策中的职责边界、需要我提供的材料、输出产物，以及我应该如何向你提问。`,
    },
    ...profile.questions,
  ]
}

export const R1_AGENT_SEQUENCE = [
  'siq_ic_strategist',
  'siq_ic_sector_expert',
  'siq_ic_finance_auditor',
  'siq_ic_legal_scanner',
  'siq_ic_risk_controller',
  'siq_ic_chairman',
]

export const IC_EXPERT_AGENT_IDS = [
  'siq_ic_strategist',
  'siq_ic_sector_expert',
  'siq_ic_finance_auditor',
  'siq_ic_legal_scanner',
  'siq_ic_risk_controller',
]

export type MeetingPreparationRound = 'R0' | 'R1' | 'R1.5' | 'R2' | 'R3' | 'R4'

export interface MeetingPreparationPlan {
  roundName: MeetingPreparationRound
  profileIds: string[]
  individualProfileIds: string[]
  label: string
  reason: string
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  r0_ready: 'R0 就绪',
  r1_in_progress: 'R1 尽调中',
  r4_completed: 'R4 已完成',
  archived: '已归档',
  closed: '已关闭',
  pass: '通过',
  warn: '警告',
  fail: '失败',
  missing: '缺失',
  pending: '待处理',
  blocked: '阻断',
  ready: '就绪',
  confirmed: '已确认',
}

const PHASE_LABELS: Record<string, string> = {
  R0: 'R0 信息校验',
  R1: 'R1 专家首轮',
  'R1.5': 'R1.5 分歧裁决',
  R2: 'R2 观点修订',
  R3: 'R3 红蓝对抗',
  R4: 'R4 投决草案',
  HUMAN: '人工确认',
}

export interface ParserBindDraft {
  taskId: string
  artifactPath: string
  note: string
}

export interface EvidenceDocumentRow {
  document_id?: string
  status?: string
  items?: number
  reason?: string
  [key: string]: unknown
}

export interface PrimaryMarketProjectRow {
  deal: DealSummary
  status?: DealStatusResponse | null
  phase: string
  statusLabel: string
  statusTone: BadgeTone
  ready: boolean
  nextAction: string
  blockingCount: number
  warningCount: number
  missingCount: number
  category: 'completed' | 'decision_pending' | 'blocked' | 'ready' | 'in_progress' | 'draft'
  blockingMessages: string[]
}

export interface PrimaryMarketMetrics {
  total: number
  active: number
  blocked: number
  ready: number
  decisionPending: number
  completed: number
}

export interface MeetingAgendaItem {
  phase: string
  label: string
  status: string
  tone: BadgeTone
  blocking?: boolean
  detail?: string
  count?: number
}

export type MeetingEventType =
  | 'coordinator_instruction'
  | 'agent_speech'
  | 'human_intervention'
  | 'phase_summary'
  | 'receipt_generated'
  | 'dispute_detected'
  | 'decision_draft'
  | 'system_blocking'
  | 'artifact_written'
  | 'audit_event'
  | 'quality_check'

export interface MeetingQualityCheck {
  id: string
  status: string
  detail?: string
}

export interface MeetingQualityResult {
  status: string
  checks: MeetingQualityCheck[]
  [key: string]: unknown
}

export interface MeetingEvent {
  id: string
  phase: string
  type: MeetingEventType
  speaker: string
  title: string
  body: string
  tone: BadgeTone
  meta?: string
  agentId?: string | null
  attachments?: AgentAttachment[]
  quality?: MeetingQualityResult | null
  createdAt?: string | null
}

export interface MeetingBundle {
  detail?: DealDetailResponse | null
  workflow?: DealWorkflowResponse | null
  preflight?: DealPreflight | null
  disputes?: DealDisputesResponse | null
  phaseArtifacts?: DealPhaseArtifactsResponse | null
  agents?: DealAgentsResponse | null
  decision?: DealDecisionResponse | null
  audit?: DealAuditResponse | null
  evidence?: DealEvidenceResponse | null
  meetingReadiness?: PrimaryMarketMeetingAgentReadiness | null
  r2Reports?: DealR2AgentReportsResponse | null
  r3Review?: DealR3ReviewSummaryResponse | null
  startupReceipts?: Record<string, DealStartupRetrievalResponse | null>
}

export interface MeetingReceiptRow {
  agentId: string
  label: string
  present: boolean
  allowed: boolean | null
  receiptId: string
  evidenceHits: number
  sharedHits: number
  privateHits: number
  collections: string[]
  physicalCollections: string[]
  gaps: string[]
  warnings: string[]
  createdAt?: string | null
}

export interface MeetingScoreRow {
  agentId: string
  label: string
  hasReport: boolean
  score: number | null
  scoreText: string
  recommendation: string
  confidence: string
  verifiedCount: number
  assumedCount: number
  receiptId: string
  artifactPath: string
}

export interface MeetingScoringSummary {
  count: number
  scoredCount: number
  average: number | null
  min: number | null
  max: number | null
  supportCount: number
  opposeCount: number
  watchCount: number
}

export interface MeetingAgentReadinessRow {
  agentId: string
  label: string
  runtimeHealth: string
  runtimeTone: BadgeTone
  receiptPresent: boolean | null
  receiptId: string
  r1ReportPresent: boolean | null
  r1ReportScore: number | null
  r1ReportRecommendation: string
  contractSourceCount: number
  contractSourceText: string
  contractVersion: string
  sharedCollection: string
  privateCollection: string
  logicalCollections: string[]
  physicalCollections: string[]
  projectEvidenceHits: number
  backgroundKnowledgeHits: number
  retrievalStatus: string
  retrievalTone: BadgeTone
  degradedReasons: string[]
  evidenceSnapshotHash: string
  capabilityRestrictions: string[]
  phaseTaskStatus: string
  qualityStatus: string
  stale: boolean
  readyForFormalTask: boolean
  blockingReasons: string[]
  warnings: string[]
}

export interface WorkflowPhaseObservabilityRow {
  phase: string
  label: string
  status: string
  tone: BadgeTone
  blocking: boolean
  generationMode: string
  deterministicFallback: boolean
  detail: string
}

export interface AgentHandoffObservabilityRow {
  id: string
  phase: string
  fromAgentId: string
  fromLabel: string
  toAgentId: string
  toLabel: string
  workflowRunId: string
  inputDigest: string
  createdAt: string
}

export interface R15DisputeBoardRow {
  id: string
  topic: string
  severity: string
  resolved: boolean
  positionCount: number
  evidenceIds: string[]
  agents: string[]
  ruling: string
  generationMode: string
  fallback: boolean
  followups: string[]
}

export interface R2DeltaRow {
  agentId: string
  label: string
  status: string
  r1Score: number | null
  r2Score: number | null
  scoreChange: number | null
  recommendation: string
  revisions: number
  summary: string
  artifactAvailable: boolean
}

export interface R3TimelineRow {
  id: string
  agentId: string
  label: string
  stance: string
  status: string
  summary: string
  challengeCount: number
  evidenceCount: number
  createdAt?: string | null
  generationMode: string
  fallback: boolean
}

export interface R4QualityObservability {
  status: string
  generationMode: string
  fallback: boolean
  factcheckStatus: string
  qualityStatus: string
  humanStatus: string
  humanConfirmed: boolean
  attestationStatus: 'pending' | 'bound' | 'incomplete'
  reportId: string
  reportRevision: number | null
  workflowRunId: string
  missingRequired: string[]
  missingAdvisory: string[]
  findings: string[]
  reportPath: string
}

export interface MeetingReadinessChip {
  id: string
  label: string
  tone: BadgeTone
  detail: string
}

export type CoordinatorActionId =
  | 'review_materials'
  | 'generate_receipts'
  | 'r1_dry'
  | 'r1_write'
  | 'identify_disputes_dry'
  | 'identify_disputes_write'
  | 'ruling_dry'
  | 'ruling_write'
  | 'r2_dry'
  | 'r2_write'
  | 'r3_dry'
  | 'r3_write'
  | 'r4_dry'
  | 'r4_write'
  | 'human_dry'
  | 'human_write'
  | 'open_decision'
  | 'refresh'

export type CoordinatorActionMode = 'link' | 'preview' | 'write' | 'refresh'

export interface CoordinatorNextAction {
  id: CoordinatorActionId
  label: string
  phase: string
  mode: CoordinatorActionMode
  tone: BadgeTone
  priority: number
  reason: string
  to?: string
  disabledReason?: string
}

export interface SelectedAgentDryRunSummary {
  schema_version: 'siq_primary_market_selected_agents_dry_run_v1'
  workflow_action: 'selected-agents-dry-run'
  dry_run: true
  allowed: boolean
  planned_agent_ids: string[]
  agent_runs: unknown[]
  blocking_reasons: string[]
  warnings: string[]
  hermes_called: false
  report_written: false
  workflow_advanced: false
}

export function text(value: unknown, fallback = '未记录') {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

export function statusLabel(status?: string | null) {
  const value = String(status || '').trim()
  return value ? STATUS_LABELS[value] || value : '未设置'
}

export function phaseLabel(phase?: string | null) {
  const value = String(phase || '').trim()
  return value ? PHASE_LABELS[value] || value : '未设置'
}

export function statusTone(status?: string | null): BadgeTone {
  const value = String(status || '').toLowerCase()
  if (!value || value === 'draft' || value === 'unknown' || value === 'non_r1') return 'neutral'
  if (
    ['pass', 'ready', 'available', 'completed', 'complete', 'success', 'succeeded', 'ok', 'confirmed', 'approved', 'r4_completed', 'archived', 'closed'].includes(value)
  ) return 'success'
  if (['fail', 'failed', 'error', 'blocked', 'rejected', 'unavailable'].includes(value)) return 'error'
  if (value.includes('fail') || value.includes('error') || value.includes('blocked')) return 'error'
  if (['warn', 'warning', 'missing', 'pending', 'queued', 'processing', 'needs_human', 'needs_revision', 'overridden'].includes(value)) return 'warning'
  if (value.includes('missing') || value.includes('warn') || value.includes('pending')) return 'warning'
  return 'info'
}

export function formatTime(value?: string | null) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatSize(value?: number | null) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

export function compactList(values?: unknown[], limit = 3) {
  if (!Array.isArray(values) || values.length === 0) return ''
  const shown = values.slice(0, limit).map((value) => text(value)).join(', ')
  return values.length > limit ? `${shown} +${values.length - limit}` : shown
}

function asStringArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean) : []
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function buildSelectedAgentDryRunSummary(agentIds: string[], agentRuns: unknown[]): SelectedAgentDryRunSummary {
  const blockingReasons: string[] = []
  const warnings: string[] = []
  let allowed = true

  agentRuns.forEach((run, index) => {
    const record = asRecord(run)
    const agentId = text(record?.agent_id || agentIds[index], agentIds[index] || '')
    if (record?.allowed === false) allowed = false
    asStringArray(record?.blocking_reasons).forEach((reason) => blockingReasons.push(`${agentLabel(agentId)}: ${reason}`))
    asStringArray(record?.warnings).forEach((warning) => warnings.push(`${agentLabel(agentId)}: ${warning}`))
  })

  return {
    schema_version: 'siq_primary_market_selected_agents_dry_run_v1',
    workflow_action: 'selected-agents-dry-run',
    dry_run: true,
    allowed,
    planned_agent_ids: agentIds,
    agent_runs: agentRuns,
    blocking_reasons: blockingReasons,
    warnings,
    hermes_called: false,
    report_written: false,
    workflow_advanced: false,
  }
}

function receiptUnknownArray(receipt: DealStartupReceipt | null | undefined, key: string) {
  return asStringArray(receipt?.[key])
}

function numericValue(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function primaryMarketTabHref(tab: PrimaryMarketTab, dealId?: string) {
  if (!dealId || tab.id === 'workbench') return tab.to
  const params = new URLSearchParams({ dealId })
  return `${tab.to}?${params.toString()}`
}

export function documentTypeLabel(type?: string | null) {
  const value = String(type || '').trim()
  if (!value) return '未分类'
  return DOCUMENT_TYPE_OPTIONS.find((item) => item.value === value)?.label || value
}

export function dimensionLabel(dimension?: string | null) {
  const value = String(dimension || '').trim()
  if (!value) return '未分类'
  return EVIDENCE_DIMENSIONS.find((item) => item.value === value)?.label || value
}

export function agentLabel(agentId?: string | null) {
  const value = String(agentId || '').trim()
  if (!value) return '未知智能体'
  return IC_AGENT_OPTIONS.find((item) => item.value === value)?.label || value
}

export function validateR3Action(skip: boolean, skipReason: string) {
  if (skip && !skipReason.trim()) return 'R3 skip 必须填写 skip_reason，方便审计回放。'
  return ''
}

export function validateHumanConfirmationDraft(payload: {
  status: string
  overrideReason?: string | null
  overrideDecision?: string | null
  overrideScore?: string | number | null
}) {
  const status = payload.status.trim().toLowerCase()
  const reason = payload.overrideReason?.toString().trim() || ''
  const decision = payload.overrideDecision?.toString().trim() || ''
  const scoreText = payload.overrideScore?.toString().trim() || ''

  if ((status === 'rejected' || status === 'override') && !reason) {
    return '驳回或 override 必须填写理由。'
  }
  if (status === 'override' && !decision) {
    return 'override 必须填写 override decision。'
  }
  if (scoreText) {
    const score = Number(scoreText)
    if (!Number.isFinite(score) || score < 0 || score > 100) {
      return 'override score 必须是 0-100 的数字。'
    }
  }
  return ''
}

export function documentTitle(document: DealDocument) {
  return document.original_filename || document.filename || document.document_id
}

const MATERIAL_STATUS_LABELS: Record<string, string> = {
  uploaded: '已上传',
  not_started: '已上传',
  submitting: '提交解析中',
  queued: '排队中',
  parsing: '解析中',
  processing: '解析中',
  archiving: '归档中',
  review_required: '质量待确认',
  pending: '质量待确认',
  ready: '可用于分析',
  ready_with_restrictions: '可用于文本分析，财务受限',
  failed: '解析失败',
  cancelled: '解析已取消',
  interrupted: '解析已中断',
  blocked: '质量未通过',
  disabled: '分析源已停用',
  superseded: '已被新版替代',
}

const TERMINAL_PARSE_STATUSES = new Set(['succeeded', 'completed', 'success', 'done', 'finished', 'failed', 'cancelled', 'interrupted'])

export function materialStatusValue(material: PrimaryMarketMaterial) {
  if (material.document_status === 'superseded') return 'superseded'
  if (material.document_status === 'deleted') return 'deleted'
  const parseStatus = String(material.parse_status || material.parse_run?.status || material.current_parse_run?.status || '')
  if (parseStatus && parseStatus !== 'succeeded') return parseStatus
  return String(material.analysis_source_status || (parseStatus === 'succeeded' ? 'review_required' : material.status || 'uploaded'))
}

export function materialStatusLabel(material: PrimaryMarketMaterial) {
  const value = materialStatusValue(material)
  return MATERIAL_STATUS_LABELS[value] || value || '状态未知'
}

export function materialStatusTone(material: PrimaryMarketMaterial): BadgeTone {
  const value = materialStatusValue(material)
  if (['ready', 'indexed'].includes(value)) return 'success'
  if (['failed', 'blocked', 'deleted'].includes(value)) return 'error'
  if (['ready_with_restrictions', 'review_required', 'pending', 'cancelled', 'interrupted'].includes(value)) return 'warning'
  if (value === 'superseded' || value === 'disabled') return 'neutral'
  return statusTone(value)
}

export function isMaterialPolling(material: PrimaryMarketMaterial) {
  const status = String(material.parse_status || material.parse_run?.status || material.current_parse_run?.status || '')
  return Boolean(status && !TERMINAL_PARSE_STATUSES.has(status))
}

function capabilityStatus(value: PrimaryMarketMaterialCapabilityValue | undefined) {
  if (typeof value === 'boolean') return value ? 'ready' : 'blocked'
  if (typeof value === 'string') return value
  if (!value) return 'pending'
  if (value.ready === true) return value.restricted ? 'ready_with_restrictions' : 'ready'
  if (value.ready === false && !value.status) return 'blocked'
  return String(value.status || 'pending')
}

function capabilityReason(value: PrimaryMarketMaterialCapabilityValue | undefined) {
  if (!value || typeof value !== 'object') return ''
  return value.reason || value.warnings?.[0] || ''
}

function capabilityTone(status: string): BadgeTone {
  if (['ready', 'indexed'].includes(status)) return 'success'
  if (['blocked', 'failed'].includes(status)) return 'error'
  if (['ready_with_restrictions', 'review_required', 'pending', 'not_requested', 'queued', 'indexing'].includes(status)) return 'warning'
  return statusTone(status)
}

export interface PrimaryMarketCapabilityRow {
  id: 'text_evidence' | 'source_trace' | 'financial_facts' | 'semantic_index'
  label: string
  status: string
  detail: string
  tone: BadgeTone
}

export function materialCapabilities(material: PrimaryMarketMaterial): PrimaryMarketCapabilityRow[] {
  const capabilities: PrimaryMarketMaterialCapabilities = material.capabilities
    || material.parse_run?.capabilities
    || material.current_parse_run?.capabilities
    || {}
  const sourceTrace = capabilities.source_page_trace ?? capabilities.source_trace ?? capabilities.page_trace
  const semanticIndex = capabilities.semantic_index ?? capabilities.indexing ?? material.index_status
  const entries: Array<[PrimaryMarketCapabilityRow['id'], string, PrimaryMarketMaterialCapabilityValue | undefined]> = [
    ['text_evidence', '文本', capabilities.text_evidence],
    ['source_trace', '页码', sourceTrace],
    ['financial_facts', '财务', capabilities.financial_facts],
    ['semantic_index', '索引', semanticIndex],
  ]
  return entries.map(([id, label, value]) => {
    const status = capabilityStatus(value)
    return {
      id,
      label,
      status,
      detail: capabilityReason(value),
      tone: capabilityTone(status),
    }
  })
}

export function materialVersionLabel(material: PrimaryMarketMaterial) {
  if (material.version !== null && material.version !== undefined && material.version !== '') {
    return `V${material.version}`
  }
  const chainLength = material.version_chain?.length || 0
  return chainLength ? `V${chainLength}` : '当前版本'
}

export function withMaterialVersions(materials: PrimaryMarketMaterial[]) {
  const prospectuses = materials
    .filter((material) => material.document_type === 'prospectus' || material.document_profile === 'cn_a_share_prospectus')
    .sort((a, b) => new Date(a.created_at || '').getTime() - new Date(b.created_at || '').getTime())
  const versionById = new Map(prospectuses.map((material, index) => [material.document_id, index + 1]))
  const versionChain = prospectuses.map((material, index) => ({
    document_id: material.document_id,
    supersedes_document_id: material.supersedes_document_id,
    superseded_by_document_id: material.superseded_by_document_id,
    version: material.version ?? index + 1,
    is_current: material.is_active_source ?? (
      material.document_status === 'active' && !material.superseded_by_document_id && index === prospectuses.length - 1
    ),
  }))
  return materials.map((material) => {
    const version = versionById.get(material.document_id)
    return version ? { ...material, version: material.version ?? version, version_chain: material.version_chain || versionChain } : material
  })
}

export function exchangeLabel(exchange?: string | null) {
  return PROSPECTUS_EXCHANGE_OPTIONS.find((item) => item.value === exchange)?.label || text(exchange, '交易所未设置')
}

export function boardLabel(board?: string | null) {
  return PROSPECTUS_BOARD_OPTIONS.find((item) => item.value === board)?.label || text(board, '板块未设置')
}

export function filingStageLabel(stage?: string | null) {
  return PROSPECTUS_FILING_STAGE_OPTIONS.find((item) => item.value === stage)?.label || text(stage, '文件阶段未设置')
}

export function materialsFromResponse(payload: PrimaryMarketMaterialsResponse) {
  return sortDocuments(payload.materials || payload.documents || []) as PrimaryMarketMaterial[]
}

export function materialFromResponse(payload: PrimaryMarketMaterialResponse) {
  const material = payload.document || payload.material
  if (!material) return null
  const analysisSource = payload.analysis_source || {}
  const analysisSourceStatus = typeof analysisSource.status === 'string' ? analysisSource.status : material.analysis_source_status
  return {
    ...material,
    parse_run: payload.parse_run || material.parse_run || null,
    current_parse_run: payload.current_parse_run || material.current_parse_run || null,
    quality_report: payload.quality || material.quality_report || null,
    capabilities: (
      analysisSource.capabilities && typeof analysisSource.capabilities === 'object'
        ? analysisSource.capabilities as PrimaryMarketMaterialCapabilities
        : material.capabilities
    ) || material.current_parse_run?.capabilities || material.parse_run?.capabilities || null,
    analysis_source_status: analysisSourceStatus,
    is_active_source: typeof analysisSource.is_active === 'boolean'
      ? analysisSource.is_active
      : material.is_active_source ?? Boolean(
        analysisSource.parse_run_id
        && material.current_parse_run_id
        && analysisSource.parse_run_id === material.current_parse_run_id
        && ['ready', 'ready_with_restrictions'].includes(String(analysisSource.status || ''))
      ),
    source_id: typeof analysisSource.source_id === 'string' ? analysisSource.source_id : material.source_id,
    status_url: payload.status_url || material.status_url || null,
    reused: payload.reused ?? material.reused,
  } satisfies PrimaryMarketMaterial
}

export function mergeMaterialParseStatus(
  material: PrimaryMarketMaterial,
  payload: PrimaryMarketMaterialParseStatusResponse,
) {
  const updated: Partial<PrimaryMarketMaterial> = payload.document || payload.material || {}
  const parseRun = payload.parse_run || updated.parse_run || updated.current_parse_run || material.parse_run || material.current_parse_run
  const analysisSource = payload.analysis_source || {}
  const sourceCapabilities = analysisSource.capabilities && typeof analysisSource.capabilities === 'object'
    ? analysisSource.capabilities
    : null
  return {
    ...material,
    ...updated,
    parse_run: parseRun || null,
    current_parse_run: parseRun || null,
    parse_status: payload.parse_status || updated.parse_status || parseRun?.parse_status || parseRun?.status || material.parse_status,
    analysis_source_status: payload.analysis_source_status || updated.analysis_source_status || analysisSource.status || material.analysis_source_status,
    index_status: payload.index_status || updated.index_status || material.index_status,
    capabilities: payload.capabilities || updated.capabilities || sourceCapabilities || parseRun?.capabilities || material.capabilities,
    quality_report: payload.quality_report || updated.quality_report || parseRun?.quality_report || material.quality_report,
    source_id: analysisSource.source_id || updated.source_id || material.source_id,
    is_active_source: typeof analysisSource.is_active === 'boolean'
      ? analysisSource.is_active
      : updated.is_active_source ?? material.is_active_source,
  } satisfies PrimaryMarketMaterial
}

export function createdByText(document: DealDocument) {
  const user = document.created_by
  if (!user) return ''
  return user.username || (user.id !== null && user.id !== undefined ? String(user.id) : '')
}

export function sortDocuments(documents: DealDocument[]) {
  return [...documents].sort((a, b) => {
    const aTime = new Date(a.created_at || '').getTime()
    const bTime = new Date(b.created_at || '').getTime()
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0)
  })
}

export function parserLinks(document: DealDocument) {
  return [
    { label: '状态', url: document.parser_status_url },
    { label: '结果', url: document.parser_result_url },
    { label: '页面', url: document.parser_page_url },
  ].filter((item): item is { label: string; url: string } => Boolean(item.url))
}

export function parserDraftFromDocument(document: DealDocument): ParserBindDraft {
  return {
    taskId: document.parse_task_id || '',
    artifactPath: document.parsed_artifact_path || '',
    note: '',
  }
}

export function evidenceDocumentMap(report?: DealEvidenceQualityReport | null) {
  const rows = Array.isArray(report?.documents) ? report.documents as EvidenceDocumentRow[] : []
  const entries: Array<[string, EvidenceDocumentRow]> = []
  for (const row of rows) {
    const id = String(row.document_id || '')
    if (id) entries.push([id, row])
  }
  return new Map<string, EvidenceDocumentRow>(entries)
}

export function sortedDimensions(report?: DealEvidenceQualityReport | null) {
  const dimensions = Array.isArray(report?.dimensions) ? report.dimensions : []
  return [...dimensions].sort((a, b) => dimensionLabel(a).localeCompare(dimensionLabel(b), 'zh-CN'))
}

export function sortedMissingDimensions(report?: DealEvidenceQualityReport | null) {
  const dimensions = Array.isArray(report?.missing_dimensions) ? report.missing_dimensions : []
  return [...dimensions].sort((a, b) => dimensionLabel(a).localeCompare(dimensionLabel(b), 'zh-CN'))
}

export function coverageText(report?: DealEvidenceQualityReport | null) {
  if (!report) return '未构建'
  const covered = sortedDimensions(report).length
  const missing = sortedMissingDimensions(report).length
  const total = covered + missing
  if (!total) return '0/0'
  return `${covered}/${total}`
}

export function sourceLinks(item: { source_url?: string | null; artifact_url?: string | null; parser_page_url?: string | null }) {
  return [
    { label: 'source', url: item.source_url },
    { label: 'artifact', url: item.artifact_url },
    { label: 'parser', url: item.parser_page_url },
  ].filter((entry): entry is { label: string; url: string } => Boolean(entry.url))
}

export function deriveMeetingReceiptRows(bundle: MeetingBundle): MeetingReceiptRow[] {
  const readinessByAgent = new Map((bundle.workflow?.r1_agent_readiness?.agents || []).map((agent) => [agent.agent_id, agent]))
  const agentsById = new Map((bundle.agents?.agents || []).map((agent) => [agent.agent_id, agent]))
  const meetingReadinessByAgent = new Map((bundle.meetingReadiness?.profiles || []).map((profile) => [profile.profileId, profile]))

  return R1_AGENT_SEQUENCE.map((agentId) => {
    const retrieval = bundle.startupReceipts?.[agentId]
    const receipt = retrieval?.receipt || null
    const readiness = readinessByAgent.get(agentId)
    const agent = agentsById.get(agentId)
    const meetingReadiness = meetingReadinessByAgent.get(agentId)
    const receiptId = receipt?.receipt_id || meetingReadiness?.startupReceipt.receiptId || readiness?.startup_receipt_id || agent?.receipt?.receipt_id || ''
    const present = Boolean(receipt || meetingReadiness?.startupReceipt.present || receiptId || readiness?.has_startup_receipt || agent?.receipt?.present)
    const evidenceHits = numericValue(receipt?.evidence_hit_count) ?? (Array.isArray(receipt?.evidence_hits) ? receipt.evidence_hits.length : 0)

    return {
      agentId,
      label: meetingReadiness?.label || agent?.label || readiness?.label || agentLabel(agentId),
      present,
      allowed: typeof readiness?.allowed === 'boolean'
        ? readiness.allowed
        : typeof meetingReadiness?.quality.readyForFormalTask === 'boolean'
          ? meetingReadiness.quality.readyForFormalTask
          : null,
      receiptId,
      evidenceHits: evidenceHits || meetingReadiness?.startupReceipt.evidenceHits || 0,
      sharedHits: numericValue(receipt?.shared_hits) ?? meetingReadiness?.startupReceipt.sharedHits ?? 0,
      privateHits: numericValue(receipt?.private_hits) ?? meetingReadiness?.startupReceipt.privateHits ?? 0,
      collections: receiptUnknownArray(receipt, 'collections'),
      physicalCollections: receiptUnknownArray(receipt, 'physical_collections'),
      gaps: asStringArray(receipt?.gaps).length ? asStringArray(receipt?.gaps) : meetingReadiness?.startupReceipt.gaps || [],
      warnings: [...asStringArray(readiness?.warnings), ...(meetingReadiness?.quality.warnings || [])],
      createdAt: receipt?.created_at,
    }
  })
}

function runtimeHealthTone(health: string): BadgeTone {
  const value = health.trim().toLowerCase()
  if (!value || value === 'unknown') return 'neutral'
  if (['running', 'ready', 'healthy', 'ok', 'enabled'].includes(value)) return 'success'
  if (['disabled', 'stopped', 'unavailable', 'down', 'error', 'failed'].includes(value)) return 'error'
  if (value.includes('warn') || value.includes('pending') || value.includes('starting')) return 'warning'
  return 'info'
}

function runtimeHealthLabel(health: string) {
  const value = health.trim().toLowerCase()
  if (!value || value === 'unknown') return 'unknown'
  if (['running', 'ready', 'healthy', 'ok', 'enabled'].includes(value)) return 'running'
  if (['disabled', 'stopped', 'unavailable', 'down'].includes(value)) return value
  return health
}

function profileReadinessByAgent(bundle: MeetingBundle) {
  return new Map((bundle.meetingReadiness?.profiles || []).map((profile) => [profile.profileId, profile]))
}

function profileReadinessFor(bundle: MeetingBundle, agentId: string): PrimaryMarketMeetingReadinessProfile | undefined {
  return profileReadinessByAgent(bundle).get(agentId)
}

const IC_PRIVATE_COLLECTIONS: Record<string, string> = {
  siq_ic_master_coordinator: 'ic_master_coordinator',
  siq_ic_chairman: 'ic_chairman',
  siq_ic_strategist: 'ic_strategist',
  siq_ic_sector_expert: 'ic_sector_expert',
  siq_ic_finance_auditor: 'ic_finance_auditor',
  siq_ic_legal_scanner: 'ic_legal_scanner',
  siq_ic_risk_controller: 'ic_risk_controller',
}

function retrievalStatusTone(status: string): BadgeTone {
  const value = status.toLowerCase()
  if (['ready', 'complete', 'completed', 'success'].includes(value)) return 'success'
  if (['blocked', 'failed', 'error', 'unavailable'].includes(value)) return 'error'
  if (['degraded', 'partial', 'warning', 'pending'].includes(value)) return 'warning'
  return 'neutral'
}

export function deriveMeetingAgentReadinessRows(bundle: MeetingBundle): MeetingAgentReadinessRow[] {
  const receiptByAgent = new Map(deriveMeetingReceiptRows(bundle).map((row) => [row.agentId, row]))
  const scoreByAgent = new Map(deriveMeetingScoreRows(bundle).map((row) => [row.agentId, row]))
  const agentsById = new Map((bundle.agents?.agents || []).map((agent) => [agent.agent_id, agent]))
  const readinessByAgent = profileReadinessByAgent(bundle)

  return IC_AGENT_OPTIONS.map((agentOption) => {
    const agentId = agentOption.value
    const readiness = readinessByAgent.get(agentId)
    const receipt = receiptByAgent.get(agentId)
    const score = scoreByAgent.get(agentId)
    const agent = agentsById.get(agentId)
    const rawReceipt = bundle.startupReceipts?.[agentId]?.receipt
    const runtimeHealth = readiness?.runtime.health
      || (agent?.runtime?.enabled === false ? 'disabled' : agent?.runtime?.enabled === true ? 'running' : 'unknown')
    const sourceFiles = readiness?.contract.sourceFiles || []
    const contractSourceText = sourceFiles.length ? sourceFiles.join('/') : 'missing'
    const receiptPresent = Boolean(readiness?.startupReceipt.present ?? receipt?.present ?? rawReceipt)
    const r1ReportPresent = agentOption.r1 ? Boolean(readiness?.r1Report.present ?? score?.hasReport) : null
    const blockingReasons = readiness?.quality.blockingReasons || []
    const logicalCollections = readiness?.startupReceipt.collections?.length
      ? readiness.startupReceipt.collections
      : asStringArray(rawReceipt?.collections)
    const physicalCollections = readiness?.startupReceipt.physicalCollections?.length
      ? readiness.startupReceipt.physicalCollections
      : asStringArray(rawReceipt?.physical_collections)
    const expectedPrivateCollection = IC_PRIVATE_COLLECTIONS[agentId] || agentId.replace(/^siq_/, '')
    const sharedCollection = readiness?.startupReceipt.sharedCollection
      || text(rawReceipt?.shared_collection, '')
      || physicalCollections.find((item) => item === 'ic_collaboration_shared')
      || 'ic_collaboration_shared'
    const privateCollection = readiness?.startupReceipt.privateCollection
      || text(rawReceipt?.private_collection, '')
      || physicalCollections.find((item) => item !== 'ic_collaboration_shared')
      || expectedPrivateCollection
    const projectEvidenceHits = numericValue(rawReceipt?.project_evidence_hits)
      ?? numericValue(rawReceipt?.shared_hits)
      ?? readiness?.startupReceipt.sharedHits
      ?? 0
    const backgroundKnowledgeHits = numericValue(rawReceipt?.background_knowledge_hits)
      ?? numericValue(rawReceipt?.private_hits)
      ?? readiness?.startupReceipt.privateHits
      ?? 0
    const explicitRetrievalStatus = readiness?.startupReceipt.retrievalStatus
      || text(rawReceipt?.retrieval_status, '')
    const retrievalStatus = explicitRetrievalStatus
      || (!receiptPresent ? 'blocked' : backgroundKnowledgeHits > 0 ? 'ready' : 'degraded')
    const degradedReasons = [
      ...(readiness?.startupReceipt.degradedReasons || []),
      ...asStringArray(rawReceipt?.degraded_reasons),
      ...(receiptPresent && backgroundKnowledgeHits === 0 ? ['private_background_hits_missing'] : []),
    ].filter((item, index, values) => item && values.indexOf(item) === index)
    const receiptBlockingReasons = [
      ...(readiness?.startupReceipt.blockingReasons || []),
      ...asStringArray(rawReceipt?.blocking_reasons),
    ]
    const allBlockingReasons = [...blockingReasons, ...receiptBlockingReasons]
      .filter((item, index, values) => item && values.indexOf(item) === index)
    const evidenceSnapshotHash = readiness?.startupReceipt.evidenceSnapshotHash
      || text(rawReceipt?.evidence_snapshot_hash, '')
    const capabilityRestrictions = (readiness?.startupReceipt.capabilityRestrictions?.length
      ? readiness.startupReceipt.capabilityRestrictions
      : asStringArray(rawReceipt?.capability_restrictions))
    const stale = Boolean(readiness?.startupReceipt.stale || readiness?.quality.stale || rawReceipt?.stale)
    return {
      agentId,
      label: readiness?.label || agent?.label || agentOption.label,
      runtimeHealth,
      runtimeTone: runtimeHealthTone(runtimeHealth),
      receiptPresent,
      receiptId: readiness?.startupReceipt.receiptId || receipt?.receiptId || '',
      r1ReportPresent,
      r1ReportScore: readiness?.r1Report.score ?? score?.score ?? null,
      r1ReportRecommendation: readiness?.r1Report.recommendation || score?.recommendation || '',
      contractSourceCount: sourceFiles.length,
      contractSourceText,
      contractVersion: readiness?.contract.version || 'unversioned',
      sharedCollection,
      privateCollection,
      logicalCollections,
      physicalCollections,
      projectEvidenceHits,
      backgroundKnowledgeHits,
      retrievalStatus,
      retrievalTone: retrievalStatusTone(retrievalStatus),
      degradedReasons,
      evidenceSnapshotHash,
      capabilityRestrictions,
      phaseTaskStatus: readiness?.phaseTaskStatus || 'unavailable',
      qualityStatus: readiness?.quality.status || (allBlockingReasons.length ? 'blocked' : readiness?.quality.warnings.length ? 'warning' : 'ready'),
      stale,
      readyForFormalTask: (readiness?.quality.readyForFormalTask ?? (receipt?.allowed !== false && receiptPresent))
        && retrievalStatus.toLowerCase() !== 'blocked'
        && !stale,
      blockingReasons: allBlockingReasons,
      warnings: readiness?.quality.warnings || receipt?.warnings || [],
    }
  })
}

function generationModeFrom(...values: unknown[]) {
  for (const value of values) {
    const record = asRecord(value)
    const mode = text(
      record?.generation_mode
        || record?.generationMode
        || record?.execution_mode
        || record?.executionMode,
      '',
    ).trim()
    if (mode) return mode
  }
  return 'unavailable'
}

function isDeterministicFallback(mode: string) {
  const value = mode.toLowerCase()
  return value.includes('deterministic') || value.includes('fallback')
}

function observabilityStatus(status: string, fallback = 'missing') {
  const value = status.trim()
  return value || fallback
}

export function deriveWorkflowPhaseObservability(bundle: MeetingBundle): WorkflowPhaseObservabilityRow[] {
  const artifacts = phaseArtifactByPhase(bundle.phaseArtifacts)
  const r1Artifact = artifacts.get('R1')
  const r2Artifact = artifacts.get('R2')
  const r3Artifact = artifacts.get('R3')
  const r4Artifact = artifacts.get('R4')
  const decisionRaw = bundle.decision?.decision || {}
  const rows = [
    {
      phase: 'R0',
      label: 'R0 准入与范围',
      status: observabilityStatus(bundle.preflight?.status || ''),
      blocking: bundle.preflight?.status === 'fail',
      generationMode: generationModeFrom(bundle.preflight),
      detail: bundle.preflight?.checks?.filter((check) => check.status !== 'pass').map((check) => check.message).slice(0, 2).join(' / ') || '项目身份、Evidence 与检索范围',
    },
    {
      phase: 'R1A/R1B',
      label: 'R1 独立研究与交叉验证',
      status: observabilityStatus(r1Artifact?.status || ''),
      blocking: Boolean(r1Artifact?.blocking),
      generationMode: generationModeFrom(r1Artifact),
      detail: `${bundle.workflow?.agent_reports?.filter((report) => report.has_report).length || 0}/${R1_AGENT_SEQUENCE.length} 正式报告`,
    },
    {
      phase: 'R1.5',
      label: 'R1.5 分歧与裁决',
      status: observabilityStatus(bundle.disputes?.status || ''),
      blocking: Number(bundle.disputes?.counts?.unresolved || 0) > 0,
      generationMode: generationModeFrom(bundle.disputes),
      detail: `${bundle.disputes?.counts?.disputes || 0} 个争议 / ${bundle.disputes?.counts?.unresolved || 0} 个未解决`,
    },
    {
      phase: 'R2',
      label: 'R2 专家修订',
      status: observabilityStatus(
        bundle.r2Reports?.counts?.reports
          ? Number(bundle.r2Reports.counts.warn || 0) > 0 ? 'warn' : 'pass'
          : r2Artifact?.status || '',
      ),
      blocking: Boolean(r2Artifact?.blocking),
      generationMode: generationModeFrom(bundle.r2Reports, r2Artifact),
      detail: `${bundle.r2Reports?.counts?.reports || 0} 份修订 / ${bundle.r2Reports?.counts?.revisions || 0} 个 delta`,
    },
    {
      phase: 'R3',
      label: 'R3 红蓝对抗',
      status: observabilityStatus(bundle.r3Review?.status || r3Artifact?.status || ''),
      blocking: Boolean(r3Artifact?.blocking),
      generationMode: generationModeFrom(bundle.r3Review, r3Artifact),
      detail: bundle.r3Review?.skipped
        ? `skip: ${bundle.r3Review.skip_reason || 'reason unavailable'}`
        : `${bundle.r3Review?.counts?.reports || 0} 个回合产物 / ${bundle.r3Review?.counts?.challenges || 0} 个 challenge`,
    },
    {
      phase: 'R4',
      label: 'R4 决策与质量门禁',
      status: observabilityStatus(bundle.decision?.contract?.status || r4Artifact?.status || ''),
      blocking: Boolean(bundle.decision?.contract?.missing_required_fields?.length),
      generationMode: generationModeFrom(decisionRaw, bundle.decision?.contract, r4Artifact),
      detail: `${bundle.decision?.contract?.missing_required_fields?.length || 0} 个必填缺失 / human ${bundle.decision?.contract?.human_confirmation?.status || 'pending'}`,
    },
  ]
  return rows.map((row) => ({
    ...row,
    tone: row.blocking ? 'error' : statusTone(row.status),
    deterministicFallback: isDeterministicFallback(row.generationMode),
  }))
}

export function deriveAgentHandoffRows(bundle: MeetingBundle): AgentHandoffObservabilityRow[] {
  const seen = new Set<string>()
  return (bundle.audit?.audit.events || [])
    .filter((event) => event.event_type === 'ic_agent_handoff_persisted')
    .map((event, index) => {
      const id = text(event.handoff_id, `handoff-${index + 1}`)
      const fromAgentId = text(event.from_agent_id, 'workflow')
      const toAgentId = text(event.to_agent_id, 'unknown')
      return {
        id,
        phase: text(event.phase, 'unknown'),
        fromAgentId,
        fromLabel: fromAgentId === 'workflow' ? 'Workflow' : agentLabel(fromAgentId),
        toAgentId,
        toLabel: agentLabel(toAgentId),
        workflowRunId: text(event.workflow_run_id, ''),
        inputDigest: text(event.input_digest, ''),
        createdAt: text(event.created_at, ''),
      }
    })
    .filter((row) => {
      if (seen.has(row.id)) return false
      seen.add(row.id)
      return true
    })
    .slice(-20)
    .reverse()
}

export function deriveR15DisputeBoard(bundle: MeetingBundle): R15DisputeBoardRow[] {
  const topMode = generationModeFrom(bundle.disputes)
  return (bundle.disputes?.disputes || bundle.workflow?.disputes || []).map((dispute, index) => {
    const ruling = asRecord(dispute.chairman_ruling)
    const generationMode = generationModeFrom(ruling, { generation_mode: topMode })
    return {
      id: text(dispute.dispute_id, `DISPUTE-${index + 1}`),
      topic: text(dispute.topic || dispute.dimension, '未命名争议'),
      severity: text(dispute.severity, 'unknown'),
      resolved: dispute.resolved === true,
      positionCount: Number(dispute.position_count || 0),
      evidenceIds: asStringArray(dispute.evidence_ids),
      agents: asStringArray(dispute.agent_ids),
      ruling: text(ruling?.decision || ruling?.ruling || ruling?.status, '未裁决'),
      generationMode,
      fallback: isDeterministicFallback(generationMode),
      followups: asStringArray(dispute.required_followups || ruling?.required_followups),
    }
  })
}

export function deriveR2DeltaRows(bundle: MeetingBundle): R2DeltaRow[] {
  return (bundle.r2Reports?.agents || []).map((report) => {
    const r1Score = numericValue(report.r1_score)
    const r2Score = numericValue(report.r2_score ?? report.score)
    const explicitChange = numericValue(report.score_change)
    return {
      agentId: report.agent_id,
      label: report.label || agentLabel(report.agent_id),
      status: report.status || (report.has_report ? 'warn' : 'missing'),
      r1Score,
      r2Score,
      scoreChange: explicitChange ?? (r1Score !== null && r2Score !== null ? Number((r2Score - r1Score).toFixed(2)) : null),
      recommendation: report.recommendation || '',
      revisions: Number(report.revision_count || 0),
      summary: report.summary || '',
      artifactAvailable: report.artifact_available === true,
    }
  })
}

export function deriveR3Timeline(bundle: MeetingBundle): R3TimelineRow[] {
  const topMode = generationModeFrom(bundle.r3Review)
  return (bundle.r3Review?.reports || []).map((report, index) => {
    const generationMode = generationModeFrom(report, { generation_mode: topMode })
    return {
      id: `${report.agent_id || 'r3'}-${index + 1}`,
      agentId: report.agent_id,
      label: report.label || agentLabel(report.agent_id),
      stance: report.stance || 'unavailable',
      status: report.status || 'unknown',
      summary: report.summary || '',
      challengeCount: Number(report.challenge_count || 0),
      evidenceCount: Number(report.evidence_count || 0),
      createdAt: report.created_at,
      generationMode,
      fallback: isDeterministicFallback(generationMode),
    }
  })
}

function findingTexts(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.map((item) => {
    if (typeof item === 'string') return item
    const record = asRecord(item)
    const claimId = text(record?.claim_id, '')
    const finding = text(
      record?.message || record?.detail || record?.finding || record?.action || record?.id || record?.severity,
      '',
    )
    return [claimId, finding].filter(Boolean).join(' · ')
  }).filter(Boolean)
}

export function deriveR4QualityObservability(bundle: MeetingBundle): R4QualityObservability {
  const contract = bundle.decision?.contract
  const decision = bundle.decision?.decision || {}
  const quality = asRecord(bundle.decision?.quality || decision.quality || decision.report_quality || decision.quality_gate)
  const factcheck = asRecord(bundle.decision?.factcheck || decision.factcheck || decision.fact_check || quality?.factcheck)
  const generationMode = generationModeFrom(decision, contract)
  const human = {
    ...(contract?.human_confirmation || {}),
    ...(asRecord(decision.human_confirmation) || {}),
  }
  const humanStatus = text(human.status || (human.confirmed ? 'confirmed' : 'pending'))
  const humanConfirmed = human.confirmed === true || ['confirmed', 'approved', 'overridden'].includes(humanStatus.toLowerCase())
  const reportId = text(decision.report_id, '')
  const reportRevision = typeof decision.revision === 'number' ? decision.revision : null
  const workflowRunId = text(decision.workflow_run_id, '')
  const evidenceSnapshotHash = text(decision.evidence_snapshot_hash, '')
  const sha256Pattern = /^[a-f0-9]{64}$/i
  const attestationBound = Boolean(
    human.attestation_schema_version === 'siq_ic_human_confirmation_attestation_v1'
      && reportId
      && reportRevision
      && workflowRunId
      && sha256Pattern.test(evidenceSnapshotHash)
      && human.report_id === reportId
      && human.report_revision === reportRevision
      && human.workflow_run_id === workflowRunId
      && human.evidence_snapshot_hash === evidenceSnapshotHash
      && sha256Pattern.test(text(human.decision_sha256, ''))
      && sha256Pattern.test(text(human.quality_sha256, ''))
      && sha256Pattern.test(text(human.factcheck_sha256, ''))
      && quality?.report_id === reportId
      && quality.report_revision === reportRevision
      && quality.evidence_snapshot_hash === evidenceSnapshotHash
      && factcheck?.report_id === reportId
      && factcheck.report_revision === reportRevision
      && factcheck.evidence_snapshot_hash === evidenceSnapshotHash,
  )
  const qualityAttentionChecks = Array.isArray(quality?.checks)
    ? quality.checks.filter((item) => text(asRecord(item)?.status, '').toLowerCase() !== 'pass')
    : []
  const findings = Array.from(new Set([
    ...findingTexts(quality?.findings),
    ...findingTexts(quality?.warnings),
    ...findingTexts(quality?.blocking_reasons),
    ...findingTexts(qualityAttentionChecks),
    ...findingTexts(factcheck?.findings),
    ...findingTexts(factcheck?.warnings),
    ...findingTexts(factcheck?.contradictions),
    ...findingTexts(factcheck?.unsupported_claims),
    ...findingTexts(factcheck?.required_repairs),
  ]))
  return {
    status: contract?.status || (bundle.decision ? 'warn' : 'missing'),
    generationMode,
    fallback: isDeterministicFallback(generationMode),
    factcheckStatus: text(factcheck?.status, 'unavailable'),
    qualityStatus: text(quality?.status, contract?.status || 'unavailable'),
    humanStatus,
    humanConfirmed,
    attestationStatus: humanConfirmed ? (attestationBound ? 'bound' : 'incomplete') : 'pending',
    reportId: text(human.report_id || reportId, ''),
    reportRevision: typeof human.report_revision === 'number' ? human.report_revision : reportRevision,
    workflowRunId: text(human.workflow_run_id || workflowRunId, ''),
    missingRequired: contract?.missing_required_fields || [],
    missingAdvisory: contract?.missing_advisory_fields || [],
    findings,
    reportPath: bundle.decision?.report_path || contract?.artifacts?.markdown?.path || '',
  }
}

function readinessSummaryChips(bundle: MeetingBundle): MeetingReadinessChip[] {
  const rows = deriveMeetingAgentReadinessRows(bundle)
  const r1Rows = rows.filter((row) => R1_AGENT_SEQUENCE.includes(row.agentId))
  const runtimeRunning = bundle.meetingReadiness?.summary.runtimeRunning ?? rows.filter((row) => runtimeHealthLabel(row.runtimeHealth) === 'running').length
  const receiptsPresent = bundle.meetingReadiness?.summary.receiptPresent ?? r1Rows.filter((row) => row.receiptPresent).length
  const reportsPresent = bundle.meetingReadiness?.summary.r1ReportsPresent ?? r1Rows.filter((row) => row.r1ReportPresent).length
  const blockingProfiles = bundle.meetingReadiness?.summary.blockingProfiles || r1Rows.filter((row) => row.blockingReasons.length).map((row) => row.agentId)
  return [
    {
      id: 'runtime',
      label: `Hermes ${runtimeRunning}/${IC_AGENT_OPTIONS.length}`,
      tone: runtimeRunning >= IC_AGENT_OPTIONS.length ? 'success' : runtimeRunning > 0 ? 'warning' : 'neutral',
      detail: 'Hermes runtime running count',
    },
    {
      id: 'receipt',
      label: `Receipts ${receiptsPresent}/${R1_AGENT_SEQUENCE.length}`,
      tone: receiptsPresent >= R1_AGENT_SEQUENCE.length ? 'success' : receiptsPresent > 0 ? 'warning' : 'error',
      detail: 'R1 startup-retrieval receipt readiness',
    },
    {
      id: 'report',
      label: `R1 reports ${reportsPresent}/${R1_AGENT_SEQUENCE.length}`,
      tone: reportsPresent >= R1_AGENT_SEQUENCE.length ? 'success' : reportsPresent > 0 ? 'warning' : 'neutral',
      detail: 'Formal R1 report readiness',
    },
    {
      id: 'blocking',
      label: `Blocking ${blockingProfiles.length}`,
      tone: blockingProfiles.length ? 'error' : 'success',
      detail: blockingProfiles.length ? blockingProfiles.map((agentId) => agentLabel(agentId)).join(' / ') : 'No blocking profiles reported',
    },
  ]
}

export function deriveAgentReadinessChips(
  bundle: MeetingBundle,
  agentId: string,
  mode: PrimaryMarketMeetingMode = 'single',
): MeetingReadinessChip[] {
  if (mode === 'committee' || mode === 'workflow') return readinessSummaryChips(bundle)
  const row = deriveMeetingAgentReadinessRows(bundle).find((item) => item.agentId === agentId)
  if (!row) return []
  return [
    {
      id: 'runtime',
      label: `Hermes ${runtimeHealthLabel(row.runtimeHealth)}`,
      tone: row.runtimeTone,
      detail: `${row.label} runtime: ${row.runtimeHealth}`,
    },
    {
      id: 'receipt',
      label: row.receiptPresent === null ? 'Receipt n/a' : `Receipt ${row.receiptPresent ? 'present' : 'missing'}`,
      tone: row.receiptPresent === null ? 'neutral' : row.receiptPresent ? 'success' : 'warning',
      detail: row.receiptId || (row.receiptPresent === null ? 'This profile does not require R1 startup retrieval' : 'Startup retrieval receipt status'),
    },
    {
      id: 'report',
      label: row.r1ReportPresent === null ? 'R1 report n/a' : `R1 report ${row.r1ReportPresent ? 'present' : 'missing'}`,
      tone: row.r1ReportPresent === null ? 'neutral' : row.r1ReportPresent ? 'success' : 'warning',
      detail: row.r1ReportRecommendation || (row.r1ReportScore === null ? 'Formal R1 report status' : `score ${row.r1ReportScore}`),
    },
    {
      id: 'profile',
      label: row.contractSourceCount ? 'Profile loaded' : 'Profile missing',
      tone: row.contractSourceCount ? 'success' : 'warning',
      detail: row.contractSourceText,
    },
  ]
}

export function deriveAgentReadinessLine(
  bundle: MeetingBundle,
  agentId: string,
  mode: PrimaryMarketMeetingMode = 'single',
) {
  const readiness = profileReadinessFor(bundle, agentId)
  const chips = deriveAgentReadinessChips(bundle, agentId, mode)
  if (!chips.length && !readiness) return ''
  return chips.map((chip) => chip.label).join(' · ')
}

function parseQualityChecksFromBody(body: string): MeetingQualityCheck[] {
  return [...String(body || '').matchAll(/([a-z][a-z0-9_.-]+)=([a-z_]+)/gi)].map((match) => ({
    id: match[1],
    status: match[2],
  }))
}

function qualityChipLabel(check: MeetingQualityCheck) {
  const id = check.id.toLowerCase()
  const status = check.status.toLowerCase()
  if (id === 'role.boundary') return status === 'pass' ? 'role ok' : 'boundary warning'
  if (id === 'evidence.reference') return status === 'pass' ? 'evidence ok' : 'needs evidence'
  if (id === 'verified_assumed') return status === 'pass' ? 'verified/assumed ok' : 'needs verified/assumed'
  if (id === 'next_action') return status === 'pass' ? 'next action ok' : 'needs next action'
  return `${id} ${status}`
}

export function deriveMeetingEventQualityChips(event: MeetingEvent): MeetingReadinessChip[] {
  if (event.type !== 'quality_check' && !event.quality) return []
  const checks = event.quality?.checks?.length ? event.quality.checks : parseQualityChecksFromBody(event.body)
  if (!checks.length) {
    const status = event.quality?.status || event.tone || 'warn'
    return [{
      id: `${event.id}-quality`,
      label: `quality ${status}`,
      tone: statusTone(status),
      detail: event.body || event.title,
    }]
  }
  return checks.slice(0, 4).map((check) => ({
    id: `${event.id}-${check.id}`,
    label: qualityChipLabel(check),
    tone: statusTone(check.status),
    detail: check.detail || `${check.id}=${check.status}`,
  }))
}

export function deriveMeetingScoreRows(bundle: MeetingBundle): MeetingScoreRow[] {
  const order = new Map(R1_AGENT_SEQUENCE.map((agentId, index) => [agentId, index]))
  return [...(bundle.workflow?.agent_reports || [])]
    .sort((a, b) => (order.get(a.agent_id) ?? 999) - (order.get(b.agent_id) ?? 999))
    .map((report) => {
      const score = numericValue(report.score)
      return {
        agentId: report.agent_id,
        label: report.label || agentLabel(report.agent_id),
        hasReport: report.has_report === true,
        score,
        scoreText: score === null ? text(report.score, '-') : score.toFixed(1).replace(/\.0$/, ''),
        recommendation: text(report.recommendation, '-'),
        confidence: text(report.confidence, '-'),
        verifiedCount: numericValue(report.verified_count) ?? 0,
        assumedCount: numericValue(report.assumed_count) ?? 0,
        receiptId: report.startup_receipt_id || '',
        artifactPath: report.artifact_path || '',
      }
    })
}

export function deriveMeetingScoringSummary(rows: MeetingScoreRow[]): MeetingScoringSummary {
  const scores = rows.map((row) => row.score).filter((score): score is number => typeof score === 'number')
  const total = scores.reduce((sum, score) => sum + score, 0)
  const normalizedRecommendation = (value: string) => value.trim().toLowerCase()
  return {
    count: rows.length,
    scoredCount: scores.length,
    average: scores.length ? Number((total / scores.length).toFixed(1)) : null,
    min: scores.length ? Math.min(...scores) : null,
    max: scores.length ? Math.max(...scores) : null,
    supportCount: rows.filter((row) => ['support', 'pass', 'approve', 'yes'].includes(normalizedRecommendation(row.recommendation))).length,
    opposeCount: rows.filter((row) => ['oppose', 'reject', 'no'].includes(normalizedRecommendation(row.recommendation))).length,
    watchCount: rows.filter((row) => ['watch', 'hold', 'neutral', 'caution'].includes(normalizedRecommendation(row.recommendation))).length,
  }
}

export function componentPath(dealId: string, href?: string | null) {
  if (!href) return ''
  if (href.startsWith('/')) return href
  return `/deals/${encodeURIComponent(dealId)}/${href.replace(/^\/+/, '')}`
}

export function deriveProjectRow(deal: DealSummary, status?: DealStatusResponse | null): PrimaryMarketProjectRow {
  const components = Array.isArray(status?.components) ? status.components : []
  const blockingComponents = components.filter((component) => component.blocking)
  const blockingCount = Number(status?.counts?.blocking ?? blockingComponents.length ?? 0)
  const warningCount = Number(status?.counts?.warn ?? 0)
  const missingCount = Number(status?.counts?.missing ?? 0)
  const dealStatus = String(deal.status || '')
  const completed = ['r4_completed', 'archived', 'closed'].includes(dealStatus)
  const decisionPending = Boolean(deal.final_decision && !completed)
  const ready = status?.ready_for_next_action === true
  const category = completed
    ? 'completed'
    : decisionPending
      ? 'decision_pending'
      : blockingCount > 0 || status?.status === 'fail'
        ? 'blocked'
        : ready
          ? 'ready'
          : dealStatus === 'draft' || !dealStatus
            ? 'draft'
            : 'in_progress'

  return {
    deal,
    status,
    phase: deal.current_phase || status?.sources?.workflow_phase as string || '-',
    statusLabel: statusLabel(deal.status),
    statusTone: category === 'blocked' ? 'error' : category === 'decision_pending' ? 'warning' : statusTone(deal.status),
    ready,
    nextAction: text(status?.next_action, ready ? '可继续推进' : '等待状态刷新'),
    blockingCount,
    warningCount,
    missingCount,
    category,
    blockingMessages: blockingComponents.map((component) => text(component.message || component.label || component.id)).filter(Boolean),
  }
}

export function deriveProjectMetrics(rows: PrimaryMarketProjectRow[]): PrimaryMarketMetrics {
  return {
    total: rows.length,
    active: rows.filter((row) => !['completed'].includes(row.category)).length,
    blocked: rows.filter((row) => row.category === 'blocked').length,
    ready: rows.filter((row) => row.ready).length,
    decisionPending: rows.filter((row) => row.category === 'decision_pending').length,
    completed: rows.filter((row) => row.category === 'completed').length,
  }
}

function phaseArtifactByPhase(phaseArtifacts?: DealPhaseArtifactsResponse | null) {
  return new Map((phaseArtifacts?.phases || []).map((phase) => [String(phase.phase || ''), phase]))
}

function bundleDealId(bundle: MeetingBundle) {
  return bundle.detail?.summary.deal_id
    || bundle.workflow?.workflow.deal_id
    || bundle.preflight?.deal_id
    || bundle.disputes?.deal_id
    || bundle.phaseArtifacts?.deal_id
    || bundle.decision?.contract?.deal_id
    || ''
}

function artifactStatus(artifact?: DealPhaseArtifactPhase | null) {
  if (!artifact) return 'pending'
  if (artifact.blocking) return 'blocked'
  return artifact.status || 'pending'
}

function phaseArtifactAvailable(bundle: MeetingBundle, phase: string) {
  const artifact = phaseArtifactByPhase(bundle.phaseArtifacts).get(phase)
  if (!artifact || artifact.blocking) return false
  if (artifact.status === 'pass') return true
  return Boolean(
    artifact.artifacts?.json?.available
      || artifact.artifacts?.markdown?.available
      || Number(artifact.counts?.items || 0) > 0,
  )
}

function decisionAvailable(bundle: MeetingBundle) {
  const contract = bundle.decision?.contract
  return Boolean(
    contract?.decision?.value
      || contract?.decision?.qualitative
      || contract?.generated_at
      || contract?.artifacts?.markdown?.available
      || contract?.artifacts?.html?.available
      || bundle.decision?.report_path,
  )
}

function isHumanDecisionConfirmed(status?: string | null, confirmed?: boolean | null) {
  return confirmed === true || ['confirmed', 'approved', 'overridden'].includes(String(status || '').trim().toLowerCase())
}

function coordinatorAction(action: CoordinatorNextAction) {
  return action
}

export function deriveCoordinatorNextActions(bundle: MeetingBundle): CoordinatorNextAction[] {
  const dealId = bundleDealId(bundle)
  const materialsPath = dealId ? `/primary-market/materials?dealId=${encodeURIComponent(dealId)}` : '/primary-market/materials'
  const decisionPath = dealId ? `/deals/${encodeURIComponent(dealId)}/decision` : ''
  const hasMeetingState = Boolean(bundle.detail || bundle.workflow || bundle.preflight || bundle.phaseArtifacts || bundle.decision)

  if (!hasMeetingState) {
    return [
      coordinatorAction({
        id: 'refresh',
        label: '刷新会议状态',
        phase: 'SYNC',
        mode: 'refresh',
        tone: 'neutral',
        priority: 10,
        reason: '等待项目状态、工作流和会议上下文加载完成。',
      }),
    ]
  }

  if (bundle.preflight?.status === 'fail') {
    const findings = bundle.preflight.checks.filter((check) => check.status !== 'pass')
    return [
      coordinatorAction({
        id: 'review_materials',
        label: '补充项目材料',
        phase: 'R0',
        mode: 'link',
        tone: 'error',
        priority: 10,
        reason: findings.length ? `${findings.length} 个 R0 门禁项未通过，需先补齐 evidence。` : 'R0 门禁未通过，需先回到材料与证据入口处理。',
        to: materialsPath,
      }),
      coordinatorAction({
        id: 'refresh',
        label: '刷新门禁状态',
        phase: 'R0',
        mode: 'refresh',
        tone: 'neutral',
        priority: 20,
        reason: '材料补齐后重新读取 preflight、evidence 和阶段产物。',
      }),
    ]
  }

  const receiptRows = deriveMeetingReceiptRows(bundle)
  const receiptCount = receiptRows.filter((row) => row.present).length
  const missingReceiptCount = Math.max(R1_AGENT_SEQUENCE.length - receiptCount, 0)
  if (missingReceiptCount > 0) {
    return [
      coordinatorAction({
        id: 'generate_receipts',
        label: '生成缺失 Receipt',
        phase: 'R1',
        mode: 'write',
        tone: 'warning',
        priority: 10,
        reason: `${receiptCount}/${R1_AGENT_SEQUENCE.length} 位 R1 智能体已有启动检索回执，建议先补齐上下文。`,
      }),
      coordinatorAction({
        id: 'r1_dry',
        label: '预演 R1 发言',
        phase: 'R1',
        mode: 'preview',
        tone: 'info',
        priority: 20,
        reason: '补齐 Receipt 后用 dry-run 检查 R1 串行发言是否会被门禁阻断。',
      }),
      coordinatorAction({
        id: 'review_materials',
        label: '查看材料证据',
        phase: 'R0',
        mode: 'link',
        tone: 'neutral',
        priority: 30,
        reason: '如 Receipt 命中不足，可回到材料入口补充底稿或 evidence 绑定。',
        to: materialsPath,
      }),
    ]
  }

  const scoreRows = deriveMeetingScoreRows(bundle)
  const reportCount = scoreRows.filter((row) => row.hasReport).length
  if (reportCount < R1_AGENT_SEQUENCE.length) {
    return [
      coordinatorAction({
        id: 'r1_dry',
        label: '预演 R1 发言',
        phase: 'R1',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: `${reportCount}/${R1_AGENT_SEQUENCE.length} 位 R1 智能体已形成报告，先 dry-run 检查剩余发言。`,
      }),
      coordinatorAction({
        id: 'r1_write',
        label: '写入 R1 发言',
        phase: 'R1',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: 'dry-run 通过后再写入 R1 报告和工作流状态。',
      }),
    ]
  }

  const disputes = bundle.disputes?.disputes || bundle.workflow?.disputes || []
  const unresolvedCount = Math.max(
    Number(bundle.disputes?.counts?.unresolved || 0),
    disputes.filter((dispute) => dispute.resolved === false).length,
  )
  const disputesAvailable = Boolean(
    bundle.disputes?.artifacts?.json?.available
      || bundle.disputes?.artifacts?.markdown?.available
      || phaseArtifactAvailable(bundle, 'R1.5')
      || disputes.length > 0,
  )
  if (!disputesAvailable) {
    return [
      coordinatorAction({
        id: 'identify_disputes_dry',
        label: '预演分歧识别',
        phase: 'R1.5',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: 'R1 报告已齐，下一步应汇总专家之间的分歧和证据冲突。',
      }),
      coordinatorAction({
        id: 'identify_disputes_write',
        label: '写入分歧清单',
        phase: 'R1.5',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: '确认分歧识别结果后写入 R1.5 产物，供主席裁决和 R2 综合使用。',
      }),
    ]
  }

  const needsRuling = unresolvedCount > 0 && disputes.some((dispute) => dispute.resolved === false && !dispute.chairman_ruling)
  if (needsRuling) {
    return [
      coordinatorAction({
        id: 'ruling_dry',
        label: '预演主席裁决',
        phase: 'R1.5',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: `${unresolvedCount} 个分歧仍待主席裁决，先生成裁决草案。`,
      }),
      coordinatorAction({
        id: 'ruling_write',
        label: '写入裁决草案',
        phase: 'R1.5',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: '确认裁决口径后写入分歧产物，减少 R2 综合阶段摇摆。',
      }),
    ]
  }

  if (!phaseArtifactAvailable(bundle, 'R2')) {
    return [
      coordinatorAction({
        id: 'r2_dry',
        label: '预演 R2 综合',
        phase: 'R2',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: 'R1 与分歧裁决已具备，下一步生成主席综合意见。',
      }),
      coordinatorAction({
        id: 'r2_write',
        label: '写入 R2 综合',
        phase: 'R2',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: 'dry-run 通过后写入 R2 综合报告，作为 R3 验证输入。',
      }),
    ]
  }

  if (!phaseArtifactAvailable(bundle, 'R3')) {
    return [
      coordinatorAction({
        id: 'r3_dry',
        label: '预演 R3 验证',
        phase: 'R3',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: 'R2 综合已生成，建议验证引用、证据覆盖和审计链。',
      }),
      coordinatorAction({
        id: 'r3_write',
        label: '写入 R3 验证',
        phase: 'R3',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: '验证结果确认后写入 R3 产物，进入最终投决草案。',
      }),
    ]
  }

  if (!phaseArtifactAvailable(bundle, 'R4') && !decisionAvailable(bundle)) {
    return [
      coordinatorAction({
        id: 'r4_dry',
        label: '预演 R4 投决',
        phase: 'R4',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: 'R3 已完成，下一步形成最终投决草案和决策合同。',
      }),
      coordinatorAction({
        id: 'r4_write',
        label: '写入 R4 投决',
        phase: 'R4',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: '确认 R4 草案后写入最终投决产物，等待人工确认。',
      }),
    ]
  }

  const humanConfirmation = bundle.decision?.contract?.human_confirmation
  const humanStatus = humanConfirmation?.status || (humanConfirmation?.confirmed ? 'confirmed' : 'pending')
  const humanConfirmed = isHumanDecisionConfirmed(humanStatus, humanConfirmation?.confirmed)
  if (decisionAvailable(bundle) && !humanConfirmed) {
    return [
      coordinatorAction({
        id: 'human_dry',
        label: '预演人工确认',
        phase: 'HUMAN',
        mode: 'preview',
        tone: 'info',
        priority: 10,
        reason: 'R4 投决已形成，建议先预演人工确认写入内容。',
      }),
      coordinatorAction({
        id: 'human_write',
        label: '写入人工确认',
        phase: 'HUMAN',
        mode: 'write',
        tone: 'warning',
        priority: 20,
        reason: '人工确认后投决链路闭环，审计记录可回放。',
      }),
      coordinatorAction({
        id: 'open_decision',
        label: '查看投决产物',
        phase: 'R4',
        mode: 'link',
        tone: 'neutral',
        priority: 30,
        reason: '在写入人工确认前复核最终投决合同和报告。',
        to: decisionPath,
        disabledReason: decisionPath ? undefined : '缺少 dealId，无法打开投决产物。',
      }),
    ]
  }

  return [
    coordinatorAction({
      id: 'open_decision',
      label: '查看已确认投决',
      phase: 'R4',
      mode: 'link',
      tone: 'success',
      priority: 10,
      reason: '投决已形成且人工确认完成，可以进入最终产物复核。',
      to: decisionPath,
      disabledReason: decisionPath ? undefined : '缺少 dealId，无法打开投决产物。',
    }),
    coordinatorAction({
      id: 'refresh',
      label: '刷新会议状态',
      phase: 'SYNC',
      mode: 'refresh',
      tone: 'neutral',
      priority: 20,
      reason: '同步最新工作流、审计和产物状态。',
    }),
  ]
}

function normalizedPreparationRound(bundle: MeetingBundle): MeetingPreparationRound {
  const actionPhase = deriveCoordinatorNextActions(bundle)[0]?.phase
  if (['R0', 'R1', 'R1.5', 'R2', 'R3', 'R4'].includes(String(actionPhase))) {
    return actionPhase as MeetingPreparationRound
  }
  if (actionPhase === 'HUMAN') return 'R4'
  const currentPhase = String(bundle.workflow?.workflow.current_phase || bundle.detail?.summary.current_phase || '').toUpperCase()
  if (['R0', 'R1', 'R1.5', 'R2', 'R3', 'R4'].includes(currentPhase)) {
    return currentPhase as MeetingPreparationRound
  }
  return 'R0'
}

export function deriveMeetingPreparationPlan(bundle: MeetingBundle): MeetingPreparationPlan {
  const roundName = normalizedPreparationRound(bundle)
  const chairman = 'siq_ic_chairman'
  const coordinator = 'siq_ic_master_coordinator'
  const plans: Record<MeetingPreparationRound, MeetingPreparationPlan> = {
    R0: {
      roundName: 'R0',
      profileIds: [coordinator],
      individualProfileIds: [coordinator],
      label: 'R0 准入核验',
      reason: '由 Coordinator 检索项目身份、材料完整性和尽调范围所需背景知识。',
    },
    R1: {
      roundName: 'R1',
      profileIds: [...R1_AGENT_SEQUENCE],
      individualProfileIds: [...R1_AGENT_SEQUENCE],
      label: 'R1 独立研究',
      reason: '为 R1A 专家、R1B 风控和主席准备各自专属背景知识。',
    },
    'R1.5': {
      roundName: 'R1.5',
      profileIds: [chairman],
      individualProfileIds: [chairman],
      label: 'R1.5 主席裁决',
      reason: '仅主席读取结构化分歧、项目 Evidence 和主席专属知识后裁决。',
    },
    R2: {
      roundName: 'R2',
      profileIds: [...IC_EXPERT_AGENT_IDS],
      individualProfileIds: [...IC_EXPERT_AGENT_IDS],
      label: 'R2 专家修订',
      reason: '五位领域专家依据裁决和新增 Evidence 修订观点与评分。',
    },
    R3: {
      roundName: 'R3',
      profileIds: [...IC_EXPERT_AGENT_IDS, chairman],
      individualProfileIds: [...IC_EXPERT_AGENT_IDS, chairman],
      label: 'R3 红蓝对抗',
      reason: '为相关五专家和主席准备红方、蓝方、反驳及裁定上下文。',
    },
    R4: {
      roundName: 'R4',
      profileIds: [chairman],
      individualProfileIds: [chairman, coordinator],
      label: 'R4 决策与汇编',
      reason: '主席准备最终决策知识；Coordinator 可单独准备报告汇编和审计上下文。',
    },
  }
  return plans[roundName]
}

export function deriveMeetingAgenda(bundle: MeetingBundle): MeetingAgendaItem[] {
  const artifacts = phaseArtifactByPhase(bundle.phaseArtifacts)
  const r1Reports = bundle.workflow?.agent_reports || []
  const disputes = bundle.disputes?.disputes || bundle.workflow?.disputes || []
  const humanConfirmation = bundle.decision?.contract?.human_confirmation
  const humanStatus = humanConfirmation?.status || (humanConfirmation?.confirmed ? 'confirmed' : 'pending')
  const humanConfirmed = isHumanDecisionConfirmed(humanStatus, humanConfirmation?.confirmed)

  const items: MeetingAgendaItem[] = [
    {
      phase: 'R0',
      label: phaseLabel('R0'),
      status: bundle.preflight?.status || artifactStatus(artifacts.get('R0')),
      tone: statusTone(bundle.preflight?.status || artifactStatus(artifacts.get('R0'))),
      blocking: bundle.preflight?.status === 'fail',
      detail: bundle.preflight ? `${bundle.preflight.checks.filter((check) => check.status !== 'pass').length} 个发现` : undefined,
    },
    {
      phase: 'R1',
      label: phaseLabel('R1'),
      status: artifactStatus(artifacts.get('R1')),
      tone: statusTone(artifactStatus(artifacts.get('R1'))),
      blocking: artifacts.get('R1')?.blocking,
      detail: `${r1Reports.filter((report) => report.has_report).length}/${R1_AGENT_SEQUENCE.length} 位专家`,
      count: r1Reports.length,
    },
    {
      phase: 'R1.5',
      label: phaseLabel('R1.5'),
      status: bundle.disputes?.status || artifactStatus(artifacts.get('R1.5')),
      tone: statusTone(bundle.disputes?.status || artifactStatus(artifacts.get('R1.5'))),
      blocking: Boolean((bundle.disputes?.counts?.unresolved || 0) > 0),
      detail: `${disputes.length} 个争议`,
      count: disputes.length,
    },
    ...(['R2', 'R3', 'R4'] as const).map((phase) => {
      const artifact = artifacts.get(phase)
      return {
        phase,
        label: phaseLabel(phase),
        status: artifactStatus(artifact),
        tone: statusTone(artifactStatus(artifact)),
        blocking: artifact?.blocking,
        detail: artifact?.mode ? `mode: ${artifact.mode}` : text(artifact?.artifacts?.json?.path, ''),
        count: Number(artifact?.counts?.items || 0),
      }
    }),
    {
      phase: 'HUMAN',
      label: phaseLabel('HUMAN'),
      status: humanStatus,
      tone: statusTone(humanStatus),
      blocking: !humanConfirmed,
      detail: humanConfirmation?.confirmed_at || undefined,
    },
  ]

  return items
}

export function buildMeetingEvents(bundle: MeetingBundle): MeetingEvent[] {
  const events: MeetingEvent[] = []
  const dealId = bundle.detail?.summary.deal_id || bundle.workflow?.workflow.deal_id || bundle.decision?.contract?.deal_id || 'deal'
  const workflow = bundle.workflow?.workflow

  if (workflow) {
    events.push({
      id: `${dealId}-coordinator-${workflow.current_phase || 'phase'}`,
      phase: workflow.current_phase || 'R0',
      type: 'coordinator_instruction',
      speaker: agentLabel('siq_ic_master_coordinator'),
      title: '会议状态同步',
      body: `当前阶段 ${phaseLabel(workflow.current_phase)}，工作流状态 ${text(workflow.status)}。`,
      tone: statusTone(workflow.status),
      createdAt: workflow.updated_at,
    })
  }

  if (bundle.preflight) {
    const findings = bundle.preflight.checks.filter((check) => check.status !== 'pass')
    events.push({
      id: `${dealId}-preflight`,
      phase: 'R0',
      type: bundle.preflight.status === 'fail' ? 'system_blocking' : 'phase_summary',
      speaker: 'R0 Preflight',
      title: `信息校验 ${statusLabel(bundle.preflight.status)}`,
      body: findings.length
        ? findings.slice(0, 4).map((check) => `${check.label}: ${check.message}`).join('\n')
        : '基础信息、证据和工作流门禁未发现阻断项。',
      tone: statusTone(bundle.preflight.status),
      meta: `${findings.length} findings`,
    })
  }

  for (const row of deriveMeetingReceiptRows(bundle).filter((item) => item.present)) {
    events.push({
      id: `${dealId}-receipt-${row.agentId}`,
      phase: 'R1',
      type: 'receipt_generated',
      speaker: row.label,
      title: `Startup Receipt ${row.receiptId || 'present'}`,
      body: `检索命中 evidence ${row.evidenceHits} 条，shared ${row.sharedHits} 条，private ${row.privateHits} 条。${row.gaps.length ? `\n缺口：${row.gaps.join(' / ')}` : ''}`,
      tone: row.allowed === false ? 'error' : 'success',
      meta: [...row.collections, ...row.physicalCollections.map((collection) => `physical:${collection}`)].join(' / ') || undefined,
      createdAt: row.createdAt,
    })
  }

  for (const report of bundle.workflow?.agent_reports || []) {
    events.push({
      id: `${dealId}-r1-${report.agent_id}`,
      phase: 'R1',
      type: 'agent_speech',
      speaker: report.label || agentLabel(report.agent_id),
      title: `${text(report.recommendation, '未给建议')} · ${text(report.score, '-')} 分`,
      body: text(report.summary, report.has_report ? '专家报告已归档。' : '尚未形成正式报告。'),
      tone: report.has_report ? statusTone(report.recommendation || 'pass') : 'warning',
      meta: report.artifact_path || report.startup_receipt_id || undefined,
      createdAt: report.created_at,
    })
  }

  const disputes = bundle.disputes?.disputes || bundle.workflow?.disputes || []
  for (const dispute of disputes.slice(0, 8)) {
    const resolved = dispute.resolved === true
    events.push({
      id: `${dealId}-dispute-${dispute.dispute_id || dispute.topic || events.length}`,
      phase: 'R1.5',
      type: 'dispute_detected',
      speaker: '投委会主席',
      title: text(dispute.topic || dispute.dimension, '识别到争议'),
      body: [
        `严重度 ${text(dispute.severity, 'unknown')}，${text(dispute.position_count, '0')} 个立场。`,
        dispute.chairman_ruling ? `裁决：${text((dispute.chairman_ruling as Record<string, unknown>).decision, '已生成')}` : '尚未生成主席裁决。',
      ].join('\n'),
      tone: resolved ? 'success' : 'warning',
      meta: compactList(dispute.agent_ids),
    })
  }

  for (const phase of bundle.phaseArtifacts?.phases || []) {
    if (phase.artifacts?.json?.available || phase.artifacts?.markdown?.available) {
      events.push({
        id: `${dealId}-artifact-${phase.phase}`,
        phase: phase.phase || 'R0',
        type: 'artifact_written',
        speaker: '归档系统',
        title: `${phaseLabel(phase.phase)} 产物已归档`,
        body: text(phase.artifacts?.markdown?.path || phase.artifacts?.json?.path, '阶段产物已可用。'),
        tone: statusTone(phase.status),
        meta: phase.mode || undefined,
      })
    }
  }

  const decision = bundle.decision?.contract || null
  if (decision) {
    const finalDecision = decision.decision?.value || decision.decision?.qualitative || bundle.detail?.summary.final_decision
    events.push({
      id: `${dealId}-decision`,
      phase: 'R4',
      type: 'decision_draft',
      speaker: '投决草案',
      title: text(finalDecision, 'R4 决策合同'),
      body: `最终分 ${text(decision.scoring?.final_score, '-')}; 人工确认 ${text(decision.human_confirmation?.status || (decision.human_confirmation?.confirmed ? 'confirmed' : 'pending'))}。`,
      tone: statusTone(decision.status),
      meta: decision.artifacts?.markdown?.path || undefined,
      createdAt: decision.generated_at,
    })
  }

  const auditEvents = bundle.audit?.audit.events || []
  auditEvents.slice(-5).reverse().forEach((event, index) => {
    events.push({
      id: `${dealId}-audit-${event.created_at || index}`,
      phase: 'AUDIT',
      type: 'audit_event',
      speaker: 'Audit',
      title: text(event.event_type, 'audit_event'),
      body: text(event.message || event.detail || event.actor || event.status, '审计事件已记录。'),
      tone: 'neutral',
      createdAt: event.created_at,
    })
  })

  if (!events.length) {
    events.push({
      id: `${dealId}-empty`,
      phase: 'R0',
      type: 'coordinator_instruction',
      speaker: agentLabel('siq_ic_master_coordinator'),
      title: '等待会议启动',
      body: '请选择项目并完成材料、证据和 R0 校验后开始投委会流程。',
      tone: 'neutral',
    })
  }

  return events
}
