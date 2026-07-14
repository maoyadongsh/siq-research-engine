-- Frozen native-capture batch declarations used to verify offline backfill.
-- Safe to apply repeatedly after migrations 002 and 004.
CREATE TABLE IF NOT EXISTS meeting_native_capture_manifest_entries (
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
    manifest_revision INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_native_capture_manifest_entry UNIQUE (
        capture_id, stream_epoch, sequence
    ),
    CONSTRAINT ck_meeting_native_capture_manifest_entry_range CHECK (
        stream_epoch > 0 AND sequence >= 0 AND first_sample >= 0
        AND sample_count > 0 AND end_sample = first_sample + sample_count
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_manifest_entries_capture_id
    ON meeting_native_capture_manifest_entries(capture_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_manifest_entries_meeting_id
    ON meeting_native_capture_manifest_entries(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_manifest_entries_owner_user_id
    ON meeting_native_capture_manifest_entries(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_native_capture_manifest_timeline
    ON meeting_native_capture_manifest_entries(capture_id, first_sample);
