import { ExternalLink, Table2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/page'
import type {
  DocumentSourceMapPayload,
  DocumentTable,
  DocumentTableRelation,
} from '@/lib/documentTypes'
import {
  firstSourceUrl,
  isPreviewCrossPageTableRelation,
  pageNumber,
  relationConfidence,
  relationFlowTone,
  relationId,
  relationPages,
  relationTableIds,
  relationTables,
  tableLabel,
} from './documentResultWorkbenchUtils'

export type DocumentTablePaneProps = {
  physicalTables: DocumentTable[]
  relationItems: DocumentTableRelation[]
  tableById: Map<string, DocumentTable>
  sourceMap: DocumentSourceMapPayload | null
  onFocusTable: (tableId: string, page: number) => void
  onReviewTableRelation: (relationId: string, reviewStatus: 'accepted' | 'rejected', note?: string) => Promise<void>
  openResource: (url: string, filename?: string) => void | Promise<void>
}

export function DocumentTablePane({
  physicalTables,
  relationItems,
  tableById,
  sourceMap,
  onFocusTable,
  onReviewTableRelation,
  openResource,
}: DocumentTablePaneProps) {
  return (
    <div className="doc-table-list">
      {physicalTables.length ? physicalTables.map((table, index) => {
        const tableId = table.table_id || String(index)
        const sourceUrl = firstSourceUrl(sourceMap, table)
        return (
          <div className="doc-data-row" key={table.table_id || index}>
            <h3><Table2 className="mr-2 inline h-4 w-4" />{table.title || table.caption || table.table_id || `表格 ${index + 1}`}</h3>
            <p>页码 {table.page_number || 1}{table.sheet_name ? ` · ${table.sheet_name}` : ''} · {table.quality?.row_count || 0} 行 · {table.quality?.column_count || 0} 列</p>
            <div className="doc-action-row mt-2 justify-start">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => onFocusTable(tableId, pageNumber(table.page_number))}
              >
                定位原页
              </Button>
              {sourceUrl ? (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  leftIcon={<ExternalLink className="h-4 w-4" />}
                  onClick={() => void openResource(sourceUrl, `${table.table_id || 'table'}.json`)}
                >
                  打开来源
                </Button>
              ) : null}
            </div>
            {table.markdown ? <pre className="doc-table-markdown">{table.markdown}</pre> : null}
          </div>
        )
      }) : <EmptyState icon={Table2} title="暂无表格产物" description="当前任务没有识别到表格。" size="sm" className="min-h-[240px]" />}

      <div className="doc-data-row">
        <h3>表格关系复核</h3>
        <p>跨页断表候选、逻辑合并关系和人工复核结果。</p>
      </div>

      {relationItems.length ? relationItems.map((relation, index) => {
        const id = relationId(relation, index)
        const tableIds = relationTableIds(relation)
        const pages = relationPages(relation, tableById)
        return (
          <div className="doc-data-row" key={id}>
            <h3>{relationTables(relation)}</h3>
            <p>
              {relation.relation_type || relation.merge_status || 'relation'} · 置信度 {relationConfidence(relation)}
              {relation.review_status ? ` · ${relation.review_status}` : ''}
            </p>
            {tableIds.length && isPreviewCrossPageTableRelation(relation, tableById) ? (
              <div className={`doc-relation-flow ${relationFlowTone(relation)}`}>
                {tableIds.map((tableId, nodeIndex) => {
                  const table = tableById.get(tableId)
                  const tablePage = table?.page_number || pages[nodeIndex] || pages[0] || 1
                  return (
                    <div className="doc-relation-step" key={`${id}-${tableId}-${nodeIndex}`}>
                      <button
                        type="button"
                        className="doc-relation-node"
                        onClick={() => onFocusTable(tableId, pageNumber(tablePage))}
                      >
                        <span className="doc-relation-page">p{tablePage}</span>
                        <strong>{tableId}</strong>
                        <span>{tableLabel(table, tableId)}</span>
                        <em>{table?.quality?.row_count || 0} 行 · {table?.quality?.column_count || 0} 列</em>
                      </button>
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
              <Button type="button" size="sm" variant="secondary" onClick={() => void onReviewTableRelation(id, 'accepted')}>
                接受合并
              </Button>
              <Button type="button" size="sm" variant="secondary" onClick={() => void onReviewTableRelation(id, 'rejected')}>
                拒绝合并
              </Button>
            </div>
          </div>
        )
      }) : <EmptyState icon={Table2} title="暂无跨页断表候选" description="当前产物没有返回 table_relations。" size="sm" className="min-h-[160px]" />}
    </div>
  )
}
