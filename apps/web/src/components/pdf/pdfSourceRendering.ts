import type { PageBlock, PageContent } from '../../lib/pdfTypes'
import { sanitizeReadingHtml, sanitizeTableHtml } from '../../lib/pdfSanitize'

function escHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function pageNumberOf(pd: PageContent) {
  const parsed = Number(pd.page_number || pd.pdf_page_number || 1)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 1
}

function bboxAttrValue(value: unknown) {
  if (Array.isArray(value) && value.length === 4) return value.map((item) => Number(item)).join(', ')
  if (typeof value === 'string' && value.trim()) return value.trim()
  return ''
}

function textList(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value]
  return values.map((item) => String(item ?? '').trim()).filter(Boolean)
}

function renderFootnotes(value: unknown, label = '注释') {
  const notes = textList(value)
  if (!notes.length) return ''
  return `<div class="pdf-page-block-footnotes"><span class="pdf-page-block-footnote-label">${escHtml(label)}</span>${notes
    .map((note) => `<p>${escHtml(note)}</p>`)
    .join('')}</div>`
}

function blockAttrs(b: PageBlock, index: number, pageNumberValue: number) {
  const bbox = bboxAttrValue(b?.bbox)
  const blockId = b.block_id || `p${pageNumberValue}-b${index + 1}`
  const type = b?.type || 'unknown'
  const focusKey = `${pageNumberValue}:${blockId}`
  const attrs = [
    `data-block-id="${escHtml(blockId)}"`,
    `data-focus-key="${escHtml(focusKey)}"`,
    `data-focus-keys="block:${escHtml(blockId)} ${escHtml(focusKey)}"`,
    `data-block-index="${index + 1}"`,
    `data-block-type="${escHtml(type)}"`,
    `data-page-number="${pageNumberValue}"`,
    bbox ? `data-bbox="${escHtml(bbox)}"` : '',
    b.table_index ? `data-ptidx="${b.table_index}" data-table-index="${b.table_index}"` : '',
    b.source_table_index ? `data-source-table-index="${b.source_table_index}"` : '',
  ]
  return attrs.filter(Boolean).join(' ')
}

function renderBlock(b: PageBlock, index: number, pageNumberValue: number): string {
  const type = b?.type || 'unknown'
  const bboxValue = bboxAttrValue(b?.bbox)
  const attrs = blockAttrs(b, index, pageNumberValue)
  const bboxText = bboxValue ? `bbox: ${bboxValue}` : ''
  const blockId = b.block_id || `p${pageNumberValue}-b${index + 1}`
  const blockMeta = `p${pageNumberValue} · ${blockId} · ${type}`
  if (type === 'table') {
    const label = b.table_index ? `表 ${b.table_index}` : '表格块'
    const tags = ([] as (string | unknown)[])
      .concat(Array.isArray(b.heading) ? b.heading : [b.heading])
      .concat(Array.isArray(b.matched_financial_names) ? b.matched_financial_names : [])
      .filter(Boolean)
      .slice(0, 3)
      .map((t) => `<span class="pdf-page-block-tag">${escHtml(String(t))}</span>`)
      .join('')
    const action = b.table_index ? `<button class="pdf-trace-btn" data-ptidx="${b.table_index}">打开该表</button>` : ''
    return `<section class="pdf-page-block ${b.is_focus_table ? 'focus-table' : ''}" ${attrs}><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(blockMeta)}</span><span class="pdf-page-block-meta">${escHtml(bboxText || label)}</span></div>${action}</div><div class="pdf-page-block-tag-row">${tags}</div><div class="pdf-table-wrap pdf-page-table-wrap">${b.table_html ? sanitizeTableHtml(b.table_html) : '<div style="color:#64748b">表格区域，无可用 HTML。</div>'}</div>${renderFootnotes(b.footnote)}</section>`
  }
  if (type === 'list') {
    const items = (b.list_items || []).map((item: unknown) => `<li>${escHtml(String(item || ''))}</li>`).join('')
    return `<section class="pdf-page-block" ${attrs}><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(blockMeta)}</span><span class="pdf-page-block-meta">${escHtml(bboxText || '列表解析块')}</span></div></div><ul class="pdf-page-block-list">${items}</ul></section>`
  }
  if (type === 'image') {
    return `<section class="pdf-page-block pdf-page-block-muted" ${attrs}><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(blockMeta)}</span><span class="pdf-page-block-meta">${escHtml(bboxText || '图片解析块')}</span></div></div><div class="pdf-page-block-text" style="color:#64748b">来源图像：${escHtml(b.image_path || '未提供路径')}</div>${renderFootnotes(b.footnote, '图片注释')}</section>`
  }
  const headingLike = type === 'header' || Number(b.text_level || 0) > 0
  const label = type === 'header' ? '页眉' : type === 'page_number' ? '页码' : headingLike ? '标题' : '文本'
  return `<section class="pdf-page-block ${type === 'page_number' || type === 'header' ? 'pdf-page-block-muted' : ''}" ${attrs}><div class="pdf-page-block-head"><div><span class="pdf-page-block-type">${escHtml(blockMeta)}</span><span class="pdf-page-block-meta">${escHtml(bboxText || label)}</span></div></div><div class="pdf-page-block-text ${headingLike ? 'pdf-page-block-heading' : ''}">${escHtml(b.text || b.markdown || ' ')}</div></section>`
}

export function renderPageContentHtml(pd: PageContent | null | undefined): string {
  if (!pd) return ''
  const pageNumberValue = pageNumberOf(pd)
  const pageTables = pd.page_tables || []
  const chips = pageTables.length
    ? pageTables
        .map(
          (t) =>
            `<button class="pdf-chip trace-chip" data-ptidx="${t.table_index}">表 ${t.table_index}${(t.matched_financial_names || []).length ? ' · ' + (t.matched_financial_names as string[]).join('、') : ''}</button>`,
        )
        .join('')
    : '<span style="color:#64748b">这一页没有可定位的表格。</span>'
  const blocks = (pd.blocks || []).map((b: PageBlock, index) => renderBlock(b, index, pageNumberValue)).join('')
  return sanitizeReadingHtml(
    `<div class="pdf-page-reading-view" data-page-number="${pageNumberValue}"><div class="pdf-page-reading-summary"><div><strong>PDF 第 ${pageNumberValue}</strong><span>${pd.block_count || 0} 个解析块 / ${pd.table_count || 0} 张表</span></div><div class="pdf-chip-row">${chips}</div></div>${blocks || '<div style="padding:20px;color:#64748b">没有可展示的解析内容。</div>'}</div>`,
  )
}
