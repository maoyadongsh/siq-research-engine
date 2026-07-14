-- Durable native-capture packaging, provenance, and explicit gap accounting.
-- Safe to apply repeatedly after migrations 002 and 004.
CREATE TABLE IF NOT EXISTS meeting_native_capture_gaps (
    id VARCHAR(36) PRIMARY KEY,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    stream_epoch INTEGER NOT NULL,
    from_sequence BIGINT NOT NULL,
    to_sequence BIGINT NOT NULL,
    start_sample BIGINT NOT NULL,
    end_sample BIGINT NOT NULL,
    reason VARCHAR(40) NOT NULL,
    manifest_revision INTEGER NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_native_capture_gap_key UNIQUE (capture_id, idempotency_key),
    CONSTRAINT ck_meeting_native_capture_gap_sequence CHECK (
        stream_epoch > 0 AND from_sequence >= 0 AND to_sequence >= from_sequence
    ),
    CONSTRAINT ck_meeting_native_capture_gap_samples CHECK (
        start_sample >= 0 AND end_sample > start_sample
    )
);

CREATE TABLE IF NOT EXISTS meeting_native_capture_finalizations (
    id VARCHAR(36) PRIMARY KEY,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    state VARCHAR(24) NOT NULL DEFAULT 'pending_upload',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    lease_owner VARCHAR(100),
    lease_until TIMESTAMP,
    retry_after TIMESTAMP,
    public_error_code VARCHAR(64),
    internal_diagnostic TEXT,
    wav_sha256 VARCHAR(64),
    wav_byte_size BIGINT,
    audio_chunk_count INTEGER NOT NULL DEFAULT 0,
    accepted_gap_count INTEGER NOT NULL DEFAULT 0,
    final_transcript_job_id VARCHAR(36) REFERENCES meeting_jobs(id),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    ready_at TIMESTAMP,
    CONSTRAINT uq_meeting_native_capture_finalization_capture UNIQUE (capture_id),
    CONSTRAINT ck_meeting_native_capture_finalization_attempts CHECK (
        attempt >= 0 AND max_attempts > 0
    ),
    CONSTRAINT ck_meeting_native_capture_finalization_wav_size CHECK (
        wav_byte_size IS NULL OR wav_byte_size >= 44
    )
);

CREATE TABLE IF NOT EXISTS meeting_native_capture_audio_links (
    id VARCHAR(36) PRIMARY KEY,
    capture_id VARCHAR(36) NOT NULL REFERENCES meeting_native_captures(id),
    batch_id VARCHAR(36) NOT NULL REFERENCES meeting_native_capture_batches(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    stream_epoch INTEGER NOT NULL,
    sequence BIGINT NOT NULL,
    audio_chunk_id VARCHAR(36) NOT NULL REFERENCES meeting_audio_chunks(id),
    source_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_native_capture_audio_link_batch UNIQUE (batch_id),
    CONSTRAINT uq_meeting_native_capture_audio_link_sequence UNIQUE (
        capture_id, stream_epoch, sequence
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_gaps_capture_id
    ON meeting_native_capture_gaps(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_gaps_meeting_id
    ON meeting_native_capture_gaps(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_gaps_owner_user_id
    ON meeting_native_capture_gaps(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_gap_timeline
    ON meeting_native_capture_gaps(capture_id, start_sample);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalizations_capture_id
    ON meeting_native_capture_finalizations(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalizations_meeting_id
    ON meeting_native_capture_finalizations(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalizations_owner_user_id
    ON meeting_native_capture_finalizations(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalizations_state
    ON meeting_native_capture_finalizations(state);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalizations_final_transcript_job_id
    ON meeting_native_capture_finalizations(final_transcript_job_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_finalization_claim
    ON meeting_native_capture_finalizations(state, retry_after, lease_until, created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_audio_links_capture_id
    ON meeting_native_capture_audio_links(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_audio_links_batch_id
    ON meeting_native_capture_audio_links(batch_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_audio_links_meeting_id
    ON meeting_native_capture_audio_links(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_audio_link_chunk
    ON meeting_native_capture_audio_links(audio_chunk_id);
