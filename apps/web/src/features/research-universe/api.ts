import { apiBlob, apiJson, apiText } from '@/shared/api/client'
import type { DisclosureMarketCode } from '@/lib/marketMetadata'
import type {
  ArtifactScope,
  DeleteArtifactResponse,
  GeneratedArtifactsResponse,
  ResearchAgentType,
  ResearchCompaniesResponse,
  ResearchMarketsResponse,
  SourceReportsResponse,
} from './types'

const API_ROOT = '/api/research-universe'

function queryString(params: Record<string, string | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value)
  }
  return query.toString()
}

function artifactEndpoint(artifactId: string) {
  return `${API_ROOT}/artifacts/${encodeURIComponent(artifactId)}`
}

function artifactScopeQuery(scope?: ArtifactScope) {
  if (!scope) return ''
  return queryString({
    market: scope.market,
    company_key: scope.companyKey,
    report_id: scope.reportId,
    artifact_type: scope.artifactType,
  })
}

export function artifactContentUrl(artifactId: string, scope?: ArtifactScope) {
  const query = artifactScopeQuery(scope)
  return `${artifactEndpoint(artifactId)}/content${query ? `?${query}` : ''}`
}

export async function fetchResearchMarkets(agentType: ResearchAgentType, signal?: AbortSignal) {
  return apiJson<ResearchMarketsResponse>(
    `${API_ROOT}/markets?${queryString({ agent_type: agentType })}`,
    { signal },
  )
}

export async function fetchResearchCompanies(
  market: DisclosureMarketCode,
  agentType: ResearchAgentType,
  signal?: AbortSignal,
) {
  return apiJson<ResearchCompaniesResponse>(
    `${API_ROOT}/companies?${queryString({ market, agent_type: agentType })}`,
    { signal },
  )
}

export async function fetchSourceReports(
  market: DisclosureMarketCode,
  companyKey: string,
  agentType: ResearchAgentType,
  signal?: AbortSignal,
  options: { deferArtifactIntegrity?: boolean } = {},
) {
  return apiJson<SourceReportsResponse>(
    `${API_ROOT}/companies/${encodeURIComponent(companyKey)}/reports?${queryString({
      market,
      agent_type: agentType,
      defer_artifact_integrity: options.deferArtifactIntegrity ? 'true' : undefined,
    })}`,
    { signal },
  )
}

export async function fetchGeneratedArtifacts(
  market: DisclosureMarketCode,
  companyKey: string,
  reportId: string,
  artifactType: Exclude<ResearchAgentType, 'legal'>,
  signal?: AbortSignal,
  options: {
    limit?: number
    cursor?: string
    requestedArtifactId?: string
    legacyFilename?: string
  } = {},
) {
  return apiJson<GeneratedArtifactsResponse>(
    `${API_ROOT}/companies/${encodeURIComponent(companyKey)}/artifacts?${queryString({
      market,
      artifact_type: artifactType,
      report_id: reportId,
      limit: options.limit ? String(options.limit) : undefined,
      cursor: options.cursor,
      requested_artifact_id: options.requestedArtifactId,
      legacy_filename: options.legacyFilename,
    })}`,
    { signal },
  )
}

export function fetchArtifactContent(artifactId: string, scope?: ArtifactScope, signal?: AbortSignal) {
  return apiText(artifactContentUrl(artifactId, scope), { signal })
}

export function downloadArtifactContent(artifactId: string, scope?: ArtifactScope, signal?: AbortSignal) {
  return apiBlob(artifactContentUrl(artifactId, scope), { signal })
}

export function deleteGeneratedArtifact(artifactId: string, scope?: ArtifactScope) {
  const query = artifactScopeQuery(scope)
  return apiJson<DeleteArtifactResponse>(`${artifactEndpoint(artifactId)}${query ? `?${query}` : ''}`, { method: 'DELETE' })
}
