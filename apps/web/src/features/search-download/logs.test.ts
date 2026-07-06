/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  buildQueryFailureLogMessage,
  identifierHintForMarket,
  shouldAppendIdentifierHint,
} from './logs.ts'

test('query failure logs append market-specific identifier hints', () => {
  assert.equal(
    buildQueryFailureLogMessage('SEC company ticker catalog did not match: 英伟达', 'US'),
    'SEC company ticker catalog did not match: 英伟达 请直接输入准确的股票代码或 CIK，例如 NVDA 或 0001045810。',
  )
  assert.equal(
    buildQueryFailureLogMessage('HKEX issuer catalog did not match: 腾讯', 'HK'),
    'HKEX issuer catalog did not match: 腾讯 请直接输入准确的股票代码或代号，例如 00700 或 9988.HK。',
  )
})

test('query failure logs do not duplicate existing direct-code hint', () => {
  const message = '未能识别该美股公司，请直接输入准确的股票代码或 CIK，例如 NVDA 或 0001045810。'

  assert.equal(buildQueryFailureLogMessage(message, 'US'), message)
})

test('identifier hint helpers cover all supported markets', () => {
  for (const market of ['CN', 'HK', 'US', 'EU', 'KR', 'JP'] as const) {
    assert.match(identifierHintForMarket(market), /请直接输入准确/)
  }
  assert.equal(shouldAppendIdentifierHint('network timeout'), false)
  assert.equal(shouldAppendIdentifierHint('resolve failed'), true)
})
