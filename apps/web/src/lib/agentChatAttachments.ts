import type { AgentAttachment } from './agentChatTypes'

export const MAX_ATTACHMENTS = 6
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
export const MAX_DOCUMENT_ATTACHMENT_BYTES = 25 * 1024 * 1024

export const SUPPORTED_DOCUMENT_TYPES = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
  'text/markdown',
  'text/x-markdown',
  'text/plain',
  'text/csv',
  'application/json',
  'application/rtf',
  'text/rtf',
])

export const DOCUMENT_CONTENT_TYPES_BY_EXT: Record<string, string> = {
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  doc: 'application/msword',
  md: 'text/markdown',
  markdown: 'text/markdown',
  txt: 'text/plain',
  csv: 'text/csv',
  json: 'application/json',
  rtf: 'application/rtf',
}

export function extensionOf(filename: string) {
  return (filename.split('.').pop() || '').toLowerCase()
}

export function inferredContentType(file: File) {
  return file.type || DOCUMENT_CONTENT_TYPES_BY_EXT[extensionOf(file.name)] || 'application/octet-stream'
}

export function isSupportedAttachment(file: File) {
  const type = inferredContentType(file)
  return type.startsWith('image/') || SUPPORTED_DOCUMENT_TYPES.has(type)
}

export function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })
}

export function createTempAttachmentId() {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? `tmp-${crypto.randomUUID()}`
    : `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function stripRenderedAttachmentMarkdown(content: string, attachments?: AgentAttachment[] | null) {
  if (!attachments?.length) return content
  const attachmentNames = new Set(attachments.map((item) => item.filename).filter(Boolean))
  return content
    .split('\n')
    .filter((line) => {
      const text = line.trim()
      const imageMatch = text.match(/^!\[(?:图片|image):\s*([^\]]+)\]\((?:\/api\/chat\/attachments\/|https?:\/\/[^)]+\/api\/chat\/attachments\/)[^)]+\)$/i)
      if (imageMatch) return !attachmentNames.has(imageMatch[1])
      const fileMatch = text.match(/^\[(?:文档|document):\s*([^\]]+)\]\((?:\/api\/chat\/attachments\/|https?:\/\/[^)]+\/api\/chat\/attachments\/)[^)]+\)$/i)
      if (fileMatch) return !attachmentNames.has(fileMatch[1])
      const audioMatch = text.match(/^\[(?:语音|audio):\s*([^\]]+)\]\((?:\/api\/chat\/attachments\/|https?:\/\/[^)]+\/api\/chat\/attachments\/)[^)]+\)$/i)
      if (audioMatch) return !attachmentNames.has(audioMatch[1])
      return true
    })
    .join('\n')
    .trim()
}

export interface AttachmentUploadItem {
  previewUrl: string
  tempAttachment: AgentAttachment
  payloadPromise: Promise<{ filename: string; content_type: string; data_url: string }>
}

export function validateAndSelectAttachments(files: FileList | File[], currentAttachmentCount: number) {
  const incoming = Array.from(files).filter(Boolean)
  if (!incoming.length) return []
  const available = Math.max(0, MAX_ATTACHMENTS - currentAttachmentCount)
  if (available <= 0) throw new Error(`每条消息最多上传 ${MAX_ATTACHMENTS} 个附件`)

  const selected = incoming.slice(0, available)
  for (const file of selected) {
    const contentType = inferredContentType(file)
    if (!isSupportedAttachment(file)) throw new Error('目前支持图片、PDF、Word、Markdown、TXT、CSV、JSON 和 RTF')
    const maxBytes = contentType.startsWith('image/') ? MAX_ATTACHMENT_BYTES : MAX_DOCUMENT_ATTACHMENT_BYTES
    if (file.size > maxBytes) {
      const limit = Math.floor(maxBytes / 1024 / 1024)
      throw new Error(`单个附件不能超过 ${limit}MB`)
    }
  }
  return selected
}

export function buildAttachmentUploadItems(selected: File[]): AttachmentUploadItem[] {
  return selected.map((file) => {
    const contentType = inferredContentType(file)
    const previewUrl = URL.createObjectURL(file)
    const tempAttachment: AgentAttachment = {
      id: createTempAttachmentId(),
      filename: file.name || 'image',
      content_type: contentType,
      size: file.size,
      path: file.name || 'local-file',
      url: previewUrl,
      kind: contentType.startsWith('image/') ? 'image' : 'document',
    }
    return {
      previewUrl,
      tempAttachment,
      payloadPromise: readFileAsDataUrl(file).then((data_url) => ({
        filename: file.name || 'image',
        content_type: contentType,
        data_url,
      })),
    }
  })
}
