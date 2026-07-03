# SIQ IC Shared Profile Assets

`siq_ic_shared` stores the shared contracts, workflow policy, role matrix, and templates for the SIQ primary-market investment committee profiles.

This directory is not a runnable agent profile. Runtime profiles should reference these files when coordinating investment committee workflows, producing expert reports, or validating evidence.

## Files

| File | Purpose |
| --- | --- |
| `ic_workflow_policy.json` | Investment committee phases, role weights, gates, and scoring policy migrated from OpenClaw. |
| `ic_profile_matrix.json` | Hermes profile IDs, ports, role labels, and source workspace mapping. |
| `ic_report_contract.md` | Required structure and quality gate for committee and expert reports. |
| `ic_evidence_contract.md` | Evidence classification, citation, verification, and dispute handling rules. |
| `ic_prompt_contract.md` | Prompt composition and role-boundary rules shared by all IC profiles. |
| `openclaw_script_migration_matrix.json` | Machine-readable migration status for OpenClaw workspace scripts and their SIQ owners. |
| `templates/` | Placeholder directory for future IC report and expert submission templates. |

## Scope

- Use `siq_ic_*` as the canonical Hermes profile IDs.
- Use `data/wiki/deals` as the canonical SIQ project execution artifact root.
- Do not store sessions, memory, vector stores, `.venv`, or OpenClaw runtime state here.
- Keep project execution artifacts outside this directory unless they are stable templates or contracts.
