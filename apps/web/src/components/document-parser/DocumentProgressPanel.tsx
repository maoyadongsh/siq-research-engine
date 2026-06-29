import { Activity, Clock3, FileText, ListChecks } from 'lucide-react'
import type { DocumentLogEntry, DocumentTaskItem } from '@/lib/documentTypes'

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
    submitted: '已提交',
    pending: '排队中',
    processing: '处理中',
    postprocessing: '后处理',
    completed: '完成',
    completed_with_warnings: '有警告',
    failed: '失败',
    cancelled: '已取消',
  } as Record<string, string>)[String(status || '')] || status || '未知'
}

function formatDuration(seconds?: number) {
  const value = Number(seconds || 0)
  if (!Number.isFinite(value) || value <= 0) return ''
  const minutes = Math.floor(value / 60)
  const remain = Math.floor(value % 60)
  return minutes > 0 ? `${minutes}分${remain}秒` : `${remain}秒`
}

function progressPercent(task: DocumentTaskItem) {
  const explicit = Number(task.progress_percent || 0)
  if (explicit > 0) return Math.max(0, Math.min(100, explicit))
  const total = Number(task.total_pages || 0)
  const processed = Number(task.processed_pages || 0)
  if (total > 0 && processed >= 0) return Math.max(0, Math.min(99, Math.round((processed / total) * 100)))
  return ['completed', 'completed_with_warnings'].includes(String(task.status || '')) ? 100 : 0
}

function stageText(task: DocumentTaskItem) {
  const upstream = task.upstream_status ? `PDF ${statusLabel(task.upstream_status)}` : ''
  const stage = statusLabel(task.stage || task.status)
  return upstream || stage
}

export function DocumentProgressPanel({
  task,
  logs,
  defaultOpen = false,
}: {
  task: DocumentTaskItem | undefined
  logs: DocumentLogEntry[]
  defaultOpen?: boolean
}) {
  const pct = task ? progressPercent(task) : 0
  const totalPages = Number(task?.total_pages || 0)
  const processedPages = Number(task?.processed_pages || 0)
  const remainingPages = totalPages > 0 ? Math.max(0, totalPages - processedPages) : 0
  const elapsed = formatDuration(task?.elapsed_seconds)
  const visibleLogs = logs.slice(-120)

  return (
    <details className="doc-panel" open={defaultOpen}>
      <summary className="doc-panel-head">
        <div>
          <h2>解析进度</h2>
          <p>当前任务的解析状态、进度与处理日志。</p>
        </div>
      </summary>
      {task ? (
        <div className="doc-panel-body">
          <section className="doc-progress-stack">
      <div className="doc-panel">
        <div className="doc-progress-card">
          <div className="doc-progress-topline">
            <div className="doc-progress-title">
              <span className="doc-live-dot" />
              <h2>当前解析进度</h2>
            </div>
            <span className={`doc-badge ${statusTone(task.status)}`}>{statusLabel(task.status)}</span>
          </div>
          <div className="doc-progress-bar-row">
            <span className="doc-progress"><span style={{ width: `${pct}%` }} /></span>
            <strong>{Math.round(pct)}%</strong>
          </div>
          <div className="doc-progress-stage">{stageText(task)}</div>
          <div className="doc-progress-facts">
            {task.local_queue_position ? <span><ListChecks className="h-4 w-4" />本地队列: 第 {task.local_queue_position} 位</span> : null}
            {task.queue_position != null ? <span><ListChecks className="h-4 w-4" />MinerU 队列前方: {task.queue_position} 任务</span> : null}
            {elapsed ? <span><Clock3 className="h-4 w-4" />已耗时: {elapsed}</span> : null}
            {totalPages > 0 ? (
              <span className="strong"><FileText className="h-4 w-4" />已完成 {processedPages}/{totalPages} 页，还剩 {remainingPages} 页</span>
            ) : null}
            {task.upstream_task_id ? <span><Activity className="h-4 w-4" />上游任务: {task.upstream_task_id}</span> : null}
          </div>
        </div>
      </div>

      <div className="doc-panel">
        <div className="doc-panel-head">
          <div>
            <h2>处理日志</h2>
            <p>解析过程中的关键状态会持续刷新。</p>
          </div>
        </div>
        <div className="doc-log-box" aria-live="polite">
          {visibleLogs.length ? visibleLogs.map((log, index) => (
            <div key={`${log.id || index}-${log.time}`} className={`doc-log-line ${log.level || 'info'}`}>
              <span>{String(log.time || '').slice(11, 19) || '--:--:--'}</span>
              <p>{log.message}</p>
            </div>
          )) : (
            <div className="doc-log-line info">
              <span>--:--:--</span>
              <p>等待任务日志...</p>
            </div>
          )}
        </div>
      </div>
          </section>
        </div>
      ) : (
        <div className="doc-panel-body">
          <p className="text-sm text-text-muted">选择一个任务后查看解析进度。</p>
        </div>
      )}
    </details>
  )
}
