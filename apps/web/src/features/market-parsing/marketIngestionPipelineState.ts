import { WIKI_INPUT_ARTIFACTS, type ArtifactsMap, type WorkflowStatus } from '../../lib/pdfTypes'
import type { MarketDocumentFullPostgresStatus } from './api'

export type MarketIngestionPipelineStepKey = 'artifacts' | 'wiki' | 'semantic' | 'postgres'

export interface MarketIngestionPipelineStep {
  key: MarketIngestionPipelineStepKey
  label: string
  status?: string
  description: string
}

export interface MarketIngestionPostgresSummary {
  status: 'ready' | 'warning' | 'unknown' | 'pending'
  rawStatus: string
  ready: boolean
  stale: boolean
  partial: boolean
  parseRuns: number
  facts: number
  tables: number
  chunks: number
  evidence: number
  schema: string
  parseRunId: string
  missingCounts: string[]
  description: string
}

export interface MarketIngestionPipelineActionState<Key extends string = string> {
  key: Key
  label: string
  loadingLabel: string
  primary: boolean
  disabled: boolean
  busy: boolean
  disabledReason?: string
}

export interface MarketIngestionPipelineState<ActionKey extends string = string> {
  steps: MarketIngestionPipelineStep[]
  cards: MarketIngestionPipelineStep[]
  activeStepIndex: number
  actions: Array<MarketIngestionPipelineActionState<ActionKey>>
  runAll: MarketIngestionPipelineActionState<'runAll'>
  artifactsReady: boolean
  artifactReadyCount: number
  artifactTotal: number
  artifactMissing: string[]
  postgresSummary: MarketIngestionPostgresSummary
}

export type PdfGenericIngestionActionKey = 'wiki' | 'semantic' | 'postgres'
export type UsSecIngestionActionKey = 'wiki' | 'semantic' | 'postgres'

const MISSING_DOCUMENT_FULL_REASON = '缺少 SEC parser result document_full.json 路径，请先刷新结果包'
const GENERIC_MISSING_ARTIFACTS_REASON = '缺少完整解析产物，请先完成 PDF 解析并生成核心 artifact'
const GENERIC_MISSING_WIKI_REASON = '请先完成 LLM-Wiki 入库'

function normalized(value: unknown): string {
  return String(value || '').trim()
}

function numberValue(value: unknown): number {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n : 0
}

function workflowBucketReady(status: WorkflowStatus | null | undefined, key: 'semantic' | 'database'): boolean {
  const bucket = status?.[key] as { status?: string } | undefined
  return ['ready', 'missing_optional', 'stale_optional', 'postgres_ready'].includes(normalized(bucket?.status))
}

function artifactSummary(artifacts: ArtifactsMap | null | undefined): {
  readyCount: number
  total: number
  missing: string[]
  ready: boolean
} {
  const readyNames = WIKI_INPUT_ARTIFACTS.filter((name) => artifacts?.[name]?.exists)
  return {
    readyCount: readyNames.length,
    total: WIKI_INPUT_ARTIFACTS.length,
    missing: WIKI_INPUT_ARTIFACTS.filter((name) => !artifacts?.[name]?.exists),
    ready: readyNames.length === WIKI_INPUT_ARTIFACTS.length,
  }
}

function statusReady(status: unknown): boolean {
  return normalized(status).toLowerCase() === 'ready'
}

function activeStepIndex(steps: MarketIngestionPipelineStep[], busyIndex?: number): number {
  if (busyIndex !== undefined) return busyIndex
  const firstPending = steps.findIndex((step) => !statusReady(step.status))
  return firstPending >= 0 ? firstPending : Math.max(steps.length - 1, 0)
}

function genericBusyReason(workflowBusy: string): string | undefined {
  if (!workflowBusy) return undefined
  if (workflowBusy === 'wiki' || workflowBusy === 'wiki-import-generic') return 'LLM-Wiki 入库正在执行，请等待完成'
  if (workflowBusy === 'semantic' || workflowBusy === 'semantic-generic') return 'Wiki语义增强入库正在执行，请等待完成'
  if (workflowBusy === 'postgres' || workflowBusy === 'db-import') return 'PostgreSQL 入库正在执行，请等待完成'
  if (workflowBusy === 'runAll' || workflowBusy === 'remaining') return '一键入库正在执行，请等待完成'
  return '流水线任务正在执行，请等待完成'
}

function recordValue(record: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key]
  }
  return undefined
}

function postgresSummaryLine(summary: Pick<MarketIngestionPostgresSummary, 'parseRuns' | 'facts' | 'tables' | 'chunks' | 'evidence'>): string {
  return `parse_runs ${summary.parseRuns} / facts ${summary.facts} / tables ${summary.tables} / chunks ${summary.chunks} / evidence ${summary.evidence}`
}

function postgresContextLine(summary: Pick<MarketIngestionPostgresSummary, 'schema' | 'parseRunId'>): string {
  return [
    summary.schema ? `schema ${summary.schema}` : '',
    summary.parseRunId ? `parse_run_id ${summary.parseRunId}` : '',
  ].filter(Boolean).join(' / ')
}

export function deriveMarketDocumentFullPostgresSummary(status?: object | null): MarketIngestionPostgresSummary {
  const record = (status || {}) as Record<string, unknown>
  const rawStatus = normalized(record.status).toLowerCase()
  const parseRuns = numberValue(recordValue(record, 'parse_runs', 'parseRuns'))
  const facts = numberValue(recordValue(record, 'facts', 'statementItems'))
  const tables = numberValue(record.tables)
  const chunks = numberValue(record.chunks)
  const evidence = numberValue(record.evidence)
  const schema = normalized(record.schema)
  const parseRunId = normalized(recordValue(record, 'parse_run_id', 'parseRunId'))
  const missingCounts = [
    ['parse_runs', parseRuns],
    ['facts', facts],
    ['tables', tables],
    ['chunks', chunks],
    ['evidence', evidence],
  ].filter(([, value]) => numberValue(value) <= 0).map(([name]) => String(name))
  const backendMissingCounts = Array.isArray(record.missing_counts)
    ? record.missing_counts.map((value) => normalized(value)).filter(Boolean)
    : Array.isArray(record.missingCounts)
      ? record.missingCounts.map((value) => normalized(value)).filter(Boolean)
      : []
  const displayMissingCounts = backendMissingCounts.length ? backendMissingCounts : missingCounts
  const stale = rawStatus === 'stale' || normalized(recordValue(record, 'artifact_status', 'artifactStatus')).toLowerCase() === 'stale'
  const countsReady = missingCounts.length === 0
  const ready = countsReady && !stale
  const partial = !countsReady && (parseRuns > 0 || facts > 0 || tables > 0 || chunks > 0 || evidence > 0)
  const displayStatus: MarketIngestionPostgresSummary['status'] = stale
    ? 'warning'
    : ready
    ? 'ready'
    : rawStatus === 'unknown'
      ? 'unknown'
      : (partial || rawStatus === 'warning' || rawStatus === 'partial')
        ? 'warning'
        : 'pending'
  const context = postgresContextLine({ schema, parseRunId })
  const counts = postgresSummaryLine({ parseRuns, facts, tables, chunks, evidence })
  const message = normalized(record.message)
  const description = stale
    ? [message || '解析产物已更新，请重新执行 PostgreSQL 入库', context, counts].filter(Boolean).join('；')
    : ready
    ? [context, counts].filter(Boolean).join('；')
    : displayStatus === 'unknown'
      ? (message || 'document_full status 查询失败，PostgreSQL 状态不可确认')
      : partial
        ? [`未完整：缺少 ${displayMissingCounts.join(', ')}`, context, counts].filter(Boolean).join('；')
        : (message || '等待 PostgreSQL 入库')

  return {
    status: displayStatus,
    rawStatus,
    ready,
    stale,
    partial,
    parseRuns,
    facts,
    tables,
    chunks,
    evidence,
    schema,
    parseRunId,
    missingCounts: displayMissingCounts,
    description,
  }
}

function pdfGenericWikiDescription(asset: WorkflowStatus['wiki']): string {
  if (asset?.status === 'ready') return 'LLM-Wiki 已由解析产物生成'
  if (asset?.status === 'stale') return '解析产物已更新，建议重新入库'
  if (asset?.message && !/wiki/i.test(asset.message)) return asset.message
  if (asset?.status === 'failed' || asset?.status === 'error') return '入库失败，请查看流水线任务详情'
  return '等待 LLM-Wiki 入库'
}

export function derivePdfGenericMarketIngestionPipelineState({
  workflowStatus,
  artifacts,
  workflowBusy = '',
}: {
  workflowStatus?: WorkflowStatus | null
  artifacts?: ArtifactsMap | null
  workflowBusy?: string
}): MarketIngestionPipelineState<PdfGenericIngestionActionKey> {
  const localSummary = artifactSummary(artifacts)
  const backendSummary = workflowStatus?.artifactBundle
  const artifactReadyCount = backendSummary?.readyCount ?? localSummary.readyCount
  const artifactTotal = backendSummary?.total ?? localSummary.total
  const artifactMissing = backendSummary?.missing ?? localSummary.missing
  const artifactsReady = Boolean(backendSummary?.ready) || localSummary.ready
  const artifactStatus = workflowStatus?.artifactBundle?.status || (artifactsReady ? 'ready' : 'pending')

  const llmSemanticCounts = workflowStatus?.semantic?.llm?.counts || {}
  const llmSemanticDesc = workflowStatus?.semantic?.llm?.status === 'ready'
    ? `模型增强 ${llmSemanticCounts.claims || 0} 条判断 / ${llmSemanticCounts.risks || 0} 条风险`
    : ''
  const semanticDescription = workflowBucketReady(workflowStatus, 'semantic')
    ? `规则事实 ${workflowStatus?.semantic?.counts?.facts || 0} / 证据 ${workflowStatus?.semantic?.counts?.evidence || 0}；${llmSemanticDesc}`
    : (workflowStatus?.semantic?.message || llmSemanticDesc || '等待 Wiki语义增强入库')
  const postgresSummary = deriveMarketDocumentFullPostgresSummary(workflowStatus?.database as Record<string, unknown> | null | undefined)
  const wikiReady = ['ready', 'stale'].includes(normalized(workflowStatus?.wiki?.status))

  const steps: MarketIngestionPipelineStep[] = [
    {
      key: 'artifacts',
      label: '解析产物',
      status: artifactStatus,
      description: backendSummary?.message || (workflowStatus?.documentFull?.status === 'ready' ? `${artifactReadyCount}/${artifactTotal} 个核心文件已生成` : '等待 document_full.json'),
    },
    {
      key: 'wiki',
      label: 'LLM-Wiki',
      status: workflowStatus?.wiki?.status || 'pending',
      description: pdfGenericWikiDescription(workflowStatus?.wiki),
    },
    {
      key: 'semantic',
      label: 'Wiki语义增强',
      status: workflowStatus?.semantic?.status || 'pending',
      description: semanticDescription,
    },
    {
      key: 'postgres',
      label: 'PostgreSQL',
      status: postgresSummary.status,
      description: postgresSummary.description,
    },
  ]

  const busyMap: Record<string, number> = {
    wiki: 1,
    'wiki-import-generic': 1,
    semantic: 2,
    'semantic-generic': 2,
    postgres: 3,
    'db-import': 3,
    runAll: 3,
    remaining: 3,
  }
  const busyStepIndex = busyMap[workflowBusy]
  const hasBusyAction = Boolean(workflowBusy)
  const busyReason = genericBusyReason(workflowBusy)
  const actions: Array<MarketIngestionPipelineActionState<PdfGenericIngestionActionKey>> = [
    {
      key: 'wiki',
      label: 'LLM-Wiki入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !artifactsReady,
      busy: workflowBusy === 'wiki' || workflowBusy === 'wiki-import-generic',
      disabledReason: busyReason || (artifactsReady ? undefined : GENERIC_MISSING_ARTIFACTS_REASON),
    },
    {
      key: 'semantic',
      label: 'Wiki语义增强入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !artifactsReady || !wikiReady,
      busy: workflowBusy === 'semantic' || workflowBusy === 'semantic-generic',
      disabledReason: busyReason || (!artifactsReady ? GENERIC_MISSING_ARTIFACTS_REASON : (wikiReady ? undefined : GENERIC_MISSING_WIKI_REASON)),
    },
    {
      key: 'postgres',
      label: 'PostgreSQL入库',
      loadingLabel: '入库中...',
      primary: false,
      disabled: hasBusyAction || !artifactsReady,
      busy: workflowBusy === 'postgres' || workflowBusy === 'db-import',
      disabledReason: busyReason || (artifactsReady ? undefined : GENERIC_MISSING_ARTIFACTS_REASON),
    },
  ]

  return {
    steps,
    cards: steps,
    activeStepIndex: activeStepIndex(steps, busyStepIndex),
    actions,
    runAll: {
      key: 'runAll',
      label: '一键入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !artifactsReady,
      busy: workflowBusy === 'runAll' || workflowBusy === 'remaining',
      disabledReason: busyReason || (artifactsReady ? undefined : GENERIC_MISSING_ARTIFACTS_REASON),
    },
    artifactsReady,
    artifactReadyCount,
    artifactTotal,
    artifactMissing,
    postgresSummary,
  }
}

export function deriveUsSecMarketIngestionPipelineState({
  artifactsReady,
  artifactReadyCount,
  artifactTotal,
  wikiReady,
  semanticEvidence = 0,
  semanticReady,
  semanticDescription = '',
  postgresStatus,
  documentFullPath = '',
  busyAction = '',
  taskId = '',
}: {
  artifactsReady: boolean
  artifactReadyCount: number
  artifactTotal: number
  wikiReady: boolean
  semanticEvidence?: number
  semanticReady?: boolean
  semanticDescription?: string
  postgresStatus?: MarketDocumentFullPostgresStatus | null
  documentFullPath?: string
  busyAction?: string
  taskId?: string
}): MarketIngestionPipelineState<UsSecIngestionActionKey> {
  const semanticEvidenceCount = numberValue(semanticEvidence)
  const semanticStepReady = semanticReady ?? semanticEvidenceCount > 0
  const readyForPostgres = Boolean(normalized(documentFullPath))
  const readyForRunAll = artifactsReady && readyForPostgres
  const hasBusyAction = Boolean(busyAction)
  const postgresSummary = deriveMarketDocumentFullPostgresSummary(postgresStatus)
  const postgresActionKey = taskId ? `postgres:${taskId}` : 'postgres'
  const semanticActionKey = taskId ? `semantic:${taskId}` : 'semantic'
  const wikiActionKey = taskId ? `wiki:${taskId}` : 'wiki'
  const remainingActionKey = taskId ? `remaining:${taskId}` : 'remaining'
  const busyStepIndex = busyAction === wikiActionKey
    ? 1
    : busyAction === semanticActionKey
      ? 2
      : busyAction === postgresActionKey
        ? 3
        : undefined

  const steps: MarketIngestionPipelineStep[] = [
    {
      key: 'artifacts',
      label: '解析产物',
      status: artifactsReady ? 'ready' : 'pending',
      description: artifactsReady ? `${artifactReadyCount}/${artifactTotal} 个核心文件已生成` : '等待解析生成结构化结果包',
    },
    {
      key: 'wiki',
      label: 'LLM-Wiki',
      status: wikiReady ? 'ready' : 'pending',
      description: wikiReady ? 'LLM-Wiki 已由 SEC 解析产物生成' : '等待 LLM-Wiki 入库',
    },
    {
      key: 'semantic',
      label: 'Wiki语义增强',
      status: semanticStepReady ? 'ready' : 'pending',
      description: semanticStepReady
        ? (semanticDescription || `Wiki语义证据 ${semanticEvidenceCount}`)
        : (semanticDescription || '等待 Wiki语义增强入库'),
    },
    {
      key: 'postgres',
      label: 'PostgreSQL',
      status: postgresSummary.status,
      description: postgresSummary.description,
    },
  ]

  const actions: Array<MarketIngestionPipelineActionState<UsSecIngestionActionKey>> = [
    {
      key: 'wiki',
      label: 'LLM-Wiki入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !artifactsReady,
      busy: busyAction === wikiActionKey,
      disabledReason: artifactsReady ? undefined : '缺少 SEC 解析产物，请先生成结构化结果包',
    },
    {
      key: 'semantic',
      label: 'Wiki语义增强入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !wikiReady,
      busy: busyAction === semanticActionKey,
      disabledReason: wikiReady ? undefined : '请先完成 LLM-Wiki 入库',
    },
    {
      key: 'postgres',
      label: 'PostgreSQL入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !readyForPostgres,
      busy: busyAction === postgresActionKey,
      disabledReason: readyForPostgres ? undefined : MISSING_DOCUMENT_FULL_REASON,
    },
  ]

  return {
    steps,
    cards: steps,
    activeStepIndex: activeStepIndex(steps, busyStepIndex),
    actions,
    runAll: {
      key: 'runAll',
      label: '一键入库',
      loadingLabel: '入库中...',
      primary: true,
      disabled: hasBusyAction || !readyForRunAll,
      busy: busyAction === remainingActionKey,
      disabledReason: readyForRunAll
        ? undefined
        : (!artifactsReady ? '缺少 SEC 解析产物，请先生成结构化结果包' : MISSING_DOCUMENT_FULL_REASON),
    },
    artifactsReady,
    artifactReadyCount,
    artifactTotal,
    artifactMissing: [],
    postgresSummary,
  }
}
