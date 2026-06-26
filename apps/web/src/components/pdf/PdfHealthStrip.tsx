import type { HealthStatus } from '../../lib/pdfTypes'

export interface PdfHealthStripProps {
  health: HealthStatus | null
}

export function PdfHealthStrip({ health }: PdfHealthStripProps) {
  return (
    <>
      <div className="pdf-health-strip">
        <span className="pdf-health-label">解析服务</span>
        <div className={`secondary-status ${health?.mineru ? 'secondary-status-success' : health ? 'secondary-status-error' : ''}`}>
          MinerU
        </div>
        <div className={`secondary-status ${health?.vlm ? 'secondary-status-success' : health ? 'secondary-status-error' : ''}`}>
          VLM
        </div>
      </div>
      {health?.warning && (
        <div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-sm text-warning">{health.warning}</div>
      )}
      {health && !health.submit_ready && (
        <div className="rounded-lg border border-warning/20 bg-warning/5 px-4 py-3 text-sm text-warning">服务暂未就绪，无法提交新任务</div>
      )}
    </>
  )
}
