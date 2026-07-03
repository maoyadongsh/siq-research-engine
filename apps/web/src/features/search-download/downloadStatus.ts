import type { DownloadedPdf, ReportItem } from './model'

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

export interface SearchDownloadLogSpec {
  type: 'success' | 'error' | 'warn'
  message: string
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

export function buildBatchDownloadCompleteLog(result: { succeeded: number; failed: number }): SearchDownloadLogSpec {
  return {
    type: 'success',
    message: `下载完成: 成功 ${result.succeeded}, 失败 ${result.failed}`,
  }
}

export function buildBatchDownloadFallbackLog(error: Error): SearchDownloadLogSpec {
  return {
    type: 'warn',
    message: `批量下载失败: ${error.message}, 尝试逐个下载...`,
  }
}

export function buildIndividualDownloadLogs(results: Array<{ report: ReportItem; success: boolean }>): SearchDownloadLogSpec[] {
  return results.map((result) => ({
    type: result.success ? 'success' : 'error',
    message: `${result.success ? '下载成功' : '下载失败'}: ${result.report.title}`,
  }))
}

export function buildAllDownloadsFinishedLog(): SearchDownloadLogSpec {
  return {
    type: 'success',
    message: '全部下载任务完成',
  }
}

export function buildQuickDownloadCompleteLog(result: { companyName: string; succeeded: number; total: number }): SearchDownloadLogSpec {
  return {
    type: 'success',
    message: `下载完成: ${result.companyName} 成功 ${result.succeeded}/${result.total}`,
  }
}

export function buildQuickDownloadFailureLog(error: Error): SearchDownloadLogSpec {
  return {
    type: 'error',
    message: `下载失败: ${error.message}`,
  }
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
