-- Additive-only meeting transcription schema.
-- The DDL uses the SQLite/PostgreSQL common subset and is safe to run repeatedly.

CREATE TABLE IF NOT EXISTS meeting_sessions (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    title VARCHAR(200) NOT NULL,
    language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
    state VARCHAR(24) NOT NULL DEFAULT 'draft',
    postprocess_state VARCHAR(24) NOT NULL DEFAULT 'not_started',
    audio_source VARCHAR(24) NOT NULL DEFAULT 'microphone',
    voiceprint_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ai_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    selection_mode VARCHAR(16) NOT NULL DEFAULT 'none',
    requested_model_ref VARCHAR(255),
    fallback_policy VARCHAR(32) NOT NULL DEFAULT 'disabled',
    settings_version INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    stream_epoch INTEGER NOT NULL DEFAULT 0,
    last_audio_sequence BIGINT NOT NULL DEFAULT -1,
    last_segment_ordinal BIGINT NOT NULL DEFAULT 0,
    active_lexicon_version INTEGER,
    started_at TIMESTAMP,
    stopped_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_meeting_sessions_settings_version CHECK (settings_version >= 1),
    CONSTRAINT ck_meeting_sessions_version CHECK (version >= 1),
    CONSTRAINT ck_meeting_sessions_time_order CHECK (
        stopped_at IS NULL OR started_at IS NULL OR stopped_at >= started_at
    ),
    CONSTRAINT ck_meeting_sessions_ai_selection CHECK (
        (ai_enabled AND selection_mode IN ('auto', 'pinned')) OR
        ((NOT ai_enabled) AND selection_mode = 'none' AND requested_model_ref IS NULL)
    ),
    CONSTRAINT ck_meeting_sessions_pinned_model CHECK (
        selection_mode <> 'pinned' OR requested_model_ref IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_sessions_owner_user_id ON meeting_sessions(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_sessions_state ON meeting_sessions(state);
CREATE INDEX IF NOT EXISTS ix_meeting_sessions_created_at ON meeting_sessions(created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_sessions_owner_created ON meeting_sessions(owner_user_id, created_at);

CREATE TABLE IF NOT EXISTS meeting_stream_leases (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    stream_epoch INTEGER NOT NULL DEFAULT 0,
    connection_id VARCHAR(64) NOT NULL,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    lease_until TIMESTAMP NOT NULL,
    last_acked_sequence BIGINT NOT NULL DEFAULT -1,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_stream_lease_meeting UNIQUE (meeting_id),
    CONSTRAINT ck_meeting_stream_lease_epoch CHECK (stream_epoch >= 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_stream_leases_meeting_id ON meeting_stream_leases(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_stream_leases_owner_user_id ON meeting_stream_leases(owner_user_id);

CREATE TABLE IF NOT EXISTS meeting_audio_chunks (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    stream_epoch INTEGER NOT NULL,
    sequence BIGINT NOT NULL,
    start_ms BIGINT NOT NULL,
    duration_ms BIGINT NOT NULL,
    storage_key VARCHAR(500) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    byte_size BIGINT NOT NULL,
    codec VARCHAR(32) NOT NULL DEFAULT 'pcm_s16le',
    sample_rate INTEGER NOT NULL DEFAULT 16000,
    channels INTEGER NOT NULL DEFAULT 1,
    state VARCHAR(24) NOT NULL DEFAULT 'received',
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_audio_chunk_sequence UNIQUE (meeting_id, stream_epoch, sequence),
    CONSTRAINT ck_meeting_audio_chunk_sequence CHECK (sequence >= 0),
    CONSTRAINT ck_meeting_audio_chunk_time CHECK (start_ms >= 0 AND duration_ms > 0),
    CONSTRAINT ck_meeting_audio_chunk_size CHECK (byte_size > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_audio_chunks_meeting_id ON meeting_audio_chunks(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_audio_chunks_timeline ON meeting_audio_chunks(meeting_id, start_ms);

CREATE TABLE IF NOT EXISTS meeting_voice_profiles (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    display_name VARCHAR(100) NOT NULL,
    scope VARCHAR(32) NOT NULL DEFAULT 'user_private',
    status VARCHAR(24) NOT NULL DEFAULT 'collecting',
    encoder_name VARCHAR(100),
    encoder_version VARCHAR(100),
    encrypted_embedding TEXT,
    key_id VARCHAR(100),
    sample_count INTEGER NOT NULL DEFAULT 0,
    effective_duration_ms BIGINT NOT NULL DEFAULT 0,
    quality_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    CONSTRAINT ck_meeting_voice_profile_scope CHECK (scope = 'user_private'),
    CONSTRAINT ck_meeting_voice_profile_quality CHECK (
        sample_count >= 0 AND effective_duration_ms >= 0
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_voice_profiles_owner_user_id ON meeting_voice_profiles(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voice_profiles_status ON meeting_voice_profiles(status);
CREATE INDEX IF NOT EXISTS ix_meeting_voice_profile_owner_status ON meeting_voice_profiles(owner_user_id, status);

CREATE TABLE IF NOT EXISTS meeting_speaker_tracks (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    track_key VARCHAR(128) NOT NULL,
    anonymous_label VARCHAR(100) NOT NULL,
    display_name VARCHAR(100),
    label_source VARCHAR(32) NOT NULL DEFAULT 'anonymous',
    voice_profile_id VARCHAR(36),
    match_confidence DOUBLE PRECISION,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_speaker_track_key UNIQUE (meeting_id, track_key),
    CONSTRAINT ck_meeting_speaker_track_version CHECK (version >= 1),
    CONSTRAINT ck_meeting_speaker_track_confidence CHECK (
        match_confidence IS NULL OR (match_confidence >= 0 AND match_confidence <= 1)
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_speaker_tracks_meeting_id ON meeting_speaker_tracks(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_speaker_tracks_voice_profile_id ON meeting_speaker_tracks(voice_profile_id);

CREATE TABLE IF NOT EXISTS meeting_transcript_segments (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    ordinal BIGINT NOT NULL,
    utterance_id VARCHAR(128) NOT NULL,
    provider_segment_key VARCHAR(255) NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL,
    speaker_track_id VARCHAR(36) REFERENCES meeting_speaker_tracks(id),
    raw_text TEXT NOT NULL,
    asr_final_text TEXT NOT NULL,
    normalized_text TEXT,
    asr_confidence DOUBLE PRECISION,
    asr_provider VARCHAR(100) NOT NULL,
    asr_model VARCHAR(200) NOT NULL,
    asr_version VARCHAR(100) NOT NULL,
    hotword_version INTEGER,
    word_timestamps_json TEXT NOT NULL DEFAULT '[]',
    asr_metadata_json TEXT NOT NULL DEFAULT '{}',
    overlap BOOLEAN NOT NULL DEFAULT FALSE,
    noise_level DOUBLE PRECISION,
    human_locked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_transcript_ordinal UNIQUE (meeting_id, ordinal),
    CONSTRAINT uq_meeting_transcript_provider_key UNIQUE (meeting_id, provider_segment_key),
    CONSTRAINT ck_meeting_transcript_ordinal CHECK (ordinal > 0),
    CONSTRAINT ck_meeting_transcript_time CHECK (start_ms >= 0 AND end_ms >= start_ms),
    CONSTRAINT ck_meeting_transcript_confidence CHECK (
        asr_confidence IS NULL OR (asr_confidence >= 0 AND asr_confidence <= 1)
    ),
    CONSTRAINT ck_meeting_transcript_noise CHECK (
        noise_level IS NULL OR (noise_level >= 0 AND noise_level <= 1)
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_transcript_segments_meeting_id ON meeting_transcript_segments(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_transcript_segments_speaker_track_id ON meeting_transcript_segments(speaker_track_id);
CREATE INDEX IF NOT EXISTS ix_meeting_transcript_timeline ON meeting_transcript_segments(meeting_id, start_ms);

CREATE TABLE IF NOT EXISTS meeting_segment_revisions (
    id VARCHAR(36) PRIMARY KEY,
    segment_id VARCHAR(36) NOT NULL REFERENCES meeting_transcript_segments(id),
    revision_no INTEGER NOT NULL,
    revision_type VARCHAR(32) NOT NULL,
    text TEXT NOT NULL,
    base_revision_no INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    model_snapshot_id VARCHAR(36),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_segment_revision_no UNIQUE (segment_id, revision_no),
    CONSTRAINT ck_meeting_segment_revision_no CHECK (revision_no > 0),
    CONSTRAINT ck_meeting_segment_base_revision_no CHECK (base_revision_no >= 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_segment_revisions_segment_id ON meeting_segment_revisions(segment_id);
CREATE INDEX IF NOT EXISTS ix_meeting_segment_revisions_model_snapshot_id ON meeting_segment_revisions(model_snapshot_id);

CREATE TABLE IF NOT EXISTS meeting_asr_correction_events (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    segment_id VARCHAR(36) NOT NULL REFERENCES meeting_transcript_segments(id),
    speaker_track_id VARCHAR(36),
    voice_profile_id VARCHAR(36),
    base_revision_no INTEGER NOT NULL,
    result_revision_no INTEGER NOT NULL,
    original_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    diff_ops_json TEXT NOT NULL,
    edit_intent VARCHAR(24) NOT NULL,
    error_class VARCHAR(24) NOT NULL,
    contribute_to_accuracy BOOLEAN NOT NULL DEFAULT FALSE,
    asr_provider VARCHAR(100) NOT NULL,
    asr_model VARCHAR(200) NOT NULL,
    asr_version VARCHAR(100) NOT NULL,
    hotword_version INTEGER,
    audio_start_ms BIGINT NOT NULL,
    audio_end_ms BIGINT NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'active',
    idempotency_key VARCHAR(128),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_correction_idempotency UNIQUE (owner_user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_owner_user_id ON meeting_asr_correction_events(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_meeting_id ON meeting_asr_correction_events(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_segment_id ON meeting_asr_correction_events(segment_id);
CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_speaker_track_id ON meeting_asr_correction_events(speaker_track_id);
CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_voice_profile_id ON meeting_asr_correction_events(voice_profile_id);
CREATE INDEX IF NOT EXISTS ix_meeting_asr_correction_events_status ON meeting_asr_correction_events(status);
CREATE INDEX IF NOT EXISTS ix_meeting_correction_owner_created ON meeting_asr_correction_events(owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_correction_segment ON meeting_asr_correction_events(segment_id, result_revision_no);

CREATE TABLE IF NOT EXISTS meeting_term_candidates (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    canonical_term VARCHAR(200) NOT NULL,
    misrecognition VARCHAR(200) NOT NULL DEFAULT '',
    language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
    candidate_type VARCHAR(32) NOT NULL DEFAULT 'hotword',
    source_count INTEGER NOT NULL DEFAULT 1,
    distinct_meeting_count INTEGER NOT NULL DEFAULT 1,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    reverted_count INTEGER NOT NULL DEFAULT 0,
    speaker_specific_candidate VARCHAR(36),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_term_candidate_identity UNIQUE (
        owner_user_id, language, canonical_term, misrecognition
    ),
    CONSTRAINT ck_meeting_term_candidate_confidence CHECK (
        confidence >= 0 AND confidence <= 1
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_term_candidates_owner_user_id ON meeting_term_candidates(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_term_candidates_status ON meeting_term_candidates(status);
CREATE INDEX IF NOT EXISTS ix_meeting_term_candidate_owner_status ON meeting_term_candidates(owner_user_id, status);

CREATE TABLE IF NOT EXISTS meeting_term_candidate_sources (
    id VARCHAR(36) PRIMARY KEY,
    candidate_id VARCHAR(36) NOT NULL REFERENCES meeting_term_candidates(id),
    correction_event_id VARCHAR(36) NOT NULL REFERENCES meeting_asr_correction_events(id),
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_term_candidate_source UNIQUE (candidate_id, correction_event_id)
);

CREATE INDEX IF NOT EXISTS ix_meeting_term_candidate_sources_candidate_id ON meeting_term_candidate_sources(candidate_id);
CREATE INDEX IF NOT EXISTS ix_meeting_term_candidate_sources_correction_event_id ON meeting_term_candidate_sources(correction_event_id);
CREATE INDEX IF NOT EXISTS ix_meeting_term_candidate_sources_meeting_id ON meeting_term_candidate_sources(meeting_id);

CREATE TABLE IF NOT EXISTS meeting_lexicon_entries (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
    canonical_term VARCHAR(200) NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    misrecognitions_json TEXT NOT NULL DEFAULT '[]',
    entry_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    weight DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    scope VARCHAR(32) NOT NULL DEFAULT 'user_future_meetings',
    meeting_id VARCHAR(36) REFERENCES meeting_sessions(id),
    speaker_voice_profile_id VARCHAR(36),
    status VARCHAR(24) NOT NULL DEFAULT 'active',
    source_candidate_id VARCHAR(36) REFERENCES meeting_term_candidates(id),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_meeting_lexicon_entry_weight CHECK (weight >= 0 AND weight <= 10)
);

CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_entries_owner_user_id ON meeting_lexicon_entries(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_entries_meeting_id ON meeting_lexicon_entries(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_entries_source_candidate_id ON meeting_lexicon_entries(source_candidate_id);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_entries_status ON meeting_lexicon_entries(status);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_owner_status ON meeting_lexicon_entries(owner_user_id, language, status);

CREATE TABLE IF NOT EXISTS meeting_lexicon_versions (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    meeting_id VARCHAR(36) REFERENCES meeting_sessions(id),
    version INTEGER NOT NULL,
    language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
    entries_hash VARCHAR(64) NOT NULL,
    entry_count INTEGER NOT NULL DEFAULT 0,
    entries_json TEXT NOT NULL DEFAULT '[]',
    change_reason VARCHAR(100) NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    supersedes_version INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_meeting_lexicon_version UNIQUE (owner_user_id, language, version),
    CONSTRAINT ck_meeting_lexicon_version_no CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_versions_owner_user_id ON meeting_lexicon_versions(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_versions_meeting_id ON meeting_lexicon_versions(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_versions_is_active ON meeting_lexicon_versions(is_active);
CREATE INDEX IF NOT EXISTS ix_meeting_lexicon_version_owner ON meeting_lexicon_versions(owner_user_id, language, created_at);

CREATE TABLE IF NOT EXISTS meeting_voiceprint_consents (
    id VARCHAR(36) PRIMARY KEY,
    voice_profile_id VARCHAR(36) NOT NULL REFERENCES meeting_voice_profiles(id),
    actor_user_id INTEGER NOT NULL REFERENCES users(id),
    subject_label VARCHAR(100) NOT NULL,
    purpose VARCHAR(64) NOT NULL DEFAULT 'future_meeting_speaker_identification',
    scope VARCHAR(32) NOT NULL DEFAULT 'user_private',
    policy_version VARCHAR(64) NOT NULL,
    source_meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    granted_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT ck_meeting_voiceprint_consent_purpose CHECK (
        purpose = 'future_meeting_speaker_identification'
    ),
    CONSTRAINT ck_meeting_voiceprint_consent_scope CHECK (scope = 'user_private')
);

CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_consents_voice_profile_id ON meeting_voiceprint_consents(voice_profile_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_consents_actor_user_id ON meeting_voiceprint_consents(actor_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_consents_source_meeting_id ON meeting_voiceprint_consents(source_meeting_id);

CREATE TABLE IF NOT EXISTS meeting_voiceprint_matches (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    speaker_track_id VARCHAR(36) NOT NULL REFERENCES meeting_speaker_tracks(id),
    voice_profile_id VARCHAR(36) NOT NULL REFERENCES meeting_voice_profiles(id),
    encoder_version VARCHAR(100) NOT NULL,
    threshold_version VARCHAR(100) NOT NULL,
    top1_score DOUBLE PRECISION NOT NULL,
    top1_top2_margin DOUBLE PRECISION NOT NULL,
    effective_duration_ms BIGINT NOT NULL,
    quality_grade VARCHAR(24) NOT NULL,
    decision VARCHAR(24) NOT NULL DEFAULT 'suggested',
    decided_by VARCHAR(64),
    created_at TIMESTAMP NOT NULL,
    decided_at TIMESTAMP,
    CONSTRAINT ck_meeting_voice_match_top1 CHECK (top1_score >= 0 AND top1_score <= 1),
    CONSTRAINT ck_meeting_voice_match_margin CHECK (
        top1_top2_margin >= 0 AND top1_top2_margin <= 1
    )
);

CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_matches_meeting_id ON meeting_voiceprint_matches(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_matches_speaker_track_id ON meeting_voiceprint_matches(speaker_track_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_matches_voice_profile_id ON meeting_voiceprint_matches(voice_profile_id);
CREATE INDEX IF NOT EXISTS ix_meeting_voiceprint_matches_decision ON meeting_voiceprint_matches(decision);
CREATE INDEX IF NOT EXISTS ix_meeting_voice_match_meeting ON meeting_voiceprint_matches(meeting_id, created_at);

CREATE TABLE IF NOT EXISTS meeting_model_settings (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    settings_version INTEGER NOT NULL,
    selection_mode VARCHAR(16) NOT NULL,
    requested_model_ref VARCHAR(255),
    fallback_policy VARCHAR(32) NOT NULL DEFAULT 'disabled',
    effective_after_segment_ordinal BIGINT NOT NULL DEFAULT 0,
    cloud_data_boundary_confirmed_at TIMESTAMP,
    changed_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_model_setting_version UNIQUE (meeting_id, settings_version),
    CONSTRAINT ck_meeting_model_setting_version CHECK (settings_version > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_model_settings_meeting_id ON meeting_model_settings(meeting_id);

CREATE TABLE IF NOT EXISTS meeting_model_snapshots (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    model_ref VARCHAR(255) NOT NULL,
    selection_mode VARCHAR(16) NOT NULL,
    resolved_provider VARCHAR(100) NOT NULL,
    resolved_model VARCHAR(200) NOT NULL,
    provider_locality VARCHAR(16) NOT NULL,
    hermes_target VARCHAR(255) NOT NULL,
    meeting_profile_version VARCHAR(64) NOT NULL,
    prompt_version VARCHAR(64) NOT NULL,
    schema_version VARCHAR(64) NOT NULL DEFAULT 'meeting.v1',
    settings_version INTEGER NOT NULL,
    effective_after_segment_ordinal BIGINT NOT NULL DEFAULT 0,
    resolved_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_meeting_model_snapshots_meeting_id ON meeting_model_snapshots(meeting_id);

CREATE TABLE IF NOT EXISTS meeting_artifacts (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    artifact_type VARCHAR(32) NOT NULL,
    version INTEGER NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'generating',
    content_json TEXT,
    content_text TEXT,
    input_from_ordinal BIGINT NOT NULL DEFAULT 1,
    input_to_ordinal BIGINT NOT NULL DEFAULT 0,
    transcript_revision INTEGER NOT NULL DEFAULT 0,
    model_snapshot_id VARCHAR(36) REFERENCES meeting_model_snapshots(id),
    supersedes_id VARCHAR(36) REFERENCES meeting_artifacts(id),
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_artifact_version UNIQUE (meeting_id, artifact_type, version),
    CONSTRAINT ck_meeting_artifact_version CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_artifacts_meeting_id ON meeting_artifacts(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_artifacts_state ON meeting_artifacts(state);
CREATE INDEX IF NOT EXISTS ix_meeting_artifacts_model_snapshot_id ON meeting_artifacts(model_snapshot_id);
CREATE INDEX IF NOT EXISTS ix_meeting_artifact_meeting_state ON meeting_artifacts(meeting_id, state);

CREATE TABLE IF NOT EXISTS meeting_jobs (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    job_kind VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner VARCHAR(100),
    lease_until TIMESTAMP,
    input_watermark BIGINT NOT NULL DEFAULT 0,
    settings_version INTEGER NOT NULL DEFAULT 1,
    model_snapshot_id VARCHAR(36) REFERENCES meeting_model_snapshots(id),
    input_json TEXT NOT NULL DEFAULT '{}',
    public_error_code VARCHAR(64),
    internal_diagnostic TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_job_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_meeting_job_attempts CHECK (attempt >= 0 AND max_attempts > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_jobs_meeting_id ON meeting_jobs(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_jobs_state ON meeting_jobs(state);
CREATE INDEX IF NOT EXISTS ix_meeting_jobs_model_snapshot_id ON meeting_jobs(model_snapshot_id);
CREATE INDEX IF NOT EXISTS ix_meeting_jobs_claim ON meeting_jobs(state, lease_until, created_at);
CREATE INDEX IF NOT EXISTS ix_meeting_jobs_meeting ON meeting_jobs(meeting_id, created_at);

CREATE TABLE IF NOT EXISTS meeting_events (
    id VARCHAR(36) PRIMARY KEY,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    cursor BIGINT NOT NULL,
    event_id VARCHAR(36) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    schema_version VARCHAR(64) NOT NULL DEFAULT 'meeting.event.v1',
    payload_json TEXT NOT NULL DEFAULT '{}',
    trace_id VARCHAR(64),
    created_at TIMESTAMP NOT NULL,
    published_at TIMESTAMP,
    CONSTRAINT uq_meeting_event_cursor UNIQUE (meeting_id, cursor),
    CONSTRAINT uq_meeting_event_id UNIQUE (event_id),
    CONSTRAINT ck_meeting_event_cursor CHECK (cursor > 0)
);

CREATE INDEX IF NOT EXISTS ix_meeting_events_meeting_id ON meeting_events(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_events_event_type ON meeting_events(event_type);
CREATE INDEX IF NOT EXISTS ix_meeting_events_unpublished ON meeting_events(published_at, created_at);

CREATE TABLE IF NOT EXISTS meeting_stream_tickets (
    id VARCHAR(36) PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL,
    meeting_id VARCHAR(36) NOT NULL REFERENCES meeting_sessions(id),
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    stream_epoch INTEGER NOT NULL,
    purpose VARCHAR(64) NOT NULL DEFAULT 'meeting_audio_producer',
    origin VARCHAR(500) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP,
    connection_id VARCHAR(64),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_stream_ticket_hash UNIQUE (token_hash),
    CONSTRAINT ck_meeting_stream_ticket_epoch CHECK (stream_epoch >= 1)
);

CREATE INDEX IF NOT EXISTS ix_meeting_stream_tickets_meeting_id ON meeting_stream_tickets(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_stream_tickets_owner_user_id ON meeting_stream_tickets(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_stream_ticket_expiry ON meeting_stream_tickets(expires_at, consumed_at);

CREATE TABLE IF NOT EXISTS meeting_idempotency_records (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    idempotency_key VARCHAR(128) NOT NULL,
    operation VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    resource_id VARCHAR(64),
    response_status INTEGER NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_meeting_http_idempotency UNIQUE (owner_user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_meeting_idempotency_records_owner_user_id ON meeting_idempotency_records(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_meeting_idempotency_created ON meeting_idempotency_records(created_at);
