import { CheckCircle2, FileText, FolderOpen, Loader2, RefreshCw, Search, Settings2, UploadCloud } from 'lucide-react'
import type { DownloadedPdf, HealthStatus } from '../../lib/pdfTypes'
import { escHtml, formatDateTime, formatSize, isTerminal } from '../../lib/pdfFormatting'

export interface PdfUploadPanelProps {
  health: HealthStatus | null
  selectedFiles: File[]
  setSelectedFiles: (files: File[]) => void
  fileInput: React.RefObject<HTMLInputElement | null>
  startConvert: () => Promise<void>
  uploading: boolean
  uploadActive: boolean
  parseBadge: { cls: string; text: string }
  taskId: string | null
  cancelTask: () => Promise<void>
  error: string | null
  downloadedReports: DownloadedPdf[]
  downloadedQuery: string
  setDownloadedQuery: (q: string) => void
  downloadedLoading: boolean
  downloadedBusyPath: string
  setDownloadedBusyPath: (path: string) => void
  loadDownloadedReports: (text: string) => Promise<void>
  selectDownloadedReport: (report: DownloadedPdf, onBusy: (path: string) => void) => Promise<void>
  parseDownloadedReport: (report: DownloadedPdf, onBusy: (path: string) => void) => Promise<void>
  backend: string
  setBackend: (v: string) => void
  parseMethod: string
  setParseMethod: (v: string) => void
  startPage: string
  setStartPage: (v: string) => void
  endPage: string
  setEndPage: (v: string) => void
  formula: boolean
  setFormula: (v: boolean) => void
  table: boolean
  setTable: (v: boolean) => void
  showConfig: boolean
  setShowConfig: (v: boolean) => void
  handleFiles: (files: FileList | File[]) => void
  dragover: boolean
  setDragover: (v: boolean) => void
}

export function PdfUploadPanel(props: PdfUploadPanelProps) {
  const {
    health,
    selectedFiles,
    setSelectedFiles,
    fileInput,
    startConvert,
    uploading,
    uploadActive,
    parseBadge,
    taskId,
    cancelTask,
    error,
    downloadedReports,
    downloadedQuery,
    setDownloadedQuery,
    downloadedLoading,
    downloadedBusyPath,
    setDownloadedBusyPath,
    loadDownloadedReports,
    selectDownloadedReport,
    parseDownloadedReport,
    backend,
    setBackend,
    parseMethod,
    setParseMethod,
    startPage,
    setStartPage,
    endPage,
    setEndPage,
    formula,
    setFormula,
    table,
    setTable,
    showConfig,
    setShowConfig,
    handleFiles,
    dragover,
    setDragover,
  } = props

  const submitReady = health?.submit_ready ?? false

  return (
    <div className="secondary-panel p-5">
      <div className="pdf-source-choice">
        <div className="pdf-source-choice-head">
          <div>
            <h3 className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5 text-primary" />
              已下载财报
            </h3>
            <p>优先从搜索下载阶段保存的 PDF 中选择，也可以直接发起解析。</p>
          </div>
          <div className="pdf-download-search">
            <label>
              <Search className="h-4 w-4" />
              <input
                value={downloadedQuery}
                onChange={(e) => setDownloadedQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void loadDownloadedReports(downloadedQuery)
                }}
                placeholder="搜索公司、类型或文件名"
              />
            </label>
            <button
              className="pdf-icon-btn"
              onClick={() => loadDownloadedReports(downloadedQuery)}
              disabled={downloadedLoading}
              aria-label="刷新已下载财报"
            >
              {downloadedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span>刷新</span>
            </button>
            <span className="pdf-download-count">{downloadedReports.length ? `${downloadedReports.length} 份` : '无结果'}</span>
          </div>
        </div>
        {downloadedReports.length > 0 ? (
          <>
            <div className="pdf-download-list">
              {downloadedReports.slice(0, 10).map((report) => {
                const busy = downloadedBusyPath === report.relativePath
                return (
                <div key={report.id} className="pdf-download-item">
                  <div className="pdf-download-main">
                    <FileText className="h-5 w-5" />
                    <div className="min-w-0">
                      <div className="pdf-download-title">{report.filename}</div>
                      <div className="pdf-download-meta">
                        <span>{report.company || '未知公司'}</span>
                        <span>{report.category || '未分类'}</span>
                        <span>{formatSize(report.size)}</span>
                        <span>{formatDateTime(report.mtime)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="pdf-download-actions">
                    <button
                      className="pdf-small-action"
                      onClick={() => selectDownloadedReport(report, setDownloadedBusyPath)}
                      disabled={busy || uploading}
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}选择
                    </button>
                    <button
                      className="pdf-small-action primary"
                      onClick={() => parseDownloadedReport(report, setDownloadedBusyPath)}
                      disabled={busy || uploading || !submitReady}
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}解析
                    </button>
                  </div>
                </div>
                )
              })}
            </div>
            {downloadedReports.length > 10 ? (
              <div className="mt-3 rounded-xl border border-border bg-bg/60 px-3 py-2 text-xs leading-5 text-text-muted">
                已显示最近 10 份，共 {downloadedReports.length} 份；输入公司、类型或文件名可继续缩小范围。
              </div>
            ) : null}
          </>
        ) : (
          <div className="rounded-[18px] border border-dashed border-border bg-bg/50 px-4 py-6 text-center text-sm text-text-muted">
            {downloadedLoading ? '正在读取已下载财报...' : '暂无已下载财报，可继续使用本地上传。'}
          </div>
        )}
      </div>

      <div className="pdf-source-separator">或上传本地 PDF</div>
      <div
        className={`pdf-drop-zone ${dragover ? 'dragover' : ''}`}
        onClick={() => fileInput.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragover(true)
        }}
        onDragLeave={() => setDragover(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragover(false)
          if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files)
        }}
      >
        <UploadCloud className="mx-auto mb-3 h-10 w-10 text-slate-500" />
        <p>
          <strong>点击选择 PDF</strong> 或拖拽文件到此处
        </p>
        <p style={{ color: '#64748b', marginTop: 4 }}>一次最多 5 个 PDF，单个最大 100 MB</p>
        {selectedFiles.length === 1 && (
          <div style={{ marginTop: 12, fontWeight: 600 }}>
            {selectedFiles[0].name} ({formatSize(selectedFiles[0].size)})
          </div>
        )}
        {selectedFiles.length > 1 && <div style={{ marginTop: 12, fontWeight: 600 }}>已选择 {selectedFiles.length} 个 PDF</div>}
      </div>
      <input
        ref={fileInput}
        type="file"
        accept=".pdf"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) handleFiles(e.target.files)
          e.target.value = ''
        }}
      />
      {selectedFiles.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: '.84rem', color: '#64748b', marginBottom: 8, fontWeight: 600 }}>本次入队文件</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {selectedFiles.map((f, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 10,
                  alignItems: 'center',
                  border: '1px solid #e2e8f0',
                  borderRadius: 12,
                  background: '#fff',
                  padding: '8px 10px',
                  fontSize: '.88rem',
                }}
              >
                <b style={{ fontWeight: 600, wordBreak: 'break-word' }}>{escHtml(f.name)}</b>
                <span style={{ color: '#64748b', whiteSpace: 'nowrap', fontSize: '.8rem' }}>{formatSize(f.size)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pdf-upload-actions mt-4 flex flex-wrap gap-2.5">
        <button
          onClick={() => void startConvert()}
          disabled={uploading || selectedFiles.length === 0 || !submitReady}
          className="flex h-11 items-center justify-center gap-2 rounded-xl accent-gradient px-5 text-sm font-semibold text-white shadow-md shadow-blue-900/12 hover:brightness-110 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {uploading && <span className="inline-block h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />}
          批量入队
        </button>
        {selectedFiles.length > 0 && (
          <button
            onClick={() => setSelectedFiles([])}
            className="rounded-xl border border-border bg-card px-4 py-3 text-sm font-semibold text-text shadow-sm hover:bg-bg"
          >
            清除
          </button>
        )}
        {taskId && !isTerminal(parseBadge.cls) && uploadActive && (
          <button
            onClick={() => void cancelTask()}
            className="rounded-lg border border-error/20 bg-error/5 px-4 py-2.5 text-sm font-semibold text-error hover:bg-error/10"
          >
            停止查看
          </button>
        )}
      </div>
      {error && <div className="mt-4 rounded-lg border border-error/20 bg-error/5 px-4 py-3 text-sm text-error">{error}</div>}

      {/* Advanced config */}
      <div className="mt-4 overflow-hidden rounded-[18px] border border-border bg-card shadow-sm">
        <button onClick={() => setShowConfig(!showConfig)} className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left">
          <span className="flex items-center gap-2 text-sm font-semibold text-text">
            <Settings2 className="h-4 w-4 text-primary" />
            高级配置
          </span>
          <span className="shrink-0 text-sm font-semibold text-primary">{showConfig ? '收起' : '展开'}</span>
        </button>
        {showConfig && (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4 border-t border-border px-6 py-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-semibold text-text-muted">后端模式</label>
              <select value={backend} onChange={(e) => setBackend(e.target.value)} className="form-control px-4 text-base">
                <option value="hybrid-http-client">hybrid-http-client (推荐)</option>
                <option value="pipeline">pipeline (快速)</option>
                <option value="vlm-http-client">vlm-http-client (高精度)</option>
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-semibold text-text-muted">解析方式</label>
              <select value={parseMethod} onChange={(e) => setParseMethod(e.target.value)} className="form-control px-4 text-base">
                <option value="auto">auto (自动判断)</option>
                <option value="txt">txt (文本提取)</option>
                <option value="ocr">ocr (OCR 识别)</option>
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-semibold text-text-muted">起始页码 (0-based)</label>
              <input
                type="number"
                min="0"
                value={startPage}
                onChange={(e) => setStartPage(e.target.value)}
                placeholder="0"
                className="form-control px-4 text-base"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-semibold text-text-muted">结束页码 (0-based)</label>
              <input
                type="number"
                min="0"
                value={endPage}
                onChange={(e) => setEndPage(e.target.value)}
                placeholder="可选"
                className="form-control px-4 text-base"
              />
            </div>
            <div className="flex items-center gap-3 pt-5">
              <label className="relative inline-flex h-6 w-11 cursor-pointer items-center">
                <input type="checkbox" checked={formula} onChange={(e) => setFormula(e.target.checked)} className="peer sr-only" />
                <div className="h-6 w-11 rounded-full bg-gray-200 peer-checked:bg-primary after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-5" />
              </label>
              <span className="text-sm font-medium">启用公式识别</span>
            </div>
            <div className="flex items-center gap-3 pt-5">
              <label className="relative inline-flex h-6 w-11 cursor-pointer items-center">
                <input type="checkbox" checked={table} onChange={(e) => setTable(e.target.checked)} className="peer sr-only" />
                <div className="h-6 w-11 rounded-full bg-gray-200 peer-checked:bg-primary after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-5" />
              </label>
              <span className="text-sm font-medium">启用表格识别</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
