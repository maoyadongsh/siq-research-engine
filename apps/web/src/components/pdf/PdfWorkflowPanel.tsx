import { useMemo } from 'react'
import { Brain, Check, Database, Loader2, Play, RefreshCw, RotateCcw, Terminal } from 'lucide-react'
import type { ArtifactsMap, WorkflowJob, WorkflowStatus } from '../../lib/pdfTypes'
import { WIKI_INPUT_ARTIFACTS } from '../../lib/pdfTypes'
import { pipelineArtifactSummary, workflowReady } from '../../features/pdf-parsing/api'
import { derivePdfGenericMarketIngestionPipelineState } from '../../features/market-parsing/marketIngestionPipelineState'
import { workflowStateClass, workflowStateLabel } from '../../lib/pdfFormatting'

type WorkflowStep = 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import'
type PipelineActionKey = WorkflowStep | 'wiki' | 'postgres'

export interface PdfWorkflowPanelProps {
  workflowStatus: WorkflowStatus | null
  workflowLoading: boolean
  workflowBusy: string
  workflowJob: WorkflowJob | null
  workflowError: string
  artifacts: ArtifactsMap | null
  mode?: 'standard' | 'generic'
  title?: string
  description?: string
  loadWorkflowStatus: () => Promise<void>
  runRemainingWorkflow: () => Promise<void>
  runWorkflowStep: (step: WorkflowStep) => Promise<void>
}

function normalizePipelineDescription(description: string, mode: 'standard' | 'generic'): string {
  if (!description.includes('Wiki')) return description
  if (mode === 'generic') {
    return '解析产物与 results 目录保存全量解析信息；LLM-Wiki、Wiki语义增强和 PostgreSQL 入库都读取同一套解析产物。'
  }
  return '解析产物与 results 目录保存全量解析信息；PostgreSQL 入库直接读取解析产物，研究资产和派生知识资产由解析产物继续生成。'
}

function derivedKnowledgeAssetDescription(asset: WorkflowStatus['wiki']): string {
  if (asset?.status === 'ready') return '已由解析产物生成，可供研究资产引用'
  if (asset?.status === 'stale') return '解析产物已更新，建议重新生成'
  if (asset?.message && !/wiki/i.test(asset.message)) return asset.message
  if (asset?.status === 'failed' || asset?.status === 'error') return '生成失败，请查看流水线任务详情'
  return '等待从解析产物生成'
}

function marketWorkflowStep(actionKey: PipelineActionKey): WorkflowStep {
  if (actionKey === 'wiki') return 'wiki-import-generic'
  if (actionKey === 'semantic') return 'semantic-generic'
  if (actionKey === 'postgres') return 'db-import'
  return actionKey
}

function compactOutput(value?: string): string {
  return String(value || '').trim().split(/\r?\n/).filter(Boolean).slice(-3).join('\n')
}

function standardBusyReason(workflowBusy: string): string | undefined {
  if (!workflowBusy) return undefined
  if (workflowBusy === 'wiki-import') return 'LLM-Wiki 入库正在执行，请等待完成'
  if (workflowBusy === 'wiki-import-generic') return '通用知识资产任务正在执行，请等待完成'
  if (workflowBusy === 'semantic') return 'LLM-Wiki 语义增强正在执行，请等待完成'
  if (workflowBusy === 'semantic-generic') return '通用语义层任务正在执行，请等待完成'
  if (workflowBusy === 'db-import') return 'PostgreSQL 入库正在执行，请等待完成'
  if (workflowBusy === 'remaining') return '一键生成与入库正在执行，请等待完成'
  return '流水线任务正在执行，请等待完成'
}

function standardArtifactDisabledReason(artifactMissing: string[]): string {
  return artifactMissing.length
    ? `缺少核心解析产物：${artifactMissing.join('、')}`
    : '核心解析产物未就绪，请先完成解析'
}

export function PdfWorkflowPanel(props: PdfWorkflowPanelProps) {
  const {
    workflowStatus,
    workflowLoading,
    workflowBusy,
    workflowJob,
    workflowError,
    artifacts,
    mode = 'standard',
    title = '数据管线',
    description = '解析产物与 results 目录保存全量解析信息；PostgreSQL 入库直接读取解析产物，研究资产和派生知识资产由解析产物继续生成。',
    loadWorkflowStatus,
    runRemainingWorkflow,
    runWorkflowStep,
  } = props
  const displayDescription = normalizePipelineDescription(description, mode)
  const genericPipelineState = useMemo(
    () => derivePdfGenericMarketIngestionPipelineState({ workflowStatus, artifacts, workflowBusy }),
    [artifacts, workflowBusy, workflowStatus],
  )

  const localSummary = pipelineArtifactSummary(artifacts)
  const backendSummary = workflowStatus?.artifactBundle
  const artifactReadyCount = backendSummary?.readyCount ?? localSummary.ready.length
  const artifactTotal = backendSummary?.total ?? localSummary.total
  const artifactMissing = backendSummary?.missing ?? localSummary.missing
  const bundleReady = backendSummary?.ready || localSummary.ready.length === localSummary.total
  const standardPipelineBusyReason = standardBusyReason(workflowBusy)
  const standardMissingArtifactReason = standardArtifactDisabledReason(artifactMissing)

  const llmSemanticCounts = workflowStatus?.semantic?.llm?.counts || {}
  const llmSemanticDesc = workflowStatus?.semantic?.llm?.status === 'ready'
    ? `模型增强 ${llmSemanticCounts.claims || 0} 条判断 / ${llmSemanticCounts.risks || 0} 条风险`
    : ''
  const isMarketWorkflow = mode === 'generic'
  const knowledgeAssetDesc = derivedKnowledgeAssetDescription(workflowStatus?.wiki)
  const standardSemanticDesc = workflowReady(workflowStatus as Record<string, unknown> | null, 'semantic')
    ? `规则事实 ${workflowStatus?.semantic?.counts?.facts || 0} / 证据 ${workflowStatus?.semantic?.counts?.evidence || 0}；${llmSemanticDesc}`
    : (workflowStatus?.semantic?.message || llmSemanticDesc || '未生成或不完整')
  const databaseDesc = workflowReady(workflowStatus as Record<string, unknown> | null, 'database')
    ? `已从解析产物入库：指标 ${workflowStatus?.database?.statementItems || 0} / 表格 ${workflowStatus?.database?.tables || 0}`
    : (workflowStatus?.database?.message || '等待从解析产物入库')
  const standardWikiReady = ['ready', 'stale'].includes(workflowStatus?.wiki?.status || '')
  const standardWikiDependencyReason = '请先完成 LLM-Wiki / 派生知识资产生成'

  const standardSteps = useMemo(
    () => [
      {
        key: 'artifactBundle' as const,
        label: '解析产物',
        status: workflowStatus?.artifactBundle?.status,
        desc: backendSummary?.message || (workflowStatus?.documentFull?.status === 'ready' ? `${artifactReadyCount}/${artifactTotal} 个核心文件已生成` : '等待 document_full.json'),
      },
      {
        key: 'wiki' as const,
        label: '派生知识资产',
        status: workflowStatus?.wiki?.status,
        desc: knowledgeAssetDesc,
      },
      {
        key: 'semantic' as const,
        label: '研究语义层',
        status: workflowStatus?.semantic?.status,
        desc: standardSemanticDesc,
      },
      {
        key: 'database' as const,
        label: '生成与入库',
        status: workflowStatus?.database?.status,
        desc: databaseDesc,
      },
    ],
    [workflowStatus, backendSummary, artifactReadyCount, artifactTotal, knowledgeAssetDesc, standardSemanticDesc, databaseDesc],
  )

  const steps = isMarketWorkflow
    ? genericPipelineState.steps.map((step) => ({
        key: step.key,
        label: step.label,
        status: step.status,
        desc: step.description,
      }))
    : standardSteps

  const standardActiveStep = useMemo(() => {
    if (workflowBusy) {
      const busyMap: Record<string, number> = {
        'wiki-import': 1,
        'wiki-import-generic': 1,
        semantic: 2,
        'semantic-generic': 2,
        'db-import': 3,
      }
      if (busyMap[workflowBusy] !== undefined) return busyMap[workflowBusy]
    }
    const firstPending = standardSteps.findIndex((s) => s.status !== 'ready')
    return firstPending >= 0 ? firstPending : standardSteps.length - 1
  }, [workflowBusy, standardSteps])
  const activeStep = isMarketWorkflow ? genericPipelineState.activeStepIndex : standardActiveStep

  const cards: Array<{ label: string; status?: string; desc: string }> = steps.map((step) => ({
    label: step.label,
    status: step.status,
    desc: step.desc,
  }))

  const stepButtons: Array<{ key: PipelineActionKey; label: string; loadingLabel: string; primary: boolean; disabled?: boolean; busy?: boolean; disabledReason?: string }> =
    mode === 'generic'
      ? genericPipelineState.actions
      : [
          { key: 'wiki-import', label: 'LLM-Wiki入库', loadingLabel: '入库中...', primary: true },
          { key: 'wiki-import-generic', label: '生成通用主体资产', loadingLabel: '生成中...', primary: false },
          {
            key: 'semantic',
            label: 'LLM-Wiki语义增强入库',
            loadingLabel: '生成中...',
            primary: true,
            disabled: !standardWikiReady,
            disabledReason: standardWikiReady ? undefined : standardWikiDependencyReason,
          },
          {
            key: 'semantic-generic',
            label: '通用主体语义层',
            loadingLabel: '生成中...',
            primary: false,
            disabled: !standardWikiReady,
            disabledReason: standardWikiReady ? undefined : standardWikiDependencyReason,
          },
          { key: 'db-import', label: '导入 PostgreSQL', loadingLabel: '导入中...', primary: true },
        ]
  const runAllLabel = isMarketWorkflow ? '一键入库' : '一键生成与入库'
  const runAllDisabled = isMarketWorkflow ? genericPipelineState.runAll.disabled : (!!workflowBusy || !bundleReady)
  const runAllDisabledReason = runAllDisabled
    ? (isMarketWorkflow
        ? genericPipelineState.runAll.disabledReason
        : (standardPipelineBusyReason || (!bundleReady ? standardMissingArtifactReason : undefined)))
    : undefined
  const runAllBusy = isMarketWorkflow ? genericPipelineState.runAll.busy : workflowBusy === 'remaining'
  const pipelineNote = isMarketWorkflow
    ? <>PostgreSQL 入库直接读取解析产物；LLM-Wiki 和 Wiki语义增强都引用同一套解析证据，不作为 PostgreSQL 主数据源。<code>artifact_manifest.json</code> 记录核心文件路径、hash 和版本，用于判断是否过期。</>
    : <>PostgreSQL 入库直接读取解析产物；派生知识资产不复制全量解析包。<code>artifact_manifest.json</code> 只记录核心文件路径、hash 和版本，用于判断是否过期。</>
  const artifactListDescription = isMarketWorkflow
    ? '这些文件共同支撑 PostgreSQL 入库、质量校验、LLM-Wiki、Wiki语义增强和证据溯源；派生资产只引用清单，不重复保存全量包。'
    : '这些文件共同支撑 PostgreSQL 入库、质量校验、研究资产生成和证据溯源；派生知识资产只引用清单，不重复保存全量包。'
  const stepButtonStates = stepButtons.map((btn) => {
    const buttonBusy = btn.busy ?? workflowBusy === btn.key
    const buttonDisabled = isMarketWorkflow ? Boolean(btn.disabled) : (!!workflowBusy || !bundleReady || Boolean(btn.disabled))
    const workflowStep = isMarketWorkflow ? marketWorkflowStep(btn.key) : btn.key as WorkflowStep
    const disabledReason = buttonDisabled
      ? (isMarketWorkflow
          ? btn.disabledReason
          : (standardPipelineBusyReason || (!bundleReady ? standardMissingArtifactReason : btn.disabledReason)))
      : undefined
    return { ...btn, buttonBusy, buttonDisabled, workflowStep, disabledReason }
  })
  const disabledReasonHints = Array.from(
    new Set(
      [
        runAllDisabledReason ? `${runAllLabel}：${runAllDisabledReason}` : '',
        ...stepButtonStates
          .filter((btn) => btn.buttonDisabled && btn.disabledReason)
          .map((btn) => `${btn.label}：${btn.disabledReason}`),
      ].filter(Boolean),
    ),
  )

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-text">
            <Database className="h-4 w-4 text-primary" />
            {title}
          </h3>
          <p className="mt-1 text-sm text-text-muted">
            {displayDescription}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="pdf-small-action inline-flex items-center gap-1"
            onClick={() => void loadWorkflowStatus()}
            disabled={workflowLoading}
          >
            {workflowLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新状态
          </button>
          <button
            type="button"
            className="pdf-small-action primary inline-flex items-center gap-1"
            onClick={() => void runRemainingWorkflow()}
            disabled={runAllDisabled}
            title={runAllDisabledReason}
          >
            {runAllBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {runAllLabel}
          </button>
        </div>
      </div>

      <div className="pdf-pipeline-note mb-4">
        <Database className="h-4 w-4" />
        <div>
          {pipelineNote}
        </div>
      </div>

      {mode === 'generic' || workflowStatus?.semantic?.llm ? (
        <div className="pdf-pipeline-note mb-4">
          <Brain className="h-4 w-4" />
          <div>
            Wiki语义增强使用当前项目设置中的模型，可选择本地或云端 OpenAI-compatible / Hermes 预设；输出到 <code>semantic/llm/{workflowStatus?.semantic?.reportId || 'report'}/</code>，不覆盖规则层事实和证据。
          </div>
        </div>
      ) : null}

      {workflowError ? (
        <div className="mb-4 rounded-2xl border border-error/20 bg-error/5 p-3 text-sm text-error">{workflowError}</div>
      ) : null}

      {disabledReasonHints.length ? (
        <div className="mb-4 rounded-2xl border border-warning/20 bg-warning/5 p-3 text-xs leading-5 text-warning">
          <div className="font-semibold">当前不可执行原因</div>
          <ul className="mt-1 list-disc pl-4">
            {disabledReasonHints.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mb-5 flex items-start gap-1 overflow-x-auto pb-2">
        {steps.map((step, index) => {
          const completed = step.status === 'ready'
          const active = index === activeStep
          const isLast = index === steps.length - 1
          return (
            <div key={step.key} className="relative flex min-w-[5.5rem] flex-1 flex-col items-center px-1">
              {!isLast && (
                <div
                  className={`absolute left-1/2 top-3.5 h-0.5 w-full ${index < activeStep || (completed && index < steps.length - 1) ? 'bg-primary/40' : 'bg-border'}`}
                />
              )}
              <div
                className={`relative z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 text-xs font-bold ${
                  completed
                    ? 'border-green-600 bg-green-600 text-white'
                    : active
                      ? 'border-primary bg-primary text-white shadow-md shadow-primary/25'
                      : 'border-border bg-card text-text-muted'
                }`}
              >
                {completed ? <Check className="h-4 w-4" /> : index + 1}
              </div>
              <div className={`mt-2 text-center text-xs font-semibold ${active ? 'text-text' : 'text-text-muted'}`}>{step.label}</div>
              <div className="mt-0.5 max-w-[8rem] text-center text-[11px] leading-tight text-text-muted line-clamp-2">
                {step.desc}
              </div>
            </div>
          )
        })}
      </div>

      <div className="hidden gap-3 md:grid md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <div key={card.label} className="rounded-2xl border border-border bg-card p-4">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-semibold text-text">{card.label}</span>
              <span className={`secondary-status ${workflowStateClass(card.status)}`}>{workflowStateLabel(card.status)}</span>
            </div>
            <p className="mt-2 break-all text-sm leading-6 text-text-muted">{card.desc}</p>
          </div>
        ))}
      </div>

      {workflowJob?.steps?.length ? (
        <div className="mt-4 rounded-2xl border border-border bg-bg p-3 text-sm text-text-muted">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-2 font-semibold text-text">
              <Terminal className="h-4 w-4 text-primary" />
              流水线任务
            </span>
            <span
              className={`secondary-status ${
                workflowJob.status === 'succeeded'
                  ? 'secondary-status-success'
                  : workflowJob.status === 'failed'
                    ? 'secondary-status-warning'
                    : 'secondary-status-info'
              }`}
            >
              {workflowJob.status}
            </span>
          </div>
          <div className="mb-3 flex flex-wrap gap-2 text-xs text-text-muted">
            {workflowJob.currentStep ? <span className="secondary-status secondary-status-info">当前 step：{workflowJob.currentStep}</span> : null}
            {workflowJob.retryScope ? (
              <span className="secondary-status secondary-status-info inline-flex items-center gap-1">
                <RotateCcw className="h-3.5 w-3.5" />
                retry scope：{workflowJob.retryScope}
              </span>
            ) : null}
            {workflowJob.failedStep ? <span className="secondary-status secondary-status-warning">失败 step：{workflowJob.failedStep}</span> : null}
          </div>
          <div className="pdf-preflight-list">
            {workflowJob.steps.map((step, index) => {
              const stdout = compactOutput(step.stdoutTail)
              const stderr = compactOutput(step.stderrTail)
              const timeoutSeconds = step.timeoutSeconds ?? step.commandResults?.find((item) => item.timeoutSeconds)?.timeoutSeconds
              return (
                <div key={`${step.step}-${index}`} className={`pdf-preflight-item ${step.status === 'failed' ? 'error' : step.status === 'skipped' ? 'warn' : ''}`}>
                  <span className="pdf-preflight-dot" />
                  <div className="min-w-0 flex-1">
                    <div className="pdf-preflight-title">
                      {step.step} · {step.status}
                    </div>
                    {timeoutSeconds ? <div className="pdf-preflight-message">timeout：{timeoutSeconds}s</div> : null}
                    <div className="pdf-preflight-message">{step.message || step.error || ''}</div>
                    {stdout ? <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-card p-2 text-[11px] leading-5 text-text-muted">stdout: {stdout}</pre> : null}
                    {stderr ? <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-error/5 p-2 text-[11px] leading-5 text-error">stderr: {stderr}</pre> : null}
                  </div>
                </div>
              )
            })}
          </div>
          {workflowJob.error ? <div className="mt-3 text-sm text-error">{workflowJob.error}</div> : null}
        </div>
      ) : null}

      <div className="mt-4 rounded-2xl border border-border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-text">核心解析产物清单</div>
            <div className="mt-1 text-xs leading-5 text-text-muted">{artifactListDescription}</div>
          </div>
          <span className="secondary-status secondary-status-info">{artifactReadyCount}/{artifactTotal}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {WIKI_INPUT_ARTIFACTS.map((name) => {
            const ok = backendSummary?.artifacts?.[name]?.exists ?? !!artifacts?.[name]?.exists
            return (
              <span key={name} className={`secondary-status ${ok ? 'secondary-status-success' : ''}`}>
                {name}
              </span>
            )
          })}
        </div>
        {artifactMissing.length > 0 ? (
          <div className="mt-3 text-xs leading-5 text-text-muted">未生成：{artifactMissing.join('、')}</div>
        ) : null}
        {workflowStatus?.preflight?.checks?.length ? (
          <div className="pdf-preflight-list mt-3">
            {workflowStatus.preflight.checks.map((check) => (
              <div key={check.id} className={`pdf-preflight-item ${check.blocking ? 'error' : check.ok ? '' : 'warn'}`}>
                <span className="pdf-preflight-dot" />
                <div>
                  <div className="pdf-preflight-title">
                    {check.label} · {check.status}
                  </div>
                  <div className="pdf-preflight-message">{check.message}</div>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {stepButtonStates.map((btn) => {
          return (
            <button
              key={btn.key}
              type="button"
              className={btn.primary ? 'pdf-small-action primary inline-flex items-center gap-1' : 'pdf-small-action inline-flex items-center gap-1'}
              onClick={() => void runWorkflowStep(btn.workflowStep)}
              disabled={btn.buttonDisabled}
              title={btn.disabledReason}
            >
              {btn.buttonBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {btn.buttonBusy ? btn.loadingLabel : btn.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
