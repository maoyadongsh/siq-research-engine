import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const hkMvpPackagePath = 'data/wiki/hk/companies/00700-Tencent-Holdings/reports/2025-annual'
type GenericPdfMarket = 'HK' | 'JP' | 'KR' | 'EU'

const genericMarketRoutes: Record<GenericPdfMarket, string> = {
  HK: '/parse-hk',
  JP: '/parse-jp',
  KR: '/parse-kr',
  EU: '/parse-eu',
}

const genericPdfArtifacts = [
  'result.md',
  'result_complete.md',
  'document_full.json',
  'content_list_enhanced.json',
  'financial_data.json',
  'financial_checks.json',
  'quality_report.json',
  'table_relations.json',
  'table_index.json',
  'artifact_manifest.json',
  'hash_manifest.json',
  'metadata.json',
]

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

async function mockGenericMarketPostgresApis(page: Page, market: GenericPdfMarket) {
  const taskId = `document-full-${market.toLowerCase()}-task`
  const documentFullImportRequests: unknown[] = []
  const artifacts = Object.fromEntries(
    genericPdfArtifacts.map((name) => [name, { exists: true, size: 128, url: `/api/pdf/artifact/${taskId}/${name}` }]),
  )
  const task = {
    task_id: taskId,
    filename: `generic-${market}-annual-report.pdf`,
    market,
    status: 'completed',
    stage: 'completed',
    progress_percent: 100,
    total_pages: 3,
    processed_pages: 3,
    markdown_ready: true,
    created_at: '2026-07-01T08:00:00.000Z',
    completed_at: '2026-07-01T08:01:00.000Z',
  }

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
      await route.fulfill(json({ tasks: [task] }))
      return
    }

    if (pathname === '/api/downloads/reports') {
      await route.fulfill(json({ reports: [] }))
      return
    }

    if (pathname === `/api/pdf/status/${taskId}`) {
      await route.fulfill(json({ ...task, logs: [], log_count: 0 }))
      return
    }

    if (pathname === `/api/pdf/result/${taskId}`) {
      await route.fulfill(json({
        artifacts,
        markdown: `# ${market} document_full fixture\n\nPostgreSQL 入库按钮使用通用市场解析产物。`,
      }))
      return
    }

    if (pathname === `/api/pdf/quality/${taskId}`) {
      await route.fulfill(json({
        quality: {
          overall_status: 'ok',
          market,
          page_count: 3,
          table_count: 2,
          core_tables: [],
          key_candidates: [],
        },
      }))
      return
    }

    if (pathname === `/api/workflow/task/${taskId}/status`) {
      await route.fulfill(json({
        documentFull: { status: 'ready' },
        artifactBundle: {
          status: 'ready',
          ready: true,
          readyCount: genericPdfArtifacts.length,
          total: genericPdfArtifacts.length,
          missing: [],
          message: `${genericPdfArtifacts.length}/${genericPdfArtifacts.length} 个核心文件已生成`,
        },
        wiki: { status: 'ready', message: 'LLM-Wiki 已由解析产物生成' },
        semantic: {
          status: 'ready',
          counts: { facts: 5, evidence: 8 },
          llm: { status: 'ready', counts: { claims: 2, risks: 1 } },
        },
        database: { status: 'pending', message: '等待 PostgreSQL 入库' },
      }))
      return
    }

    if (pathname === '/api/market-reports/document-full/import') {
      documentFullImportRequests.push(route.request().postDataJSON())
      await route.fulfill(json({
        ok: true,
        stdout: `${market} document_full PostgreSQL import accepted`,
        parse_run_id: `parse-${market.toLowerCase()}-document-full`,
      }))
      return
    }

    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })

  return { taskId, documentFullImportRequests }
}

async function mockDownloadedStructuredReportApis(page: Page, market: 'EU' | 'HK') {
  const buildRequests: unknown[] = []
  const relativePath = market === 'EU'
    ? 'EU/DE/SAP/annual/2025/sap-2025-annual.xhtml'
    : 'HK/00700/annual/2025/tencent-2025-annual.xhtml'

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
      await route.fulfill(json({
        reports: [{
          id: `${market}-structured-report`,
          market,
          company: market === 'EU' ? 'SAP SE' : 'Tencent Holdings',
          category: 'annual/2025',
          filename: relativePath.split('/').at(-1),
          relativePath,
          size: 2048,
          mtime: '2026-07-12T08:00:00.000Z',
          url: `/api/downloads/report-file?path=${encodeURIComponent(relativePath)}`,
          contentType: 'application/xhtml+xml',
          isPdf: false,
        }],
      }))
      return
    }
    if (pathname === '/api/market-reports/packages/build') {
      buildRequests.push(route.request().postDataJSON())
      await route.fulfill(json({ ok: true, queued: true, job_id: 'eu-esef-build-job' }))
      return
    }
    if (pathname === '/api/jobs/eu-esef-build-job') {
      await route.fulfill(json({
        job_id: 'eu-esef-build-job',
        status: 'succeeded',
        result: {
          ok: true,
          stdout: 'EU ESEF package built',
          package: {
            package_path: 'data/wiki/eu/companies/SAP-SE/reports/2025-annual',
            market: 'EU',
          },
        },
      }))
      return
    }
    await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
  })

  return { buildRequests, relativePath }
}

test.describe('二级市场 MVP 闭环', () => {
  test('港股 PDF 解析页不展示 Wiki 证据包质量门禁面板', async ({ page }) => {
    const { importRequests, vectorRequests } = await mockSecondaryMarketMvpApis(page)

    await page.goto('/parse-hk')

    await expect(page.getByRole('heading', { name: '港股 PDF 解析' })).toBeVisible()
    await expect(page.getByText('PostgreSQL 入库材料')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Wiki 证据包' })).toHaveCount(0)
    await expect(page.getByText('00700 Tencent Holdings Limited')).toHaveCount(0)
    await expect(page.getByText('质量 warning')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '强制入库' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '强制检索' })).toHaveCount(0)

    expect(importRequests).toEqual([])
    expect(vectorRequests).toEqual([])
  })

  for (const market of Object.keys(genericMarketRoutes) as GenericPdfMarket[]) {
    test(`${market} PostgreSQL 入库按钮调用 document_full 通用入库接口`, async ({ page }) => {
      const { taskId, documentFullImportRequests } = await mockGenericMarketPostgresApis(page, market)

      await page.goto(`${genericMarketRoutes[market]}?task=${encodeURIComponent(taskId)}`)

      await expect(page.getByRole('heading', { level: 1, name: /解析/ })).toBeVisible()
      await expect(page.getByRole('heading', { name: '数据管线' })).toBeVisible()
      const postgresButton = page.getByRole('button', { name: 'PostgreSQL入库' })
      await expect(postgresButton).toBeEnabled()

      await postgresButton.click()

      await expect.poll(() => documentFullImportRequests).toEqual([
        { market, task_id: taskId, ddl: true },
      ])
    })
  }

  test('EU downloaded iXBRL builds a package with a portable scoped payload', async ({ page }) => {
    const { buildRequests, relativePath } = await mockDownloadedStructuredReportApis(page, 'EU')

    await page.goto('/parse-eu')

    const buildButton = page.getByRole('button', { name: '结构化解析' })
    await expect(buildButton).toBeEnabled()
    await buildButton.click()

    await expect.poll(() => buildRequests).toEqual([{
      market: 'EU',
      download_relative_path: relativePath,
      force: false,
    }])
    await expect(page.getByText(/结构化解析产物已生成：data\/wiki\/eu\/companies\/SAP-SE\/reports\/2025-annual/)).toBeVisible()
  })

  test('HK keeps a downloaded XHTML report outside the PDF parser and package builder', async ({ page }) => {
    const { buildRequests } = await mockDownloadedStructuredReportApis(page, 'HK')

    await page.goto('/parse-hk')

    await expect(page.getByText('HK 仅支持 PDF 解析；此文件不会送入 PDF parser。')).toBeVisible()
    await expect(page.getByRole('button', { name: '结构化解析' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '解析' })).toBeDisabled()
    expect(buildRequests).toEqual([])
  })
})
