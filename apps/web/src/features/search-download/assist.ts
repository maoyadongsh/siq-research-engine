import {
  MARKET_CONFIGS,
  isMarketCode,
  type AssistIntent,
  type AssistResult,
  type CandidateExplanation,
  type MarketCode,
} from './model'

export interface AssistSearchPlanContext {
  currentMarket: MarketCode
  currentYear: string
  currentMarketFilter: string
  smartPrompt: string
}

export interface AssistSearchPlan {
  targetMarket: MarketCode
  targetYear: string
  nextQuery: string
  targetQuery: string
  targetTicker?: string
  targetCompanyId?: string
  targetCountry?: string
  reportTypes: string[]
  understoodLog: string
}

export function buildAssistSearchPlan(result: AssistResult, context: AssistSearchPlanContext): AssistSearchPlan {
  const intent = result.intent || {}
  const targetMarket = intent.market && isMarketCode(intent.market) ? intent.market : context.currentMarket
  const targetYear = String(intent.report_year || context.currentYear)
  const nextQuery = String(intent.ticker || intent.company_id || intent.company_query || '')
  const targetQuery = String(intent.company_query || intent.ticker || intent.company_id || nextQuery || context.smartPrompt).trim()
  const reportTypes = intent.report_types || []
  const codeText = [intent.ticker, intent.company_id].filter(Boolean).join(' / ')
  const understoodCompany = intent.company_query || targetQuery || '公司待确认'
  const understoodReportTypes = reportTypes.join('+') || '年报'

  return {
    targetMarket,
    targetYear,
    nextQuery,
    targetQuery,
    targetTicker: intent.ticker,
    targetCompanyId: intent.company_id,
    targetCountry: targetMarket === 'EU' ? context.currentMarketFilter : undefined,
    reportTypes,
    understoodLog: `已理解: ${MARKET_CONFIGS[targetMarket].label} · ${understoodCompany}${codeText ? ` · ${codeText}` : ''} / ${understoodReportTypes}`,
  }
}

export interface AssistIntentChipContext {
  currentMarketLabel: string
  currentQuery: string
  currentYear: string
  assistantMode?: string
  typeLabels: Record<string, string>
}

export function buildAssistIntentChips(
  intent: AssistIntent,
  context: AssistIntentChipContext,
): string[] {
  const marketLabel = intent.market && isMarketCode(intent.market)
    ? MARKET_CONFIGS[intent.market].label
    : intent.market || context.currentMarketLabel
  const reportTypes = (intent.report_types || []).map((item) => context.typeLabels[item] || item).join(' / ') || '年报'
  const mode = context.assistantMode?.startsWith('llm:') ? '模型增强' : '规则辅助'

  return [
    `市场：${marketLabel}`,
    `公司：${intent.company_query || intent.ticker || context.currentQuery || '待确认'}`,
    `年份：${intent.report_year || context.currentYear}`,
    `报告：${reportTypes}`,
    `模式：${mode}`,
  ]
}

export function recommendedCandidateUrls(explanations: CandidateExplanation[]): string[] {
  return explanations.filter((item) => item.recommended).map((item) => item.document_url)
}
