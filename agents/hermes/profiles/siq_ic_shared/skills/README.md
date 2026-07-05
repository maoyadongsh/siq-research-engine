# SIQ IC Shared Skills

These skills are migrated from the OpenClaw workspace for the SIQ IC profiles. They are shared by all executable `siq_ic_*` Hermes profiles and synchronized into each runtime profile by `scripts/hermes/run_gateway.sh`.

## Included Skills

### batch_1_core_ic

- `ic-finance-auditor`
- `ic-memo`
- `venture-capital`
- `due-diligence-analyst`
- `due-diligence-dataroom`
- `deal-screening`
- `startup-tools`
- `term-sheet-analyzer`
- `tam-sam-som`
- `cap-table-manager`
- `unit-economics`
- `dcf-model`
- `3-statement-model`

### batch_2_research_valuation_pipeline

- `market-intelligence-claw`
- `competitive-analysis`
- `strategic-competitor-analysis`
- `thesis-tracker`
- `comps-analysis`
- `equity-valuation-framework`
- `financial-analyst`
- `teaser`
- `pitch-deck`
- `deal-sourcing`
- `deal-tracker`
- `risk-metrics-calculation`
- `portfolio-monitoring`

### batch_3_diligence_research_materials

- `company-investment-research`
- `dd-checklist`
- `dd-meeting-prep`
- `sector-overview`
- `mckinsey-research`
- `initiating-coverage`
- `cim-builder`
- `buyer-list`
- `value-creation-plan`
- `ai-readiness`
- `financial-analysis-agent`
- `merger-model`
- `returns-analysis`
- `return-rate-impact-calculator`

## Migration Rules

- Runtime state, hidden metadata folders, credentials, caches, and project outputs are not copied.
- OpenClaw-local scripts with credentials or old workspace paths are represented by SIQ services instead.
- Agent IDs and collection names in text assets are normalized to `siq_ic_*` and `siq_deal_shared`.
