export type MarketPackagePathGroupId =
  | 'manifest'
  | 'quality'
  | 'source'
  | 'financial'
  | 'parser'
  | 'qa'
  | 'sections'
  | 'tables'

export interface MarketPackagePathEntry {
  name: string
  file: string
}

export interface MarketPackagePathGroup {
  id: MarketPackagePathGroupId
  label: string
  entries: MarketPackagePathEntry[]
}

const PACKAGE_FILE_GROUPS: Array<{ id: MarketPackagePathGroupId; label: string }> = [
  { id: 'manifest', label: 'Manifest' },
  { id: 'quality', label: 'Quality' },
  { id: 'source', label: 'Source' },
  { id: 'financial', label: 'Financial' },
  { id: 'parser', label: 'Parser' },
  { id: 'qa', label: 'QA' },
  { id: 'sections', label: 'Sections' },
  { id: 'tables', label: 'Tables' },
]

const PACKAGE_FILE_GROUP_BY_KEY: Record<string, MarketPackagePathGroupId> = {
  manifest: 'manifest',
  quality_report: 'quality',
  source_map: 'source',
  financial_data: 'financial',
  financial_checks: 'financial',
  normalized_metrics: 'financial',
  load_plan: 'quality',
  document_full: 'parser',
  content_list_enhanced: 'parser',
  table_relations: 'parser',
  footnotes: 'qa',
  toc: 'qa',
  financial_note_links: 'qa',
  table_quality_signals: 'qa',
  report_complete: 'sections',
  wiki_report_complete: 'sections',
  table_index: 'tables',
}

function groupForPath(name: string, file: string): MarketPackagePathGroupId {
  const [topLevel] = file.split('/')
  if (name === 'report_complete' && (topLevel === 'parser' || topLevel === 'sections')) return topLevel
  if (name === 'wiki_report_complete') return 'sections'
  const byKey = PACKAGE_FILE_GROUP_BY_KEY[name]
  if (byKey) return byKey
  if (file.endsWith('/load_plan.json') || file === 'load_plan.json') return 'quality'
  if (topLevel === 'metrics') return 'financial'
  if (topLevel === 'parser' || topLevel === 'qa' || topLevel === 'sections' || topLevel === 'tables') {
    return topLevel
  }
  return 'source'
}

export function groupMarketPackagePaths(paths: Record<string, string> | null | undefined): MarketPackagePathGroup[] {
  const grouped = new Map<MarketPackagePathGroupId, MarketPackagePathEntry[]>()
  for (const [name, value] of Object.entries(paths || {})) {
    const file = String(value || '').trim().replace(/^\/+/, '')
    if (!file) continue
    const group = groupForPath(name, file)
    grouped.set(group, [...(grouped.get(group) || []), { name, file }])
  }

  return PACKAGE_FILE_GROUPS.flatMap((group) => {
    const entries = grouped.get(group.id) || []
    return entries.length ? [{ ...group, entries }] : []
  })
}
