import type {
  DocumentArtifactInfo,
  DocumentArtifactsMap,
  DocumentBlocksPayload,
  DocumentBlock,
  DocumentFigure,
  DocumentFiguresPayload,
  DocumentLayoutBlocksPayload,
  DocumentLayoutPage,
  DocumentQualityReport,
  DocumentResult,
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
  DocumentTablesPayload,
  DocumentTableRelationsPayload,
} from '@/lib/documentTypes'
import {
  buildDocumentResultFocusDerivation,
  buildDocumentResultJsonPreview,
  buildDocumentResultMarkdownBlocks,
  buildDocumentResultPageByNumber,
  buildDocumentResultPageNumbers,
  buildDocumentResultPreviewMarkdownBlocks,
  buildDocumentResultPreviewOverlays,
  buildDocumentResultPreviewPageModels,
  buildDocumentResultPreviewPages,
  buildDocumentResultPreviewRelations,
  buildDocumentResultRelationsByTableId,
  buildDocumentResultSourceLookups,
  buildDocumentResultTableLookups,
  buildDocumentResultVisibleRelations,
} from './documentResultWorkbenchDerivations'
import type { FocusTarget, MarkdownBlock, SourceMapEntry } from './documentResultWorkbenchUtils'

export type DocumentResultBaseViewModelInput = {
  taskId: string
  result: DocumentResult | null
  quality: DocumentQualityReport | null
  blocks: DocumentBlocksPayload | null
  layout: DocumentLayoutBlocksPayload | null
  tables: DocumentTablesPayload | null
  tableRelations: DocumentTableRelationsPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
}

export type DocumentResultBaseViewModel = {
  taskId: string
  artifactEntries: [string, DocumentArtifactInfo][]
  sourceBlocks: DocumentBlock[]
  pageByNumber: Map<number, DocumentLayoutPage>
  physicalTables: DocumentTable[]
  figureItems: DocumentFigure[]
  relationItems: DocumentTableRelation[]
  jsonPreview: ReturnType<typeof buildDocumentResultJsonPreview>
  sourceByBlockId: Map<string, SourceMapEntry>
  sourceByTableId: Map<string, SourceMapEntry>
  sourceByFigureId: Map<string, SourceMapEntry>
  tableById: Map<string, DocumentTable>
  tableByBlockId: Map<string, DocumentTable>
  tableIdByBlockId: Map<string, string>
  blockIdByTableId: Map<string, string>
  markdownBlocks: MarkdownBlock[]
  pageNumbers: number[]
}

export type DocumentResultViewModelInput = {
  base: DocumentResultBaseViewModel
  activePage: number
  focused: FocusTarget
}

export type DocumentResultViewModel = DocumentResultBaseViewModel & {
  activeFocusKeys: Set<string>
  focusedRelations: DocumentTableRelation[]
  visibleRelations: DocumentTableRelation[]
  overlays: ReturnType<typeof buildDocumentResultPreviewOverlays>
  previewPages: number[]
  previewMarkdownBlocks: MarkdownBlock[]
  previewPageModels: ReturnType<typeof buildDocumentResultPreviewPageModels>
}

export function buildDocumentResultBaseViewModel({
  taskId,
  result,
  quality,
  blocks,
  layout,
  tables,
  tableRelations,
  figures,
  sourceMap,
}: DocumentResultBaseViewModelInput): DocumentResultBaseViewModel {
  const sourceBlocks: DocumentBlock[] = blocks?.blocks || []
  const pageByNumber = buildDocumentResultPageByNumber(layout?.pages)
  const artifactEntries = Object.entries((result?.artifacts || {}) as DocumentArtifactsMap)
  const physicalTables = tables?.physical_tables || tables?.tables || []
  const figureItems: DocumentFigure[] = figures?.figures || []
  const relationItems: DocumentTableRelation[] = tableRelations?.relations || []
  const jsonPreview = buildDocumentResultJsonPreview({
    manifest: result?.manifest,
    blocks,
    tables,
    figures,
    sourceMap,
  })
  const { tableById, tableByBlockId, tableIdByBlockId, blockIdByTableId } = buildDocumentResultTableLookups(physicalTables)
  const { sourceByBlockId, sourceByTableId, sourceByFigureId } = buildDocumentResultSourceLookups(sourceMap)
  const markdownBlocks = buildDocumentResultMarkdownBlocks(sourceBlocks, result?.markdown || '', tableByBlockId)
  const pageNumbers = buildDocumentResultPageNumbers({
    sourceBlocks,
    pageByNumber,
    physicalTables,
    figureItems,
    markdownBlocks,
    qualityPageCount: quality?.page_count,
  })

  return {
    taskId,
    artifactEntries,
    sourceBlocks,
    pageByNumber,
    physicalTables,
    figureItems,
    relationItems,
    jsonPreview,
    sourceByBlockId,
    sourceByTableId,
    sourceByFigureId,
    tableById,
    tableByBlockId,
    tableIdByBlockId,
    blockIdByTableId,
    markdownBlocks,
    pageNumbers,
  }
}

export function buildDocumentResultViewModel({
  base,
  activePage,
  focused,
}: DocumentResultViewModelInput): DocumentResultViewModel {
  const previewRelations = buildDocumentResultPreviewRelations(base.relationItems, base.tableById)
  const relationsByTableId = buildDocumentResultRelationsByTableId(previewRelations)
  const { activeFocusKeys, focusedRelations } = buildDocumentResultFocusDerivation({
    focused,
    tableIdByBlockId: base.tableIdByBlockId,
    blockIdByTableId: base.blockIdByTableId,
    relationsByTableId,
  })
  const visibleRelations = buildDocumentResultVisibleRelations({
    activePage,
    focusedRelations,
    previewRelations,
    tableById: base.tableById,
  })
  const overlays = buildDocumentResultPreviewOverlays({
    sourceBlocks: base.sourceBlocks,
    physicalTables: base.physicalTables,
    figureItems: base.figureItems,
    sourceByBlockId: base.sourceByBlockId,
    sourceByTableId: base.sourceByTableId,
    sourceByFigureId: base.sourceByFigureId,
    tableIdByBlockId: base.tableIdByBlockId,
  })
  const previewPages = buildDocumentResultPreviewPages({
    activePage,
    visibleRelations,
    tableById: base.tableById,
  })
  const previewMarkdownBlocks = buildDocumentResultPreviewMarkdownBlocks(base.markdownBlocks, previewPages)
  const previewPageModels = buildDocumentResultPreviewPageModels({
    previewPages,
    visibleRelations,
    tableById: base.tableById,
    overlays,
  })

  return {
    ...base,
    activeFocusKeys,
    focusedRelations,
    visibleRelations,
    overlays,
    previewPages,
    previewMarkdownBlocks,
    previewPageModels,
  }
}
