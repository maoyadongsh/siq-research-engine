import type { LogEntry } from '../../lib/pdfTypes'

export type MarketParsingCode = 'CN' | 'HK' | 'US' | 'JP' | 'KR' | 'EU'

export const MARKET_PARSING_SECTION_LABELS = {
  'pdf-upload': '上传',
  'pdf-status': '状态',
  'pdf-result': '结果',
  'pdf-source': '溯源',
  'pdf-tasks': '任务',
} as const

export type MarketParsingSectionId = keyof typeof MARKET_PARSING_SECTION_LABELS

export const MARKET_PARSING_SECTION_IDS = Object.keys(MARKET_PARSING_SECTION_LABELS) as MarketParsingSectionId[]

export interface MarketParsingPageViewModelInput {
  market?: MarketParsingCode
  parseBadgeClass: string
  resultDeferred: boolean
  markdown: string
  parseActive: boolean
  taskCount: number
  logs: LogEntry[]
  logsExpanded: boolean
}

export interface MarketParsingPageViewModel {
  sectionIds: MarketParsingSectionId[]
  sectionLabelEntries: [MarketParsingSectionId, string][]
  isCompleted: boolean
  hasLogIssues: boolean
  logDescription: string
  logToggleText: string
  shouldShowLogs: boolean
  shouldShowResultGate: boolean
  shouldShowWorkflow: boolean
  shouldShowEmptyState: boolean
  canBuildDownloadedPackage: boolean
}

export interface MarketParsingStateScopeInput {
  market?: MarketParsingCode | string | null
  taskId?: string | null
  packagePath?: string | null
  documentFullPath?: string | null
}

function normalizedScopePart(value: unknown): string {
  return String(value || '').trim()
}

export function buildMarketParsingStateScopeKey({
  market,
  taskId,
  packagePath,
  documentFullPath,
}: MarketParsingStateScopeInput): string {
  const identity = normalizedScopePart(documentFullPath)
    || normalizedScopePart(packagePath)
    || normalizedScopePart(taskId)
  if (!identity) return ''
  const marketKey = normalizedScopePart(market).toUpperCase() || 'ALL'
  return `${marketKey}::${identity}`
}

export function hasMarketParsingLogIssues(logs: LogEntry[]) {
  return logs.some((entry) => entry.level === 'error' || entry.level === 'warn')
}

export function buildMarketParsingPageViewModel({
  market,
  parseBadgeClass,
  resultDeferred,
  markdown,
  parseActive,
  taskCount,
  logs,
  logsExpanded,
}: MarketParsingPageViewModelInput): MarketParsingPageViewModel {
  const isCompleted = parseBadgeClass === 'completed'
  const hasLogIssues = hasMarketParsingLogIssues(logs)
  const shouldShowLogs = logsExpanded || hasLogIssues

  return {
    sectionIds: MARKET_PARSING_SECTION_IDS,
    sectionLabelEntries: Object.entries(MARKET_PARSING_SECTION_LABELS) as [MarketParsingSectionId, string][],
    isCompleted,
    hasLogIssues,
    logDescription: hasLogIssues ? '解析过程中有需要关注的提示。' : '默认收起，保留解析过程可审计记录。',
    logToggleText: shouldShowLogs ? '收起日志' : `展开 ${logs.length} 条`,
    shouldShowLogs,
    shouldShowResultGate: isCompleted && resultDeferred && !markdown,
    shouldShowWorkflow: isCompleted,
    shouldShowEmptyState: !markdown && !parseActive && taskCount === 0,
    canBuildDownloadedPackage: market === 'EU',
  }
}
