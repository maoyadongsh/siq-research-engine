import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

const readyDocument = {
  document_id: 'DOC-NEW',
  deal_id: 'DEAL-001',
  filename: 'DOC-NEW.pdf',
  original_filename: '示例科技首次公开发行股票并在科创板上市招股说明书（注册稿）.pdf',
  document_type: 'prospectus',
  document_profile: 'cn_a_share_prospectus',
  document_status: 'active',
  parse_status: 'succeeded',
  analysis_source_status: 'ready_with_restrictions',
  index_status: 'indexed',
  current_parse_run_id: 'PRUN-20260713-ABCDEFGHIJKL',
  exchange: 'SSE',
  board: 'star',
  filing_stage: 'registration_draft',
  document_date: '2026-07-01',
  size_bytes: 25_000_000,
  created_at: '2026-07-13T08:00:00Z',
  supersedes_document_id: 'DOC-OLD',
  original_url: '/api/primary-market/projects/DEAL-001/materials/DOC-NEW/original',
}

const parseRun = {
  parse_run_id: 'PRUN-20260713-ABCDEFGHIJKL',
  status: 'succeeded',
  quality_status: 'ready_with_restrictions',
  capabilities: {
    text_evidence: 'ready',
    source_page_trace: 'ready',
    financial_facts: 'blocked',
    semantic_index: 'indexed',
  },
}

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function mockMaterialsApis(page: Page) {
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
      await route.fulfill(json({ deals: [{ deal_id: 'DEAL-001', company_name: '示例科技', industry: '先进制造', stage: 'IPO' }] }))
      return
    }
    if (path === '/api/primary-market/projects/DEAL-001/materials') {
      await route.fulfill(json({
        deal_id: 'DEAL-001',
        materials: [
          readyDocument,
          {
            ...readyDocument,
            document_id: 'DOC-OLD',
            filename: 'DOC-OLD.pdf',
            original_filename: '示例科技招股说明书（申报稿）.pdf',
            document_status: 'superseded',
            analysis_source_status: 'superseded',
            created_at: '2026-06-01T08:00:00Z',
            supersedes_document_id: null,
            superseded_by_document_id: 'DOC-NEW',
          },
          {
            document_id: 'DOC-BP',
            deal_id: 'DEAL-001',
            filename: 'business-plan.pdf',
            original_filename: '示例科技商业计划书.pdf',
            document_type: 'bp',
            status: 'uploaded',
            created_at: '2026-05-01T08:00:00Z',
          },
        ],
      }))
      return
    }
    if (path === '/api/primary-market/projects/DEAL-001/materials/DOC-NEW') {
      await route.fulfill(json({
        deal_id: 'DEAL-001',
        document: readyDocument,
        current_parse_run: parseRun,
        analysis_source: { status: 'ready_with_restrictions', source_id: 'PM:DEAL-001:DOC-NEW:PRUN-20260713-ABCDEFGHIJKL', parse_run_id: parseRun.parse_run_id, capabilities: parseRun.capabilities },
        quality: { status: 'ready_with_restrictions', warnings: ['财务报表期间仍需人工复核'] },
      }))
      return
    }
    if (path === '/api/primary-market/projects/DEAL-001/materials/DOC-OLD') {
      await route.fulfill(json({ document: { ...readyDocument, document_id: 'DOC-OLD', document_status: 'superseded', analysis_source_status: 'superseded', created_at: '2026-06-01T08:00:00Z', superseded_by_document_id: 'DOC-NEW' }, current_parse_run: parseRun }))
      return
    }
    if (path === '/api/deals/DEAL-001/evidence') {
      await route.fulfill(json({ quality_report: { status: 'warn', item_count: 48, verified_count: 40, dimensions: ['business', 'finance', 'legal'], missing_dimensions: ['risk'] }, items: [] }))
      return
    }
    await route.fulfill(json({ items: [], artifacts: [], projects: [] }))
  })
}

test.describe('一级市场材料中心响应式验收', () => {
  for (const viewport of viewports) {
    test(`${viewport.name} 招股书状态、capability 与操作不溢出`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await mockMaterialsApis(page)
      await page.goto('/primary-market/materials?dealId=DEAL-001')

      await expect(page.getByRole('heading', { name: '一级市场材料中心' })).toBeVisible()
      await expect(page.getByText('可用于文本分析，财务受限').first()).toBeVisible()
      await expect(page.getByRole('button', { name: '重新解析' }).first()).toBeVisible()
      await expect(page.getByRole('button', { name: '停用分析源' }).first()).toBeVisible()
      await expect(page.getByRole('link', { name: '原件' }).first()).toHaveAttribute('href', '/api/primary-market/projects/DEAL-001/materials/DOC-NEW/original')
      await expect(page.getByText('Task ID')).toHaveCount(0)

      const layout = await page.evaluate(() => {
        const articles = [...document.querySelectorAll('article')].map((element) => {
          const rect = element.getBoundingClientRect()
          return { left: rect.left, right: rect.right, width: rect.width }
        })
        return {
          scrollWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
          articles,
        }
      })
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
      expect(layout.articles.length).toBeGreaterThanOrEqual(3)
      for (const article of layout.articles) {
        expect(article.left).toBeGreaterThanOrEqual(0)
        expect(article.right).toBeLessThanOrEqual(layout.viewportWidth + 1)
        expect(article.width).toBeGreaterThan(0)
      }
    })
  }
})
