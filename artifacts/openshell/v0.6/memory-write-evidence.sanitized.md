# Host Memory Write Evidence

- Decision: `GO`
- Logical Milvus alias: `siq_agent_memory_active`
- Physical collection digest: `2a4e3e9be46e889d1147148f6ec9687e0e273ea0880f5536ec66f52f68cd74b8`
- Agent groups: `primary_market`, `secondary_market`
- PostgreSQL: `insert / readback / rollback / post-rollback verify` passed
- Milvus: `upsert / get / search / delete / post-delete verify` passed
- PostgreSQL residual count: `0`
- Milvus residual count: `0`
- Sandbox direct memory writes: `disabled`

The evidence contains only operation outcomes, timestamps and SHA-256 bindings.
Record content, runtime identifiers, endpoints and credentials are excluded.
