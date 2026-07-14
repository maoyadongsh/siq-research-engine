-- Forward upgrade for databases that applied migration 004 before epoch-level
-- canonical manifest verification was introduced. PostgreSQL deployment
-- migrations are required to be additive and safe to replay.
ALTER TABLE meeting_native_capture_epochs
    ADD COLUMN IF NOT EXISTS manifest_sha256 VARCHAR(64);
