-- PostgreSQL runtime-coordination authority for T05/T06.
--
-- This migration is additive and safe to re-run.  Operational rollback means
-- disabling the PostgreSQL job/IC backends in a non-production profile, while keeping
-- these tables as audit evidence.  Production forward fixes must remain
-- additive and must not drop active leases or reservation history.

CREATE TABLE IF NOT EXISTS active_run_leases (
    id SERIAL PRIMARY KEY,
    profile VARCHAR(80) NOT NULL,
    session_id VARCHAR(255) NOT NULL,
    run_id VARCHAR(255) NOT NULL,
    owner_id VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    lease_until TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_active_run_profile_session UNIQUE (profile, session_id),
    CONSTRAINT uq_active_run_run_id UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS ix_active_run_leases_profile ON active_run_leases(profile);
CREATE INDEX IF NOT EXISTS ix_active_run_leases_session_id ON active_run_leases(session_id);
CREATE INDEX IF NOT EXISTS ix_active_run_leases_run_id ON active_run_leases(run_id);
CREATE INDEX IF NOT EXISTS ix_active_run_leases_status ON active_run_leases(status);
CREATE INDEX IF NOT EXISTS ix_active_run_leases_lease_until ON active_run_leases(lease_until);

CREATE TABLE IF NOT EXISTS quota_ledgers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    event_date VARCHAR(10) NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    reserved_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_quota_ledger_user_event_day UNIQUE (user_id, event_type, event_date),
    CONSTRAINT ck_quota_ledger_nonnegative CHECK (used_count >= 0 AND reserved_count >= 0)
);

CREATE INDEX IF NOT EXISTS ix_quota_ledgers_user_id ON quota_ledgers(user_id);
CREATE INDEX IF NOT EXISTS ix_quota_ledgers_event_type ON quota_ledgers(event_type);
CREATE INDEX IF NOT EXISTS ix_quota_ledgers_event_date ON quota_ledgers(event_date);

CREATE TABLE IF NOT EXISTS quota_reservations (
    id VARCHAR(80) PRIMARY KEY,
    user_id INTEGER NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    event_date VARCHAR(10) NOT NULL,
    amount INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'reserved',
    run_id VARCHAR(255),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_quota_reservation_amount CHECK (amount > 0)
);

-- Forward-fix databases created before reservation expiry was introduced.
ALTER TABLE quota_reservations ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;
UPDATE quota_reservations
SET expires_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) + INTERVAL '15 minutes'
WHERE expires_at IS NULL;
ALTER TABLE quota_reservations ALTER COLUMN expires_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_quota_reservations_user_id ON quota_reservations(user_id);
CREATE INDEX IF NOT EXISTS ix_quota_reservations_event_type ON quota_reservations(event_type);
CREATE INDEX IF NOT EXISTS ix_quota_reservations_event_date ON quota_reservations(event_date);
CREATE INDEX IF NOT EXISTS ix_quota_reservations_status ON quota_reservations(status);
CREATE INDEX IF NOT EXISTS ix_quota_reservations_run_id ON quota_reservations(run_id);
CREATE INDEX IF NOT EXISTS ix_quota_reservations_expires_at ON quota_reservations(expires_at);

CREATE TABLE IF NOT EXISTS durable_background_jobs (
    job_id VARCHAR(255) PRIMARY KEY,
    kind VARCHAR(120) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    created_by_json TEXT,
    result_json TEXT,
    artifact_refs_json TEXT,
    error TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    owner VARCHAR(255),
    heartbeat_at TIMESTAMP,
    lease_until TIMESTAMP NOT NULL,
    interrupted_reason VARCHAR(120),
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_durable_background_job_attempt CHECK (attempt >= 0)
);

CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_kind ON durable_background_jobs(kind);
CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_status ON durable_background_jobs(status);
CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_owner ON durable_background_jobs(owner);
CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_lease_until ON durable_background_jobs(lease_until);
CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_created_at ON durable_background_jobs(created_at);
CREATE INDEX IF NOT EXISTS ix_durable_background_jobs_active_lease
    ON durable_background_jobs(status, lease_until);

CREATE TABLE IF NOT EXISTS ic_task_leases (
    id SERIAL PRIMARY KEY,
    scope_key VARCHAR(500) NOT NULL,
    task_key VARCHAR(500) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'running',
    owner VARCHAR(255) NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    claimed_at TIMESTAMP NOT NULL,
    heartbeat_at TIMESTAMP NOT NULL,
    lease_until TIMESTAMP,
    finished_at TIMESTAMP,
    failure_reason VARCHAR(500),
    recovery_reason VARCHAR(120),
    history_json TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_ic_task_lease_scope_task UNIQUE (scope_key, task_key),
    CONSTRAINT ck_ic_task_lease_attempt CHECK (attempt > 0)
);

CREATE INDEX IF NOT EXISTS ix_ic_task_leases_scope_key ON ic_task_leases(scope_key);
CREATE INDEX IF NOT EXISTS ix_ic_task_leases_task_key ON ic_task_leases(task_key);
CREATE INDEX IF NOT EXISTS ix_ic_task_leases_status ON ic_task_leases(status);
CREATE INDEX IF NOT EXISTS ix_ic_task_leases_owner ON ic_task_leases(owner);
CREATE INDEX IF NOT EXISTS ix_ic_task_leases_lease_until ON ic_task_leases(lease_until);
CREATE INDEX IF NOT EXISTS ix_ic_task_leases_active_lease ON ic_task_leases(status, lease_until);

-- SQLModel.create_all() may have created these tables before this authority was
-- applied.  CREATE TABLE IF NOT EXISTS does not retrofit constraints or server
-- defaults onto an existing table, so audit legacy rows before adding them.
DO $runtime_coordination_preflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM quota_ledgers
        WHERE used_count < 0 OR reserved_count < 0
    ) THEN
        RAISE EXCEPTION 'runtime coordination migration blocked: quota_ledgers contains negative counters'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM quota_reservations WHERE amount <= 0) THEN
        RAISE EXCEPTION 'runtime coordination migration blocked: quota_reservations contains non-positive amounts'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM durable_background_jobs WHERE attempt < 0) THEN
        RAISE EXCEPTION 'runtime coordination migration blocked: durable_background_jobs contains negative attempts'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM ic_task_leases WHERE attempt <= 0) THEN
        RAISE EXCEPTION 'runtime coordination migration blocked: ic_task_leases contains non-positive attempts'
            USING ERRCODE = '23514';
    END IF;
END
$runtime_coordination_preflight$;

ALTER TABLE active_run_leases ALTER COLUMN status SET DEFAULT 'running';
ALTER TABLE quota_ledgers ALTER COLUMN used_count SET DEFAULT 0;
ALTER TABLE quota_ledgers ALTER COLUMN reserved_count SET DEFAULT 0;
ALTER TABLE quota_reservations ALTER COLUMN amount SET DEFAULT 1;
ALTER TABLE quota_reservations ALTER COLUMN status SET DEFAULT 'reserved';
ALTER TABLE durable_background_jobs ALTER COLUMN status SET DEFAULT 'queued';
ALTER TABLE durable_background_jobs ALTER COLUMN attempt SET DEFAULT 0;
ALTER TABLE ic_task_leases ALTER COLUMN status SET DEFAULT 'running';
ALTER TABLE ic_task_leases ALTER COLUMN attempt SET DEFAULT 1;
ALTER TABLE ic_task_leases ALTER COLUMN history_json SET DEFAULT '[]';

DO $runtime_coordination_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'quota_ledgers'::regclass
          AND conname = 'ck_quota_ledger_nonnegative'
    ) THEN
        ALTER TABLE quota_ledgers
            ADD CONSTRAINT ck_quota_ledger_nonnegative
            CHECK (used_count >= 0 AND reserved_count >= 0) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'quota_reservations'::regclass
          AND conname = 'ck_quota_reservation_amount'
    ) THEN
        ALTER TABLE quota_reservations
            ADD CONSTRAINT ck_quota_reservation_amount
            CHECK (amount > 0) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'durable_background_jobs'::regclass
          AND conname = 'ck_durable_background_job_attempt'
    ) THEN
        ALTER TABLE durable_background_jobs
            ADD CONSTRAINT ck_durable_background_job_attempt
            CHECK (attempt >= 0) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'ic_task_leases'::regclass
          AND conname = 'ck_ic_task_lease_attempt'
    ) THEN
        ALTER TABLE ic_task_leases
            ADD CONSTRAINT ck_ic_task_lease_attempt
            CHECK (attempt > 0) NOT VALID;
    END IF;
END
$runtime_coordination_constraints$;

ALTER TABLE quota_ledgers VALIDATE CONSTRAINT ck_quota_ledger_nonnegative;
ALTER TABLE quota_reservations VALIDATE CONSTRAINT ck_quota_reservation_amount;
ALTER TABLE durable_background_jobs VALIDATE CONSTRAINT ck_durable_background_job_attempt;
ALTER TABLE ic_task_leases VALIDATE CONSTRAINT ck_ic_task_lease_attempt;
