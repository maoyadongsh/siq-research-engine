import { useCallback, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { WorkflowJob, WorkflowStatus } from '../../lib/pdfTypes'
import {
  fetchWorkflowJobApi,
  loadWorkflowStatusApi,
  runMarketDocumentFullWorkflowImportApi,
  runRemainingWorkflowApi,
  runWorkflowStepApi,
} from '../../features/pdf-parsing/api'
import { waitForMarketReportJob, type MarketCode } from '../../features/market-parsing/api'
import { createTaskRequestScope } from './taskRequestScope'

type WorkflowStep = 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import'
type WorkflowMode = 'standard' | 'generic'
type PdfDocumentFullMarket = Exclude<MarketCode, 'US'>

function isPdfDocumentFullMarket(market?: string | null): market is PdfDocumentFullMarket {
  return market === 'HK' || market === 'JP' || market === 'KR' || market === 'EU'
}

export function usePdfWorkflow(
  taskIdRef: MutableRefObject<string | null>,
  showToast: (msg: string) => void,
  reportError: (msg: string | null) => void,
  market?: string | null,
) {
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus | null>(null)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [workflowBusy, setWorkflowBusy] = useState('')
  const [workflowJob, setWorkflowJob] = useState<WorkflowJob | null>(null)
  const [workflowError, setWorkflowError] = useState('')
  const [statusRequestScope] = useState(createTaskRequestScope)
  const [actionRequestScope] = useState(createTaskRequestScope)
  const [jobRequestScope] = useState(createTaskRequestScope)

  const loadWorkflowStatus = useCallback(async (requestedTaskId?: string | null) => {
    const tid = requestedTaskId || taskIdRef.current
    if (!tid) return
    const request = statusRequestScope.begin(tid)
    setWorkflowLoading(true)
    setWorkflowError('')
    try {
      const status = await loadWorkflowStatusApi(tid)
      if (statusRequestScope.isCurrent(request, taskIdRef.current)) setWorkflowStatus(status)
    } catch (e) {
      const message = e instanceof Error ? e.message : '工作流状态查询失败'
      if (statusRequestScope.isCurrent(request, taskIdRef.current)) setWorkflowError(message)
    } finally {
      if (statusRequestScope.isCurrent(request, taskIdRef.current)) setWorkflowLoading(false)
    }
  }, [statusRequestScope, taskIdRef])

  const watchJob = useCallback(
    async (jobId?: string, requestedTaskId?: string | null) => {
      const tid = requestedTaskId || taskIdRef.current
      if (!tid) return null
      if (!jobId) {
        await loadWorkflowStatus(tid)
        return null
      }
      const request = jobRequestScope.begin(tid)
      let latestJob: WorkflowJob | null = null
      for (let i = 0; i < 900; i += 1) {
        const job = await fetchWorkflowJobApi(jobId)
        if (!jobRequestScope.isCurrent(request, taskIdRef.current)) return null
        latestJob = job
        setWorkflowJob(job)
        if (['completed', 'succeeded', 'failed', 'error'].includes(String(job.status))) break
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        if (!jobRequestScope.isCurrent(request, taskIdRef.current)) return null
      }
      if (jobRequestScope.isCurrent(request, taskIdRef.current)) await loadWorkflowStatus(tid)
      return latestJob
    },
    [jobRequestScope, loadWorkflowStatus, taskIdRef],
  )

  const runMarketDocumentFullImport = useCallback(
    async (tid: string, marketCode: PdfDocumentFullMarket) => {
      const response = await runMarketDocumentFullWorkflowImportApi(marketCode, tid)
      const result = response.job_id
        ? await waitForMarketReportJob(response.job_id, { timeoutMs: 15 * 60 * 1000 })
        : response
      const job: WorkflowJob = {
        status: result.ok === false ? 'failed' : 'succeeded',
        retryScope: 'db-import',
        currentStep: 'db-import',
        steps: [{
          step: 'db-import',
          status: result.ok === false ? 'failed' : 'succeeded',
          stdoutTail: String(result.stdout || ''),
          stderrTail: String(result.stderr || ''),
          commandResults: [{
            stage: 'document_full_import',
            stdoutTail: String(result.stdout || ''),
            stderrTail: String(result.stderr || ''),
          }],
        }],
        error: result.ok === false ? String(result.stderr || result.stdout || 'document_full 入库失败') : undefined,
      }
      if (result.ok === false) {
        throw new Error(String(result.stderr || result.stdout || 'document_full 入库失败'))
      }
      return job
    },
    [],
  )

  const runWorkflowStep = useCallback(
    async (step: WorkflowStep) => {
      const tid = taskIdRef.current
      if (!tid) return
      const request = actionRequestScope.begin(tid)
      jobRequestScope.invalidate()
      statusRequestScope.invalidate()
      setWorkflowBusy(step)
      setWorkflowError('')
      reportError(null)
      try {
        if (step === 'db-import' && isPdfDocumentFullMarket(market)) {
          const job = await runMarketDocumentFullImport(tid, market)
          if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
          setWorkflowJob(job)
          showToast('document_full PostgreSQL 入库完成')
          await loadWorkflowStatus(tid)
          if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
        } else {
          const job = await runWorkflowStepApi(tid, step)
          if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
          setWorkflowJob(job)
          showToast('工作流步骤已启动')
          await watchJob(job.jobId, tid)
          if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
        }
      } catch (e) {
        if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
        const message = e instanceof Error ? e.message : '工作流步骤执行失败'
        setWorkflowError(message)
        reportError(message)
      } finally {
        if (actionRequestScope.isCurrent(request, taskIdRef.current)) setWorkflowBusy('')
      }
    },
    [actionRequestScope, jobRequestScope, loadWorkflowStatus, market, reportError, runMarketDocumentFullImport, showToast, statusRequestScope, taskIdRef, watchJob],
  )

  const runRemainingWorkflow = useCallback(async (mode: WorkflowMode = 'standard') => {
    const tid = taskIdRef.current
    if (!tid) return
    const request = actionRequestScope.begin(tid)
    jobRequestScope.invalidate()
    statusRequestScope.invalidate()
    setWorkflowBusy('remaining')
    setWorkflowError('')
    reportError(null)
    try {
      if (mode === 'generic') {
        const steps: WorkflowStep[] = ['wiki-import-generic', 'semantic-generic', 'db-import']
        const completedSteps: NonNullable<WorkflowJob['steps']> = []
        for (const step of steps) {
          setWorkflowBusy(step)
          if (step === 'db-import' && isPdfDocumentFullMarket(market)) {
            const job = await runMarketDocumentFullImport(tid, market)
            if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
            completedSteps.push(...(job.steps?.length ? job.steps : [{ step, status: 'succeeded' }]))
            setWorkflowJob({ ...job, status: 'running', currentStep: step, retryScope: 'remaining', steps: [...completedSteps] })
            await loadWorkflowStatus(tid)
            if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
          } else {
            const result = await runWorkflowStepApi(tid, step)
            if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
            setWorkflowJob({ status: 'running', currentStep: step, retryScope: 'remaining', steps: [...completedSteps, { step, status: 'running' }] })
            let watchedJob: WorkflowJob | null = null
            if (result.jobId) {
              watchedJob = await watchJob(result.jobId, tid)
            } else {
              await loadWorkflowStatus(tid)
            }
            if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
            const finalStep = watchedJob?.steps?.find((item) => item.step === step)
            completedSteps.push(finalStep || { step, status: watchedJob?.status === 'failed' ? 'failed' : 'succeeded', error: watchedJob?.error })
            if (watchedJob?.status === 'failed') throw new Error(watchedJob.error || finalStep?.error || '工作流步骤执行失败')
            if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
          }
        }
        if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
        setWorkflowJob({ status: 'succeeded', steps: [...completedSteps] })
        showToast('境外市场数据管线已完成')
      } else {
        const job = await runRemainingWorkflowApi(tid)
        if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
        setWorkflowJob(job)
        showToast('剩余工作流已启动')
        await watchJob(job.jobId, tid)
        if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
      }
    } catch (e) {
      if (!actionRequestScope.isCurrent(request, taskIdRef.current)) return
      const message = e instanceof Error ? e.message : '工作流执行失败'
      setWorkflowError(message)
      reportError(message)
    } finally {
      if (actionRequestScope.isCurrent(request, taskIdRef.current)) setWorkflowBusy('')
    }
  }, [actionRequestScope, jobRequestScope, loadWorkflowStatus, market, reportError, runMarketDocumentFullImport, showToast, statusRequestScope, taskIdRef, watchJob])

  return {
    workflowStatus,
    workflowLoading,
    workflowBusy,
    workflowJob,
    workflowError,
    loadWorkflowStatus,
    runWorkflowStep,
    runRemainingWorkflow,
  }
}
