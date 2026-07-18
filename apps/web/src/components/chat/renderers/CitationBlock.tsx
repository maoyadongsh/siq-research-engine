import { renderInline } from './InlineRenderer'
import { isSafeLinkHref, normalizeLinkHref, parseCitationActions } from './rendererUtils'
import { handleAuthenticatedSourceClick } from '@/lib/authenticatedSourceLinks'

function citationSummary(text: string, index: number) {
  const sourceType = text.match(/source_type=([^,\s]+)/i)?.[1] || ''
  const metric = text.match(/metric=([^,]+)/i)?.[1]?.trim() || ''
  const period = text.match(/period=([^,\s]+)/i)?.[1] || ''
  const page = text.match(/(?:pdf_page|printed_page)=([0-9]+)/i)?.[1] || ''
  const sourceLabel = sourceType.includes('metrics')
    ? '三表数据'
    : sourceType.includes('document')
      ? '报告原文'
      : sourceType.includes('wiki')
        ? '研究数据'
        : '证据来源'
  const metricLabel = metric.replace(/^\(?\d+\)?[.、]?\s*/, '').slice(0, 24)
  return [`来源 ${index + 1}`, sourceLabel, metricLabel, period, page ? `P${page}` : '']
    .filter(Boolean)
    .join(' · ')
}

function citationActionLabel(kind: 'pdf' | 'source' | 'table' | 'other') {
  if (kind === 'pdf') return 'PDF'
  if (kind === 'source') return '原文'
  if (kind === 'table') return '表格'
  return '打开'
}

export function CitationBlock({ lines, blockKey }: { lines: string[]; blockKey: string }) {
  const items = lines.map((line) => line.trim()).filter(Boolean)

  return (
    <section key={blockKey} className="chat-citation-block" aria-label="引用来源">
      <div className="chat-citation-title">引用来源</div>
      <div className="chat-citation-list">
        {items.length ? items.map((item, index) => (
          <div
            key={`${blockKey}-${index}`}
            className={`chat-citation-item ${/^\[\d+\]/.test(item) ? 'chat-citation-numbered' : ''}`}
          >
            {(() => {
              const parsed = parseCitationActions(item)
              const summary = citationSummary(parsed.text || item, index)
              return (
                <>
                  <div className="chat-citation-text" title={parsed.text || item}>
                    {renderInline(summary, `${blockKey}-${index}`)}
                  </div>
                  {parsed.actions.length > 0 && (
                    <div className="chat-citation-actions" aria-label="来源操作">
                      {parsed.actions.map((action, actionIndex) => (
                        isSafeLinkHref(action.href) ? (
                          <a
                            key={`${blockKey}-${index}-${actionIndex}`}
                            href={normalizeLinkHref(action.href)}
                            target="_blank"
                            rel="noreferrer"
                            className={`chat-citation-action chat-citation-action-${action.kind}`}
                            onClick={(event) => {
                              handleAuthenticatedSourceClick(event.nativeEvent, action.href).catch((error) => {
                                console.warn('Failed to open authenticated source link', error)
                              })
                            }}
                          >
                            {citationActionLabel(action.kind)}
                          </a>
                        ) : null
                      ))}
                    </div>
                  )}
                </>
              )
            })()}
          </div>
        )) : (
          <div className="chat-citation-item">未提供引用来源。</div>
        )}
      </div>
    </section>
  )
}
