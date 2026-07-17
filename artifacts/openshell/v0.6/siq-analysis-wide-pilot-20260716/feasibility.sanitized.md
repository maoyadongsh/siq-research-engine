# SIQ Hermes/OpenShell Wide Pilot Feasibility

- Result: `PASS`
- Mode: `NOT_PRODUCTION_WIDE_PILOT`
- Profile: `siq_analysis`
- OpenShell: `0.0.83`
- Gateway: `siq-openshell-dev`
- Candidate image: `siq/hermes-openshell-siq-analysis:638ffbda3bf47167f7fabfdd`
- Readiness effect: `none`

The current candidate image started a real Hermes gateway through OpenShell with
seven business mounts and five read-only OpenShell control mounts. The finalized
source remained read-only, while only one task-scoped analysis leaf and the
dedicated Hermes runtime state were writable.

Four configured providers were injected through OpenShell placeholders. A real
Tavily query succeeded. The protected `/v1/runs` contract completed one terminal
tool workflow, emitted six structured events, read the source, wrote the exact
pilot output, verified the source was unchanged, and removed the derived output.

Identity-verified stop removed the sandbox, service forward, runtime and deletion
snapshots, active state, ephemeral identity files, and output leaf. Host Hermes
remained unchanged, both strict host brokers remained healthy, and the final
sandbox inventory was empty.

This proves that OpenShell can run the current Hermes `siq_analysis` business
path without changing the default host runtime. It is auxiliary feasibility
evidence only. It does not replace formal multi-case A/B evaluation, quality
equivalence, the complete provider and fallback matrix, or human release review.
