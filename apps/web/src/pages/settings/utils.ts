import type { ProviderFormData, ProviderKey, ServiceCounts, ServiceStatus } from './types'

export function readSetting(key: string, fallback: string) {
  if (typeof window === 'undefined') return fallback
  return localStorage.getItem(key) ?? fallback
}

export function normalizeNumber(value: string, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function formatCheckedAt(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return ''
  return time.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function mapProviderFromApi(
  provider: Partial<ProviderFormData> & Record<string, unknown>,
  fallback: ProviderFormData,
): ProviderFormData {
  return {
    enabled: Boolean(provider.enabled ?? fallback.enabled),
    providerName: String(provider.providerName || fallback.providerName),
    baseUrl: String(provider.baseUrl || fallback.baseUrl),
    apiKey: '',
    hasApiKey: Boolean(provider.hasApiKey),
    clearApiKey: false,
    model: String(provider.model || fallback.model),
    temperature: String(provider.temperature ?? fallback.temperature),
    maxTokens: String(provider.maxTokens ?? fallback.maxTokens),
    timeoutSeconds: String(provider.timeoutSeconds ?? fallback.timeoutSeconds),
  }
}

export function isProviderKey(value: unknown): value is ProviderKey {
  return value === 'cloud' || value === 'local'
}

export function countEnabledServices(services: ServiceStatus[] = []): ServiceCounts {
  const enabledServices = services.filter((service) => service.enabled !== false && service.status !== 'disabled')
  return {
    total: enabledServices.length,
    disabled: services.length - enabledServices.length,
    ok: enabledServices.filter((service) => service.ok).length,
    requiredDown: enabledServices.filter((service) => service.required && !service.ok).length,
  }
}
