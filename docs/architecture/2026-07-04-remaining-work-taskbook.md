# 2026-07-04 Remaining Work Taskbook

Scope: deep follow-up audit of the current `dev/sunbo` worktree after the owner split audit and Milvus / Vector Ingest runtime check.

Purpose: make the remaining work explicit before any more feature expansion. This taskbook separates current worktree hygiene from productization work so we can commit, merge, and continue without smuggling unfinished systems into the same owner.

## 0. Audit Snapshot

Observed on 2026-07-04:

- Current branch: `dev/sunbo`.
- Dirty tracked files: 27.
- Untracked files: `AGENTS.md`, `docs/architecture/2026-07-04-primary-market-deal-os-v2-redesign.md`.
- Live Milvus probe: `default` DB contains current physical collections `ic_archive_sop`, `ic_chairman`, `ic_collaboration_shared`, `ic_finance_auditor`, `ic_legal_scanner`, `ic_master_coordinator`, `ic_risk_controller`, `ic_sector_expert`, and `ic_strategist`; no `_ws` collections were found.
- Vector ingest runtime now defaults to `ic_collaboration_shared`; `siq_*` names are logical aliases only in runtime compatibility code.

## 1. Current Worktree Tasks

### T0.1 Owner Split Cleanup

Status: not done.

Problem:

- `AGENTS.md` is untracked and not represented in `2026-07-04-worktree-owner-split-audit.md`.
- `docs/architecture/2026-07-04-primary-market-deal-os-v2-redesign.md` is untracked and not assigned to an owner.
- The owner audit now covers Vector Ingest, but it still mixes active audit edits with the new Deal OS V2 design unless we explicitly split them.

Deliverables:

- Add an owner section for repository contribution guidelines if `AGENTS.md` is intended to be committed.
- Add an owner section for Deal OS V2 redesign, or explicitly mark it as a draft outside the current commit batch.
- Decide commit order for `AGENTS.md` and `2026-07-04-primary-market-deal-os-v2-redesign.md`.

Focused gate:

```bash
git diff --check -- AGENTS.md docs/architecture/2026-07-04-primary-market-deal-os-v2-redesign.md docs/architecture/2026-07-04-worktree-owner-split-audit.md
git status --short
```

### T0.2 Commit Split And Final Verification

Status: not done.

Problem:

- Focused owner gates have been recorded, but no commit split has happened.
- New Vector Ingest changes were added after earlier global gates; the full global gate should be rerun before a merge candidate.

Deliverables:

- Split commits by owner as listed in `2026-07-04-worktree-owner-split-audit.md`.
- Re-run global gates after the final split, not just focused gates.

Suggested commit order:

1. V2 execution record docs.
2. Shared job / Market reports canonical adapter work.
3. Hermes smoke script follow-up.
4. Milvus / Vector Ingest runtime compatibility.
5. Deal / IC / OpenClaw parallel work.
6. Repository guidelines and Deal OS V2 design docs, if accepted.

Merge-candidate gate:

```bash
git diff --check
cd apps/api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
cd ../web && npm run test:unit && npm run build && npm run e2e -- e2e/tests/deals-workflow.spec.ts
cd ../document-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
cd ../pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
cd ../../services/market-report-finder && uv run python -m pytest -q
cd ../market-report-rules && uv run pytest -q
cd ../../packages/market-contracts && uv run pytest -q
```

## 2. Deal OS V2 Productization

### P0.1 Milvus Collection Naming Decision

Status: not done.

Problem:

- `apps/api/services/deal_evidence.py` still plans Deal evidence chunks for `siq_deal_shared`.
- Live Milvus currently has `ic_collaboration_shared`, not `siq_deal_shared`.
- Vector ingest runtime now maps `siq_deal_shared` to `ic_collaboration_shared`, but Deal evidence dry-run output still advertises `siq_deal_shared`.

Decision required:

- Option A: create and migrate a real `siq_deal_shared` collection, then update Vector Ingest defaults after migration.
- Option B: keep `ic_collaboration_shared` as the current physical shared evidence collection and make Deal evidence dry-run report that physical target.
- Option C: support a logical collection field and a physical collection field in the ingest plan.

Deliverables:

- Update `deal_evidence.py` and tests to reflect the chosen logical/physical contract.
- Add a live Milvus smoke that fails if the configured physical collection is missing.

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_deal_store.py tests/test_deals_router.py
cd ../../scripts/vector-index/milvus-ingestion
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from runtime_compat import normalize_collection_name
assert normalize_collection_name("siq_deal_shared") in {"ic_collaboration_shared", "siq_deal_shared"}
PY
```

### P0.2 Demo Package Repair And Seed

Status: not done.

Problem:

- Deal OS V2 design says the YUSHU demo must be reproducible, but `data/wiki/` is ignored and no `scripts/deals/seed_yushu_demo.py` or `fixtures/deals/` path exists.
- Package repair for R4 contract, manifest hash, audit mismatch, accepted missing files, and policy version is still design-only.

Deliverables:

- Add `scripts/deals/repair_deal_package.py`.
- Add either `scripts/deals/seed_yushu_demo.py` or a sanitized fixture under `fixtures/deals/`.
- Add tests that generate `DEAL-YUSHU-2026-001` from scratch in a temp wiki root.

Focused gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_deal_store.py tests/test_deals_router.py
```

### P0.3 Canonical API And Mode Separation

Status: partially done.

Done:

- `workflow/advance-next` exists.
- R1 serial dry-run exists.
- Deterministic R2/R3/R4 actions exist.

Not done:

- `mode=preview|deterministic|model` is not a first-class API contract.
- Workflow actions still mostly expose older route-specific semantics such as `dry_run`.
- There is no action registry that frontends can use to drive labels, blocking reasons, and write/model risk.

Deliverables:

- Add an action registry in the API service layer.
- Add `mode` to action preview/execute payloads while preserving `dry_run` aliases.
- Update Web workflow buttons to use action metadata rather than page-local assumptions.

### P0.4 Real Hermes Smoke Matrix

Status: not done.

Done:

- Dry-run matrix and serial dry-run smoke exist.
- Focused smoke tests cover synthetic package planning.

Not done:

- Real gateway/model smoke is still not complete for sector, legal, risk, chairman, and R1 serial real.
- Gateway health is not the same as upstream model success.

Deliverables:

- Extend `scripts/hermes/smoke_r1_agent_workflow.py` with a real serial smoke mode guarded by explicit flags.
- Record a profile matrix with `gateway_health`, `model_call`, `contract_valid`, and `report_written`.
- Add docs for expected local env and token alignment.

Gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hermes_smoke_scripts.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile ../../scripts/hermes/smoke_r1_agent_workflow.py
```

### P1.1 Evidence Service Real Indexing

Status: not done.

Problem:

- `deal_evidence.py` explicitly remains local and deterministic.
- `postgres_written` and `milvus_written` are false.
- `write_readiness` can warn/pass, but no real PostgreSQL or Milvus write path exists.

Deliverables:

- Create minimal PostgreSQL `deal_os` schema and migration.
- Implement real evidence indexing with idempotent writes.
- Add failure-safe behavior: package archive remains authoritative, indexing failures block model run but do not corrupt package files.
- Expose `GET /api/deals/{deal_id}/evidence/readiness`.

Gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_deal_store.py tests/test_deals_router.py
```

### P1.2 Retrieval Receipt V2

Status: not done.

Problem:

- Startup retrieval is still file-backed and deterministic.
- Receipts do not yet preserve shared/private vector hit provenance, embedding/index version, query intent, source path, score, and receipt hash as a replayable retrieval proof.

Deliverables:

- Add `siq_ic_startup_receipt_v2`.
- Generate receipts from shared + private hybrid retrieval.
- Enforce receipt hash on agent report write.
- Make reports fail closed when referenced evidence IDs were not in the startup receipt.

### P2.1 Phase Engine And Locks

Status: not done.

Problem:

- Workflow logic is still concentrated in `ic_agent_runtime.py`.
- There is no shared phase engine with locks, temp artifacts, retry, cancellation, or recovery.
- `advance-next` is useful, but it is not yet a durable phase orchestration layer.

Deliverables:

- Add a Phase Engine service with `preview`, `execute`, `commit`, `audit`, and `recover` boundaries.
- Add phase locks for write/model actions.
- Write model outputs to temp paths and commit only after contract validation.

### P2.2 Model Augmentation Beyond R1

Status: not done.

Problem:

- R2/R3/R4 remain deterministic by default.
- R1.5 chairman ruling, R2 per-agent revision, R3 challenge, and R4 model drafting are design-only.

Deliverables:

- Keep deterministic core as fallback.
- Add optional model augmentation per phase.
- Keep scoring and human confirmation controlled by the service layer.

### P2.3 Web Deal OS Productization

Status: partially done.

Done:

- Deal pages exist for workspace, data room, evidence, workflow, agents, reports, decision, and audit.
- Workflow page distinguishes several dry-run/write paths.

Not done:

- Evidence page is not a full indexing/readiness dashboard.
- Agents page does not yet combine gateway health, model readiness, receipt hash, and report contract state.
- Model run actions still need explicit cost/time/health confirmation.
- Real API Playwright E2E is missing; current Deal E2E is mock-based.

Deliverables:

- Upgrade Evidence page to readiness dashboard.
- Add gateway/model health to Agents and Workflow.
- Add true API-backed Playwright E2E using a seeded demo package.

## 3. Job / Worker Backlog

### J1 Canonical Job Store Migration

Status: not done.

Done:

- `job_envelope.py` canonical adapter contracts exist.
- `market-ingestion-eval` queue snapshot/status projection uses the adapter.

Not done:

- FileBackedJobService persistence schema is not migrated.
- Workflow `_workflow_jobs` is not migrated.
- Worker loop, cancellation, retry, log tail, restart recovery, and subprocess ownership are not implemented.
- Route schemas still deliberately project legacy public payloads.

Next step:

- Keep this paused until the current owner split is committed.
- If resumed, migrate only one low-risk job class and preserve existing public payload contracts.

## 4. Broader Backlog From Existing Taskbooks

These are not part of the current owner batch, but they remain open:

- Multi-market ingestion: US 20-F/IFRS sample, real DB imports, SQL evidence traceability, JP/KR real packages, Milvus multi-market rebuild and DB evidence reverse lookup.
- Frontend UI audit: multi-viewport Playwright/screenshot verification remains incomplete for many pages beyond the first workspace pass.
- General document parsing P1/P2: PostgreSQL `document_parser`, Milvus `siq_documents`, and retrieval integration are still future work.

Do not pull these into the current dirty worktree unless a new owner window is opened.

## 5. Recommended Next Actions

1. Decide and document the `siq_deal_shared` vs `ic_collaboration_shared` physical collection contract.
2. Add owner audit entries for `AGENTS.md` and `2026-07-04-primary-market-deal-os-v2-redesign.md`.
3. Commit split the current worktree by owner.
4. Implement demo seed/repair before expanding Deal OS UI or model workflow.
5. Build the minimal `deal_os` indexing schema and real evidence ingest path.
6. Add Retrieval Receipt V2 only after physical collection naming is settled.
7. Add real API Playwright E2E once the seeded demo is deterministic.

