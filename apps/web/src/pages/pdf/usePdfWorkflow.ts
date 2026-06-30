import { useCallback, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { WorkflowJob, WorkflowStatus } from '../../lib/pdfTypes'
import {
  fetchWorkflowJobApi,
  loadWorkflowStatusApi,
  runRemainingWorkflowApi,
  runWorkflowStepApi,
} from '../../features/pdf-parsing/api'

type WorkflowStep = 'wiki-import' | 'wiki-import-generic' | 'semantic' | 'semantic-generic' | 'db-import'

export function usePdfWorkflow(
  taskIdRef: MutableRefObject<string | null>,
  showToast: (msg: string) => void,
  reportError: (msg: string | null) => void,
) {
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus | null>(null)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [workflowBusy, setWorkflowBusy] = useState('')
  const [workflowJob, setWorkflowJob] = useState<WorkflowJob | null>(null)
  const [workflowError, setWorkflowError] = useState('')

  const loadWorkflowStatus = useCallback(async () => {
    const tid = taskIdRef.current
    if (!tid) return
    setWorkflowLoading(true)
    setWorkflowError('')
    try {
      setWorkflowStatus(await loadWorkflowStatusApi(tid))
    } catch (e) {
      const message = e instanceof Error ? e.message : '工作流状态查询失败'
      setWorkflowError(message)
    } finally {
      setWorkflowLoading(false)
    }
  }, [taskIdRef])

  const watchJob = useCallback(
    async (jobId?: string) => {
      if (!jobId) return
      for (let i = 0; i < 30; i += 1) {
        const job = await fetchWorkflowJobApi(jobId)
        setWorkflowJob(job)
        if (['completed', 'failed', 'error'].includes(String(job.status))) break
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
      }
      await loadWorkflowStatus()
    },
    [loadWorkflowStatus],
  )

  const runWorkflowStep = useCallback(
    async (step: WorkflowStep) => {
      const tid = taskIdRef.current
      if (!tid) return
      setWorkflowBusy(step)
      setWorkflowError('')
      reportError(null)
      try {
        const job = await runWorkflowStepApi(tid, step)
        setWorkflowJob(job)
        showToast('工作流步骤已启动')
        await watchJob(job.jobId)
      } catch (e) {
        const message = e instanceof Error ? e.message : '工作流步骤执行失败'
        setWorkflowError(message)
        reportError(message)
      } finally {
        setWorkflowBusy('')
      }
    },
    [reportError, showToast, taskIdRef, watchJob],
  )

  const runRemainingWorkflow = useCallback(async () => {
    const tid = taskIdRef.current
    if (!tid) return
    setWorkflowBusy('remaining')
    setWorkflowError('')
    reportError(null)
    try {
      const job = await runRemainingWorkflowApi(tid)
      setWorkflowJob(job)
      showToast('剩余工作流已启动')
      await watchJob(job.jobId)
    } catch (e) {
      const message = e instanceof Error ? e.message : '工作流执行失败'
      setWorkflowError(message)
      reportError(message)
    } finally {
      setWorkflowBusy('')
    }
  }, [reportError, showToast, taskIdRef, watchJob])

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
