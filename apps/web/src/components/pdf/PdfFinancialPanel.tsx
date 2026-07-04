import { BarChart3, CheckCircle2, ExternalLink, XCircle } from 'lucide-react'
import type { FinancialResult } from '../../lib/pdfTypes'
import { PDF_API } from '../../features/pdf-parsing/api'
import { formatFinancialNumber, scopeName } from '../../lib/pdfFormatting'
import { handleAuthenticatedSourceClick } from '../../lib/authenticatedSourceLinks'
import { pdfFinancialPanelTitle } from './pdfMarketPanelLabels'

export interface PdfFinancialPanelProps {
  financial: FinancialResult
  taskId: string | null
  market?: string | null
}

export function PdfFinancialPanel({ financial, taskId, market }: PdfFinancialPanelProps) {
  const data = financial.financial_data
  const checks = financial.financial_checks
  const metrics = data?.key_metrics || []
  const statements = data?.statements || []
  const summary = checks?.summary || {}
  const status = checks?.overall_status || 'skipped'
  const failures = (checks?.checks || []).filter((c) => c.status === 'fail').slice(0, 8)
  const warnings = (checks?.warnings || []).slice(0, 8)

  const stmtCount = data?.summary?.statement_count ?? statements.length
  const keyMetricCount = data?.summary?.key_metric_count ?? metrics.length
  const scopes = (data?.summary?.scopes || []).map(scopeName).join('、') || '--'
  const statusText = status === 'pass' ? '通过' : status === 'fail' ? '存在异常' : status === 'error' ? '生成失败' : '未生成'

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-text">
            <BarChart3 className="h-4 w-4 text-primary" />
            {pdfFinancialPanelTitle(market)}
          </h3>
          {taskId ? <p className="text-xs text-text-muted">任务 {taskId}</p> : null}
        </div>
        {checks?.overall_status ? (
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${
              status === 'pass' ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'
            }`}
          >
            {status === 'pass' ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
            {statusText}
          </span>
        ) : null}
      </div>

      <div className="pdf-quality-grid">
        <div>
          <strong>{statusText}</strong>
          <span>整体状态</span>
        </div>
        <div>
          <strong>{summary.pass ?? 0}</strong>
          <span>通过</span>
        </div>
        <div>
          <strong>{summary.fail ?? 0}</strong>
          <span>失败</span>
        </div>
        <div>
          <strong>{summary.skipped ?? 0}</strong>
          <span>跳过</span>
        </div>
        <div>
          <strong>{stmtCount}</strong>
          <span>结构化报表</span>
        </div>
      </div>

      <div className="pdf-quality-row">
        <span>识别范围</span>
        <b>{scopes}</b>
      </div>
      <div className="pdf-quality-row">
        <span>关键指标</span>
        <b>{keyMetricCount}</b>
      </div>
      <div className="pdf-quality-row">
        <span>报告年份</span>
        <b>{data?.report_year || '--'}</b>
      </div>

      <div className="pdf-quality-section">
        <div className="pdf-quality-section-title">失败项</div>
        <ul className="list-disc pl-5 text-sm text-text">
          {failures.length ? (
            failures.map((f, i) => (
              <li key={i}>
                <b>{String(f.rule_name || f.rule_id || '校验失败')}</b> · {scopeName(String(f.scope))} · {String(f.period || '--')}
                {f.diff !== undefined ? ` · 差异 ${formatFinancialNumber(Number(f.diff))}` : ''}
                {f.tolerance !== undefined ? ` / 容差 ${formatFinancialNumber(Number(f.tolerance))}` : ''}
              </li>
            ))
          ) : (
            <li>未发现失败项</li>
          )}
        </ul>
      </div>

      <div className="pdf-quality-section">
        <div className="pdf-quality-section-title">提示</div>
        <ul className="list-disc pl-5 text-sm text-warning">
          {warnings.length ? warnings.map((w, i) => <li key={i}>{w}</li>) : <li>无额外提示</li>}
        </ul>
      </div>

      <div className="pdf-quality-section">
        <div className="pdf-chip-row">
          {taskId ? (
            <a
              className="pdf-trace-btn inline-flex items-center gap-1"
              href={`${PDF_API}/financial/${encodeURIComponent(taskId)}`}
              target="_blank"
              rel="noopener"
              onClick={(event) => {
                handleAuthenticatedSourceClick(event.nativeEvent, `${PDF_API}/financial/${encodeURIComponent(taskId)}`).catch((error) => {
                  console.warn('Failed to open authenticated financial link', error)
                })
              }}
            >
              <ExternalLink size={13} />
              打开 JSON
            </a>
          ) : null}
        </div>
      </div>

      {metrics.length ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="text-text-muted">
              <tr>
                <th className="border-b border-border py-2 pr-3">指标</th>
                <th className="border-b border-border py-2 pr-3">期间</th>
                <th className="border-b border-border py-2 pr-3">值</th>
              </tr>
            </thead>
            <tbody>
              {metrics.slice(0, 12).map((item, index) => {
                const row = item as Record<string, unknown>
                const value = Number(row.value)
                return (
                  <tr key={index}>
                    <td className="border-b border-border/70 py-2 pr-3 font-medium text-text">{String(row.name || row.metric || '--')}</td>
                    <td className="border-b border-border/70 py-2 pr-3 text-text-muted">{String(row.period || row.date || '--')}</td>
                    <td className="border-b border-border/70 py-2 pr-3 text-text">
                      {Number.isFinite(value) ? formatFinancialNumber(value) : String(row.value ?? '--')}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
