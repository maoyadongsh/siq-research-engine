import { useMemo, useState } from 'react'
import { Download, FileJson, FileText, Image, Loader2, Table2 } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import {
  documentArtifactUrl,
  documentDownloadUrl,
} from '@/lib/documentApi'
import type {
  DocumentArtifactsMap,
  DocumentBlocksPayload,
  DocumentFiguresPayload,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
} from '@/lib/documentTypes'

function statusLabel(status?: string) {
  return ({
    queued: '排队',
    uploaded: '已上传',
    detecting_type: '识别类型',
    running: '解析中',
    postprocessing: '后处理',
    completed: '完成',
    completed_with_warnings: '有警告',
    failed: '失败',
    cancelled: '已取消',
  } as Record<string, string>)[String(status || '')] || status || '未选择'
}

function statusTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'done'
  if (value === 'completed_with_warnings') return 'warn'
  if (value === 'failed' || value === 'cancelled') return 'fail'
  return 'run'
}

function stringify(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

export function DocumentResultWorkbench({
  selectedTask,
  result,
  quality,
  blocks,
  tables,
  figures,
  sourceMap,
  loading,
  extractionResult,
  onRunExtraction,
}: {
  selectedTask?: DocumentTaskItem
  result: DocumentResult | null
  quality: DocumentQualityReport | null
  blocks: DocumentBlocksPayload | null
  tables: DocumentTablesPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
  loading: boolean
  extractionResult: Record<string, unknown> | null
  onRunExtraction: (schemaText: string, instructions: string) => Promise<void>
}) {
  const [schemaText, setSchemaText] = useState('{\n  "type": "object",\n  "properties": {\n    "title": { "type": "string" }\n  }\n}')
  const [instructions, setInstructions] = useState('只从原文抽取，不确定则返回 null。')

  const taskId = selectedTask?.task_id || result?.manifest?.task_id || ''
  const sourceBlocks = blocks?.blocks?.slice(0, 80) || []
  const artifactEntries = useMemo(() => Object.entries((result?.artifacts || {}) as DocumentArtifactsMap), [result?.artifacts])
  const physicalTables = tables?.physical_tables || tables?.tables || []
  const figureItems = figures?.figures || []

  if (!selectedTask) {
    return (
      <section className="doc-panel">
        <div className="doc-empty">
          <div>
            <FileText className="mx-auto mb-3 h-10 w-10 text-text-muted" />
            <p>选择或上传一份文档后查看解析结果</p>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="doc-panel min-w-0">
      <div className="doc-result-head">
        <div className="doc-result-title">
          <h2>{selectedTask.filename || selectedTask.task_id}</h2>
          <p>
            {selectedTask.document_kind || 'document'} · {selectedTask.parser_provider || 'provider pending'}
          </p>
        </div>
        <div className="doc-action-row">
          <span className={`doc-badge ${statusTone(selectedTask.status)}`}>{statusLabel(selectedTask.status)}</span>
          {taskId ? (
            <Button asChild variant="secondary" size="sm" leftIcon={<Download className="h-4 w-4" />}>
              <a href={documentDownloadUrl(taskId)}>完整 ZIP</a>
            </Button>
          ) : null}
        </div>
      </div>

      {loading ? (
        <div className="doc-empty">
          <div>
            <Loader2 className="mx-auto mb-3 h-8 w-8 animate-spin text-primary" />
            <p>正在加载解析产物...</p>
          </div>
        </div>
      ) : null}

      {!loading && result ? (
        <Tabs defaultValue="preview" className="p-0">
          <div className="border-b border-border px-3 pt-3">
            <TabsList variant="default" className="w-full overflow-x-auto">
              <TabsTrigger value="preview">预览</TabsTrigger>
              <TabsTrigger value="markdown">Markdown</TabsTrigger>
              <TabsTrigger value="json">JSON</TabsTrigger>
              <TabsTrigger value="tables">表格</TabsTrigger>
              <TabsTrigger value="figures">图片</TabsTrigger>
              <TabsTrigger value="extract">抽取</TabsTrigger>
              <TabsTrigger value="quality">质量</TabsTrigger>
              <TabsTrigger value="artifacts">产物</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="preview" className="m-0">
            <div className="doc-preview-grid">
              <div className="doc-source-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>源文档结构</h3>
                    <p>当前 P0 使用 blocks/source map 对照，后续可替换为页图 bbox。</p>
                  </div>
                </div>
                <div className="doc-source-page">
                  {sourceBlocks.length ? sourceBlocks.map((block) => (
                    <div className="doc-source-block" key={block.block_id}>
                      <b>{block.block_id} · p{block.page_number || 1} · {block.type}</b>
                      <p>{block.text || block.markdown || '(空块)'}</p>
                    </div>
                  )) : <p className="text-sm text-text-muted">暂无结构块。</p>}
                </div>
              </div>
              <div className="doc-content-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>Markdown</h3>
                    <p>块级 source marker 已保留在 Markdown 中。</p>
                  </div>
                </div>
                <pre className="doc-markdown">{result.markdown || ''}</pre>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="markdown" className="m-0">
            <pre className="doc-markdown">{result.markdown || ''}</pre>
          </TabsContent>

          <TabsContent value="json" className="m-0">
            <pre className="doc-json">{stringify({ manifest: result.manifest, blocks, tables, figures, sourceMap })}</pre>
          </TabsContent>

          <TabsContent value="tables" className="m-0">
            <div className="doc-table-list">
              {physicalTables.length ? physicalTables.map((table, index) => (
                <div className="doc-data-row" key={table.table_id || index}>
                  <h3><Table2 className="mr-2 inline h-4 w-4" />{table.title || table.caption || table.table_id || `表格 ${index + 1}`}</h3>
                  <p>页码 {table.page_number || 1}{table.sheet_name ? ` · ${table.sheet_name}` : ''} · {table.quality?.row_count || 0} 行 · {table.quality?.column_count || 0} 列</p>
                  {table.markdown ? <pre className="doc-table-markdown">{table.markdown}</pre> : null}
                </div>
              )) : <div className="doc-empty">暂无表格产物</div>}
            </div>
          </TabsContent>

          <TabsContent value="figures" className="m-0">
            <div className="doc-figure-list">
              {figureItems.length ? figureItems.map((figure) => (
                <div className="doc-data-row" key={figure.image_id}>
                  <h3><Image className="mr-2 inline h-4 w-4" />{figure.caption || figure.image_id}</h3>
                  <p>页码 {figure.page_number || 1} · {figure.type || 'image'} · {figure.evidence_id || ''}</p>
                  {figure.image_path && taskId ? (
                    <img
                      src={documentArtifactUrl(taskId, figure.image_path)}
                      alt={figure.alt_text || figure.caption || figure.image_id || 'document figure'}
                      className="mt-3 max-h-[320px] max-w-full rounded-lg border border-border object-contain"
                      loading="lazy"
                    />
                  ) : null}
                  {figure.ocr_text ? <p>{figure.ocr_text}</p> : null}
                </div>
              )) : <div className="doc-empty">暂无图片产物</div>}
            </div>
          </TabsContent>

          <TabsContent value="extract" className="m-0">
            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,.95fr)_minmax(0,1.05fr)]">
              <div className="grid gap-3">
                <label className="doc-field">
                  <span className="doc-label">JSON Schema</span>
                  <textarea className="doc-textarea" value={schemaText} onChange={(event) => setSchemaText(event.target.value)} />
                </label>
                <label className="doc-field">
                  <span className="doc-label">抽取指令</span>
                  <input className="doc-input" value={instructions} onChange={(event) => setInstructions(event.target.value)} />
                </label>
                <Button type="button" onClick={() => onRunExtraction(schemaText, instructions)}>运行抽取</Button>
              </div>
              <pre className="doc-json">{stringify(extractionResult || { status: 'not_run' })}</pre>
            </div>
          </TabsContent>

          <TabsContent value="quality" className="m-0">
            <div className="doc-quality-list">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['总体状态', quality?.overall_status || result.manifest?.quality_status || '-'],
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
          </TabsContent>

          <TabsContent value="artifacts" className="m-0">
            <div className="doc-artifact-list">
              {artifactEntries.map(([name, info]) => (
                <div className="doc-data-row" key={name}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3><FileJson className="mr-2 inline h-4 w-4" />{name}</h3>
                      <p>{info.exists ? `${info.size || 0} bytes` : '缺失'}</p>
                    </div>
                    {info.exists && taskId ? (
                      <Button asChild variant="secondary" size="sm">
                        <a href={documentArtifactUrl(taskId, info.path || name)} target="_blank" rel="noreferrer">打开</a>
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>
        </Tabs>
      ) : null}

      {!loading && !result ? (
        <div className="doc-empty">
          <div>
            <FileText className="mx-auto mb-3 h-8 w-8 text-text-muted" />
            <p>任务尚未生成可展示结果</p>
          </div>
        </div>
      ) : null}
    </section>
  )
}
