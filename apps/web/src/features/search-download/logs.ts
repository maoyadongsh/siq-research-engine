import { type MarketCode } from './model'

export interface SearchDownloadLogEntry {
  time: string
  msg: string
  type: string
}

export function getSearchDownloadVisibleLogs(logs: SearchDownloadLogEntry[], limit = 200) {
  return logs.slice(-limit)
}

export function hasSearchDownloadProblemLogs(logs: SearchDownloadLogEntry[]) {
  return logs.some((log) => log.type === 'error' || log.type === 'warn')
}

const identifierHints: Record<MarketCode, string> = {
  CN: '请直接输入准确的股票代码或交易所代码，例如 600519 或 SH:600519。',
  HK: '请直接输入准确的股票代码或代号，例如 00700 或 9988.HK。',
  US: '请直接输入准确的股票代码或 CIK，例如 NVDA 或 0001045810。',
  EU: '请直接输入准确的 ticker、ISIN 或国家前缀代码，例如 ASML、NL:ASML 或 NL0010273215。',
  KR: '请直接输入准确的 6 位股票代码或 DART corp code，例如 005930 或 00126380。',
  JP: '请直接输入准确的 4 位证券代码或 EDINET code，例如 7203 或 E02144。',
}

export function identifierHintForMarket(market: MarketCode) {
  return identifierHints[market]
}

export function shouldAppendIdentifierHint(message: string) {
  const normalized = message.toLowerCase()
  return (
    normalized.includes('did not match')
    || normalized.includes('not match')
    || normalized.includes('no matching')
    || normalized.includes('catalog')
    || normalized.includes('resolve')
    || message.includes('未能识别')
    || message.includes('无法识别')
    || message.includes('未匹配')
    || message.includes('未找到')
    || message.includes('解析失败')
  )
}

export function buildQueryFailureLogMessage(message: string, market: MarketCode) {
  const trimmed = String(message || '查询失败').trim()
  if (!trimmed || trimmed.includes('请直接输入准确')) return trimmed || identifierHintForMarket(market)
  if (!shouldAppendIdentifierHint(trimmed)) return trimmed
  return `${trimmed} ${identifierHintForMarket(market)}`
}
