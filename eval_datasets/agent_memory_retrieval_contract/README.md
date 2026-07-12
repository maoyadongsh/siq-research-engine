# Agent memory retrieval contract fixture

This fixture is the PR-safe/self-hosted contract dataset for SIQ agent-memory
Milvus retrieval probes.

It intentionally contains only profile-level expectations that can be satisfied
by seeding checked-in Hermes profile files:

- `siq_assistant`
- `siq_ic_legal_scanner`
- `siq_ic_chairman`

The release wrapper uses these cases by default for the nightly
`agent_memory_milvus_retrieval_latency` probe. When
`SIQ_AGENT_MEMORY_VECTOR_SEED=1` is enabled, the wrapper also seeds the same
three profiles by default before running the performance baseline.

Do not add runtime Milvus dumps, embedding caches, secrets, or real retrieval
hits to this directory.
