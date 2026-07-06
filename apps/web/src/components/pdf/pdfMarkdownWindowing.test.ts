/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  hasWrappedLongMarkdownLines,
  shouldWindowPdfMarkdownLines,
} from './pdfMarkdownWindowing.ts'

test('shouldWindowPdfMarkdownLines keeps fixed-height windowing for many short lines', () => {
  const lines = Array.from({ length: 901 }, (_, index) => `line ${index}`)

  assert.equal(shouldWindowPdfMarkdownLines(lines), true)
})

test('shouldWindowPdfMarkdownLines disables fixed-height windowing when long lines wrap', () => {
  const lines = Array.from({ length: 1200 }, (_, index) => (
    index === 77 ? 'x'.repeat(261) : `line ${index}`
  ))

  assert.equal(hasWrappedLongMarkdownLines(lines), true)
  assert.equal(shouldWindowPdfMarkdownLines(lines), false)
})
