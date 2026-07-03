# SIQ IC Evidence Contract

## Evidence Classes

| Class | Meaning |
| --- | --- |
| `verified` | Directly supported by primary materials, database records, regulatory filings, contracts, financial statements, or cited diligence notes. |
| `derived` | Calculated or inferred from verified evidence, with method disclosed. |
| `assumed` | Plausible but not yet verified; must not be presented as fact. |
| `contested` | Conflicting evidence or role disagreement exists. |
| `missing` | Required evidence has not been obtained. |

## Minimum Citation Fields

Every material evidence item should preserve:

| Field | Requirement |
| --- | --- |
| `source_id` | Stable document, database, or artifact identifier. |
| `source_type` | Examples: `filing`, `contract`, `interview_note`, `financial_model`, `database`, `web`, `expert_report`. |
| `locator` | Page, table, section, row id, URL, or file path. |
| `claim` | The exact claim supported by the source. |
| `confidence` | `high`, `medium`, or `low`. |
| `retrieved_at` | ISO date or timestamp when evidence was gathered. |

## Gates

- Final committee recommendations require evidence across business, finance, legal, and risk dimensions.
- Unresolved disputes must be listed in the final report.
- Evidence packages must distinguish source text from agent interpretation.
- Expert profiles may refuse to score when the evidence package is below threshold.
