import type { ProviderFormData, ProviderKey, ProviderMeta, TestState } from './types'

interface ProviderCardProps {
  providerKey: ProviderKey
  provider: ProviderFormData
  meta: ProviderMeta
  state: TestState
  isActive: boolean
  onSelect: (key: ProviderKey) => void
}

export function ProviderCard({
  providerKey,
  provider,
  meta,
  state,
  isActive,
  onSelect,
}: ProviderCardProps) {
  const Icon = meta.icon
  const statusLabel =
    state.status === 'success'
      ? '连接正常'
      : state.status === 'error'
        ? '需检查'
        : state.status === 'testing'
          ? '测试中'
          : '未测试'
  const statusClass =
    state.status === 'success'
      ? 'text-success bg-success/10'
      : state.status === 'error'
        ? 'text-error bg-error/10'
        : 'text-text-muted bg-bg'

  return (
    <button
      key={providerKey}
      type="button"
      onClick={() => onSelect(providerKey)}
      className={`lift-card flex h-full min-h-[132px] w-full rounded-[var(--radius-card)] border p-4 text-left sm:min-h-[190px] sm:p-5 ${
        isActive
          ? 'border-primary bg-primary/5 shadow-lg shadow-blue-900/10'
          : 'border-border bg-card hover:bg-primary/[0.03]'
      }`}
    >
      <div className="flex w-full items-start gap-4">
        <div
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl ${meta.iconClass}`}
        >
          <Icon className="h-6 w-6" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-text">{meta.title}</h3>
            {isActive && (
              <span className="rounded-full accent-gradient px-2.5 py-1 text-xs font-semibold text-white shadow-sm">
                当前调用源
              </span>
            )}
            {!provider.enabled && (
              <span className="rounded-full bg-bg px-2.5 py-1 text-sm font-semibold text-text-muted">
                未启用
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-sm leading-6 text-text-muted">{meta.desc}</p>
          <div className="mt-auto grid grid-cols-2 gap-2 pt-3 text-sm sm:pt-4">
            <div className="rounded-xl border border-border bg-card/80 px-3 py-2">
              <span className="block text-sm font-semibold text-text-muted">模型</span>
              <span className="mt-0.5 block truncate font-semibold text-text">
                {provider.model || '未填写'}
              </span>
            </div>
            <div className="rounded-xl border border-border bg-card/80 px-3 py-2">
              <span className="block text-sm font-semibold text-text-muted">连接</span>
              <span
                className={`mt-0.5 inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${statusClass}`}
              >
                {statusLabel}
              </span>
            </div>
          </div>
        </div>
      </div>
    </button>
  )
}
