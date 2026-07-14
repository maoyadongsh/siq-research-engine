import { expect, test, type Route } from '@playwright/test'

import { mockAuthenticatedWorkspace } from '../support/mockApi'

function deal(dealId: string, companyName: string) {
  return {
    deal_id: dealId,
    company_name: companyName,
    industry: '软件',
    stage: 'Pre-IPO',
    status: 'r1_in_progress',
    current_phase: 'R1',
    updated_at: '2026-07-14T08:00:00Z',
  }
}

function projectPage(page: number, deals: ReturnType<typeof deal>[], total = 55, hasMore = page === 1) {
  return {
    deals,
    stats: { total, active: total, diligence: 0, highRisk: 0 },
    pagination: { page, page_size: 50, total, has_more: hasMore },
    status_summaries: {},
  }
}

async function fulfill(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test.describe('一级市场项目分页', () => {
  test('下一页请求 page=2 并展示 51-55 区间', async ({ page }) => {
    await mockAuthenticatedWorkspace(page)
    const requestedUrls: URL[] = []
    await page.route('**/api/primary-market/projects*', async (route) => {
      const url = new URL(route.request().url())
      requestedUrls.push(url)
      const requestedPage = Number(url.searchParams.get('page') || '1')
      if (requestedPage === 2) {
        await fulfill(
          route,
          projectPage(2, Array.from({ length: 5 }, (_, index) => deal(`DEAL-P2-${index + 1}`, `第二页项目${index + 1}`)), 55, false),
        )
        return
      }
      await fulfill(route, projectPage(1, [deal('DEAL-P1-1', '第一页项目')]))
    })

    await page.goto('/primary-market')
    await expect(page.getByText('第一页项目')).toBeVisible()
    await page.getByRole('button', { name: '下一页' }).click()

    await expect(page.getByText('第二页项目5')).toBeVisible()
    await expect(page.getByText('第 2 / 2 页')).toBeVisible()
    await expect(page.getByText(/当前显示 51-55/)).toBeVisible()
    expect(
      requestedUrls.some(
        (url) =>
          url.searchParams.get('page') === '2' &&
          url.searchParams.get('page_size') === '50' &&
          url.searchParams.get('include_status') === 'true',
      ),
    ).toBe(true)
  })

  test('旧页响应晚到时不能覆盖新查询结果', async ({ page }) => {
    await mockAuthenticatedWorkspace(page)
    let releaseStalePage!: () => void
    const stalePageRelease = new Promise<void>((resolve) => {
      releaseStalePage = resolve
    })
    let markStaleRequestSeen!: () => void
    const staleRequestSeen = new Promise<void>((resolve) => {
      markStaleRequestSeen = resolve
    })
    let markStaleResponseSettled!: () => void
    const staleResponseSettled = new Promise<void>((resolve) => {
      markStaleResponseSettled = resolve
    })

    await page.route('**/api/primary-market/projects*', async (route) => {
      const url = new URL(route.request().url())
      const requestedPage = Number(url.searchParams.get('page') || '1')
      const query = url.searchParams.get('q') || ''
      if (requestedPage === 2 && !query) {
        markStaleRequestSeen()
        await stalePageRelease
        try {
          await fulfill(route, projectPage(2, [deal('DEAL-STALE', '过期第二页')], 55, false))
        } catch {
          // AbortController may cancel the route before the delayed response is fulfilled.
        } finally {
          markStaleResponseSettled()
        }
        return
      }
      if (query === '新查询') {
        await fulfill(route, projectPage(1, [deal('DEAL-FRESH', '新查询项目')], 1, false))
        return
      }
      await fulfill(route, projectPage(1, [deal('DEAL-P1-1', '第一页项目')]))
    })

    await page.goto('/primary-market')
    await expect(page.getByText('第一页项目')).toBeVisible()
    await page.getByRole('button', { name: '下一页' }).click()
    await staleRequestSeen

    await page.getByLabel('搜索一级市场项目').fill('新查询')
    await page.getByLabel('搜索一级市场项目').evaluate((input) => {
      input.closest('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    await expect(page.getByText('新查询项目')).toBeVisible()

    releaseStalePage()
    await staleResponseSettled
    await expect(page.getByText('新查询项目')).toBeVisible()
    await expect(page.getByText('过期第二页')).toHaveCount(0)
  })
})
