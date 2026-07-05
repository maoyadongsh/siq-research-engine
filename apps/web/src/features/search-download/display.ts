import {
  MARKET_CONFIGS,
  marketSourceConfigLabels,
  type MarketCode,
  type MarketSourceStatus,
} from './model'

export function reportTableTitlesForMarket(market: MarketCode) {
  return {
    annualTitle: market === 'US'
      ? '年度报告列表'
      : market === 'JP'
        ? '有价证券报告书列表'
        : market === 'EU'
          ? '年度财务报告列表'
          : '年报列表',
    financialTitle: market === 'US'
      ? '定期披露列表（10-Q / 20-F / 6-K）'
      : market === 'EU'
        ? '其他定期披露列表'
        : market === 'HK'
          ? '财报列表（中期 / 季度）'
          : market === 'JP'
            ? '财报列表（半期 / 季度）'
            : '财报列表（半年报 / 季报）',
  }
}

export function smartSearchPlaceholderForMarket(market: MarketCode) {
  if (market === 'HK') return '例如：腾讯控股 2025 年年报'
  if (market === 'US') return '例如：英伟达 2025 年 10-K'
  if (market === 'EU') return '例如：ASML 2025 年年度报告'
  if (market === 'KR') return '例如：三星电子 2025 年年报和三季度报告'
  if (market === 'JP') return '例如：铠侠 2025 年有价证券报告书'
  return '例如：比亚迪 2025 年年报'
}

export function missingMarketSourceConfig(market: MarketCode, source?: MarketSourceStatus) {
  if (source?.required_config?.length) return source.required_config
  const fallbackKey = marketSourceConfigLabels[market]
  if (source?.report_search_ready === false && fallbackKey) return [fallbackKey]
  return []
}

export function marketSourceDisplay({
  market,
  source,
  loading,
}: {
  market: MarketCode
  source?: MarketSourceStatus
  loading: boolean
}) {
  const missingConfig = missingMarketSourceConfig(market, source)
  const searchBlocked = Boolean(source?.report_search_ready === false && missingConfig.length)
  const showReady = Boolean(source && source.report_search_ready !== false && missingConfig.length === 0)
  const label = MARKET_CONFIGS[market].label
  const message = searchBlocked
    ? market === 'JP'
      ? `${label}完整法定年报需配置 ${missingConfig.join('、')}；Integrated Report/IR PDF 不作为主年报兜底。`
      : `${label}增强官方源需配置 ${missingConfig.join('、')}；当前仍可使用官方 fallback。`
    : missingConfig.length
      ? market === 'JP'
        ? `${label}缺少 ${missingConfig.join('、')} 时只能使用发行人法定镜像或辅助 IR 资料，EDINET 法定报告全量可能不完整。`
        : `${label}部分官方源缺少 ${missingConfig.join('、')}；将继续使用可用的免费官方源查询，法定报告全量可能不完整。`
      : loading
        ? '正在检查官方披露源配置...'
        : showReady
          ? `${label}官方披露源已就绪，可查询下载列表。`
          : `正在等待${label}官方披露源状态。`

  return {
    missingConfig,
    searchBlocked,
    showReady,
    className: missingConfig.length ? 'smart-search-source is-warning' : 'smart-search-source is-ready',
    message,
  }
}

export function logMessageClassName(type: string) {
  if (type === 'success') return 'text-success'
  if (type === 'error') return 'text-error'
  if (type === 'warn') return 'text-warning'
  return 'text-text'
}
