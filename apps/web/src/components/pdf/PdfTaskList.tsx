import { useDeferredValue, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import type { TaskItem } from '../../lib/pdfTypes'
import { isTerminal, statusBadgeClass, translateStatus } from '../../lib/pdfFormatting'

export interface PdfTaskListProps {
  tasks: TaskItem[]
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
  if (tasks.length === 0) return null
  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="text-base font-semibold text-text">最近任务</h3>
        <button onClick={() => onRefresh()} className="self-start text-sm font-semibold text-text-muted hover:text-text">
          刷新
        </button>
      </div>
      {visibleTasks.map((task) => (
        <div
          key={task.task_id}
          className="pdf-task-item content-auto"
          role="button"
          tabIndex={0}
          onClick={() => onResume(task)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              onResume(task)
            }
          }}
        >
          <div className="task-main">
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
          </div>
          <div className="task-actions">
            {['completed', 'success', 'done', 'finished'].includes(String(task.status)) && (
              <button
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
            {['completed', 'completed_missing_artifact'].includes(String(task.status)) && (
              <button
                className="pdf-task-action"
                onClick={(e) => {
                  e.stopPropagation()
                  onRefetch(task.task_id)
                }}
              >
                补拉
              </button>
            )}
            {isTerminal(String(task.status)) && (
              <button
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
      ))}
      {tasks.length > visibleTasks.length ? (
        <p className="mt-3 rounded-xl border border-border bg-bg/60 px-3 py-2 text-xs leading-5 text-text-muted">
          已显示最近 {visibleTasks.length} 个任务，共 {tasks.length} 个；刷新后仍按最近任务优先展示。
        </p>
      ) : null}
    </div>
  )
}
