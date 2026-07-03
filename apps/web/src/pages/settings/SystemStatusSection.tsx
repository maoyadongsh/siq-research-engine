import { AlertTriangle, CheckCircle2, Loader2, PowerOff, RefreshCw, XCircle } from 'lucide-react'
import { MetricCard } from '@/components/research/MetricCard'
import { formatCheckedAt } from './utils'
import type { ProviderFormData, ServiceStatus, ServiceCounts, SystemStatus } from './types'

interface SystemStatusSectionProps {
  systemStatus: SystemStatus | null
  loadingSystemStatus: boolean
  systemStatusError: string
  loadSystemStatus: () => void
  counts: ServiceCounts
  activeProvider: ProviderFormData
}

export function SystemStatusSection({
  systemStatus,
  loadingSystemStatus,
  systemStatusError,
  loadSystemStatus,
  counts,
  activeProvider,
}: SystemStatusSectionProps) {
  const serviceStatus = (service: ServiceStatus) => {
    const disabled = service.enabled === false || service.status === 'disabled'
    const statusIcon = disabled ? (
      <PowerOff className="h-5 w-5" />
    ) : service.ok ? (
      <CheckCircle2 className="h-5 w-5" />
    ) : (
      <XCircle className="h-5 w-5" />
    )
    const statusTone = disabled
      ? 'bg-bg text-text-muted'
      : service.ok
        ? 'bg-success/10 text-success'
        : 'bg-error/10 text-error'
    const statusText = disabled ? '未启用' : service.ok ? '运行中' : '不可用'
    const statusTextTone = disabled ? 'text-text-muted' : service.ok ? 'text-success' : 'text-error'

    return (
      <div
        key={service.id}
        className="rounded-[var(--radius-card)] border border-border bg-card p-4 shadow-sm"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold text-text">{service.name}</h3>
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  service.required ? 'bg-primary/10 text-primary' : 'bg-bg text-text-muted'
                }`}
              >
                {service.required ? '核心' : '可选'}
              </span>
            </div>
            <p
              className="mt-1 truncate font-mono text-xs text-text-muted"
              title={service.url}
            >
              {service.url}
            </p>
          </div>
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${statusTone}`}
          >
            {statusIcon}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-text-muted">
          <span className={`font-semibold ${statusTextTone}`}>{statusText}</span>
          <span>{disabled ? '按需启动' : service.statusCode ? `HTTP ${service.statusCode}` : '无响应'}</span>
          {!disabled ? <span>{service.latencyMs}ms</span> : null}
        </div>
      </div>
    )
  }

  return (
    <section className="apple-card rounded-[var(--radius-card)] p-4 sm:rounded-[var(--radius-panel)] sm:p-6">
      <div className="mb-5 flex flex-col gap-4 sm:mb-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div
            className={`flex h-11 w-11 items-center justify-center rounded-2xl sm:h-12 sm:w-12 ${
              systemStatus?.status === 'ok'
                ? 'bg-success/10 text-success'
                : 'bg-warning/10 text-warning'
            }`}
          >
            {loadingSystemStatus ? (
              <Loader2 className="h-6 w-6 animate-spin" />
            ) : systemStatus?.status === 'ok' ? (
              <CheckCircle2 className="h-6 w-6" />
            ) : (
              <AlertTriangle className="h-6 w-6" />
            )}
          </div>
          <div>
            <h2 className="text-base font-semibold text-text sm:text-lg">系统状态</h2>
            <p className="text-sm text-text-muted sm:text-base">
              {systemStatus
                ? `上次检查 ${formatCheckedAt(systemStatus.checkedAt)}，${counts.ok}/${counts.total} 个服务可用`
                : '检查 SIQ 关联服务、Wiki 数据和模型配置。'}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={loadSystemStatus}
          disabled={loadingSystemStatus}
          className="flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loadingSystemStatus ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          刷新状态
        </button>
      </div>

      {systemStatusError ? (
        <div className="rounded-[var(--radius-card)] border border-error/20 bg-error/5 p-5 text-base text-error">
          {systemStatusError}
        </div>
      ) : (
        <div className="space-y-5">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3">
            <MetricCard
              label="服务总览"
              value={loadingSystemStatus && !systemStatus ? '--' : `${counts.ok}/${counts.total}`}
              status={counts.requiredDown > 0 ? 'warning' : 'success'}
              trend={
                counts.requiredDown > 0
                  ? `${counts.requiredDown} 个核心服务异常`
                  : counts.disabled > 0
                    ? `${counts.disabled} 个 IC Hermes 网关未启用`
                  : '核心服务状态良好'
              }
            />
            <MetricCard
              label="Wiki 数据"
              value={systemStatus?.wiki.exists ? systemStatus.wiki.companyCount : '--'}
              trend={
                systemStatus?.wiki.exists
                  ? `${systemStatus.wiki.generatedResultCount} 个已生成结果`
                  : '目录不可用'
              }
            />
            <MetricCard
              label="Hermes 调用模型"
              value={systemStatus?.model.activeModel || activeProvider.model || '未填写模型'}
              trend="保存设置后会同步到 SIQ Hermes profiles。"
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {(systemStatus?.services || []).map(serviceStatus)}
          </div>
        </div>
      )}
    </section>
  )
}
