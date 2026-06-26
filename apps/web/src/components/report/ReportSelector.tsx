import { Building2 } from 'lucide-react'
import type { Company, ReportItem, ReportType } from '@/lib/reportTypes'
import { reportUrlFor } from '@/lib/reportTypes'

interface ReportSelectorProps {
  companies: Company[]
  selectedDir: string
  onSelectDir: (dir: string) => void
  reports: ReportItem[]
  selectedReportUrl: string
  onSelectReportUrl: (url: string) => void
  reportType: ReportType
  hasReports: boolean
}

export default function ReportSelector({
  companies,
  selectedDir,
  onSelectDir,
  reports,
  selectedReportUrl,
  onSelectReportUrl,
  reportType,
  hasReports,
}: ReportSelectorProps) {
  return (
    <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:flex lg:flex-wrap lg:items-center">
      <label className="min-w-0 space-y-1">
        <span className="secondary-label">公司</span>
        <span className="relative block">
          <Building2 className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <select
            value={selectedDir}
            onChange={(e) => onSelectDir(e.target.value)}
            className="form-control w-full appearance-none py-0 pl-10 pr-9 text-sm font-medium lg:min-w-[240px]"
          >
            {companies.map((c) => (
              <option key={c.dir} value={c.dir}>
                {c.code} {c.name}
              </option>
            ))}
          </select>
        </span>
      </label>
      {hasReports && (
        <label className="min-w-0 space-y-1">
          <span className="secondary-label">报告版本</span>
          <select
            value={selectedReportUrl}
            onChange={(e) => onSelectReportUrl(e.target.value)}
            className="form-control w-full appearance-none px-3 pr-9 text-sm lg:min-w-[280px]"
          >
            {reports.map((r) => (
              <option key={r.filename} value={reportUrlFor(selectedDir, reportType, r)}>
                {r.filename}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}
