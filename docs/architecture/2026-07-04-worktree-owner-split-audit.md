# 2026-07-04 Worktree Owner Split Audit

Scope: follow-up to `2026-07-03-architecture-optimization-plan-v2.md`.

Purpose: stop expanding active work, split the current worktree by owner, and verify each owner with focused gates before any commit or merge. This audit deliberately keeps Deal / IC / OpenClaw work out of the v2 architecture optimization mainline.

## Owner Groups

### V2 Execution Record

Files:

- `docs/architecture/2026-07-03-architecture-optimization-plan-v2.md`
- `docs/architecture/2026-07-04-worktree-owner-split-audit.md`

Recommended commit:

- `docs: update v2 execution record and owner audit`

Gate:

- `git diff --check -- docs/architecture/2026-07-03-architecture-optimization-plan-v2.md`
- `git diff --check -- docs/architecture/2026-07-03-architecture-optimization-plan-v2.md docs/architecture/2026-07-04-worktree-owner-split-audit.md`

Verified:

- 2026-07-04: `git diff --check` passed.

### Market Reports API

Files:

- `apps/api/services/market_package_repository.py`
- `apps/api/services/market_report_status_service.py`
- `apps/api/tests/test_market_package_repository.py`
- `apps/api/tests/test_market_report_commands.py`
- `apps/api/tests/test_market_report_proxy_service.py`
- `apps/api/tests/test_market_report_queueing.py`
- `apps/api/tests/test_market_report_queueing_service.py`
- `apps/api/tests/test_market_report_settings.py`
- `apps/api/tests/test_market_report_status_service.py`
- `apps/api/tests/test_market_reports_proxy.py`

Recommended commit:

- `api: tighten market report package and status contracts`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_market_package_repository.py \
  tests/test_market_report_commands.py \
  tests/test_market_report_queueing_service.py \
  tests/test_market_report_queueing.py \
  tests/test_market_report_settings.py \
  tests/test_market_report_status_service.py \
  tests/test_market_report_proxy_service.py \
  tests/test_market_reports_proxy.py \
  tests/test_job_service.py
```

Verified:

- 2026-07-04: focused Market Reports API gate above passed, 150 passed, 2 existing Pydantic deprecation warnings.
- 2026-07-04: follow-up router parser_result-missing contract kept this owner green, 151 passed, 2 existing Pydantic deprecation warnings.

### Agent Runtime Contracts

Files:

- `apps/api/tests/test_agent_runtime_statement_context.py`
- `apps/api/tests/test_agent_runtime_loop_guard.py`

Recommended commit:

- `api: add agent runtime contract coverage`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_agent_runtime_statement_context.py \
  tests/test_agent_runtime_loop_guard.py \
  tests/test_agent_chat_runtime_loops.py
```

Verified:

- 2026-07-04: focused Agent Runtime Contracts gate above passed, 71 passed, existing utcnow warnings.

### Source Access Contracts

Files:

- `apps/api/tests/test_source_access.py`

Recommended commit:

- `api: harden source access viewer contracts`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_source_access.py
```

Verified:

- 2026-07-04: combined Source/Workflow/Shared gate passed, 57 passed, existing Pydantic/utcnow warnings.

### Workflow Contracts

Files:

- `apps/api/tests/test_workflow_subprocess_contracts.py`

Recommended commit:

- `workflow: add semantic subprocess contract coverage`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_workflow_subprocess_contracts.py \
  tests/test_command_runner.py
```

Verified:

- 2026-07-04: combined Source/Workflow/Shared gate passed, 57 passed, existing Pydantic/utcnow warnings.

### Shared Job And Command Contracts

Files:

- `apps/api/tests/test_command_runner.py`
- `apps/api/tests/test_job_service.py`
- `apps/api/tests/test_workflow_job_service.py`

Recommended commit:

- `api: add shared job and command contract coverage`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_command_runner.py \
  tests/test_job_service.py \
  tests/test_workflow_job_service.py
```

Verified:

- 2026-07-04: covered by Market Reports API gate for `test_job_service.py` and combined Source/Workflow/Shared gate for `test_command_runner.py` and `test_workflow_job_service.py`; both gates passed.

### Document Parser

Files:

- `apps/document-parser/app.py`
- `apps/document-parser/table_relations_payload.py`
- `apps/document-parser/tests/test_document_parser_app.py`
- `apps/document-parser/tests/test_table_relations_payload.py`

Recommended commit:

- `document-parser: extract table relations payload`

Focused gate:

```bash
cd apps/document-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tests/test_table_relations_payload.py \
  tests/test_document_parser_app.py
```

Verified:

- 2026-07-04: focused Document Parser gate above passed, 19 passed.
- 2026-07-04: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider` passed, 49 passed.

### Web Market Helpers

Files:

- `apps/web/src/pages/MarketParsingPage.tsx`
- `apps/web/src/features/market-parsing/uploadFiles.ts`
- `apps/web/src/features/market-parsing/uploadFiles.test.ts`
- `apps/web/src/pages/SearchDownload.tsx`
- `apps/web/src/features/search-download/curatedAnnuals.ts`
- `apps/web/src/features/search-download/curatedAnnuals.test.ts`

Recommended commit:

- `web: extract market parsing and curated annual helpers`

Focused gate:

```bash
cd apps/web
npm run test:unit -- uploadFiles curatedAnnuals
npm run build
```

Verified:

- 2026-07-04: `npm run test:unit -- uploadFiles curatedAnnuals settings/utils workflowViewModel` passed, 101 passed.
- 2026-07-04: `npm run build` passed.

### Web Settings

Files:

- `apps/web/src/pages/Settings.tsx`
- `apps/web/src/pages/settings/utils.ts`
- `apps/web/src/pages/settings/utils.test.ts`

Recommended commit:

- `web: extract settings service count helper`

Focused gate:

```bash
cd apps/web
npm run test:unit -- settings/utils
npm run build
```

Verified:

- 2026-07-04: `npm run test:unit -- uploadFiles curatedAnnuals settings/utils workflowViewModel` passed, 101 passed.
- 2026-07-04: `npm run build` passed.

### Hermes Smoke

Files:

- `agents/hermes/README.md`
- `scripts/README.md`
- `scripts/hermes/smoke_r1_agent_workflow.py`
- `apps/api/tests/test_hermes_smoke_scripts.py`

Recommended commit:

- `hermes: harden R1 smoke workflow script`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hermes_smoke_scripts.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile ../../scripts/hermes/smoke_r1_agent_workflow.py
```

Verified:

- 2026-07-04: focused Hermes smoke gate above passed, 3 passed; `py_compile` passed.

### Deal / IC / OpenClaw Parallel Work

Files:

- `apps/api/routers/deals.py`
- `apps/api/services/deal_audit.py`
- `apps/api/services/deal_decision.py`
- `apps/api/services/deal_status.py`
- `apps/api/services/ic_agent_runtime.py`
- `apps/api/services/ic_openclaw_importer.py`
- `apps/api/tests/test_deal_store.py`
- `apps/api/tests/test_deals_router.py`
- `apps/web/src/lib/dealApi.ts`
- `apps/web/src/lib/dealApi.test.ts`
- `apps/web/src/lib/dealTypes.ts`
- `apps/web/src/pages/DealWorkflow.tsx`
- `apps/web/src/features/deals/workflowViewModel.ts`
- `apps/web/src/features/deals/workflowViewModel.test.ts`
- `apps/web/e2e/support/mockApi.ts`
- `apps/web/e2e/tests/deals-workflow.spec.ts`
- `docs/architecture/2026-06-28-primary-market-openclaw-compat-design.md`
- `agents/hermes/profiles/siq_ic_finance_auditor/config.yaml`
- `agents/hermes/profiles/siq_ic_legal_scanner/config.yaml`
- `agents/hermes/profiles/siq_ic_master_coordinator/config.yaml`
- `agents/hermes/profiles/siq_ic_risk_controller/config.yaml`
- `agents/hermes/profiles/siq_ic_sector_expert/config.yaml`
- `agents/hermes/profiles/siq_ic_strategist/config.yaml`

Recommended commit:

- `deal/ic: update OpenClaw workflow contracts`

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_deal_store.py \
  tests/test_deals_router.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_hermes_model_control.py \
  tests/test_hermes_ic_profiles.py
cd ../web
npm run test:unit -- workflowViewModel
npm run e2e -- e2e/tests/deals-workflow.spec.ts
npm run build
```

Verified:

- 2026-07-04: Deal API gate above passed, 81 passed, existing Pydantic/utcnow warnings.
- 2026-07-04: web unit focused gate passed, 101 passed.
- 2026-07-04: `npm run e2e -- e2e/tests/deals-workflow.spec.ts` passed, 1 passed.
- 2026-07-04: `npm run build` passed.

This group is explicitly outside the v2 optimization mainline and must not be mixed into the v2 owner commits.

## Commit Order

1. V2 execution record.
2. Market Reports API.
3. Agent Runtime Contracts.
4. Source Access Contracts.
5. Workflow Contracts.
6. Document Parser.
7. Web Market Helpers.
8. Web Settings.
9. Hermes Smoke.
10. Deal / IC / OpenClaw parallel work.

## Global Gates

Run after all focused gates:

```bash
git diff --check
git status --short
```

Higher-level merge candidate gates:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider

cd ../web
npm run test:unit
npm run build
npm run e2e -- e2e/tests/deals-workflow.spec.ts

cd ../document-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider

cd ../pdf-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider

cd ../../services/market-report-finder
uv run python -m pytest -q

cd ../market-report-rules
uv run pytest -q

cd ../../packages/market-contracts
uv run pytest -q
```

Verified:

- 2026-07-04: API full gate passed, 867 passed, existing Pydantic/utcnow warnings.
- 2026-07-04: Web full unit passed, 101 passed; web build passed; Deal workflow e2e passed, 1 passed.
- 2026-07-04: Document parser full gate passed, 49 passed.
- 2026-07-04: PDF parser full gate passed, 343 passed.
- 2026-07-04: Market report finder full gate passed, 46 passed.
- 2026-07-04: Market report rules full gate passed, 29 passed, existing Starlette deprecation warning.
- 2026-07-04: Market contracts passed, 2 passed.
- 2026-07-04: `git diff --check` passed.

For a final merge candidate, also run the broader owner gates from `2026-07-03-architecture-optimization-plan-v2.md`.
