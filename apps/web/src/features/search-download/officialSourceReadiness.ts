import { missingMarketSourceConfig } from './display'
import { MARKET_CONFIGS, type MarketCode, type MarketSourceStatus } from './model'

export interface OfficialSourceReadinessDecision {
  ok: boolean
  message: string | null
  toast?: {
    type: 'warning'
    title: string
    description: string
  }
}

export function evaluateOfficialSourceReadiness(
  market: MarketCode,
  source?: MarketSourceStatus,
): OfficialSourceReadinessDecision {
  if (market !== 'JP' && market !== 'KR') {
    return { ok: true, message: null }
  }

  if (!source && market === 'JP') {
    return {
      ok: true,
      message: '暂未获取到日股官方源状态；将优先尝试 EDINET 有价证券报告书与发行人法定镜像，Integrated Report 仅作为辅助资料。',
    }
  }

  const missing = missingMarketSourceConfig(market, source)
  if (source?.report_search_ready === false && missing.length > 0) {
    const sourceName = source?.official_source || (market === 'JP' ? 'EDINET' : 'DART')
    const message = market === 'JP'
      ? `${MARKET_CONFIGS[market].label}${sourceName} 完整法定年报需要配置 ${missing.join('、')}；Integrated Report/IR PDF 不再作为主年报兜底。`
      : `${MARKET_CONFIGS[market].label}${sourceName} 增强源需要配置 ${missing.join('、')}；将继续使用当前可用的官方 fallback。`
    return {
      ok: false,
      message,
      toast: {
        type: 'warning',
        title: '官方源配置缺失',
        description: message,
      },
    }
  }

  if (missing.length > 0) {
    return {
      ok: true,
      message: market === 'JP'
        ? `${MARKET_CONFIGS[market].label}缺少 ${missing.join('、')} 时只能使用发行人法定镜像或辅助 IR 资料，完整 EDINET 法定报告全量可能不完整。`
        : `${MARKET_CONFIGS[market].label}部分官方源缺少 ${missing.join('、')}；将优先使用可用的免费官方源查询，法定报告全量可能不完整。`,
    }
  }

  return { ok: true, message: null }
}
