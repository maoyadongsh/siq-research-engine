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
  '生成年报法律意见书',
  {
    label: '起草意见要点',
    prompt: '请基于当前公司、报告和我提供的事实，按公司法务/律师工作底稿口径在对话中起草法律意见要点，不要生成 HTML 或保存文件。请先说明事实前提和检索边界，再给出审慎结论、法规依据、风险提示、建议动作、待核实事项和引用来源。',
  },
  {
    label: '检索法规依据',
    prompt: '请围绕当前事项检索适用法规、交易所规则或监管文件。请不要只列法规名称，而要说明每条依据与本事项的关联、适用条件、可能例外和仍需核实的事实。',
  },
  {
    label: '列出合规风险',
    prompt: '请以公司法务向管理层汇报的口径，梳理当前事项的合规风险。请按风险等级说明触发条件、可能后果、缓释措施、责任部门和后续跟踪建议。',
  },
]
