import type {
  DocumentBlocksPayload,
  DocumentFiguresPayload,
  DocumentManifest,
  DocumentSourceMapPayload,
  DocumentTablesPayload,
} from '@/lib/documentTypes'
import { stringify } from './documentResultWorkbenchUtils'

export function DocumentResultJsonPane({
  manifest,
  blocks,
  tables,
  figures,
  sourceMap,
}: {
  manifest?: DocumentManifest | null
  blocks: DocumentBlocksPayload | null
  tables: DocumentTablesPayload | null
  figures: DocumentFiguresPayload | null
  sourceMap: DocumentSourceMapPayload | null
}) {
  return <pre className="doc-json">{stringify({ manifest, blocks, tables, figures, sourceMap })}</pre>
}
