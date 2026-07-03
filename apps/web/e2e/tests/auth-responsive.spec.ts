import { expect, test, type Page, type Route } from '@playwright/test'

const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockPublicAuthApis(page: Page) {
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (url.pathname === '/api/auth/me') {
      await route.fulfill(json({ detail: 'Not authenticated' }, 401))
      return
    }
    await route.fulfill(json({ detail: 'mocked' }))
  })
}

async function pageLayoutMetrics(page: Page) {
  return page.evaluate(() => {
    const card = document.querySelector('.auth-card')
    const form = document.querySelector('.auth-form')
    const cardRect = card?.getBoundingClientRect()
    const formRect = form?.getBoundingClientRect()
    return {
      viewportWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      cardLeft: Math.round(cardRect?.left || 0),
      cardRight: Math.round(cardRect?.right || 0),
      cardHeight: Math.round(cardRect?.height || 0),
      formLeft: Math.round(formRect?.left || 0),
      formRight: Math.round(formRect?.right || 0),
    }
  })
}

test.describe('登录注册响应式验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.name} 登录页不预填弱默认且无横向溢出`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockPublicAuthApis(page)

      await page.goto('/login')
      await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
      await expect(page.getByLabel('用户名')).toHaveValue('')
      await expect(page.getByLabel('密码')).toHaveValue('')
      await expect(page.getByText('欢迎来到SIQ')).toBeVisible()
      if (viewport.width >= 1024) {
        await expect(page.getByText('主权智感投研决策引擎')).toBeVisible()
      }

      const metrics = await pageLayoutMetrics(page)
      expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
      expect(metrics.cardLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.cardRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.formLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.formRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.cardHeight).toBeGreaterThan(0)

      await page.screenshot({
        path: testInfo.outputPath(`login-${viewport.name}.png`),
        fullPage: true,
      })
    })

    test(`${viewport.name} 注册页表单控件稳定且无横向溢出`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockPublicAuthApis(page)

      await page.goto('/register')
      await expect(page.getByRole('heading', { name: '创建账户' })).toBeVisible()
      await expect(page.locator('#username')).toBeVisible()
      await expect(page.locator('#email')).toBeVisible()
      await expect(page.locator('#password')).toBeVisible()
      await expect(page.locator('#password2')).toBeVisible()
      await expect(page.getByRole('button', { name: '注册' })).toBeVisible()
      await expect(page.getByRole('button', { name: '返回登录' })).toBeVisible()

      const metrics = await pageLayoutMetrics(page)
      expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
      expect(metrics.cardLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.cardRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.formLeft).toBeGreaterThanOrEqual(0)
      expect(metrics.formRight).toBeLessThanOrEqual(metrics.viewportWidth)
      expect(metrics.cardHeight).toBeGreaterThan(0)

      await page.screenshot({
        path: testInfo.outputPath(`register-${viewport.name}.png`),
        fullPage: true,
      })
    })
  }
})
