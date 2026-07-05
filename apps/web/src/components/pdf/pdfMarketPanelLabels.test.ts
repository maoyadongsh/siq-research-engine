import test from 'node:test'
import assert from 'node:assert/strict'

import {
  pdfCoreTablesLabel,
  pdfFinancialPanelTitle,
  pdfIndicatorCandidatesLabel,
  pdfKeyCandidatesLabel,
  pdfNoIndicatorCandidatesLabel,
  pdfQualityPanelTitle,
} from './pdfMarketPanelLabels'

test('pdf panel titles use Japan wording for JP market', () => {
  assert.equal(pdfQualityPanelTitle('JP'), '日本解析质量报告')
  assert.equal(pdfFinancialPanelTitle('JP'), '日本财务识别与一致性检查')
})

test('pdf panel titles keep A-share wording for CN market', () => {
  assert.equal(pdfQualityPanelTitle('CN'), '解析质量报告')
  assert.equal(pdfFinancialPanelTitle('CN'), '财务勾稽校验')
  assert.equal(pdfCoreTablesLabel('CN'), '财报核心表')
  assert.equal(pdfKeyCandidatesLabel('CN'), '关键表候选')
  assert.equal(pdfIndicatorCandidatesLabel('CN'), '指标/经营分析候选')
  assert.equal(pdfNoIndicatorCandidatesLabel('CN'), '未定位到指标/经营分析候选表')
})

test('pdf panel titles use Europe wording for EU market', () => {
  assert.equal(pdfQualityPanelTitle('EU'), '欧洲 IFRS/ESEF 解析质量报告')
  assert.equal(pdfFinancialPanelTitle('EU'), '欧洲财务识别与一致性检查')
  assert.equal(pdfCoreTablesLabel('EU'), 'IFRS/ESEF 核心报表')
  assert.equal(pdfKeyCandidatesLabel('EU'), '核心报表候选')
})

test('pdf panel titles use market wording for HK/KR/US markets', () => {
  assert.equal(pdfQualityPanelTitle('HK'), '香港市场解析质量报告')
  assert.equal(pdfFinancialPanelTitle('HK'), '香港财务识别与一致性检查')
  assert.equal(pdfCoreTablesLabel('HK'), 'HKFRS/IFRS 核心报表')
  assert.equal(pdfQualityPanelTitle('KR'), '韩国 DART 解析质量报告')
  assert.equal(pdfFinancialPanelTitle('KR'), '韩国财务识别与一致性检查')
  assert.equal(pdfCoreTablesLabel('KR'), 'DART 核心报表')
  assert.equal(pdfQualityPanelTitle('US'), '美国 SEC/PDF 解析质量报告')
  assert.equal(pdfFinancialPanelTitle('US'), '美国 SEC/PDF 财务识别与一致性检查')
  assert.equal(pdfCoreTablesLabel('US'), 'SEC 核心报表')
  assert.equal(pdfIndicatorCandidatesLabel('US'), '指标/MD&A 候选')
  assert.equal(pdfNoIndicatorCandidatesLabel('US'), '未定位到指标/MD&A 候选')
})
