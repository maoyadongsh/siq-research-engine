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
