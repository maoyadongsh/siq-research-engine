import { EmptyState } from '@/components/page'
import { FileText } from 'lucide-react'
import {
  hasFocusedKey,
  type FocusTarget,
  type MarkdownBlock,
} from './documentResultWorkbenchUtils'

export type DocumentMarkdownPaneProps = {
  blocks: MarkdownBlock[]
  activeFocusKeys: Set<string>
  emptyTitle: string
  emptyDescription: string
  onFocusBlock: (target: NonNullable<FocusTarget>) => void
}

export function DocumentMarkdownPane({
  blocks,
  activeFocusKeys,
  emptyTitle,
  emptyDescription,
  onFocusBlock,
}: DocumentMarkdownPaneProps) {
  return blocks.length ? blocks.map((block) => {
    const focusTarget = { kind: 'block', id: block.id, page: block.pageNumber } as const
    const isFocused = hasFocusedKey(block.focusKeys, activeFocusKeys)
    return (
      <article
        role="button"
        tabIndex={0}
        className={`doc-md-block ${isFocused ? 'is-focused' : ''}`}
        key={block.id}
        data-focus-keys={block.focusKeys.join(' ')}
        onClick={() => onFocusBlock(focusTarget)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onFocusBlock(focusTarget)
          }
        }}
      >
        <span className="doc-md-block-meta">p{block.pageNumber} · {block.title}</span>
        <div className="doc-md-html" dangerouslySetInnerHTML={{ __html: block.html }} />
      </article>
    )
  }) : (
    <EmptyState
      icon={FileText}
      title={emptyTitle}
      description={emptyDescription}
      size="sm"
      className="min-h-[240px]"
    />
  )
}
