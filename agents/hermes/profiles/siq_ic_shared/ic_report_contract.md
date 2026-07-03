# SIQ IC Report Contract

## Required Committee Report Sections

Every final investment committee decision report must include:

1. Conclusion
2. Evidence sufficiency
3. Key verified facts
4. Key unverified assumptions
5. Core disagreements and chairman ruling
6. Investment conditions and post-investment monitoring metrics

## Required Expert Report Fields

Each expert profile report must include:

| Field | Requirement |
| --- | --- |
| `profile_id` | Canonical Hermes profile ID, for example `siq_ic_finance_auditor`. |
| `project_id` | Stable project or deal identifier. |
| `recommendation` | One of `pass`, `conditional_pass`, `review`, `reject`, or `insufficient_evidence`. |
| `score` | Numeric 0-100 score when the role has enough evidence to score. |
| `verified` | Evidence-backed facts with source references. |
| `assumed` | Assumptions that affect the conclusion. |
| `open_questions` | Unresolved diligence questions. |
| `risks` | Material risks owned by the role. |
| `conditions` | Preconditions, covenants, or follow-up items needed before investment. |

## Quality Gate

- State the evidence status before making a recommendation.
- Separate verified facts from assumptions.
- Do not convert missing data into negative findings unless the absence itself is material and cited.
- Any numeric conclusion must include calculation basis or source lineage.
- Any legal, finance, risk, or sector claim that can affect the decision must point to evidence or be marked as unresolved.
