import test from 'node:test'
import assert from 'node:assert/strict'

import { pdfFinancialPanelTitle, pdfQualityPanelTitle } from './pdfMarketPanelLabels'

test('pdf panel titles use Japan wording for JP market', () => {
  assert.equal(pdfQualityPanelTitle('JP'), '日本解析质量报告')
  assert.equal(pdfFinancialPanelTitle('JP'), '日本财务识别与一致性检查')
})

test('pdf panel titles keep default wording for non-JP markets', () => {
  assert.equal(pdfQualityPanelTitle('CN'), '解析质量报告')
  assert.equal(pdfFinancialPanelTitle('HK'), '财务勾稽校验')
})
