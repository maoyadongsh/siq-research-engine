# R4 Chairman Structured Decision Task

## Owner

`siq_ic_chairman`, followed by coordinator assembly, factcheck, contract validation, and human confirmation.

## Inputs

- Current Evidence snapshot, active source IDs, and capability restrictions.
- Current R2 specialist reports and score deltas.
- R1.5 rulings and unresolved follow-ups.
- R3 topics, turns, verdicts, and residual risks.
- Quality findings and scoring policy.

## Required Decision

- `support|conditional_support|review|reject|insufficient_evidence`.
- Six-dimension R4 scores with Evidence and change rationale from earlier rounds.
- Critical claims, counter-evidence, unresolved uncertainty, and veto items.
- Executable conditions, owners, deadlines, leading indicators, and trigger thresholds.
- Explicit effect of R2/R3 on the final conclusion and score.
- Generation mode and all report/receipt/snapshot identities.

## Quality Gate

Factchecker findings never silently modify the chairman draft. Repairs create a new revision and are revalidated. Critical unsupported claims, numeric trace failures, unresolved high disputes, placeholders, or missing report sections block human confirmation.
