import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const mixedTasks = [
  {
    task_id: 'cn-task',
    filename: '贵州茅台_CN_600519_2025-12-31_年报.pdf',
    market: 'CN',
    status: 'completed',
    created_at: '2026-06-27T08:00:00.000Z',
    markdown_ready: true,
  },
  {
    task_id: 'manual-cn-task',
    filename: '手工上传未带市场码.pdf',
    status: 'completed',
    created_at: '2026-06-27T07:00:00.000Z',
    markdown_ready: true,
  },
  {
    task_id: 'hk-task',
    filename: 'Tencent-Holdings_HK_00700_2025-12-31_年报.pdf',
    market: 'HK',
    status: 'completed',
    created_at: '2026-06-27T06:00:00.000Z',
    markdown_ready: true,
  },
  {
    task_id: 'us-task',
    filename: 'NVIDIA-US-manual-upload.pdf',
    market: 'US',
    status: 'completed',
    created_at: '2026-06-27T05:00:00.000Z',
    markdown_ready: true,
  },
  {
    task_id: 'doc-annual-report-task',
    filename: '安 纳 达_2025年年度报告.pdf',
    market: 'DOC',
    status: 'processing',
    created_at: '2026-06-27T04:00:00.000Z',
    markdown_ready: false,
  },
  {
    task_id: 'doc-legacy-bridge-task',
    filename: '文档解析上传未显式市场.pdf',
    status: 'processing',
    created_at: '2026-06-27T03:00:00.000Z',
    markdown_ready: false,
  },
]

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockPdfParsingApis(page: Page) {
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
    if (url.pathname === '/api/pdf/health') {
      await route.fulfill(json({ mineru: true, vlm: true, submit_ready: true }))
      return
    }
    if (url.pathname === '/api/pdf/tasks') {
      await route.fulfill(json({ tasks: mixedTasks }))
      return
    }
    if (url.pathname === '/api/downloads/reports') {
      await route.fulfill(json({ reports: [] }))
      return
    }
    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })
}

test.describe('财报解析市场隔离', () => {
  test('A 股解析页只展示 CN 和未标记的历史任务', async ({ page }) => {
    await mockPdfParsingApis(page)

    await page.goto('/parse')
    await expect(page.getByRole('heading', { name: '智能解析' })).toBeVisible()
    await expect(page.getByText('贵州茅台_CN_600519_2025-12-31_年报.pdf')).toBeVisible()
    await expect(page.getByText('手工上传未带市场码.pdf')).toBeVisible()
    await expect(page.getByText('Tencent-Holdings_HK_00700_2025-12-31_年报.pdf')).toHaveCount(0)
    await expect(page.getByText('NVIDIA-US-manual-upload.pdf')).toHaveCount(0)
    await expect(page.getByText('安 纳 达_2025年年度报告.pdf')).toHaveCount(0)
    await expect(page.getByText('文档解析上传未显式市场.pdf')).toHaveCount(0)
  })

  test('移动端 A 股任务列表保持市场隔离且不横向溢出', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockPdfParsingApis(page)

    await page.goto('/parse')
    await expect(page.getByRole('heading', { name: '智能解析' })).toBeVisible()
    await expect(page.getByText('贵州茅台_CN_600519_2025-12-31_年报.pdf')).toBeVisible()
    await expect(page.getByText('手工上传未带市场码.pdf')).toBeVisible()
    await expect(page.getByText('Tencent-Holdings_HK_00700_2025-12-31_年报.pdf')).toHaveCount(0)
    await expect(page.getByText('NVIDIA-US-manual-upload.pdf')).toHaveCount(0)
    await expect(page.getByText('安 纳 达_2025年年度报告.pdf')).toHaveCount(0)
    await expect(page.getByText('文档解析上传未显式市场.pdf')).toHaveCount(0)

    const layout = await page.evaluate(() => {
      const viewportWidth = document.documentElement.clientWidth
      const selectors = ['.pdf-task-item', '.task-actions', '.pdf-task-action']
      const boxes = selectors.flatMap((selector) =>
        Array.from(document.querySelectorAll<HTMLElement>(selector)).map((element) => {
          const rect = element.getBoundingClientRect()
          return {
            selector,
            left: rect.left,
            right: rect.right,
            height: rect.height,
          }
        }),
      )
      return {
        viewportWidth,
        scrollWidth: document.documentElement.scrollWidth,
        boxes,
      }
    })

    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
    expect(layout.boxes.length).toBeGreaterThan(0)
    for (const box of layout.boxes) {
      expect(box.left).toBeGreaterThanOrEqual(-1)
      expect(box.right).toBeLessThanOrEqual(layout.viewportWidth + 1)
      if (box.selector === '.pdf-task-action') {
        expect(box.height).toBeGreaterThanOrEqual(44)
      }
    }
  })

  test('美股 PDF 兼容入口只展示 US 任务', async ({ page }) => {
    await mockPdfParsingApis(page)

    await page.goto('/parse?market=US')
    await expect(page.getByRole('heading', { name: '美股 PDF 解析' })).toBeVisible()
    await expect(page.getByText('NVIDIA-US-manual-upload.pdf')).toBeVisible()
    await expect(page.getByText('贵州茅台_CN_600519_2025-12-31_年报.pdf')).toHaveCount(0)
    await expect(page.getByText('手工上传未带市场码.pdf')).toHaveCount(0)
    await expect(page.getByText('Tencent-Holdings_HK_00700_2025-12-31_年报.pdf')).toHaveCount(0)
    await expect(page.getByText('安 纳 达_2025年年度报告.pdf')).toHaveCount(0)
  })
})
