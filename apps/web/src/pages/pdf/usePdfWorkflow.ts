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
type WorkflowMode = 'standard' | 'generic'

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
      if (!jobId) {
        await loadWorkflowStatus()
        return
      }
      for (let i = 0; i < 900; i += 1) {
        const job = await fetchWorkflowJobApi(jobId)
        setWorkflowJob(job)
        if (['completed', 'succeeded', 'failed', 'error'].includes(String(job.status))) break
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

  const runRemainingWorkflow = useCallback(async (mode: WorkflowMode = 'standard') => {
    const tid = taskIdRef.current
    if (!tid) return
    setWorkflowBusy('remaining')
    setWorkflowError('')
    reportError(null)
    try {
      if (mode === 'generic') {
        const steps: WorkflowStep[] = ['wiki-import-generic', 'semantic-generic', 'db-import']
        const completedSteps: NonNullable<WorkflowJob['steps']> = []
        for (const step of steps) {
          setWorkflowBusy(step)
          const result = await runWorkflowStepApi(tid, step)
          completedSteps.push({ step, status: 'succeeded' })
          setWorkflowJob({ status: 'running', steps: completedSteps })
          if (result.jobId) await watchJob(result.jobId)
          else await loadWorkflowStatus()
        }
        setWorkflowJob({ status: 'succeeded', steps: completedSteps })
        showToast('境外市场数据管线已完成')
      } else {
        const job = await runRemainingWorkflowApi(tid)
        setWorkflowJob(job)
        showToast('剩余工作流已启动')
        await watchJob(job.jobId)
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : '工作流执行失败'
      setWorkflowError(message)
      reportError(message)
    } finally {
      setWorkflowBusy('')
    }
  }, [loadWorkflowStatus, reportError, showToast, taskIdRef, watchJob])

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
