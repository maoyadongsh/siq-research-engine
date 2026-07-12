import type { ResearchIdentity } from './agentChatTypes'

const IDENTITY_FIELDS = ['market', 'company_id', 'filing_id', 'parse_run_id'] as const

type IdentitySource = Partial<ResearchIdentity> | Record<string, unknown> | null | undefined

function clean(value: unknown) {
  const text = String(value ?? '').trim()
  return text || undefined
}

/**
 * Merge only explicitly supplied identity fields. A conflicting field makes
 * the derived identity unusable instead of guessing which record is correct.
 */
export function mergeResearchIdentity(...sources: IdentitySource[]): ResearchIdentity | undefined {
  const merged: ResearchIdentity = {}
  for (const field of IDENTITY_FIELDS) {
    const values = [...new Set(sources.map((source) => clean(source?.[field])).filter((value): value is string => Boolean(value)))]
    if (values.length > 1) return undefined
    if (values.length === 1) merged[field] = values[0]
  }
  return Object.keys(merged).length ? merged : undefined
}
