import { useRef, useState } from 'react'
import { FileUp, FolderInput, Link2, Loader2, RefreshCw, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { DocumentMineruImportCandidate, DocumentParseConfig } from '@/lib/documentTypes'

const supported = 'PDF、图片、Word、PPT、Excel、HTML、TXT、MD、网页 URL'

export function DocumentUploadPanel({
  config,
  uploading,
  mineruCandidates,
  onSubmitFiles,
  onSubmitUrl,
  onImportMineruResult,
  onRefreshMineruCandidates,
}: {
  config: DocumentParseConfig
  uploading: boolean
  mineruCandidates?: DocumentMineruImportCandidate[]
  onSubmitFiles: (files: File[], config: DocumentParseConfig) => Promise<void>
  onSubmitUrl: (url: string, config: DocumentParseConfig) => Promise<void>
  onImportMineruResult: (sourceDir: string, config: DocumentParseConfig) => Promise<void>
  onRefreshMineruCandidates: () => Promise<void>
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [url, setUrl] = useState('')
  const [mineruDir, setMineruDir] = useState('')
  const [dragover, setDragover] = useState(false)

  const pickFiles = (incoming: FileList | File[]) => {
    const next = Array.from(incoming)
    if (!next.length) return
    setFiles(next)
  }

  const submitFiles = async () => {
    await onSubmitFiles(files, config)
    setFiles([])
    if (inputRef.current) inputRef.current.value = ''
  }

  const submitUrl = async () => {
    await onSubmitUrl(url, config)
    setUrl('')
  }

  const importMineruResult = async () => {
    await onImportMineruResult(mineruDir, config)
    setMineruDir('')
  }

  return (
    <section className="doc-panel">
      <div className="doc-panel-head">
        <div>
          <h2>上传与来源</h2>
          <p>{supported}</p>
        </div>
      </div>
      <div className="doc-panel-body grid gap-3">
        <button
          type="button"
          className={`doc-drop ${dragover ? 'is-dragover' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault()
            setDragover(true)
          }}
          onDragLeave={() => setDragover(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragover(false)
            pickFiles(event.dataTransfer.files)
          }}
        >
          <FileUp className="h-8 w-8" />
          <strong>拖拽文件到这里，或点击选择</strong>
          <span>一次可上传多份文件；当前后端默认单文件 200 MB。</span>
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.png,.jpg,.jpeg,.jp2,.webp,.gif,.bmp,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.html,.htm,.txt,.md,.markdown"
          onChange={(event) => pickFiles(event.target.files || [])}
        />

        {files.length ? (
          <div className="doc-file-list">
            {files.map((file) => (
              <div className="doc-file-pill" key={`${file.name}-${file.size}`}>
                <span>{file.name}</span>
                <button type="button" aria-label={`移除 ${file.name}`} onClick={() => setFiles((prev) => prev.filter((item) => item !== file))}>
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <Button type="button" onClick={submitFiles} disabled={!files.length || uploading} leftIcon={uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}>
          上传解析
        </Button>

        <div className="doc-field">
          <label className="doc-label" htmlFor="doc-url">网页或文件 URL</label>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
            <input
              id="doc-url"
              className="doc-input"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/document.pdf"
            />
            <Button type="button" variant="secondary" onClick={submitUrl} disabled={!url.trim() || uploading} leftIcon={<Link2 className="h-4 w-4" />}>
              解析 URL
            </Button>
          </div>
        </div>

        <div className="doc-field">
          <div className="flex items-center justify-between gap-2">
            <label className="doc-label" htmlFor="doc-mineru-dir">已解析 MinerU 目录</label>
            <Button type="button" variant="ghost" size="sm" onClick={() => void onRefreshMineruCandidates()} leftIcon={<RefreshCw className="h-4 w-4" />}>
              刷新候选
            </Button>
          </div>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
            <input
              id="doc-mineru-dir"
              className="doc-input"
              value={mineruDir}
              onChange={(event) => setMineruDir(event.target.value)}
              placeholder="/home/maoyd/siq-research-engine/data/pdf-parser/results/..."
            />
            <Button type="button" variant="secondary" onClick={importMineruResult} disabled={!mineruDir.trim() || uploading} leftIcon={<FolderInput className="h-4 w-4" />}>
              导入
            </Button>
          </div>
          {mineruCandidates?.length ? (
            <div className="doc-file-list">
              {mineruCandidates.slice(0, 3).map((candidate) => {
                const sourceDir = candidate.source_dir || candidate.sourceDir || ''
                return (
                  <button
                    type="button"
                    className="doc-file-pill"
                    key={sourceDir}
                    onClick={() => setMineruDir(sourceDir)}
                    title={sourceDir}
                  >
                    <span>{candidate.title || sourceDir}</span>
                  </button>
                )
              })}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}
