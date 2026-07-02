import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const chatViewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

const fixedNow = '2026-06-27T08:00:00.000Z'

type MockChatSession = {
  session_id: string
  title: string
  preview: string
  message_count: number
  last_message_at: string
  current?: boolean
}

const defaultChatSessions: MockChatSession[] = [
  {
    session_id: 'session-1',
    title: '贵州茅台 财报讨论',
    preview: '请分析收入质量',
    message_count: 2,
    last_message_at: fixedNow,
    current: true,
  },
]

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockChatApis(page: Page, options: { sessions?: MockChatSession[] } = {}) {
  let releaseChatStream: (() => void) | null = null
  const sessions = options.sessions ?? defaultChatSessions

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

    if (url.pathname === '/api/workspace/summary') {
      await route.fulfill(json({
        quotas: {
          agentQuestion: { used: 3, limit: 20, remaining: 17, resetAt: fixedNow },
          parseJob: { used: 1, limit: 10, remaining: 9, resetAt: fixedNow },
        },
        stats: { projects: 1, artifacts: 2, downloads: 1, parses: 1, reports: 0 },
        recentArtifacts: [],
        artifacts: [],
        projects: [],
      }))
      return
    }

    if (url.pathname === '/api/workspace/me') {
      await route.fulfill(json({
        user: e2eUser,
        quotas: {
          agentQuestion: { used: 3, limit: 20, remaining: 17, resetAt: fixedNow },
          parseJob: { used: 1, limit: 10, remaining: 9, resetAt: fixedNow },
        },
        stats: { projects: 1, artifacts: 2, downloads: 1, parses: 1, reports: 0 },
      }))
      return
    }

    if (url.pathname === '/api/workspace/artifacts') {
      await route.fulfill(json({ artifacts: [] }))
      return
    }

    if (url.pathname === '/api/chat/sessions') {
      await route.fulfill(json({ sessions }))
      return
    }

    if (url.pathname === '/api/chat/history') {
      await route.fulfill(json({
        session_id: url.searchParams.get('session_id') || 'session-1',
        messages: [
          {
            role: 'assistant',
            content: '欢迎回来，可以继续分析已入库财报。',
            created_at: fixedNow,
          },
        ],
      }))
      return
    }

    if (url.pathname === '/api/chat/active') {
      await route.fulfill(json({ running: false, session_id: 'session-1' }))
      return
    }

    if (url.pathname === '/api/chat/session' && route.request().method() === 'POST') {
      await route.fulfill(json({ session_id: 'session-new' }))
      return
    }

    if (url.pathname === '/api/chat/stream' && route.request().method() === 'POST') {
      await new Promise<void>((resolve) => {
        releaseChatStream = resolve
      })
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'event: run',
          'data: {"run_id":"run-1","session_id":"session-1"}',
          '',
          'data: {"content":"模拟回复"}',
          '',
          'data: [DONE]',
          '',
        ].join('\n'),
      }).catch(() => {})
      return
    }

    if (url.pathname === '/api/chat/stop' && route.request().method() === 'POST') {
      releaseChatStream?.()
      releaseChatStream = null
      await route.fulfill(json({ stopped: true }))
      return
    }

    if (url.pathname.startsWith('/api/chat/')) {
      await route.fulfill(json({ sessions: [], messages: [] }))
      return
    }

    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })
}

async function expectNoHorizontalOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))

  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
}

test.describe('聊天系统响应式验收', () => {
  for (const viewport of chatViewports) {
    test(`/chat ${viewport.width}x${viewport.height} 共享组件可见且无横向溢出`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockChatApis(page)

      await page.goto('/chat')
      await page.waitForLoadState('networkidle')
      await expect(page.getByRole('heading', { name: '财报问答助手' })).toBeVisible()
      await expect(page.locator('.chat-page-shell')).toBeVisible()
      await expect(page.locator('.chat-page-messages')).toBeVisible()
      await expect(page.locator('.chat-page-composer-section')).toBeVisible()
      await expect(page.locator('textarea[placeholder*="输入你的问题"]')).toBeVisible()
      await expect(page.getByRole('button', { name: '查看历史' })).toBeVisible()
      await expect(page.getByRole('button', { name: '删除历史' })).toBeVisible()
      await expectNoHorizontalOverflow(page)
    })
  }

  test('/chat 移动端可打开并选择历史会话', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockChatApis(page)

    await page.goto('/chat')
    await page.waitForLoadState('networkidle')

    await page.getByRole('button', { name: '查看历史' }).click()
    const historyDialog = page.getByRole('dialog', { name: '历史会话' })
    await expect(historyDialog).toBeVisible()

    await historyDialog.getByRole('button', { name: /贵州茅台 财报讨论/ }).click()
    await expect(page.getByText('已打开历史会话')).toBeVisible()
    await expect(historyDialog).toHaveClass(/translate-x-full/)
    await expectNoHorizontalOverflow(page)
  })

  test('/chat 历史会话过滤无效条目并显示空态', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockChatApis(page, {
      sessions: [
        {
          session_id: 'no-messages',
          title: '无消息会话',
          preview: '不应展示',
          message_count: 0,
          last_message_at: fixedNow,
        },
        {
          session_id: 'blank-copy',
          title: ' ',
          preview: ' ',
          message_count: 3,
          last_message_at: fixedNow,
        },
      ],
    })

    await page.goto('/chat')
    await page.waitForLoadState('networkidle')

    await page.getByRole('button', { name: '查看历史' }).click()
    const historyDialog = page.getByRole('dialog', { name: '历史会话' })
    await expect(historyDialog).toBeVisible()
    await expect(historyDialog.getByText('暂无历史会话')).toBeVisible()
    await expect(historyDialog.getByText('无消息会话')).toBeHidden()
    await expect(historyDialog.getByText('不应展示')).toBeHidden()
    await expectNoHorizontalOverflow(page)
  })

  test('/chat 发送后可停止生成并回到可继续输入状态', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await mockChatApis(page)

    await page.goto('/chat')
    await page.waitForLoadState('networkidle')

    const question = '请分析贵州茅台收入质量'
    const input = page.locator('textarea[placeholder*="输入你的问题"]')
    const sendButton = page.getByRole('button', { name: '发送消息' })

    await input.fill(question)
    await expect(sendButton).toBeEnabled()
    await sendButton.click()

    await expect(page.getByText(question)).toBeVisible()
    const stopButton = page.getByRole('button', { name: '停止生成' })
    await expect(stopButton).toBeVisible()

    await stopButton.click()

    await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible()
    await expect(page.getByRole('button', { name: '发送消息' })).toBeDisabled()
    await expect(input).toBeEnabled()
  })

  test('全局财报助手窗口在移动端不溢出且 composer 可见', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockChatApis(page)

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '工作平台' })).toBeVisible()
    await expectNoHorizontalOverflow(page)

    await page.getByLabel('打开财报助手').click()
    const chatWindow = page.locator('.global-chat-window')
    const composer = chatWindow.locator('.chat-composer-section')
    const expectChatWindowWithinViewport = async () => {
      const bounds = await chatWindow.evaluate((element) => {
        const rect = element.getBoundingClientRect()
        return {
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          viewportWidth: window.innerWidth,
        }
      })
      expect(bounds.left).toBeGreaterThanOrEqual(0)
      expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth)
      expect(bounds.width).toBeLessThanOrEqual(bounds.viewportWidth)
    }

    await expect(chatWindow).toBeVisible()
    await expect(composer).toBeVisible()
    await expectChatWindowWithinViewport()

    await chatWindow.getByLabel('最小化').click()
    await expect(composer).toBeHidden()
    await expect(chatWindow.getByLabel('展开')).toBeVisible()
    await expectChatWindowWithinViewport()

    await chatWindow.getByLabel('展开').click()
    await expect(composer).toBeVisible()
    await expect(chatWindow.getByLabel('最小化')).toBeVisible()
    await expectChatWindowWithinViewport()

    await chatWindow.getByLabel('查看历史').click()
    await expect(page.getByRole('dialog', { name: '历史会话' })).toBeVisible()
  })
})
