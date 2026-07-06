import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  pdfFinancialCandidateText,
  pdfFinancialCheckReasonText,
  pdfFinancialCheckTitle,
  pdfFinancialStatusText,
} from './pdfFinancialDisplay'

test('pdf financial display maps JP statement diagnostics to user-facing text', () => {
  const check = {
    rule_id: 'required.statement.cash_flow_statement',
    reason: 'statement_candidate_found_but_not_structured',
    left: { statement_type: 'cash_flow_statement' },
    raw: {
      jp_candidate: {
        table_index: 80,
        pdf_page_number: 89,
        confidence: 'high',
      },
    },
  }

  assert.equal(pdfFinancialStatusText('warning'), '需复核')
  assert.equal(pdfFinancialCheckTitle(check, 'JP'), '必备正式报表：现金流量表')
  assert.equal(pdfFinancialCheckReasonText(check, 'JP'), '质量报告已定位候选表，但结构化抽取未覆盖')
  assert.equal(pdfFinancialCandidateText(check), '候选：表 80 / PDF 89页 / high置信')
})

test('pdf financial display keeps generic check names for non-JP markets', () => {
  const check = {
    rule_id: 'required.statement.cash_flow_statement',
    rule_name: 'Required statement present: cash_flow_statement',
    reason: 'statement_missing_for_report_type',
    left: { statement_type: 'cash_flow_statement' },
  }

  assert.equal(pdfFinancialCheckTitle(check, 'HK'), 'Required statement present: cash_flow_statement')
  assert.equal(pdfFinancialCheckReasonText(check, 'HK'), 'statement_missing_for_report_type')
})

