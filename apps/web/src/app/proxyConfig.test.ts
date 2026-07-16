/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'

type ProxyRule = {
  prefix: string
  target: string
  rewrite?: (url: string) => string
  headers?: Record<string, string>
  ws?: boolean
}

type ViteProxyRule = {
  target: string
  rewrite?: (url: string) => string
  headers?: Record<string, string>
  ws?: boolean
}

type CreateProxyOptions = {
  backendUrl?: string
  meetingStreamGatewayUrl?: string
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

test('research universe APIs stay on the backend in Vite and production nginx', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const nginxTemplate = readFileSync(resolvePath('nginx.conf.template'), 'utf8')
  const backendRoute = nginxTemplate.match(/location ~ \^\/api\/\(([^)]+)\)/)?.[1] || ''

  assert.ok(prefixes.includes('/api/research-universe'))
  assert.ok(prefixes.indexOf('/api/research-universe') < prefixes.indexOf('/api'))
  assert.equal(proxy['/api/research-universe'].target, 'http://backend.local')
  assert.equal(proxy['/api/research-universe'].rewrite, undefined)
  assert.ok(backendRoute.split('|').includes('research-universe'))
})

test('meeting APIs stay on the backend before the finder fallback', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })
  const prefixes = rules.map((rule) => rule.prefix)
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.ok(prefixes.includes('/api/meetings'))
  assert.ok(prefixes.indexOf('/api/meetings') < prefixes.indexOf('/api'))
  assert.equal(proxy['/api/meetings'].target, 'http://backend.local')
  assert.equal(proxy['/api/meetings'].rewrite, undefined)
  assert.equal(proxy['/api/meetings'].ws, true)
  assert.equal(proxy['/api/deals'].ws, undefined)
})

test('meeting capture and replay can use an isolated same-origin gateway', () => {
  const rules = createProxyRules({
    backendUrl: 'http://backend.local',
    meetingStreamGatewayUrl: 'http://meeting-stream.local',
    reportFinderUrl: 'http://finder.local',
  })
  const audioRule = rules.find((rule) => rule.prefix.startsWith('^/api/meetings/v1/sessions/'))
  const proxy = createViteProxy({
    backendUrl: 'http://backend.local',
    meetingStreamGatewayUrl: 'http://meeting-stream.local',
    reportFinderUrl: 'http://finder.local',
  })

  assert.ok(audioRule)
  assert.equal(audioRule?.target, 'http://meeting-stream.local')
  assert.equal(audioRule?.ws, true)
  assert.equal(proxy[audioRule?.prefix || '']?.target, 'http://meeting-stream.local')
  assert.ok(rules.indexOf(audioRule as ProxyRule) < rules.findIndex((rule) => rule.prefix === '/api/meetings'))
})

test('production nginx keeps meeting APIs on the backend', () => {
  const nginxTemplate = readFileSync(resolvePath('nginx.conf.template'), 'utf8')
  const backendRoute = nginxTemplate.match(/location ~ \^\/api\/\(([^)]+)\)/)?.[1] || ''
  const backendBlock = nginxTemplate.match(/location ~ \^\/api\/\([^)]+\)[^{]*\{([\s\S]*?)\n\s*\}/)?.[1] || ''

  assert.ok(backendRoute.split('|').includes('meetings'))
  assert.match(backendBlock, /proxy_set_header Upgrade \$http_upgrade;/)
  assert.match(backendBlock, /proxy_set_header Connection \$connection_upgrade;/)
  assert.match(nginxTemplate, /proxy_pass \$\{SIQ_MEETING_STREAM_GATEWAY_URL\};/)
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
