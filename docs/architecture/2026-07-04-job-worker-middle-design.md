# 2026-07-04 Job / Worker Middle Design

Scope: follow-up to `2026-07-03-architecture-optimization-plan-v2.md` P2. This note is design-only and deliberately avoids changing job execution, thread ownership, subprocess ownership, or route response schemas.

## Current Job Owners

### FileBackedJobService

Owner:

- `apps/api/services/job_service.py`
- Used by Market reports queueing via `market_report_job_service`.

Shape:

- `job_id`
- `kind`
- `status`
- `created_at`
- `started_at`
- `finished_at`
- `created_by`
- `result`
- `error`

Execution model:

- `start()` stores a queued snapshot, then immediately starts a daemon thread.
- Target runs in the API process.
- Result is JSON-safe serialized before persistence.
- Store is a local JSON file and is resilient to malformed payloads / persist failures.

Known limits:

- No reliable cancellation.
- No retry policy.
- No multi-process locking.
- No step timeline.
- No log tail.
- No restart resume for running jobs.

### Workflow Job Service

Owner:

- `apps/api/services/workflow_job_service.py`
- Used by `apps/api/routers/workflow.py` for legacy `/task/{task_id}/run-remaining`.

Shape:

- `jobId`
- `taskId`
- `status`
- `steps`
- `createdAt`
- `updatedAt`

Step shape:

- `step`
- `status`
- `startedAt`
- optional `finishedAt`
- optional `result`
- optional `message`

Execution model:

- Router creates the job under `_job_lock`.
- Router starts a daemon thread that calls `_run_remaining_pipeline()`.
- Pipeline records step status and persists after each mutation.
- Existing failure behavior preserves completed steps and may leave the failing step as `running`; this is a current contract, not a cleanup opportunity.

Known limits:

- CamelCase schema is route-facing legacy contract.
- No `startedAt` / `finishedAt` at job level.
- No `createdBy`.
- No cancellation / retry.
- No shared job backend with Market reports.
- API process owns execution and persistence.

## Design Decision

Do not merge these schemas in-place.

For the next stage, introduce a canonical internal job envelope and adapters, while preserving current public response schemas:

```text
CanonicalJobV1
  id
  kind
  subject
  status
  created_at
  started_at
  finished_at
  updated_at
  created_by
  result
  error
  steps[]
  logs[]
  attempts
```

Adapter metadata used by the first implementation:

- `schema_version`: currently `siq_job_envelope_v1`.
- `source_schema`: records the original owner schema, such as `market_file_backed_job_v1` or `workflow_job_v1`.
- `legacy_payload`: preserves the existing route-facing payload for lossless public projection.

Adapter rules:

- Market reports routes keep returning current snake_case fields until a route contract explicitly changes.
- Workflow routes keep returning current camelCase fields until a route contract explicitly changes.
- New code may write canonical job envelopes internally, but route adapters must project back to existing shapes.
- No existing job store should be rewritten in place without a migration reader that accepts old and new payloads.

## Worker Runtime Recommendation

Short term:

- Keep `FileBackedJobService` for Market reports.
- Keep `_workflow_jobs` for legacy run-remaining.
- Add contract tests before each migration.

Middle term:

- Prefer a single local worker process with a durable SQLite-backed queue before Redis/RQ/Arq/Celery.
- SQLite is already acceptable for local deployment, easier to back up, and avoids adding broker operations during architecture cleanup.
- A broker-backed queue can come later if there is a clear multi-host requirement.

Long term:

- Move to Redis/RQ or Arq only when deployment needs multi-process / multi-host workers, cancellation, retries, and log streaming under concurrent load.
- Celery is likely too heavy for the current local-first architecture unless scheduled jobs and distributed workers become product requirements.

## First Contract Tests

Before any migration, add tests for a canonical adapter layer. The first implementation lives in `apps/api/services/job_envelope.py` with focused tests in `apps/api/tests/test_job_envelope.py`.

1. Snake-case Market job to canonical:
   - preserves `job_id`, `kind`, `created_by`, `result`, `error`
   - maps `created_at`, `started_at`, `finished_at`
   - produces empty `steps` and `logs`

2. CamelCase workflow job to canonical:
   - preserves `jobId`, `taskId`, `status`
   - maps `createdAt` / `updatedAt`
   - maps `steps[].startedAt` / `finishedAt`
   - preserves existing failing-step-as-running behavior

3. Canonical to Market public payload:
   - keeps snake_case response fields
   - does not expose internal `logs` unless route contract adds it

4. Canonical to Workflow public payload:
   - keeps camelCase response fields
   - preserves `steps` exactly enough for existing UI/tests
   - projects canonical-native steps even when no legacy step payload exists

5. Store migration reader:
   - accepts legacy list payload
   - accepts `{jobs: [...]}` payload
   - accepts canonical-native envelope payloads
   - ignores malformed jobs
   - pairs with existing `FileBackedJobService` persistence-failure tests so runtime snapshots are not blocked by store write failures

Owner split note: track this with the Shared Job And Command Contracts group in `2026-07-04-worktree-owner-split-audit.md`. Adding adapter contracts does not authorize changing any job store, route schema, worker loop, or subprocess owner in the same window.

## First Low-Risk Migration Candidate

Best first candidate: Market ingestion eval queued job.

Why:

- It already uses `FileBackedJobService`.
- The queued route has a `wait=true` inline path for comparison.
- Existing tests already fake `run_command` / queue start.
- It has no user-facing step timeline today, so a canonical internal envelope can be introduced behind the same public payload.

2026-07-04 implementation note:

- `market_report_queueing.py` now uses `job_envelope` as an internal adapter for `market-ingestion-eval` queue snapshots and job status reads.
- Public API payloads still project back to the existing Market snake_case shape.
- `FileBackedJobService` persistence, thread lifecycle, route schemas, `wait=true` inline execution, and command execution remain unchanged.

Do not start with:

- Workflow `_run_remaining_pipeline()`: step semantics and legacy UI contract are riskier.
- PDF parser queue worker: queue claim / stale recovery / MinerU lifecycle are separate high-risk owners.
- Deal / IC jobs: outside the v2 optimization scope.

## Acceptance For First Migration

Minimum gate:

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_job_envelope.py \
  tests/test_job_service.py \
  tests/test_workflow_job_service.py \
  tests/test_market_report_queueing_service.py \
  tests/test_market_report_queueing.py \
  tests/test_market_reports_proxy.py
```

Global guard:

```bash
git diff --check
```
