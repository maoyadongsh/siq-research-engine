# Agent Memory Milvus migration snapshot v1

This contract is an offline planner input. It never authorizes writes to Milvus.

`snapshot_kind` is either `synthetic_contract` or `redacted_read_only_inventory`.
Synthetic input always blocks production readiness.

`identity.observation_status` is backward-compatible:

- Missing or `observed`: all four aggregate counts and every `missing_by_field` value must be non-negative integers.
- `unavailable`: all aggregate counts and every `missing_by_field` value must be JSON `null`, and `observation_reason` is required.

`unavailable` is the correct representation when a v1 collection has no scalar ResearchIdentity fields and no authoritative read-only inventory has established the distribution. Unknown counts must never be encoded as zero. The planner adds `identity_inventory_unavailable`, keeps `migration_ready=false`, and prohibits inferring identity from `metadata_json`, content, titles, or source paths.

The generated artifact separates `planner_live_milvus_contacted=false` from `source_inventory_live_milvus_contacted`; `writes_performed` always remains false. A real migration remains blocked until a separately reviewed read-only inventory supplies schema, entity count, vector/index configuration, ID/content-hash manifest, alias target, and observed ResearchIdentity counts.
