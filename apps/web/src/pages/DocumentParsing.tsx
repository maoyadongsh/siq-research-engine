import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Files, Loader2, RefreshCw } from 'lucide-react'
import { PageHeader, PageShell, StatusBadge } from '@/components/page'
import { Button } from '@/components/ui/button'
import { DocumentParameterPanel } from '@/components/document-parser/DocumentParameterPanel'
import { DocumentResultWorkbench } from '@/components/document-parser/DocumentResultWorkbench'
import { DocumentTaskList } from '@/components/document-parser/DocumentTaskList'
import { DocumentUploadPanel } from '@/components/document-parser/DocumentUploadPanel'
import { DOCUMENT_CSS } from '@/components/document-parser/documentStyles'
import { checkDocumentParserHealth, loadDocumentQuota } from '@/lib/documentApi'
import type { DocumentParseConfig } from '@/lib/documentTypes'
import { useToast } from '@/hooks/useToast'
import { useDocumentTasks } from './documents/useDocumentTasks'

const defaultConfig: DocumentParseConfig = {
  modelVersion: 'auto',
  ocr: 'auto',
  enableFormula: true,
  enableTable: true,
  language: 'auto',
  pageRanges: '',
  extraFormats: ['zip'],
  noCache: false,
}

export default function DocumentParsing() {
  const [searchParams] = useSearchParams()
  const { toast } = useToast()
  const [config, setConfig] = useState<DocumentParseConfig>(defaultConfig)
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [quota, setQuota] = useState<Record<string, unknown> | null>(null)

  const showToast = (message: string) => toast({ type: 'info', title: message })
  const tasks = useDocumentTasks(showToast)

  const selectedTask = useMemo(
    () => tasks.tasks.find((task) => task.task_id === tasks.selectedTaskId),
    [tasks.selectedTaskId, tasks.tasks],
  )
  const requestedTaskId = searchParams.get('task') || ''
  const { selectTask, selectedTaskId } = tasks

  useEffect(() => {
    let cancelled = false
    async function loadMeta() {
      const [healthData, quotaData] = await Promise.all([
        checkDocumentParserHealth(),
        loadDocumentQuota(),
      ])
      if (cancelled) return
      setHealth(healthData)
      setQuota(quotaData)
    }
    void loadMeta()
    const timer = window.setInterval(() => void loadMeta(), 15000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!requestedTaskId || requestedTaskId === selectedTaskId) return
    void selectTask(requestedTaskId)
  }, [requestedTaskId, selectTask, selectedTaskId])

  return (
    <PageShell>
      <style>{DOCUMENT_CSS}</style>
      <PageHeader
        icon={Files}
        eyebrow="Document Parser"
        title="文档解析"
        description="上传 PDF、Office、图片、HTML 或网页 URL，生成 Markdown、JSON、表格、图片索引、source map、质量报告和结构化抽取结果。"
        meta={
          <>
            <StatusBadge tone={health?.status === 'ok' ? 'success' : 'warning'}>
              {health?.status === 'ok' ? '服务正常' : '服务待检查'}
            </StatusBadge>
            <StatusBadge tone="info">
              今日剩余 {typeof quota?.remaining === 'number' ? quota.remaining : '不限'}
            </StatusBadge>
          </>
        }
        actions={
          <Button variant="secondary" onClick={() => tasks.refreshTasks()} leftIcon={<RefreshCw className="h-4 w-4" />}>
            刷新任务
          </Button>
        }
      />

      {tasks.error ? <div className="doc-error">{tasks.error}</div> : null}

      <main className="doc-workbench">
        <aside className="doc-side">
          <DocumentUploadPanel
            config={config}
            uploading={tasks.uploading}
            onSubmitFiles={tasks.submitFiles}
            onSubmitUrl={tasks.submitUrl}
          />
          <DocumentParameterPanel config={config} onChange={setConfig} />
          <DocumentTaskList
            tasks={tasks.tasks}
            selectedTaskId={tasks.selectedTaskId}
            onSelect={(taskId) => void tasks.selectTask(taskId)}
            onRetry={(taskId) => void tasks.retryTask(taskId)}
            onDelete={(taskId) => void tasks.deleteTask(taskId)}
          />
          {tasks.uploading ? (
            <section className="doc-panel">
              <div className="doc-panel-body flex items-center gap-2 text-sm font-semibold text-primary">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在提交文档解析任务...
              </div>
            </section>
          ) : null}
        </aside>

        <DocumentResultWorkbench
          selectedTask={selectedTask}
          result={tasks.result}
          quality={tasks.quality}
          blocks={tasks.blocks}
          tables={tasks.tables}
          figures={tasks.figures}
          sourceMap={tasks.sourceMap}
          loading={tasks.loading}
          extractionResult={tasks.extractionResult}
          onRunExtraction={tasks.runExtraction}
        />
      </main>
    </PageShell>
  )
}
