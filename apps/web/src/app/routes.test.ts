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

test('secondary market navigation groups existing routes without changing their paths', () => {
  const routesSource = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'routes.tsx'), 'utf-8')

  assert.match(routesSource, /label: '二级市场'/)
  assert.match(routesSource, /to: '\/', label: '工作平台', end: true/)
  assert.match(routesSource, /to: '\/search', label: '财报下载'/)
  assert.match(routesSource, /to: '\/parse', label: '财报解析'/)
  assert.match(routesSource, /to: '\/analysis', label: '智能分析'/)
  assert.match(routesSource, /to: '\/verify', label: '事实核查'/)
  assert.match(routesSource, /to: '\/tracking', label: '持续跟踪'/)
  assert.match(routesSource, /to: '\/legal', label: '法务合规'/)
  assert.match(routesSource, /to: '\/system-dashboard', label: '系统平台', permission: 'system.config'/)
})

test('application center navigation groups existing application routes', () => {
  const routesSource = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'routes.tsx'), 'utf-8')

  assert.match(routesSource, /label: '应用中心'/)
  assert.match(routesSource, /to: '\/documents', label: '文档解析', end: true/)
  assert.match(routesSource, /meetingsNavigationEnabled \? \[\{ to: '\/meetings', label: '会议转写' \}\] : \[\]/)
  assert.match(routesSource, /to: '\/vector-ingest', label: '向量入库', permission: 'system.config'/)
})

test('meeting transcription routes are isolated from chat and primary-market meeting routes', () => {
  const routesSource = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'routes.tsx'), 'utf-8')
  const meetingRoutes = [
    '/meetings',
    '/meetings/new',
    '/meetings/import',
    '/meetings/lexicon',
    '/meetings/voiceprints',
    '/meetings/:meetingId/live',
    '/meetings/:meetingId',
  ]

  for (const route of meetingRoutes) {
    assert.match(routesSource, new RegExp(`defineRoute\\('${route.replaceAll('/', '\\/')}'`))
  }

  assert.match(routesSource, /const meetingsNavigationEnabled = import\.meta\.env\.VITE_SIQ_MEETINGS_ENABLED === '1'/)
  assert.match(routesSource, /const loadMeetingUnavailable: PageLoader = \(\) => import\('\.\.\/pages\/MeetingUnavailable'\)/)
  assert.match(routesSource, /selectFeatureRouteLoader\(meetingsNavigationEnabled, loadEnabled, loadMeetingUnavailable\)/)
  assert.match(routesSource, /meetingsNavigationEnabled \? \[\{ to: '\/meetings', label: '会议转写' \}\] : \[\]/)
  assert.match(routesSource, /defineRoute\('\/chat', \(\) => import\('\.\.\/pages\/ChatPage'\)/)
  assert.match(routesSource, /defineRoute\('\/primary-market\/meeting', \(\) => import\('\.\.\/pages\/PrimaryMarketMeeting'\)/)
})
