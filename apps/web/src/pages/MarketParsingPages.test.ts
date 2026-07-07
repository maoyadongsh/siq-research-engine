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
    assert.doesNotMatch(source, /Wiki Evidence Package|Wiki 证据包/)
  })
}

test('MarketParsingPage normalizes legacy Wiki workflow descriptions for visible PDF pipeline copy', () => {
  const source = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')

  assert.match(source, /function normalizeWorkflowDescription/)
  assert.match(source, /PostgreSQL 入库直接读取解析产物/)
  assert.match(source, /研究资产和派生知识资产由解析产物继续生成/)
  assert.match(source, /description=\{normalizedWorkflowDescription\}/)
})

test('UsParsing.tsx keeps only the upload-panel PDF compatibility entry', () => {
  const source = readFileSync(resolve(pageDir, 'UsParsing.tsx'), 'utf-8')

  assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
  assert.doesNotMatch(source, /打开 PDF 解析/)
  assert.match(source, /解析产物入库/)
  assert.match(source, /研究资产生成/)
})

test('PdfWorkflowPanel keeps Wiki out of primary pipeline labels and actions', () => {
  const source = readFileSync(resolve(pageDir, '../components/pdf/PdfWorkflowPanel.tsx'), 'utf-8')

  assert.match(source, /解析产物/)
  assert.match(source, /PostgreSQL 入库/)
  assert.match(source, /研究资产/)
  assert.match(source, /派生知识资产/)
  assert.match(source, /PostgreSQL 入库直接读取解析产物/)
  assert.doesNotMatch(source, /Wiki 入库/)
  assert.doesNotMatch(source, /导入 Wiki/)
  assert.doesNotMatch(source, /增强 Wiki 语义层/)
})

test('Help page presents Wiki only as compatibility wording, not the main data source', () => {
  const source = readFileSync(resolve(pageDir, 'Help.tsx'), 'utf-8')

  assert.match(source, /派生知识资产兼容目录/)
  assert.match(source, /不是解析或 PostgreSQL 的主数据源/)
  assert.match(source, /历史接口中也称 Evidence Package/)
  assert.doesNotMatch(source, /Wiki Evidence Package/)
  assert.doesNotMatch(source, /公司 Wiki 主库/)
  assert.doesNotMatch(source, /写入 Wiki\/PostgreSQL/)
})

test('PdfTaskList row click opens results for completed tasks', () => {
  const source = readFileSync(resolve(pageDir, '../components/pdf/PdfTaskList.tsx'), 'utf-8')
  const pageSource = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')

  assert.match(pageSource, /最近任务（点击查看结果）/)
  assert.doesNotMatch(source, /最近任务（点击查看结果）/)
  assert.match(source, /if \(canView\) \{\s+onViewResult\(task\)/)
  assert.match(source, /onResume\(task\)/)
})
