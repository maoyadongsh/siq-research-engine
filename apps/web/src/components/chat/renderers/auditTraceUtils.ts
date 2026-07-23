export type AuditTraceRun = Record<string, unknown>

function traceRecordValue(run: AuditTraceRun, key: string) {
  const value = run[key]
  return value === null || value === undefined ? '' : String(value)
}

function tracePayload(run: AuditTraceRun): AuditTraceRun {
  return run.payload && typeof run.payload === 'object' && !Array.isArray(run.payload)
    ? run.payload as AuditTraceRun
    : run
}

export function validationRunsForTitle(trace: unknown, title: string): AuditTraceRun[] {
  if (!trace || typeof trace !== 'object') return []
  if (!title.includes('计算器校验') && !title.includes('勾稽校验')) return []
  const runs = (trace as { calculator_runs?: unknown }).calculator_runs
  if (!Array.isArray(runs)) return []
  const reconciliation = title.includes('勾稽')
  return runs.filter((candidate): candidate is AuditTraceRun => {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return false
    const run = candidate as AuditTraceRun
    const payload = tracePayload(run)
    const tool = traceRecordValue(run, 'tool') || traceRecordValue(payload, 'tool')
    const section = traceRecordValue(run, 'section')
    const source = traceRecordValue(run, 'source')
    const line = traceRecordValue(run, 'line')
    const isReconciliation = tool.includes('reconciliation') || section.includes('勾稽')
    const isCalculator = tool.includes('financial_calculator') || section.includes('计算器')
    const belongsToSection = reconciliation ? isReconciliation : isCalculator && !isReconciliation
    if (!belongsToSection) return false

    // Successful reply markers repeat the summary already visible above. Keep the
    // structured runs plus genuine warnings so the expanded list is complete
    // without showing every validation twice.
    if (source === 'reply_marker') {
      return Boolean(line) && !/^[-*+]\s*状态[:：]/.test(line) && /⚠|待核对|失败|不一致|未通过/.test(line)
    }
    return true
  })
}

export function validationRunSummary(run: AuditTraceRun, index: number) {
  const payload = tracePayload(run)
  const operation = traceRecordValue(run, 'operation') || traceRecordValue(payload, 'operation') || '校验记录'
  const metric = traceRecordValue(run, 'metric') || traceRecordValue(payload, 'metric')
  const period = traceRecordValue(run, 'period') || traceRecordValue(payload, 'period')
  const validated = run.validated === true
  const line = traceRecordValue(run, 'line')
  const details = [operation, metric, period].filter(Boolean).join(' · ')
  return {
    label: `记录 ${index + 1}`,
    details: details || '未结构化摘要',
    status: validated ? '已验证' : '待核对',
    line,
    payload,
  }
}
