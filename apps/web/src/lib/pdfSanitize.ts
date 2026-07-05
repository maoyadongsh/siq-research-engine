import DOMPurify from 'dompurify'

const TABLE_ALLOWED_TAGS = ['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col']
const TABLE_ALLOWED_ATTR = ['rowspan', 'colspan', 'data-bbox', 'data-cell-bbox', 'bbox']

function purifyHtml(html: string, options: Record<string, unknown>): string {
  const sanitize = (DOMPurify as { sanitize?: (value: string, options: Record<string, unknown>) => string }).sanitize
  return typeof sanitize === 'function' ? sanitize(html, options) : html
}

export function sanitizeTableHtml(html: string | null): string {
  if (!html) return ''
  const purified = purifyHtml(String(html), {
    ALLOWED_TAGS: TABLE_ALLOWED_TAGS,
    ALLOWED_ATTR: TABLE_ALLOWED_ATTR,
  })
  if (typeof DOMParser === 'undefined' || typeof document === 'undefined') return purified
  const doc = new DOMParser().parseFromString(purified, 'text/html')
  doc.querySelectorAll('script, style, iframe, object, embed, link, meta').forEach((n) => n.remove())
  const ok = new Set(['TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TH', 'TD', 'CAPTION', 'COLGROUP', 'COL'])
  doc.body.querySelectorAll('*').forEach((node) => {
    if (!ok.has(node.tagName)) {
      node.replaceWith(document.createTextNode(node.textContent || ''))
      return
    }
    Array.from(node.attributes).forEach((a) => {
      if (!TABLE_ALLOWED_ATTR.includes(a.name.toLowerCase())) node.removeAttribute(a.name)
    })
  })
  return doc.body.innerHTML
}

export function sanitizeReadingHtml(html: string | null): string {
  if (!html) return ''
  return purifyHtml(String(html), {
    ALLOWED_TAGS: [
      'section',
      'div',
      'span',
      'p',
      'br',
      'strong',
      'b',
      'em',
      'i',
      'u',
      'small',
      'sup',
      'sub',
      'table',
      'thead',
      'tbody',
      'tfoot',
      'tr',
      'th',
      'td',
      'caption',
      'colgroup',
      'col',
      'ul',
      'ol',
      'li',
      'button',
    ],
    ALLOWED_ATTR: [
      'class',
      'type',
      'data-ptidx',
      'data-focus-key',
      'data-focus-keys',
      'data-page-number',
      'data-table-index',
      'data-source-table-index',
      'data-block-id',
      'data-block-index',
      'data-block-type',
      'rowspan',
      'colspan',
      'data-bbox',
      'data-cell-bbox',
      'bbox',
    ],
  })
}

export function makeEditableHtml(html: string): string {
  if (!html) return ''
  const doc = new DOMParser().parseFromString(sanitizeTableHtml(html), 'text/html')
  doc.querySelectorAll('th, td').forEach((c) => {
    c.setAttribute('contenteditable', 'true')
    c.setAttribute('spellcheck', 'false')
    c.setAttribute('tabindex', '0')
  })
  return doc.body.innerHTML
}

export function serializeEditableTable(wrap: HTMLElement | null): string {
  const table = wrap?.querySelector('table')
  if (!table) return ''
  const clone = table.cloneNode(true) as HTMLElement
  clone.querySelectorAll('[contenteditable], [spellcheck], [tabindex]').forEach((n) => {
    n.removeAttribute('contenteditable')
    n.removeAttribute('spellcheck')
    n.removeAttribute('tabindex')
  })
  clone.querySelectorAll('.selected-cell, .selected-row').forEach((n) => {
    n.classList.remove('selected-cell', 'selected-row')
    if (!n.getAttribute('class')) n.removeAttribute('class')
  })
  return clone.outerHTML
}

export function parseBbox(v: unknown): number[] | null {
  if (Array.isArray(v)) {
    const b = v.map(Number)
    return b.length === 4 && b.every(Number.isFinite) ? b : null
  }
  if (!v) return null
  const b = String(v)
    .replaceAll('[', '')
    .replaceAll(']', '')
    .split(/[,\s]+/)
    .filter(Boolean)
    .map(Number)
  return b.length === 4 && b.every(Number.isFinite) ? b : null
}

export function normalizeBbox(bbox: number[] | null, extent?: { width: number; height: number }): number[] | null {
  if (!bbox || bbox.length !== 4) return null
  if (bbox.every((v) => v >= 0 && v <= 1) && extent?.width && extent?.height)
    return [bbox[0] * extent.width, bbox[1] * extent.height, bbox[2] * extent.width, bbox[3] * extent.height]
  return bbox
}

export function parseBboxFromAttr(el: HTMLElement): number[] | null {
  return parseBbox(el.dataset.cellBbox || el.dataset.bbox || el.getAttribute('bbox'))
}
