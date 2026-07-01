import type { DownloadedPdf } from './model'

export interface DownloadSummary {
  total: number
  succeeded: number
  failed: number
  hasSuccess: boolean
  hasFailure: boolean
}

export interface SearchDownloadToastSpec {
  type: 'success' | 'error' | 'warning'
  title: string
  description: string
}

export function summarizeDownloadResults(results: Array<{ success?: boolean }>): DownloadSummary {
  const total = results.length
  const succeeded = results.reduce((count, result) => count + (result.success !== false ? 1 : 0), 0)
  const failed = total - succeeded

  return {
    total,
    succeeded,
    failed,
    hasSuccess: succeeded > 0,
    hasFailure: failed > 0,
  }
}

export function shouldRefreshDownloadedReports(results: Array<{ success?: boolean }>) {
  return summarizeDownloadResults(results).hasSuccess
}

export function buildDownloadedReportDeleteToast(report: DownloadedPdf): SearchDownloadToastSpec {
  return {
    type: 'success',
    title: '文件已删除',
    description: report.filename,
  }
}

export function buildDownloadedReportDeleteFailureToast(): SearchDownloadToastSpec {
  return {
    type: 'error',
    title: '删除失败',
    description: '请确认后端服务可用，且文件仍在 downloads 目录内。',
  }
}

export function buildDownloadedReportOpenFailureToast(): SearchDownloadToastSpec {
  return {
    type: 'error',
    title: '打开失败',
    description: '请确认登录状态有效，且文件仍在 downloads 目录内。',
  }
}
