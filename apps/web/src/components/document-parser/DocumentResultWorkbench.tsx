import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Brain, ChevronLeft, ChevronRight, Database, Download, Eye, FileJson, FileText, Image, ListChecks, Loader2, Table2 } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/page'
import {
  documentArtifactUrl,
  documentDownloadUrl,
  openDocumentResource,
} from '@/features/document-parser/api'
import type {
  DocumentArtifactInfo,
  DocumentArtifactsMap,
  DocumentBlocksPayload,
  DocumentExtractionTemplate,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTableRelation,
  DocumentTableRelationsPayload,
  DocumentTablesPayload,
  DocumentTaskItem,
  DocumentWikiImportResult,
  DocumentWorkflowStatus,
} from '@/lib/documentTypes'
import { DocumentArtifactPane } from './DocumentArtifactPane'
import { DocumentExtractPane } from './DocumentExtractPane'
import { DocumentFigurePane } from './DocumentFigurePane'
import { DocumentResultJsonPane } from './DocumentResultJsonPane'
import { DocumentMarkdownPane } from './DocumentMarkdownPane'
import { DocumentQualityPane, DocumentWorkflowPane } from './DocumentStatusPanes'
import { DocumentTablePane } from './DocumentTablePane'
import { PdfPagePreview } from './DocumentSourcePreview'
import {
  adjacentDocumentResultPage,
  buildDocumentResultFocusDerivation,
  buildDocumentResultJsonPreview,
  buildDocumentResultMarkdownBlocks,
  buildDocumentResultPageByNumber,
  buildDocumentResultPageNumbers,
  buildDocumentResultPreviewMarkdownBlocks,
  buildDocumentResultPreviewOverlays,
  buildDocumentResultPreviewPages,
  buildDocumentResultPreviewRelations,
  buildDocumentResultPreviewPageModels,
  buildDocumentResultRelationsByTableId,
  buildDocumentResultSourceLookups,
  buildDocumentResultTableLookups,
  buildDocumentResultVisibleRelations,
} from './documentResultWorkbenchDerivations'
import {
  cssAttrValue,
  relationFlowTone,
  relationLabel,
  statusLabel,
  statusTone,
  type FocusTarget,
} from './documentResultWorkbenchUtils'

function MergePageBridge({
  relation,
  onClick,
}: {
  relation: DocumentTableRelation
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`doc-page-merge-bridge ${relationFlowTone(relation)}`}
      title={relationLabel(relation)}
      onClick={onClick}
    >
      <span>合并</span>
    </button>
  )
}

export function DocumentResultWorkbench({
  selectedTask,
  result,
  quality,
  blocks,
  layout,
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
  layout: DocumentLayoutBlocksPayload | null
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
  const pdfPaneRef = useRef<HTMLDivElement | null>(null)
  const markdownPaneRef = useRef<HTMLDivElement | null>(null)
  const [activePage, setActivePage] = useState(1)
  const [focused, setFocused] = useState<FocusTarget>(null)
  const [resourceError, setResourceError] = useState('')
  const [activeTab, setActiveTab] = useState('preview')
  const tabListRef = useRef<HTMLDivElement | null>(null)
  const scrollTabs = useCallback((direction: number) => {
    const el = tabListRef.current
    if (!el) return
    el.scrollBy({ left: direction * 160, behavior: 'smooth' })
  }, [])

  const taskId = selectedTask?.task_id || result?.manifest?.task_id || ''
  const sourceBlocks = useMemo(() => blocks?.blocks || [], [blocks?.blocks])
  const pageByNumber = useMemo(() => buildDocumentResultPageByNumber(layout?.pages), [layout?.pages])
  const artifactEntries = useMemo(() => Object.entries((result?.artifacts || {}) as DocumentArtifactsMap), [result?.artifacts])
  const physicalTables = useMemo(() => tables?.physical_tables || tables?.tables || [], [tables?.physical_tables, tables?.tables])
  const figureItems = useMemo(() => figures?.figures || [], [figures?.figures])
  const relationItems = useMemo(() => tableRelations?.relations || [], [tableRelations?.relations])
  const jsonPreview = useMemo(
    () => buildDocumentResultJsonPreview({ manifest: result?.manifest, blocks, tables, figures, sourceMap }),
    [blocks, figures, result?.manifest, sourceMap, tables],
  )
  const { sourceByBlockId, sourceByTableId, sourceByFigureId } = useMemo(
    () => buildDocumentResultSourceLookups(sourceMap),
    [sourceMap],
  )
  const { tableById, tableByBlockId, tableIdByBlockId, blockIdByTableId } = useMemo(
    () => buildDocumentResultTableLookups(physicalTables),
    [physicalTables],
  )
  const previewRelations = useMemo(
    () => buildDocumentResultPreviewRelations(relationItems, tableById),
    [relationItems, tableById],
  )

  const markdownBlocks = useMemo(
    () => buildDocumentResultMarkdownBlocks(sourceBlocks, result?.markdown || '', tableByBlockId),
    [sourceBlocks, result?.markdown, tableByBlockId],
  )
  const relationsByTableId = useMemo(
    () => buildDocumentResultRelationsByTableId(previewRelations),
    [previewRelations],
  )
  const { activeFocusKeys, focusedRelations } = useMemo(
    () => buildDocumentResultFocusDerivation({
      focused,
      tableIdByBlockId,
      blockIdByTableId,
      relationsByTableId,
    }),
    [blockIdByTableId, focused, relationsByTableId, tableIdByBlockId],
  )
  const visibleRelations = useMemo(
    () => buildDocumentResultVisibleRelations({
      activePage,
      focusedRelations,
      previewRelations,
      tableById,
    }),
    [activePage, focusedRelations, previewRelations, tableById],
  )

  const pageNumbers = useMemo(() => buildDocumentResultPageNumbers({
    sourceBlocks,
    pageByNumber,
    physicalTables,
    figureItems,
    markdownBlocks,
    qualityPageCount: quality?.page_count,
  }), [figureItems, markdownBlocks, pageByNumber, physicalTables, quality?.page_count, sourceBlocks])

  useEffect(() => {
    let cancelled = false
    const nextPage = pageNumbers[0] || 1
    queueMicrotask(() => {
      if (cancelled) return
      setActivePage(nextPage)
      setFocused(null)
      setResourceError('')
    })
    return () => {
      cancelled = true
    }
  }, [taskId, pageNumbers])

  const overlays = useMemo(() => buildDocumentResultPreviewOverlays({
    sourceBlocks,
    physicalTables,
    figureItems,
    sourceByBlockId,
    sourceByTableId,
    sourceByFigureId,
    tableIdByBlockId,
  }), [figureItems, physicalTables, sourceBlocks, sourceByBlockId, sourceByFigureId, sourceByTableId, tableIdByBlockId])

  const previewPages = useMemo(() => buildDocumentResultPreviewPages({
    activePage,
    visibleRelations,
    tableById,
  }), [activePage, tableById, visibleRelations])
  const previewMarkdownBlocks = useMemo(
    () => buildDocumentResultPreviewMarkdownBlocks(markdownBlocks, previewPages),
    [markdownBlocks, previewPages],
  )
  const previewPageModels = useMemo(
    () => buildDocumentResultPreviewPageModels({
      previewPages,
      visibleRelations,
      tableById,
      overlays,
    }),
    [overlays, previewPages, tableById, visibleRelations],
  )

  useEffect(() => {
    if (!focused || !activeFocusKeys.size) return
    const selector = Array.from(activeFocusKeys)
      .map((key) => `[data-focus-keys~="${cssAttrValue(key)}"]`)
      .join(',')
    if (!selector) return

    window.requestAnimationFrame(() => {
      const pdfTarget = pdfPaneRef.current?.querySelector<HTMLElement>(selector)
      pdfTarget?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })

      const markdownTarget = markdownPaneRef.current?.querySelector<HTMLElement>(selector)
      markdownTarget?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' })
    })
  }, [activeFocusKeys, focused, previewPages])

  const openResource = useCallback(async (url: string, filename?: string) => {
    if (!url) return
    setResourceError('')
    try {
      await openDocumentResource(url, filename)
    } catch (err) {
      setResourceError(err instanceof Error ? err.message : '产物打开失败')
    }
  }, [])
  const openArtifact = useCallback((name: string, info: DocumentArtifactInfo) => {
    if (!taskId || !info.exists) return
    const artifactPath = info.path || name
    void openResource(documentArtifactUrl(taskId, artifactPath), artifactPath)
  }, [openResource, taskId])

  const focusTarget = (nextFocus: FocusTarget) => {
    setFocused(nextFocus)
    if (nextFocus?.page) setActivePage(nextFocus.page)
  }

  const selectPage = (page: number) => {
    setActivePage(page)
    setFocused({ kind: 'page', id: `page-${page}`, page })
  }

  if (!selectedTask) {
    return (
      <section className="doc-panel">
        <EmptyState
          icon={FileText}
          title="选择或上传一份文档"
          description="选择左侧任务或上传新文档后查看解析结果。"
          size="lg"
          className="min-h-[360px]"
        />
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
            <Button
              type="button"
              variant="secondary"
              size="sm"
              leftIcon={<Download className="h-4 w-4" />}
              onClick={() => void openResource(documentDownloadUrl(taskId), `${taskId}.zip`)}
            >
              完整 ZIP
            </Button>
          ) : null}
        </div>
      </div>

      {resourceError ? <div className="doc-error mx-4 mt-4">{resourceError}</div> : null}

      {loading ? (
        <div className="doc-empty">
          <div>
            <Loader2 className="mx-auto mb-3 h-8 w-8 animate-spin text-primary" />
            <p>正在加载解析产物...</p>
          </div>
        </div>
      ) : null}

      {!loading && result ? (
        <Tabs value={activeTab} onValueChange={setActiveTab} className="p-0">
          <div className="border-b border-border px-3 pt-3">
            <div className="relative">
              <TabsList ref={tabListRef} variant="default" className="scroll-hint w-full overflow-x-auto px-7 md:px-9">
                <TabsTrigger value="preview" className="flex-none gap-1.5"><Eye className="h-4 w-4" /><span className="hidden md:inline">预览</span></TabsTrigger>
                <TabsTrigger value="markdown" className="flex-none gap-1.5"><FileText className="h-4 w-4" /><span className="hidden md:inline">Markdown</span></TabsTrigger>
                <TabsTrigger value="json" className="flex-none gap-1.5"><FileJson className="h-4 w-4" /><span className="hidden md:inline">JSON</span></TabsTrigger>
                <TabsTrigger value="tables" className="flex-none gap-1.5"><Table2 className="h-4 w-4" /><span className="hidden md:inline">表格</span></TabsTrigger>
                <TabsTrigger value="figures" className="flex-none gap-1.5"><Image className="h-4 w-4" /><span className="hidden md:inline">图片</span></TabsTrigger>
                <TabsTrigger value="extract" className="flex-none gap-1.5"><Brain className="h-4 w-4" /><span className="hidden md:inline">抽取</span></TabsTrigger>
                <TabsTrigger value="workflow" className="flex-none gap-1.5"><Database className="h-4 w-4" /><span className="hidden md:inline">入库</span></TabsTrigger>
                <TabsTrigger value="quality" className="flex-none gap-1.5"><ListChecks className="h-4 w-4" /><span className="hidden md:inline">质量</span></TabsTrigger>
                <TabsTrigger value="artifacts" className="flex-none gap-1.5"><Archive className="h-4 w-4" /><span className="hidden md:inline">产物</span></TabsTrigger>
              </TabsList>
              <button
                type="button"
                onClick={() => scrollTabs(-1)}
                className="absolute left-0 top-1/2 hidden h-10 w-8 -translate-y-1/2 items-center justify-center rounded-r-lg border border-border bg-white/90 text-text shadow-sm hover:bg-bg md:flex"
                aria-label="向左滚动标签"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => scrollTabs(1)}
                className="absolute right-0 top-1/2 hidden h-10 w-8 -translate-y-1/2 items-center justify-center rounded-l-lg border border-border bg-white/90 text-text shadow-sm hover:bg-bg md:flex"
                aria-label="向右滚动标签"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
            <select
              className="md:hidden doc-select mt-2 w-full"
              value={activeTab}
              onChange={(event) => setActiveTab(event.target.value)}
              aria-label="切换结果标签"
            >
              <option value="preview">预览</option>
              <option value="markdown">Markdown</option>
              <option value="json">JSON</option>
              <option value="tables">表格</option>
              <option value="figures">图片</option>
              <option value="extract">抽取</option>
              <option value="workflow">入库</option>
              <option value="quality">质量</option>
              <option value="artifacts">产物</option>
            </select>
          </div>

          <TabsContent value="preview" className="m-0">
            <div className="doc-preview-grid">
              <div className="doc-source-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>PDF 原页</h3>
                    <p>页面截图中的 bbox 与右侧 document.md 块同步高亮。</p>
                  </div>
                  <div className="doc-page-controls">
                    <Button
                      type="button"
                      variant="secondary"
                      size="icon-xs"
                      aria-label="上一页"
                      disabled={!pageNumbers.length || activePage <= pageNumbers[0]}
                      onClick={() => selectPage(adjacentDocumentResultPage(pageNumbers, activePage, -1))}
                    >
                      <ChevronLeft className="h-3 w-3" />
                    </Button>
                    <select className="doc-page-select" value={activePage} onChange={(event) => selectPage(Number(event.target.value))}>
                      {(pageNumbers.length ? pageNumbers : [1]).map((page) => (
                        <option key={page} value={page}>p{page}</option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="secondary"
                      size="icon-xs"
                      aria-label="下一页"
                      disabled={!pageNumbers.length || activePage >= pageNumbers[pageNumbers.length - 1]}
                      onClick={() => selectPage(adjacentDocumentResultPage(pageNumbers, activePage, 1))}
                    >
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <div className="doc-source-page" ref={pdfPaneRef}>
                  {taskId ? previewPageModels.map((pageModel) => {
                    return (
                      <div className="doc-pdf-page-stack" key={pageModel.pageNumber}>
                        <PdfPagePreview
                          taskId={taskId}
                          pageNumberValue={pageModel.pageNumber}
                          page={pageByNumber.get(pageModel.pageNumber)}
                          overlays={pageModel.overlays}
                          relations={pageModel.relations}
                          tableById={tableById}
                          activeFocusKeys={activeFocusKeys}
                          onFocus={focusTarget}
                          onOpenResource={(url, filename) => void openResource(url, filename)}
                        />
                        {pageModel.bridgeRelation ? (
                          <MergePageBridge
                            relation={pageModel.bridgeRelation}
                            onClick={() => focusTarget({ kind: 'table', id: pageModel.bridgeFocusId, page: pageModel.bridgePage })}
                          />
                        ) : null}
                      </div>
                    )
                  }) : <EmptyState icon={Image} title="暂无页图" description="当前任务未返回页面截图。" size="sm" className="min-h-[240px]" />}
                </div>
              </div>
              <div className="doc-content-pane">
                <div className="doc-panel-head">
                  <div>
                    <h3>document.md</h3>
                    <p>渲染为可读 HTML，点击块会定位到对应 PDF 页。</p>
                  </div>
                </div>
                <div className="doc-md-render doc-md-preview" ref={markdownPaneRef}>
                  <DocumentMarkdownPane
                    blocks={previewMarkdownBlocks}
                    activeFocusKeys={activeFocusKeys}
                    emptyTitle="暂无 Markdown 块"
                    emptyDescription="当前页没有可渲染的 Markdown 内容。"
                    onFocusBlock={focusTarget}
                  />
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="markdown" className="m-0">
            <div className="doc-md-render is-full">
              <DocumentMarkdownPane
                blocks={markdownBlocks}
                activeFocusKeys={activeFocusKeys}
                emptyTitle="暂无 Markdown 内容"
                emptyDescription="当前任务没有返回 Markdown 产物。"
                onFocusBlock={focusTarget}
              />
            </div>
          </TabsContent>

          <TabsContent value="json" className="m-0">
            <DocumentResultJsonPane preview={jsonPreview} />
          </TabsContent>

          <TabsContent value="tables" className="m-0">
            <DocumentTablePane
              physicalTables={physicalTables}
              relationItems={relationItems}
              tableById={tableById}
              sourceMap={sourceMap}
              onFocusTable={(tableId, page) => focusTarget({ kind: 'table', id: tableId, page })}
              onReviewTableRelation={onReviewTableRelation}
              openResource={(url, filename) => void openResource(url, filename)}
            />
          </TabsContent>

          <TabsContent value="figures" className="m-0">
            <DocumentFigurePane
              figures={figureItems}
              sourceMap={sourceMap}
              taskId={taskId}
              onFocusFigure={(figureId, page) => focusTarget({ kind: 'figure', id: figureId, page })}
              openResource={(url, filename) => void openResource(url, filename)}
            />
          </TabsContent>

          <TabsContent value="extract" className="m-0">
            <DocumentExtractPane
              extractionResult={extractionResult}
              extractionTemplates={extractionTemplates}
              onRunExtraction={onRunExtraction}
              openResource={(url, filename) => void openResource(url, filename)}
            />
          </TabsContent>

          <TabsContent value="quality" className="m-0">
            <DocumentQualityPane
              quality={quality}
              manifestQualityStatus={result.manifest?.quality_status}
            />
          </TabsContent>

          <TabsContent value="workflow" className="m-0">
            <DocumentWorkflowPane
              workflowStatus={workflowStatus}
              workflowBusy={workflowBusy}
              wikiImportResult={wikiImportResult}
              onRefreshWorkflow={onRefreshWorkflow}
              onImportWiki={onImportWiki}
              onImportDatabase={onImportDatabase}
              onBuildSemanticChunks={onBuildSemanticChunks}
            />
          </TabsContent>

          <TabsContent value="artifacts" className="m-0">
            <DocumentArtifactPane entries={artifactEntries} taskId={taskId} onOpenArtifact={openArtifact} />
          </TabsContent>
        </Tabs>
      ) : null}

      {!loading && !result ? (
        <EmptyState
          icon={FileText}
          title="任务尚未生成可展示结果"
          description="解析完成后会自动刷新，也可以点击右上角刷新任务。"
          size="md"
          className="min-h-[320px]"
        />
      ) : null}
    </section>
  )
}
