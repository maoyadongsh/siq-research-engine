import { apiJson, type ApiRequestInit } from '../../shared/api/client'
import { PDF_API } from '../../lib/pdfApi'
import type { PageContent } from '../../lib/pdfTypes'

export {
  PDF_API,
  artifactDownloadName,
  artifactDownloadUrl,
  artifactUrl,
  cancelTaskApi,
  checkHealth,
  deleteTaskApi,
  downloadedReportToFile,
  fetchFinancialApi,
  fetchPageSourceApi,
  fetchQualityApi,
  fetchResultApi,
  fetchStatus,
  fetchWorkflowJobApi,
  getDownloadUrl,
  getPdfUrl,
  linkDownloadedReport,
  loadDownloadedReports,
  loadTasks,
  loadWorkflowStatusApi,
  pipelineArtifactSummary,
  readJsonResponse,
  refetchTaskApi,
  reparseTaskApi,
  runRemainingWorkflowApi,
  runWorkflowStepApi,
  saveCorrectionApi,
  showTableSourceApi,
  uploadPdfs,
  workflowReady,
} from '../../lib/pdfApi'

export function fetchPdfArtifactJson<T>(url: string, init: ApiRequestInit = {}): Promise<T> {
  return apiJson<T>(url, init)
}

export function fetchPdfPageContentApi(
  taskId: string,
  pageNum: number,
  focusTableIndex = 0,
  init: ApiRequestInit = {},
): Promise<PageContent> {
  return apiJson<PageContent>(
    `${PDF_API}/source/${encodeURIComponent(taskId)}/page/${encodeURIComponent(pageNum)}?focus_table=${encodeURIComponent(String(focusTableIndex || ''))}`,
    init,
  )
}
