import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const taskId = 'doc-preview-task'
const imageBytes = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000"><rect width="1000" height="1000" fill="white"/></svg>')

const task = {
  task_id: taskId,
  filename: 'preview-fixture.pdf',
  status: 'completed',
  stage: 'completed',
  progress_percent: 100,
  total_pages: 3,
  markdown_ready: true,
  created_at: '2026-06-30T08:00:00.000Z',
  completed_at: '2026-06-30T08:01:00.000Z',
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockDocumentPreviewApis(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (url.pathname === '/api/auth/me') {
      await route.fulfill(json(e2eUser))
      return
    }
    if (url.pathname === '/api/documents/health') {
      await route.fulfill(json({ status: 'ok' }))
      return
    }
    if (url.pathname === '/api/documents/quota') {
      await route.fulfill(json({ remaining: 9 }))
      return
    }
    if (url.pathname === '/api/documents/tasks') {
      await route.fulfill(json({ tasks: [task] }))
      return
    }
    if (url.pathname === `/api/documents/status/${taskId}`) {
      await route.fulfill(json({ ...task, logs: [], log_count: 0 }))
      return
    }
    if (url.pathname === `/api/documents/result/${taskId}`) {
      await route.fulfill(json({
        manifest: { task_id: taskId, filename: task.filename, status: 'completed' },
        markdown: [
          '[PDF_PAGE: 1]',
          '',
          '# Preview fixture',
          '',
          '第一页正文定位测试。',
          '',
          '[PDF_PAGE: 2]',
          '',
          '## Second page section',
          '',
          '第二页 Markdown 块用于同步 PDF overlay。',
          '',
          '[PDF_PAGE: 3]',
          '',
          '第三页收尾内容。',
        ].join('\n'),
        artifacts: {
          'figures/figure-1.png': { exists: true, size: imageBytes.length },
          'reports/failing-open.json': { exists: true, size: 24 },
        },
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/quality_report.json`) {
      await route.fulfill(json({ overall_status: 'ok', page_count: 3, block_count: 4, table_count: 1, image_count: 1 }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/blocks.json`) {
      await route.fulfill(json({
        blocks: [
          { block_id: 'block-title', type: 'title', page_number: 1, bbox: [60, 60, 220, 110], bbox_unit: 'pixel', markdown: '# Preview fixture' },
          { block_id: 'block-body-1', type: 'text', page_number: 1, bbox: [70, 130, 460, 210], bbox_unit: 'pixel', markdown: '第一页正文定位测试。' },
          { block_id: 'block-second', type: 'text', page_number: 2, bbox: [100, 120, 460, 260], bbox_unit: 'pixel', markdown: '## Second page section\n\n第二页 Markdown 块用于同步 PDF overlay。' },
          { block_id: 'block-third', type: 'text', page_number: 3, bbox: [100, 120, 420, 230], bbox_unit: 'pixel', markdown: '第三页收尾内容。' },
        ],
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/layout_blocks.json`) {
      await route.fulfill(json({
        pages: [
          { page_number: 1, width: 1000, height: 1000, bbox_unit: 'pixel' },
          { page_number: 2, width: 1000, height: 1000, bbox_unit: 'pixel' },
          { page_number: 3, width: 1000, height: 1000, bbox_unit: 'pixel' },
        ],
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/tables.json`) {
      await route.fulfill(json({
        physical_tables: [
          {
            table_id: 'table-1',
            block_id: 'block-table-1',
            title: '测试表格',
            page_number: 1,
            bbox: [120, 180, 520, 420],
            bbox_unit: 'pixel',
            quality: { row_count: 2, column_count: 3 },
            markdown: '| 项目 | Q1 | Q2 |\n| --- | ---: | ---: |\n| 营收 | 10 | 12 |',
          },
        ],
      }))
      return
    }
    if (url.pathname === `/api/documents/table-relations/${taskId}`) {
      await route.fulfill(json({ relations: [] }))
      return
    }
    if (url.pathname === `/api/documents/figures/${taskId}`) {
      await route.fulfill(json({
        figures: [
          { image_id: 'figure-1', type: 'image', page_number: 1, bbox: [560, 180, 860, 420], bbox_unit: 'pixel', image_path: 'figures/figure-1.png', caption: '测试图片' },
        ],
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/source_map.json`) {
      await route.fulfill(json({
        sources: [
          { block_id: 'block-title', page_number: 1, open_source_url: `/api/documents/source/${taskId}/page-image/1` },
          { block_id: 'block-body-1', page_number: 1, open_source_url: `/api/documents/source/${taskId}/page-image/1` },
          { block_id: 'block-second', page_number: 2, open_source_url: `/api/documents/source/${taskId}/page-image/2` },
          { block_id: 'block-third', page_number: 3, open_source_url: `/api/documents/source/${taskId}/page-image/3` },
          { table_id: 'table-1', page_number: 1, open_source_url: `/api/documents/source/${taskId}/page-image/1` },
          { image_id: 'figure-1', page_number: 1, open_source_url: `/api/documents/artifact/${taskId}/figures/figure-1.png` },
        ],
      }))
      return
    }
    if (url.pathname === `/api/workflow/document/${taskId}/status`) {
      await route.fulfill(json({ targets: {} }))
      return
    }
    if (url.pathname === '/api/documents/import/mineru/candidates') {
      await route.fulfill(json({ candidates: [] }))
      return
    }
    if (url.pathname === '/api/documents/extraction/templates') {
      await route.fulfill(json({ templates: [] }))
      return
    }
    if (
      url.pathname === `/api/documents/source/${taskId}/page-image/1`
      || url.pathname === `/api/documents/source/${taskId}/page-image/2`
      || url.pathname === `/api/documents/source/${taskId}/page-image/3`
      || url.pathname === `/api/documents/artifact/${taskId}/figures/figure-1.png`
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: imageBytes,
      })
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/reports/failing-open.json`) {
      await route.fulfill(json({ detail: '产物打开失败：fixture denied' }, 503))
      return
    }
    await route.fulfill(json({}))
  })
}

test('文档结果工作台加载受保护页图、overlay 和图片产物', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockDocumentPreviewApis(page)

  await page.goto(`/documents?task=${taskId}`)
  await expect(page.getByRole('heading', { name: '文档解析', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'preview-fixture.pdf' })).toBeVisible()
  await expect(page.getByText('PDF p1')).toBeVisible()

  const pageImage = page.locator('img.doc-pdf-page-image')
  await expect(pageImage).toBeVisible()
  await expect(page.locator('.doc-auth-image-state')).toHaveCount(0)

  const tableOverlay = page.getByRole('button', { name: /定位 table-1/ })
  await expect(tableOverlay).toBeVisible()
  await tableOverlay.click()
  await expect(tableOverlay).toHaveClass(/is-focused/)

  await page.getByRole('tab', { name: /表格/ }).click()
  const tablePane = page.locator('[data-slot="tabs-content"][data-state="active"] .doc-table-list')
  await expect(tablePane).toBeVisible()
  const tableRow = tablePane.locator('.doc-data-row').filter({ hasText: '测试表格' }).first()
  await expect(tableRow).toBeVisible()
  await expect(tableRow.getByText('页码 1 · 2 行 · 3 列')).toBeVisible()
  const tablePrimaryAction = tableRow.getByRole('button', { name: '定位原页', exact: true })
  await expect(tablePrimaryAction).toBeVisible()
  const tableTabLayout = await page.evaluate(() => {
    const selectors = {
      tablePane: '[data-slot="tabs-content"][data-state="active"] .doc-table-list',
      tableRow: '[data-slot="tabs-content"][data-state="active"] .doc-table-list .doc-data-row',
      primaryAction: '[data-slot="tabs-content"][data-state="active"] .doc-table-list .doc-data-row button',
      tabList: '[data-slot="tabs-list"]',
      tabSelect: 'select[aria-label="切换结果标签"]',
      assistantFab: '.agent-chat-fab',
    }
    const rectFor = (selector: string, text?: string) => {
      const elements = Array.from(document.querySelectorAll<HTMLElement>(selector))
      const element = text ? elements.find((candidate) => candidate.innerText.includes(text)) : elements[0]
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const overlaps = (
      a: NonNullable<ReturnType<typeof rectFor>>,
      b: NonNullable<ReturnType<typeof rectFor>>,
    ) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
    const tablePaneRect = rectFor(selectors.tablePane)
    const tableRowRect = rectFor(selectors.tableRow, '测试表格')
    const primaryActionRect = rectFor(selectors.primaryAction, '定位原页')
    const controlRects = [rectFor(selectors.tabList), rectFor(selectors.tabSelect), rectFor(selectors.assistantFab)].filter(
      (rect): rect is NonNullable<typeof rect> => Boolean(rect && rect.width > 0 && rect.height > 0),
    )

    return {
      hasRects: Boolean(tablePaneRect && tableRowRect && primaryActionRect),
      rowInsidePane: Boolean(
        tablePaneRect && tableRowRect
          && tableRowRect.left >= tablePaneRect.left
          && tableRowRect.right <= tablePaneRect.right
          && tableRowRect.top >= tablePaneRect.top
          && tableRowRect.bottom <= tablePaneRect.bottom,
      ),
      actionInsideRow: Boolean(
        tableRowRect && primaryActionRect
          && primaryActionRect.left >= tableRowRect.left
          && primaryActionRect.right <= tableRowRect.right
          && primaryActionRect.top >= tableRowRect.top
          && primaryActionRect.bottom <= tableRowRect.bottom,
      ),
      rowControlCollisions: tableRowRect ? controlRects.filter((rect) => overlaps(tableRowRect, rect)).length : -1,
      actionControlCollisions: primaryActionRect ? controlRects.filter((rect) => overlaps(primaryActionRect, rect)).length : -1,
    }
  })
  expect(tableTabLayout.hasRects).toBe(true)
  expect(tableTabLayout.rowInsidePane).toBe(true)
  expect(tableTabLayout.actionInsideRow).toBe(true)
  expect(tableTabLayout.rowControlCollisions).toBe(0)
  expect(tableTabLayout.actionControlCollisions).toBe(0)

  await page.getByRole('tab', { name: /图片/ }).click()
  const figureImage = page.locator('img.doc-figure-image')
  await expect(figureImage).toBeVisible()
  await expect(page.getByText('测试图片')).toBeVisible()

  await page.getByRole('tab', { name: /产物/ }).click()
  const artifactRow = page.locator('.doc-artifact-list .doc-data-row').filter({ hasText: 'figures/figure-1.png' })
  await expect(artifactRow.getByRole('button', { name: '打开', exact: true })).toBeVisible()
})

test('Markdown 块 focus 后同步 PDF overlay 与 markdown focused 状态', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockDocumentPreviewApis(page)

  await page.goto(`/documents?task=${taskId}`)
  await expect(page.getByText('PDF p1')).toBeVisible()
  await page.locator('.doc-page-select').selectOption('2')
  await expect(page.getByText('PDF p2')).toBeVisible()

  const markdownBlock = page.locator('.doc-md-block').filter({ hasText: 'Second page section' }).first()
  await expect(markdownBlock).toBeVisible()
  await markdownBlock.click()

  await expect(page.getByText('PDF p2')).toBeVisible()
  await expect(markdownBlock).toHaveClass(/is-focused/)
  await expect(page.getByRole('button', { name: /定位 block-second/ })).toHaveClass(/is-focused/)
  await expect(page.locator('.doc-pdf-page-card').filter({ hasText: 'PDF p1' })).toHaveCount(0)
})

test('上一页、下一页和页码 select 与预览页同步', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockDocumentPreviewApis(page)

  await page.goto(`/documents?task=${taskId}`)
  const pageSelect = page.locator('.doc-page-select')
  await expect(pageSelect).toHaveValue('1')
  await expect(page.getByText('PDF p1')).toBeVisible()

  await page.getByRole('button', { name: '下一页' }).click()
  await expect(pageSelect).toHaveValue('2')
  await expect(page.getByText('PDF p2')).toBeVisible()
  await expect(page.locator('.doc-md-block').filter({ hasText: 'Second page section' })).toBeVisible()

  await pageSelect.selectOption('3')
  await expect(page.getByText('PDF p3')).toBeVisible()
  await expect(page.locator('.doc-md-block').filter({ hasText: '第三页收尾内容' })).toBeVisible()

  await page.getByRole('button', { name: '上一页' }).click()
  await expect(pageSelect).toHaveValue('2')
  await expect(page.getByText('PDF p2')).toBeVisible()
  await expect(page.locator('.doc-pdf-page-card').filter({ hasText: 'PDF p3' })).toHaveCount(0)
})

test('标签滚动按钮更新 tab list scrollLeft，产物打开失败显示错误', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 720 })
  await mockDocumentPreviewApis(page)

  await page.goto(`/documents?task=${taskId}`)
  const tabList = page.locator('[data-slot="tabs-list"]')
  await expect(tabList).toBeVisible()
  await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-slot="tabs-list"]')
    if (!el) throw new Error('missing tabs list')
    Object.defineProperty(el, 'scrollWidth', { configurable: true, value: 1200 })
    Object.defineProperty(el, 'clientWidth', { configurable: true, value: 300 })
    let scrollLeft = 0
    Object.defineProperty(el, 'scrollLeft', {
      configurable: true,
      get: () => scrollLeft,
      set: (value) => {
        scrollLeft = Math.max(0, Number(value) || 0)
      },
    })
    Object.defineProperty(el, 'scrollBy', {
      configurable: true,
      value: ({ left }: ScrollToOptions) => {
        el.scrollLeft += Number(left || 0)
        el.dispatchEvent(new Event('scroll'))
      },
    })
  })

  await page.getByRole('button', { name: '向右滚动标签' }).click()
  await expect.poll(async () => tabList.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)
  await page.getByRole('button', { name: '向左滚动标签' }).click()
  await expect.poll(async () => tabList.evaluate((el) => el.scrollLeft)).toBe(0)

  await page.getByRole('tab', { name: /产物/ }).click()
  const failingArtifact = page.locator('.doc-artifact-list .doc-data-row').filter({ hasText: 'reports/failing-open.json' })
  await failingArtifact.getByRole('button', { name: '打开', exact: true }).click()
  await expect(page.locator('.doc-error')).toContainText('产物打开失败：fixture denied')
})

test('移动端 select 切换标签后保持选中状态', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockDocumentPreviewApis(page)

  await page.goto(`/documents?task=${taskId}`)
  const tabSelect = page.getByLabel('切换结果标签')
  await expect(tabSelect).toBeVisible()
  await expect(tabSelect).toHaveValue('preview')

  await tabSelect.selectOption('tables')
  await expect(tabSelect).toHaveValue('tables')
  await expect(page.locator('[data-slot="tabs-content"][data-state="active"] .doc-table-list')).toBeVisible()

  await tabSelect.selectOption('preview')
  await expect(tabSelect).toHaveValue('preview')
  await expect(page.getByText('PDF p1')).toBeVisible()
})
