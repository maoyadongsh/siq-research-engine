-- Optional iOS native capture ingest. Safe to apply after meeting migration 002.
CREATE TABLE IF NOT EXISTS meeting_native_captures (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    device_installation_hash VARCHAR(64) NOT NULL,
    create_idempotency_key VARCHAR(128) NOT NULL,
    create_request_hash VARCHAR(64) NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'active',
    encoding VARCHAR(32) NOT NULL DEFAULT 'pcm_s16le',
    sample_rate INTEGER NOT NULL DEFAULT 16000,
    channels INTEGER NOT NULL DEFAULT 1,
    current_epoch INTEGER NOT NULL DEFAULT 1,
    max_total_bytes BIGINT NOT NULL,
    max_duration_samples BIGINT NOT NULL,
    total_bytes BIGINT NOT NULL DEFAULT 0,
    total_samples BIGINT NOT NULL DEFAULT 0,
    sealed_through_sample BIGINT,
    seal_manifest_revision INTEGER,
    seal_manifest_sha256 VARCHAR(64),
    ingest_complete BOOLEAN NOT NULL DEFAULT FALSE,
    server_playback_state VARCHAR(32) NOT NULL DEFAULT 'not_ready',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    sealed_at TIMESTAMP,
    revoked_at TIMESTAMP,
    CONSTRAINT uq_meeting_native_capture_owner_create_key UNIQUE (owner_user_id, create_idempotency_key),
    CONSTRAINT ck_meeting_native_capture_epoch CHECK (current_epoch > 0),
    CONSTRAINT ck_meeting_native_capture_limits CHECK (max_total_bytes > 0 AND max_duration_samples > 0),
    CONSTRAINT ck_meeting_native_capture_totals CHECK (total_bytes >= 0 AND total_samples >= 0)
);

CREATE TABLE IF NOT EXISTS meeting_native_capture_epochs (
    id VARCHAR(36) PRIMARY KEY,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    stream_epoch INTEGER NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'active',
    last_sequence BIGINT,
    recorded_through_sample BIGINT,
    manifest_revision INTEGER,
    manifest_sha256 VARCHAR(64),
    rollover_from_epoch INTEGER,
    rollover_idempotency_key VARCHAR(128),
    rollover_request_hash VARCHAR(64),
    created_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    CONSTRAINT uq_meeting_native_capture_epoch UNIQUE (capture_id, stream_epoch),
    CONSTRAINT uq_meeting_native_capture_rollover_key UNIQUE (capture_id, rollover_idempotency_key),
    CONSTRAINT ck_meeting_native_capture_epoch_no CHECK (stream_epoch > 0),
    CONSTRAINT ck_meeting_native_capture_epoch_sequence CHECK (last_sequence IS NULL OR last_sequence >= -1),
    CONSTRAINT ck_meeting_native_capture_epoch_sample CHECK (
        recorded_through_sample IS NULL OR recorded_through_sample >= 0
    )
);

CREATE TABLE IF NOT EXISTS meeting_native_capture_batches (
    id VARCHAR(36) PRIMARY KEY,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    stream_epoch INTEGER NOT NULL,
    sequence BIGINT NOT NULL,
    first_sample BIGINT NOT NULL,
    sample_count BIGINT NOT NULL,
    end_sample BIGINT NOT NULL,
    captured_monotonic_ns BIGINT NOT NULL,
    encoding VARCHAR(32) NOT NULL,
    sample_rate INTEGER NOT NULL,
    channels INTEGER NOT NULL,
    byte_size BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_key VARCHAR(500) NOT NULL,
    manifest_revision INTEGER NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_native_capture_batch_sequence UNIQUE (capture_id, stream_epoch, sequence),
    CONSTRAINT uq_meeting_native_capture_batch_key UNIQUE (capture_id, idempotency_key),
    CONSTRAINT ck_meeting_native_capture_batch_position CHECK (stream_epoch > 0 AND sequence >= 0),
    CONSTRAINT ck_meeting_native_capture_batch_samples CHECK (
        first_sample >= 0 AND sample_count > 0 AND end_sample = first_sample + sample_count
        AND captured_monotonic_ns >= 0
    ),
    CONSTRAINT ck_meeting_native_capture_batch_size CHECK (byte_size > 0)
);

CREATE TABLE IF NOT EXISTS meeting_native_capture_tokens (
    id VARCHAR(36) PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    purpose VARCHAR(64) NOT NULL DEFAULT 'meeting_native_capture',
    scopes_json TEXT NOT NULL DEFAULT '[]',
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_native_capture_token_hash UNIQUE (token_hash)
);

CREATE INDEX IF NOT EXISTS ix_meeting_native_captures_meeting_id ON meeting_native_captures(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_captures_owner_user_id ON meeting_native_captures(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_captures_state ON meeting_native_captures(state);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_owner_state ON meeting_native_captures(owner_user_id, state);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_epochs_capture_id ON meeting_native_capture_epochs(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_epoch_state ON meeting_native_capture_epochs(capture_id, state);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_batches_capture_id ON meeting_native_capture_batches(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_batches_meeting_id ON meeting_native_capture_batches(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_batches_owner_user_id ON meeting_native_capture_batches(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_batch_samples ON meeting_native_capture_batches(capture_id, first_sample);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_tokens_capture_id ON meeting_native_capture_tokens(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_tokens_meeting_id ON meeting_native_capture_tokens(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_tokens_owner_user_id ON meeting_native_capture_tokens(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_token_expiry ON meeting_native_capture_tokens(expires_at, revoked_at);
