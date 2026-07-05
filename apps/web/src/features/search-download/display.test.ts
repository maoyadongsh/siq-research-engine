import assert from 'node:assert/strict'
import test from 'node:test'

import { smartSearchPlaceholderForMarket } from './display'
import type { MarketCode } from './model'

test('smart search placeholder examples match the selected market', () => {
  const expected: Record<MarketCode, string> = {
    CN: '例如：比亚迪 2025 年年报',
    HK: '例如：腾讯控股 2025 年年报',
    US: '例如：英伟达 2025 年 10-K',
    EU: '例如：ASML 2025 年年度报告',
    KR: '例如：三星电子 2025 年年报和三季度报告',
    JP: '例如：铠侠 2025 年有价证券报告书',
  }

  for (const [market, placeholder] of Object.entries(expected)) {
    assert.equal(smartSearchPlaceholderForMarket(market as MarketCode), placeholder)
  }
})
