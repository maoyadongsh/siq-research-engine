import { useMemo, useState } from 'react'
import { Download, FileText, Loader2, RotateCcw, Search, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/page'
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
  selectedBulkTaskIds,
  bulkBusy,
  onSelect,
  onRetry,
  onDelete,
  onToggleBulkSelection,
  onClearBulkSelection,
  onRetrySelected,
  onDeleteSelected,
  onDownloadSelected,
  defaultOpen = true,
}: {
  tasks: DocumentTaskItem[]
  selectedTaskId: string
  selectedBulkTaskIds: string[]
  bulkBusy: string
  onSelect: (taskId: string) => void
  onRetry: (taskId: string) => void
  onDelete: (taskId: string) => void
  onToggleBulkSelection: (taskId: string, selected: boolean) => void
  onClearBulkSelection: () => void
  onRetrySelected: () => void
  onDeleteSelected: () => void
  onDownloadSelected: () => void
  defaultOpen?: boolean
}) {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const selectedSet = useMemo(() => new Set(selectedBulkTaskIds), [selectedBulkTaskIds])
  const filteredTasks = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return tasks.filter((task) => {
      const status = String(task.status || '').toLowerCase()
      if (statusFilter !== 'all' && status !== statusFilter) return false
      if (!needle) return true
      return [
        task.filename,
        task.task_id,
        task.document_kind,
        task.parser_provider,
        task.quality_status,
      ].some((value) => String(value || '').toLowerCase().includes(needle))
    })
  }, [query, statusFilter, tasks])
  const allFilteredSelected = filteredTasks.length > 0 && filteredTasks.every((task) => selectedSet.has(task.task_id))

  const toggleFiltered = (selected: boolean) => {
    filteredTasks.forEach((task) => onToggleBulkSelection(task.task_id, selected))
  }

  return (
    <details className="doc-panel" open={defaultOpen}>
      <summary className="doc-panel-head">
        <div>
          <h2>任务管理</h2>
          <p>当前只显示你拥有的通用文档解析任务。</p>
        </div>
        <span className="doc-badge">{tasks.length}</span>
      </summary>
      <div className="doc-panel-body">
        <div className="doc-task-toolbar">
          <label className="doc-search">
            <Search className="h-4 w-4" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件、任务或 provider"
            />
          </label>
          <select className="doc-select doc-status-filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">全部状态</option>
            <option value="queued">排队</option>
            <option value="running">解析中</option>
            <option value="postprocessing">后处理</option>
            <option value="completed">完成</option>
            <option value="completed_with_warnings">有警告</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
          </select>
        </div>

        <div className="doc-batch-bar">
          <label className="doc-check compact">
            <input
              type="checkbox"
              checked={allFilteredSelected}
              disabled={!filteredTasks.length}
              onChange={(event) => toggleFiltered(event.target.checked)}
            />
            选择当前结果
          </label>
          <span className="doc-batch-count">{selectedBulkTaskIds.length ? `已选 ${selectedBulkTaskIds.length}` : `筛选 ${filteredTasks.length}`}</span>
          <div className="doc-action-row">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!selectedBulkTaskIds.length || Boolean(bulkBusy)}
              onClick={onDownloadSelected}
              leftIcon={bulkBusy === 'download' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            >
              下载
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!selectedBulkTaskIds.length || Boolean(bulkBusy)}
              onClick={onRetrySelected}
              leftIcon={bulkBusy === 'retry' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            >
              重试
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!selectedBulkTaskIds.length || Boolean(bulkBusy)}
              onClick={onDeleteSelected}
              leftIcon={bulkBusy === 'delete' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            >
              删除
            </Button>
            {selectedBulkTaskIds.length ? (
              <Button type="button" variant="ghost" size="sm" disabled={Boolean(bulkBusy)} onClick={onClearBulkSelection}>
                清空
              </Button>
            ) : null}
          </div>
        </div>

        <div className="doc-task-list">
          {filteredTasks.length ? filteredTasks.map((task) => (
            <div
              key={task.task_id}
              className={`doc-task ${selectedTaskId === task.task_id ? 'active' : ''}`}
            >
              <input
                className="doc-task-check"
                type="checkbox"
                aria-label={`选择 ${task.filename || task.task_id}`}
                checked={selectedSet.has(task.task_id)}
                onChange={(event) => onToggleBulkSelection(task.task_id, event.target.checked)}
              />
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
            <EmptyState
              icon={FileText}
              title={tasks.length ? '没有符合筛选条件的任务' : '暂无通用文档解析任务'}
              description={tasks.length ? '尝试调整筛选条件或搜索关键词。' : '上传文件或解析 URL 后会出现在这里。'}
              size="md"
              className="min-h-[180px]"
            />
          )}
        </div>
      </div>
    </details>
  )
}
