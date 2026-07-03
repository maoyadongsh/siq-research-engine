import type { DealDisputeSummary, DealWorkflowGenerateDisputeRulingsResponse } from '@/lib/dealTypes'

export interface DealDisputeCounts {
  disputes: number
  resolved: number
  unresolved: number
  high_severity: number
  positions: number
  rulings: number
}

export interface GeneratedRulingDraft {
  dispute_id: string
  topic: string
  decision: string
  rationale: string
  resolved: boolean
  required_followups: string[]
  evidence_ids: string[]
}

export function disputePositionCount(dispute: { position_count?: number; positions?: unknown }): number {
  if (typeof dispute.position_count === 'number') return dispute.position_count
  const positions = dispute.positions
  return Array.isArray(positions) ? positions.length : 0
}

export function disputeCountsFor(disputes: DealDisputeSummary[]): DealDisputeCounts {
  const resolved = disputes.filter((item) => item.resolved).length
  return {
    disputes: disputes.length,
    resolved,
    unresolved: disputes.length - resolved,
    high_severity: disputes.filter((item) => String(item.severity || '').toLowerCase() === 'high').length,
    positions: disputes.reduce((total, item) => total + disputePositionCount(item), 0),
    rulings: disputes.filter((item) => item.chairman_ruling).length,
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function textValue(value: unknown, fallback = '') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function stringList(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value ? [value] : []
  return values.map((item) => textValue(item).trim()).filter(Boolean)
}

function boolValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') return ['true', 'yes', '1', 'resolved'].includes(value.trim().toLowerCase())
  return Boolean(value)
}

export function generatedRulingDraftsFor(
  response?: DealWorkflowGenerateDisputeRulingsResponse | null,
): GeneratedRulingDraft[] {
  return (response?.rulings || []).map((item, index) => {
    const record = asRecord(item)
    const ruling = asRecord(record.ruling)
    const dispute = asRecord(record.dispute)
    return {
      dispute_id: textValue(record.dispute_id || ruling.dispute_id || dispute.dispute_id, `draft-${index + 1}`),
      topic: textValue(dispute.topic || record.topic || record.dispute_id || ruling.dispute_id, '裁决草案'),
      decision: textValue(ruling.decision),
      rationale: textValue(ruling.rationale),
      resolved: boolValue(ruling.resolved),
      required_followups: stringList(ruling.required_followups),
      evidence_ids: stringList(ruling.evidence_ids),
    }
  })
}

export function canWriteGeneratedRulingDrafts({
  preview,
  confirmed,
  busy,
  canPreviewRulings,
}: {
  preview?: DealWorkflowGenerateDisputeRulingsResponse | null
  confirmed: boolean
  busy: boolean
  canPreviewRulings: boolean
}): boolean {
  return Boolean(canPreviewRulings && confirmed && !busy && generatedRulingDraftsFor(preview).length > 0)
}
