import { expect, test, type Page, type Route } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import { e2eUser } from '../support/mockApi'

const marketOrder = ['CN', 'HK', 'US', 'EU', 'KR', 'JP'] as const
const multiMarketResearchEnabled = ['1', 'true', 'yes', 'on'].includes(
  String(
    process.env.SIQ_MULTI_MARKET_RESEARCH_ENABLED
      ?? process.env.VITE_SIQ_MULTI_MARKET_RESEARCH_ENABLED
      ?? '0',
  ).trim().toLowerCase(),
)

test.skip(!multiMarketResearchEnabled, 'requires the deployment-controlled multi-market research flag')
const marketLabels = ['中国内地市场', '香港市场', '美国市场', '欧洲市场', '韩国市场', '日本市场']
const saveAcceptanceScreenshots = process.env.SIQ_SAVE_ACCEPTANCE_SCREENSHOTS === '1'
const acceptanceEvidenceDir = resolvePath(process.cwd(), '../../artifacts/secondary-market-multi-market')

async function saveAcceptanceScreenshot(page: Page, filename: string) {
  if (!saveAcceptanceScreenshots) return
  mkdirSync(acceptanceEvidenceDir, { recursive: true })
  await page.screenshot({ path: resolvePath(acceptanceEvidenceDir, filename), fullPage: true })
}

function reportControl(page: Page, label: string) {
  return page.getByRole('combobox', { name: label, exact: true })
}

async function openAssistant(page: Page, title: string) {
  await page.getByRole('button', { name: new RegExp(`^(?:打开|展开)${title}$`) }).click()
}

async function expectControlsNotToOverlap(page: Page, labels: string[]) {
  const boxes = await Promise.all(labels.map(async (label) => {
    const box = await reportControl(page, label).boundingBox()
    expect(box, `${label} control should have a layout box`).not.toBeNull()
    return box!
  }))

  for (let leftIndex = 0; leftIndex < boxes.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < boxes.length; rightIndex += 1) {
      const left = boxes[leftIndex]
      const right = boxes[rightIndex]
      const overlapsHorizontally = left.x < right.x + right.width && right.x < left.x + left.width
      const overlapsVertically = left.y < right.y + right.height && right.y < left.y + left.height
      expect(
        overlapsHorizontally && overlapsVertically,
        `${labels[leftIndex]} and ${labels[rightIndex]} controls should not overlap`,
      ).toBe(false)
    }
  }
}

const identities = {
  US: {
    market: 'US',
    company_id: 'US:0000320193',
    filing_id: 'US:0000320193:0000320193-25-000079',
    parse_run_id: 'parse-us-aapl-2025',
  },
  CN: {
    market: 'CN',
    company_id: 'CN:000333',
    filing_id: 'CN:000333:2025-annual',
    parse_run_id: 'parse-cn-midea-2025',
  },
  CN_SAIC: {
    market: 'CN',
    company_id: '600104-上汽集团',
    filing_id: 'CN:600104-上汽集团:2025-annual',
    parse_run_id: 'parse-cn-saic-2025',
  },
  HK: {
    market: 'HK',
    company_id: 'HK:00700',
    filing_id: 'HK:00700:2025-annual',
    parse_run_id: 'parse-hk-tencent-2025',
  },
} as const

const saicCompany = {
  company_key: 'cn-saic',
  market: 'CN',
  company_id: identities.CN_SAIC.company_id,
  company_wiki_id: '600104-上汽集团',
  display_code: '600104',
  display_name: '上汽集团',
  parsed_report_count: 1,
  readiness: { parsed_ready: true },
  capabilities: { analysis_input_ready: true },
  degraded_reasons: [],
}

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

function company(market: 'US' | 'CN' | 'HK') {
  const values = {
    US: { key: 'us-aapl', wiki: 'AAPL-Apple-Inc', code: 'AAPL', name: 'Apple Inc' },
    CN: { key: 'cn-midea', wiki: '000333-美的集团', code: '000333', name: '美的集团' },
    HK: { key: 'hk-tencent', wiki: '00700-Tencent-Holdings', code: '00700', name: '腾讯控股' },
  }[market]
  return {
    company_key: values.key,
    market,
    company_id: identities[market].company_id,
    company_wiki_id: values.wiki,
    display_code: values.code,
    display_name: values.name,
    parsed_report_count: 1,
    readiness: { parsed_ready: true },
    capabilities: { analysis_input_ready: true },
    degraded_reasons: [],
  }
}

function report(market: 'US' | 'CN' | 'HK', baseline: string | null = null, companyKey = '') {
  const values = {
    US: { id: '2025-10-K-0000320193-25-000079', label: '2025 10-K · 截止 2025-09-27 · warning', form: '10-K', quality: 'warning', family: 'sec_ixbrl' },
    CN: { id: '2025-annual', label: '2025 年报 · 截止 2025-12-31', form: null, quality: 'pass', family: 'pdf_market' },
    HK: { id: '2025-annual', label: '2025 年报 · 截止 2025-12-31', form: null, quality: 'pass', family: 'pdf_market' },
  }[market]
  return {
    report_id: values.id,
    label: values.label,
    report_type: 'annual',
    form_type: values.form,
    fiscal_year: 2025,
    period_end: market === 'US' ? '2025-09-27' : '2025-12-31',
    published_at: '2026-01-01',
    quality_status: values.quality,
    source_family: values.family,
    document_format: market === 'US' ? 'ixbrl_html' : 'pdf',
    research_identity: market === 'CN' && companyKey === saicCompany.company_key
      ? identities.CN_SAIC
      : identities[market],
    readiness: { parsed_ready: true },
    capabilities: { analysis_input_ready: true, factcheck_ready: Boolean(baseline) },
    degraded_reasons: market === 'US' ? ['financial_checks_warning'] : [],
    baseline_analysis_artifact_id: baseline,
    analysis_artifact_id: baseline,
  }
}

async function mockMultiMarketAgentApis(
  page: Page,
  options: { pagedAnalysisArtifacts?: boolean } = {},
) {
  const researchRequests: string[] = []

  await page.addInitScript(({ user }) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, { user: e2eUser })

  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    const pathname = url.pathname
    if (!pathname.startsWith('/api/')) {
      await route.continue()
      return
    }

    if (pathname === '/api/auth/me') {
      await route.fulfill(json(e2eUser))
      return
    }

    if (pathname.startsWith('/api/research-universe/')) researchRequests.push(`${route.request().method()} ${pathname}${url.search}`)

    if (pathname === '/api/research-universe/markets') {
      await route.fulfill(json({
        markets: marketOrder.toReversed().map((market, index) => ({
          market,
          label: `untrusted-${market}`,
          order: index,
          enabled: true,
          company_count: ['CN', 'HK', 'US'].includes(market) ? 1 : 0,
          capabilities: {},
          degraded_reasons: [],
        })),
      }))
      return
    }

    if (pathname === '/api/research-universe/companies') {
      const market = url.searchParams.get('market')
      if (market === 'HK') await new Promise((resolve) => setTimeout(resolve, 250))
      const companies = market === 'US'
        ? [company('US')]
        : market === 'CN'
          ? [company('CN'), saicCompany]
          : market === 'HK'
            ? [company('HK')]
            : []
      await route.fulfill(json({ market, companies }))
      return
    }

    const reportMatch = pathname.match(/^\/api\/research-universe\/companies\/([^/]+)\/reports$/)
    if (reportMatch) {
      const companyKey = decodeURIComponent(reportMatch[1])
      const market = url.searchParams.get('market') as 'US' | 'CN' | 'HK'
      const baseline = url.searchParams.get('agent_type') === 'factcheck' ? 'analysis-aapl-exact' : null
      await route.fulfill(json({ market, company_key: companyKey, reports: [report(market, baseline, companyKey)] }))
      return
    }

    const artifactListMatch = pathname.match(/^\/api\/research-universe\/companies\/([^/]+)\/artifacts$/)
    if (artifactListMatch) {
      const artifactType = url.searchParams.get('artifact_type')
      const artifactMarket = url.searchParams.get('market')
      if (options.pagedAnalysisArtifacts && artifactType === 'analysis' && artifactMarket === 'US') {
        const cursor = url.searchParams.get('cursor')
        const artifactId = cursor === 'exact:1' ? 'analysis-aapl-older' : 'analysis-aapl-latest'
        const item = {
          artifact_id: artifactId,
          artifact_type: 'analysis',
          status: 'completed',
          created_at: cursor === 'exact:1' ? '2026-07-15T08:00:00Z' : '2026-07-16T08:00:00Z',
          source_report_id: '2025-10-K-0000320193-25-000079',
          source_family: 'sec_ixbrl',
          quality: { status: 'pass', warnings: [] },
          identity_status: 'exact',
          usable_as_baseline: true,
          filename: `${artifactId}.html`,
          research_identity: identities.US,
        }
        const nextCursor = cursor === 'exact:1' ? null : 'exact:1'
        await route.fulfill(json({
          market: artifactMarket,
          company_key: decodeURIComponent(artifactListMatch[1]),
          report_id: url.searchParams.get('report_id'),
          artifact_type: artifactType,
          artifacts: [item],
          legacy_artifacts: [],
          items: [item],
          pagination: {
            limit: Number(url.searchParams.get('limit')),
            next_cursor: nextCursor,
            has_more: Boolean(nextCursor),
            targeted: false,
          },
        }))
        return
      }
      const artifacts = artifactType === 'factcheck'
        ? [{
            artifact_id: 'factcheck-aapl-current',
            artifact_type: 'factcheck',
            status: 'completed',
            created_at: '2026-07-16T08:00:00Z',
            source_report_id: '2025-10-K-0000320193-25-000079',
            source_family: 'sec_ixbrl',
            quality: { status: 'pass', warnings: [] },
            identity_status: 'exact',
            usable_as_baseline: false,
            content_url: '/api/research-universe/artifacts/factcheck-aapl-current/content',
            filename: 'factcheck-aapl.html',
          }]
        : []
      const companyKey = decodeURIComponent(artifactListMatch[1])
      const saicLegacyArtifacts = artifactMarket === 'CN' && companyKey === saicCompany.company_key
        ? [{
            artifact_id: `legacy-saic-${artifactType}`,
            artifact_type: artifactType,
            status: 'legacy_unbound',
            identity_status: 'legacy_unbound',
            filename: `saic-${artifactType}.html`,
            usable_as_baseline: false,
            content_url: `/api/research-universe/artifacts/legacy-saic-${artifactType}/content`,
          }]
        : []
      const legacyArtifacts = artifactType === 'analysis' && artifactMarket === 'CN'
        ? [{
            artifact_id: 'legacy-cn-analysis',
            artifact_type: 'analysis',
            status: 'legacy_unbound',
            identity_status: 'legacy_unbound',
            filename: 'old-cn-analysis.html',
            usable_as_baseline: false,
            content_url: '/api/research-universe/artifacts/legacy-cn-analysis/content',
          }]
        : saicLegacyArtifacts
      await route.fulfill(json({
        market: artifactMarket,
        company_key: decodeURIComponent(artifactListMatch[1]),
        report_id: url.searchParams.get('report_id'),
        artifact_type: artifactType,
        artifacts,
        legacy_artifacts: legacyArtifacts,
      }))
      return
    }

    if (pathname === '/api/research-universe/artifacts/factcheck-aapl-current/content') {
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><h1>Apple 事实核查</h1></body></html>' })
      return
    }

    if (pathname === '/api/research-universe/artifacts/legacy-cn-analysis/content') {
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><h1>旧版 CN 分析报告</h1></body></html>' })
      return
    }

    if (/^\/api\/research-universe\/artifacts\/legacy-saic-(?:analysis|factcheck|tracking)\/content$/.test(pathname)) {
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><h1>上汽集团历史报告</h1></body></html>' })
      return
    }

    if (pathname === '/api/research-universe/artifacts/analysis-aapl-latest/content') {
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><h1>Apple 最新分析</h1></body></html>' })
      return
    }

    if (pathname === '/api/research-universe/artifacts/analysis-aapl-older/content') {
      await route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body><h1>Apple 历史分析</h1></body></html>' })
      return
    }

    if (pathname === '/api/research-universe/artifacts/factcheck-aapl-current' && route.request().method() === 'DELETE') {
      await route.fulfill(json({ deleted: true, artifact_id: 'factcheck-aapl-current' }))
      return
    }

    if (/^\/api\/(?:analysis|factchecker|tracking)\/chat\/stream$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"content":"已接收当前源报告身份"}\n\n',
      })
      return
    }

    if (/^\/api\/(?:analysis|factchecker|tracking)\/chat\/sessions$/.test(pathname)) {
      await route.fulfill(json({ sessions: [] }))
      return
    }
    if (/^\/api\/(?:analysis|factchecker|tracking)\/chat\/history$/.test(pathname)) {
      await route.fulfill(json({ session_id: 'e2e-session', messages: [] }))
      return
    }
    if (/^\/api\/(?:analysis|factchecker|tracking)\/chat\/active$/.test(pathname)) {
      await route.fulfill(json({ running: false }))
      return
    }

    if (pathname === '/api/wiki/companies/list') {
      await route.fulfill(json({
        companies: [{
          code: '000333', name: '美的集团', dir: '000333-美的集团', hasReport: true, reportCount: 1,
          hasFactcheck: false, factcheckCount: 0, hasTracking: false, trackingCount: 0, hasLegal: false, legalCount: 0,
        }, {
          code: '600104', name: '上汽集团', dir: '600104-上汽集团', hasReport: true, reportCount: 2,
          hasFactcheck: true, factcheckCount: 1, hasTracking: true, trackingCount: 1, hasLegal: true, legalCount: 1,
        }],
      }))
      return
    }
    if (pathname === '/api/wiki/companies/000333-%E7%BE%8E%E7%9A%84%E9%9B%86%E5%9B%A2/legals') {
      await route.fulfill(json({ legals: [] }))
      return
    }

    await route.fulfill(json({ sessions: [], messages: [], items: [], data: [] }))
  })

  return { researchRequests }
}

test('美股无生成结果仍保留源报告身份，市场顺序和移动端布局稳定', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 844 })
  await mockMultiMarketAgentApis(page)
  await page.goto('/analysis?market=US&company_key=us-aapl&report_id=2025-10-K-0000320193-25-000079')

  const marketSelect = reportControl(page, '市场')
  await expect(marketSelect).toHaveValue('US')
  await expect(reportControl(page, '公司')).toHaveValue('us-aapl')
  await expect(reportControl(page, '源报告')).toHaveValue('2025-10-K-0000320193-25-000079')
  await expect(reportControl(page, '分析结果')).toBeDisabled()
  await expect(page.getByText('该源报告存在质量警告，可继续使用')).toBeVisible()
  await expect(page.getByText(/当前来源能力降级/)).toBeVisible()
  await expect(page.getByRole('button', { name: '分享' })).toBeVisible()

  const labels = await marketSelect.locator('option').allTextContents()
  expect(labels).toEqual(marketLabels)
  await expectControlsNotToOverlap(page, ['市场', '公司', '源报告', '分析结果'])
  await expect.poll(() => new URL(page.url()).searchParams.get('company_key')).toBe('us-aapl')
  await saveAcceptanceScreenshot(page, 'ui-analysis-mobile-375.png')

  const chatRequest = page.waitForRequest((request) => request.url().endsWith('/api/analysis/chat/stream') && request.method() === 'POST')
  await openAssistant(page, '分析助手')
  await page.getByPlaceholder('输入问题…').fill('请分析这份报告')
  await page.getByRole('button', { name: '发送消息' }).click()
  const payload = (await chatRequest).postDataJSON()

  expect(payload.context.market).toBe('US')
  expect(payload.context.research_identity).toEqual(identities.US)
  expect(payload.context.source_report.report_id).toBe('2025-10-K-0000320193-25-000079')
  expect(payload.context.source_report.filing_id).toBe(identities.US.filing_id)
  expect(payload.context.source_report.parse_run_id).toBe(identities.US.parse_run_id)
  expect(payload.context.source_report.source_family).toBe('sec_ixbrl')
  expect(payload.context.artifact).toBeUndefined()
  expect(payload.context.capabilities.analysis_baseline_available).toBe(false)

  const layout = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }))
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1)
  await page.getByRole('button', { name: '收起助手' }).click()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(reportControl(page, '市场')).toBeVisible()
  await expect(reportControl(page, '公司')).toBeVisible()
  await expect(reportControl(page, '源报告')).toBeVisible()
  await expectControlsNotToOverlap(page, ['市场', '公司', '源报告', '分析结果'])
  const desktopLayout = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }))
  expect(desktopLayout.scrollWidth).toBeLessThanOrEqual(desktopLayout.width + 1)
  await saveAcceptanceScreenshot(page, 'ui-analysis-desktop-1440.png')
})

test('智能分析、事实核查、持续跟踪都固定以六市场选择作为公司选择前置入口', async ({ page }) => {
  await mockMultiMarketAgentApis(page)

  for (const path of ['/analysis', '/verify', '/tracking']) {
    await page.goto(`${path}?market=US&company_key=us-aapl&report_id=2025-10-K-0000320193-25-000079`)
    const toolbarSelects = page.locator('.page-toolbar select')
    await expect(toolbarSelects.nth(0)).toHaveAttribute('aria-label', '市场')
    await expect(toolbarSelects.nth(1)).toHaveAttribute('aria-label', '公司')
    await expect(reportControl(page, '市场').locator('option')).toHaveText(marketLabels)
    await expect(reportControl(page, '公司')).toHaveValue('us-aapl')
  }
})

test('二级市场各分析入口无 URL 选择时都以上汽集团案例作为首屏', async ({ page }) => {
  await mockMultiMarketAgentApis(page)

  for (const path of ['/analysis', '/verify', '/tracking']) {
    await page.goto(path)
    await expect(reportControl(page, '市场')).toHaveValue('CN')
    await expect(reportControl(page, '公司')).toHaveValue('cn-saic')
    await expect(reportControl(page, '源报告')).toHaveValue('2025-annual')
    await expect(page.locator('.secondary-company-name')).toHaveText('上汽集团')
    await expect.poll(() => new URL(page.url()).searchParams.get('company_key')).toBe('cn-saic')
  }
})

test('分析页首屏只加载首个结果，展开后分页取元数据并按选择加载 HTML', async ({ page }) => {
  const { researchRequests } = await mockMultiMarketAgentApis(page, { pagedAnalysisArtifacts: true })
  await page.goto('/analysis?market=US&company_key=us-aapl&report_id=2025-10-K-0000320193-25-000079')

  const resultSelect = reportControl(page, '分析结果')
  await expect(resultSelect).toHaveValue('analysis-aapl-latest')
  await expect(resultSelect.locator('option')).toHaveCount(1)
  await expect(page.frameLocator('iframe[title="智能分析"]').getByRole('heading', { name: 'Apple 最新分析' })).toBeVisible()
  expect(researchRequests.filter((request) => request.includes('/artifacts/analysis-aapl-latest/content'))).toHaveLength(1)
  expect(researchRequests.some((request) => request.includes('/artifacts/analysis-aapl-older/content'))).toBe(false)
  expect(researchRequests.some((request) => request.includes('/artifacts?') && request.includes('limit=1'))).toBe(true)

  await resultSelect.focus()
  await expect(resultSelect.locator('option')).toHaveCount(2)
  expect(researchRequests.some((request) => request.includes('/artifacts?') && request.includes('cursor=exact%3A1'))).toBe(true)
  expect(researchRequests.some((request) => request.includes('/artifacts/analysis-aapl-older/content'))).toBe(false)

  await resultSelect.selectOption('analysis-aapl-older')
  await expect(page.frameLocator('iframe[title="智能分析"]').getByRole('heading', { name: 'Apple 历史分析' })).toBeVisible()
  expect(researchRequests.filter((request) => request.includes('/artifacts/analysis-aapl-older/content'))).toHaveLength(1)

  await resultSelect.selectOption('analysis-aapl-latest')
  await expect(page.frameLocator('iframe[title="智能分析"]').getByRole('heading', { name: 'Apple 最新分析' })).toBeVisible()
  expect(researchRequests.filter((request) => request.includes('/artifacts/analysis-aapl-latest/content'))).toHaveLength(1)
})

test('快速切换市场会取消旧请求，不会回显旧公司或 ResearchIdentity', async ({ page }) => {
  await mockMultiMarketAgentApis(page)
  await page.goto('/analysis?market=US')
  const marketSelect = reportControl(page, '市场')
  await expect(reportControl(page, '公司')).toHaveValue('us-aapl')

  await marketSelect.selectOption('HK')
  await marketSelect.selectOption('CN')
  await expect(reportControl(page, '公司')).toHaveValue('cn-saic')
  await expect(reportControl(page, '公司').locator('option')).toHaveText(['000333 美的集团', '600104 上汽集团'])
  await page.waitForTimeout(350)
  await expect(reportControl(page, '公司')).toHaveValue('cn-saic')
  await expect(page.getByText('腾讯控股')).toHaveCount(0)

  const chatRequest = page.waitForRequest((request) => request.url().endsWith('/api/analysis/chat/stream') && request.method() === 'POST')
  await openAssistant(page, '分析助手')
  await page.getByPlaceholder('输入问题…').fill('当前公司是谁')
  await page.getByRole('button', { name: '发送消息' }).click()
  const payload = (await chatRequest).postDataJSON()
  expect(payload.context.research_identity).toEqual(identities.CN_SAIC)
  expect(payload.context.company.company_key).toBe('cn-saic')

  const layout = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }))
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1)
})

test('事实核查绑定 exact 分析基线，删除只提交 artifact_id', async ({ page }) => {
  await mockMultiMarketAgentApis(page)
  await page.goto('/verify?market=US&company_key=us-aapl&report_id=2025-10-K-0000320193-25-000079&artifact_id=factcheck-aapl-current')

  await expect(reportControl(page, '核查结果')).toHaveValue('factcheck-aapl-current')
  await expect(page.frameLocator('iframe[title="事实核查报告"]').getByRole('heading', { name: 'Apple 事实核查' })).toBeVisible()

  const chatRequest = page.waitForRequest((request) => request.url().endsWith('/api/factchecker/chat/stream') && request.method() === 'POST')
  await openAssistant(page, '核查助手')
  await page.getByPlaceholder('输入问题…').fill('核查当前结论')
  await page.getByRole('button', { name: '发送消息' }).click()
  const payload = (await chatRequest).postDataJSON()
  expect(payload.context.market).toBe('US')
  expect(payload.context.company.company_key).toBe('us-aapl')
  expect(payload.context.source_report.report_id).toBe('2025-10-K-0000320193-25-000079')
  expect(payload.context.source_report.filing_id).toBe(identities.US.filing_id)
  expect(payload.context.source_report.parse_run_id).toBe(identities.US.parse_run_id)
  expect(payload.context.source_report.source_family).toBe('sec_ixbrl')
  expect(payload.context.upstream_analysis_artifact_id).toBe('analysis-aapl-exact')
  expect(payload.context.artifact.artifact_id).toBe('factcheck-aapl-current')
  expect(payload.context.upstream_analysis_artifact_id).not.toBe(payload.context.artifact.artifact_id)

  await page.getByRole('button', { name: '收起助手' }).click()
  const deleteRequest = page.waitForRequest((request) => request.method() === 'DELETE')
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '确认删除' }).click()
  const deleted = await deleteRequest
  expect(new URL(deleted.url()).pathname).toBe('/api/research-universe/artifacts/factcheck-aapl-current')
})

test('法务页面忽略境外 URL 参数并保持 CN 旧链路', async ({ page }) => {
  const { researchRequests } = await mockMultiMarketAgentApis(page)
  await page.goto('/legal?market=US&company_key=us-aapl&report_id=foreign-report')

  await expect(page.getByRole('heading', { name: '法务合规' })).toBeVisible()
  await expect(reportControl(page, '公司')).toHaveValue('600104-上汽集团')
  await expect(reportControl(page, '市场')).toHaveCount(0)
  expect(researchRequests).toEqual([])
})

test('旧 CN company/result 分享链接只在 CN 内恢复并转换为新选择状态', async ({ page }) => {
  await mockMultiMarketAgentApis(page)
  await page.goto('/analysis?company=000333-%E7%BE%8E%E7%9A%84%E9%9B%86%E5%9B%A2&result=old-cn-analysis.html')

  await expect(reportControl(page, '市场')).toHaveValue('CN')
  await expect(reportControl(page, '公司')).toHaveValue('cn-midea')
  await expect(reportControl(page, '源报告')).toHaveValue('2025-annual')
  await expect(reportControl(page, '分析结果')).toHaveValue('legacy-cn-analysis')
  await expect(page.frameLocator('iframe[title="智能分析"]').getByRole('heading', { name: '旧版 CN 分析报告' })).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('market')).toBe('CN')
  expect(new URL(page.url()).searchParams.get('company')).toBeNull()
  expect(new URL(page.url()).searchParams.get('result')).toBeNull()
})
