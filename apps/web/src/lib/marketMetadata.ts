export type DisclosureMarketCode = 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP'

export interface DisclosureMarketMetadata {
  code: DisclosureMarketCode
  label: string
  shortLabel: string
  professionalName: string
  exchanges: string
  parsingDescription: string
  searchDescription: string
  parseTo: string
}

export const DISCLOSURE_MARKET_ORDER: DisclosureMarketCode[] = ['CN', 'HK', 'US', 'EU', 'KR', 'JP']

export const DISCLOSURE_MARKETS: Record<DisclosureMarketCode, DisclosureMarketMetadata> = {
  CN: {
    code: 'CN',
    label: '中国内地市场',
    shortLabel: 'CN',
    professionalName: '中国内地市场',
    exchanges: 'SSE / SZSE / BSE',
    parsingDescription: '标准解析',
    searchDescription: '公告检索',
    parseTo: '/parse',
  },
  HK: {
    code: 'HK',
    label: '香港市场',
    shortLabel: 'HK',
    professionalName: '香港市场',
    exchanges: 'HKEX / SEHK',
    parsingDescription: '通用入库',
    searchDescription: '披露下载',
    parseTo: '/parse-hk',
  },
  US: {
    code: 'US',
    label: '美国市场',
    shortLabel: 'US',
    professionalName: '美国市场',
    exchanges: 'NYSE / Nasdaq / Cboe / OTC',
    parsingDescription: 'SEC 披露',
    searchDescription: 'EDGAR 检索',
    parseTo: '/parse-us',
  },
  EU: {
    code: 'EU',
    label: '欧洲市场',
    shortLabel: 'EU',
    professionalName: '欧洲市场',
    exchanges: 'LSE / Euronext / Xetra / SIX',
    parsingDescription: 'IFRS/ESEF',
    searchDescription: '官方源检索',
    parseTo: '/parse-eu',
  },
  KR: {
    code: 'KR',
    label: '韩国市场',
    shortLabel: 'KR',
    professionalName: '韩国市场',
    exchanges: 'KRX / KOSPI / KOSDAQ / KONEX',
    parsingDescription: 'DART 混合解析',
    searchDescription: 'DART 检索',
    parseTo: '/parse-kr',
  },
  JP: {
    code: 'JP',
    label: '日本市场',
    shortLabel: 'JP',
    professionalName: '日本市场',
    exchanges: 'TSE / OSE / NSE / SSE / FSE',
    parsingDescription: 'EDINET 混合解析',
    searchDescription: 'EDINET 检索',
    parseTo: '/parse-jp',
  },
}

export function isDisclosureMarketCode(value: string | null | undefined): value is DisclosureMarketCode {
  return DISCLOSURE_MARKET_ORDER.includes(String(value || '').toUpperCase() as DisclosureMarketCode)
}
