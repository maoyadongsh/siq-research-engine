# ORCHESTRATION_BRIDGE.md - OpenClaw to SIQ Hermes

This profile does not execute OpenClaw workspace scripts directly. The coordinator's orchestration capability is carried by SIQ Deal OS APIs and services, with OpenClaw script names kept only as migration references.

## Primary Runtime Entry

Use Deal OS workflow endpoints:

| OpenClaw capability | SIQ Hermes / Deal OS entry |
|---|---|
| `coordinator_workflow.py`, `full_auto_workflow.py` | `POST /api/deals/{deal_id}/workflow/advance-next` |
| `r1_serial_dispatcher.py` | `POST /api/deals/{deal_id}/workflow/run-r1-serial` |
| single R1 expert task | `POST /api/deals/{deal_id}/workflow/run-r1-agent` |
| agent task construction | `GET /api/deals/{deal_id}/agents/{profile_id}/task-payload?round_name=R1` |
| startup retrieval | `POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval` |
| R1.5 dispute identification | `POST /api/deals/{deal_id}/workflow/identify-disputes` |
| chairman ruling | `POST /api/deals/{deal_id}/workflow/disputes/{dispute_id}/ruling` |
| R2 revision | `POST /api/deals/{deal_id}/workflow/run-r2` |
| R3 red-blue review | `POST /api/deals/{deal_id}/workflow/run-r3` |
| R4 final decision | `POST /api/deals/{deal_id}/workflow/finalize-r4` |

Default to dry-run payloads unless the caller explicitly enables execution. R1 Hermes execution can call live Hermes only through the backend runtime with audit events.

## Service Map

- `apps/api/services/ic_agent_runtime.py`: R1 task contracts, R1 serial execution, R2/R3/R4 deterministic workflow actions, phase advancement.
- `apps/api/services/ic_startup_retrieval.py`: startup retrieval receipts and evidence-linkage gate.
- `apps/api/services/deal_retrieval.py`: dynamic query planning and role-aware local evidence ranking for IC agents.
- `apps/api/services/vector_retrieval.py`: optional Milvus vector retrieval adapter with explicit configuration and audit-safe normalized hits.
- `apps/api/services/rerank_provider.py`: optional OpenAI-compatible rerank adapter for platform-hosted reranker endpoints.
- `apps/api/services/external_research_clients.py`: opt-in Exa/Tavily/QCC wrappers with SIQ-managed credentials, timeouts, normalized source attribution, and redacted outputs.
- `apps/api/services/deal_disputes.py`: R1.5 dispute detection, ruling payloads, and dispute summary.
- `apps/api/services/deal_reports.py`: report indexes, R1/R2/R3/R4 contract summaries, artifact readers.
- `apps/api/services/deal_decision.py`: human confirmation and decision override handling.
- `apps/api/services/ic_policy.py`: canonical profile IDs, R1 order, thresholds, weights, and policy loading.
- `apps/api/services/deal_store.py`: safe deal package paths, audit logging, redaction, JSON writes.

## Source Trace

Detailed script-by-script status lives in:

```text
agents/hermes/profiles/siq_ic_shared/openclaw_script_migration_matrix.json
```

Status meanings:

- `migrated`: behavior exists in SIQ services or shared contracts.
- `wrap_required`: behavior is useful but must stay behind SIQ auth, evidence, credential, and audit boundaries.
- `planned`: behavior is in scope but should be implemented as SIQ-native service code, not copied into profile-local scripts.
- `reference_only`: keep as design reference.
- `do_not_migrate`: local runtime glue, caches, credentials, or obsolete one-off tooling.

## Do Not Execute Directly

Do not run these OpenClaw scripts from Hermes profiles:

- `milvus_mcp_server.py`
- `unified_hybrid_retriever.py`
- `dynamic_retrieval_engine.py`
- `qcc_client.py`
- `exa_client.py`
- `tavily_client.py`
- `qwen3_vl_reranker_http.py`

Their capabilities must be implemented through backend services so credentials, rate limits, retrieval receipts, source attribution, and audit logs stay controlled. Current SIQ service equivalents include `deal_retrieval.py`, `vector_retrieval.py`, `rerank_provider.py`, and `external_research_clients.py`.
