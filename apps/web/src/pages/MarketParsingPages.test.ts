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

test('PdfWorkflowPanel keeps PostgreSQL source as parser artifacts while allowing Wiki-derived asset actions', () => {
  const source = readFileSync(resolve(pageDir, '../components/pdf/PdfWorkflowPanel.tsx'), 'utf-8')

  assert.match(source, /解析产物/)
  assert.match(source, /PostgreSQL 入库/)
  assert.match(source, /研究资产/)
  assert.match(source, /派生知识资产/)
  assert.match(source, /PostgreSQL 入库直接读取解析产物/)
  assert.match(source, /LLM-Wiki入库/)
  assert.match(source, /LLM-Wiki语义增强入库/)
  assert.doesNotMatch(source, /PostgreSQL.*Wiki|Wiki.*PostgreSQL 入库源|Wiki 主数据源/)
  assert.doesNotMatch(source, /导入 Wiki/)
  assert.doesNotMatch(source, /Wiki Evidence Package|Wiki 证据包/)
})

test('MarketParsingPage wires PDF-market PostgreSQL imports through document_full API', () => {
  const pageSource = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')
  const workflowSource = readFileSync(resolve(pageDir, 'pdf/usePdfWorkflow.ts'), 'utf-8')
  const apiSource = readFileSync(resolve(pageDir, '../features/pdf-parsing/api.ts'), 'utf-8')

  assert.match(pageSource, /usePdfWorkflow\(tasks\.taskIdRef, showToast,[\s\S]*market\)/)
  assert.match(workflowSource, /isPdfDocumentFullMarket/)
  assert.match(workflowSource, /runMarketDocumentFullWorkflowImportApi\(marketCode, tid\)/)
  assert.match(workflowSource, /waitForMarketReportJob/)
  assert.match(apiSource, /\/api\/market-reports\/document-full\/import/)
  assert.match(apiSource, /body: \{ market, task_id: taskId, ddl: true \}/)
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
  assert.match(source, /<button\s+type="button"\s+className="task-main task-main-button"/)
  assert.doesNotMatch(source, /role="button"/)
  assert.match(source, /if \(canView\) \{\s+onViewResult\(task\)/)
  assert.match(source, /onResume\(task\)/)
})

test('US SEC parsing copy presents structured artifacts instead of evidence-package jargon', () => {
  const downloadedPanel = readFileSync(resolve(pageDir, '../components/sec/UsSecDownloadedReportsPanel.tsx'), 'utf-8')
  const ingestionPanel = readFileSync(resolve(pageDir, '../components/sec/UsSecIngestionPanel.tsx'), 'utf-8')
  const recentPanel = readFileSync(resolve(pageDir, '../components/sec/UsSecRecentTasksPanel.tsx'), 'utf-8')
  const uploadPanel = readFileSync(resolve(pageDir, '../components/pdf/PdfUploadPanel.tsx'), 'utf-8')
  const apiSource = readFileSync(resolve(pageDir, '../features/market-parsing/api.ts'), 'utf-8')

  for (const source of [downloadedPanel, ingestionPanel, recentPanel, uploadPanel, apiSource]) {
    assert.match(source, /解析产物|结构化解析/)
    assert.doesNotMatch(source, /证据包|evidence package/)
  }
})

test('US SEC PostgreSQL button imports canonical parser document_full', () => {
  const source = readFileSync(resolve(pageDir, '../components/sec/UsSecIngestionPanel.tsx'), 'utf-8')

  assert.match(source, /runMarketDocumentFullImport\('US', task\.documentFullPath, true, false\)/)
  assert.match(source, /缺少 SEC parser result document_full\.json 路径/)
  assert.doesNotMatch(source, /postgres:\s*true/)
})
