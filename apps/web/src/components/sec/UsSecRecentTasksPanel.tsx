import { type CSSProperties, useDeferredValue, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { EmptyState } from '@/components/page'
import { formatDateTime } from '../../lib/pdfFormatting'
import type { UsSecRecentTaskRow } from '../../features/market-parsing/usSecWorkbench'

export interface UsSecRecentTasksPanelProps {
  tasks: UsSecRecentTaskRow[]
  selectedTaskId: string
  loading: boolean
  busyAction: string
  onViewResult: (task: UsSecRecentTaskRow) => Promise<void>
  onRebuild: (task: UsSecRecentTaskRow) => Promise<void>
  onRefresh: () => Promise<void>
}

function statusClass(status: string): string {
  if (status === 'postgres_ready' || status === 'package_ready') return 'secondary-status-success'
  if (status === 'stale' || status === 'warning' || status === 'failed') return 'secondary-status-warning'
  if (status === 'building') return 'secondary-status-info'
  return ''
}

export function UsSecRecentTasksPanel({
  tasks,
  selectedTaskId,
  loading,
  busyAction,
  onViewResult,
  onRebuild,
  onRefresh,
}: UsSecRecentTasksPanelProps) {
  const deferredTasks = useDeferredValue(tasks)
  const visibleTasks = useMemo(() => deferredTasks.slice(0, 100), [deferredTasks])

  return (
    <section className="surface-panel">
      <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-4 sm:px-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-bold text-text sm:text-xl">最近任务（点击查看结果）</h2>
          <p className="mt-1 text-sm leading-6 text-text-muted">已解析 SEC 任务列表；点击任务或查看结果后再展开数据管线、Markdown、质量报告和勾稽校验。</p>
        </div>
        <div className="shrink-0">
          <button onClick={() => void onRefresh()} disabled={loading} className="pdf-small-action inline-flex items-center gap-1">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            刷新
          </button>
        </div>
      </div>

      <div className="p-4 sm:p-5">
        {visibleTasks.length ? (
          <div className="apple-card rounded-[24px] p-4 sm:p-6">
            <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="text-base font-semibold text-text">任务列表</h3>
              <button onClick={() => void onRefresh()} className="self-start text-sm font-semibold text-text-muted hover:text-text">
                刷新
              </button>
            </div>

            {visibleTasks.map((task) => {
              const active = task.id === selectedTaskId
              const busyView = busyAction === `view:${task.id}`
              const busyRebuild = busyAction === `rebuild:${task.id}`
              return (
                <div key={task.id} className={`pdf-task-item content-auto ${active ? 'ring-1 ring-primary/30' : ''}`}>
                  <button
                    type="button"
                    className="task-main task-main-button"
                    aria-label={`查看解析结果：${task.companyName} ${task.ticker} ${task.form} ${task.periodEnd}`}
                    onClick={() => void onViewResult(task)}
                  >
                    <span className="task-name">{task.companyName} · {task.ticker} · {task.form} · {task.periodEnd}</span>
                    <div className="task-meta">
                      <span className={`secondary-status ${statusClass(task.status)}`}>{task.statusText}</span>
                      <span className="text-text-muted text-xs">{task.sectionCount} sections</span>
                      <span className="text-text-muted text-xs">{task.factCount} facts</span>
                      <span className="text-text-muted text-xs">{formatDateTime(task.filingDate)}</span>
                    </div>
                  </button>
                  <div className="task-actions" style={{ '--task-action-count': 2 } as CSSProperties}>
                    <button
                      type="button"
                      className="pdf-task-action primary"
                      onClick={(event) => {
                        event.stopPropagation()
                        void onViewResult(task)
                      }}
                    >
                      {busyView ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      查看结果
                    </button>
                    <button
                      type="button"
                      className="pdf-task-action"
                      onClick={(event) => {
                        event.stopPropagation()
                        void onRebuild(task)
                      }}
                      disabled={busyRebuild}
                    >
                      {busyRebuild ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      重建
                    </button>
                  </div>
                </div>
              )
            })}

            {tasks.length > visibleTasks.length ? (
              <p className="mt-3 rounded-xl border border-border bg-bg/60 px-3 py-2 text-xs leading-5 text-text-muted">
                已显示最近 {visibleTasks.length} 个任务，共 {tasks.length} 个；刷新后仍按最近任务优先展示。
              </p>
            ) : null}
          </div>
        ) : (
          <EmptyState
            title="暂无已解析 SEC 任务"
            description="先从上方已下载财报中生成 SEC 解析产物包，任务会出现在这里。"
            size="sm"
            className="rounded-[18px] border border-dashed border-border bg-bg/50"
          />
        )}
      </div>
    </section>
  )
}
