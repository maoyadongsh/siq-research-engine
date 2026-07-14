import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'
import type {
  MarketDocumentFullPostgresStatus,
  UsSecCaseSetStatus,
  UsSecPackageDetail,
} from '../../src/features/market-parsing/api'

const structuredReport = {
  id: 'us-nvda-10k-html',
  market: 'US',
  company: 'NVIDIA Corporation',
  companyName: 'NVIDIA Corporation',
  ticker: 'NVDA',
  category: '10-K',
  filename: 'NVDA_2025_10-K_0001045810-25-000023.htm',
  relativePath: 'US/NVDA/2025/10-K/NVDA_2025_10-K_0001045810-25-000023.htm',
  size: 7_421_312,
  mtime: '2026-06-27T08:00:00.000Z',
  url: '/api/downloads/report-file?path=US%2FNVDA%2F2025%2F10-K%2FNVDA_2025_10-K_0001045810-25-000023.htm',
  contentType: 'text/html',
  isPdf: false,
  form: '10-K',
  reportType: '年报',
  reportFamily: 'annual',
  reportEnd: '2025-01-26',
  publishedAt: '2025-03-14',
  metadataPath: 'US/NVDA/2025/10-K/metadata.json',
  accessionNumber: '0001045810-25-000023',
  sourceId: 'sec-edgar',
}

const pdfAttachment = {
  id: 'us-nvda-proxy-pdf',
  market: 'US',
  company: 'NVIDIA Corporation',
  companyName: 'NVIDIA Corporation',
  ticker: 'NVDA',
  category: 'proxy',
  filename: 'NVDA_2025_proxy.pdf',
  relativePath: 'US/NVDA/2025/proxy/NVDA_2025_proxy.pdf',
  size: 1_512_400,
  mtime: '2026-06-26T08:00:00.000Z',
  url: '/api/downloads/report-file?path=US%2FNVDA%2F2025%2Fproxy%2FNVDA_2025_proxy.pdf',
  contentType: 'application/pdf',
  isPdf: true,
  form: 'DEF 14A',
  reportType: 'proxy',
  reportFamily: 'proxy',
  reportEnd: '2025-01-26',
  publishedAt: '2025-04-25',
  accessionNumber: '0001045810-25-000101',
  sourceId: 'sec-edgar',
}

const usCaseSet = {
  company_count: 1,
  counts: {
    xbrl_fact_count: 1280,
    normalized_metric_count: 64,
  },
  ingest_report: {
    package_count: 0,
    summary: {
      xbrl_facts: 0,
      normalized_metrics: 0,
      sections: 0,
      tables: 0,
      evidence_items: 0,
    },
  },
  items: [
    {
      ticker: 'NVDA',
      company_name: 'NVIDIA Corporation',
      fiscal_year: 2025,
      period_end: '2025-01-26',
      filing_date: '2025-03-14',
      quality_status: 'pass',
      package_path: 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023',
      quality_summary: {
        section_count: 42,
        table_count: 18,
        xbrl_fact_count: 1280,
        normalized_metric_count: 64,
      },
    },
  ],
}

const packageDetail = {
  package_path: 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2025-10-K-0001045810-25-000023',
  manifest: {
    ticker: 'NVDA',
    company_name: 'NVIDIA Corporation',
    form: '10-K',
    period_end: '2025-01-26',
  },
  preview: {
    raw_html: 'raw/filing.htm',
    default_markdown: 'report_complete.md',
  },
  counts: {
    sections: 42,
    tables: 18,
    metrics: 64,
    evidence: 240,
    dimension_facts: 219,
    dimension_metrics: 9,
  },
  sections: [
    { section_id: 'business', file: 'business.md', html_anchor: 'business', char_start: 0, char_end: 800, text_length: 800 },
    { section_id: 'mda', file: 'mda.md', html_anchor: 'mda', char_start: 800, char_end: 1800, text_length: 1000 },
  ],
  tables: [
    {
      table_id: 'mda-table-1',
      table_index: 1,
      title: 'Revenue table',
      section_id: 'mda',
      row_count: 3,
      column_count: 3,
      html_anchor: 'mda-table',
      is_financial_statement_candidate: true,
    },
  ],
  metrics: [
    {
      metric_id: 'revenue-2025',
      canonical_name: 'Revenue',
      value: '130497000000',
      unit: 'USD',
      period_key: 'FY2025',
      concept: 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
    },
  ],
  dimension_facts: [
    {
      fact_id: 'fact-product-revenue',
      concept: 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
      label: 'Revenue by Product',
      value: '60000000000',
      unit: 'USD',
      period: { start: '2024-01-29', end: '2025-01-26', fiscal_year: 2025 },
      context: 'c-product-revenue',
      dimensions: { 'srt:ProductOrServiceAxis': 'nvda:ComputeAndNetworkingMember' },
      anchor: 'f-product-revenue',
      evidence: { evidence_id: 'e-product-revenue', target: '#f-product-revenue' },
    },
  ],
  dimension_metrics: [],
  bridge_checks: {
    overall_status: 'pass',
    summary: { pass: 3, warning: 0, fail: 0 },
    checks: [
      {
        rule_id: 'income_statement_revenue',
        rule_name: 'Revenue continuity',
        period: 'FY2025',
        status: 'pass',
        diff: 0,
        tolerance: 1,
        reason: 'Mocked SEC fact package',
      },
    ],
  },
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

interface MockUsSecWorkbenchOptions {
  documentFullPath?: string
  caseSet?: UsSecCaseSetStatus
  packageDetails?: Record<string, { detail: UsSecPackageDetail; delayMs?: number }>
  packageDetailResponses?: Record<string, Array<{ status?: number; body: unknown }>>
  postgresStatus?: MarketDocumentFullPostgresStatus
}

async function mockUsSecWorkbenchApis(page: Page, options: MockUsSecWorkbenchOptions = {}) {
  const buildRequests: unknown[] = []
  const documentFullImportRequests: unknown[] = []
  const packageFileRequests: Array<{ file: string | null; authorization: string }> = []
  const packageDetailRequests: string[] = []
  const genericPackageRequests: string[] = []
  const packageDetailResponses = new Map(
    Object.entries(options.packageDetailResponses || {}).map(([packagePath, responses]) => [packagePath, [...responses]]),
  )
  const caseSetBase = options.caseSet || usCaseSet
  const caseSet = options.documentFullPath
    ? {
        ...caseSetBase,
        items: caseSetBase.items.map((item) => ({
          ...item,
          document_full_path: options.documentFullPath,
        })),
      }
    : caseSetBase
  const detail = options.documentFullPath
    ? {
        ...packageDetail,
        document_full_path: options.documentFullPath,
        manifest: {
          ...packageDetail.manifest,
          document_full_path: options.documentFullPath,
        },
      }
    : packageDetail

  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

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

    if (pathname === '/api/downloads/reports') {
      await route.fulfill(json({ reports: [structuredReport, pdfAttachment] }))
      return
    }

    if (pathname === '/api/us-sec/case-set') {
      await route.fulfill(json(caseSet))
      return
    }

    if (pathname === '/api/us-sec/packages/AAPL') {
      await route.fulfill(json(detail))
      return
    }

    if (pathname === '/api/us-sec/packages/NVDA') {
      await route.fulfill(json(detail))
      return
    }

    if (pathname === '/api/us-sec/package') {
      const requestedPackagePath = url.searchParams.get('package_path') || ''
      packageDetailRequests.push(requestedPackagePath)
      const queuedResponse = packageDetailResponses.get(requestedPackagePath)?.shift()
      if (queuedResponse) {
        await route.fulfill(json(queuedResponse.body, queuedResponse.status))
        return
      }
      const configured = options.packageDetails?.[requestedPackagePath]
      if (configured?.delayMs) await new Promise((resolve) => setTimeout(resolve, configured.delayMs))
      await route.fulfill(json(configured?.detail || detail))
      return
    }

    if (pathname === '/api/market-reports/package') {
      genericPackageRequests.push(url.searchParams.get('package_path') || '')
      await route.fulfill(json({ detail: 'US rich package detail must use /api/us-sec/package' }, 500))
      return
    }

    if (pathname === '/api/us-sec/package-file') {
      const file = url.searchParams.get('file')
      const authorization = route.request().headers().authorization || ''
      packageFileRequests.push({ file, authorization })
      if (file === 'qa/source_map.json') {
        await route.fulfill(json({
          schema_version: 'market_source_map_v1',
          market: 'US',
          entries: [
            {
              evidence_id: 'business-source',
              source_type: 'sec_html_section',
              section_id: 'business',
              html_anchor: 'business',
              local_path: 'sections/business.md',
              raw: detail.sections[0],
            },
            {
              evidence_id: 'mda-source',
              source_type: 'sec_html_section',
              section_id: 'mda',
              html_anchor: 'mda',
              local_path: 'sections/mda.md',
              raw: detail.sections[1],
            },
          ],
        }))
        return
      }
      if (file === 'raw/filing.htm' && authorization !== 'Bearer playwright-token') {
        await route.fulfill(json({ detail: 'Not authenticated' }, 401))
        return
      }
      if (file === 'raw/filing.htm') {
        const businessParagraphs = '<p>Business section raw HTML content for source trace.</p>'.repeat(32)
        const mdaParagraphs = '<p>Management discussion raw HTML content for source trace.</p>'.repeat(32)
        await route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: '<!doctype html><html><body style="margin:0;padding:12px" onload="localStorage.setItem(\'sec-event-executed\', \'1\')">' +
            '<script>localStorage.setItem(\'sec-script-executed\', \'1\')</script>' +
            '<div style="display:none"><ix:header><ix:hidden><ix:nonNumeric>HIDDEN-XBRL-CONTEXT</ix:nonNumeric></ix:hidden>' +
            '<ix:resources><xbrli:context>HIDDEN-XBRL-RESOURCE</xbrli:context></ix:resources></ix:header></div>' +
            `<section id="business" style="padding:24px"><h1><ix:nonNumeric>SEC raw HTML preview</ix:nonNumeric></h1>${businessParagraphs}</section>` +
            `<section id="mda"><h1>Management Discussion raw HTML</h1>${mdaParagraphs}</section>` +
            '</body></html>',
        })
        return
      }
      if (file === 'sections/mda.md') {
        await route.fulfill({
          status: 200,
          contentType: 'text/markdown',
          body: '# MD&A\n\nManagement discussion for the mocked US SEC package.\n\n| Metric | 2025 | 2024 |\n| --- | ---: | ---: |\n| Revenue | 130497 | 60922 |',
        })
        return
      }
      if (file === 'sections/filing-2024.md') {
        await route.fulfill({ status: 200, contentType: 'text/markdown', body: '# Selected 2024 filing' })
        return
      }
      if (file === 'sections/filing-2025.md') {
        await route.fulfill({ status: 200, contentType: 'text/markdown', body: '# Selected 2025 filing' })
        return
      }
      if (file === 'tables/table_0001.json') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            table_id: 'mda-table-1',
            table_index: 1,
            title: 'Revenue table',
            section_id: 'mda',
            row_count: 3,
            column_count: 3,
            is_financial_statement_candidate: true,
            rows: [
              ['Metric', '2025', '2024'],
              ['Revenue', '130497', '60922'],
              ['Gross margin', '97824', '44301'],
            ],
          }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'text/markdown',
        body: '# Business\n\nNVIDIA generated a mocked US SEC evidence package.',
      })
      return
    }

    if (pathname === '/api/market-reports/packages/build') {
      buildRequests.push(route.request().postDataJSON())
      await route.fulfill(json({
        ok: true,
        package: detail,
        stdout: 'US 证据包已生成',
      }))
      return
    }

    if (pathname === '/api/market-reports/document-full/import') {
      documentFullImportRequests.push(route.request().postDataJSON())
      await route.fulfill(json({
        ok: true,
        stdout: 'US SEC PostgreSQL入库完成',
      }))
      return
    }

    if (pathname === '/api/market-reports/document-full/status' && options.postgresStatus) {
      await route.fulfill(json({ markets: { US: { postgres: options.postgresStatus } } }))
      return
    }

    if (pathname === '/api/us-sec/case-set/ingest') {
      await route.fulfill(json({ ok: true, stdout: 'dry run ok' }))
      return
    }

    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })

  return {
    buildRequests,
    documentFullImportRequests,
    packageFileRequests,
    packageDetailRequests,
    genericPackageRequests,
  }
}

async function openRecentNvdaTask(page: Page) {
  await page.goto('/parse-us')
  const recentTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2025-01-26' })
  await recentTask.getByRole('button', { name: '查看结果' }).click()
  await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toBeVisible()
}

test.describe('美股 SEC 解析工作台', () => {
  test('先展示已下载财报并从下载文件生成解析产物包', async ({ page }) => {
    const {
      buildRequests,
      packageFileRequests,
      packageDetailRequests,
      genericPackageRequests,
    } = await mockUsSecWorkbenchApis(page)

    await page.goto('/parse-us')

    await expect(page.getByRole('heading', { name: '已下载财报', exact: true })).toBeVisible()
    const structuredRow = page.locator('.pdf-download-item').filter({ hasText: structuredReport.filename })
    await expect(structuredRow.getByText(structuredReport.filename, { exact: true })).toBeVisible()
    await expect(page.getByText('解析产物已生成').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: '最近任务（点击查看结果）', exact: true })).toBeVisible()
    await expect(page.getByText('NVIDIA Corporation · NVDA · 10-K · 2025-01-26')).toBeVisible()
    await expect(page.getByText('选择一条已解析 SEC 任务后查看证据包、勾稽校验和入库状态。')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '上传附件', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: '美股 PDF 兼容入口' })).toBeVisible()
    await expect(page.getByRole('link', { name: '打开 PDF 解析' })).toHaveCount(0)

    const downloadedHeadingTop = await page.getByRole('heading', { name: '已下载财报', exact: true }).evaluate((element) => element.getBoundingClientRect().top)
    const uploadHeadingTop = await page.getByRole('heading', { name: '上传附件', exact: true }).evaluate((element) => element.getBoundingClientRect().top)
    const recentTasksTop = await page.getByRole('heading', { name: '最近任务（点击查看结果）', exact: true }).evaluate((element) => element.getBoundingClientRect().top)
    expect(downloadedHeadingTop).toBeLessThan(uploadHeadingTop)
    expect(uploadHeadingTop).toBeLessThan(recentTasksTop)

    const searchSurfaceStyle = await page.locator('.pdf-download-search').evaluate((element) => {
      const style = window.getComputedStyle(element)
      return {
        borderRadius: style.borderRadius,
        backgroundColor: style.backgroundColor,
      }
    })
    expect(searchSurfaceStyle.borderRadius).toBe('16px')
    expect(searchSurfaceStyle.backgroundColor).toBe('rgb(248, 250, 252)')

    const taskRowStyle = await page.locator('.pdf-task-item').first().evaluate((element) => {
      const style = window.getComputedStyle(element)
      return {
        borderRadius: style.borderRadius,
        backgroundColor: style.backgroundColor,
      }
    })
    expect(taskRowStyle.borderRadius).toBe('10px')
    expect(taskRowStyle.backgroundColor).toBe('rgb(248, 250, 252)')

    await expect(structuredRow.getByRole('button', { name: /解析/ })).toBeEnabled()

    const pdfRow = page.locator('.pdf-download-item').filter({ hasText: pdfAttachment.filename })
    await expect(pdfRow.getByRole('button', { name: /解析/ })).toBeDisabled()

    await structuredRow.getByRole('button', { name: /解析/ }).click()
    await expect(page.getByText('US 证据包已生成')).toBeVisible()
    await expect(page.getByRole('heading', { name: '最近任务（点击查看结果）', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toHaveCount(0)

    const recentTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2025-01-26' })
    await recentTask.getByRole('button', { name: '查看结果' }).click()
    await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toBeVisible()
    await expect(page.getByText('manifest.json')).toBeVisible()
    await expect(page.getByText('核心解析产物清单')).toBeVisible()
    await expect(page.getByText('13/13', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Markdown 结果', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Business', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '解析质量报告', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'XBRL 维度事实', exact: true })).toBeVisible()
    await expect(page.getByText('全量 219 条 · 显示 1 条样例', { exact: true })).toBeVisible()
    await expect(page.getByText('Revenue by Product', { exact: true })).toBeVisible()
    await expect(page.getByText('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'HTML/iXBRL 可视化溯源', exact: true })).toBeVisible()
    await expect.poll(() => packageFileRequests.find((request) => request.file === 'raw/filing.htm')?.authorization).toBe('Bearer playwright-token')
    const sourceFrameLocator = page.locator('iframe[title="SEC 原始 HTML"]')
    await expect(sourceFrameLocator).toHaveAttribute('sandbox', 'allow-same-origin allow-popups')
    const sourceFrame = page.frameLocator('iframe[title="SEC 原始 HTML"]')
    await expect(sourceFrame.getByText('SEC raw HTML preview')).toBeVisible()
    await expect(sourceFrame.getByText('HIDDEN-XBRL-CONTEXT')).toHaveCount(0)
    await expect(sourceFrame.getByText('HIDDEN-XBRL-RESOURCE')).toHaveCount(0)
    await expect.poll(() => sourceFrame.locator('#business').evaluate((element) => getComputedStyle(element).paddingLeft)).toBe('24px')
    await expect(sourceFrame.locator('meta[http-equiv="Content-Security-Policy"]')).toHaveAttribute('content', /default-src 'none'/)
    await expect.poll(() => page.evaluate(() => localStorage.getItem('sec-script-executed'))).toBeNull()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('sec-event-executed'))).toBeNull()
    await expect(page.getByTestId('us-sec-source-active-section')).toContainText('business')

    const sectionSelect = page.getByLabel('选择 SEC Markdown 文件')
    await sectionSelect.selectOption('sections/mda.md')
    await expect(page.getByTestId('us-sec-source-markdown-pane')).toContainText('Management discussion')
    await expect(page.getByTestId('us-sec-source-markdown-pane').locator('table').filter({ hasText: 'Revenue' }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: '表格上下文' })).toBeVisible()
    await expect(page.getByText('Gross margin')).toBeVisible()
    const frameHandle = await page.locator('iframe[title="SEC 原始 HTML"]').elementHandle()
    const frame = await frameHandle?.contentFrame()
    expect(frame).toBeTruthy()
    await expect.poll(async () => frame?.evaluate(() => window.scrollY) ?? 0).toBeGreaterThan(400)
    await expect(page.getByTestId('us-sec-source-active-section')).toContainText('mda')

    await page.waitForTimeout(800)
    await frame?.evaluate(() => window.scrollTo(0, 0))
    await expect.poll(async () => sectionSelect.inputValue()).toBe('sections/business.md')
    await expect(page.getByTestId('us-sec-source-active-section')).toContainText('business')

    await page.getByLabel('切换左右联动').uncheck()
    await frame?.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.waitForTimeout(500)
    await expect(sectionSelect).toHaveValue('sections/business.md')
    await expect(page.getByRole('heading', { name: '财务勾稽校验', exact: true })).toBeVisible()

    expect(packageDetailRequests).toContain(packageDetail.package_path)
    expect(genericPackageRequests).toEqual([])
    expect(packageFileRequests.some((request) => request.file === 'sections/business.md')).toBe(true)
    expect(packageFileRequests.some((request) => request.file === 'report_complete.md')).toBe(false)

    expect(buildRequests).toEqual([
      {
        market: 'US',
        download_relative_path: structuredReport.relativePath,
        force: true,
      },
    ])
  })

  test('缺少 document_full.json 路径时前置禁用 PostgreSQL 和一键入库', async ({ page }) => {
    await mockUsSecWorkbenchApis(page)

    await openRecentNvdaTask(page)

    await expect(page.getByRole('button', { name: '一键入库' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'PostgreSQL入库' })).toBeDisabled()
    await expect(page.getByText('缺少 SEC parser result document_full.json 路径，请先刷新结果包')).toHaveCount(2)
  })

  test('PostgreSQL 入库使用 US SEC document_full_path 请求体', async ({ page }) => {
    const documentFullPath = 'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json'
    const { documentFullImportRequests } = await mockUsSecWorkbenchApis(page, { documentFullPath })

    await openRecentNvdaTask(page)
    await page.getByRole('button', { name: 'PostgreSQL入库' }).click()

    await expect(page.getByText('US SEC PostgreSQL入库完成')).toBeVisible()
    expect(documentFullImportRequests).toEqual([
      {
        market: 'US',
        document_full_path: documentFullPath,
        ddl: true,
        force: false,
      },
    ])
  })

  test('刷新后已下载财报和最近任务仍显示 PostgreSQL 已入库', async ({ page }) => {
    const documentFullPath = 'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023/document_full.json'
    await mockUsSecWorkbenchApis(page, {
      documentFullPath,
      postgresStatus: { status: 'postgres_ready', facts: 1280, chunks: 42, evidence: 240 },
    })

    await page.goto('/parse-us')
    const downloadedRow = page.locator('.pdf-download-item').filter({ hasText: structuredReport.filename })
    const recentTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2025-01-26' })
    await expect(downloadedRow.getByText('PostgreSQL 已入库', { exact: true })).toBeVisible()
    await expect(recentTask.getByText('PostgreSQL 已入库', { exact: true })).toBeVisible()

    await page.reload()
    await expect(downloadedRow.getByText('PostgreSQL 已入库', { exact: true })).toBeVisible()
    await expect(recentTask.getByText('PostgreSQL 已入库', { exact: true })).toBeVisible()
  })

  test('详情请求失败时显示就地重试且不把失败伪装成空产物', async ({ page }) => {
    const refreshedDetail = {
      ...packageDetail,
      counts: { ...packageDetail.counts, sections: 43 },
    }
    const { packageDetailRequests } = await mockUsSecWorkbenchApis(page, {
      packageDetailResponses: {
        [packageDetail.package_path]: [
          { status: 404, body: { detail: 'Not Found' } },
          { body: packageDetail },
          { body: refreshedDetail },
        ],
      },
    })

    await page.goto('/parse-us')
    const recentTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2025-01-26' })
    await recentTask.getByRole('button', { name: '查看结果' }).click()

    await expect(page.getByRole('heading', { name: '解析产物详情加载失败', exact: true })).toBeVisible()
    await expect(page.getByText('Not Found', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toHaveCount(0)
    await expect(page.getByText('0/13', { exact: true })).toHaveCount(0)
    await expect(page.getByText('SEC 解析产物 · missing', { exact: true })).toHaveCount(0)

    await page.getByRole('button', { name: '重试', exact: true }).click()
    await expect(page.getByRole('heading', { name: '数据管线', exact: true })).toBeVisible()
    await expect(page.getByText('13/13', { exact: true })).toBeVisible()
    await expect(page.locator('.pdf-quality-grid').getByText('42', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await expect(page.locator('.pdf-quality-grid').getByText('43', { exact: true })).toBeVisible()
    await expect.poll(() => packageDetailRequests.length).toBe(3)
    expect(packageDetailRequests).toEqual([
      packageDetail.package_path,
      packageDetail.package_path,
      packageDetail.package_path,
    ])
  })

  test('同 ticker 多 filing 快速切换时忽略较早 package 的迟到响应', async ({ page }) => {
    const olderPackagePath = 'data/wiki/us/companies/NVDA-NVIDIA-Corporation/reports/2024-10-K-0001045810-24-000029'
    const newerPackagePath = packageDetail.package_path
    const baseItem = usCaseSet.items[0]
    const olderDetail = {
      ...packageDetail,
      package_path: olderPackagePath,
      manifest: { ...packageDetail.manifest, period_end: '2024-01-28' },
      sections: [{ ...packageDetail.sections[0], section_id: 'filing-2024', file: 'filing-2024.md' }],
      preview: { ...packageDetail.preview, default_markdown: 'report_complete-2024.md' },
    }
    const newerDetail = {
      ...packageDetail,
      sections: [{ ...packageDetail.sections[0], section_id: 'filing-2025', file: 'filing-2025.md' }],
      preview: { ...packageDetail.preview, default_markdown: 'report_complete-2025.md' },
    }
    const { packageDetailRequests, genericPackageRequests } = await mockUsSecWorkbenchApis(page, {
      caseSet: {
        ...usCaseSet,
        items: [
          {
            ...baseItem,
            fiscal_year: 2024,
            period_end: '2024-01-28',
            filing_date: '2024-03-15',
            package_path: olderPackagePath,
            parser_result_dir: 'data/parser-results/us-sec/NVDA-10-K-0001045810-24-000029',
          },
          {
            ...baseItem,
            package_path: newerPackagePath,
            parser_result_dir: 'data/parser-results/us-sec/NVDA-10-K-0001045810-25-000023',
          },
        ],
      },
      packageDetails: {
        [olderPackagePath]: { detail: olderDetail, delayMs: 250 },
        [newerPackagePath]: { detail: newerDetail, delayMs: 10 },
      },
    })

    await page.goto('/parse-us')
    const olderTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2024-01-28' })
    const newerTask = page.locator('.pdf-task-item').filter({ hasText: 'NVIDIA Corporation · NVDA · 10-K · 2025-01-26' })
    await olderTask.getByRole('button', { name: '查看结果' }).click()
    await newerTask.getByRole('button', { name: '查看结果' }).click()

    await expect(page.getByRole('heading', { name: 'Selected 2025 filing', exact: true })).toBeVisible()
    await page.waitForTimeout(350)
    await expect(page.getByRole('heading', { name: 'Selected 2025 filing', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Selected 2024 filing', exact: true })).toHaveCount(0)
    expect(packageDetailRequests).toEqual(expect.arrayContaining([olderPackagePath, newerPackagePath]))
    expect(genericPackageRequests).toEqual([])
  })
})
