import { useMemo, useState } from 'react'
import { Archive, Download, FileJson, FileText, Image, Loader2, RefreshCw, Table2 } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import {
  documentArtifactUrl,
  documentDownloadUrl,
} from '@/lib/documentApi'
import type {
  DocumentArtifactsMap,
  DocumentBlocksPayload,
  DocumentExtractionTemplate,
  DocumentFiguresPayload,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
  DocumentTableRelationsPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
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

function relationId(relation: DocumentTableRelation, index: number) {
  return relation.relation_id || relation.id || `relation-${index + 1}`
}

function relationTables(relation: DocumentTableRelation) {
  const fragments = relationTableIds(relation)
  if (fragments?.length) return fragments.join(' -> ')
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean)
    .join(' -> ') || '未标注表格'
}

function relationTableIds(relation: DocumentTableRelation) {
  const fragments = relation.fragment_table_ids?.filter(Boolean) || []
  if (fragments.length) return fragments
  return [relation.source_table_id || relation.table_id, relation.target_table_id || relation.next_table_id]
    .filter(Boolean) as string[]
}

function relationConfidence(relation: DocumentTableRelation) {
  const value = relation.confidence ?? relation.merge_confidence
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

function relationFlowTone(relation: DocumentTableRelation) {
  const status = String(relation.review_status || relation.merge_status || relation.relation_type || '').toLowerCase()
  if (status.includes('reject') || status.includes('not_continuation')) return 'is-rejected'
  if (status.includes('accept') || status === 'continuation') return 'is-accepted'
  return 'is-candidate'
}

function tableLabel(table?: DocumentTable, fallbackId = '') {
  return table?.title || table?.caption || fallbackId || '表格片段'
}

function sourceEntriesFor(sourceMap: DocumentSourceMapPayload | null, match: (entry: NonNullable<DocumentSourceMapPayload['sources']>[number]) => boolean) {
  return (sourceMap?.sources || []).filter(match)
}

function firstSourceUrl(sourceMap: DocumentSourceMapPayload | null, table: DocumentTable) {
  const tableId = table.table_id || ''
  const blockId = table.block_id || ''
  return sourceEntriesFor(sourceMap, (entry) => Boolean(
    (tableId && entry.table_id === tableId) ||
    (blockId && entry.block_id === blockId),
  ))[0]?.open_source_url
}

export function DocumentResultWorkbench({
  selectedTask,
  result,
  quality,
  blocks,
  tables,
  tableRelations,
  figures,
  sourceMap,
  loading,
  extractionResult,
  extractionTemplates,
  workflowStatus,
  workflowBusy,
  wikiImportResult,
  onRunExtraction,
  onImportWiki,
  onImportDatabase,
  onBuildSemanticChunks,
  onRefreshWorkflow,
  onReviewTableRelation,
}: {
  selectedTask?: DocumentTaskItem
  result: DocumentResult | null
  quality: DocumentQualityReport | null
  blocks: DocumentBlocksPayload | null
  tables: DocumentTablesPayload | null
  tableRelations: DocumentTableRelationsPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
  loading: boolean
  extractionResult: Record<string, unknown> | null
  extractionTemplates: DocumentExtractionTemplate[]
  workflowStatus: DocumentWorkflowStatus | null
  workflowBusy: string
  wikiImportResult: DocumentWikiImportResult | null
  onRunExtraction: (schemaText: string, instructions: string, templateId?: string) => Promise<void>
  onImportWiki: () => Promise<void>
  onImportDatabase: () => Promise<void>
  onBuildSemanticChunks: (milvus?: boolean) => Promise<void>
  onRefreshWorkflow: () => Promise<unknown>
  onReviewTableRelation: (relationId: string, reviewStatus: string, note?: string) => Promise<void>
}) {
  const [schemaText, setSchemaText] = useState('{\n  "type": "object",\n  "properties": {\n    "title": { "type": "string" }\n  }\n}')
  const [instructions, setInstructions] = useState('只从原文抽取，不确定则返回 null。')
  const [templateId, setTemplateId] = useState('')

  const taskId = selectedTask?.task_id || result?.manifest?.task_id || ''
  const sourceBlocks = blocks?.blocks?.slice(0, 80) || []
  const artifactEntries = useMemo(() => Object.entries((result?.artifacts || {}) as DocumentArtifactsMap), [result?.artifacts])
  const physicalTables = tables?.physical_tables || tables?.tables || []
  const figureItems = figures?.figures || []
  const relationItems = tableRelations?.relations || []
  const tableById = useMemo(() => {
    const lookup = new Map<string, DocumentTable>()
    physicalTables.forEach((table) => {
      if (table.table_id) lookup.set(table.table_id, table)
    })
    return lookup
  }, [physicalTables])
  const workflowIsBusy = Boolean(workflowBusy)
  const validationReport = extractionResult?.validation_report as Record<string, unknown> | undefined
  const evidenceMap = (extractionResult?.evidence_map || {}) as Record<string, Array<Record<string, unknown>>>
  const missingFields = Array.isArray(validationReport?.missing_fields) ? validationReport.missing_fields : []

  const applyTemplate = (nextTemplateId: string) => {
    setTemplateId(nextTemplateId)
    const template = extractionTemplates.find((item) => item.template_id === nextTemplateId)
    if (!template) return
    setSchemaText(JSON.stringify(template.schema || {}, null, 2))
    setInstructions(template.instructions || '只从原文抽取，不确定则返回 null。')
  }

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
              <TabsTrigger value="workflow">入库</TabsTrigger>
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
                      {sourceEntriesFor(sourceMap, (entry) => entry.block_id === block.block_id)[0]?.open_source_url ? (
                        <a
                          className="doc-source-link"
                          href={sourceEntriesFor(sourceMap, (entry) => entry.block_id === block.block_id)[0]?.open_source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          打开源块
                        </a>
                      ) : null}
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
                  {firstSourceUrl(sourceMap, table) ? (
                    <a className="doc-source-link" href={firstSourceUrl(sourceMap, table)} target="_blank" rel="noreferrer">打开表格来源</a>
                  ) : null}
                  {table.markdown ? <pre className="doc-table-markdown">{table.markdown}</pre> : null}
                </div>
              )) : <div className="doc-empty">暂无表格产物</div>}
              <div className="doc-data-row">
                <h3>表格关系复核</h3>
                <p>跨页断表候选、逻辑合并关系和人工复核结果。</p>
              </div>
              {relationItems.length ? relationItems.map((relation, index) => {
                const id = relationId(relation, index)
                const tableIds = relationTableIds(relation)
                const pageNumbers = relation.page_numbers || []
                return (
                  <div className="doc-data-row" key={id}>
                    <h3>{relationTables(relation)}</h3>
                    <p>
                      {relation.relation_type || relation.merge_status || 'relation'} · 置信度 {relationConfidence(relation)}
                      {relation.review_status ? ` · ${relation.review_status}` : ''}
                    </p>
                    {tableIds.length ? (
                      <div className={`doc-relation-flow ${relationFlowTone(relation)}`}>
                        {tableIds.map((tableId, nodeIndex) => {
                          const table = tableById.get(tableId)
                          const pageNumber = table?.page_number || pageNumbers[nodeIndex] || pageNumbers[0] || 1
                          return (
                            <div className="doc-relation-step" key={`${id}-${tableId}-${nodeIndex}`}>
                              <div className="doc-relation-node">
                                <span className="doc-relation-page">p{pageNumber}</span>
                                <strong>{tableId}</strong>
                                <span>{tableLabel(table, tableId)}</span>
                                <em>{table?.quality?.row_count || 0} 行 · {table?.quality?.column_count || 0} 列</em>
                              </div>
                              {nodeIndex < tableIds.length - 1 ? (
                                <div className="doc-relation-connector" aria-hidden="true">
                                  <span />
                                </div>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    ) : null}
                    {relation.reasons?.length || relation.merge_reasons?.length ? (
                      <p>{[...(relation.reasons || []), ...(relation.merge_reasons || [])].join('；')}</p>
                    ) : null}
                    <div className="doc-action-row mt-3 justify-start">
                      <Button type="button" size="sm" variant="secondary" onClick={() => onReviewTableRelation(id, 'accepted')}>
                        接受合并
                      </Button>
                      <Button type="button" size="sm" variant="secondary" onClick={() => onReviewTableRelation(id, 'rejected')}>
                        拒绝合并
                      </Button>
                    </div>
                  </div>
                )
              }) : <div className="doc-data-row"><h3>暂无跨页断表候选</h3><p>当前产物没有返回 table_relations。</p></div>}
            </div>
          </TabsContent>

          <TabsContent value="figures" className="m-0">
            <div className="doc-figure-list">
              {figureItems.length ? figureItems.map((figure) => (
                <div className="doc-data-row" key={figure.image_id}>
                  <h3><Image className="mr-2 inline h-4 w-4" />{figure.caption || figure.image_id}</h3>
                  <p>页码 {figure.page_number || 1} · {figure.type || 'image'} · {figure.evidence_id || ''}</p>
                  {figure.bbox?.length ? <p>bbox: {figure.bbox.join(', ')} {figure.bbox_unit || ''}</p> : null}
                  {sourceEntriesFor(sourceMap, (entry) => entry.image_id === figure.image_id)[0]?.open_source_url ? (
                    <a
                      className="doc-source-link"
                      href={sourceEntriesFor(sourceMap, (entry) => entry.image_id === figure.image_id)[0]?.open_source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开图片来源
                    </a>
                  ) : null}
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
                  <span className="doc-label">抽取模板</span>
                  <select className="doc-select" value={templateId} onChange={(event) => applyTemplate(event.target.value)}>
                    <option value="">自定义 JSON Schema</option>
                    {extractionTemplates.map((template) => (
                      <option key={template.template_id} value={template.template_id}>
                        {template.name || template.template_id}
                      </option>
                    ))}
                  </select>
                </label>
                {templateId ? (
                  <div className="doc-data-row">
                    <h3>{extractionTemplates.find((item) => item.template_id === templateId)?.name || templateId}</h3>
                    <p>{extractionTemplates.find((item) => item.template_id === templateId)?.description || '模板 schema 已载入，可直接运行抽取。'}</p>
                  </div>
                ) : null}
                <label className="doc-field">
                  <span className="doc-label">JSON Schema</span>
                  <textarea className="doc-textarea" value={schemaText} onChange={(event) => setSchemaText(event.target.value)} />
                </label>
                <label className="doc-field">
                  <span className="doc-label">抽取指令</span>
                  <input className="doc-input" value={instructions} onChange={(event) => setInstructions(event.target.value)} />
                </label>
                <Button type="button" onClick={() => onRunExtraction(schemaText, instructions, templateId)}>运行抽取</Button>
                {validationReport ? (
                  <div className="doc-data-row">
                    <h3>{validationReport.schema_valid ? 'Schema 有效' : 'Schema 需检查'}</h3>
                    <p>
                      evidence coverage {String(validationReport.evidence_coverage_ratio ?? 0)}
                      {missingFields.length ? ` · 缺失 ${missingFields.map(String).join(', ')}` : ''}
                    </p>
                  </div>
                ) : null}
              </div>
              <div className="grid gap-3">
                <pre className="doc-json">{stringify(extractionResult || { status: 'not_run' })}</pre>
                {Object.keys(evidenceMap).length ? (
                  <div className="doc-table-list">
                    <div className="doc-data-row">
                      <h3>字段证据</h3>
                      <p>每个非空字段会保留 evidence id、页码和原文片段。</p>
                    </div>
                    {Object.entries(evidenceMap).map(([field, evidences]) => (
                      <div className="doc-data-row" key={field}>
                        <h3>{field}</h3>
                        {evidences.length ? evidences.map((evidence, index) => (
                          <p key={`${field}-${index}`}>
                            p{String(evidence.page_number || 1)} · {String(evidence.quote || '')}
                            {evidence.open_source_url ? (
                              <>
                                {' · '}
                                <a className="doc-source-link" href={String(evidence.open_source_url)} target="_blank" rel="noreferrer">打开证据</a>
                              </>
                            ) : null}
                          </p>
                        )) : <p>未找到证据，结果保持 null 或需人工复核。</p>}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
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

          <TabsContent value="workflow" className="m-0">
            <div className="doc-quality-list">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-text">通用文档入库</h3>
                  <p className="text-sm text-text-muted">当前阶段将解析产物归档到 data/wiki/documents，并生成 PostgreSQL 入库与语义 chunks。</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" size="sm" onClick={() => onRefreshWorkflow()} leftIcon={<RefreshCw className="h-4 w-4" />}>
                    刷新
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => onImportWiki()}
                    disabled={workflowIsBusy || workflowStatus?.artifacts?.ready === false}
                    leftIcon={workflowBusy === 'wiki' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  >
                    {workflowBusy === 'wiki' ? '归档中...' : '导入 Wiki'}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => onImportDatabase()}
                    disabled={workflowIsBusy || workflowStatus?.targets?.wiki?.status !== 'ready' || workflowStatus?.targets?.postgres?.status !== 'ready'}
                    leftIcon={workflowBusy === 'postgres' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  >
                    {workflowBusy === 'postgres' ? '入库中...' : '导入 PostgreSQL'}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => onBuildSemanticChunks(false)}
                    disabled={
                      workflowIsBusy ||
                      workflowStatus?.targets?.wiki?.status !== 'ready' ||
                      workflowStatus?.targets?.milvus?.status === 'missing'
                    }
                    leftIcon={workflowBusy === 'milvus' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  >
                    {workflowBusy === 'milvus' ? '生成中...' : '生成语义 chunks'}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => onBuildSemanticChunks(true)}
                    disabled={
                      workflowIsBusy ||
                      workflowStatus?.targets?.wiki?.status !== 'ready' ||
                      workflowStatus?.targets?.milvus?.status === 'missing'
                    }
                    leftIcon={workflowBusy === 'milvus-ingest' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
                  >
                    {workflowBusy === 'milvus-ingest' ? '写入中...' : '写入 Milvus'}
                  </Button>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['产物', workflowStatus?.artifacts?.status || 'unknown', workflowStatus?.artifacts?.message || '等待状态'],
                  ['Wiki', workflowStatus?.targets?.wiki?.status || 'unknown', workflowStatus?.targets?.wiki?.message || workflowStatus?.targets?.wiki?.path || '未归档'],
                  ['PostgreSQL', workflowStatus?.targets?.postgres?.status || 'disabled', workflowStatus?.targets?.postgres?.message || '后续接入'],
                  ['Milvus', workflowStatus?.targets?.milvus?.status || 'disabled', workflowStatus?.targets?.milvus?.message || '后续接入'],
                ].map(([label, status, desc]) => (
                  <div className="doc-data-row" key={label}>
                    <h3>{status}</h3>
                    <p>{label} · {desc}</p>
                  </div>
                ))}
              </div>

              {wikiImportResult?.packageDir ? (
                <div className="doc-data-row">
                  <h3>{wikiImportResult.documentKey || 'Wiki package'}</h3>
                  <p>{wikiImportResult.packageDir}</p>
                </div>
              ) : null}
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
