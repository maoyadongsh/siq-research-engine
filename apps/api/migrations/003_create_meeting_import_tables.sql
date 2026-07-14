-- Additive resumable long-recording imports. Safe to apply after 002.
CREATE TABLE IF NOT EXISTS meeting_import_uploads (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    meeting_id VARCHAR(36) REFERENCES meeting_sessions(id),
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    extension VARCHAR(16) NOT NULL,
    media_type VARCHAR(100),
    expected_size BIGINT NOT NULL,
    expected_sha256 VARCHAR(64),
    chunk_size INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    received_size BIGINT NOT NULL DEFAULT 0,
    received_chunks INTEGER NOT NULL DEFAULT 0,
    detected_duration_ms BIGINT,
    detected_format VARCHAR(100),
    assembled_sha256 VARCHAR(64),
    title VARCHAR(200) NOT NULL,
    language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
    voiceprint_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ai_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    selection_mode VARCHAR(16) NOT NULL DEFAULT 'none',
    requested_model_ref VARCHAR(255),
    fallback_policy VARCHAR(32) NOT NULL DEFAULT 'disabled',
    cloud_data_boundary_confirmed_at TIMESTAMP,
    state VARCHAR(32) NOT NULL DEFAULT 'uploading',
    step VARCHAR(32) NOT NULL DEFAULT 'uploading',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner VARCHAR(100),
    lease_until TIMESTAMP,
    retry_after TIMESTAMP,
    public_error_code VARCHAR(64),
    internal_diagnostic TEXT,
    expires_at TIMESTAMP NOT NULL,
    staging_purged_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_import_owner_key UNIQUE (owner_user_id, idempotency_key),
    CONSTRAINT ck_meeting_import_expected_size CHECK (expected_size > 0),
    CONSTRAINT ck_meeting_import_chunk_shape CHECK (chunk_size > 0 AND total_chunks > 0),
    CONSTRAINT ck_meeting_import_progress CHECK (received_size >= 0 AND received_chunks >= 0),
    CONSTRAINT ck_meeting_import_attempts CHECK (attempt >= 0 AND max_attempts > 0)
);

CREATE TABLE IF NOT EXISTS meeting_import_chunks (
    id VARCHAR(36) PRIMARY KEY,
    upload_id VARCHAR(36) NOT NULL REFERENCES meeting_import_uploads(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    byte_offset BIGINT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_key VARCHAR(500) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_import_chunk_ordinal UNIQUE (upload_id, ordinal),
    CONSTRAINT ck_meeting_import_chunk_position CHECK (ordinal >= 0 AND byte_offset >= 0),
    CONSTRAINT ck_meeting_import_chunk_size CHECK (byte_size > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_import_claim
    ON meeting_import_uploads (state, lease_until, created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_import_owner_created
    ON meeting_import_uploads (owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_import_uploads_meeting_id
    ON meeting_import_uploads (meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_import_chunks_upload
    ON meeting_import_chunks (upload_id, ordinal);
