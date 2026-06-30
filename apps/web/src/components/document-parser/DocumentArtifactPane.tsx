import { ExternalLink, FileJson } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DocumentArtifactInfo } from '@/lib/documentTypes'

type DocumentArtifactPaneProps = {
  entries: Array<[string, DocumentArtifactInfo]>
  taskId: string
  onOpenArtifact: (name: string, info: DocumentArtifactInfo) => void
}

export function DocumentArtifactPane({
  entries,
  taskId,
  onOpenArtifact,
}: DocumentArtifactPaneProps) {
  return (
    <div className="doc-artifact-list">
      {entries.map(([name, info]) => (
        <div className="doc-data-row" key={name}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3><FileJson className="mr-2 inline h-4 w-4" />{name}</h3>
              <p>{info.exists ? `${info.size || 0} bytes` : '缺失'}</p>
            </div>
            {info.exists && taskId ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                leftIcon={<ExternalLink className="h-4 w-4" />}
                onClick={() => onOpenArtifact(name, info)}
              >
                打开
              </Button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}
