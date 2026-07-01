/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { PDF_CSS } from '../../pages/pdf/pdfStyles.ts'
import { DOCUMENT_CSS } from './documentStyles.ts'

type CssRule = {
  context: string
  selector: string
}

type StylesheetSmokeCase = {
  css: string
  duplicateSelectors: string[]
  keySelectors: string[]
  minSelectors: number
  name: string
}

const smokeCases: StylesheetSmokeCase[] = [
  {
    name: 'PDF_CSS',
    css: PDF_CSS,
    minSelectors: 250,
    keySelectors: [
      '.pdf-workbench-main',
      '.pdf-stage',
      '.pdf-status-badge.completed',
      '.pdf-download-search input:focus',
      '.pdf-drop-zone:hover',
      '.pdf-md-render',
      '.pdf-pdf-page-stack[data-zoom="100"] .pdf-pdf-page-stack-item',
      '.pdf-table-wrap table',
      '.pdf-editable th[contenteditable="true"]',
      '.pdf-bbox-selected',
      '.pdf-page-merge-bridge.is-candidate::before',
      '.pdf-task-item .task-actions',
      '.mobile-tab-strip a.is-active',
    ],
    duplicateSelectors: [
      ':: .pdf-correction-editor',
      ':: .pdf-correction-note',
      ':: .pdf-md-block.is-focused',
      ':: .pdf-md-html h1',
      ':: .pdf-md-html h2',
      ':: .pdf-md-html h3',
      ':: .pdf-md-html th',
      ':: .pdf-page-state',
      ':: .pdf-quality-row',
      ':: .pdf-source-pane',
      ':: .pdf-table-wrap th',
      ':: .pdf-table-wrap th:first-child',
      ':: .pdf-task-action.danger',
      ':: .pdf-task-action.danger:hover',
      '@media (max-width: 720px) :: .pdf-download-item',
      '@media (max-width: 720px) :: .pdf-page-nav',
      '@media (max-width: 720px) :: .pdf-page-viewer',
      '@media (max-width: 720px) :: .pdf-reading-body',
      '@media (max-width: 720px) :: .pdf-source-pane > .pdf-table-wrap',
    ],
  },
  {
    name: 'DOCUMENT_CSS',
    css: DOCUMENT_CSS,
    minSelectors: 160,
    keySelectors: [
      '.doc-workbench',
      '.doc-drop.is-dragover',
      '.doc-task.active',
      '.doc-progress > span',
      '.doc-preview-grid',
      '.doc-pdf-bbox.is-focused',
      '.doc-md-render.is-full',
      '.doc-md-html table',
      '.doc-relation-flow.is-rejected .doc-relation-connector::after',
      'details.doc-panel > summary::-webkit-details-marker',
      '.scroll-hint::after',
    ],
    duplicateSelectors: [
      ':: .doc-json',
      ':: .doc-md-block.is-focused',
      ':: .doc-md-html h1',
      ':: .doc-md-html h2',
      ':: .doc-md-html h3',
      ':: .doc-md-html th',
      ':: .doc-source-pane',
      ':: .doc-textarea',
      '@media (max-width: 720px) :: .doc-preview-grid',
    ],
  },
]

function extractRules(css: string): CssRule[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const rules: CssRule[] = []
  const stack: Array<{ prelude: string; type: 'at' | 'rule' }> = []
  let blockStart = 0

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]

    if (char === '{') {
      const prelude = text.slice(blockStart, index).trim()
      if (prelude.startsWith('@')) {
        stack.push({ type: 'at', prelude })
      } else {
        const context = stack
          .filter((entry) => entry.type === 'at')
          .map((entry) => entry.prelude)
          .join(' | ')
        for (const selector of prelude.split(',').map((item) => item.trim()).filter(Boolean)) {
          rules.push({ context, selector })
        }
        stack.push({ type: 'rule', prelude })
      }
      blockStart = index + 1
    }

    if (char === '}') {
      stack.pop()
      blockStart = index + 1
    }
  }

  assert.equal(stack.length, 0, 'CSS braces should be balanced')
  return rules
}

function ruleKey(rule: CssRule): string {
  return rule.context ? `${rule.context} :: ${rule.selector}` : `:: ${rule.selector}`
}

function duplicateRuleKeys(rules: CssRule[]): string[] {
  const counts = new Map<string, number>()
  for (const rule of rules) {
    const key = ruleKey(rule)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }

  return Array.from(counts)
    .filter(([, count]) => count > 1)
    .map(([key]) => key)
    .sort()
}

for (const smokeCase of smokeCases) {
  test(`${smokeCase.name} exposes the expected runtime selector surface`, () => {
    assert.ok(smokeCase.css.trim().length > 0, `${smokeCase.name} should not be empty`)
    assert.ok(!/^\s*`\s*$/.test(smokeCase.css), `${smokeCase.name} should contain CSS, not only a template shell`)

    const rules = extractRules(smokeCase.css)
    const selectors = new Set(rules.map((rule) => rule.selector))

    assert.ok(
      rules.length >= smokeCase.minSelectors,
      `${smokeCase.name} should keep its selector inventory; found ${rules.length}`,
    )
    assert.equal(selectors.has(''), false, `${smokeCase.name} should not contain empty selectors`)

    for (const selector of smokeCase.keySelectors) {
      assert.ok(selectors.has(selector), `${smokeCase.name} is missing selector ${selector}`)
    }

    assert.deepEqual(duplicateRuleKeys(rules), smokeCase.duplicateSelectors)
  })
}
