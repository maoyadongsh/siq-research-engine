import type {
  DealEvidenceMilvusIndexReceipt,
  PrimaryMarketMaterial,
  PrimaryMarketWikiProjection,
} from '@/lib/dealTypes'

export interface MaterialEvidenceStage {
  status?: string | null
  items?: number | null
}

export function projectWikiStage(
  catalog: PrimaryMarketWikiProjection | null | undefined,
  parsedDocumentCount: number,
) {
  const projectedCount = Number(catalog?.counts?.company_wiki_projections || 0)
  if (projectedCount <= 0) return 'pending'
  if (parsedDocumentCount > 0 && projectedCount >= parsedDocumentCount) return 'ready'
  return 'partial'
}

export function materialWikiStage(
  material: PrimaryMarketMaterial,
  catalogEntry?: Record<string, unknown> | null,
) {
  const ownStatus = String(
    material.wiki_status
    || material.wiki_projection?.wiki_status
    || material.wiki_projection?.status
    || '',
  ).trim()
  if (ownStatus) return ownStatus
  if (material.wiki_path || material.wiki_projection?.wiki_path) return 'ready'
  if (catalogEntry?.entry_type === 'company_wiki_projection') return 'ready'
  return 'pending'
}

export function materialMilvusStage(
  evidence: MaterialEvidenceStage | null | undefined,
  receipt: DealEvidenceMilvusIndexReceipt | null | undefined,
  currentSnapshotHash?: string | null,
) {
  const evidenceStatus = String(evidence?.status || '').toLowerCase()
  const itemCount = Number(evidence?.items || 0)
  const evidenceReady = evidenceStatus === 'indexed'
    || (itemCount > 0 && !['failed', 'blocked', 'missing'].includes(evidenceStatus))
  if (!evidenceReady) return 'pending'

  const receiptStatus = String(receipt?.status || '').toLowerCase()
  if (receiptStatus === 'failed') return 'failed'
  if (!['indexed', 'unchanged'].includes(receiptStatus)) return 'pending'

  const receiptSnapshot = String(receipt?.snapshot_hash || '')
  const currentSnapshot = String(currentSnapshotHash || '')
  if (!receiptSnapshot || !currentSnapshot || receiptSnapshot !== currentSnapshot) return 'stale'
  return 'indexed'
}
