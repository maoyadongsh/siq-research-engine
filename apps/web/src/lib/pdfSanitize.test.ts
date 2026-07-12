/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { buildReadingHtmlDocument, sanitizeReadingHtml, sanitizeTableHtml } = await import('./pdfSanitize.ts')

test('sanitizeTableHtml strips active content with an allowlist fallback', () => {
  const cleaned = sanitizeTableHtml(
    '<table onclick="bad()" style="color:red"><tr><td colspan="2" onmouseover="bad()">' +
      '<a href="javascript:alert(1)">Revenue</a></td><td><img src=x onerror=bad()>Cost</td></tr></table>' +
      '<style>td{background:url(javascript:bad)}</style><script>alert(1)</script>',
  )

  assert.equal(cleaned, '<table><tr><td colspan="2">Revenue</td><td>Cost</td></tr></table>')
  assert.doesNotMatch(cleaned, /script|style=|onclick|onmouseover|javascript:/i)
  assert.doesNotMatch(cleaned, /<a|<img/i)
})

test('sanitizeReadingHtml keeps expected trace markup but strips script, style, and events', () => {
  const cleaned = sanitizeReadingHtml(
    '<section class="pdf-page-block" style="color:red" onclick="bad()" data-focus-key="1:a">' +
      '<button type="submit" data-ptidx="7" onfocus="bad()">Open</button>' +
      '<a href="javascript:alert(1)">link</a><script>alert(1)</script></section>',
  )

  assert.match(cleaned, /<section class="pdf-page-block" data-focus-key="1:a">/)
  assert.match(cleaned, /<button type="button" data-ptidx="7">Open<\/button>/)
  assert.match(cleaned, /<a>link<\/a>/)
  assert.doesNotMatch(cleaned, /script|style=|onclick|onfocus|javascript:|href=/i)
})

test('sanitizeReadingHtml preserves SEC reading structure and hardens anchors', () => {
  const cleaned = sanitizeReadingHtml(
    '<h2 id="business">Business</h2>' +
      '<table><tbody><tr><td>Revenue</td></tr></tbody></table>' +
      '<a name="mda"></a><a href="#business">Internal</a>' +
      '<a href="https://www.sec.gov/Archives/example.htm" onclick="bad()">SEC filing</a>' +
      '<a href="javascript:alert(1)">Unsafe</a><a href="custom-protocol:open">Custom</a>',
  )

  assert.match(cleaned, /<h2 id="business">Business<\/h2>/)
  assert.match(cleaned, /<table><tbody><tr><td>Revenue<\/td><\/tr><\/tbody><\/table>/)
  assert.match(cleaned, /<a name="mda"><\/a>/)
  assert.match(cleaned, /<a href="#business">Internal<\/a>/)
  assert.match(cleaned, /<a href="https:\/\/www\.sec\.gov\/Archives\/example\.htm" target="_blank" rel="noopener noreferrer">SEC filing<\/a>/)
  assert.match(cleaned, /<a>Unsafe<\/a>/)
  assert.match(cleaned, /<a>Custom<\/a>/)
  assert.doesNotMatch(cleaned, /javascript:|onclick/i)
})

test('buildReadingHtmlDocument applies a deny-by-default CSP around sanitized content', () => {
  const documentHtml = buildReadingHtmlDocument('<p>Safe</p><script>bad()</script>')

  assert.match(documentHtml, /Content-Security-Policy/)
  assert.match(documentHtml, /default-src 'none'/)
  assert.match(documentHtml, /style-src 'unsafe-inline'/)
  assert.match(documentHtml, /<p>Safe<\/p>/)
  assert.doesNotMatch(documentHtml, /bad\(\)/)
})
