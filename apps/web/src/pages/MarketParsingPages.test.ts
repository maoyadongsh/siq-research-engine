/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const pageDir = dirname(fileURLToPath(import.meta.url))

const marketPages = {
  JpParsing: 'JP',
  HkParsing: 'HK',
  EuParsing: 'EU',
  KrParsing: 'KR',
}

for (const name of Object.keys(marketPages)) {
  test(`${name}.tsx keeps Wiki evidence package panels out of PDF parsing pages`, () => {
    const page = `${name}.tsx`
    const source = readFileSync(resolve(pageDir, page), 'utf-8')

    assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
    assert.doesNotMatch(source, /extraPanel=\{<MarketEvidencePackagesPanel/)
    assert.match(source, /PostgreSQL 直接从解析产物入库/)
    assert.match(source, /Wiki 作为解析产物派生/)
  })
}

test('UsParsing.tsx keeps only the upload-panel PDF compatibility entry', () => {
  const source = readFileSync(resolve(pageDir, 'UsParsing.tsx'), 'utf-8')

  assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
  assert.doesNotMatch(source, /打开 PDF 解析/)
})

test('PdfTaskList row click opens results for completed tasks', () => {
  const source = readFileSync(resolve(pageDir, '../components/pdf/PdfTaskList.tsx'), 'utf-8')
  const pageSource = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')

  assert.match(pageSource, /最近任务（点击查看结果）/)
  assert.doesNotMatch(source, /最近任务（点击查看结果）/)
  assert.match(source, /if \(canView\) \{\s+onViewResult\(task\)/)
  assert.match(source, /onResume\(task\)/)
})
