# R1A Independent Expert Research Task

## Owners

`siq_ic_strategist`, `siq_ic_sector_expert`, `siq_ic_finance_auditor`, and `siq_ic_legal_scanner`.

## Anti-Anchoring Rule

R1A receives the R0 fact package, current Evidence snapshot, and role scope. It must not receive other R1A conclusions. The risk controller and chairman operate in R1B after all independent reports are durable.

## Retrieval Receipt

The task must include and the report must retain:

- shared project Evidence collection and hit count;
- profile-private background collection and hit count;
- retrieval status plus degraded/block reasons;
- source-classified citations;
- Evidence snapshot hash.

Every citation must declare `source_class=project_evidence` or `source_class=background_knowledge`; an unlabeled citation is not eligible for a formal claim.

## Required Output

- Role-specific conclusion, recommendation, score, and confidence.
- Claim list with `verified|derived|assumed|contested|missing` status.
- Evidence IDs for project facts and separately labeled background references.
- Counter-evidence, open questions, diligence requests, and red flags.
- Conditions and monitoring indicators within the profile's authority.

## Discipline

- `insufficient_evidence` is a valid conclusion.
- A background benchmark may support methodology or a challenge hypothesis, never a verified issuer number.
- Finance must retain period, currency, unit, formula, and calculator trace.
- Legal must retain authority, severity, remediation, and closing-condition impact.
