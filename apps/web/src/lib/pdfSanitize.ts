import DOMPurify from 'dompurify'

const TABLE_ALLOWED_TAGS = ['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col']
const TABLE_ALLOWED_ATTR = ['rowspan', 'colspan', 'data-bbox', 'data-cell-bbox', 'bbox']
const TABLE_ALLOWED_TAG_SET = new Set(TABLE_ALLOWED_TAGS.map((tag) => tag.toUpperCase()))
const TABLE_ALLOWED_ATTR_SET = new Set(TABLE_ALLOWED_ATTR)
const READING_ALLOWED_TAGS = [
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
]
const READING_ALLOWED_ATTR = [
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
]
const READING_ALLOWED_TAG_SET = new Set(READING_ALLOWED_TAGS.map((tag) => tag.toUpperCase()))
const READING_ALLOWED_ATTR_SET = new Set(READING_ALLOWED_ATTR)
const SKIP_CONTENT_TAGS = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'svg', 'math', 'template'])

function purifyHtml(html: string, options: Record<string, unknown>): string {
  const sanitize = (DOMPurify as { sanitize?: (value: string, options: Record<string, unknown>) => string }).sanitize
  return typeof sanitize === 'function' ? sanitize(html, options) : html
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

function escapeTextHtml(value: string): string {
  return value
    .replace(/&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#x[0-9A-Fa-f]{1,6});)/g, '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

function escapeAttr(value: string): string {
  return escapeHtml(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;')
}

function normalizeAllowedAttr(tagName: string, name: string, value: string | null, allowedAttrs: Set<string>): string | null {
  const attr = name.toLowerCase()
  if (!allowedAttrs.has(attr) || attr.startsWith('on') || attr === 'style') return null
  const rawValue = String(value ?? '').trim()
  if (attr === 'rowspan' || attr === 'colspan') {
    if (!/^\d+$/.test(rawValue)) return null
    return String(Math.min(Math.max(Number(rawValue), 1), 1000))
  }
  if (attr === 'type' && tagName !== 'button') return null
  if (attr === 'type' && rawValue && rawValue.toLowerCase() !== 'button') return 'button'
  return rawValue
}

function sanitizeWithAllowlist(html: string, allowedTags: Set<string>, allowedAttrs: Set<string>): string {
  const tagPattern = /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<![^>]*>|<\/?([A-Za-z][A-Za-z0-9:-]*)(?:\s[^<>]*)?>/g
  const attrPattern = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g
  let output = ''
  let cursor = 0
  let skipDepth = 0
  let match: RegExpExecArray | null

  while ((match = tagPattern.exec(html)) !== null) {
    if (!skipDepth) output += escapeTextHtml(html.slice(cursor, match.index))
    cursor = match.index + match[0].length

    const raw = match[0]
    const tagName = (match[1] || '').toLowerCase()
    if (!tagName) continue
    const isEndTag = /^<\//.test(raw)
    const isSelfClosing = /\/\s*>$/.test(raw) || tagName === 'br' || tagName === 'col'

    if (skipDepth) {
      if (SKIP_CONTENT_TAGS.has(tagName)) skipDepth += isEndTag ? -1 : isSelfClosing ? 0 : 1
      if (skipDepth < 0) skipDepth = 0
      continue
    }

    if (SKIP_CONTENT_TAGS.has(tagName)) {
      if (!isEndTag && !isSelfClosing) skipDepth = 1
      continue
    }
    if (!allowedTags.has(tagName.toUpperCase())) continue

    if (isEndTag) {
      if (tagName !== 'br' && tagName !== 'col') output += `</${tagName}>`
      continue
    }

    const attrs: string[] = []
    const attrSource = raw.replace(/^<[A-Za-z][A-Za-z0-9:-]*/, '').replace(/\/?>$/, '')
    let attrMatch: RegExpExecArray | null
    while ((attrMatch = attrPattern.exec(attrSource)) !== null) {
      const value = normalizeAllowedAttr(tagName, attrMatch[1], attrMatch[2] ?? attrMatch[3] ?? attrMatch[4] ?? null, allowedAttrs)
      if (value !== null) attrs.push(`${attrMatch[1].toLowerCase()}="${escapeAttr(value)}"`)
    }
    output += `<${tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`
  }

  if (!skipDepth) output += escapeTextHtml(html.slice(cursor))
  return output
}

export function sanitizeTableHtml(html: string | null): string {
  if (!html) return ''
  const purified = purifyHtml(String(html), {
    ALLOWED_TAGS: TABLE_ALLOWED_TAGS,
    ALLOWED_ATTR: TABLE_ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: Array.from(SKIP_CONTENT_TAGS),
    FORBID_ATTR: ['style'],
  })
  if (typeof DOMParser === 'undefined' || typeof document === 'undefined') {
    return sanitizeWithAllowlist(purified, TABLE_ALLOWED_TAG_SET, TABLE_ALLOWED_ATTR_SET)
  }
  const doc = new DOMParser().parseFromString(purified, 'text/html')
  doc.querySelectorAll(Array.from(SKIP_CONTENT_TAGS).join(',')).forEach((n) => n.remove())
  doc.body.querySelectorAll('*').forEach((node) => {
    if (!TABLE_ALLOWED_TAG_SET.has(node.tagName)) {
      node.replaceWith(document.createTextNode(node.textContent || ''))
      return
    }
    Array.from(node.attributes).forEach((a) => {
      const value = normalizeAllowedAttr(node.tagName.toLowerCase(), a.name, a.value, TABLE_ALLOWED_ATTR_SET)
      if (value === null) node.removeAttribute(a.name)
      else node.setAttribute(a.name.toLowerCase(), value)
    })
  })
  return doc.body.innerHTML
}

export function sanitizeReadingHtml(html: string | null): string {
  if (!html) return ''
  const purified = purifyHtml(String(html), {
    ALLOWED_TAGS: READING_ALLOWED_TAGS,
    ALLOWED_ATTR: READING_ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: Array.from(SKIP_CONTENT_TAGS),
    FORBID_ATTR: ['style'],
  })
  if (typeof DOMParser === 'undefined' || typeof document === 'undefined') {
    return sanitizeWithAllowlist(purified, READING_ALLOWED_TAG_SET, READING_ALLOWED_ATTR_SET)
  }
  const doc = new DOMParser().parseFromString(purified, 'text/html')
  doc.querySelectorAll(Array.from(SKIP_CONTENT_TAGS).join(',')).forEach((n) => n.remove())
  doc.body.querySelectorAll('*').forEach((node) => {
    if (!READING_ALLOWED_TAG_SET.has(node.tagName)) {
      node.replaceWith(document.createTextNode(node.textContent || ''))
      return
    }
    Array.from(node.attributes).forEach((a) => {
      const value = normalizeAllowedAttr(node.tagName.toLowerCase(), a.name, a.value, READING_ALLOWED_ATTR_SET)
      if (value === null) node.removeAttribute(a.name)
      else node.setAttribute(a.name.toLowerCase(), value)
    })
  })
  return doc.body.innerHTML
}

export function makeEditableHtml(html: string): string {
  if (!html) return ''
  if (typeof DOMParser === 'undefined') return sanitizeTableHtml(html)
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
