# US SEC Visual Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/parse-us` visually match the A-share `/parse` workbench while preserving the current US SEC flow and data semantics.

**Architecture:** Keep `UsParsing` and `UsSecIngestionPanel` as the US entry path, but restyle each section to reuse the same shell, spacing, card hierarchy, row density, and empty-state language as the A-share parse page. Limit behavioral changes to presentation-only adjustments, with tests proving the existing parse/result workflow still works.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind utility classes, shared SIQ surface styles, Playwright E2E, Node test runner.

## Global Constraints

- Keep `/parse-us` as the US SEC main entry and `/parse?market=US` as the PDF compatibility entry.
- Keep the current US interaction flow: downloaded reports -> recent tasks -> click task to expand result sections.
- Do not change backend semantics, package APIs, or turn the US flow into a PDF parser flow.
- Reuse existing A-share visual language (`surface-panel`, `apple-card`, `pdf-task-item`, `PdfWorkflowPanel`) wherever practical.
- Milvus stays out of the main visible workflow.
- Tests must continue to prove the existing build-package and result-expansion behavior.

---

## File Map

- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/UsParsing.tsx`
  - Responsibility: page-level shell, hero weight, placement of the PDF compatibility entry.
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx`
  - Responsibility: downloaded report panel layout and row presentation.
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecRecentTasksPanel.tsx`
  - Responsibility: recent task list visual alignment with A-share task rows.
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx`
  - Responsibility: section ordering, card shells, upload area, result panels, and PDF compatibility entry placement.
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/pdf/pdfStyles.ts`
  - Responsibility: shared row, action, and workbench styles needed by US SEC panels.
- Modify: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`
  - Responsibility: keep assertions aligned to the preserved flow while tolerating the new presentation structure.

## Task 1: Restyle the page shell and section hierarchy

**Files:**
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/UsParsing.tsx`
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx`
- Test: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: `UsSecIngestionPanel()` page body, existing `MarketParsingTabs`, existing PDF compatibility link.
- Produces: a lower-weight hero, A-share-like section spacing, and PDF compatibility entry moved into a secondary visual role.

- [ ] **Step 1: Write the failing E2E expectation for the compatibility entry becoming secondary**

```ts
await expect(page.getByRole('heading', { name: '上传附件', exact: true })).toBeVisible()
await expect(page.getByRole('link', { name: '打开 PDF 解析' })).toBeVisible()
const uploadHeadingTop = await page.getByRole('heading', { name: '上传附件', exact: true }).evaluate((el) => el.getBoundingClientRect().top)
const recentTasksTop = await page.getByRole('heading', { name: '最近任务', exact: true }).evaluate((el) => el.getBoundingClientRect().top)
expect(uploadHeadingTop).toBeLessThan(recentTasksTop)
```

- [ ] **Step 2: Run test to verify it fails or needs updated placement checks**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: FAIL on the new visual-order assertion until the shell is updated.

- [ ] **Step 3: Update the page shell and section wrappers**

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/pages/UsParsing.tsx
<section className="secondary-hero">
  <div className="secondary-hero-inner">
    <div className="max-w-3xl">
      <div className="secondary-kicker">
        <FileText className="h-3.5 w-3.5" />
        US Report Parsing
      </div>
      <h1 className="secondary-title">美股解析</h1>
      <p className="secondary-description">解析 SEC 10-K、10-Q、20-F、6-K 披露文件；HTML/iXBRL 走主体、附注、表格、XBRL facts 和 evidence 关系链入库。</p>
    </div>
    <div className="secondary-step-row">
      <span className="secondary-step-chip is-active">美股</span>
      <span className="secondary-step-chip">SEC 解析</span>
      <span className="secondary-step-chip">关系入库</span>
    </div>
  </div>
</section>
```

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx
<section className="surface-panel">
  <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-4 sm:px-5 lg:flex-row lg:items-end lg:justify-between">
    <div className="min-w-0">
      <h2 className="text-lg font-bold text-text sm:text-xl">上传附件</h2>
      <p className="mt-1 text-sm leading-6 text-text-muted">用于 SEC 附件、补充 HTML/XBRL 文件或临时样本入库；主链路仍建议从已下载财报启动。</p>
    </div>
    <div className="shrink-0">
      <Link to="/parse?market=US" className="pdf-small-action inline-flex items-center gap-1">
        <FileText className="h-4 w-4" />
        美股 PDF 兼容入口
      </Link>
    </div>
  </div>
</section>
```

- [ ] **Step 4: Run test to verify the new shell order passes**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: PASS or move to the next failing visual assertion.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/pages/UsParsing.tsx apps/web/src/components/sec/UsSecIngestionPanel.tsx apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts
git commit -m "feat: align us sec page shell with parse workbench"
```

## Task 2: Rebuild the downloaded reports list to match A-share list rows

**Files:**
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx`
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/pdf/pdfStyles.ts`
- Test: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: `UsSecDownloadedRow[]`, current parse/select callbacks.
- Produces: A-share-like downloaded rows with two-line metadata, inline status badges, and consistent right-side action buttons.

- [ ] **Step 1: Write the failing E2E check for row metadata density**

```ts
await expect(structuredRow.getByText('NVIDIA Corporation')).toBeVisible()
await expect(structuredRow.getByText('10-K')).toBeVisible()
await expect(structuredRow.getByText('证据包已生成')).toBeVisible()
```

- [ ] **Step 2: Run test to verify the current row presentation is insufficient**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: FAIL on one of the new row-scoped expectations.

- [ ] **Step 3: Update the row layout and shared styles**

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx
<div className={`pdf-download-item ${selected ? 'ring-1 ring-primary/30' : ''}`}>
  <div className="pdf-download-main">
    <FileText className="h-5 w-5" />
    <div className="min-w-0">
      <div className="pdf-download-title">{row.filename}</div>
      <div className="pdf-download-meta">
        <span>{row.companyName}</span>
        {row.ticker ? <span>{row.ticker}</span> : null}
        {row.form ? <span>{row.form}</span> : null}
        <span>{row.fileType}</span>
        <span>{formatSize(row.sizeBytes)}</span>
        <span>{formatDateTime(row.downloadedAt)}</span>
      </div>
    </div>
  </div>
  <div className="pdf-download-actions">
    <span className={`secondary-status ${statusClass[row.parseStatus]}`}>{statusText[row.parseStatus]}</span>
    <button className="pdf-small-action">选择</button>
    <button className="pdf-small-action primary">解析</button>
  </div>
</div>
```

```ts
// /home/maoyd/siq-research-engine/apps/web/src/pages/pdf/pdfStyles.ts
.pdf-download-item { display: flex; align-items: center; justify-content: space-between; gap: .75rem; border: 1px solid var(--border); border-radius: 14px; background: #fff; padding: .9rem 1rem; }
.pdf-download-item:hover { border-color: #bfdbfe; background: #fbfdff; box-shadow: 0 8px 20px rgba(37, 99, 235, .04); }
.pdf-download-meta { display: flex; flex-wrap: wrap; gap: .55rem; color: var(--text-muted); font-size: .82rem; }
.pdf-download-actions { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; justify-content: flex-end; }
```

- [ ] **Step 4: Run the E2E again to verify the list still parses correctly**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: PASS on downloaded-row assertions and continue to the next checks.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/sec/UsSecDownloadedReportsPanel.tsx apps/web/src/pages/pdf/pdfStyles.ts apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts
git commit -m "feat: restyle us sec downloaded report rows"
```

## Task 3: Rebuild the recent task rows and empty state in A-share style

**Files:**
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecRecentTasksPanel.tsx`
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/pdf/pdfStyles.ts`
- Test: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: `UsSecRecentTaskRow[]`, existing view/rebuild/postgres callbacks.
- Produces: task rows visually aligned with `PdfTaskList`, while keeping `查看结果` as the trigger for result expansion.

- [ ] **Step 1: Write the failing E2E assertion for task action layout and empty gate**

```ts
await expect(page.getByText('选择最近任务后查看结果')).toBeVisible()
await expect(recentTask.getByRole('button', { name: '查看结果' })).toBeVisible()
await expect(recentTask.getByRole('button', { name: '重建' })).toBeVisible()
await expect(recentTask.getByRole('button', { name: 'PostgreSQL' })).toBeVisible()
```

- [ ] **Step 2: Run test to verify the current panel fails the stricter presentation contract**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: FAIL on task row presentation or action grouping.

- [ ] **Step 3: Update the recent task panel to mirror A-share card density**

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecRecentTasksPanel.tsx
<div className="apple-card rounded-[24px] p-4 sm:p-6">
  <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
    <h3 className="text-base font-semibold text-text">任务列表</h3>
    <button onClick={() => void onRefresh()} className="self-start text-sm font-semibold text-text-muted hover:text-text">刷新</button>
  </div>
  {visibleTasks.map((task) => (
    <div key={task.id} className={`pdf-task-item content-auto ${task.id === selectedTaskId ? 'ring-1 ring-primary/30' : ''}`}>
      <div className="task-main">
        <span className="task-name">{task.companyName} · {task.ticker} · {task.form} · {task.periodEnd}</span>
        <div className="task-meta">
          <span className={`secondary-status ${statusClass(task.status)}`}>{task.statusText}</span>
          <span className="text-text-muted text-xs">{task.sectionCount} sections</span>
          <span className="text-text-muted text-xs">{task.factCount} facts</span>
          <span className="text-text-muted text-xs">{formatDateTime(task.filingDate)}</span>
        </div>
      </div>
      <div className="task-actions" style={{ '--task-action-count': 3 } as CSSProperties}>
        <button type="button" className="pdf-task-action primary">查看结果</button>
        <button type="button" className="pdf-task-action">重建</button>
        <button type="button" className="pdf-task-action">PostgreSQL</button>
      </div>
    </div>
  ))}
</div>
```

- [ ] **Step 4: Run the E2E again to verify result gating still behaves the same**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: PASS, with no result sections visible until `查看结果` is clicked.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/sec/UsSecRecentTasksPanel.tsx apps/web/src/pages/pdf/pdfStyles.ts apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts
git commit -m "feat: restyle us sec recent tasks"
```

## Task 4: Align pipeline, markdown, quality, and trace cards with A-share result panels

**Files:**
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx`
- Modify: `/home/maoyd/siq-research-engine/apps/web/src/pages/pdf/pdfStyles.ts`
- Test: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: existing `selectedTask`, `packageDetail`, `workflowSummary`, `qualitySummary`, `bridgeChecks`, `metrics`.
- Produces: result cards with A-share-like section shells, card density, markdown body styling, and trace workbench framing.

- [ ] **Step 1: Write the failing E2E assertions for result section headings after task selection**

```ts
await recentTask.getByRole('button', { name: '查看结果' }).click()
await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toBeVisible()
await expect(page.getByRole('heading', { name: 'Markdown 结果', exact: true })).toBeVisible()
await expect(page.getByRole('heading', { name: '解析质量报告', exact: true })).toBeVisible()
await expect(page.getByRole('heading', { name: 'HTML/iXBRL 可视化溯源', exact: true })).toBeVisible()
```

- [ ] **Step 2: Run test to verify the current section framing is still out of sync**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: FAIL on the first visual/result assertion that the current layout does not satisfy.

- [ ] **Step 3: Update result card shells to reuse A-share visual rhythm**

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx
<PageSection title="解析结果" description="Markdown 原文、核心证据文件与结构化结果。">
  <div className="apple-card rounded-[24px] p-4 sm:p-6">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h3 className="text-base font-semibold text-text">Markdown 结果</h3>
        <p className="text-xs text-text-muted">{selectedTask.companyName} · {selectedTask.form} · {selectedTask.periodEnd}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">...</div>
    </div>
    <div className="pdf-markdown-body">...</div>
  </div>
</PageSection>
```

```tsx
// /home/maoyd/siq-research-engine/apps/web/src/components/sec/UsSecIngestionPanel.tsx
<PageSection title="HTML/iXBRL 可视化溯源" description="左侧查看 SEC 原始 HTML，右侧查看渲染后的 Markdown section 与表格上下文。">
  <div className="apple-card rounded-[24px] p-4 sm:p-6">
    <div className="grid gap-3 xl:grid-cols-2">
      <iframe title="SEC 原始 HTML" src={rawHtmlUrl} className="h-[520px] w-full rounded-md border border-border bg-white" />
      <div className="h-[520px] overflow-auto rounded-md border border-border bg-white p-4 text-sm leading-6 text-text">...</div>
    </div>
  </div>
</PageSection>
```

- [ ] **Step 4: Run full targeted verification for result expansion**

Run: `cd /home/maoyd/siq-research-engine/apps/web && npm run build && npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium`
Expected: build PASS; Playwright PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/sec/UsSecIngestionPanel.tsx apps/web/src/pages/pdf/pdfStyles.ts apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts
git commit -m "feat: align us sec result panels with parse ui"
```

## Task 5: Final regression and live-page verification

**Files:**
- Modify: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts` (only if final selector hardening is needed)
- Test: `/home/maoyd/siq-research-engine/apps/web/src/features/market-parsing/usSecWorkbench.test.ts`
- Test: `/home/maoyd/siq-research-engine/apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts`

**Interfaces:**
- Consumes: completed visual changes from Tasks 1-4.
- Produces: a verified `/parse-us` workbench that matches the spec and preserves the existing US workflow.

- [ ] **Step 1: Run the unit test suite for the US workbench model**

```bash
cd /home/maoyd/siq-research-engine/apps/web
node --import ./scripts/register-node-test-alias-loader.mjs --test src/features/market-parsing/usSecWorkbench.test.ts
```

- [ ] **Step 2: Run full frontend verification**

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npx playwright test e2e/tests/us-sec-parsing-workbench.spec.ts --project=chromium
```

Expected: all commands PASS.

- [ ] **Step 3: Verify the live page structure in the browser**

```js
const visibleText = await liveTab.playwright.evaluate(() => document.body.innerText.slice(0, 3000));
return visibleText.includes('已下载财报') && visibleText.includes('最近任务') && visibleText.includes('选择最近任务后查看结果');
```

Expected: `true` when the live logged-in `/parse-us` page reflects the updated UI.

- [ ] **Step 4: Commit any final selector or polish fix**

```bash
git add apps/web/e2e/tests/us-sec-parsing-workbench.spec.ts apps/web/src/components/sec apps/web/src/pages/UsParsing.tsx apps/web/src/pages/pdf/pdfStyles.ts
git commit -m "test: verify us sec visual alignment"
```
