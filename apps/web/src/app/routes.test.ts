/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

test('deal routes are registered and resolve dynamic loaders', () => {
  const routesSource = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'routes.tsx'), 'utf-8')
  const dealRoutes = [
    '/deals/:dealId',
    '/deals/:dealId/agents',
    '/deals/:dealId/workflow',
    '/deals/:dealId/reports',
    '/deals/:dealId/decision',
    '/deals/:dealId/audit',
  ]

  for (const route of dealRoutes) {
    assert.match(routesSource, new RegExp(`defineRoute\\('${route.replaceAll('/', '\\/')}'`))
  }

  assert.match(routesSource, /defineRoute\('\/deals\/:dealId\/agents', \(\) => import\('\.\.\/pages\/DealAgents'\)\)/)
  assert.match(routesSource, /defineRoute\('\/deals\/:dealId\/workflow', \(\) => import\('\.\.\/pages\/DealWorkflow'\)\)/)
  assert.match(routesSource, /defineRoute\('\/deals\/:dealId\/reports', \(\) => import\('\.\.\/pages\/DealReports'\)\)/)
  assert.match(routesSource, /defineRoute\('\/deals\/:dealId\/decision', \(\) => import\('\.\.\/pages\/DealDecision'\)\)/)
  assert.match(routesSource, /defineRoute\('\/deals\/:dealId\/audit', \(\) => import\('\.\.\/pages\/DealAudit'\)\)/)
})

test('primary market routes are registered as additive navigation', () => {
  const routesSource = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'routes.tsx'), 'utf-8')

  assert.match(routesSource, /defineRoute\('\/primary-market', \(\) => import\('\.\.\/pages\/PrimaryMarketWorkbench'\)/)
  assert.match(routesSource, /defineRoute\('\/primary-market\/materials', \(\) => import\('\.\.\/pages\/PrimaryMarketMaterials'\)\)/)
  assert.match(routesSource, /defineRoute\('\/primary-market\/meeting', \(\) => import\('\.\.\/pages\/PrimaryMarketMeeting'\)\)/)
  assert.match(routesSource, /defineRoute\('\/primary-market\/post-investment', \(\) => import\('\.\.\/pages\/PrimaryMarketPostInvestment'\)\)/)
  assert.match(routesSource, /label: '一级市场'/)
  assert.match(routesSource, /label: '工作平台', end: true/)
  assert.match(routesSource, /to: '\/deals', label: '项目管理'/)
  assert.match(routesSource, /label: '材料中心'/)
  assert.match(routesSource, /label: '投研决策'/)
  assert.match(routesSource, /label: '投后管理'/)
})
