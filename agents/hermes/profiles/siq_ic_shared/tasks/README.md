# SIQ IC Phase Task Templates

These templates define the production behavior expected from Hermes IC profiles. The API orchestrator owns task delivery, leases, durable handoffs, retries, and artifact writes. Profiles do not communicate directly with other gateways.

## Source Classes

- `project_evidence`: Deal-scoped facts from `siq_deal_shared` / `ic_collaboration_shared`, with Evidence IDs and source coordinates.
- `background_knowledge`: role-specific methods, benchmarks, cases, and challenge hypotheses from the profile's private Milvus collection.

Background knowledge cannot verify a project fact. A formal claim about the issuer must cite project Evidence. Every task must report shared and private retrieval status separately.

## Templates

- `R0_COORDINATOR_READINESS.md`: identity, material, snapshot, retrieval, and scope gate.
- `R1_INDEPENDENT_RESEARCH.md`: independent specialist analysis before cross-agent anchoring.
- `R1_CROSS_VALIDATION.md`: risk stress test and chairman initial synthesis over R1A handoffs.
- `R1_5_CHAIRMAN_RULING.md`: dispute ruling and evidence follow-up.
- `R2_EXPERT_REVISION.md`: evidence-based viewpoint and score revision.
- `R3_RED_BLUE_DEBATE.md`: red argument, blue answer, rebuttal, and chairman verdict.
- `R4_CHAIRMAN_DECISION.md`: structured final decision and six-dimension scoring.
- `DETERMINISTIC_FALLBACK.md`: mandatory identity for non-model recovery artifacts.

`quality_accepted` is never inferred from template presence. Acceptance requires a named golden case, automated checks, and human methodology approval.
