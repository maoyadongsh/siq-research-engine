import { type CSSProperties, useDeferredValue, useMemo } from 'react'
import { AlertTriangle, Inbox, Loader2, RefreshCw } from 'lucide-react'
import type { TaskItem } from '../../lib/pdfTypes'
import { isTerminal, statusBadgeClass, translateStatus } from '../../lib/pdfFormatting'

export interface PdfTaskListProps {
  tasks: TaskItem[]
  tasksLoading: boolean
  tasksError: string | null
  taskId: string | null
  resultLoading: boolean
  onResume: (task: TaskItem) => void
  onViewResult: (task: TaskItem) => void
  onDelete: (taskId: string, status: string) => void
  onRefetch: (taskId: string) => void
  onReparse: (taskId: string) => void
  onRefresh: () => void
}

export function PdfTaskList({
  tasks,
  tasksLoading,
  tasksError,
  taskId,
  resultLoading,
  onResume,
  onViewResult,
  onDelete,
  onRefetch,
  onReparse,
  onRefresh,
}: PdfTaskListProps) {
  const deferredTasks = useDeferredValue(tasks)
  const visibleTasks = useMemo(() => deferredTasks.slice(0, 100), [deferredTasks])

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="text-xs text-text-muted">{tasks.length ? `${tasks.length} 个任务` : '任务列表'}</span>
        <button
          type="button"
          onClick={() => onRefresh()}
          disabled={tasksLoading}
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-text-muted transition hover:bg-bg hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
          aria-label="刷新最近任务"
          title="刷新最近任务"
        >
          <RefreshCw className={`h-4 w-4 ${tasksLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {tasksError ? (
        <div role="alert" className="mb-4 flex items-start gap-2 rounded-md border border-error/20 bg-error-soft px-3 py-2.5 text-sm text-error">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0">
            <p className="font-semibold">最近任务加载失败</p>
            <p className="mt-0.5 break-words text-xs leading-5">{tasksError}</p>
          </div>
          <button
            type="button"
            onClick={() => onRefresh()}
            className="ml-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition hover:bg-error/10"
            aria-label="重试加载最近任务"
            title="重试加载最近任务"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      ) : null}

      {tasksLoading && tasks.length === 0 ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-2 text-center text-sm text-text-muted" aria-live="polite">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <span>正在加载最近任务</span>
        </div>
      ) : null}

      {!tasksLoading && !tasksError && tasks.length === 0 ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-2 text-center">
          <Inbox className="h-5 w-5 text-text-muted" />
          <p className="text-sm font-semibold text-text">暂无最近任务</p>
          <button
            type="button"
            onClick={() => onRefresh()}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm font-semibold text-text-muted transition hover:bg-bg hover:text-text"
          >
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
      ) : null}

      {visibleTasks.map((task) => {
        const canView = ['completed', 'success', 'done', 'finished'].includes(String(task.status))
        const canRefetch = ['completed', 'completed_missing_artifact'].includes(String(task.status))
        const canReparse = isTerminal(String(task.status))
        const actionCount = Number(canView) + Number(canRefetch) + Number(canReparse) + 1
        const taskActionStyle = { '--task-action-count': Math.max(actionCount, 1) } as CSSProperties
        const openTask = () => {
          if (canView) {
            onViewResult(task)
            return
          }
          onResume(task)
        }
        return (
          <div key={task.task_id} className="pdf-task-item content-auto">
            <button
              type="button"
              className="task-main task-main-button"
              aria-label={`${canView ? '查看解析结果' : '恢复解析任务'}：${task.filename}`}
              onClick={openTask}
            >
              <span className="task-name">{task.filename}</span>
              <div className="task-meta">
                <span className={`pdf-status-badge ${statusBadgeClass(String(task.status))}`}>{translateStatus(String(task.status))}</span>
                {task.local_queue_position && (
                  <span className="text-text-muted text-xs">本地队列第 {task.local_queue_position} 位</span>
                )}
                <span className="text-text-muted text-xs">
                  {task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : ''}
                </span>
              </div>
            </button>
            <div className="task-actions" style={taskActionStyle}>
              {canView && (
                <button
                  type="button"
                  className="pdf-task-action primary"
                  onClick={(e) => {
                    e.stopPropagation()
                    onViewResult(task)
                  }}
                >
                  {resultLoading && taskId === task.task_id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : null}
                  查看结果
                </button>
              )}
              {canRefetch && (
                <button
                  type="button"
                  className="pdf-task-action"
                  onClick={(e) => {
                    e.stopPropagation()
                    onRefetch(task.task_id)
                  }}
                >
                  补拉
                </button>
              )}
              {canReparse && (
                <button
                  type="button"
                  className="pdf-task-action"
                  onClick={(e) => {
                    e.stopPropagation()
                    onReparse(task.task_id)
                  }}
                >
                  重跑
                </button>
              )}
              <button
                type="button"
                className="pdf-task-action danger"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(task.task_id, String(task.status))
                }}
              >
                删除
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
  )
}
