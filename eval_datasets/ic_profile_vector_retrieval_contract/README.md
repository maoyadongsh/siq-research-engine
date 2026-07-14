# IC Profile Vector Retrieval Contract

This dataset probes the existing Deal OS `vector_retrieval` business path. Each
case requires the logical profile collection to resolve to its versioned
physical Milvus collection and retrieve managed methodology with the exact
profile and project tag.

The nightly performance baseline reports Recall@K, MRR, and per-case latency
P95. The probe is optional for developer runs and fail-closed when
`--require-ic-vector-retrieval-probe` is supplied. It does not contain project
facts, runtime hits, embeddings, credentials, or Milvus dumps.
