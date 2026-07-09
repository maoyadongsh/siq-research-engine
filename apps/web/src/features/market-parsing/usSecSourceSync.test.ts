/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  approximateUsSecSectionTop,
  buildUsSecSectionScrollTargets,
  isUsSecSyncSuppressed,
  normalizeUsSecTraceSections,
  resolveUsSecActiveSection,
  usSecSectionFilePath,
} = await import('./usSecSourceSync.ts')

const sections = [
  {
    section_id: 'item_1',
    file: 'business.md',
    section_title: 'Business',
    section_order: 1,
    html_anchor: 'item_1',
    char_start: 100,
    char_end: 500,
    text_length: 400,
  },
  {
    section_id: 'item_8',
    file: 'financial_statements.md',
    section_title: 'Financial Statements',
    section_order: 2,
    html_anchor: 'item_8',
    char_start: 500,
    char_end: 1500,
    text_length: 1000,
  },
]

const sourceMap = [
  {
    evidence_id: 'e1',
    source_type: 'sec_html_section',
    section_id: 'item_1',
    html_anchor: 'business-anchor',
    local_path: 'sections/business.md',
    raw: {
      section_id: 'item_1',
      file: 'business.md',
      section_title: 'Business from source map',
      char_start: 90,
      char_end: 520,
    },
  },
  {
    evidence_id: 'fact-1',
    source_type: 'sec_xbrl_fact',
    section_id: 'item_8',
    html_anchor: 'fact-anchor',
    local_path: 'xbrl/facts_raw.json',
  },
]

test('usSecSectionFilePath normalizes section paths', () => {
  assert.equal(usSecSectionFilePath('business.md'), 'sections/business.md')
  assert.equal(usSecSectionFilePath('/sections/business.md'), 'sections/business.md')
  assert.equal(usSecSectionFilePath('sections/business.md'), 'sections/business.md')
})

test('normalizeUsSecTraceSections merges sections with sec_html_section source map entries', () => {
  const normalized = normalizeUsSecTraceSections(sections, sourceMap)
  assert.equal(normalized.length, 2)
  assert.equal(normalized[0].sectionId, 'item_1')
  assert.equal(normalized[0].filePath, 'sections/business.md')
  assert.equal(normalized[0].htmlAnchor, 'item_1')
  assert.equal(normalized[0].evidenceId, 'e1')
  assert.equal(normalized[1].sectionId, 'item_8')
  assert.equal(normalized[1].filePath, 'sections/financial_statements.md')
})

test('buildUsSecSectionScrollTargets prefers anchor positions and falls back to char offsets', () => {
  const normalized = normalizeUsSecTraceSections(sections, sourceMap)
  const targets = buildUsSecSectionScrollTargets(
    normalized,
    { item_1: 240 },
    2000,
    500,
  )
  assert.equal(targets[0].sectionId, 'item_1')
  assert.equal(targets[0].top, 240)
  assert.equal(targets[0].approximate, false)
  assert.equal(targets[1].sectionId, 'item_8')
  assert.equal(targets[1].approximate, true)
  assert.equal(targets[1].top, approximateUsSecSectionTop(normalized[1], normalized, 2000, 500))
})

test('resolveUsSecActiveSection returns the nearest section above the viewport', () => {
  const targets = [
    { sectionId: 'item_1', filePath: 'sections/business.md', top: 100, approximate: false },
    { sectionId: 'item_8', filePath: 'sections/financial_statements.md', top: 800, approximate: true },
    { sectionId: 'notes', filePath: 'sections/notes.md', top: 1200, approximate: true },
  ]
  assert.equal(resolveUsSecActiveSection(0, targets)?.sectionId, 'item_1')
  assert.equal(resolveUsSecActiveSection(790, targets)?.sectionId, 'item_8')
  assert.equal(resolveUsSecActiveSection(1300, targets)?.sectionId, 'notes')
})

test('isUsSecSyncSuppressed respects the suppression window', () => {
  assert.equal(isUsSecSyncSuppressed('markdown', 100, 500), true)
  assert.equal(isUsSecSyncSuppressed('markdown', 600, 500), false)
  assert.equal(isUsSecSyncSuppressed(null, 100, 500), false)
})
