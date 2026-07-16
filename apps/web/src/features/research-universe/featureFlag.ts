function truthy(value: unknown) {
  return ['1', 'true', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase())
}

export interface MultiMarketFeatureFlagSources {
  envValue?: unknown
  runtimeValue?: unknown
}

export function resolveMultiMarketResearchEnabled(sources: MultiMarketFeatureFlagSources) {
  if (sources.envValue !== undefined) return truthy(sources.envValue)
  if (sources.runtimeValue !== undefined) return truthy(sources.runtimeValue)
  return false
}

export function isMultiMarketResearchEnabled() {
  const metaEnv = (import.meta as unknown as { env?: Record<string, unknown> }).env
  const runtimeConfig = (globalThis as typeof globalThis & {
    __SIQ_CONFIG__?: Record<string, unknown>
  }).__SIQ_CONFIG__
  return resolveMultiMarketResearchEnabled({
    envValue: metaEnv?.VITE_SIQ_MULTI_MARKET_RESEARCH_ENABLED ?? metaEnv?.SIQ_MULTI_MARKET_RESEARCH_ENABLED,
    runtimeValue: runtimeConfig?.SIQ_MULTI_MARKET_RESEARCH_ENABLED
      ?? runtimeConfig?.VITE_SIQ_MULTI_MARKET_RESEARCH_ENABLED,
  })
}
