import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const taskId = 'doc-preview-task'
const secondTaskId = 'doc-preview-task-next'
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

const secondTask = {
  ...task,
  task_id: secondTaskId,
  filename: 'second-preview-fixture.pdf',
  total_pages: 2,
}

const tasks = [task, secondTask]

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

function fixtureForTask(id: string) {
  const isSecond = id === secondTaskId
  const currentTask = isSecond ? secondTask : task
  const pages = isSecond ? [1, 2] : [1, 2, 3]
  const blocks = isSecond
    ? [
      { block_id: 'second-title', type: 'title', page_number: 1, bbox: [60, 60, 220, 110], bbox_unit: 'pixel', markdown: '# Second fixture' },
      { block_id: 'second-body', type: 'text', page_number: 2, bbox: [100, 120, 460, 260], bbox_unit: 'pixel', markdown: '第二个任务的末页正文。' },
    ]
    : [
      { block_id: 'block-title', type: 'title', page_number: 1, bbox: [60, 60, 220, 110], bbox_unit: 'pixel', markdown: '# Preview fixture' },
      { block_id: 'block-body-1', type: 'text', page_number: 1, bbox: [70, 130, 460, 210], bbox_unit: 'pixel', markdown: '第一页正文定位测试。' },
      { block_id: 'block-second', type: 'text', page_number: 2, bbox: [100, 120, 460, 260], bbox_unit: 'pixel', markdown: '## Second page section\n\n第二页 Markdown 块用于同步 PDF overlay。' },
      { block_id: 'block-third', type: 'text', page_number: 3, bbox: [100, 120, 420, 230], bbox_unit: 'pixel', markdown: '第三页收尾内容。' },
    ]
  const markdown = isSecond
    ? [
      '[PDF_PAGE: 1]',
      '',
      '# Second fixture',
      '',
      '第二个任务第一页从 p1 开始。',
      '',
      '[PDF_PAGE: 2]',
      '',
      '第二个任务的末页正文。',
    ].join('\n')
    : [
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
    ].join('\n')
  const tables = isSecond
    ? []
    : [
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
    ]
  const figures = isSecond
    ? []
    : [
      { image_id: 'figure-1', type: 'image', page_number: 1, bbox: [560, 180, 860, 420], bbox_unit: 'pixel', image_path: 'figures/figure-1.png', caption: '测试图片' },
    ]
  return { currentTask, pages, blocks, markdown, tables, figures }
}

async function mockDocumentPreviewApis(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
    const nativeAnchorClick = window.HTMLAnchorElement.prototype.click
    window.HTMLAnchorElement.prototype.click = function clickAnchor() {
      if (this.href.startsWith('blob:')) return
      nativeAnchorClick.call(this)
    }
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
      await route.fulfill(json({ tasks }))
      return
    }
    const statusMatch = url.pathname.match(/^\/api\/documents\/status\/([^/]+)$/)
    if (statusMatch) {
      const id = decodeURIComponent(statusMatch[1])
      const fixture = fixtureForTask(id)
      await route.fulfill(json({ ...fixture.currentTask, logs: [], log_count: 0 }))
      return
    }
    const resultMatch = url.pathname.match(/^\/api\/documents\/result\/([^/]+)$/)
    if (resultMatch) {
      const id = decodeURIComponent(resultMatch[1])
      const fixture = fixtureForTask(id)
      await route.fulfill(json({
        manifest: { task_id: id, filename: fixture.currentTask.filename, status: 'completed' },
        markdown: fixture.markdown,
        artifacts: {
          'figures/figure-1.png': { exists: true, size: imageBytes.length },
          'reports/failing-open.json': { exists: true, size: 24 },
        },
      }))
      return
    }
    const artifactMatch = url.pathname.match(/^\/api\/documents\/artifact\/([^/]+)\/(.+)$/)
    if (artifactMatch?.[2] === 'quality_report.json') {
      const fixture = fixtureForTask(decodeURIComponent(artifactMatch[1]))
      await route.fulfill(json({
        overall_status: 'ok',
        page_count: fixture.pages.length,
        block_count: fixture.blocks.length,
        table_count: fixture.tables.length,
        image_count: fixture.figures.length,
      }))
      return
    }
    if (artifactMatch?.[2] === 'blocks.json') {
      const fixture = fixtureForTask(decodeURIComponent(artifactMatch[1]))
      await route.fulfill(json({ blocks: fixture.blocks }))
      return
    }
    if (artifactMatch?.[2] === 'layout_blocks.json') {
      const fixture = fixtureForTask(decodeURIComponent(artifactMatch[1]))
      await route.fulfill(json({
        pages: fixture.pages.map((pageNumber) => ({ page_number: pageNumber, width: 1000, height: 1000, bbox_unit: 'pixel' })),
      }))
      return
    }
    if (artifactMatch?.[2] === 'tables.json') {
      const fixture = fixtureForTask(decodeURIComponent(artifactMatch[1]))
      await route.fulfill(json({ physical_tables: fixture.tables }))
      return
    }
    if (url.pathname.startsWith('/api/documents/table-relations/')) {
      await route.fulfill(json({ relations: [] }))
      return
    }
    const figuresMatch = url.pathname.match(/^\/api\/documents\/figures\/([^/]+)$/)
    if (figuresMatch) {
      const fixture = fixtureForTask(decodeURIComponent(figuresMatch[1]))
      await route.fulfill(json({ figures: fixture.figures }))
      return
    }
    if (artifactMatch?.[2] === 'source_map.json') {
      const id = decodeURIComponent(artifactMatch[1])
      const fixture = fixtureForTask(id)
      await route.fulfill(json({
        sources: [
          ...fixture.blocks.map((block) => ({
            block_id: block.block_id,
            page_number: block.page_number,
            open_source_url: `/api/documents/source/${id}/page-image/${block.page_number}`,
          })),
          ...fixture.tables.map((table) => ({
            table_id: table.table_id,
            page_number: table.page_number,
            open_source_url: `/api/documents/source/${id}/page-image/${table.page_number}`,
          })),
          ...fixture.figures.map((figure) => ({
            image_id: figure.image_id,
            page_number: figure.page_number,
            open_source_url: `/api/documents/artifact/${id}/${figure.image_path}`,
          })),
        ],
      }))
      return
    }
    if (/^\/api\/workflow\/document\/[^/]+\/status$/.test(url.pathname)) {
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
      /^\/api\/documents\/source\/[^/]+\/page-image\/\d+$/.test(url.pathname)
      || /^\/api\/documents\/artifact\/[^/]+\/figures\/figure-1\.png$/.test(url.pathname)
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: imageBytes,
      })
      return
    }
    if (/^\/api\/documents\/artifact\/[^/]+\/reports\/failing-open\.json$/.test(url.pathname)) {
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

test('切换到新任务后 active page 重置到新任务首页', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockDocumentPreviewApis(page)

  await page.goto('/documents')
  const firstTaskButton = page.getByRole('button', { name: 'preview-fixture.pdf document', exact: true })
  await expect(firstTaskButton).toBeVisible()
  await Promise.all([
    page.waitForResponse((response) => response.url().includes(`/api/documents/result/${taskId}`) && response.status() === 200),
    firstTaskButton.click(),
  ])
  await expect(page.getByRole('heading', { name: 'preview-fixture.pdf' })).toBeVisible()
  const pageSelect = page.locator('.doc-page-select')
  await expect(pageSelect).toHaveValue('1')
  await pageSelect.selectOption('3')
  await expect(page.getByText('PDF p3')).toBeVisible()

  const secondTaskButton = page.getByRole('button', { name: 'second-preview-fixture.pdf document', exact: true })
  await expect(secondTaskButton).toBeVisible()
  await Promise.all([
    page.waitForResponse((response) => response.url().includes(`/api/documents/result/${secondTaskId}`) && response.status() === 200),
    secondTaskButton.click(),
  ])
  await expect(page.getByRole('heading', { name: 'second-preview-fixture.pdf' })).toBeVisible()
  await expect(pageSelect).toHaveValue('1')
  await expect(page.getByText('PDF p1')).toBeVisible()
  await expect(page.locator('.doc-pdf-page-card').filter({ hasText: 'PDF p3' })).toHaveCount(0)
})

test('标签滚动按钮更新 tab list scrollLeft，产物打开失败和成功状态分离', async ({ page }) => {
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

  await page.getByRole('tab', { name: /预览/ }).click()
  await page.locator('.doc-pdf-page-card').filter({ hasText: 'PDF p1' }).getByRole('button', { name: '打开页图' }).click()
  await expect(page.locator('.doc-error')).toHaveCount(0)
})

test('移动端 select 切换标签后保持选中状态，且不污染预览页码', async ({ page }) => {
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

  const pageSelect = page.locator('.doc-page-select')
  await pageSelect.selectOption('2')
  await expect(pageSelect).toHaveValue('2')
  await expect(page.getByText('PDF p2')).toBeVisible()

  await tabSelect.selectOption('figures')
  await expect(tabSelect).toHaveValue('figures')
  await expect(page.getByText('测试图片')).toBeVisible()

  await tabSelect.selectOption('preview')
  await expect(tabSelect).toHaveValue('preview')
  await expect(pageSelect).toHaveValue('2')
  await expect(page.getByText('PDF p2')).toBeVisible()
})
