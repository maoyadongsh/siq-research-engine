export type FinancialCheckLike = Record<string, unknown>

export function pdfFinancialStatusText(status: unknown): string {
  if (status === 'pass') return '通过'
  if (status === 'fail') return '存在异常'
  if (status === 'warning') return '需复核'
  if (status === 'error') return '生成失败'
  return '未生成'
}

export function pdfFinancialCheckTitle(check: FinancialCheckLike, market?: string | null): string {
  const statementType = String((check.left as Record<string, unknown> | undefined)?.statement_type || '')
  if (String(market || '').toUpperCase() === 'JP' && String(check.rule_id || '').startsWith('required.statement.')) {
    if (statementType === 'balance_sheet') return '必备正式报表：财政状态表/资产负债表'
    if (statementType === 'income_statement') return '必备正式报表：损益表/利润表'
    if (statementType === 'cash_flow_statement') return '必备正式报表：现金流量表'
  }
  return String(check.rule_name || check.rule_id || '校验失败')
}

export function pdfFinancialCheckReasonText(check: FinancialCheckLike, market?: string | null): string {
  const reason = String(check.reason || '')
  if (String(market || '').toUpperCase() !== 'JP') return reason
  if (reason === 'statement_candidate_found_but_not_structured') return '质量报告已定位候选表，但结构化抽取未覆盖'
  if (reason === 'statement_not_located_in_jp_quality_scan') return '质量扫描未定位正式报表候选'
  if (reason === 'statement_only_summary_or_note_facts_found_for_jp_annual_report') return '仅发现摘要/附注指标，未确认正式报表来源'
  if (reason === 'statement_not_required_for_jp_report_kind') return '当前 JP 报告类型不要求完整三表'
  if (reason === 'required_metric_missing_for_industry_profile') return '行业关键指标未抽取'
  return reason
}

export function pdfFinancialCandidateText(check: FinancialCheckLike): string {
  const raw = check.raw as Record<string, unknown> | undefined
  const candidate = raw?.jp_candidate as Record<string, unknown> | undefined
  if (!candidate) return ''
  const parts = [
    candidate.table_index ? `表 ${candidate.table_index}` : '',
    candidate.pdf_page_number ? `PDF ${candidate.pdf_page_number}页` : '',
    candidate.confidence ? `${candidate.confidence}置信` : '',
  ].filter(Boolean)
  return parts.length ? `候选：${parts.join(' / ')}` : ''
}

