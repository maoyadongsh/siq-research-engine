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
  total_pages: 1,
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
        markdown: '[PDF_PAGE: 1]\n\n# Preview fixture\n\n表格和图片定位测试。',
        artifacts: { 'figures/figure-1.png': { exists: true, size: imageBytes.length } },
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/quality_report.json`) {
      await route.fulfill(json({ overall_status: 'ok', page_count: 1, block_count: 2, table_count: 1, image_count: 1 }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/blocks.json`) {
      await route.fulfill(json({
        blocks: [
          { block_id: 'block-title', type: 'title', page_number: 1, bbox: [60, 60, 220, 110], bbox_unit: 'pixel', markdown: '# Preview fixture' },
        ],
      }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/layout_blocks.json`) {
      await route.fulfill(json({ pages: [{ page_number: 1, width: 1000, height: 1000, bbox_unit: 'pixel' }] }))
      return
    }
    if (url.pathname === `/api/documents/artifact/${taskId}/tables.json`) {
      await route.fulfill(json({
        physical_tables: [
          { table_id: 'table-1', block_id: 'block-table-1', title: '测试表格', page_number: 1, bbox: [120, 180, 520, 420], bbox_unit: 'pixel' },
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
      || url.pathname === `/api/documents/artifact/${taskId}/figures/figure-1.png`
    ) {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: imageBytes,
      })
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

  await page.getByRole('tab', { name: /图片/ }).click()
  const figureImage = page.locator('img.doc-figure-image')
  await expect(figureImage).toBeVisible()
  await expect(page.getByText('测试图片')).toBeVisible()
})
