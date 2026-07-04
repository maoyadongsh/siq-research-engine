# US SEC Frontend Parsing Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `/parse-us` so users first see an A-share-style downloaded reports list, click `解析` to build US SEC evidence packages from downloaded filings, then review Wiki/PostgreSQL status and SEC source/results.

**Architecture:** Keep US SEC parsing out of PDF `task_id` plumbing. Add a pure US workbench view model, add a US downloaded-list component that uses the existing A-share `pdf-*` visual classes, then recompose `UsSecIngestionPanel` so package build and PostgreSQL import are the main flow. Use existing backend APIs; do not add new endpoints in this plan.

**Tech Stack:** React 19, TypeScript, Vite, lucide-react, node:test, Playwright.

## Global Constraints

- `/parse-us` remains the US main entry.
- `/parse?market=US` remains only the US PDF compatibility entry.
- `/parse-us` must show the downloaded reports list as the first main workbench panel after the market tabs.
- US list rows must retain `选择` and `解析` actions.
- US `解析` means building Markdown, JSON, XBRL facts, metrics, quality, and source-map evidence package files from a downloaded SEC disclosure.
- US parsing must not call the PDF parser, must not create a PDF task, and must not invent PDF page citations.
- Milvus is not part of the main flow in this phase and must not appear as a primary action.
- A-share `/parse` behavior must remain unchanged.
- Existing unrelated dirty worktree changes must not be reverted or included in task commits.

---

## File Structure

- Create `apps/web/src/features/market-parsing/usSecWorkbench.ts`: pure row/status derivation for US downloaded disclosures.
- Create `apps/web/src/features/market-parsing/usSecWorkbench.test.ts`: node:test coverage for row/status derivation.
- Create `apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx`: A-share-style downloaded reports panel for US.
- Modify `apps/web/src/components/sec/UsSecIngestionPanel.tsx`: render the downloaded list first, wire `解析` to `buildUsSecPackage`, keep result/source views, hide Milvus.
- Modify `apps/web/src/pages/UsParsing.tsx`: move the PDF compatibility panel below the SEC workbench.
- Create `apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`: E2E coverage for downloaded list first and package-build payload.
- Modify `apps/web/README.md` only if its `/parse-us` row still says Milvus/vectorization is part of the main flow.

---

### Task 1: US SEC Downloaded Row View Model

**Files:**
- Create: `apps/web/src/features/market-parsing/usSecWorkbench.ts`
- Create: `apps/web/src/features/market-parsing/usSecWorkbench.test.ts`

**Interfaces:**
- Consumes: `DownloadedPdf` from `apps/web/src/lib/pdfTypes.ts`; `UsSecCaseSetStatus` and `UsSecCaseSetItem` from `apps/web/src/features/market-parsing/api.ts`.
- Produces: `UsSecParseStatus`, `UsSecDownloadedRow`, `usSecDocumentKind()`, `findUsSecCaseItem()`, `deriveUsSecParseStatus()`, `deriveUsSecDownloadedRows()`.

- [ ] **Step 1: Write the failing unit test**

Create `apps/web/src/features/market-parsing/usSecWorkbench.test.ts`:

```ts
/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import type { DownloadedPdf } from '../../lib/pdfTypes.ts'
import type { UsSecCaseSetStatus } from './api.ts'

const {
  deriveUsSecDownloadedRows,
  deriveUsSecParseStatus,
  findUsSecCaseItem,
  usSecDocumentKind,
} = await import('./usSecWorkbench.ts')

function report(overrides: Partial<DownloadedPdf> = {}): DownloadedPdf {
  return {
    id: 'nvda-10k',
    market: 'US',
    company: 'NVIDIA',
    companyName: 'NVIDIA Corporation',
    ticker: 'NVDA',
    category: '10-K',
    filename: 'nvidia-2025-10k.htm',
    relativePath: 'US/NVIDIA/2025/nvidia-2025-10k.htm',
    size: 1234,
    mtime: '2026-06-27T08:00:00.000Z',
    url: '/api/downloads/report-file/US/NVIDIA/2025/nvidia-2025-10k.htm',
    contentType: 'text/html',
    isPdf: false,
    form: '10-K',
    reportType: '10-K',
    reportFamily: 'annual',
    reportEnd: '2025-01-31',
    publishedAt: '2025-03-18',
    accessionNumber: '0001045810-25-000023',
    ...overrides,
  }
}

const status: UsSecCaseSetStatus = {
  company_count: 1,
  items: [{
    ticker: 'NVDA',
    company_name: 'NVIDIA Corporation',
    fiscal_year: 2025,
    period_end: '2025-01-31',
    filing_date: '2025-03-18',
    quality_status: 'pass',
    package_path: 'data/wiki/us_sec/NVDA/2025/10-K_0001045810-25-000023',
  }],
  ingest_report: {
    package_count: 1,
    summary: { xbrl_facts: 120, normalized_metrics: 20, sections: 8, tables: 5, evidence_items: 180, quality: { pass: 1 } },
  },
}

test('usSecDocumentKind labels SEC files', () => {
  assert.equal(usSecDocumentKind(report()), 'HTML')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.xhtml', contentType: 'application/xhtml+xml' })), 'iXBRL')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.xml', contentType: 'application/xml' })), 'XML')
  assert.equal(usSecDocumentKind(report({ filename: 'filing.zip', contentType: 'application/zip' })), 'ZIP')
  assert.equal(usSecDocumentKind(report({ filename: 'proxy.pdf', contentType: 'application/pdf', isPdf: true })), 'PDF')
})

test('findUsSecCaseItem prefers accession, ticker, and period', () => {
  const item = findUsSecCaseItem(report(), status)
  assert.equal(item?.ticker, 'NVDA')
  assert.match(String(item?.package_path), /0001045810-25-000023/)
})

test('deriveUsSecParseStatus maps package and import states', () => {
  const matched = status.items?.[0]
  assert.equal(deriveUsSecParseStatus({ report: report(), item: null, status }), 'unparsed')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status: null }), 'package_ready')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status }), 'postgres_ready')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'warning' }, status }), 'warning')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: { ...matched, quality_status: 'fail' }, status }), 'failed')
  assert.equal(deriveUsSecParseStatus({ report: report(), item: matched, status, busyPath: report().relativePath }), 'building')
})

test('deriveUsSecDownloadedRows exposes list row metadata', () => {
  const rows = deriveUsSecDownloadedRows([report()], status, '')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].ticker, 'NVDA')
  assert.equal(rows[0].form, '10-K')
  assert.equal(rows[0].fileType, 'HTML')
  assert.equal(rows[0].parseStatus, 'postgres_ready')
  assert.equal(rows[0].packagePath, 'data/wiki/us_sec/NVDA/2025/10-K_0001045810-25-000023')
})
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd apps/web
node --import ./scripts/register-node-test-alias-loader.mjs --test src/features/market-parsing/usSecWorkbench.test.ts
```

Expected: FAIL because `./usSecWorkbench.ts` does not exist.

- [ ] **Step 3: Implement the view model**

Create `apps/web/src/features/market-parsing/usSecWorkbench.ts`:

```ts
import type { DownloadedPdf } from '../../lib/pdfTypes'
import type { UsSecCaseSetItem, UsSecCaseSetStatus } from './api'

export type UsSecParseStatus = 'unparsed' | 'building' | 'package_ready' | 'postgres_ready' | 'warning' | 'failed'

export interface UsSecDownloadedRow {
  id: string
  relativePath: string
  filename: string
  companyName: string
  ticker: string
  form: string
  periodEnd: string
  filingDate: string
  fileType: string
  sizeBytes: number
  downloadedAt: string
  parseStatus: UsSecParseStatus
  packagePath: string
  report: DownloadedPdf
}

function normalized(value: unknown): string {
  return String(value || '').trim()
}

function upper(value: unknown): string {
  return normalized(value).toUpperCase()
}

function pathIncludesAccession(packagePath: string | undefined, accession: string): boolean {
  if (!packagePath || !accession) return false
  return packagePath.toLowerCase().includes(accession.toLowerCase())
}

export function usSecDocumentKind(report: DownloadedPdf): string {
  const filename = normalized(report.filename).toLowerCase()
  const suffix = filename.split('.').pop() || ''
  const contentType = normalized(report.contentType).toLowerCase()
  if (report.isPdf === true || suffix === 'pdf' || contentType.includes('pdf')) return 'PDF'
  if (suffix === 'zip' || contentType.includes('zip')) return 'ZIP'
  if (suffix === 'xhtml' || suffix === 'xbrl' || contentType.includes('xhtml')) return 'iXBRL'
  if (suffix === 'htm' || suffix === 'html' || contentType.includes('html')) return 'HTML'
  if (suffix === 'xml' || contentType.includes('xml')) return 'XML'
  return suffix ? suffix.toUpperCase() : '文件'
}

export function findUsSecCaseItem(report: DownloadedPdf, status: UsSecCaseSetStatus | null | undefined): UsSecCaseSetItem | null {
  const items = status?.items || []
  if (!items.length) return null
  const ticker = upper(report.ticker)
  const accession = normalized(report.accessionNumber)
  const periodEnd = normalized(report.reportEnd)
  const exact = items.find((item) =>
    (!ticker || upper(item.ticker) === ticker)
    && (!accession || pathIncludesAccession(item.package_path, accession))
    && (!periodEnd || normalized(item.period_end) === periodEnd)
  )
  if (exact) return exact
  const tickerMatch = ticker ? items.find((item) => upper(item.ticker) === ticker) : null
  if (tickerMatch) return tickerMatch
  const company = normalized(report.companyName || report.company).toLowerCase()
  return company ? items.find((item) => normalized(item.company_name).toLowerCase() === company) || null : null
}

export function deriveUsSecParseStatus({
  report,
  item,
  status,
  busyPath = '',
}: {
  report: DownloadedPdf
  item?: UsSecCaseSetItem | null
  status?: UsSecCaseSetStatus | null
  busyPath?: string
}): UsSecParseStatus {
  if (busyPath && busyPath === report.relativePath) return 'building'
  if (!item?.package_path) return 'unparsed'
  const quality = normalized(item.quality_status).toLowerCase()
  if (quality === 'fail' || quality === 'failed') return 'failed'
  if (quality === 'warning' || quality === 'warn') return 'warning'
  const importedPackages = Number(status?.ingest_report?.package_count || 0)
  const importedFacts = Number(status?.ingest_report?.summary?.xbrl_facts || 0)
  if (importedPackages > 0 && importedFacts > 0) return 'postgres_ready'
  return 'package_ready'
}

export function deriveUsSecDownloadedRows(reports: DownloadedPdf[], status?: UsSecCaseSetStatus | null, busyPath = ''): UsSecDownloadedRow[] {
  return reports.map((report) => {
    const item = findUsSecCaseItem(report, status)
    return {
      id: report.id,
      relativePath: report.relativePath,
      filename: report.filename,
      companyName: normalized(report.companyName || report.company) || '未知公司',
      ticker: upper(report.ticker),
      form: normalized(report.form || report.reportType || report.category),
      periodEnd: normalized(report.reportEnd || item?.period_end),
      filingDate: normalized(report.publishedAt || item?.filing_date),
      fileType: usSecDocumentKind(report),
      sizeBytes: Number(report.size || report.downloadedFile?.size_bytes || 0),
      downloadedAt: normalized(report.mtime),
      parseStatus: deriveUsSecParseStatus({ report, item, status, busyPath }),
      packagePath: normalized(item?.package_path),
      report,
    }
  })
}
```

- [ ] **Step 4: Run the unit test and verify it passes**

Run:

```bash
cd apps/web
node --import ./scripts/register-node-test-alias-loader.mjs --test src/features/market-parsing/usSecWorkbench.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add apps/web/src/features/market-parsing/usSecWorkbench.ts apps/web/src/features/market-parsing/usSecWorkbench.test.ts
git commit -m "feat: add us sec workbench view model"
```

---

### Task 2: A-Share-Style US Downloaded Reports Panel

**Files:**
- Create: `apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx`

**Interfaces:**
- Consumes: `UsSecDownloadedRow` from Task 1.
- Produces: `UsSecDownloadedReportsPanel(props)` with rows, query, loading, busy path, selected path, refresh/select/parse/upload handlers.

- [ ] **Step 1: Create the component**

Create `apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx`:

```tsx
import { useMemo, useState } from 'react'
import { CheckCircle2, FileText, FolderOpen, Loader2, RefreshCw, Search, Upload } from 'lucide-react'
import { EmptyState } from '@/components/page'
import { formatDateTime, formatSize } from '../../lib/pdfFormatting'
import type { UsSecDownloadedRow } from '../../features/market-parsing/usSecWorkbench'

export interface UsSecDownloadedReportsPanelProps {
  rows: UsSecDownloadedRow[]
  query: string
  loading: boolean
  busyPath: string
  selectedPath: string
  onQueryChange: (value: string) => void
  onRefresh: () => Promise<void>
  onSelect: (row: UsSecDownloadedRow) => Promise<void>
  onParse: (row: UsSecDownloadedRow) => Promise<void>
  onUploadClick: () => void
}

const statusText: Record<UsSecDownloadedRow['parseStatus'], string> = {
  unparsed: '未解析',
  building: '解析中',
  package_ready: '证据包已生成',
  postgres_ready: 'PostgreSQL 已入库',
  warning: '质量警告',
  failed: '质量失败',
}

const statusClass: Record<UsSecDownloadedRow['parseStatus'], string> = {
  unparsed: '',
  building: 'secondary-status-info',
  package_ready: 'secondary-status-success',
  postgres_ready: 'secondary-status-success',
  warning: 'secondary-status-warning',
  failed: 'secondary-status-warning',
}

function isStructuredUsDisclosure(row: UsSecDownloadedRow): boolean {
  return row.fileType !== 'PDF'
}

export function UsSecDownloadedReportsPanel({
  rows,
  query,
  loading,
  busyPath,
  selectedPath,
  onQueryChange,
  onRefresh,
  onSelect,
  onParse,
  onUploadClick,
}: UsSecDownloadedReportsPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => new Date(b.downloadedAt || 0).getTime() - new Date(a.downloadedAt || 0).getTime()),
    [rows],
  )
  const visibleRows = expanded ? sortedRows : sortedRows.slice(0, 5)
  const hasMore = sortedRows.length > visibleRows.length

  return (
    <section className="secondary-panel p-5">
      <div className="pdf-source-choice">
        <div className="pdf-source-choice-head">
          <div>
            <h3 className="flex items-center gap-2"><FolderOpen className="h-5 w-5 text-primary" />已下载财报</h3>
            <p>优先从搜索下载阶段保存的 SEC 披露文件开始；HTML/iXBRL/XML/ZIP 走结构化证据包解析。</p>
          </div>
          <div className="pdf-download-search">
            <label><Search className="h-4 w-4" /><input value={query} onChange={(event) => onQueryChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void onRefresh() }} placeholder="搜索公司、ticker、form 或文件名" /></label>
            <button className="pdf-icon-btn" onClick={() => void onRefresh()} disabled={loading} aria-label="刷新已下载财报">{loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}<span>刷新</span></button>
            <span className="pdf-download-count">{rows.length ? `${rows.length} 份` : '无结果'}</span>
          </div>
        </div>

        {rows.length ? <>
          <div className="pdf-download-list">
            {visibleRows.map((row) => {
              const busy = busyPath === row.relativePath || row.parseStatus === 'building'
              const selected = selectedPath === row.relativePath
              const canParse = isStructuredUsDisclosure(row)
              return (
                <div key={row.id} className={`pdf-download-item ${selected ? 'ring-1 ring-primary/30' : ''}`}>
                  <div className="pdf-download-main"><FileText className="h-5 w-5" /><div className="min-w-0"><div className="pdf-download-title">{row.filename}</div><div className="pdf-download-meta"><span>{row.companyName}</span>{row.ticker ? <span>{row.ticker}</span> : null}{row.form ? <span>{row.form}</span> : null}<span>{row.fileType}</span><span>{formatSize(row.sizeBytes)}</span><span>{formatDateTime(row.downloadedAt)}</span></div></div></div>
                  <div className="pdf-download-actions"><span className={`secondary-status ${statusClass[row.parseStatus]}`}>{statusText[row.parseStatus]}</span><button className="pdf-small-action" onClick={() => void onSelect(row)} disabled={busy}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}选择</button><button className="pdf-small-action primary" onClick={() => void onParse(row)} disabled={busy || !canParse} title={canParse ? '生成 SEC Markdown/JSON evidence package' : 'PDF 附件请使用美股 PDF 兼容入口'}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}解析</button></div>
                </div>
              )
            })}
          </div>
          {hasMore ? <button type="button" onClick={() => setExpanded((value) => !value)} className="mt-3 w-full rounded-xl border border-border bg-bg/60 px-3 py-2 text-xs font-semibold leading-5 text-text-muted hover:bg-bg hover:text-text">{expanded ? '收起' : '展开'} 已下载财报（{visibleRows.length}/{sortedRows.length}）</button> : null}
        </> : <EmptyState icon={FolderOpen} title={loading ? '正在读取已下载财报...' : '暂无已下载财报'} description={loading ? '请稍候' : '可先到搜索下载页下载 SEC 披露，或使用下方上传入口。'} size="sm" className="rounded-[18px] border border-dashed border-border bg-bg/50" />}

        <div className="mt-3 flex flex-wrap gap-2"><button onClick={onUploadClick} className="pdf-small-action inline-flex items-center gap-1"><Upload className="h-4 w-4" />上传附件到 US 目录</button></div>
      </div>
    </section>
  )
}
```

- [ ] **Step 2: Run TypeScript build**

Run:

```bash
cd apps/web
npm run build
```

Expected: PASS or only unrelated pre-existing errors. Fix any errors introduced by this file.

- [ ] **Step 3: Commit Task 2**

```bash
git add apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx
git commit -m "feat: add us sec downloaded reports panel"
```

---

### Task 3: Recompose `/parse-us` Around the Downloaded List

**Files:**
- Modify: `apps/web/src/components/sec/UsSecIngestionPanel.tsx`
- Modify: `apps/web/src/pages/UsParsing.tsx`

**Interfaces:**
- Consumes: `deriveUsSecDownloadedRows()` and `UsSecDownloadedReportsPanel`.
- Produces: downloaded list first, `解析` wired to `buildUsSecPackage({ download_relative_path, force: true })`, no primary Milvus button.

- [ ] **Step 1: Add imports and derived rows**

In `apps/web/src/components/sec/UsSecIngestionPanel.tsx`, add:

```tsx
import { UsSecDownloadedReportsPanel } from './UsSecDownloadedReportsPanel'
import { deriveUsSecDownloadedRows, type UsSecDownloadedRow } from '../../features/market-parsing/usSecWorkbench'
```

After `displayTicker`, add:

```tsx
const downloadedRows = useMemo(() => deriveUsSecDownloadedRows(downloadedReports, status, busy), [downloadedReports, status, busy])
const selectedDownloadRow = useMemo(
  () => downloadedRows.find((row) => row.relativePath === selectedDownloadPath) || downloadedRows[0] || null,
  [downloadedRows, selectedDownloadPath],
)
```

- [ ] **Step 2: Replace downloaded select and parse handlers**

Add row-based handlers:

```tsx
const onSelectDownloadedRow = useCallback(async (row: UsSecDownloadedRow) => {
  const report = row.report
  setSelectedDownloadPath(report.relativePath)
  setError('')
  setBusy('download-select')
  try {
    const nextTicker = String(report.ticker || row.ticker || '').toUpperCase()
    if (nextTicker) {
      setSelectedTicker(nextTicker)
      try {
        await loadPackage(nextTicker)
      } catch {
        setPackageDetail(null)
        setMarkdownText('')
      }
    }
  } finally {
    setBusy('')
  }
}, [loadPackage])

const onParseDownloadedRow = useCallback(async (row: UsSecDownloadedRow) => {
  const report = row.report
  setSelectedDownloadPath(report.relativePath)
  setBusy(report.relativePath)
  setError('')
  setLastOutput('')
  try {
    const response = await buildUsSecPackage({ download_relative_path: report.relativePath, force: true })
    const result = response.job_id
      ? await waitForMarketReportJob<UsSecPackageBuildResponse>(response.job_id, { timeoutMs: 15 * 60 * 1000 })
      : response
    if (result.ok === false) throw new Error(String(result.stderr || result.stdout || 'US 证据包构建失败'))
    setLastOutput(result.stdout || result.stderr || 'US 证据包已生成')
    if (result.package) {
      await applyPackageDetail(result.package)
      const nextTicker = String(result.package.manifest?.ticker || report.ticker || row.ticker || '').toUpperCase()
      if (nextTicker) setSelectedTicker(nextTicker)
    }
    await load()
    await loadDownloads(downloadQuery)
  } catch (err) {
    setError(err instanceof Error ? err.message : 'US 证据包构建失败')
  } finally {
    setBusy('')
  }
}, [applyPackageDetail, downloadQuery, load, loadDownloads])
```

Remove or stop using the old `DownloadRow`, `onSelectDownloaded`, and `onBuildDownloadedPackage` code paths.

- [ ] **Step 3: Hide Milvus from primary flow**

Change `run` to only accept `'plan' | 'postgres'` and send:

```tsx
postgres: mode === 'postgres',
milvus: false,
ddl: mode === 'postgres',
```

Delete the Milvus button from the action grid. Keep `生成 Wiki`, `Dry Run`, and `PostgreSQL`.

- [ ] **Step 4: Render the downloaded panel first**

Wrap the component return in a `div className="space-y-4"` and render this before the existing detail section:

```tsx
<UsSecDownloadedReportsPanel
  rows={downloadedRows}
  query={downloadQuery}
  loading={downloadedLoading}
  busyPath={busy}
  selectedPath={selectedDownloadPath}
  onQueryChange={setDownloadQuery}
  onRefresh={() => loadDownloads(downloadQuery)}
  onSelect={onSelectDownloadedRow}
  onParse={onParseDownloadedRow}
  onUploadClick={openFilePicker}
/>
```

Update the detail section copy to:

```tsx
当前阶段串联 SEC evidence package、Wiki 证据文件和 PostgreSQL facts；向量化入口后续接回。
```

- [ ] **Step 5: Remove the old sidebar download list and keep upload form**

Delete the old `US 下载目录` sidebar list. Keep the upload form, but place it below the stat tiles or inside the main detail section so it is secondary to the top downloaded list.

Where the old selected file footer used `selectedDownload`, replace it with `selectedDownloadRow` and route `设为当前` / `重新解析` to `onSelectDownloadedRow` / `onParseDownloadedRow`.

- [ ] **Step 6: Move PDF compatibility below the SEC workbench**

In `apps/web/src/pages/UsParsing.tsx`, render in this order:

```tsx
<MarketParsingTabs active="US" />
<UsSecIngestionPanel />
<section className="secondary-panel p-4 sm:p-5">
  {/* existing 美股 PDF 兼容入口 */}
</section>
```

- [ ] **Step 7: Verify Task 3**

Run:

```bash
cd apps/web
npm run test:unit
npm run build
```

Expected: PASS. Investigate any new failure before committing.

- [ ] **Step 8: Commit Task 3**

```bash
git add apps/web/src/components/sec/UsSecIngestionPanel.tsx apps/web/src/pages/UsParsing.tsx
git commit -m "feat: make us sec parse page download-first"
```

---

### Task 4: E2E Coverage for US Download-First Parsing

**Files:**
- Create: `apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: `/parse-us` UI from Task 3.
- Produces: Playwright test proving `解析` calls `/api/market-reports/packages/build` with `market: 'US'` and `download_relative_path`, not a PDF parser endpoint.

- [ ] **Step 1: Add the E2E test**

Create `apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`:

```ts
import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function mockUsSecApis(page: Page) {
  const requests: Array<{ path: string; body: unknown }> = []
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname === '/api/auth/me') return route.fulfill(json(e2eUser))
    if (url.pathname === '/api/downloads/reports') return route.fulfill(json({ reports: [{ id: 'nvda-10k', market: 'US', company: 'NVIDIA', companyName: 'NVIDIA Corporation', ticker: 'NVDA', category: '10-K', filename: 'nvidia-2025-10k.htm', relativePath: 'US/NVIDIA/2025/nvidia-2025-10k.htm', size: 1234, mtime: '2026-06-27T08:00:00.000Z', url: '/api/downloads/report-file/US/NVIDIA/2025/nvidia-2025-10k.htm', contentType: 'text/html', isPdf: false, form: '10-K', reportType: '10-K', reportFamily: 'annual', reportEnd: '2025-01-31', publishedAt: '2025-03-18', accessionNumber: '0001045810-25-000023' }] }))
    if (url.pathname === '/api/us-sec/case-set') return route.fulfill(json({ company_count: 1, counts: {}, items: [] }))
    if (url.pathname === '/api/market-reports/packages/build') {
      requests.push({ path: url.pathname, body: route.request().postDataJSON() })
      return route.fulfill(json({ ok: true, package: { package_path: 'data/wiki/us_sec/NVDA/2025/10-K_0001045810-25-000023', manifest: { ticker: 'NVDA', company_name: 'NVIDIA Corporation', form: '10-K', period_end: '2025-01-31' }, counts: { sections: 8, tables: 5, metrics: 20, evidence: 180, dimension_metrics: 2 }, sections: [{ section_id: 'item_7', file: 'mda.md' }], metrics: [{ metric_id: 'm1', canonical_name: 'Revenue', value: '100', unit: 'USD', period_key: 'FY2025', concept: 'us-gaap:Revenue' }], bridge_checks: { overall_status: 'pass', summary: { pass: 1 }, checks: [] }, preview: { raw_html: 'raw/filing.htm', default_markdown: 'sections/mda.md' } } }))
    }
    if (url.pathname === '/api/us-sec/package-file') return route.fulfill({ status: 200, contentType: 'text/markdown', body: '# Item 7\n\nManagement discussion.' })
    return route.fulfill(json({ ok: true, items: [], data: [] }))
  })
  return requests
}

test('US parsing page starts with downloaded disclosures and builds evidence package', async ({ page }) => {
  const requests = await mockUsSecApis(page)
  await page.goto('/parse-us')
  await expect(page.getByRole('heading', { name: '已下载财报' })).toBeVisible()
  await expect(page.getByText('nvidia-2025-10k.htm')).toBeVisible()
  await expect(page.getByRole('button', { name: /选择/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /解析/ }).first()).toBeVisible()
  await expect(page.getByText('Milvus')).toHaveCount(0)

  await page.getByRole('button', { name: /解析/ }).first().click()

  expect(requests).toHaveLength(1)
  expect(requests[0].body).toMatchObject({ market: 'US', download_relative_path: 'US/NVIDIA/2025/nvidia-2025-10k.htm', force: true })
  await expect(page.getByText('US 证据包已生成')).toBeVisible()
  await expect(page.getByText('NVIDIA Corporation')).toBeVisible()
})
```

- [ ] **Step 2: Run the targeted E2E tests**

Run:

```bash
cd apps/web
npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts
npx playwright test e2e/tests/pdf-parsing-market-filter.spec.ts
```

Expected: PASS.

- [ ] **Step 3: Commit Task 4**

```bash
git add apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts
git commit -m "test: cover us sec parsing workbench"
```

---

### Task 5: Final Verification and README Touch-Up

**Files:**
- Modify if stale: `apps/web/README.md`

**Interfaces:**
- Consumes all prior tasks.
- Produces verified implementation and optional README wording.

- [ ] **Step 1: Update README if stale**

If `apps/web/README.md` still describes `/parse-us` as including vectorization or Milvus in the main workflow, replace that route row with:

```md
| `/parse-us` | 美股解析 | SEC 已下载披露列表、HTML/iXBRL evidence package 生成、Wiki 证据包查看和 PostgreSQL 入库 |
```

- [ ] **Step 2: Run final frontend checks**

Run:

```bash
cd apps/web
npm run test:unit
npm run build
npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts e2e/tests/pdf-parsing-market-filter.spec.ts
```

Expected: PASS.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short
git diff -- apps/web/src/features/market-parsing/usSecWorkbench.ts \
  apps/web/src/features/market-parsing/usSecWorkbench.test.ts \
  apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx \
  apps/web/src/components/sec/UsSecIngestionPanel.tsx \
  apps/web/src/pages/UsParsing.tsx \
  apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts \
  apps/web/README.md
```

Expected: only planned implementation files changed by these tasks; unrelated dirty files remain untouched.

- [ ] **Step 4: Commit README if changed**

```bash
git add apps/web/README.md
git commit -m "docs: update us parsing frontend workflow"
```

Skip this commit if README did not change.

---

## Self-Review

Spec coverage:

- Downloaded list first: Task 2 creates the panel; Task 3 renders it before the detail section.
- `选择` and `解析`: Task 2 renders both actions; Task 3 wires handlers.
- `解析` builds evidence package: Task 3 calls `buildUsSecPackage`; Task 4 verifies payload.
- No PDF parser task: Task 4 verifies `/api/market-reports/packages/build`; Task 3 does not use PDF task APIs.
- Wiki/PostgreSQL only: Task 3 removes Milvus primary action and keeps PostgreSQL.
- A-share unchanged: Task 3 only touches US page/panel; Task 4 re-runs PDF compatibility market-filter E2E.
- Result/source review: Task 3 keeps existing package result, metrics, checks, and SEC HTML/Markdown blocks.

Placeholder scan:

- The plan contains no `TBD`, no `TODO`, no undefined task dependency, and no placeholder testing instruction.

Type consistency:

- `UsSecDownloadedRow` is defined in Task 1 and consumed by Tasks 2 and 3.
- `onSelect` and `onParse` accept `UsSecDownloadedRow` in both component and handlers.
- `buildUsSecPackage` uses existing `download_relative_path` and `force` fields.
