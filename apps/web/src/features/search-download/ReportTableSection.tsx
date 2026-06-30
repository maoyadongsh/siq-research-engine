import type { ReactNode } from 'react'
import {
  reportTypeLabel,
  typeStyles,
  type CandidateExplanation,
  type ReportItem,
} from './model'

export function ReportTableSection({
  reports,
  title,
  icon,
  selected,
  candidateExplanationMap,
  onToggleSelect,
  onToggleAll,
}: {
  reports: ReportItem[]
  title: string
  icon: ReactNode
  selected: Set<string>
  candidateExplanationMap: Map<string, CandidateExplanation>
  onToggleSelect: (key: string) => void
  onToggleAll: (reports: ReportItem[]) => void
}) {
  if (reports.length === 0) return null
  const allChecked = reports.every((report) => selected.has(report.document_url))

  return (
    <div className="overflow-hidden rounded-[var(--radius-panel)] border border-border bg-card shadow-sm">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold text-text">
          {icon}
          {title}
        </h3>
        <label className="flex h-10 cursor-pointer items-center gap-2 self-start rounded-xl border border-border bg-bg/50 px-3 text-sm font-semibold text-text-muted transition-colors hover:bg-bg sm:self-auto">
          全选
          <input
            type="checkbox"
            checked={allChecked}
            onChange={() => onToggleAll(reports)}
            className="h-5 w-5 cursor-pointer rounded accent-primary"
          />
        </label>
      </div>
      <div className="divide-y divide-border/60 md:hidden">
        {reports.map((report, idx) => {
          const explanation = candidateExplanationMap.get(report.document_url)
          return (
            <div key={report.document_url || idx} className="p-4">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={selected.has(report.document_url)}
                  onChange={() => onToggleSelect(report.document_url)}
                  className="mt-0.5 h-5 w-5 shrink-0 cursor-pointer rounded accent-primary"
                />
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-sm font-semibold leading-6 text-text">{report.title}</span>
                  {explanation ? (
                    <span className="mt-1 block break-words text-sm leading-6 text-text-muted">{explanation.title_zh}</span>
                  ) : null}
                  <span className="mt-3 flex flex-wrap items-center gap-2 text-xs text-text-muted">
                    <span className={typeStyles[report.report_type] || 'secondary-table-chip'}>
                      {explanation?.report_type_zh || reportTypeLabel(report)}
                    </span>
                    <span className="rounded-full border border-border bg-bg/60 px-2.5 py-1 font-mono tabular-nums">
                      {explanation?.period_zh || report.report_end || '-'}
                    </span>
                    <span className="rounded-full border border-border bg-bg/60 px-2.5 py-1 font-mono tabular-nums">
                      披露 {report.published_at || '-'}
                    </span>
                    {explanation?.warnings?.length ? (
                      <span className="rounded-full border border-warning/20 bg-warning/10 px-2.5 py-1 text-warning">
                        {explanation.warnings.join('；')}
                      </span>
                    ) : null}
                    {explanation ? (
                      <span className={`rounded-xl border px-2.5 py-1.5 ${
                        explanation.recommended ? 'border-primary/20 bg-primary/5 text-primary' : 'border-border bg-bg/60'
                      }`}>
                        {explanation.recommended ? '推荐：' : ''}{explanation.recommendation}
                      </span>
                    ) : null}
                  </span>
                </span>
              </label>
            </div>
          )
        })}
      </div>
      <div className="scroll-hint hidden overflow-x-auto md:block">
        <table className="w-full min-w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-bg/60">
              <th className="w-12 px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">选择</th>
              <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">报告标题</th>
              <th className="min-w-[12rem] px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">中文说明</th>
              <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">类型</th>
              <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">报告期</th>
              <th className="px-4 py-3 text-left text-[11px] font-bold uppercase tracking-wider text-text-muted">披露日期</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((report, idx) => {
              const explanation = candidateExplanationMap.get(report.document_url)
              return (
                <tr
                  key={report.document_url || idx}
                  className="border-b border-border/50 transition-colors last:border-0 hover:bg-bg/50"
                >
                  <td className="px-4 py-3 align-top">
                    <input
                      type="checkbox"
                      checked={selected.has(report.document_url)}
                      onChange={() => onToggleSelect(report.document_url)}
                      className="h-5 w-5 cursor-pointer rounded accent-primary"
                    />
                  </td>
                  <td className="px-4 py-3 align-top font-medium leading-6 text-text">{report.title}</td>
                  <td className="px-4 py-3 align-top leading-6 text-text-muted">
                    {explanation ? (
                      <div className="space-y-1">
                        <div className="font-medium text-text">{explanation.title_zh}</div>
                        <div className={explanation.recommended ? 'text-primary' : ''}>{explanation.recommendation}</div>
                        {explanation.warnings?.length ? (
                          <div className="text-warning">{explanation.warnings.join('；')}</div>
                        ) : null}
                      </div>
                    ) : (
                      <span className="text-xs">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <span className={typeStyles[report.report_type] || 'secondary-table-chip'}>
                      {explanation?.report_type_zh || reportTypeLabel(report)}
                    </span>
                  </td>
                  <td className="px-4 py-3 align-top font-mono text-xs tabular-nums text-text-muted">
                    {explanation?.period_zh || report.report_end}
                  </td>
                  <td className="px-4 py-3 align-top font-mono text-xs tabular-nums text-text-muted">
                    {report.published_at}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
