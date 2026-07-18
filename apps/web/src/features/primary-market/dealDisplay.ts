import type { DealStatusComponent } from '@/lib/dealTypes'

const STATUS_LABELS: Record<string, string> = {
  archived: '已归档',
  blocked: '已阻断',
  closed: '已关闭',
  completed: '已完成',
  draft: '草稿',
  fail: '未通过',
  missing: '缺失',
  pass: '通过',
  r4_completed: '投决已完成',
  ready: '就绪',
  review_required: '待复核',
  risk: '存在风险',
  skip: '已跳过',
  warn: '待关注',
}

const NEXT_ACTION_LABELS: Record<string, string> = {
  continue_execution: '继续推进项目',
  resolve_blocking_contracts: '补齐阻断项并重新校验',
  review_decision: '复核项目决策',
}

const COMPONENT_LABELS: Record<string, string> = {
  audit_chain: '审计链',
  deal_preflight: '项目前置校验',
  r1_agent_readiness: 'R1 智能体就绪检查',
  r1_expert_reports: 'R1 专家报告',
  r1_5_revision_disputes: 'R1.5 修订争议',
  r1_5_disputes: 'R1.5 修订争议',
  r2_revision_reports: 'R2 修订报告',
  r3_review: 'R3 复核',
  r4_decision_contract: 'R4 投决结论',
}

const WARNING_LABELS: Record<string, string> = {
  audit_sources_mismatch: '审计来源不一致',
  'preflight_warn:evidence.gate': '证据门禁未通过',
  'preflight_warn:retrieval.receipt_contract': '检索回执不完整',
  'required_event_missing:deal_created': '缺少项目创建审计事件',
}

function normalizedKey(value: string) {
  return value.trim().toLowerCase().replace(/[\s.-]+/g, '_')
}

export function dealStatusLabel(value?: string | null) {
  const text = String(value || '').trim()
  if (!text) return '未设置'
  return STATUS_LABELS[normalizedKey(text)] || text
}

export function dealNextActionLabel(value?: string | null) {
  const text = String(value || '').trim()
  if (!text) return '暂无下一步操作'
  return NEXT_ACTION_LABELS[text] || text.replaceAll('_', ' ')
}

export function dealComponentLabel(component: DealStatusComponent) {
  return COMPONENT_LABELS[normalizedKey(component.id)]
    || COMPONENT_LABELS[normalizedKey(component.label || '')]
    || component.label
    || component.id
}

export function dealComponentMessage(component: DealStatusComponent) {
  const message = String(component.message || '').trim()
  if (!message) return '暂无补充说明'

  if (/^preflight status:/i.test(message)) {
    return component.blocking ? '前置校验发现阻断项，补齐材料后可继续推进。' : '前置校验已完成。'
  }

  const readiness = message.match(/^(\d+) ready,\s*(\d+) blocked\.?$/i)
  if (readiness) return `${readiness[1]} 项就绪，${readiness[2]} 项阻断。`

  const checks = message.match(/^(\d+) pass,\s*(\d+) warn,\s*(\d+) missing\.?$/i)
  if (checks) return `${checks[1]} 项通过，${checks[2]} 项待关注，${checks[3]} 项缺失。`

  const disputes = message.match(/^(\d+) resolved,\s*(\d+) unresolved,\s*(\d+) total\.?$/i)
  if (disputes) return `共 ${disputes[3]} 项争议，已解决 ${disputes[1]} 项，待解决 ${disputes[2]} 项。`

  const reports = message.match(/^(\d+) reports,\s*(\d+) warn,\s*(\d+) missing\.?$/i)
  if (reports) return `${reports[1]} 份报告，${reports[2]} 项待关注，${reports[3]} 项缺失。`

  const review = message.match(/^R3 mode:\s*([^,]+),\s*reports:\s*(\d+)\.?$/i)
  if (review) return `R3 ${dealStatusLabel(review[1])}，报告 ${review[2]} 份。`

  const decision = message.match(/^R4 decision status:\s*([^.]+)\.?$/i)
  if (decision) return `R4 投决状态：${dealStatusLabel(decision[1])}。`

  const audit = message.match(/^Audit status:\s*([^.]+)\.?$/i)
  if (audit) return `审计状态：${dealStatusLabel(audit[1])}。`

  return message
}

export function dealWarningLabel(value: string) {
  const text = value.trim()
  return WARNING_LABELS[text] || '存在待处理事项'
}

export function formatDealTime(value?: string | null) {
  if (!value) return '未生成'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
