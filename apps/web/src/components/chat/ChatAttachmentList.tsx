import { useEffect, useState, type MouseEvent } from 'react'
import { ExternalLink, FileText, X } from 'lucide-react'
import { normalizeChatAssetUrl } from '../../lib/chatAssets'
import { fetchWithAuth } from '../../lib/fetchWithAuth'
import type { AgentAttachment } from '../../lib/useAgentChat'

interface ChatAttachmentListProps {
  attachments?: AgentAttachment[]
  composer?: boolean
  onRemove?: (id: string) => void
}

function attachmentHref(item: AgentAttachment) {
  const fallbackName = (item.path || '').match(/(?:^|[/\\])chat_uploads[/\\]([^/\\]+)$/)?.[1]
  const rawUrl = item.url || (fallbackName ? `/api/chat/attachments/${encodeURIComponent(fallbackName)}` : '')
  return normalizeChatAssetUrl(rawUrl)
}

interface ImagePreviewState {
  item: AgentAttachment
  href: string
}

function isInlineAssetHref(href: string) {
  return href.startsWith('blob:') || href.startsWith('data:') || href.startsWith('#')
}

function AuthImageAttachment({ item, onPreviewImage }: { item: AgentAttachment; onPreviewImage: (preview: ImagePreviewState) => void }) {
  const href = attachmentHref(item)
  const inlineHref = !href || isInlineAssetHref(href)
  const [imageState, setImageState] = useState<{ sourceHref: string; displayHref: string; failed: boolean } | null>(null)

  useEffect(() => {
    if (inlineHref) {
      return
    }
    let cancelled = false
    let objectUrl = ''
    fetchWithAuth(href)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.blob()
      })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setImageState({ sourceHref: href, displayHref: objectUrl, failed: false })
      })
      .catch(() => {
        if (!cancelled) {
          setImageState({ sourceHref: href, displayHref: '', failed: true })
        }
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [href, inlineHref])

  const currentState = imageState?.sourceHref === href ? imageState : null
  const displayHref = inlineHref ? href : currentState?.displayHref || ''
  const failed = !inlineHref && currentState?.failed

  if (failed) {
    return (
      <div className="chat-attachment-thumb chat-attachment-thumb-fallback" title={item.filename}>
        图片加载失败
      </div>
    )
  }

  const image = displayHref ? (
      <img
        src={displayHref}
        alt={item.filename}
        width={68}
        height={68}
        className="chat-attachment-thumb"
        loading="lazy"
        decoding="async"
    />
  ) : (
    <div className="chat-attachment-thumb chat-attachment-thumb-loading" aria-label={`${item.filename} 加载中`} />
  )
  return (
    <button
      type="button"
      className="chat-attachment-image-link"
      aria-label={`查看图片 ${item.filename}`}
      title="查看图片"
      disabled={!displayHref}
      onClick={() => displayHref && onPreviewImage({ item, href: displayHref })}
    >
      {image}
    </button>
  )
}

function AttachmentItem({ item, composer, onPreviewImage }: { item: AgentAttachment; composer: boolean; onPreviewImage: (preview: ImagePreviewState) => void }) {
  const href = attachmentHref(item)
  if (item.kind === 'image') {
    return <AuthImageAttachment item={item} onPreviewImage={onPreviewImage} />
  }

  const openDocument = async (event: MouseEvent<HTMLAnchorElement>) => {
    if (!href || isInlineAssetHref(href)) return
    event.preventDefault()
    try {
      const response = await fetchWithAuth(href)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      window.open(objectUrl, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
    } catch {
      window.open(href, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={`chat-attachment-file ${composer ? 'chat-attachment-file-preview' : ''}`}
      onClick={openDocument}
    >
      <FileText className="h-4 w-4" />
      <span className="chat-attachment-file-name">{item.filename}</span>
    </a>
  )
}

export default function ChatAttachmentList({ attachments = [], composer = false, onRemove }: ChatAttachmentListProps) {
  const [preview, setPreview] = useState<ImagePreviewState | null>(null)

  useEffect(() => {
    if (!preview) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreview(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [preview])

  if (!attachments.length) return null

  return (
    <>
      <div className={`chat-attachment-grid ${composer ? 'chat-attachment-grid-composer' : ''}`}>
        {attachments.map((item) => (
          composer ? (
            <div key={item.id} className="chat-attachment-preview">
              <AttachmentItem item={item} composer={composer} onPreviewImage={setPreview} />
              {onRemove ? (
                <button
                  type="button"
                  className="chat-attachment-remove"
                  onClick={() => onRemove(item.id)}
                  aria-label={`移除 ${item.filename || '附件'}`}
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </div>
          ) : (
            <div key={item.id} className="chat-attachment-item">
              <AttachmentItem item={item} composer={composer} onPreviewImage={setPreview} />
            </div>
          )
        ))}
      </div>
      {preview ? (
        <div
          className="chat-attachment-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`图片预览 ${preview.item.filename}`}
          onClick={() => setPreview(null)}
        >
          <div className="chat-attachment-lightbox-panel" onClick={(event) => event.stopPropagation()}>
            <div className="chat-attachment-lightbox-toolbar">
              <span className="chat-attachment-lightbox-title">{preview.item.filename}</span>
              <a
                href={preview.href}
                target="_blank"
                rel="noreferrer"
                className="chat-attachment-lightbox-action"
                aria-label={`新窗口打开 ${preview.item.filename}`}
              >
                <ExternalLink className="h-4 w-4" />
              </a>
              <button
                type="button"
                className="chat-attachment-lightbox-action"
                onClick={() => setPreview(null)}
                aria-label="关闭图片预览"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <img
              src={preview.href}
              alt={preview.item.filename}
              width={960}
              height={720}
              className="chat-attachment-lightbox-image"
            />
          </div>
        </div>
      ) : null}
    </>
  )
}
