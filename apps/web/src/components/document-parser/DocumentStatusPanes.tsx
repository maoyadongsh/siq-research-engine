import { Archive, Brain, Database, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type {
  DocumentQualityReport,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
} from '@/lib/documentTypes'
import { workflowStateClass, workflowStateLabel } from '@/lib/pdfFormatting'
import { workflowReady } from './documentResultWorkbenchUtils'

export type DocumentQualityPaneProps = {
  quality: DocumentQualityReport | null
  manifestQualityStatus?: string
}

export function DocumentQualityPane({
  quality,
  manifestQualityStatus,
}: DocumentQualityPaneProps) {
  return (
    <div className="doc-quality-list">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['总体状态', quality?.overall_status || manifestQualityStatus || '-'],
          ['页数', quality?.page_count ?? '-'],
          ['块数', quality?.block_count ?? '-'],
          ['表格', quality?.table_count ?? '-'],
          ['图片', quality?.image_count ?? '-'],
          ['可入库', quality?.ready_for_knowledge_base ? '是' : '待检查'],
        ].map(([label, value]) => (
          <div className="doc-data-row" key={label}>
            <h3>{value}</h3>
            <p>{label}</p>
          </div>
        ))}
      </div>
      {quality?.warnings?.length ? quality.warnings.map((warning, index) => (
        <div className="doc-data-row" key={`${warning.code}-${index}`}>
          <h3>{warning.code || 'warning'}</h3>
          <p>{warning.message}</p>
        </div>
      )) : <div className="doc-data-row"><h3>无阻塞警告</h3><p>当前质量报告未返回 warning。</p></div>}
    </div>
  )
}

export type DocumentWorkflowPaneProps = {
  workflowStatus: DocumentWorkflowStatus | null
  workflowBusy: string
  wikiImportResult: DocumentWikiImportResult | null
  onRefreshWorkflow: () => Promise<unknown>
  onImportWiki: () => Promise<void>
  onImportDatabase: () => Promise<void>
  onBuildSemanticChunks: (milvus?: boolean) => Promise<void>
}

export function DocumentWorkflowPane({
  workflowStatus,
  workflowBusy,
  wikiImportResult,
  onRefreshWorkflow,
  onImportWiki,
  onImportDatabase,
  onBuildSemanticChunks,
}: DocumentWorkflowPaneProps) {
  const workflowIsBusy = Boolean(workflowBusy)
  const wikiPackageReady = workflowReady(workflowStatus?.targets?.wiki?.status)

  return (
    <div className="doc-quality-list">
      <div className="doc-workflow-head">
        <div>
          <h3>
            <Database className="h-4 w-4 text-primary" />
            数据管线
          </h3>
          <p>PostgreSQL 与 results 目录保存全量解析信息；Wiki 保留文档入口和轻量产物清单。</p>
        </div>
        <div className="doc-action-row">
          <Button type="button" variant="secondary" size="sm" onClick={() => onRefreshWorkflow()} leftIcon={<RefreshCw className="h-4 w-4" />}>
            刷新状态
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => onImportWiki()}
            disabled={workflowIsBusy || workflowStatus?.artifacts?.ready === false}
            leftIcon={workflowBusy === 'wiki' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
          >
            {workflowBusy === 'wiki' ? '导入中...' : '继续入库'}
          </Button>
        </div>
      </div>

      <div className="doc-pipeline-note">
        <Database className="h-4 w-4" />
        <div>
          Wiki 不复制全量解析包；<code>artifact_manifest.json</code> 只记录核心文件路径、hash 和版本，用于判断是否过期。完整文档、结构化块、表格、图片和证据页码默认直接从 results 目录读取并进入 <code>document_parser</code> schema。
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['解析产物包', workflowStatus?.artifacts?.status || 'unknown', workflowStatus?.artifacts?.message || '等待解析产物'],
          ['Wiki 入库', workflowStatus?.targets?.wiki?.status || 'unknown', workflowStatus?.targets?.wiki?.message || workflowStatus?.targets?.wiki?.path || '未归档'],
          ['语义层', workflowStatus?.targets?.milvus?.status || 'unknown', workflowStatus?.targets?.milvus?.message || '未生成语义 chunks'],
          ['PostgreSQL', workflowStatus?.targets?.postgres?.status || 'unknown', workflowStatus?.targets?.postgres?.message || '未入库'],
        ].map(([label, status, desc]) => (
          <div className="doc-data-row" key={label}>
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-semibold text-text">{label}</span>
              <span className={`secondary-status ${workflowStateClass(status)}`}>{workflowStateLabel(status)}</span>
            </div>
            <p>{desc}</p>
          </div>
        ))}
      </div>

      <div className="doc-data-row">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3>核心解析产物清单</h3>
            <p>{workflowStatus?.artifacts?.readyCount ?? 0}/{workflowStatus?.artifacts?.total ?? 0} 个核心文件已生成</p>
          </div>
          <span className={`secondary-status ${workflowStatus?.artifacts?.ready ? 'secondary-status-success' : 'secondary-status-warning'}`}>
            {workflowStatus?.artifacts?.ready ? '已就绪' : '待补齐'}
          </span>
        </div>
        {workflowStatus?.artifacts?.missing?.length ? (
          <p>缺少: {workflowStatus.artifacts.missing.join('、')}</p>
        ) : null}
      </div>

      <div className="doc-action-row justify-start">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onImportWiki()}
          disabled={workflowIsBusy || workflowStatus?.artifacts?.ready === false}
          leftIcon={workflowBusy === 'wiki' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
        >
          {workflowBusy === 'wiki' ? '导入中...' : '导入 Wiki'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onImportDatabase()}
          disabled={!wikiPackageReady || workflowIsBusy}
          leftIcon={workflowBusy === 'postgres' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
        >
          {workflowBusy === 'postgres' ? '入库中...' : '导入 PostgreSQL'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onBuildSemanticChunks(false)}
          disabled={!wikiPackageReady || workflowIsBusy}
          leftIcon={workflowBusy === 'milvus' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
        >
          {workflowBusy === 'milvus' ? '生成中...' : '生成语义 chunks'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onBuildSemanticChunks(true)}
          disabled={!wikiPackageReady || workflowIsBusy}
          leftIcon={workflowBusy === 'milvus-ingest' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
        >
          {workflowBusy === 'milvus-ingest' ? '写入中...' : '写入 Milvus'}
        </Button>
      </div>

      {wikiImportResult?.packageDir ? (
        <div className="doc-data-row">
          <h3>{wikiImportResult.documentKey || 'Wiki package'}</h3>
          <p>{wikiImportResult.packageDir}</p>
        </div>
      ) : null}
    </div>
  )
}
