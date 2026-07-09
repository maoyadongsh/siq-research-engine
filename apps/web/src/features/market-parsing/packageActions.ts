import {
  runMarketDocumentFullImport,
  runMarketPackageBuild,
  runMarketPackageImport,
  runMarketPackageVectorIngest,
  waitForMarketReportJob,
  type MarketDocumentFullImportResponse,
  type MarketCode,
  type MarketPackageActionResponse,
  type MarketPackageBuildRequest,
} from './api'

export interface MarketPackageBuildActionInput {
  market: MarketCode
  sourcePath: string
  parserResult?: string
  metadataPath?: string
  force?: boolean
}

export interface MarketPackagePathActionInput {
  market: MarketCode
  packagePath: string
}

export interface MarketPackageImportActionInput extends MarketPackagePathActionInput {
  documentFullPath?: string
  ddl?: boolean
  force?: boolean
}

export interface MarketDocumentFullImportActionInput {
  market: MarketCode
  documentFullPath: string
  ddl?: boolean
  force?: boolean
}

export interface MarketPackageVectorActionInput extends MarketPackagePathActionInput {
  dryRun?: boolean
  force?: boolean
}

export type MarketPackageJobWaiter = (
  jobId: string,
  options?: { intervalMs?: number; timeoutMs?: number },
) => Promise<MarketPackageActionResponse>

export interface MarketPackageActionDeps {
  runBuild: typeof runMarketPackageBuild
  runDocumentFullImport: typeof runMarketDocumentFullImport
  runImport: typeof runMarketPackageImport
  runVectorIngest: typeof runMarketPackageVectorIngest
  waitForJob: MarketPackageJobWaiter
}

const defaultDeps: MarketPackageActionDeps = {
  runBuild: runMarketPackageBuild,
  runDocumentFullImport: runMarketDocumentFullImport,
  runImport: runMarketPackageImport,
  runVectorIngest: runMarketPackageVectorIngest,
  waitForJob: (jobId, options) => waitForMarketReportJob<MarketPackageActionResponse>(jobId, options),
}

async function resolveMarketPackageActionResponse(
  response: MarketPackageActionResponse,
  deps: Pick<MarketPackageActionDeps, 'waitForJob'>,
): Promise<MarketPackageActionResponse> {
  return response.job_id
    ? deps.waitForJob(response.job_id)
    : response
}

export function buildMarketPackageRequest({
  sourcePath,
  parserResult,
  metadataPath,
  force = true,
}: Omit<MarketPackageBuildActionInput, 'market'>): MarketPackageBuildRequest {
  return {
    source_path: sourcePath.trim(),
    parser_result: parserResult?.trim() || undefined,
    metadata_path: metadataPath?.trim() || undefined,
    force,
  }
}

export function formatMarketPackageImportOutput(result: MarketPackageActionResponse): string {
  return result.stdout || result.stderr || `parse_run_id=${result.parse_run_id || ''}`
}

export function formatMarketDocumentFullImportOutput(result: MarketDocumentFullImportResponse): string {
  return result.stdout || result.stderr || `parse_run_id=${result.parse_run_id || ''}`
}

export function formatMarketPackageVectorOutput(result: MarketPackageActionResponse): string {
  return JSON.stringify(result.summary || { stdout: result.stdout, stderr: result.stderr }, null, 2)
}

export function formatMarketPackageBuildOutput(result: MarketPackageActionResponse): string {
  return result.stdout || result.stderr || 'package built'
}

export async function runMarketPackageImportAction(
  input: MarketPackageImportActionInput,
  deps: MarketPackageActionDeps = defaultDeps,
): Promise<{ output: string; result: MarketPackageActionResponse }> {
  const documentFullPath = input.documentFullPath?.trim()
  if (documentFullPath) {
    const documentFull = await runMarketDocumentFullImportAction({
      market: input.market,
      documentFullPath,
      ddl: input.ddl,
      force: input.force,
    }, deps)
    return documentFull
  }
  const response = await deps.runImport(input.market, input.packagePath, input.ddl ?? true, input.force ?? false)
  const result = await resolveMarketPackageActionResponse(response, deps)
  return { output: formatMarketPackageImportOutput(result), result }
}

export async function runMarketDocumentFullImportAction(
  input: MarketDocumentFullImportActionInput,
  deps: MarketPackageActionDeps = defaultDeps,
): Promise<{ output: string; result: MarketDocumentFullImportResponse }> {
  const response = await deps.runDocumentFullImport(input.market, input.documentFullPath.trim(), input.ddl ?? true, input.force ?? false)
  const result = await resolveMarketPackageActionResponse(response, deps) as MarketDocumentFullImportResponse
  return { output: formatMarketDocumentFullImportOutput(result), result }
}

export async function runMarketPackageVectorDryRunAction(
  input: MarketPackageVectorActionInput,
  deps: MarketPackageActionDeps = defaultDeps,
): Promise<{ output: string; result: MarketPackageActionResponse }> {
  const response = await deps.runVectorIngest(input.market, input.packagePath, input.dryRun ?? true, input.force ?? false)
  const result = await resolveMarketPackageActionResponse(response, deps)
  return { output: formatMarketPackageVectorOutput(result), result }
}

export async function runMarketPackageBuildAction(
  input: MarketPackageBuildActionInput,
  deps: MarketPackageActionDeps = defaultDeps,
): Promise<{ output: string; builtPath: string; result: MarketPackageActionResponse }> {
  const response = await deps.runBuild(input.market, buildMarketPackageRequest(input))
  const result = await resolveMarketPackageActionResponse(response, deps)
  return {
    output: formatMarketPackageBuildOutput(result),
    builtPath: String(result.package?.package_path || ''),
    result,
  }
}
