import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

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
    default_markdown: 'sections/business.md',
  },
  counts: {
    sections: 42,
    tables: 18,
    metrics: 64,
    evidence: 240,
    dimension_metrics: 9,
  },
  sections: [
    { section_id: 'business', file: 'business.md' },
    { section_id: 'mda', file: 'mda.md' },
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

async function mockUsSecWorkbenchApis(page: Page) {
  const buildRequests: unknown[] = []
  const packageFileRequests: Array<{ file: string | null; authorization: string }> = []

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
      await route.fulfill(json(usCaseSet))
      return
    }

    if (pathname === '/api/us-sec/packages/AAPL') {
      await route.fulfill(json(packageDetail))
      return
    }

    if (pathname === '/api/us-sec/packages/NVDA') {
      await route.fulfill(json(packageDetail))
      return
    }

    if (pathname === '/api/us-sec/package-file') {
      const file = url.searchParams.get('file')
      const authorization = route.request().headers().authorization || ''
      packageFileRequests.push({ file, authorization })
      if (file === 'raw/filing.htm' && authorization !== 'Bearer playwright-token') {
        await route.fulfill(json({ detail: 'Not authenticated' }, 401))
        return
      }
      if (file === 'raw/filing.htm') {
        await route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: '<!doctype html><html><body><h1>SEC raw HTML preview</h1></body></html>',
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
        package: packageDetail,
        stdout: 'US 证据包已生成',
      }))
      return
    }

    if (pathname === '/api/us-sec/case-set/ingest') {
      await route.fulfill(json({ ok: true, stdout: 'dry run ok' }))
      return
    }

    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })

  return { buildRequests, packageFileRequests }
}

test.describe('美股 SEC 解析工作台', () => {
  test('先展示已下载财报并从下载文件生成解析产物包', async ({ page }) => {
    const { buildRequests, packageFileRequests } = await mockUsSecWorkbenchApis(page)

    await page.goto('/parse-us')

    await expect(page.getByRole('heading', { name: '已下载财报', exact: true })).toBeVisible()
    const structuredRow = page.locator('.pdf-download-item').filter({ hasText: structuredReport.filename })
    await expect(structuredRow.getByText(structuredReport.filename, { exact: true })).toBeVisible()
    await expect(page.getByText('证据包已生成').first()).toBeVisible()
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
    await expect(page.locator('code').filter({ hasText: 'manifest.json' })).toBeVisible()
    await expect(page.getByText('核心解析产物清单')).toBeVisible()
    await expect(page.getByText('8/8', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Markdown 结果', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '解析质量报告', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'HTML/iXBRL 可视化溯源', exact: true })).toBeVisible()
    await expect.poll(() => packageFileRequests.find((request) => request.file === 'raw/filing.htm')?.authorization).toBe('Bearer playwright-token')
    await expect(page.frameLocator('iframe[title="SEC 原始 HTML"]').getByText('SEC raw HTML preview')).toBeVisible()
    await expect(page.getByRole('heading', { name: '财务勾稽校验', exact: true })).toBeVisible()

    expect(buildRequests).toEqual([
      {
        market: 'US',
        download_relative_path: structuredReport.relativePath,
        force: true,
      },
    ])
  })
})
