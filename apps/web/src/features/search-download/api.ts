import { apiJson } from '../../shared/api/client'

export async function fetchMarketReportHealth<T = Record<string, unknown>>(): Promise<T> {
  return apiJson<T>('/api/market-report-health')
}

export async function requestReportAssist<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/reports/assist', {
    method: 'POST',
    body: payload,
  })
}

export async function resolveCompany<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/company/resolve', {
    method: 'POST',
    body: payload,
  })
}

export async function fetchRecentReports<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/reports/recent', {
    method: 'POST',
    body: payload,
  })
}

export async function fetchCuratedAnnuals<T = Record<string, unknown>>(params: URLSearchParams): Promise<T> {
  return apiJson<T>(`/api/v1/reports/curated-annuals?${params.toString()}`)
}

export async function batchDownloadReports<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/reports/batch-download', {
    method: 'POST',
    body: payload,
  })
}

export async function downloadReport<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/reports/download', {
    method: 'POST',
    body: payload,
  })
}

export async function selectDownloadReports<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/v1/reports/select-download', {
    method: 'POST',
    body: payload,
  })
}

export async function linkWorkspaceDownload<T = Record<string, unknown>>(payload: Record<string, unknown>): Promise<T> {
  return apiJson<T>('/api/workspace/downloads/link', {
    method: 'POST',
    body: payload,
  })
}

export async function deleteDownloadedReport(path: string): Promise<void> {
  await apiJson(`/api/downloads/report-file?path=${encodeURIComponent(path)}`, {
    method: 'DELETE',
  })
}
