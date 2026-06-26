import { renderInline } from './InlineRenderer'
import { isSafeLinkHref, normalizeLinkHref, parseCitationActions } from './rendererUtils'
import { handleAuthenticatedSourceClick } from '@/lib/authenticatedSourceLinks'

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
              return (
                <>
                  <div className="chat-citation-text">
                    {renderInline(parsed.text || item, `${blockKey}-${index}`)}
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
                            {action.label}
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
