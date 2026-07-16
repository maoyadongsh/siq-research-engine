import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const dealId = 'DEAL-CHAT-VISUAL'
const sessionId = 'session-primary-market-markdown'
const fixedNow = '2026-07-16T08:00:00.000Z'
const shortBoldHeading = '核心判断'
const longBoldParagraph = '这是一段完整的粗体正文，用于强调估值假设仍需结合现金流、客户集中度与退出安排综合判断。'
const longUrl = 'https://example.com/primary-market/due-diligence/very-long-path/without-shortcuts?document=prospectus-review&section=customer-concentration&version=2026-07-16'

const assistantMarkdown = [
  '## 投资摘要',
  '',
  '这是普通正文，用于验证一级市场助手在历史消息中的基础字号、行高和阅读节奏。',
  '',
  '### 关键风险',
  '',
  `**${shortBoldHeading}**`,
  '',
  `**${longBoldParagraph}**`,
  '',
  '- 收入增长需要结合在手订单复核',
  '- 核心客户集中度仍需穿透验证',
  '',
  '> 本结论仅用于投委会讨论，最终判断以完成法务与财务核查为前提。',
  '',
  '| 指标 | 2023A | 2024A | 2025E | 2026E | 同业中位数 | 风险阈值 | 核验状态 | 证据编号 |',
  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |',
  '| 营业收入（亿元） | 8.4 | 11.2 | 15.6 | 20.1 | 14.8 | 12.0 | 待复核 | EVID-REVENUE-001 |',
  '| 客户集中度 | 61% | 58% | 54% | 49% | 35% | 50% | 超阈值 | EVID-CUSTOMER-002 |',
  '',
  '```ts',
  "const gate = revenueVerified && legalCleared ? 'proceed' : 'hold'",
  '```',
  '',
  `补充材料链接：${longUrl}`,
].join('\n')

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
] as const

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function mockPrimaryMarketChatApis(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }

    if (path === '/api/auth/me') {
      await route.fulfill(json(e2eUser))
      return
    }
    if (path === '/api/primary-market/projects') {
      await route.fulfill(json({
        deals: [{
          deal_id: dealId,
          company_name: '示例先进制造',
          industry: '先进制造',
          stage: 'Pre-IPO',
          status: 'r1_in_progress',
          current_phase: 'R1',
        }],
      }))
      return
    }
    if (path === `/api/primary-market/projects/${dealId}`) {
      await route.fulfill(json({
        summary: {
          deal_id: dealId,
          company_name: '示例先进制造',
          industry: '先进制造',
          stage: 'Pre-IPO',
          status: 'r1_in_progress',
          current_phase: 'R1',
        },
        project_meta: {},
        manifest: {},
        workflow: { deal_id: dealId },
      }))
      return
    }
    if (path === `/api/primary-market/projects/${dealId}/meeting-transcript`) {
      await route.fulfill(json({ deal_id: dealId, lane: 'agent-siq_ic_master_coordinator', events: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/workflow`) {
      await route.fulfill(json({
        workflow: { deal_id: dealId, company_name: '示例先进制造', status: 'r1_in_progress', current_phase: 'R1' },
        agent_reports: [],
      }))
      return
    }
    if (path === `/api/deals/${dealId}/preflight`) {
      await route.fulfill(json({ preflight: { deal_id: dealId, status: 'pass', checks: [] } }))
      return
    }
    if (path === `/api/deals/${dealId}/disputes`) {
      await route.fulfill(json({ deal_id: dealId, status: 'pass', counts: { disputes: 0, unresolved: 0, positions: 0 }, disputes: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/phase-artifacts`) {
      await route.fulfill(json({ deal_id: dealId, status: 'pass', phases: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/reports/r2-agents`) {
      await route.fulfill(json({ deal_id: dealId, counts: { reports: 0, revisions: 0 }, agents: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/reports/r3-review`) {
      await route.fulfill(json({ deal_id: dealId, status: 'pass', counts: { reports: 0, challenges: 0 }, reports: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/agents`) {
      await route.fulfill(json({ deal_id: dealId, agents: [] }))
      return
    }
    if (path === `/api/deals/${dealId}/decision`) {
      await route.fulfill(json({ deal_id: dealId, contract: null }))
      return
    }
    if (path === `/api/deals/${dealId}/audit`) {
      await route.fulfill(json({ deal_id: dealId, audit: { events: [] }, summary: { status: 'pass' } }))
      return
    }
    if (path === `/api/deals/${dealId}/evidence`) {
      await route.fulfill(json({
        deal_id: dealId,
        quality_report: { status: 'pass', item_count: 0, verified_count: 0, dimensions: [], missing_dimensions: [] },
        items: [],
      }))
      return
    }
    if (path === `/api/primary-market/meeting/${dealId}/agents/readiness`) {
      await route.fulfill(json({ deal_id: dealId, agents: [], summary: {} }))
      return
    }
    if (/\/api\/deals\/DEAL-CHAT-VISUAL\/agents\/[^/]+\/startup-retrieval$/.test(path)) {
      const agentId = decodeURIComponent(path.split('/').at(-2) || '')
      await route.fulfill(json({ deal_id: dealId, agent_id: agentId, receipt: null }))
      return
    }
    if (path.endsWith('/suggestions')) {
      await route.fulfill(json({
        deal_id: dealId,
        lane: 'agent-siq_ic_master_coordinator',
        profile: 'siq_ic_master_coordinator',
        intro: 'Mock suggestions for visual acceptance.',
        questions: [],
        source: 'playwright-mock',
      }))
      return
    }
    if (path.endsWith('/chat/history')) {
      await route.fulfill(json({
        deal_id: dealId,
        lane: 'agent-siq_ic_master_coordinator',
        session_id: sessionId,
        messages: [
          {
            role: 'user',
            content: '请给出投委会摘要并列出关键风险。',
            created_at: fixedNow,
          },
          {
            role: 'assistant',
            content: assistantMarkdown,
            created_at: fixedNow,
          },
        ],
      }))
      return
    }
    if (path.endsWith('/chat/sessions')) {
      await route.fulfill(json({
        deal_id: dealId,
        lane: 'agent-siq_ic_master_coordinator',
        sessions: [{
          session_id: sessionId,
          title: '投委会 Markdown 排版验收',
          preview: '请给出投委会摘要并列出关键风险。',
          message_count: 2,
          last_message_at: fixedNow,
          current: true,
        }],
      }))
      return
    }

    await route.fulfill(json({}))
  })
}

async function captureViewport(page: Page, testInfo: TestInfo, name: string) {
  const screenshotPath = testInfo.outputPath(`primary-market-chat-${name}.png`)
  await page.screenshot({ path: screenshotPath, animations: 'disabled' })
  await testInfo.attach(`primary-market-chat-${name}`, { path: screenshotPath, contentType: 'image/png' })
}

test.describe('一级市场助手历史消息排版与响应式验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.width}x${viewport.height} Markdown 层级、局部横滚与居中布局`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await mockPrimaryMarketChatApis(page)
      const pageErrors: string[] = []
      page.on('pageerror', (error) => pageErrors.push(error.message))

      await page.goto(`/primary-market/meeting?dealId=${dealId}`)

      const shell = page.locator('.primary-market-meeting-chat')
      const messageViewport = shell.locator('.primary-market-meeting-chat-messages')
      const messageList = shell.locator('.primary-market-meeting-chat-list')
      const composer = shell.locator('.primary-market-meeting-composer')
      const assistantColumn = messageList.locator('.chat-message-column-assistant').filter({ hasText: '投资摘要' })
      const assistantBubble = assistantColumn.locator('.chat-message-bubble')

      await expect(page.getByRole('heading', { name: '投研决策' })).toBeVisible()
      await expect(shell).toBeVisible()
      await expect(assistantBubble).toBeVisible()
      await expect.poll(() => page.evaluate(() => Math.round(window.scrollY))).toBeLessThanOrEqual(1)
      await expect(page.getByRole('button', { name: '打开财报助手' })).toHaveCount(0)

      if (viewport.width < 768) {
        const laneLayout = await page.locator('.primary-market-project-context-grid').evaluate((grid) => {
          const badge = grid.querySelector<HTMLElement>('[title^="agent-"]')
          const card = badge?.closest<HTMLElement>('.surface-muted')
          const badgeRect = badge?.getBoundingClientRect()
          const cardRect = card?.getBoundingClientRect()
          return {
            badgeRight: Math.round(badgeRect?.right || 0),
            cardRight: Math.round(cardRect?.right || 0),
          }
        })
        expect(laneLayout.badgeRight).toBeLessThanOrEqual(laneLayout.cardRight + 1)
      }

      const levelTwoHeading = assistantBubble.getByRole('heading', { name: '投资摘要', level: 2 })
      const levelThreeHeading = assistantBubble.getByRole('heading', { name: '关键风险', level: 3 })
      const promotedBoldHeading = assistantBubble.getByRole('heading', { name: shortBoldHeading, level: 3 })
      const bodyParagraph = assistantBubble.locator('p.chat-paragraph').filter({ hasText: '这是普通正文' })
      const longBold = assistantBubble.locator('p.chat-paragraph strong').filter({ hasText: longBoldParagraph })

      await expect(levelTwoHeading).toBeAttached()
      await expect(levelThreeHeading).toBeAttached()
      await expect(promotedBoldHeading).toBeAttached()
      await expect(bodyParagraph).toBeAttached()
      await expect(longBold).toBeAttached()
      await expect(assistantBubble.locator('.chat-list li')).toHaveCount(2)
      await expect(assistantBubble.locator('blockquote.chat-quote')).toContainText('最终判断以完成法务与财务核查为前提')
      await expect(assistantBubble.locator('.chat-code-block code')).toContainText("const gate = revenueVerified")
      await expect(assistantBubble.locator(`a[href="${longUrl}"]`)).toBeAttached()

      const fontSize = (locator: typeof levelTwoHeading) => locator.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
      const [h2Size, h3Size, promotedSize, bodySize, longBoldSize] = await Promise.all([
        fontSize(levelTwoHeading),
        fontSize(levelThreeHeading),
        fontSize(promotedBoldHeading),
        fontSize(bodyParagraph),
        fontSize(longBold),
      ])
      const longBoldSemantics = await longBold.evaluate((element) => ({
        tag: element.tagName,
        paragraphTag: element.closest('p')?.tagName || '',
        paragraphClass: element.closest('p')?.className || '',
        headingAncestor: Boolean(element.closest('h1,h2,h3,h4,h5,h6')),
      }))

      expect(h2Size).toBeGreaterThan(bodySize)
      expect(h3Size).toBeGreaterThan(bodySize)
      expect(promotedSize).toBeGreaterThan(bodySize)
      expect(longBoldSize).toBeCloseTo(bodySize, 1)
      expect(longBoldSemantics.tag).toBe('STRONG')
      expect(longBoldSemantics.paragraphTag).toBe('P')
      expect(longBoldSemantics.paragraphClass).toContain('chat-paragraph')
      expect(longBoldSemantics.headingAncestor).toBe(false)

      const tableRegion = assistantBubble.getByRole('region', { name: '消息表格' })
      await expect(tableRegion).toBeAttached()
      const tableOverflow = await tableRegion.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        overflowX: getComputedStyle(element).overflowX,
      }))
      expect(tableOverflow.overflowX).toBe('auto')
      expect(tableOverflow.scrollWidth).toBeGreaterThan(tableOverflow.clientWidth + 1)
      await tableRegion.evaluate((element) => { element.scrollLeft = 120 })
      await expect.poll(() => tableRegion.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
      await tableRegion.evaluate((element) => { element.scrollLeft = 0 })

      const layout = await page.evaluate(() => {
        const rect = (selector: string) => {
          const node = document.querySelector<HTMLElement>(selector)
          if (!node) throw new Error(`Missing layout fixture: ${selector}`)
          const bounds = node.getBoundingClientRect()
          return {
            top: bounds.top,
            right: bounds.right,
            bottom: bounds.bottom,
            left: bounds.left,
            width: bounds.width,
            center: bounds.left + bounds.width / 2,
          }
        }
        const rendered = document.querySelector<HTMLElement>('.primary-market-meeting-chat-list .chat-message-column-assistant .chat-rendered')
        if (!rendered) throw new Error('Missing assistant rendered content')
        return {
          viewportWidth: window.innerWidth,
          documentScrollWidth: document.documentElement.scrollWidth,
          bodyScrollWidth: document.body.scrollWidth,
          shell: rect('.primary-market-meeting-chat'),
          header: rect('.primary-market-meeting-chat-header'),
          headerActions: rect('.primary-market-meeting-chat-header > div:last-child'),
          messages: rect('.primary-market-meeting-chat-messages'),
          messageList: rect('.primary-market-meeting-chat-list'),
          composer: rect('.primary-market-meeting-composer'),
          composerSection: rect('.primary-market-meeting-composer-section'),
          assistantColumn: rect('.primary-market-meeting-chat-list .chat-message-column-assistant'),
          assistantBubble: rect('.primary-market-meeting-chat-list .chat-message-column-assistant .chat-message-bubble'),
          renderedClientWidth: rendered.clientWidth,
          renderedScrollWidth: rendered.scrollWidth,
        }
      })

      expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
      expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
      expect(layout.shell.left).toBeGreaterThanOrEqual(-1)
      expect(layout.shell.right).toBeLessThanOrEqual(layout.viewportWidth + 1)
      expect(layout.headerActions.left).toBeGreaterThanOrEqual(layout.header.left - 1)
      expect(layout.headerActions.right).toBeLessThanOrEqual(layout.header.right + 1)
      expect(Math.abs(layout.messageList.center - layout.composer.center)).toBeLessThanOrEqual(1)
      expect(Math.abs(layout.messageList.center - layout.shell.center)).toBeLessThanOrEqual(1)
      expect(layout.messageList.width).toBeLessThanOrEqual(1180.5)
      expect(layout.composer.width).toBeLessThanOrEqual(1180.5)
      expect(layout.assistantColumn.left).toBeGreaterThanOrEqual(layout.messages.left - 1)
      expect(layout.assistantColumn.right).toBeLessThanOrEqual(layout.messages.right + 1)
      expect(layout.assistantBubble.left).toBeGreaterThanOrEqual(layout.messages.left - 1)
      expect(layout.assistantBubble.right).toBeLessThanOrEqual(layout.messages.right + 1)
      expect(layout.renderedScrollWidth).toBeLessThanOrEqual(layout.renderedClientWidth + 1)
      expect(await messageList.getAttribute('class')).toContain('mx-auto')
      expect(await composer.getAttribute('class')).toContain('mx-auto')
      expect(layout.messages.top).toBeGreaterThanOrEqual(layout.header.bottom - 1)
      expect(layout.composerSection.top).toBeGreaterThanOrEqual(layout.messages.bottom - 1)
      expect(layout.composerSection.bottom).toBeLessThanOrEqual(layout.shell.bottom + 1)

      const longLinkFitsBubble = await assistantBubble.locator(`a[href="${longUrl}"]`).evaluate((element) => {
        const linkRect = element.getBoundingClientRect()
        const bubbleRect = element.closest('.chat-message-bubble')?.getBoundingClientRect()
        return Boolean(bubbleRect && linkRect.left >= bubbleRect.left - 1 && linkRect.right <= bubbleRect.right + 1)
      })
      expect(longLinkFitsBubble).toBe(true)

      await shell.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'auto' }))
      await messageViewport.evaluate((element) => { element.scrollTop = 0 })
      await expect.poll(() => messageViewport.evaluate((element) => element.scrollTop)).toBe(0)
      await captureViewport(page, testInfo, `${viewport.name}-${viewport.width}x${viewport.height}`)
      expect(pageErrors).toEqual([])
    })
  }
})
