import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const hkMvpPackagePath = 'data/wiki/hk/companies/00700-Tencent-Holdings/reports/2025-annual'

const hkMvpPackage = {
  package_path: hkMvpPackagePath,
  market: 'HK',
  ticker: '00700',
  company_name: 'Tencent Holdings Limited',
  report_type: 'annual',
  fiscal_year: 2025,
  filing_id: 'hk-00700-2025-annual',
  quality_status: 'warning',
  paths: {
    report_complete: 'report_complete.md',
    manifest: 'manifest.json',
  },
  counts: {
    sections: 38,
    tables: 21,
    metrics: 74,
    evidence: 312,
  },
  quality_gates: {
    schema_version: 'siq_market_package_quality_gates_v1',
    overall_status: 'warning',
    action_blocked: true,
    import_blocked: true,
    vector_ingest_blocked: true,
    force_allowed: true,
    block_reasons: [
      'missing_required_statement: cash_flow_statement',
      'artifact_hash_status: missing',
    ],
    evidence_coverage_ratio: 0.82,
    required_statement_status: {
      income_statement: 'present',
      balance_sheet: 'present',
      cash_flow_statement: 'missing',
    },
    missing_required_statements: ['cash_flow_statement'],
    artifact_hash_status: 'missing',
    artifact_hash_missing: ['manifest.json.sha256'],
    parser_warnings: ['table continuation low confidence'],
    rule_warnings: ['cash flow statement missing'],
    critical_warnings: [],
  },
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function mockSecondaryMarketMvpApis(page: Page) {
  const importRequests: unknown[] = []
  const vectorRequests: unknown[] = []

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

    if (pathname === '/api/pdf/health') {
      await route.fulfill(json({ mineru: true, vlm: true, submit_ready: true }))
      return
    }

    if (pathname === '/api/pdf/tasks') {
      await route.fulfill(json({ tasks: [] }))
      return
    }

    if (pathname === '/api/downloads/reports') {
      await route.fulfill(json({ reports: [] }))
      return
    }

    if (pathname === '/api/market-reports/packages') {
      expect(url.searchParams.get('market')).toBe('HK')
      await route.fulfill(json({
        ok: true,
        market: 'HK',
        count: 1,
        packages: [hkMvpPackage],
      }))
      return
    }

    if (pathname === '/api/market-reports/packages/import') {
      importRequests.push(route.request().postDataJSON())
      await route.fulfill(json({
        ok: true,
        stdout: 'HK MVP PostgreSQL import dry-run accepted with force=true',
        parse_run_id: 'hk-00700-2025-annual',
      }))
      return
    }

    if (pathname === '/api/market-reports/packages/vector-ingest') {
      vectorRequests.push(route.request().postDataJSON())
      await route.fulfill(json({
        ok: true,
        dry_run: true,
        summary: {
          market: 'HK',
          package_path: hkMvpPackagePath,
          chunks: 128,
          force: true,
        },
      }))
      return
    }

    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })

  return { importRequests, vectorRequests }
}

test.describe('二级市场 MVP 闭环', () => {
  test('港股 PDF 解析页不展示 Wiki 证据包质量门禁面板', async ({ page }) => {
    const { importRequests, vectorRequests } = await mockSecondaryMarketMvpApis(page)

    await page.goto('/parse-hk')

    await expect(page.getByRole('heading', { name: '港股 PDF 解析' })).toBeVisible()
    await expect(page.getByText('PostgreSQL 直接从解析产物入库')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Wiki 证据包' })).toHaveCount(0)
    await expect(page.getByText('00700 Tencent Holdings Limited')).toHaveCount(0)
    await expect(page.getByText('质量 warning')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '强制入库' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '强制检索' })).toHaveCount(0)

    expect(importRequests).toEqual([])
    expect(vectorRequests).toEqual([])
  })
})
