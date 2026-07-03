/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  evaluateOfficialSourceReadiness,
} = await import('./officialSourceReadiness.ts')

test('official source readiness passes through non JP/KR markets without warnings', () => {
  assert.deepEqual(evaluateOfficialSourceReadiness('CN'), {
    ok: true,
    message: null,
  })
})

test('official source readiness allows JP without health source using fallback sources', () => {
  assert.deepEqual(evaluateOfficialSourceReadiness('JP'), {
    ok: true,
    message: '暂未获取到日股官方源状态；将继续尝试公司 IR 官方 PDF 与免费的 TDnet 官方近期披露列表。',
  })
})

test('official source readiness blocks enhanced JP/KR search when required config is missing', () => {
  const decision = evaluateOfficialSourceReadiness('KR', {
    official_source: 'DART',
    report_search_ready: false,
    required_config: ['DART_API_KEY'],
  })

  assert.equal(decision.ok, false)
  assert.match(String(decision.message), /韩国市场DART 增强源需要配置 DART_API_KEY/)
  assert.deepEqual(decision.toast, {
    type: 'warning',
    title: '官方源配置缺失',
    description: decision.message,
  })
})

test('official source readiness warns but continues when partial config is missing', () => {
  const decision = evaluateOfficialSourceReadiness('JP', {
    official_source: 'EDINET',
    report_search_ready: true,
    required_config: ['EDINET_API_KEY'],
  })

  assert.equal(decision.ok, true)
  assert.match(String(decision.message), /日本市场部分官方源缺少 EDINET_API_KEY/)
  assert.equal(decision.toast, undefined)
})

test('official source readiness clears warnings when configured source is ready', () => {
  assert.deepEqual(evaluateOfficialSourceReadiness('KR', {
    official_source: 'DART',
    report_search_ready: true,
  }), {
    ok: true,
    message: null,
  })
})
