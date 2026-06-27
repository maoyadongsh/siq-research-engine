import { FileText, RotateCcw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DocumentTaskItem } from '@/lib/documentTypes'

function statusTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'done'
  if (value === 'completed_with_warnings') return 'warn'
  if (value === 'failed' || value === 'cancelled') return 'fail'
  return 'run'
}

function statusLabel(status?: string) {
  return ({
    queued: '排队',
    uploaded: '已上传',
    detecting_type: '识别类型',
    running: '解析中',
    postprocessing: '后处理',
    completed: '完成',
    completed_with_warnings: '有警告',
    failed: '失败',
    cancelled: '已取消',
  } as Record<string, string>)[String(status || '')] || status || '未知'
}

function formatSize(value?: number) {
  const size = Number(value || 0)
  if (!size) return ''
  if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(size / 1024))} KB`
}

export function DocumentTaskList({
  tasks,
  selectedTaskId,
  onSelect,
  onRetry,
  onDelete,
}: {
  tasks: DocumentTaskItem[]
  selectedTaskId: string
  onSelect: (taskId: string) => void
  onRetry: (taskId: string) => void
  onDelete: (taskId: string) => void
}) {
  return (
    <section className="doc-panel">
      <div className="doc-panel-head">
        <div>
          <h2>任务管理</h2>
          <p>当前只显示你拥有的通用文档解析任务。</p>
        </div>
        <span className="doc-badge">{tasks.length}</span>
      </div>
      <div className="doc-panel-body">
        <div className="doc-task-list">
          {tasks.length ? tasks.map((task) => (
            <div
              key={task.task_id}
              className={`doc-task ${selectedTaskId === task.task_id ? 'active' : ''}`}
            >
              <button
                type="button"
                className="min-w-0 text-left"
                onClick={() => onSelect(task.task_id)}
              >
                <span className="doc-task-title">{task.filename || task.task_id}</span>
                <span className="doc-task-meta">
                  <span>{task.document_kind || 'document'}</span>
                  {task.parser_provider ? <span>{task.parser_provider}</span> : null}
                  {task.file_size ? <span>{formatSize(task.file_size)}</span> : null}
                </span>
                <span className="mt-2 block">
                  <span className="doc-progress"><span style={{ width: `${Math.max(0, Math.min(100, Number(task.progress_percent || 0)))}%` }} /></span>
                </span>
              </button>
              <span className="grid justify-items-end gap-2">
                <span className={`doc-badge ${statusTone(task.status)}`}>{statusLabel(task.status)}</span>
                <span className="flex gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="重试"
                    onClick={(event) => {
                      event.stopPropagation()
                      onRetry(task.task_id)
                    }}
                  >
                    <RotateCcw className="h-3 w-3" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label="删除"
                    onClick={(event) => {
                      event.stopPropagation()
                      onDelete(task.task_id)
                    }}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </span>
              </span>
            </div>
          )) : (
            <div className="doc-empty min-h-[180px]">
              <div>
                <FileText className="mx-auto mb-3 h-8 w-8 text-text-muted" />
                <p>暂无通用文档解析任务</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
