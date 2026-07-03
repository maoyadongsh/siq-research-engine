import { Building2, FileText } from 'lucide-react'
import { EmptyState } from '@/components/page'

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
    return <EmptyState icon={FileText} title={emptyTitle(companyName)} description={emptyDescription} size="lg" />
  }

  return <EmptyState icon={Building2} title="选择公司查看报告" description="从上方下拉框中选择一家公司，查看其报告内容。" size="lg" />
}
