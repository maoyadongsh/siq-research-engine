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
})
