import { Brain, Database, Loader2, Play, RefreshCw } from 'lucide-react'
import type { ArtifactsMap, WorkflowJob, WorkflowStatus } from '../../lib/pdfTypes'
import { WIKI_INPUT_ARTIFACTS } from '../../lib/pdfTypes'
import { pipelineArtifactSummary, workflowReady } from '../../lib/pdfApi'
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
    description = 'PostgreSQL 与 results 目录保存全量解析信息；Wiki 保留报告入口、公司级知识资产和轻量产物清单。',
    loadWorkflowStatus,
    runRemainingWorkflow,
    runWorkflowStep,
  } = props

  const localSummary = pipelineArtifactSummary(artifacts)
  const backendSummary = workflowStatus?.artifactBundle
  const artifactReadyCount = backendSummary?.readyCount ?? localSummary.ready.length
  const artifactTotal = backendSummary?.total ?? localSummary.total
  const artifactMissing = backendSummary?.missing ?? localSummary.missing
  const bundleReady = backendSummary?.ready || localSummary.ready.length === localSummary.total

  const llmSemanticCounts = workflowStatus?.semantic?.llm?.counts || {}
  const llmSemanticDesc = workflowStatus?.semantic?.llm?.status === 'ready'
    ? `LLM 增强 ${llmSemanticCounts.claims || 0} 条判断 / ${llmSemanticCounts.risks || 0} 条风险`
    : ''

  const cards: Array<{ label: string; status?: string; desc: string }> = [
    {
      label: '解析产物包',
      status: workflowStatus?.artifactBundle?.status,
      desc: backendSummary?.message || (workflowStatus?.documentFull?.status === 'ready' ? `${artifactReadyCount}/${artifactTotal} 个核心文件已生成` : '等待 document_full.json'),
    },
    {
      label: 'Wiki 入库',
      status: workflowStatus?.wiki?.status,
      desc: workflowStatus?.wiki?.status === 'ready' ? workflowStatus?.wiki?.companyDir || '已导入' : (workflowStatus?.wiki?.message || '未导入 Wiki'),
    },
    {
      label: '语义层',
      status: workflowStatus?.semantic?.status,
      desc: workflowReady(workflowStatus as Record<string, unknown> | null, 'semantic')
        ? `规则事实 ${workflowStatus?.semantic?.counts?.facts || 0} / 证据 ${workflowStatus?.semantic?.counts?.evidence || 0}；${llmSemanticDesc}`
        : (workflowStatus?.semantic?.message || llmSemanticDesc || '未生成或不完整'),
    },
    {
      label: 'PostgreSQL',
      status: workflowStatus?.database?.status,
      desc: workflowReady(workflowStatus as Record<string, unknown> | null, 'database')
        ? `指标 ${workflowStatus?.database?.statementItems || 0} / 表格 ${workflowStatus?.database?.tables || 0}`
        : (workflowStatus?.database?.message || '未入库'),
    },
  ]

  const stepButtons: Array<{ key: WorkflowStep; label: string; loadingLabel: string; primary: boolean; disabled?: boolean }> =
    mode === 'generic'
      ? [
          { key: 'wiki-import-generic', label: '通用主体入库', loadingLabel: '导入中...', primary: true },
          {
            key: 'semantic-generic',
            label: '生成通用语义层',
            loadingLabel: '生成中...',
            primary: true,
            disabled: !['ready', 'stale'].includes(workflowStatus?.wiki?.status || ''),
          },
          { key: 'db-import', label: '导入 PostgreSQL', loadingLabel: '导入中...', primary: false },
        ]
      : [
          { key: 'wiki-import', label: '导入 Wiki', loadingLabel: '导入中...', primary: true },
          { key: 'wiki-import-generic', label: '通用主体入库', loadingLabel: '导入中...', primary: false },
          {
            key: 'semantic',
            label: '生成 Wiki 语义层',
            loadingLabel: '生成中...',
            primary: true,
            disabled: !['ready', 'stale'].includes(workflowStatus?.wiki?.status || ''),
          },
          {
            key: 'semantic-generic',
            label: '生成通用语义层',
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
            {description}
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
          {mode === 'standard' ? (
            <button
              type="button"
              className="pdf-small-action primary inline-flex items-center gap-1"
              onClick={() => void runRemainingWorkflow()}
              disabled={!!workflowBusy || !bundleReady}
            >
              {workflowBusy === 'remaining' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              继续入库
            </button>
          ) : null}
        </div>
      </div>

      <div className="pdf-pipeline-note mb-4">
        <Database className="h-4 w-4" />
        <div>
          Wiki 不复制全量解析包；<code>artifact_manifest.json</code> 只记录核心文件路径、hash 和版本，用于判断是否过期。结构化事实、完整文档和证据页码默认直接从 Wiki 与 results 目录读取。
        </div>
      </div>

      {workflowStatus?.semantic?.llm ? (
        <div className="pdf-pipeline-note mb-4">
          <Brain className="h-4 w-4" />
          <div>
            模型语义增强默认调用本地 Qwen3.6，也可在设置页切换到本机 Gemma4；输出到 <code>semantic/llm/{workflowStatus.semantic.reportId || 'report'}/</code>，不覆盖规则层事实和证据。
          </div>
        </div>
      ) : null}

      {workflowError ? (
        <div className="mb-4 rounded-2xl border border-error/20 bg-error/5 p-3 text-sm text-error">{workflowError}</div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
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
            <div className="mt-1 text-xs leading-5 text-text-muted">这些文件共同支撑入库、质量校验和证据溯源；Wiki 仅引用清单，不重复保存全量包。</div>
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
