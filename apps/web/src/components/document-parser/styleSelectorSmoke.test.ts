/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { PDF_CSS } from '../../pages/pdf/pdfStyles.ts'
import { DOCUMENT_CSS } from './documentStyles.ts'

type CssRule = {
  context: string
  selector: string
}

type CssRuleBlock = CssRule & {
  body: string
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
      '.doc-drop:focus-visible',
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

function selectorsForContext(css: string, context: string): string[] {
  return extractRules(css)
    .filter((rule) => rule.context === context)
    .map((rule) => rule.selector)
    .sort()
}

function extractRuleBlocks(css: string): CssRuleBlock[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const rules: CssRuleBlock[] = []
  const stack: Array<{ bodyStart: number; context: string; prelude: string; type: 'at' | 'rule' }> = []
  let blockStart = 0

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]

    if (char === '{') {
      const prelude = text.slice(blockStart, index).trim()
      if (prelude.startsWith('@')) {
        stack.push({ type: 'at', prelude, context: '', bodyStart: index + 1 })
      } else {
        const context = stack
          .filter((entry) => entry.type === 'at')
          .map((entry) => entry.prelude)
          .join(' | ')
        stack.push({ type: 'rule', prelude, context, bodyStart: index + 1 })
      }
      blockStart = index + 1
    }

    if (char === '}') {
      const entry = stack.pop()
      assert.ok(entry, 'CSS closing brace should match an open block')
      if (entry.type === 'rule') {
        const body = text.slice(entry.bodyStart, index).trim()
        for (const selector of entry.prelude.split(',').map((item) => item.trim()).filter(Boolean)) {
          rules.push({ context: entry.context, selector, body })
        }
      }
      blockStart = index + 1
    }
  }

  assert.equal(stack.length, 0, 'CSS braces should be balanced')
  return rules
}

function declarationsFor(css: string, selector: string, context = ''): Map<string, string> {
  const rule = extractRuleBlocks(css).find((candidate) => candidate.selector === selector && candidate.context === context)
  assert.ok(rule, `missing CSS rule ${context ? `${context} :: ` : ''}${selector}`)

  const declarations = new Map<string, string>()
  for (const declaration of rule.body.split(';')) {
    const separatorIndex = declaration.indexOf(':')
    if (separatorIndex < 0) continue
    declarations.set(
      declaration.slice(0, separatorIndex).trim(),
      declaration.slice(separatorIndex + 1).trim(),
    )
  }
  return declarations
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

test('DOCUMENT_CSS keeps mobile preview selectors collapsed without losing tap targets', () => {
  const mobile = '@media (max-width: 720px)'

  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-preview-grid', '@media (max-width: 720px)').get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-source-pane', '@media (max-width: 720px)').get('border-bottom'), '1px solid var(--border)')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-segment', mobile).get('grid-template-columns'), 'repeat(2, minmax(0, 1fr))')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-toggle-grid', mobile).get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-task-toolbar', mobile).get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-batch-bar .doc-action-row button', mobile).get('min-height'), '44px')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-batch-bar .doc-action-row button', mobile).get('min-width'), '44px')
})

test('DOCUMENT_CSS keeps the narrow viewport selector inventory explicit', () => {
  assert.deepEqual(selectorsForContext(DOCUMENT_CSS, '@media (max-width: 720px)'), [
    '.doc-batch-bar',
    '.doc-batch-bar .doc-action-row',
    '.doc-batch-bar .doc-action-row button',
    '.doc-json',
    '.doc-markdown',
    '.doc-md-render',
    '.doc-panel-body',
    '.doc-panel-head',
    '.doc-preview-grid',
    '.doc-preview-grid',
    '.doc-relation-connector',
    '.doc-relation-node',
    '.doc-result-head',
    '.doc-segment',
    '.doc-source-page',
    '.doc-source-pane',
    '.doc-task-toolbar',
    '.doc-toggle-grid',
  ])
})

test('DOCUMENT_CSS preserves overflow guards for narrow and wide document content', () => {
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-workbench').get('grid-template-columns'), 'minmax(300px, 370px) minmax(0, 1fr)')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-side').get('min-width'), '0')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-source-pane').get('min-width'), '0')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-drop:focus-visible').get('outline'), 'none')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-search:focus-within').get('border-color'), '#2563eb')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-md-html').get('overflow-x'), 'auto')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-md-html table').get('min-width'), '100%')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-md-html th').get('white-space'), 'nowrap')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-relation-flow').get('overflow-x'), 'auto')
  assert.equal(declarationsFor(DOCUMENT_CSS, '.doc-relation-step').get('flex'), '0 0 auto')
})

test('PDF_CSS keeps mobile result controls touchable without widening the viewport', () => {
  const mobile = '@media (max-width: 720px)'
  const compact = '@media (max-width: 520px)'

  assert.equal(declarationsFor(PDF_CSS, '.pdf-download-search', mobile).get('grid-template-columns'), 'minmax(0, 1fr) auto')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-download-search input', mobile).get('height'), '46px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-download-item', mobile).get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-download-actions .pdf-small-action', mobile).get('min-height'), '44px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-workbench', mobile).get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-page-topline', mobile).get('width'), '100%')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-page-stage[data-zoom="150"]', mobile).get('min-width'), '100%')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-page-stage[data-zoom="150"]', mobile).get('width'), '100%')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-md-actions', mobile).get('overflow-x'), 'auto')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-md-action', mobile).get('min-height'), '46px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-md-actions', compact).get('grid-template-columns'), '1fr')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-md-action', compact).get('min-width'), '0')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-md-action', compact).get('min-height'), '52px')
})

test('PDF_CSS preserves mobile overflow affordances for dense PDF tables and task actions', () => {
  const mobile = '@media (max-width: 720px)'

  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-wrap').get('overflow-x'), 'auto')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-wrap table').get('min-width'), 'max(100%, 1080px)')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-wrap table', mobile).get('min-width'), 'max(100%, 760px)')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-x-scrollbar').get('touch-action'), 'none')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-x-scrollbar', mobile).get('height'), '40px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-table-x-scrollbar-thumb', mobile).get('min-width'), '64px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-task-item .task-actions', mobile).get('overflow-x'), 'auto')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-task-action', mobile).get('min-height'), '44px')
  assert.equal(declarationsFor(PDF_CSS, '.pdf-task-action', mobile).get('min-width'), '0')
})
