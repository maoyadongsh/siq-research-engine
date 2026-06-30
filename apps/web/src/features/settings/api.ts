import { apiJson } from '../../shared/api/client'
import type { ProviderFormData, ProviderKey, SystemStatus } from '../../pages/settings/types'

export interface LlmSettingsApiResponse {
  activeProvider?: ProviderKey
  providers?: {
    cloud?: Partial<ProviderFormData>
    local?: Partial<ProviderFormData>
  }
}

export interface LlmProviderPayload {
  enabled: boolean
  providerName: string
  baseUrl: string
  apiKey: string | null
  clearApiKey: boolean
  model: string
  temperature: number
  maxTokens: number
  timeoutSeconds: number
}

export interface SaveLlmSettingsRequest {
  activeProvider: ProviderKey
  providers: Record<ProviderKey, LlmProviderPayload>
}

export interface TestLlmProviderRequest {
  provider: ProviderKey
  message: string
  config: LlmProviderPayload
}

export interface TestLlmProviderResponse {
  ok?: boolean
  message?: string
  latencyMs?: number
}

export async function fetchLlmSettings(apiUrl: (path: string) => string): Promise<LlmSettingsApiResponse> {
  return apiJson<LlmSettingsApiResponse>(apiUrl('/api/settings/llm'))
}

export async function saveLlmSettings(apiUrl: (path: string) => string, body: SaveLlmSettingsRequest): Promise<LlmSettingsApiResponse> {
  return apiJson<LlmSettingsApiResponse>(apiUrl('/api/settings/llm'), {
    method: 'PUT',
    body,
  })
}

export async function testLlmProvider(apiUrl: (path: string) => string, body: TestLlmProviderRequest): Promise<TestLlmProviderResponse> {
  return apiJson<TestLlmProviderResponse>(apiUrl('/api/settings/llm/test'), {
    method: 'POST',
    body,
  })
}

export async function fetchSystemStatus(apiUrl: (path: string) => string): Promise<SystemStatus> {
  return apiJson<SystemStatus>(apiUrl('/api/system/status'))
}
