/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { sanitizeReadingHtml, sanitizeTableHtml } = await import('./pdfSanitize.ts')

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
  assert.match(cleaned, /link/)
  assert.doesNotMatch(cleaned, /script|style=|onclick|onfocus|javascript:|<a/i)
})
