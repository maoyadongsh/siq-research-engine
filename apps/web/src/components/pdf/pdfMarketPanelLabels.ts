export function normalizePdfMarket(market?: string | null): string {
  return String(market || '').trim().toUpperCase()
}

export function pdfQualityPanelTitle(market?: string | null): string {
  return normalizePdfMarket(market) === 'JP' ? '日本解析质量报告' : '解析质量报告'
}

export function pdfFinancialPanelTitle(market?: string | null): string {
  return normalizePdfMarket(market) === 'JP' ? '日本财务识别与一致性检查' : '财务勾稽校验'
}
