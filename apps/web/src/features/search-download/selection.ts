import type { ReportItem } from './model'

export function toggleSearchDownloadSelection(current: Set<string>, key: string) {
  const next = new Set(current)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

export function reportSelectionKeys(reports: ReportItem[]) {
  return reports.map((report) => report.document_url).filter(Boolean)
}

export function toggleSearchDownloadReportGroupSelection(current: Set<string>, reports: ReportItem[]) {
  const keys = reportSelectionKeys(reports)
  const next = new Set(current)
  const allSelected = keys.length > 0 && keys.every((key) => next.has(key))
  if (allSelected) keys.forEach((key) => next.delete(key))
  else keys.forEach((key) => next.add(key))
  return next
}
