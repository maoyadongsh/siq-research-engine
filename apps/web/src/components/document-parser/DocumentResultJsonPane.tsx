import type { DocumentResultJsonPreview } from './documentResultWorkbenchDerivations'
import { stringify } from './documentResultWorkbenchUtils'

export function DocumentResultJsonPane({
  preview,
}: {
  preview: DocumentResultJsonPreview
}) {
  return <pre className="doc-json">{stringify(preview)}</pre>
}
