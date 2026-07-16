import { expect, test, type Page } from '@playwright/test'

import { mockAuthenticatedWorkspace } from '../support/mockApi'

const dealId = 'DEAL-YUSHU-2026-001'

const routes = [
  { path: '/primary-market', heading: '一级市场工作平台' },
  { path: '/deals', heading: '项目管理' },
  { path: `/primary-market/materials?dealId=${dealId}`, heading: '一级市场材料中心' },
  { path: `/primary-market/post-investment?dealId=${dealId}`, heading: '一级市场投后管理' },
] as const

const viewports = [
  { name: 'small-phone', width: 375, height: 812 },
  { name: 'large-phone', width: 430, height: 932 },
] as const

async function expectMobileLayout(page: Page) {
  const metrics = await page.evaluate(() => {
    const shell = document.querySelector('.page-shell')
    const shellRect = shell?.getBoundingClientRect()
    const interactive = Array.from(shell?.querySelectorAll(
      'button, [data-slot="button"], select, textarea, input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])',
    ) || [])
      .filter((element) => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
      })
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          label: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName,
          height: Math.round(rect.height),
        }
      })

    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      scrollY: Math.round(window.scrollY),
      shellLeft: Math.round(shellRect?.left || 0),
      shellRight: Math.round(shellRect?.right || 0),
      undersized: interactive.filter((item) => item.height < 44),
    }
  })

  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
  expect(metrics.scrollY).toBeLessThanOrEqual(1)
  expect(metrics.shellLeft).toBeGreaterThanOrEqual(0)
  expect(metrics.shellRight).toBeLessThanOrEqual(metrics.viewportWidth + 1)
  expect(metrics.undersized).toEqual([])
  await expect(page.getByRole('button', { name: '打开财报助手' })).toHaveCount(0)
}

test.describe('一级市场移动端布局', () => {
  for (const viewport of viewports) {
    test(`${viewport.name} 四个数据页无溢出且触控尺寸稳定`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockAuthenticatedWorkspace(page)

      for (const route of routes) {
        await page.goto(route.path)
        await expect(page.getByRole('heading', { name: route.heading })).toBeVisible()
        await expectMobileLayout(page)
      }
    })
  }

  test('390px 使用紧凑网格、项目卡片和安全的会议滚动', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockAuthenticatedWorkspace(page)

    await page.goto('/primary-market')
    await expect(page.getByRole('heading', { name: '一级市场工作平台' })).toBeVisible()
    await expect(page.locator('.primary-market-metric-grid > *')).toHaveCount(5)
    await expect(page.locator('.primary-market-metric-grid > *').first()).toHaveCSS('grid-column-start', '1')
    await expect(page.locator('.primary-market-metric-grid > *').first()).toHaveCSS('grid-column-end', '-1')

    await page.goto('/deals')
    await expect(page.getByRole('heading', { name: '项目管理' })).toBeVisible()
    await expect(page.getByLabel('项目卡片列表')).toBeVisible()
    await expect(page.getByRole('link', { name: /查看项目详情/ }).first()).toBeVisible()
    await expect(page.locator('table')).toBeHidden()

    await page.goto(`/primary-market/materials?dealId=${dealId}`)
    await expect(page.getByRole('heading', { name: '一级市场材料中心' })).toBeVisible()
    await expect(page.locator('.primary-market-material-types')).toHaveCSS('grid-template-columns', /.+ .+/)
    await expect(page.locator('.primary-market-pipeline-grid')).toHaveCSS('grid-template-columns', /.+ .+/)

    await page.goto(`/primary-market/post-investment?dealId=${dealId}`)
    await expect(page.getByRole('heading', { name: '一级市场投后管理' })).toBeVisible()
    await expect(page.locator('.primary-market-metric-grid > *')).toHaveCount(4)
  })
})
