import { expect, test } from '@playwright/test'
import { mockAuthenticatedWorkspace } from '../support/mockApi'

const dealId = 'DEAL-YUSHU-2026-001'
const dealPath = `/deals/${encodeURIComponent(dealId)}`

test.describe('Deal OS demo 最小浏览验收', () => {
  test('从 /deals 进入项目并浏览 workflow、agents、decision、audit 页面', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await mockAuthenticatedWorkspace(page)

    await page.goto('/deals')
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '项目管理' })).toBeVisible()
    await expect(page.getByText('SIQ-YUSHU-2026-002')).toBeVisible()
    await expect(page.getByText(dealId)).toBeVisible()

    await page.getByRole('link', { name: /宇树科技/ }).click()
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(new RegExp(`${dealPath}$`))
    await expect(page.getByRole('heading', { name: '宇树科技' })).toBeVisible()
    await expect(page.getByText('Deal Status')).toBeVisible()
    await expect(page.getByText('历史来源: SIQ-YUSHU-2026-002')).toBeVisible()

    await page.goto(`${dealPath}/workflow`)
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '宇树科技' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '阶段状态' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'R1 专家摘要' })).toBeVisible()
    await expect(page.getByText('Serial dry-run')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'R1.5-R4 正式推进' })).toBeVisible()
    await expect(page.getByRole('radio', { name: 'Hermes 模型' })).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByRole('button', { name: '执行 R2 模型' })).toBeDisabled()
    await expect(page.getByRole('heading', { name: '显性分歧' })).toBeVisible()
    await expect(page.getByText('收入质量与估值假设')).toBeVisible()
    await expect(page.getByText('旧版规则 fallback dry-run')).toBeVisible()

    const r15DryRunRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/run-r1-5-chairman`) &&
      request.method() === 'POST' &&
      request.postDataJSON().dry_run === true &&
      request.postDataJSON().mode === 'model'
    )
    await page.getByRole('button', { name: 'R1.5 dry-run' }).click()
    await r15DryRunRequest

    const r2DryRunRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/run-r2`) &&
      request.method() === 'POST' &&
      request.postDataJSON().dry_run === true &&
      request.postDataJSON().mode === 'model'
    )
    await page.getByRole('button', { name: 'R2 dry-run' }).click()
    await r2DryRunRequest
    await expect(page.getByText('run-r2')).toBeVisible()
    await expect(page.getByText('phases/r2_reports.json')).toBeVisible()
    await expect(page.getByRole('button', { name: '执行 R2 模型' })).toBeDisabled()
    await page.getByLabel(/已复核 dry-run，允许写入/).check()
    await expect(page.getByRole('button', { name: '执行 R2 模型' })).toBeEnabled()
    const r2WriteResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/run-r2`) &&
      response.request().method() === 'POST' &&
      response.request().postDataJSON().dry_run === false &&
      response.request().postDataJSON().mode === 'model'
    )
    await page.getByRole('button', { name: '执行 R2 模型' }).click()
    expect((await r2WriteResponse).ok()).toBe(true)
    await expect(page.getByText('Workflow · advanced').first()).toBeVisible()

    const r3DryRunRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/run-r3`) &&
      request.method() === 'POST' &&
      request.postDataJSON().dry_run === true &&
      request.postDataJSON().mode === 'model' &&
      request.postDataJSON().skip === false
    )
    await page.getByRole('button', { name: 'R3 dry-run' }).click()
    await r3DryRunRequest
    await expect(page.getByText('run-r3')).toBeVisible()
    await expect(page.getByText('discussion/04_R3_红蓝对抗.md')).toBeVisible()

    const r4DryRunRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/finalize-r4`) &&
      request.method() === 'POST' &&
      request.postDataJSON().dry_run === true &&
      request.postDataJSON().mode === 'model' &&
      request.postDataJSON().overwrite === false
    )
    await page.getByRole('button', { name: 'R4 dry-run' }).click()
    await r4DryRunRequest
    await expect(page.getByText('finalize-r4')).toBeVisible()
    await expect(page.getByText('decision/IC_DECISION_REPORT.html')).toBeVisible()

    const rulingDryRunResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/generate-dispute-rulings`) &&
      response.request().method() === 'POST'
    )
    await page.getByRole('button', { name: '旧版规则 fallback dry-run' }).click()
    expect((await rulingDryRunResponse).ok()).toBe(true)
    await expect(page.getByText('旧版规则裁决 fallback')).toBeVisible()
    await expect(page.getByText('request_followup')).toBeVisible()
    await expect(page.getByText('follow-up: 补充 2025 现金流拆解').first()).toBeVisible()
    await expect(page.getByRole('button', { name: '写入规则 fallback' })).toBeDisabled()
    await page.getByLabel(/已确认模型路径当前不可用/).check()
    await expect(page.getByRole('button', { name: '写入规则 fallback' })).toBeEnabled()

    await page.getByRole('radio', { name: '确定性 fallback' }).click()
    await expect(page.getByText(/当前为确定性 fallback/)).toBeVisible()
    const fallbackRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/deals/${encodeURIComponent(dealId)}/workflow/run-r2`) &&
      request.method() === 'POST' &&
      request.postDataJSON().mode === 'deterministic_fallback'
    )
    await page.getByRole('button', { name: 'R2 dry-run' }).click()
    await fallbackRequest

    await page.goto(`${dealPath}/agents`)
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: 'IC Agents' })).toBeVisible()
    await expect(page.getByText('Profile Status')).toBeVisible()
    await expect(page.getByText('SIQ IC Strategist')).toBeVisible()
    await expect(page.getByText('siq_ic_finance_auditor', { exact: true })).toBeVisible()
    await expect(page.getByText('startup_receipt_missing')).toBeVisible()

    await page.goto(`${dealPath}/decision`)
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '最终投决报告' })).toBeVisible()
    await expect(page.getByText('Decision Contract')).toBeVisible()
    await expect(page.getByText('Weighted:')).toBeVisible()
    await expect(page.getByText('Chairman:')).toBeVisible()
    await expect(page.getByText('宇树科技建议有条件通过')).toBeVisible()

    await page.goto(`${dealPath}/audit`)
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: '审计链' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '归档摘要' })).toBeVisible()
    await expect(page.getByText('历史来源', { exact: true })).toBeVisible()
    await expect(page.getByText('Audit Summary')).toBeVisible()
    await expect(page.getByText('openclaw_imported', { exact: true })).toBeVisible()
    await expect(page.getByText('r1_agent_submitted', { exact: true })).toBeVisible()
    await expect(page.getByText('r4_decision_generated', { exact: true })).toBeVisible()
  })
})
