import type { HealthStatus } from '../../lib/pdfTypes'

export interface PdfHealthStripProps {
  health: HealthStatus | null
}

function serviceStatusClass(available?: boolean, checked = false) {
  if (!checked) return 'secondary-status-info'
  return available ? 'secondary-status-success' : 'secondary-status-error'
}

function serviceStatusText(label: string, available?: boolean, checked = false) {
  if (!checked) return `${label} · 检测中`
  return `${label} · ${available ? '可用' : '不可用'}`
}

export function PdfHealthStrip({ health }: PdfHealthStripProps) {
  const checked = Boolean(health)

  return (
    <>
      <div className="pdf-health-strip">
        <span className="pdf-health-label">解析服务</span>
        <div className={`secondary-status ${serviceStatusClass(health?.mineru, checked)}`}>
          {serviceStatusText('标准解析', health?.mineru, checked)}
        </div>
        <div className={`secondary-status ${serviceStatusClass(health?.vlm, checked)}`}>
          {serviceStatusText('增强解析', health?.vlm, checked)}
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
