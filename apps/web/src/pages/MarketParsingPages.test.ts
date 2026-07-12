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

  assert.match(source, /function normalizeWorkflowDescription\(description: string \| undefined, mode: 'standard' \| 'generic'\)/)
  assert.match(source, /PostgreSQL 入库直接读取解析产物/)
  assert.match(source, /研究资产和派生知识资产由解析产物继续生成/)
  assert.match(source, /LLM-Wiki、Wiki语义增强和 PostgreSQL 入库都读取同一套解析产物/)
  assert.match(source, /description=\{normalizedWorkflowDescription\}/)
})

test('UsParsing.tsx keeps only the upload-panel PDF compatibility entry', () => {
  const source = readFileSync(resolve(pageDir, 'UsParsing.tsx'), 'utf-8')

  assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
  assert.doesNotMatch(source, /打开 PDF 解析/)
  assert.match(source, /解析产物入库/)
  assert.match(source, /研究资产生成/)
})

test('PdfWorkflowPanel keeps PostgreSQL source as parser artifacts and exposes non-A market import actions', () => {
  const source = readFileSync(resolve(pageDir, '../components/pdf/PdfWorkflowPanel.tsx'), 'utf-8')
  const pipelineStateSource = readFileSync(resolve(pageDir, '../features/market-parsing/marketIngestionPipelineState.ts'), 'utf-8')
  const genericStart = source.indexOf("mode === 'generic'")
  const standardStart = source.indexOf("{ key: 'wiki-import', label: 'LLM-Wiki入库'", genericStart)
  const genericBranch = source.slice(genericStart, standardStart)

  assert.match(source, /解析产物/)
  assert.match(source, /PostgreSQL 入库/)
  assert.match(source, /研究资产/)
  assert.match(source, /派生知识资产/)
  assert.match(source, /PostgreSQL 入库直接读取解析产物/)
  assert.match(source, /derivePdfGenericMarketIngestionPipelineState/)
  assert.match(pipelineStateSource, /LLM-Wiki入库/)
  assert.match(pipelineStateSource, /Wiki语义增强入库/)
  assert.match(pipelineStateSource, /PostgreSQL入库/)
  assert.match(pipelineStateSource, /key: 'wiki'/)
  assert.match(pipelineStateSource, /key: 'semantic'/)
  assert.match(pipelineStateSource, /key: 'postgres'/)
  assert.match(source, /function marketWorkflowStep/)
  assert.match(source, /if \(actionKey === 'postgres'\) return 'db-import'/)
  assert.match(source, /const runAllLabel = isMarketWorkflow \? '一键入库' : '一键生成与入库'/)
  assert.match(genericBranch, /genericPipelineState\.actions/)
  assert.match(source, /const runAllDisabled = isMarketWorkflow \? genericPipelineState\.runAll\.disabled/)
  assert.match(source, /const runAllDisabledReason = runAllDisabled/)
  assert.match(source, /const disabledReasonHints = Array\.from/)
  assert.match(source, /当前不可执行原因/)
  assert.match(source, /title=\{runAllDisabledReason\}/)
  assert.match(source, /title=\{btn\.disabledReason\}/)
  assert.match(source, /workflowJob\.currentStep/)
  assert.match(source, /workflowJob\.retryScope/)
  assert.match(source, /workflowJob\.failedStep/)
  assert.match(source, /timeout：\{timeoutSeconds\}s/)
  assert.match(source, /stdout: \{stdout\}/)
  assert.match(source, /stderr: \{stderr\}/)
  assert.doesNotMatch(genericBranch, /生成通用主体资产|通用主体语义层|导入 PostgreSQL/)
  assert.doesNotMatch(source, /Wiki.*PostgreSQL 入库源|Wiki 主数据源/)
  assert.doesNotMatch(source, /导入 Wiki/)
  assert.doesNotMatch(source, /Wiki Evidence Package|Wiki 证据包/)
})

test('PdfParsing routes HK JP KR EU PDF entries to the non-A market workflow mode', () => {
  const source = readFileSync(resolve(pageDir, 'PdfParsing.tsx'), 'utf-8')

  assert.match(source, /const workflowMode = market === 'HK' \|\| market === 'JP' \|\| market === 'KR' \|\| market === 'EU' \? 'generic' : 'standard'/)
  assert.match(source, /workflowMode=\{workflowMode\}/)
  assert.match(source, /PostgreSQL 入库材料/)
  assert.doesNotMatch(source, /通用入库材料/)
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

test('MarketParsingPage scopes workflow and source state to the active task id', () => {
  const pageSource = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')

  assert.match(pageSource, /buildMarketParsingStateScopeKey\(\{ market, taskId: currentTaskId \}\)/)
  assert.match(pageSource, /sourceVisibleScopeKey/)
  assert.match(pageSource, /sourceVisibleInScope/)
  assert.match(pageSource, /const hideScopedSource = useCallback/)
  assert.match(pageSource, /const showScopedTableSource = useCallback/)
  assert.match(pageSource, /workflowStateInScope \? workflow\.workflowStatus : null/)
  assert.match(pageSource, /sourceVisible=\{sourceVisibleInScope\}/)
  assert.match(pageSource, /onShowTableSource=\{showScopedTableSource\}/)
  assert.match(pageSource, /loadWorkflowStatus=\{loadScopedWorkflowStatus\}/)
})

test('MarketParsingPage builds only EU structured downloads with package-scoped state', () => {
  const pageSource = readFileSync(resolve(pageDir, 'MarketParsingPage.tsx'), 'utf-8')
  const uploadSource = readFileSync(resolve(pageDir, '../components/pdf/PdfUploadPanel.tsx'), 'utf-8')
  const actionSource = readFileSync(resolve(pageDir, '../features/market-parsing/packageActions.ts'), 'utf-8')
  const euSource = readFileSync(resolve(pageDir, 'EuParsing.tsx'), 'utf-8')

  assert.match(pageSource, /market !== 'EU'/)
  assert.match(pageSource, /packagePath: `download:\$\{report\.relativePath\}`/)
  assert.match(pageSource, /downloadRelativePath: report\.relativePath/)
  assert.match(pageSource, /force: false/)
  assert.match(pageSource, /buildDownloadedPackage=\{viewModel\.canBuildDownloadedPackage \? buildDownloadedPackage : undefined\}/)
  assert.match(actionSource, /download_relative_path: normalizedDownloadPath/)
  assert.match(pageSource, /EU 结构化解析仅支持 ESEF ZIP、iXBRL、XHTML、HTML 或 XML/)
  assert.match(uploadSource, /仅支持 PDF 解析；此文件不会送入 PDF parser/)
  assert.match(uploadSource, /结构化解析产物已生成/)
  assert.match(euSource, /ESEF\/iXBRL\/XHTML\/ZIP/)
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
  const pipelineStateSource = readFileSync(resolve(pageDir, '../features/market-parsing/marketIngestionPipelineState.ts'), 'utf-8')

  assert.match(source, /deriveUsSecDocumentFullImportPath\(task, packageDetail\)/)
  assert.match(source, /runMarketDocumentFullImport\('US', documentFullPath, true, false\)/)
  assert.match(source, /缺少 SEC parser result document_full\.json 路径/)
  assert.match(source, /deriveUsSecWorkflowSummary\(status, packageDetail, selectedPostgresStatus, \{/)
  assert.match(source, /documentFullPath: selectedDocumentFullPath/)
  assert.match(source, /disabled=\{workflowSummary\.runAll\.disabled\}/)
  assert.match(source, /aria-describedby=\{workflowSummary\.runAll\.disabledReason \? runAllDisabledReasonId : undefined\}/)
  assert.match(source, /disabled=\{postgresIngestAction\?\.disabled/)
  assert.match(source, /aria-describedby=\{postgresIngestAction\?\.disabledReason \? postgresDisabledReasonId : undefined\}/)
  assert.match(pipelineStateSource, /LLM-Wiki入库/)
  assert.match(pipelineStateSource, /Wiki语义增强入库/)
  assert.match(pipelineStateSource, /PostgreSQL入库/)
  assert.match(pipelineStateSource, /一键入库/)
  assert.match(pipelineStateSource, /MISSING_DOCUMENT_FULL_REASON/)
  assert.match(source, /semantic: false/)
  assert.doesNotMatch(source, /postgres:\s*true/)
})

test('US SEC detail and refresh actions prefer package scoped selectors over bare ticker', () => {
  const source = readFileSync(resolve(pageDir, '../components/sec/UsSecIngestionPanel.tsx'), 'utf-8')
  const apiSource = readFileSync(resolve(pageDir, '../features/market-parsing/api.ts'), 'utf-8')
  const workbenchSource = readFileSync(resolve(pageDir, '../features/market-parsing/usSecWorkbench.ts'), 'utf-8')

  assert.match(apiSource, /fetchUsSecPackageByPath\(packagePath: string\)/)
  assert.match(apiSource, /market: 'US', package_path: packagePath/)
  assert.match(source, /fetchUsSecPackageByPath\(packagePath\)/)
  assert.match(source, /packageLoadRequestRef\.current !== requestId/)
  assert.match(source, /selectedTaskIdRef\.current !== packagePath/)
  assert.match(source, /loadPackage\(\{ packagePath: task\.packagePath, ticker: task\.ticker \}\)/)
  assert.match(source, /deriveUsSecPackageRebuildRequest\(task, packageDetail\)/)
  assert.match(source, /buildUsSecPackage\(rebuildRequest\)/)
  assert.match(source, /package_path: task\.packagePath/)
  assert.match(workbenchSource, /accessionMatch/)
  assert.match(workbenchSource, /if \(accession \|\| periodEnd\) return null/)
})
