# Application database migrations

Fresh local databases are provisioned from `SQLModel.metadata` during API
startup. Existing PostgreSQL databases must receive the numbered forward
migrations before the new application image is started.

For the runtime ownership changes, apply
`006_create_runtime_coordination_tables.sql` to `SIQ_APP_DATABASE_URL`. The
migration is additive and idempotent. Run it once from the deployment migration
job, then start the API; startup schema validation names this file if a runtime
coordination table is incomplete.

For iOS native capture, apply migrations `004`, `005`, `007`, and
`008_add_meeting_native_capture_epoch_manifest_digest.sql` in numeric order.
Migration 007 freezes per-batch sample coordinates and SHA-256 declarations so
offline uploads received after rollover/seal can be checked against the signed
canonical manifest rather than trusting a client digest string. Migration 008
is the additive PostgreSQL upgrade for installations that ran migration 004
before the epoch-level digest column existed; it is a no-op on fresh schemas.

Do not roll back by dropping these tables: they contain job, lease, and quota
audit state. A non-production rollback may set both job and IC lease backends to
`file`. Production remains fail-closed and requires PostgreSQL; schema defects
must be corrected with another forward migration.
