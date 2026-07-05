export function normalizePdfMarket(market?: string | null): string {
  return String(market || '').trim().toUpperCase()
}

export function pdfQualityPanelTitle(market?: string | null): string {
  const normalized = normalizePdfMarket(market)
  if (normalized === 'HK') return '香港市场解析质量报告'
  if (normalized === 'JP') return '日本解析质量报告'
  if (normalized === 'KR') return '韩国 DART 解析质量报告'
  if (normalized === 'EU') return '欧洲 IFRS/ESEF 解析质量报告'
  if (normalized === 'US') return '美国 SEC/PDF 解析质量报告'
  return '解析质量报告'
}

export function pdfFinancialPanelTitle(market?: string | null): string {
  const normalized = normalizePdfMarket(market)
  if (normalized === 'HK') return '香港财务识别与一致性检查'
  if (normalized === 'JP') return '日本财务识别与一致性检查'
  if (normalized === 'KR') return '韩国财务识别与一致性检查'
  if (normalized === 'EU') return '欧洲财务识别与一致性检查'
  if (normalized === 'US') return '美国 SEC/PDF 财务识别与一致性检查'
  return '财务勾稽校验'
}

export function pdfCoreTablesLabel(market?: string | null): string {
  const normalized = normalizePdfMarket(market)
  if (normalized === 'EU') return 'IFRS/ESEF 核心报表'
  if (normalized === 'US') return 'SEC 核心报表'
  if (normalized === 'KR') return 'DART 核心报表'
  if (normalized === 'JP') return '日本核心报表'
  if (normalized === 'HK') return 'HKFRS/IFRS 核心报表'
  return '财报核心表'
}

export function pdfKeyCandidatesLabel(market?: string | null): string {
  return normalizePdfMarket(market) === 'CN' || !normalizePdfMarket(market) ? '关键表候选' : '核心报表候选'
}

export function pdfIndicatorCandidatesLabel(market?: string | null): string {
  const normalized = normalizePdfMarket(market)
  if (normalized === 'CN' || !normalized) return '指标/经营分析候选'
  if (normalized === 'US') return '指标/MD&A 候选'
  return '指标/经营候选'
}

export function pdfNoIndicatorCandidatesLabel(market?: string | null): string {
  const normalized = normalizePdfMarket(market)
  if (normalized === 'CN' || !normalized) return '未定位到指标/经营分析候选表'
  if (normalized === 'US') return '未定位到指标/MD&A 候选'
  return '未定位到指标/经营候选表'
}
