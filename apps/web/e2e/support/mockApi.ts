import type { Page, Route } from '@playwright/test'

export const e2eUser = {
  id: 1,
  username: 'playwright',
  email: 'playwright@example.com',
  full_name: 'Playwright 验收用户',
  role: 'super_admin',
  approval_status: 'approved',
  is_active: true,
}

const fixedNow = '2026-06-27T08:00:00.000Z'

const workspaceArtifacts = [
  {
    id: 'download-cn-600519',
    type: 'download',
    title: '贵州茅台 2025 年度报告',
    path: 'CN/600519/annual-2025.pdf',
    source: 'CN',
    createdAt: fixedNow,
  },
  {
    id: 'parse-cn-600519',
    type: 'parse',
    title: '贵州茅台 2025 财报解析结果',
    path: 'task-cn-600519',
    source: 'pdf-parser',
    createdAt: fixedNow,
  },
  {
    id: 'analysis-cn-600519',
    type: 'analysis_report',
    title: '贵州茅台 智能分析报告',
    path: '/api/wiki/companies/600519-贵州茅台/analysis/report.html',
    source: 'agent',
    createdAt: fixedNow,
  },
  {
    id: 'fact-cn-600519',
    type: 'factchecker_report',
    title: '贵州茅台 事实核查报告',
    path: '/api/wiki/companies/600519-贵州茅台/factcheck/report.html',
    source: 'agent',
    createdAt: fixedNow,
  },
]

const workspaceSummary = {
  quotas: {
    agentQuestion: { used: 3, limit: 20, remaining: 17, resetAt: fixedNow },
    parseJob: { used: 1, limit: 10, remaining: 9, resetAt: fixedNow },
  },
  stats: { projects: 2, artifacts: 7, downloads: 2, parses: 2, reports: 3 },
  recentArtifacts: workspaceArtifacts,
  artifacts: workspaceArtifacts,
  projects: [
    {
      id: 1,
      name: '600519-贵州茅台',
      company_code: '600519',
      company_name: '贵州茅台',
      status: 'active',
      created_at: fixedNow,
      updated_at: fixedNow,
    },
    {
      id: 2,
      name: '00700-腾讯控股',
      company_code: '00700',
      company_name: '腾讯控股',
      status: 'active',
      created_at: fixedNow,
      updated_at: fixedNow,
    },
  ],
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

async function fulfillMockApi(route: Route) {
  const url = new URL(route.request().url())

  if (url.pathname === '/api/auth/me') {
    await route.fulfill(json(e2eUser))
    return
  }

  if (url.pathname === '/api/workspace/summary') {
    await route.fulfill(json(workspaceSummary))
    return
  }

  if (url.pathname === '/api/workspace/me') {
    await route.fulfill(json({
      user: e2eUser,
      quotas: workspaceSummary.quotas,
      stats: workspaceSummary.stats,
    }))
    return
  }

  if (url.pathname === '/api/workspace/artifacts') {
    await route.fulfill(json({ artifacts: workspaceArtifacts }))
    return
  }

  await route.fulfill(json({ items: [], data: [], results: [], artifacts: [] }))
}

export async function mockAuthenticatedWorkspace(page: Page) {
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/pdfapi/')) {
      await fulfillMockApi(route)
      return
    }
    await route.continue()
  })
}
