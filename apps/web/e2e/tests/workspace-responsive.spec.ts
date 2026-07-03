import { expect, test } from '@playwright/test'
import { mockAuthenticatedWorkspace } from '../support/mockApi'

const viewports = [
  { name: 'mobile', width: 390, height: 844, columns: 2 },
  { name: 'tablet', width: 768, height: 1024, columns: 2 },
  { name: 'desktop-short', width: 1366, height: 768, columns: 3 },
  { name: 'desktop', width: 1440, height: 900, columns: 3 },
  { name: 'wide', width: 1920, height: 1080, columns: 6 },
] as const

test.describe('工作平台响应式验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.width}x${viewport.height} 无横向溢出且流程入口排版稳定`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockAuthenticatedWorkspace(page)

      await page.goto('/')
      await page.waitForLoadState('networkidle')
      await expect(page.getByRole('heading', { name: '工作平台' })).toBeVisible()

      const grid = page.locator('.workflow-step-grid')
      await expect(grid).toBeVisible()
      await expect(page.locator('.workflow-step-card')).toHaveCount(6)
      await grid.scrollIntoViewIfNeeded()

      const layout = await page.evaluate(() => {
        const cards = Array.from(document.querySelectorAll('.workflow-step-card')).map((element) => {
          const rect = element.getBoundingClientRect()
          return {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          }
        })
        const uniqueColumns = [...new Set(cards.map((card) => card.x))]
        const uniqueRows = [...new Set(cards.map((card) => card.y))]
        const viewportWidth = window.innerWidth
        const scrollWidth = document.documentElement.scrollWidth

        return {
          cardCount: cards.length,
          columnCount: uniqueColumns.length,
          rowCount: uniqueRows.length,
          minLeft: Math.min(...cards.map((card) => card.x)),
          maxRight: Math.max(...cards.map((card) => card.right)),
          minCardWidth: Math.min(...cards.map((card) => card.width)),
          minCardHeight: Math.min(...cards.map((card) => card.height)),
          viewportWidth,
          scrollWidth,
          noHorizontalOverflow: scrollWidth <= viewportWidth + 1,
        }
      })

      expect(layout.cardCount).toBe(6)
      expect(layout.columnCount).toBe(viewport.columns)
      expect(layout.rowCount).toBe(Math.ceil(6 / viewport.columns))
      expect(layout.noHorizontalOverflow).toBe(true)
      expect(layout.minLeft).toBeGreaterThanOrEqual(0)
      expect(layout.maxRight).toBeLessThanOrEqual(layout.viewportWidth)
      expect(layout.minCardWidth).toBeGreaterThanOrEqual(viewport.width < 640 ? 160 : 140)
      expect(layout.minCardHeight).toBeGreaterThanOrEqual(110)

      await page.screenshot({
        path: testInfo.outputPath(`workspace-${viewport.name}.png`),
        fullPage: true,
      })
    })
  }

  test('移动端工作平台与系统平台上下区块宽度保持一致', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockAuthenticatedWorkspace(page)

    async function pageWidthMetrics(path: string, heading: string) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await expect(page.getByRole('heading', { name: heading })).toBeVisible()
      await page.locator('.workflow-step-grid').scrollIntoViewIfNeeded()

      return page.evaluate(() => {
        const viewportWidth = window.innerWidth
        const mainInner = document.querySelector('#main-content > div')
        const hero = document.querySelector('.dashboard-hero')
        const workflow = document.querySelector('.workflow-step-grid')
        const mainRect = mainInner?.getBoundingClientRect()
        const heroRect = hero?.getBoundingClientRect()
        const workflowRect = workflow?.getBoundingClientRect()
        return {
          viewportWidth,
          scrollWidth: document.documentElement.scrollWidth,
          mainLeft: Math.round(mainRect?.left || 0),
          mainRight: Math.round(mainRect?.right || 0),
          mainWidth: Math.round(mainRect?.width || 0),
          heroLeft: Math.round(heroRect?.left || 0),
          heroRight: Math.round(heroRect?.right || 0),
          heroWidth: Math.round(heroRect?.width || 0),
          workflowLeft: Math.round(workflowRect?.left || 0),
          workflowRight: Math.round(workflowRect?.right || 0),
          workflowWidth: Math.round(workflowRect?.width || 0),
        }
      })
    }

    const workspace = await pageWidthMetrics('/', '工作平台')
    await page.screenshot({
      path: testInfo.outputPath('workspace-mobile-width.png'),
      fullPage: true,
    })

    const system = await pageWidthMetrics('/system-dashboard', '公司研究工作台')
    await page.screenshot({
      path: testInfo.outputPath('system-dashboard-mobile-width.png'),
      fullPage: true,
    })

    for (const metrics of [workspace, system]) {
      expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
      expect(metrics.heroLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.heroRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.workflowLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.workflowRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.heroLeft).toBe(metrics.workflowLeft)
      expect(metrics.heroRight).toBe(metrics.workflowRight)
      expect(Math.abs(metrics.heroWidth - metrics.workflowWidth)).toBeLessThanOrEqual(1)
      expect(metrics.heroLeft).toBeGreaterThanOrEqual(metrics.mainLeft)
      expect(metrics.heroRight).toBeLessThanOrEqual(metrics.mainRight)
    }

    expect(system.heroWidth).toBe(workspace.heroWidth)
    expect(system.workflowWidth).toBe(workspace.workflowWidth)
  })

  test('移动端侧边栏导航入口可打开和关闭抽屉', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockAuthenticatedWorkspace(page)

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '工作平台' })).toBeVisible()

    const sidebar = page.locator('#app-sidebar')
    await expect(page.getByRole('button', { name: '打开导航' })).toBeVisible()
    await expect
      .poll(() => sidebar.evaluate((element) => Math.round(element.getBoundingClientRect().right)))
      .toBeLessThanOrEqual(1)

    await page.getByRole('button', { name: '打开导航' }).click()
    await expect(page.getByRole('button', { name: '关闭导航' })).toBeVisible()
    await expect
      .poll(() => sidebar.evaluate((element) => Math.round(element.getBoundingClientRect().left)))
      .toBeGreaterThanOrEqual(0)

    await page.mouse.click(360, 120)
    await expect(page.getByRole('button', { name: '打开导航' })).toBeVisible()
    await expect
      .poll(() => sidebar.evaluate((element) => Math.round(element.getBoundingClientRect().right)))
      .toBeLessThanOrEqual(1)
  })
})
