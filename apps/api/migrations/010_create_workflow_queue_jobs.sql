-- Durable workflow queue executed by independent workers.
-- Additive, replay-safe, and intentionally separate from API process threads.

CREATE TABLE IF NOT EXISTS workflow_queue_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    task_id VARCHAR(255) NOT NULL,
    retry_scope VARCHAR(80) NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    snapshot_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    owner VARCHAR(255),
    heartbeat_at TIMESTAMP,
    available_at TIMESTAMP NOT NULL,
    lease_until TIMESTAMP,
    interrupted_reason VARCHAR(120),
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_workflow_queue_job_attempts
        CHECK (attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts)
);

CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_task_id ON workflow_queue_jobs(task_id);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_retry_scope ON workflow_queue_jobs(retry_scope);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_idempotency_key ON workflow_queue_jobs(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_status ON workflow_queue_jobs(status);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_owner ON workflow_queue_jobs(owner);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_available_at ON workflow_queue_jobs(available_at);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_lease_until ON workflow_queue_jobs(lease_until);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_created_at ON workflow_queue_jobs(created_at);
CREATE INDEX IF NOT EXISTS ix_workflow_queue_jobs_claim
    ON workflow_queue_jobs(status, available_at, lease_until);
CREATE UNIQUE INDEX IF NOT EXISTS ix_workflow_queue_jobs_active_idempotency
    ON workflow_queue_jobs(idempotency_key) WHERE status IN ('queued', 'running');

DO $workflow_queue_preflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM workflow_queue_jobs
        WHERE attempt < 0 OR max_attempts <= 0 OR attempt > max_attempts
    ) THEN
        RAISE EXCEPTION 'workflow queue migration blocked: invalid attempt budget'
            USING ERRCODE = '23514';
    END IF;
END
$workflow_queue_preflight$;

ALTER TABLE workflow_queue_jobs ALTER COLUMN status SET DEFAULT 'queued';
ALTER TABLE workflow_queue_jobs ALTER COLUMN attempt SET DEFAULT 0;
ALTER TABLE workflow_queue_jobs ALTER COLUMN max_attempts SET DEFAULT 3;

DO $workflow_queue_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'workflow_queue_jobs'::regclass
          AND conname = 'ck_workflow_queue_job_attempts'
    ) THEN
        ALTER TABLE workflow_queue_jobs
            ADD CONSTRAINT ck_workflow_queue_job_attempts
            CHECK (attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts) NOT VALID;
    END IF;
END
$workflow_queue_constraints$;

ALTER TABLE workflow_queue_jobs VALIDATE CONSTRAINT ck_workflow_queue_job_attempts;
