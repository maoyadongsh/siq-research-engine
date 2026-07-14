import { expect, test, type Page, type Route } from '@playwright/test'

import { e2eUser } from '../support/mockApi'

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function installFlagOffHarness(page: Page) {
  let meetingApiRequests = 0

  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')

    Object.defineProperty(window, '__meetingMediaRequests', {
      configurable: true,
      value: 0,
      writable: true,
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: async () => [],
        getUserMedia: async () => {
          const state = window as typeof window & { __meetingMediaRequests: number }
          state.__meetingMediaRequests += 1
          throw new Error('Meeting routes must not request media while disabled')
        },
      },
    })
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.startsWith('/api/meetings')) {
      meetingApiRequests += 1
      return route.fulfill(json({ detail: { code: 'MEETINGS_DISABLED' } }, 503))
    }
    if (path === '/api/auth/me') return route.fulfill(json(e2eUser))
    return route.fulfill(json({ items: [], sessions: [], messages: [] }))
  })

  return {
    meetingApiRequests: () => meetingApiRequests,
    mediaRequests: () => page.evaluate(() => (
      window as typeof window & { __meetingMediaRequests: number }
    ).__meetingMediaRequests),
  }
}

test('会议开关关闭时隐藏入口并阻止直接路由启动会议能力', async ({ page }) => {
  const harness = await installFlagOffHarness(page)

  await page.goto('/meetings')
  await expect(page.getByRole('heading', { name: '会议转写暂未开放' })).toBeVisible()
  await expect(page.getByRole('link', { name: '会议转写' })).toHaveCount(0)
  await expect.poll(harness.meetingApiRequests).toBe(0)
  await expect.poll(harness.mediaRequests).toBe(0)

  await page.goto('/meetings/example/live')
  await expect(page.getByRole('heading', { name: '会议转写暂未开放' })).toBeVisible()
  await expect.poll(harness.meetingApiRequests).toBe(0)
  await expect.poll(harness.mediaRequests).toBe(0)
})
