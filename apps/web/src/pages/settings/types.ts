import type { LucideIcon } from 'lucide-react'

export type ProviderKey = 'cloud' | 'local'

export type ProviderFormData = {
  enabled: boolean
  providerName: string
  baseUrl: string
  apiKey: string
  hasApiKey: boolean
  clearApiKey: boolean
  model: string
  temperature: string
  maxTokens: string
  timeoutSeconds: string
}

export type LLMSettingsForm = {
  activeProvider: ProviderKey
  providers: Record<ProviderKey, ProviderFormData>
}

export type TestState = {
  status: 'idle' | 'testing' | 'success' | 'error'
  message: string
  latencyMs?: number
}

export type ServiceStatus = {
  id: string
  name: string
  category: string
  url: string
  required: boolean
  enabled?: boolean
  ok: boolean
  status?: 'running' | 'unavailable' | 'disabled' | string
  statusCode: number | null
  latencyMs: number
  detail: unknown
}

export type HermesProfileStatus = {
  profile: string
  label: string
  mode: string
  modeLabel: string
  kind: string
  model: string
  provider: string
  baseUrl: string
  fallbackModels: string[]
}

export type SystemStatus = {
  status: 'ok' | 'degraded'
  checkedAt: string
  wiki: {
    root: string
    companiesDir: string
    exists: boolean
    companyCount: number
    generatedResultCount: number
  }
  model: {
    activeProvider: ProviderKey
    activeProviderName: string
    activeModel: string
    activeBaseUrl: string
    note: string
    hermesProfiles?: Record<string, HermesProfileStatus>
  }
  services: ServiceStatus[]
}

export type ProviderMeta = {
  title: string
  desc: string
  icon: LucideIcon
  iconClass: string
  panelClass: string
}

export type ServiceCounts = {
  total: number
  disabled: number
  ok: number
  requiredDown: number
}
