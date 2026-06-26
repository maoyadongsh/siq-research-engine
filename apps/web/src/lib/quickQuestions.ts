export type AgentQuickQuestion = {
  label: string
  prompt?: string
  featured?: boolean
}

export type AgentQuickQuestionInput = string | AgentQuickQuestion

export const INTRO_LABEL = '智能体简介'

export function introQuickQuestion(): AgentQuickQuestion {
  return {
    label: INTRO_LABEL,
    featured: true,
  }
}

export function quickQuestionLabel(question: AgentQuickQuestionInput) {
  return typeof question === 'string' ? question : question.label
}

export function quickQuestionPrompt(question: AgentQuickQuestionInput) {
  return typeof question === 'string' ? question : question.prompt || question.label
}

export function displayLabelForPrompt(text: string) {
  const normalized = text.trim()
  return normalized === INTRO_LABEL ? INTRO_LABEL : text
}

export const assistantQuickQuestions: AgentQuickQuestionInput[] = [
  introQuickQuestion(),
  '分析营收增长质量',
  '对比利润与现金流',
  '评估资产负债率风险',
  '梳理经营现金流变化',
  '说明毛利率变化原因',
  '评估研发投入占比',
]

export const analysisQuickQuestions: AgentQuickQuestionInput[] = [
  introQuickQuestion(),
  '生成深度分析',
  '评估偿债能力',
  '对比同业表现',
  'DCF估值分析',
  '三表建模分析',
  '竞争力深度分析',
  '行业对标分析',
  '现金流质量评估',
  '资产效率分析',
]

export const factcheckerQuickQuestions: AgentQuickQuestionInput[] = [
  introQuickQuestion(),
  '核查营收数据',
  '列出存疑项',
  '验证三大表',
]

export const trackingQuickQuestions: AgentQuickQuestionInput[] = [
  introQuickQuestion(),
  '提取跟踪事项',
  '生成舆情日报',
  '列出预警信号',
]

export const legalQuickQuestions: AgentQuickQuestionInput[] = [
  introQuickQuestion(),
  '生成法律意见书',
  '检索法规依据',
  '列出合规风险',
]
