import type { UsSecSourceMapEntry } from './api'

export type UsSecSyncOrigin = 'html' | 'markdown'

export interface UsSecTraceSection {
  sectionId: string
  file: string
  filePath: string
  title: string
  htmlAnchor: string
  charStart: number
  charEnd: number
  textLength: number
  order: number
  evidenceId?: string
}

export interface UsSecSectionScrollTarget {
  sectionId: string
  filePath: string
  top: number
  approximate: boolean
}

export const US_SEC_SYNC_SUPPRESS_MS = 700

function normalized(value: unknown): string {
  return String(value || '').trim()
}

function numberValue(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function plainRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function cleanPath(value: unknown): string {
  return normalized(value).replace(/^\/+/, '')
}

export function usSecSectionFilePath(value: unknown): string {
  const path = cleanPath(value)
  if (!path) return ''
  return path.startsWith('sections/') ? path : `sections/${path}`
}

export function usSecSectionFileName(value: unknown): string {
  return cleanPath(value).split('/').pop() || ''
}

function sourceMapSectionEntries(sourceMapEntries: UsSecSourceMapEntry[] = []): UsSecSourceMapEntry[] {
  return sourceMapEntries.filter((entry) => normalized(entry.source_type) === 'sec_html_section')
}

function matchingSourceMapEntry(
  section: Record<string, unknown>,
  filePath: string,
  sourceMapEntries: UsSecSourceMapEntry[],
): UsSecSourceMapEntry | undefined {
  const sectionId = normalized(section.section_id)
  return sourceMapSectionEntries(sourceMapEntries).find((entry) => {
    const entryPath = usSecSectionFilePath(entry.local_path)
    return (entryPath && entryPath === filePath) || (sectionId && normalized(entry.section_id) === sectionId)
  })
}

export function normalizeUsSecTraceSections(
  sections: Array<Record<string, unknown>> = [],
  sourceMapEntries: UsSecSourceMapEntry[] = [],
): UsSecTraceSection[] {
  return sections
    .map((section, index) => {
      const initialFilePath = usSecSectionFilePath(section.file)
      const entry = matchingSourceMapEntry(section, initialFilePath, sourceMapEntries)
      const raw = plainRecord(entry?.raw)
      const file = usSecSectionFileName(section.file || raw.file || entry?.local_path)
      const filePath = usSecSectionFilePath(file || entry?.local_path)
      const sectionId = normalized(section.section_id || raw.section_id || entry?.section_id || filePath)
      const title = normalized(section.section_title || raw.section_title || sectionId)
      const htmlAnchor = normalized(section.html_anchor || raw.html_anchor || entry?.html_anchor || sectionId)
      const charStart = numberValue(section.char_start ?? raw.char_start)
      const explicitEnd = numberValue(section.char_end ?? raw.char_end)
      const textLength = numberValue(section.text_length ?? raw.text_length)
      const charEnd = explicitEnd || (charStart && textLength ? charStart + textLength : charStart)
      return {
        sectionId,
        file,
        filePath,
        title,
        htmlAnchor,
        charStart,
        charEnd,
        textLength,
        order: numberValue(section.section_order ?? raw.section_order) || index + 1,
        evidenceId: normalized(entry?.evidence_id) || undefined,
      }
    })
    .filter((section) => section.sectionId && section.filePath)
    .sort((a, b) => a.order - b.order || a.charStart - b.charStart)
}

function maxCharEnd(sections: UsSecTraceSection[]): number {
  return Math.max(
    1,
    ...sections.map((section) => section.charEnd || section.charStart + section.textLength || section.charStart || 0),
  )
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

export function approximateUsSecSectionTop(
  section: UsSecTraceSection,
  sections: UsSecTraceSection[],
  scrollHeight: number,
  viewportHeight: number,
): number {
  const scrollLimit = Math.max(0, scrollHeight - viewportHeight)
  if (!scrollLimit) return 0
  const ratio = clamp(section.charStart / maxCharEnd(sections), 0, 1)
  return Math.round(scrollLimit * ratio)
}

export function buildUsSecSectionScrollTargets(
  sections: UsSecTraceSection[],
  anchorTops: Record<string, number | undefined>,
  scrollHeight: number,
  viewportHeight: number,
): UsSecSectionScrollTarget[] {
  const scrollLimit = Math.max(0, scrollHeight - viewportHeight)
  return sections
    .map((section) => {
      const exactTop = anchorTops[section.sectionId] ?? anchorTops[section.filePath]
      const hasExactTop = Number.isFinite(exactTop)
      const top = hasExactTop
        ? clamp(Math.round(Number(exactTop)), 0, scrollLimit)
        : approximateUsSecSectionTop(section, sections, scrollHeight, viewportHeight)
      return {
        sectionId: section.sectionId,
        filePath: section.filePath,
        top,
        approximate: !hasExactTop,
      }
    })
    .sort((a, b) => a.top - b.top)
}

export function resolveUsSecActiveSection(
  scrollTop: number,
  targets: UsSecSectionScrollTarget[],
  activationOffset = 24,
): UsSecSectionScrollTarget | null {
  if (!targets.length) return null
  const currentTop = scrollTop + activationOffset
  let active = targets[0]
  for (const target of targets) {
    if (target.top <= currentTop) active = target
    if (target.top > currentTop) break
  }
  return active
}

export function isUsSecSyncSuppressed(origin: UsSecSyncOrigin | null, now: number, suppressUntil: number): boolean {
  return Boolean(origin && now < suppressUntil)
}
