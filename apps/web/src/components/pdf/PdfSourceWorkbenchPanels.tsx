import type { FocusEvent, MouseEvent, ReactNode, RefObject } from 'react'
import { BookOpen, ChevronLeft, ChevronRight, ExternalLink, FileText, Save } from 'lucide-react'
import { cn } from '@/lib/utils'
import { artifactUrl } from '../../features/pdf-parsing/api'
import type { ArtifactsMap, SourceCorrection, SourceMeta, SourceTable } from '../../lib/pdfTypes'

export type PdfReviewMobileTab = 'pdf' | 'md'
export type ReviewStatusOption = readonly [string, string]

export function PdfSourceSummary({ srcTable }: { srcTable: SourceTable }) {
  return (
    <>
      <div className="pdf-source-summary">
        <div>
          <strong>表 {srcTable.table_index || '--'}</strong>
          <span>Markdown 行 {srcTable.line || '-'}</span>
        </div>
        <div>
          <strong>{srcTable.rows || 0}</strong>
          <span>行</span>
        </div>
        <div>
          <strong>{srcTable.pdf_page_number || '--'}</strong>
          <span>
            PDF 页码{srcTable.pdf_page_source === 'markdown_marker_inferred' ? '（推断）' : ''}
          </span>
        </div>
        <div>
          <strong>{srcTable.cells || 0}</strong>
          <span>单元格</span>
        </div>
        <div>
          <strong>{Math.round((srcTable.empty_ratio || 0) * 1000) / 10}%</strong>
          <span>空单元格</span>
        </div>
        <div>
          <strong>{Math.round((srcTable.numeric_ratio || 0) * 1000) / 10}%</strong>
          <span>数字密度</span>
        </div>
      </div>

      <div className="pdf-source-meta">
        <span>附近标题</span>
        <b>{srcTable.heading || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>单位</span>
        <b>{srcTable.unit || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>命中类别</span>
        <b>{(srcTable.matched_financial_names || []).join('、') || '普通表'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>PDF 坐标 bbox</span>
        <b>{(srcTable.bbox || []).join(', ') || '未识别'}</b>
      </div>
      <div className="pdf-source-meta">
        <span>页面截图</span>
        <b>{srcTable.source_image_path || '未识别'}</b>
      </div>
    </>
  )
}

export function PdfMobileReviewTabs({
  mobileTab,
  onChange,
}: {
  mobileTab: PdfReviewMobileTab
  onChange: (tab: PdfReviewMobileTab) => void
}) {
  return (
    <div className="pdf-mobile-review-tabs">
      <button
        type="button"
        onClick={() => onChange('pdf')}
        className={cn(
          'flex flex-1 items-center justify-center gap-1.5 rounded-lg py-2 text-sm font-semibold transition-colors',
          mobileTab === 'pdf' ? 'bg-primary text-white' : 'text-text-muted hover:text-text',
        )}
      >
        <FileText className="h-4 w-4" />
        PDF 原页
      </button>
      <button
        type="button"
        onClick={() => onChange('md')}
        className={cn(
          'flex flex-1 items-center justify-center gap-1.5 rounded-lg py-2 text-sm font-semibold transition-colors',
          mobileTab === 'md' ? 'bg-primary text-white' : 'text-text-muted hover:text-text',
        )}
      >
        <BookOpen className="h-4 w-4" />
        Markdown
      </button>
    </div>
  )
}

export function PdfReviewComparePane({ children }: { children: ReactNode }) {
  return (
    <div className="pdf-workbench" aria-label="PDF 复核对照工作台">
      {children}
    </div>
  )
}

export function PdfReviewPdfPane({
  mobileTab,
  currentPage,
  pageCount,
  pdfZoom,
  setPdfZoom,
  onPageChange,
  children,
}: {
  mobileTab: PdfReviewMobileTab
  currentPage: number
  pageCount: number
  pdfZoom: string
  setPdfZoom: (zoom: string) => void
  onPageChange: (page: number) => void
  children: ReactNode
}) {
  return (
    <div className={cn('pdf-source-block pdf-source-pane', mobileTab !== 'pdf' && 'pdf-mobile-hidden')}>
      <div className="pdf-source-pane-head">
        <div>
          <h4>PDF 原页</h4>
          <p>PDF 第 {currentPage} / {pageCount} 页</p>
        </div>
        <div className="pdf-page-toolbar-actions">
          <div className="pdf-page-nav">
            <button
              type="button"
              className="pdf-nav-btn"
              disabled={currentPage <= 1}
              onClick={() => onPageChange(currentPage - 1)}
              aria-label="上一页"
              title="上一页"
            >
              <ChevronLeft size={15} />
            </button>
            <input
              className="pdf-page-input"
              type="number"
              min={1}
              max={pageCount}
              value={currentPage}
              aria-label="PDF 页码"
              onChange={(e) => onPageChange(Number(e.target.value))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onPageChange(Number((e.target as HTMLInputElement).value))
              }}
            />
            <button
              type="button"
              className="pdf-nav-btn"
              disabled={currentPage >= pageCount}
              onClick={() => onPageChange(currentPage + 1)}
              aria-label="下一页"
              title="下一页"
            >
              <ChevronRight size={15} />
            </button>
          </div>
          <div className="pdf-zoom-controls" aria-label="PDF 缩放">
            {(['50', '100', '150'] as const).map((zoom) => (
              <button
                key={zoom}
                type="button"
                className={`pdf-zoom-btn ${pdfZoom === zoom ? 'active' : ''}`}
                onClick={() => setPdfZoom(zoom)}
                aria-pressed={pdfZoom === zoom}
              >
                {zoom}%
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="pdf-pdf-page-stack" data-zoom={pdfZoom}>
        {children}
      </div>
    </div>
  )
}

export function PdfReviewReadingPane({
  mobileTab,
  readingMode,
  switchReadingMode,
  currentPage,
  readingHtml,
  editTableRef,
  markdownPaneRef,
  onTableClick,
  onTableFocus,
  onTableInput,
  onReadingClick,
  children,
}: {
  mobileTab: PdfReviewMobileTab
  readingMode: 'table' | 'page'
  switchReadingMode: (mode: 'table' | 'page') => void | Promise<void>
  currentPage: number
  readingHtml: string
  editTableRef: RefObject<HTMLDivElement | null>
  markdownPaneRef: RefObject<HTMLDivElement | null>
  onTableClick: (e: MouseEvent<HTMLDivElement>) => void
  onTableFocus: (e: FocusEvent<HTMLDivElement>) => void
  onTableInput: () => void
  onReadingClick: (e: MouseEvent<HTMLDivElement>) => void
  children: ReactNode
}) {
  return (
    <div className={cn('pdf-source-block pdf-source-pane', mobileTab !== 'md' && 'pdf-mobile-hidden')}>
      <div className="pdf-source-pane-head">
        <div className="pdf-reading-topline">
          <div>
            <h4>Markdown</h4>
            <p>PDF 第 {currentPage} 页</p>
          </div>
          <div className="pdf-reading-mode-switch">
            <button
              type="button"
              className={`pdf-reading-mode-btn ${readingMode === 'page' ? 'active' : ''}`}
              onClick={() => void switchReadingMode('page')}
            >
              页面
            </button>
            <button
              type="button"
              className={`pdf-reading-mode-btn ${readingMode === 'table' ? 'active' : ''}`}
              onClick={() => void switchReadingMode('table')}
            >
              表格
            </button>
          </div>
        </div>
      </div>

      {readingMode === 'table' ? (
        <div
          className="pdf-table-wrap pdf-editable scroll-hint"
          ref={editTableRef}
          onClick={onTableClick}
          onFocus={onTableFocus}
          onInput={onTableInput}
          onBlur={onTableInput}
          dangerouslySetInnerHTML={{ __html: readingHtml }}
        />
      ) : (
        <div className="pdf-md-render" ref={markdownPaneRef} onClick={onReadingClick}>
          {children}
        </div>
      )}
    </div>
  )
}

export function PdfReviewCorrectionPane({
  corr,
  srcTable,
  statusOptions,
  corrStatusRef,
  corrTextRef,
  corrNoteRef,
  saveCorrection,
}: {
  corr: SourceCorrection
  srcTable: SourceTable
  statusOptions: readonly ReviewStatusOption[]
  corrStatusRef: RefObject<HTMLSelectElement | null>
  corrTextRef: RefObject<HTMLTextAreaElement | null>
  corrNoteRef: RefObject<HTMLTextAreaElement | null>
  saveCorrection: () => Promise<void>
}) {
  return (
    <div className="pdf-source-block">
      <h4>人工复核修正</h4>
      <div className="pdf-correction-toolbar">
        <label>
          状态
          <select ref={corrStatusRef} defaultValue={corr.review_status || 'unreviewed'}>
            {statusOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="pdf-trace-btn inline-flex items-center gap-1" onClick={() => void saveCorrection()}>
          <Save size={13} />
          保存修正
        </button>
        <span className="text-text-muted text-sm">{corr.updated_at ? `上次保存: ${corr.updated_at}` : ''}</span>
      </div>
      <textarea
        ref={corrTextRef}
        className="pdf-correction-editor"
        spellCheck={false}
        defaultValue={corr.table_markdown || srcTable.table_html || ''}
      />
      <textarea
        ref={corrNoteRef}
        className="pdf-correction-note"
        placeholder="复核备注，例如：第 3 列金额错位，应以 PDF 第 67 页为准。"
        defaultValue={corr.note || ''}
      />
    </div>
  )
}

export function PdfMarkdownContextPane({ excerpt }: { excerpt: SourceMeta['excerpt'] }) {
  if (!excerpt.length) return null
  return (
    <div className="pdf-source-block">
      <h4>Markdown 上下文</h4>
      {excerpt.map((item, index) => (
        <div key={index} className={`pdf-source-line ${item.focus ? 'focus' : ''}`}>
          <span>{item.line}</span>
          <code>{item.text || ' '}</code>
        </div>
      ))}
    </div>
  )
}

export function PdfArtifactPane({ artifacts }: { artifacts: ArtifactsMap }) {
  if (!Object.keys(artifacts).length) return null
  return (
    <div className="pdf-source-block">
      <h4>产物文件</h4>
      {Object.entries(artifacts).map(([name, info]) => (
        <div key={name} className={`pdf-artifact-row ${info.exists ? 'ok' : 'missing'}`}>
          <span>{name}</span>
          <code>{info.path || '未生成'}</code>
          {info.exists && info.url ? (
            <a className="pdf-trace-btn inline-flex items-center gap-1" href={artifactUrl(info)} target="_blank" rel="noopener">
              <ExternalLink size={13} />
              打开
            </a>
          ) : null}
        </div>
      ))}
    </div>
  )
}
