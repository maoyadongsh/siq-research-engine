import type { MeetingArtifact } from './types'

export const meetingMinutesSectionKeys = [
  'agenda_topics',
  'chapters',
  'decisions',
  'open_questions',
  'risks',
  'action_items',
  'speaker_viewpoints',
  'keywords',
] as const

export type MeetingMinutesSectionKey = typeof meetingMinutesSectionKeys[number]

export interface MeetingMinutesItem {
  text: string
  source_segment_ids: string[]
  owner?: string
  due_date?: string
  status?: string
  speaker?: string
}

export interface MeetingMinutesContent {
  overview: string
  agenda_topics: MeetingMinutesItem[]
  chapters: MeetingMinutesItem[]
  decisions: MeetingMinutesItem[]
  open_questions: MeetingMinutesItem[]
  risks: MeetingMinutesItem[]
  action_items: MeetingMinutesItem[]
  speaker_viewpoints: MeetingMinutesItem[]
  keywords: MeetingMinutesItem[]
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function text(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function item(value: unknown): MeetingMinutesItem | null {
  if (typeof value === 'string') {
    const itemText = value.trim()
    return itemText ? { text: itemText, source_segment_ids: [] } : null
  }
  const source = record(value)
  if (!source) return null
  const itemText = text(source.text)
  if (!itemText) return null
  const sourceSegmentIds = Array.isArray(source.source_segment_ids)
    ? [...new Set(source.source_segment_ids.map(text).filter(Boolean))]
    : []
  return {
    text: itemText,
    source_segment_ids: sourceSegmentIds,
    ...(text(source.owner) ? { owner: text(source.owner) } : {}),
    ...(text(source.due_date) ? { due_date: text(source.due_date) } : {}),
    ...(text(source.status) ? { status: text(source.status) } : {}),
    ...(text(source.speaker) ? { speaker: text(source.speaker) } : {}),
  }
}

function items(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.map(item).filter((value): value is MeetingMinutesItem => value !== null)
}

export function parseMeetingMinutes(value: unknown): MeetingMinutesContent {
  const source = record(value) || {}
  return {
    overview: text(source.overview),
    agenda_topics: items(source.agenda_topics),
    chapters: items(source.chapters),
    decisions: items(source.decisions),
    open_questions: items(source.open_questions),
    risks: items(source.risks),
    action_items: items(source.action_items),
    speaker_viewpoints: items(source.speaker_viewpoints),
    keywords: items(source.keywords),
  }
}

export function isMinutesArtifact(artifact: MeetingArtifact) {
  return artifact.artifact_type === 'final_minutes' || artifact.artifact_type === 'rolling_minutes'
}

function newest(values: MeetingArtifact[]) {
  return [...values].sort((left, right) => right.version - left.version)[0]
}

function hasContent(artifact: MeetingArtifact) {
  return Boolean(artifact.content_json || artifact.content_text)
}

export function selectPreferredMinutesArtifact(artifacts: MeetingArtifact[]) {
  for (const type of ['final_minutes', 'rolling_minutes']) {
    const candidates = artifacts.filter((artifact) => artifact.artifact_type === type)
    if (!candidates.length) continue
    return newest(candidates.filter(hasContent)) || newest(candidates)
  }
  return undefined
}

export function selectLatestMinutesArtifact(artifacts: MeetingArtifact[]) {
  const finalArtifacts = artifacts.filter((artifact) => artifact.artifact_type === 'final_minutes')
  return newest(finalArtifacts.length ? finalArtifacts : artifacts.filter((artifact) => artifact.artifact_type === 'rolling_minutes'))
}

export function hasMeetingMinutesContent(content: MeetingMinutesContent) {
  return Boolean(content.overview || meetingMinutesSectionKeys.some((key) => content[key].length))
}
