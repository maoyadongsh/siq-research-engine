# SIQ Service Preflight

- Schema: `siq.openshell.service_preflight.v2`
- Decision: `GO`
- Probe: `tcp_connect_plus_read_only_http_get`
- Required transport reachable: `5 / 5`
- Required protocol available: `3 / 3`
- Security proofs present: `2 / 2`
- Blocking checks: `0`

| Port | Service | Transport | Protocol | Error |
|---:|---|---|---|---|
| 8004 | Qwen local fallback | no_go | not_run | `connection_refused` |
| 8006 | Gemma local fallback | no_go | not_run | `connection_refused` |
| 8007 | Nemotron image model | pass | pass | `` |
| 8013 | Embedding service | pass | pass | `` |
| 15432 | PostgreSQL market facts | pass | not_applicable | `` |
| 19530 | Milvus knowledge store | pass | not_applicable | `` |
| 18081 | SIQ API | pass | pass | `` |
| 18651 | Hermes host rollback runtime | pass | pass | `` |

This is a read-only pre-cutover gate; it does not start or stop services or models.
