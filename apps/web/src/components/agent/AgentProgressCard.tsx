import { Activity, AlertTriangle, CheckCircle2, Loader2, Square } from 'lucide-react'
import type { AgentProgress } from '../../lib/useAgentChat'

interface AgentProgressCardProps {
  progress?: AgentProgress
  compact?: boolean
}

function clampPercent(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) return undefined
  return Math.max(0, Math.min(100, Math.round(value)))
}

function statusMeta(status?: AgentProgress['status']) {
  if (status === 'completed') return { label: '完成', className: 'text-success', Icon: CheckCircle2 }
  if (status === 'error') return { label: '异常', className: 'text-error', Icon: AlertTriangle }
  if (status === 'stopped') return { label: '已停止', className: 'text-warning', Icon: Square }
  if (status === 'queued') return { label: '排队中', className: 'text-text-muted', Icon: Activity }
  return { label: '执行中', className: 'text-primary', Icon: Loader2 }
}

export default function AgentProgressCard({ progress, compact = false }: AgentProgressCardProps) {
  if (!progress) return null

  const percent = clampPercent(progress.percent)
  const meta = statusMeta(progress.status)
  const Icon = meta.Icon
  const detail = progress.detail?.replace(/\s+/g, ' ').trim()
  const showPercent = typeof percent === 'number'
  const running = progress.status === 'running' || progress.status === 'queued'
  const progressValue = showPercent ? percent : running ? 42 : 100

  return (
    <div className={`agent-progress-card ${compact ? 'agent-progress-card-compact' : ''}`} aria-live="polite">
      <div className="agent-progress-head">
        <div className="agent-progress-title-wrap">
          <span className={`agent-progress-icon ${meta.className}`}>
            <Icon className={running && Icon === Loader2 ? 'animate-spin' : ''} />
          </span>
          <div className="min-w-0">
            <div className="agent-progress-title">{progress.title || '正在执行任务'}</div>
            <div className={`agent-progress-status ${meta.className}`}>{meta.label}</div>
          </div>
        </div>
        {showPercent && <span className="agent-progress-percent tabular-nums">{percent}%</span>}
      </div>

      <div
        className="agent-progress-track"
        role="progressbar"
        aria-label={progress.title || '智能体任务进度'}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progressValue}
      >
        <div className="agent-progress-fill" style={{ width: `${progressValue}%` }} />
      </div>

      {(detail || progress.current || progress.total || progress.tool) && (
        <div className="agent-progress-meta">
          {detail && <span className="agent-progress-detail">{detail}</span>}
          {progress.current !== undefined && progress.total !== undefined && (
            <span className="agent-progress-step tabular-nums">{progress.current}/{progress.total}</span>
          )}
          {progress.tool && <span className="agent-progress-tool">{progress.tool}</span>}
        </div>
      )}
    </div>
  )
}
