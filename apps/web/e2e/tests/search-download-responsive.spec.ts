import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockSearchDownloadApis(page: Page) {
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
    if (url.pathname === '/api/market-report-health') {
      await route.fulfill(json({
        report_finder: {
          markets: {
            CN: { report_search_ready: true, required_config: [] },
            HK: { report_search_ready: true, required_config: [] },
            US: { report_search_ready: true, required_config: [] },
            EU: { report_search_ready: true, required_config: [] },
            JP: { report_search_ready: true, required_config: [] },
            KR: { report_search_ready: true, required_config: [] },
          },
        },
      }))
      return
    }
    if (url.pathname === '/api/downloads/reports') {
      await route.fulfill(json({ reports: [] }))
      return
    }
    if (url.pathname === '/api/v1/reports/assist') {
      await route.fulfill(json({
        intent: {
          market: 'US',
          company_query: 'Apple Inc.',
          ticker: 'AAPL',
          company_id: '0000320193',
          report_year: 2024,
          report_types: ['annual', '10-K'],
        },
      }))
      return
    }
    if (url.pathname === '/api/v1/company/resolve') {
      await route.fulfill(json({ company_name: 'Apple Inc.', ticker: 'AAPL' }))
      return
    }
    if (url.pathname === '/api/v1/reports/recent') {
      await route.fulfill(json({ reports: [] }))
      return
    }
    await route.fulfill(json({ reports: [], items: [], data: [], results: [] }))
  })
}

test.describe('搜索下载响应式验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.width}x${viewport.height} 智能检索表单无横向溢出`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockSearchDownloadApis(page)

      await page.goto('/search?market=CN&q=%E6%AF%94%E4%BA%9A%E8%BF%AA&year=2025')
      await page.waitForLoadState('networkidle')
      await expect(page.getByRole('heading', { name: '搜索下载' })).toBeVisible()
      await expect(page.locator('.smart-search-panel')).toBeVisible()
      await expect(page.locator('.search-download-form')).toBeVisible()

      const layout = await page.evaluate(() => {
        const selectors = ['.search-download-query', '.smart-search-panel', '.search-download-form', '.search-download-submit']
        const boxes = selectors.map((selector) => {
          const element = document.querySelector(selector)
          if (!element) return null
          const rect = element.getBoundingClientRect()
          return {
            selector,
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          }
        }).filter((box): box is NonNullable<typeof box> => Boolean(box))
        return {
          boxes,
          scrollWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
        }
      })

      expect(layout.boxes).toHaveLength(4)
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
      for (const box of layout.boxes) {
        expect(box.left).toBeGreaterThanOrEqual(0)
        expect(box.right).toBeLessThanOrEqual(layout.viewportWidth)
        expect(box.width).toBeGreaterThan(0)
        expect(box.height).toBeGreaterThan(0)
      }

      await page.screenshot({
        path: testInfo.outputPath(`search-download-${viewport.name}.png`),
        fullPage: true,
      })
    })
  }

  test('搜索条件随浏览器前进后退回灌表单，智能解析原子保留完整 URL', async ({ page }) => {
    await mockSearchDownloadApis(page)
    await page.goto('/search?market=CN&q=BYD&year=2025&exchange=SSE&ask=%E6%89%BE%E6%AF%94%E4%BA%9A%E8%BF%AA%E5%B9%B4%E6%8A%A5')

    const queryInput = page.locator('.query-field input')
    const yearSelect = page.locator('.year-field select')
    const smartInput = page.locator('.smart-search-input')
    await expect(queryInput).toHaveValue('BYD')
    await expect(yearSelect).toHaveValue('2025')

    await page.getByRole('button', { name: /美国市场/ }).click()
    await expect(page).toHaveURL(/market=US/)
    await expect(queryInput).toHaveValue('BYD')

    await page.goBack()
    await expect(page).toHaveURL(/market=CN/)
    await expect(queryInput).toHaveValue('BYD')
    await expect(yearSelect).toHaveValue('2025')
    await expect(page.locator('.filter-field select')).toHaveValue('SSE')

    await smartInput.fill('找 Apple 2024 年 10-K')
    await page.getByRole('button', { name: '智能检索' }).click()
    await expect(page).toHaveURL(/market=US/)
    await expect(page).toHaveURL(/q=Apple\+Inc\./)
    await expect(page).toHaveURL(/year=2024/)
    const params = new URL(page.url()).searchParams
    expect(params.get('ask')).toBe('找 Apple 2024 年 10-K')
    expect(params.get('exchange')).toBeNull()
    expect(params.get('country')).toBeNull()
    await expect(queryInput).toHaveValue('Apple Inc.')
    await expect(yearSelect).toHaveValue('2024')
  })
})
