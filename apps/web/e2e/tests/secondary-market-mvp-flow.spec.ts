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
  test('港股证据包质量门禁可见，并在确认后强制触发入库与检索 dry-run', async ({ page }) => {
    const { importRequests, vectorRequests } = await mockSecondaryMarketMvpApis(page)

    await page.goto('/parse-hk')

    await expect(page.getByRole('heading', { name: '港股 PDF 解析' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Wiki 证据包' })).toBeVisible()
    await expect(page.getByText('00700 Tencent Holdings Limited')).toBeVisible()
    await expect(page.getByText('质量 warning')).toBeVisible()
    await expect(page.getByText('证据 82%')).toBeVisible()
    await expect(page.getByText('报表 2/3')).toBeVisible()
    await expect(page.getByText('hash missing')).toBeVisible()
    await expect(page.getByText('warnings 2')).toBeVisible()

    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('质量门禁未通过')
      expect(dialog.message()).toContain('artifact_hash_status: missing')
      await dialog.accept()
    })
    await page.getByRole('button', { name: '强制入库' }).click()
    await expect(page.getByText('HK MVP PostgreSQL import dry-run accepted with force=true')).toBeVisible()

    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('质量门禁未通过')
      expect(dialog.message()).toContain('missing_required_statement: cash_flow_statement')
      await dialog.accept()
    })
    await page.getByRole('button', { name: '强制检索' }).click()
    await expect(page.getByText('"chunks": 128')).toBeVisible()

    expect(importRequests).toEqual([
      {
        market: 'HK',
        package_path: hkMvpPackagePath,
        ddl: true,
        force: true,
      },
    ])
    expect(vectorRequests).toEqual([
      {
        market: 'HK',
        package_path: hkMvpPackagePath,
        dry_run: true,
        batch_tag: 'market-hk-evidence',
        force: true,
      },
    ])
  })
})
