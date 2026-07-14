# R1.5 Chairman Ruling Task

## Owner

`siq_ic_chairman`

## Inputs

- Structured dispute with positions, claim IDs, Evidence IDs, severity, and decision impact.
- Relevant R1A/R1B reports and current Evidence snapshot.
- New Evidence or explicit statement that no new Evidence exists.

## Required Output Per Dispute

- `accept_position|reject_position|conditional|insufficient_evidence` decision.
- Rationale that addresses each position rather than averaging them.
- Accepted and rejected claim IDs and Evidence IDs.
- Required follow-ups with owner and completion gate.
- `resolved` only when the evidence and decision logic truly close the issue.

## Blocking Rules

- High or critical unresolved disputes block an R4 `pass`.
- Evidence shortage returns the workflow to a durable follow-up task.
- Deterministic ruling drafts must carry fallback identity and require explicit human or model completion.
