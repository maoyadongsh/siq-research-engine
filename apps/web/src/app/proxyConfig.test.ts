/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { resolve as resolvePath } from 'node:path'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'

type ProxyRule = {
  prefix: string
  target: string
  rewrite?: (url: string) => string
  headers?: Record<string, string>
}

type ViteProxyRule = {
  target: string
  rewrite?: (url: string) => string
  headers?: Record<string, string>
}

type CreateProxyOptions = {
  backendUrl?: string
  reportFinderUrl?: string
  includeAuth?: boolean
  includeEval?: boolean
}

type ProxyConfigModule = {
  createProxyRules: (options: CreateProxyOptions) => ProxyRule[]
  createViteProxy: (options: CreateProxyOptions) => Record<string, ViteProxyRule>
}

const proxyConfigUrl = pathToFileURL(resolvePath('scripts/proxy-config.mjs')).href
const { createProxyRules, createViteProxy } = await import(/* @vite-ignore */ proxyConfigUrl) as ProxyConfigModule

test('proxy rules expose backend health before the finder fallback', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)
  const healthRule = rules.find((rule) => rule.prefix === '/api/health')

  assert.ok(healthRule)
  assert.ok(prefixes.indexOf('/api/health') < prefixes.indexOf('/api'))
  assert.equal(healthRule?.target, 'http://backend.local')
  assert.equal(healthRule?.rewrite?.('/api/health'), '/health')
})

test('proxy rules keep deal APIs on the backend before the market finder fallback', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)

  assert.ok(prefixes.includes('/api/deals'))
  assert.ok(prefixes.indexOf('/api/deals') < prefixes.indexOf('/api'))
  assert.equal(rules.find((rule) => rule.prefix === '/api/deals')?.target, 'http://backend.local')
  assert.equal(rules.find((rule) => rule.prefix === '/api')?.target, 'http://finder.local')
})

test('vite proxy exposes deal APIs without inheriting the finder rewrite', () => {
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.equal(proxy['/api/deals'].target, 'http://backend.local')
  assert.equal(proxy['/api/deals'].rewrite, undefined)
  assert.equal(proxy['/api'].target, 'http://finder.local')
  const finderRewrite = proxy['/api'].rewrite
  if (!finderRewrite) throw new Error('Expected /api fallback rewrite')
  assert.equal(finderRewrite('/api/v1/reports/search'), '/v1/reports/search')
})

test('proxy rules keep primary-market meeting APIs on the backend before the finder fallback', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)

  assert.ok(prefixes.includes('/api/primary-market'))
  assert.ok(prefixes.indexOf('/api/primary-market') < prefixes.indexOf('/api'))
  assert.equal(rules.find((rule) => rule.prefix === '/api/primary-market')?.target, 'http://backend.local')
})

test('vite proxy exposes primary-market APIs without inheriting the finder rewrite', () => {
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.equal(proxy['/api/primary-market'].target, 'http://backend.local')
  assert.equal(proxy['/api/primary-market'].rewrite, undefined)
})

test('proxy rules never expose the internal parser token through a public pdfapi route', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.equal(rules.some((rule) => rule.prefix === '/pdfapi'), false)
  assert.equal(proxy['/pdfapi'], undefined)
  assert.equal(rules.some((rule) => rule.headers?.['X-PDF2MD-Token']), false)
  assert.equal(proxy['/api/pdf'].target, 'http://backend.local')
})

test('proxy rules keep market report wiki APIs on the backend before the finder fallback', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)

  assert.ok(prefixes.includes('/api/market-reports'))
  assert.ok(prefixes.indexOf('/api/market-reports') < prefixes.indexOf('/api'))
  assert.equal(rules.find((rule) => rule.prefix === '/api/market-reports')?.target, 'http://backend.local')
})

test('vite proxy exposes market report wiki APIs without inheriting the finder rewrite', () => {
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.equal(proxy['/api/market-reports'].target, 'http://backend.local')
  assert.equal(proxy['/api/market-reports'].rewrite, undefined)
})

test('vite proxy exposes market report job APIs without inheriting the finder rewrite', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.ok(prefixes.includes('/api/jobs'))
  assert.ok(prefixes.indexOf('/api/jobs') < prefixes.indexOf('/api'))
  assert.equal(proxy['/api/jobs'].target, 'http://backend.local')
  assert.equal(proxy['/api/jobs'].rewrite, undefined)
})

test('vite proxy exposes backend health without inheriting the finder rewrite', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.ok(prefixes.includes('/api/health'))
  assert.ok(prefixes.indexOf('/api/health') < prefixes.indexOf('/api'))
  assert.equal(proxy['/api/health'].target, 'http://backend.local')
  const healthRewrite = proxy['/api/health'].rewrite
  if (!healthRewrite) throw new Error('Expected /api/health rewrite')
  assert.equal(healthRewrite('/api/health'), '/health')
})
