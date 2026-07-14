const SAFE_TAGS = new Set([
  'a',
  'abbr',
  'address',
  'article',
  'aside',
  'b',
  'bdi',
  'bdo',
  'big',
  'blockquote',
  'br',
  'caption',
  'center',
  'cite',
  'code',
  'col',
  'colgroup',
  'dd',
  'del',
  'details',
  'dfn',
  'div',
  'dl',
  'dt',
  'em',
  'figcaption',
  'figure',
  'font',
  'footer',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'header',
  'hr',
  'i',
  'ins',
  'kbd',
  'li',
  'main',
  'mark',
  'nav',
  'ol',
  'p',
  'pre',
  'q',
  's',
  'samp',
  'section',
  'small',
  'span',
  'strike',
  'strong',
  'sub',
  'summary',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'time',
  'tr',
  'tt',
  'u',
  'ul',
  'var',
  'wbr',
])

const VOID_TAGS = new Set(['br', 'col', 'hr', 'wbr'])

// These elements are removed with their descendants. Unknown iXBRL elements outside
// ix:header/ix:hidden are unwrapped so their visible filing text remains readable.
const DROP_WITH_CONTENT_TAGS = new Set([
  'applet',
  'audio',
  'button',
  'canvas',
  'datalist',
  'embed',
  'fieldset',
  'form',
  'frame',
  'frameset',
  'iframe',
  'input',
  'ix:header',
  'ix:hidden',
  'link',
  'map',
  'math',
  'menu',
  'meta',
  'noscript',
  'object',
  'option',
  'output',
  'picture',
  'portal',
  'script',
  'select',
  'slot',
  'source',
  'style',
  'svg',
  'template',
  'textarea',
  'track',
  'video',
])

const DROP_VOID_TAGS = new Set(['base', 'embed', 'frame', 'img', 'input', 'link', 'meta', 'source', 'track'])

const GLOBAL_ATTRIBUTES = new Set([
  'class',
  'dir',
  'hidden',
  'id',
  'lang',
  'name',
  'role',
  'style',
  'title',
  'xml:lang',
])

const TAG_ATTRIBUTES: Record<string, Set<string>> = {
  a: new Set(['href']),
  blockquote: new Set(['cite']),
  col: new Set(['align', 'span', 'valign', 'width']),
  colgroup: new Set(['align', 'span', 'valign', 'width']),
  del: new Set(['cite', 'datetime']),
  font: new Set(['color', 'face', 'size']),
  hr: new Set(['align', 'color', 'noshade', 'size', 'width']),
  ins: new Set(['cite', 'datetime']),
  li: new Set(['type', 'value']),
  ol: new Set(['reversed', 'start', 'type']),
  q: new Set(['cite']),
  table: new Set(['align', 'bgcolor', 'border', 'cellpadding', 'cellspacing', 'frame', 'rules', 'summary', 'width']),
  td: new Set(['abbr', 'align', 'axis', 'bgcolor', 'colspan', 'headers', 'height', 'rowspan', 'scope', 'valign', 'width']),
  th: new Set(['abbr', 'align', 'axis', 'bgcolor', 'colspan', 'headers', 'height', 'rowspan', 'scope', 'valign', 'width']),
  time: new Set(['datetime']),
  tr: new Set(['align', 'bgcolor', 'valign']),
  ul: new Set(['type']),
}

const SAFE_CSS_PROPERTIES = new Set([
  '-webkit-text-size-adjust',
  'align-content',
  'align-items',
  'align-self',
  'background-color',
  'border',
  'border-block-end',
  'border-block-start',
  'border-bottom',
  'border-bottom-color',
  'border-bottom-style',
  'border-bottom-width',
  'border-collapse',
  'border-color',
  'border-inline-end',
  'border-inline-start',
  'border-left',
  'border-left-color',
  'border-left-style',
  'border-left-width',
  'border-radius',
  'border-right',
  'border-right-color',
  'border-right-style',
  'border-right-width',
  'border-spacing',
  'border-style',
  'border-top',
  'border-top-color',
  'border-top-style',
  'border-top-width',
  'border-width',
  'bottom',
  'box-sizing',
  'break-after',
  'break-before',
  'break-inside',
  'caption-side',
  'clear',
  'color',
  'direction',
  'display',
  'empty-cells',
  'flex',
  'flex-basis',
  'flex-direction',
  'flex-grow',
  'flex-shrink',
  'flex-wrap',
  'float',
  'font-family',
  'font-kerning',
  'font-size',
  'font-stretch',
  'font-style',
  'font-variant',
  'font-weight',
  'height',
  'hyphens',
  'justify-content',
  'left',
  'letter-spacing',
  'line-height',
  'list-style-position',
  'list-style-type',
  'margin',
  'margin-block-end',
  'margin-block-start',
  'margin-bottom',
  'margin-inline-end',
  'margin-inline-start',
  'margin-left',
  'margin-right',
  'margin-top',
  'max-height',
  'max-width',
  'min-height',
  'min-width',
  'opacity',
  'overflow',
  'overflow-wrap',
  'overflow-x',
  'overflow-y',
  'padding',
  'padding-block-end',
  'padding-block-start',
  'padding-bottom',
  'padding-inline-end',
  'padding-inline-start',
  'padding-left',
  'padding-right',
  'padding-top',
  'page-break-after',
  'page-break-before',
  'page-break-inside',
  'position',
  'right',
  'table-layout',
  'text-align',
  'text-decoration',
  'text-decoration-color',
  'text-decoration-line',
  'text-decoration-style',
  'text-indent',
  'text-justify',
  'text-overflow',
  'text-transform',
  'top',
  'transform',
  'transform-origin',
  'unicode-bidi',
  'vertical-align',
  'visibility',
  'white-space',
  'width',
  'word-break',
  'word-spacing',
  'word-wrap',
  'z-index',
])

const FORBIDDEN_CSS_VALUE = /(?:\\|[{}<>@]|url\s*\(|expression\s*\(|javascript\s*:|vbscript\s*:|data\s*:|-moz-binding|behavior\s*:)/i
const INTEGER_ATTRIBUTES = new Set(['border', 'cellpadding', 'cellspacing', 'colspan', 'rowspan', 'span', 'start', 'value'])
const DIMENSION_ATTRIBUTES = new Set(['height', 'size', 'width'])
const BOOLEAN_ATTRIBUTES = new Set(['hidden', 'noshade', 'reversed'])
const NAMED_URL_ENTITIES: Record<string, string> = {
  amp: '&',
  colon: ':',
  newline: '\n',
  tab: '\t',
}

interface ParsedTag {
  attributes: Array<[string, string | null]>
  end: boolean
  name: string
  selfClosing: boolean
}

interface SanitizedSecDocument {
  bodyHtml: string
  bodyStyle: string
}

function escapeText(value: string): string {
  return value
    .replace(/&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#x[0-9A-Fa-f]{1,6});)/g, '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

function escapeAttribute(value: string): string {
  return escapeText(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;')
}

function findTagEnd(html: string, start: number): number {
  let quote = ''
  for (let index = start + 1; index < html.length; index += 1) {
    const character = html[index]
    if (quote) {
      if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      continue
    }
    if (character === '>') return index
  }
  return -1
}

function parseTag(rawTag: string): ParsedTag | null {
  let index = 1
  while (/\s/.test(rawTag[index] || '')) index += 1
  let end = false
  if (rawTag[index] === '/') {
    end = true
    index += 1
    while (/\s/.test(rawTag[index] || '')) index += 1
  }

  const nameStart = index
  while (/[A-Za-z0-9:_-]/.test(rawTag[index] || '')) index += 1
  if (index === nameStart || !/[A-Za-z]/.test(rawTag[nameStart] || '')) return null
  const name = rawTag.slice(nameStart, index).toLowerCase()
  if (end) return { attributes: [], end, name, selfClosing: false }

  const attributes: Array<[string, string | null]> = []
  while (index < rawTag.length - 1) {
    while (/\s/.test(rawTag[index] || '')) index += 1
    if (rawTag[index] === '/' || rawTag[index] === '>') break

    const attributeStart = index
    while (index < rawTag.length && !/[\s=/>]/.test(rawTag[index])) index += 1
    if (index === attributeStart) {
      index += 1
      continue
    }
    const attributeName = rawTag.slice(attributeStart, index).toLowerCase()
    while (/\s/.test(rawTag[index] || '')) index += 1

    let value: string | null = null
    if (rawTag[index] === '=') {
      index += 1
      while (/\s/.test(rawTag[index] || '')) index += 1
      const quote = rawTag[index]
      if (quote === '"' || quote === "'") {
        index += 1
        const valueStart = index
        while (index < rawTag.length && rawTag[index] !== quote) index += 1
        value = rawTag.slice(valueStart, index)
        if (rawTag[index] === quote) index += 1
      } else {
        const valueStart = index
        while (index < rawTag.length && !/[\s>]/.test(rawTag[index])) index += 1
        value = rawTag.slice(valueStart, index).replace(/\/$/, '')
      }
    }
    attributes.push([attributeName, value])
  }

  return {
    attributes,
    end,
    name,
    selfClosing: /\/\s*>$/.test(rawTag),
  }
}

function splitCssDeclarations(style: string): string[] {
  const declarations: string[] = []
  let current = ''
  let depth = 0
  let quote = ''
  for (const character of style) {
    if (quote) {
      current += character
      if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      current += character
      continue
    }
    if (character === '(') depth += 1
    if (character === ')' && depth > 0) depth -= 1
    if (character === ';' && depth === 0) {
      declarations.push(current)
      current = ''
      continue
    }
    current += character
  }
  if (current) declarations.push(current)
  return declarations
}

function containsUnsafeControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) || 0
    return codePoint <= 0x08
      || codePoint === 0x0b
      || codePoint === 0x0c
      || (codePoint >= 0x0e && codePoint <= 0x1f)
      || codePoint === 0x7f
  })
}

export function sanitizeUsSecInlineStyle(style: string | null): string {
  if (!style || style.length > 32_768 || /\/\*/.test(style) || /\*\//.test(style)) return ''
  const safeDeclarations: string[] = []
  for (const declaration of splitCssDeclarations(style)) {
    const separator = declaration.indexOf(':')
    if (separator <= 0) continue
    const property = declaration.slice(0, separator).trim().toLowerCase()
    const value = declaration.slice(separator + 1).trim()
    if (!SAFE_CSS_PROPERTIES.has(property) || !value || value.length > 1_000) continue
    if (FORBIDDEN_CSS_VALUE.test(value) || containsUnsafeControlCharacter(value)) continue
    if (value.includes('!') && !/!important\s*$/i.test(value)) continue
    if (property === 'position' && !/^(?:static|relative|absolute)(?:\s*!important)?$/i.test(value)) continue
    safeDeclarations.push(`${property}:${value}`)
  }
  return safeDeclarations.join(';')
}

function decodeUrlEntities(value: string): string {
  return value
    .replace(/&#(?:x([0-9a-f]+)|(\d+));?/gi, (_match, hex: string | undefined, decimal: string | undefined) => {
      const codePoint = Number.parseInt(hex || decimal || '', hex ? 16 : 10)
      return Number.isFinite(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
        ? String.fromCodePoint(codePoint)
        : ''
    })
    .replace(/&([a-z]+);?/gi, (match, name: string) => NAMED_URL_ENTITIES[name.toLowerCase()] ?? match)
}

function sanitizeHref(value: string): string | null {
  const rawValue = value.trim()
  if (!rawValue || rawValue.length > 4_096 || rawValue.includes('\\')) return null
  const decoded = decodeUrlEntities(rawValue)
  const compact = Array.from(decoded)
    .filter((character) => {
      const codePoint = character.codePointAt(0) || 0
      return codePoint > 0x20 && !(codePoint >= 0x7f && codePoint <= 0x9f)
    })
    .join('')
  if (/^(?:javascript|vbscript|data|file|blob):/i.test(compact) || compact.startsWith('//')) return null
  const scheme = compact.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase()
  if (scheme && scheme !== 'http' && scheme !== 'https') return null
  if (!scheme && !/^(?:#|\?|\/(?!\/)|\.\.?\/|[A-Za-z0-9_~%-])/.test(compact)) return null
  return rawValue
}

function normalizeAttribute(tagName: string, name: string, value: string | null): string | null {
  if (name.startsWith('on') || name === 'src' || name === 'srcset' || name === 'poster' || name === 'background') return null
  const tagAttributes = TAG_ATTRIBUTES[tagName]
  const allowed = GLOBAL_ATTRIBUTES.has(name)
    || Boolean(tagAttributes?.has(name))
    || /^aria-[a-z0-9_-]+$/.test(name)
    || /^data-[a-z0-9_-]+$/.test(name)
  if (!allowed) return null

  if (BOOLEAN_ATTRIBUTES.has(name)) return name
  const rawValue = String(value ?? '').trim()
  if (!rawValue || rawValue.length > 32_768) return null
  if (name === 'style') return sanitizeUsSecInlineStyle(rawValue) || null
  if (name === 'href') return tagName === 'a' ? sanitizeHref(rawValue) : null
  if (name === 'cite') return sanitizeHref(rawValue)
  if (INTEGER_ATTRIBUTES.has(name)) {
    if (!/^\d+$/.test(rawValue)) return null
    return String(Math.min(Math.max(Number(rawValue), name === 'border' ? 0 : 1), 1_000))
  }
  if (DIMENSION_ATTRIBUTES.has(name) && !/^\d+(?:\.\d+)?%?$/.test(rawValue)) return null
  if (name === 'color' || name === 'bgcolor') {
    if (!/^(?:#[0-9a-f]{3,8}|[a-z]{1,32})$/i.test(rawValue)) return null
  }
  if (name === 'dir' && !/^(?:auto|ltr|rtl)$/i.test(rawValue)) return null
  return rawValue
}

function sanitizedAttributes(tag: ParsedTag): string {
  const attributes: string[] = []
  const seen = new Set<string>()
  let href = ''
  for (const [name, rawValue] of tag.attributes) {
    if (seen.has(name)) continue
    seen.add(name)
    const value = normalizeAttribute(tag.name, name, rawValue)
    if (value === null) continue
    if (name === 'href') href = value
    if (BOOLEAN_ATTRIBUTES.has(name)) attributes.push(name)
    else attributes.push(`${name}="${escapeAttribute(value)}"`)
  }
  if (tag.name === 'a' && href && !href.startsWith('#')) {
    attributes.push('target="_blank"', 'rel="noopener noreferrer"')
  }
  return attributes.length ? ` ${attributes.join(' ')}` : ''
}

function sanitizeSecDocument(html: string | null): SanitizedSecDocument {
  if (!html) return { bodyHtml: '', bodyStyle: '' }
  const source = String(html)
  const hasBody = /<\s*body(?:\s|>)/i.test(source)
  let bodyStyle = ''
  let cursor = 0
  let insideBody = !hasBody
  let output = ''
  const skipTags: string[] = []

  while (cursor < source.length) {
    const tagStart = source.indexOf('<', cursor)
    if (tagStart < 0) {
      if (insideBody && !skipTags.length) output += escapeText(source.slice(cursor))
      break
    }
    if (insideBody && !skipTags.length) output += escapeText(source.slice(cursor, tagStart))

    if (source.startsWith('<!--', tagStart)) {
      const commentEnd = source.indexOf('-->', tagStart + 4)
      cursor = commentEnd < 0 ? source.length : commentEnd + 3
      continue
    }
    const tagEnd = findTagEnd(source, tagStart)
    if (tagEnd < 0) {
      if (insideBody && !skipTags.length) output += escapeText(source.slice(tagStart))
      break
    }
    const rawTag = source.slice(tagStart, tagEnd + 1)
    cursor = tagEnd + 1
    if (/^<\s*[!?]/.test(rawTag)) continue
    const tag = parseTag(rawTag)
    if (!tag) {
      if (insideBody && !skipTags.length) output += escapeText(rawTag)
      continue
    }

    if (skipTags.length) {
      const activeSkipTag = skipTags[skipTags.length - 1]
      if (!tag.end && tag.name === activeSkipTag && !tag.selfClosing && !DROP_VOID_TAGS.has(tag.name)) {
        skipTags.push(tag.name)
      } else if (tag.end && tag.name === activeSkipTag) {
        skipTags.pop()
      }
      continue
    }

    if (tag.name === 'body') {
      if (!tag.end) {
        insideBody = true
        const styleAttribute = tag.attributes.find(([name]) => name === 'style')?.[1] ?? null
        bodyStyle = sanitizeUsSecInlineStyle(styleAttribute)
      } else {
        insideBody = false
      }
      continue
    }
    if (!insideBody || tag.name === 'html' || tag.name === 'head') continue

    if (!tag.end && (DROP_WITH_CONTENT_TAGS.has(tag.name) || DROP_VOID_TAGS.has(tag.name))) {
      if (!tag.selfClosing && !DROP_VOID_TAGS.has(tag.name)) skipTags.push(tag.name)
      continue
    }
    if (tag.end && (DROP_WITH_CONTENT_TAGS.has(tag.name) || DROP_VOID_TAGS.has(tag.name))) continue
    if (!SAFE_TAGS.has(tag.name)) continue
    if (tag.end) {
      if (!VOID_TAGS.has(tag.name)) output += `</${tag.name}>`
      continue
    }
    output += `<${tag.name}${sanitizedAttributes(tag)}>`
  }

  return { bodyHtml: output, bodyStyle }
}

export function sanitizeUsSecReadingHtml(html: string | null): string {
  return sanitizeSecDocument(html).bodyHtml
}

export function buildUsSecReadingHtmlDocument(html: string | null): string {
  const { bodyHtml, bodyStyle } = sanitizeSecDocument(html)
  const bodyAttribute = bodyStyle ? ` style="${escapeAttribute(bodyStyle)}"` : ''
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; media-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'">
  <meta name="referrer" content="no-referrer">
  <title>SEC filing reading view</title>
</head>
<body${bodyAttribute}>${bodyHtml}</body>
</html>`
}
