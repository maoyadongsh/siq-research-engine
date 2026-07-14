# R0 Coordinator Readiness Task

## Owner

`siq_ic_master_coordinator`

## Required Inputs

- Deal identity and current Evidence snapshot hash.
- Active source IDs and capability restrictions.
- Material inventory and prospectus quality status.
- Seven-profile gateway, contract, shared collection, and private collection readiness.
- Requested diligence scope and human constraints.

## Required Behavior

1. Fail closed on identity conflict, cross-Deal Evidence, stale snapshot, or missing project Evidence.
2. Verify each profile has a configured private background collection and report its retrieval status.
3. Separate project Evidence gaps from missing background knowledge.
4. Define the R1A independent scope before any expert sees another expert's conclusions.
5. Produce actionable follow-ups with owner, due phase, required Evidence, and completion condition.

## Output

- `readiness`: `ready|needs_more_evidence|blocked`.
- `evidence_snapshot_hash`, active `source_ids`, and capability restrictions.
- Per-profile shared/private collection names, hit counts, degraded/block reasons.
- Material sufficiency by business, finance, legal, risk, sector, strategy, team, and terms.
- R1A task plan and explicit out-of-scope items.

## Gate

`needs_more_evidence` records a remediable evidence gap but does not advance the workflow. Missing source classification, stale identity, project Evidence, or private collection retrieval is blocking. Only `ready` advances to R1.
