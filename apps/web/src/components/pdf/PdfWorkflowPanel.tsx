import { useMemo } from 'react'
import { Brain, Check, Database, Loader2, Play, RefreshCw } from 'lucide-react'
import type { ArtifactsMap, WorkflowJob, WorkflowStatus } from '../../lib/pdfTypes'
import { WIKI_INPUT_ARTIFACTS } from '../../lib/pdfTypes'
import { pipelineArtifactSummary, workflowReady } from '../../features/pdf-parsing/api'
import { workflowStateClass, workflowStateLabel } from '../../lib/pdfFormatting'

type WorkflowStep = 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import'

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

function semanticActionLabel(mode: 'standard' | 'generic'): string {
  return mode === 'generic' ? '使用项目设置模型生成语义层' : '使用项目设置模型增强研究语义层'
}

function normalizePipelineDescription(description: string): string {
  if (!description.includes('Wiki')) return description
  return '解析产物与 results 目录保存全量解析信息；PostgreSQL 入库直接读取解析产物，研究资产和派生知识资产由解析产物继续生成。'
}

function derivedKnowledgeAssetDescription(asset: WorkflowStatus['wiki']): string {
  if (asset?.status === 'ready') return '已由解析产物生成，可供研究资产引用'
  if (asset?.status === 'stale') return '解析产物已更新，建议重新生成'
  if (asset?.message && !/wiki/i.test(asset.message)) return asset.message
  if (asset?.status === 'failed' || asset?.status === 'error') return '生成失败，请查看流水线任务详情'
  return '等待从解析产物生成'
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
  const displayDescription = normalizePipelineDescription(description)

  const localSummary = pipelineArtifactSummary(artifacts)
  const backendSummary = workflowStatus?.artifactBundle
  const artifactReadyCount = backendSummary?.readyCount ?? localSummary.ready.length
  const artifactTotal = backendSummary?.total ?? localSummary.total
  const artifactMissing = backendSummary?.missing ?? localSummary.missing
  const bundleReady = backendSummary?.ready || localSummary.ready.length === localSummary.total

  const llmSemanticCounts = workflowStatus?.semantic?.llm?.counts || {}
  const llmSemanticDesc = workflowStatus?.semantic?.llm?.status === 'ready'
    ? `模型增强 ${llmSemanticCounts.claims || 0} 条判断 / ${llmSemanticCounts.risks || 0} 条风险`
    : ''
  const knowledgeAssetDesc = derivedKnowledgeAssetDescription(workflowStatus?.wiki)
  const databaseDesc = workflowReady(workflowStatus as Record<string, unknown> | null, 'database')
    ? `已从解析产物入库：指标 ${workflowStatus?.database?.statementItems || 0} / 表格 ${workflowStatus?.database?.tables || 0}`
    : (workflowStatus?.database?.message || '等待从解析产物入库')

  const steps = useMemo(
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
        desc: workflowReady(workflowStatus as Record<string, unknown> | null, 'semantic')
          ? `规则事实 ${workflowStatus?.semantic?.counts?.facts || 0} / 证据 ${workflowStatus?.semantic?.counts?.evidence || 0}；${llmSemanticDesc}`
          : (workflowStatus?.semantic?.message || llmSemanticDesc || '未生成或不完整'),
      },
      {
        key: 'database' as const,
        label: '生成与入库',
        status: workflowStatus?.database?.status,
        desc: databaseDesc,
      },
    ],
    [workflowStatus, backendSummary, artifactReadyCount, artifactTotal, knowledgeAssetDesc, llmSemanticDesc, databaseDesc],
  )

  const activeStep = useMemo(() => {
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
    const firstPending = steps.findIndex((s) => s.status !== 'ready')
    return firstPending >= 0 ? firstPending : steps.length - 1
  }, [workflowBusy, steps])

  const cards: Array<{ label: string; status?: string; desc: string }> = [
    {
      label: '解析产物',
      status: workflowStatus?.artifactBundle?.status,
      desc: backendSummary?.message || (workflowStatus?.documentFull?.status === 'ready' ? `${artifactReadyCount}/${artifactTotal} 个核心文件已生成` : '等待 document_full.json'),
    },
    {
      label: '派生知识资产',
      status: workflowStatus?.wiki?.status,
      desc: knowledgeAssetDesc,
    },
    {
      label: '研究语义层',
      status: workflowStatus?.semantic?.status,
      desc: workflowReady(workflowStatus as Record<string, unknown> | null, 'semantic')
        ? `规则事实 ${workflowStatus?.semantic?.counts?.facts || 0} / 证据 ${workflowStatus?.semantic?.counts?.evidence || 0}；${llmSemanticDesc}`
        : (workflowStatus?.semantic?.message || llmSemanticDesc || '未生成或不完整'),
    },
    {
      label: '生成与入库',
      status: workflowStatus?.database?.status,
      desc: databaseDesc,
    },
  ]

  const stepButtons: Array<{ key: WorkflowStep; label: string; loadingLabel: string; primary: boolean; disabled?: boolean }> =
    mode === 'generic'
      ? [
          { key: 'wiki-import-generic', label: 'LLM-Wiki入库', loadingLabel: '入库中...', primary: true },
          {
            key: 'semantic-generic',
            label: semanticActionLabel(mode),
            loadingLabel: '生成中...',
            primary: true,
            disabled: !['ready', 'stale'].includes(workflowStatus?.wiki?.status || ''),
          },
          { key: 'db-import', label: '导入 PostgreSQL', loadingLabel: '导入中...', primary: false },
        ]
      : [
          { key: 'wiki-import', label: 'LLM-Wiki入库', loadingLabel: '入库中...', primary: true },
          { key: 'wiki-import-generic', label: '生成通用主体资产', loadingLabel: '生成中...', primary: false },
          {
            key: 'semantic',
            label: 'LLM-Wiki语义增强入库',
            loadingLabel: '生成中...',
            primary: true,
            disabled: !['ready', 'stale'].includes(workflowStatus?.wiki?.status || ''),
          },
          {
            key: 'semantic-generic',
            label: '通用主体语义层',
            loadingLabel: '生成中...',
            primary: false,
            disabled: !['ready', 'stale'].includes(workflowStatus?.wiki?.status || ''),
          },
          { key: 'db-import', label: '导入 PostgreSQL', loadingLabel: '导入中...', primary: true },
        ]

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
            disabled={!!workflowBusy || !bundleReady}
          >
            {workflowBusy === 'remaining' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            一键生成与入库
          </button>
        </div>
      </div>

      <div className="pdf-pipeline-note mb-4">
        <Database className="h-4 w-4" />
        <div>
          PostgreSQL 入库直接读取解析产物；派生知识资产不复制全量解析包。<code>artifact_manifest.json</code> 只记录核心文件路径、hash 和版本，用于判断是否过期。
        </div>
      </div>

      {mode === 'generic' || workflowStatus?.semantic?.llm ? (
        <div className="pdf-pipeline-note mb-4">
          <Brain className="h-4 w-4" />
          <div>
            语义增强使用当前项目设置中的模型，可选择本地或云端 OpenAI-compatible / Hermes 预设；输出到 <code>semantic/llm/{workflowStatus?.semantic?.reportId || 'report'}/</code>，不覆盖规则层事实和证据。
          </div>
        </div>
      ) : null}

      {workflowError ? (
        <div className="mb-4 rounded-2xl border border-error/20 bg-error/5 p-3 text-sm text-error">{workflowError}</div>
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
            <span className="font-semibold text-text">流水线任务</span>
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
          <div className="pdf-preflight-list">
            {workflowJob.steps.map((step, index) => (
              <div key={`${step.step}-${index}`} className={`pdf-preflight-item ${step.status === 'failed' ? 'error' : step.status === 'skipped' ? 'warn' : ''}`}>
                <span className="pdf-preflight-dot" />
                <div>
                  <div className="pdf-preflight-title">
                    {step.step} · {step.status}
                  </div>
                  <div className="pdf-preflight-message">{step.message || step.error || ''}</div>
                </div>
              </div>
            ))}
          </div>
          {workflowJob.error ? <div className="mt-3 text-sm text-error">{workflowJob.error}</div> : null}
        </div>
      ) : null}

      <div className="mt-4 rounded-2xl border border-border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-text">核心解析产物清单</div>
            <div className="mt-1 text-xs leading-5 text-text-muted">这些文件共同支撑 PostgreSQL 入库、质量校验、研究资产生成和证据溯源；派生知识资产只引用清单，不重复保存全量包。</div>
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
        {stepButtons.map((btn) => (
          <button
            key={btn.key}
            type="button"
            className={btn.primary ? 'pdf-small-action primary inline-flex items-center gap-1' : 'pdf-small-action inline-flex items-center gap-1'}
            onClick={() => void runWorkflowStep(btn.key)}
            disabled={!!workflowBusy || !bundleReady || btn.disabled}
          >
            {workflowBusy === btn.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {workflowBusy === btn.key ? btn.loadingLabel : btn.label}
          </button>
        ))}
      </div>
    </div>
  )
}
