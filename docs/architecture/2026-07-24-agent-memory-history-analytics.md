# Agent Memory History Analytics

## Scope

This change adds a controlled, read-only history analytics path for questions such as
"what does this user ask most often?" It uses the authenticated runtime context and
the application database (`siq_app`, schema `agent_memory`). It does not expand the
market-fact query tool allowlist.

## Runtime contract

- `agent_memory.messages` is queried with fixed SQL and binds `tenant_id`, `user_id`,
  `profile`, `deal_id`, and `project_id` from the runtime context.
- Only `role=user` messages in non-deleted sessions are considered.
- Questions are grouped by deterministic NFKC, case, whitespace, and edge-punctuation
  normalization. Semantic clustering is intentionally not used.
- The scan and injected item counts are bounded by
  `SIQ_AGENT_MEMORY_ANALYTICS_SCAN_LIMIT` and
  `SIQ_AGENT_MEMORY_ANALYTICS_MAX_ITEMS`.
- A result that reaches the scan limit is marked incomplete in the injected context.
- Historical user text is JSON-escaped and marked as untrusted data before prompt
  injection.

## Retrieval separation

- Question-history intent receives `<user-history-analytics>` and bypasses company,
  Wiki, PostgreSQL fact fallback, and profile-file memory context.
- Personal-memory intent receives only `user_private` memory items. System profile
  documents cannot satisfy this intent.
- All other questions keep the existing session-summary, Milvus, reranker, and memory
  recency behavior.

## Compatibility guarantees

- Explicit memory extraction and promotion rules are unchanged.
- `memory_recency_weight`, half-life, decay floor, and the time-decay feature flag are
  unchanged.
- Market-fact schemas and `pg_query.py` read-only policy are unchanged.
- The feature can be disabled with `SIQ_AGENT_MEMORY_ANALYTICS_ENABLED=false`.
