import { Building2, FileText } from 'lucide-react'
import { EmptyState } from '@/components/ui'

interface ReportEmptyStateProps {
  selectedDir: string
  hasReports: boolean
  selectedReportUrl: string
  companyName: string
  emptyTitle: (companyName: string) => string
  emptyDescription: string
}

export default function ReportEmptyState({
  selectedDir,
  hasReports,
  selectedReportUrl,
  companyName,
  emptyTitle,
  emptyDescription,
}: ReportEmptyStateProps) {
  if (selectedReportUrl) return null

  if (selectedDir && !hasReports) {
    return <EmptyState icon={<FileText className="h-16 w-16" />} title={emptyTitle(companyName)} description={emptyDescription} />
  }

  return <EmptyState icon={<Building2 className="h-16 w-16" />} title="选择公司查看报告" description="从上方下拉框中选择一家公司，查看其报告内容。" />
}
