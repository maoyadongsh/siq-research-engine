# Meeting AI Worker

Meeting post-processing runs outside the API process. Production uses three
independent lease lanes: bounded final ASR plus speaker reclustering, rolling and
final minutes, and stable-text correction. Final ASR reads verified audio chunks
into overlapping, fixed-size windows and sends at most a configured number of
independent requests concurrently. It never loads a complete meeting into
memory, sends filesystem paths to the speech service, or relies on mutable
cross-window decoder state.

## Prerequisites

- The API database is reachable through `SIQ_APP_DATABASE_URL` (or the local
  SQLite default).
- The `minutes` and `correction` lanes require an immutable meeting target pool
  defined by `SIQ_MEETINGS_HERMES_TARGETS_JSON` or
  `SIQ_MEETINGS_HERMES_TARGETS_FILE`.
- Each selected target's `api_key_env` variable is set and its immutable Hermes
  meeting gateway is healthy.
- Meeting Speech Service exposes the internal `/v1/finalize-window` endpoint
  and shares `SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN` with the `finalization`
  lane. This lane does not require a Hermes target or credential.

## Start

From the repository root:

```bash
cd apps/api
uv run python scripts/meeting_ai_worker.py
```

The command above keeps the compatible single-worker `all` mode for local
diagnostics. Production starts the isolated lanes explicitly:

```bash
uv run python scripts/meeting_ai_worker.py --lane finalization
uv run python scripts/meeting_ai_worker.py --lane minutes
uv run python scripts/meeting_ai_worker.py --lane correction
```

Long-recording verification and transcoding are a fourth process. The service
launcher uses `meeting_import_worker.py --mode ingest`; final ASR is claimed by
the common `finalization` lane only after ingestion commits the audio manifest
and durable `final_transcript` job. Running `meeting_import_worker.py` without
`--mode` preserves the legacy combined behavior for diagnostics.

Process one eligible job for diagnostics:

```bash
cd apps/api
uv run python scripts/meeting_ai_worker.py --once --worker-id meeting-ai-debug
```

The processes stop cleanly on `SIGTERM` or `SIGINT`. Multiple workers may use
the same database; a single atomic database update assigns each lease. In the
compatible `all` lane, `final_minutes` is ordered ahead of `correction`. In the
production layout those job kinds are claimed by different processes, so a long
correction request cannot occupy the minutes worker.

## Runtime settings

| Variable | Default | Meaning |
| --- | ---: | --- |
| `SIQ_MEETING_AI_LEASE_SECONDS` | `300` | Job lease duration; must be at least 30 seconds. |
| `SIQ_MEETING_AI_RETRY_DELAY_SECONDS` | `20` | Delay before a retryable job may be claimed again. |
| `SIQ_MEETING_AI_POLL_SECONDS` | `1` | Idle queue poll interval. |
| `SIQ_MEETING_AI_WORKER_LANE` | `all` | Claim lane: `all`, `finalization`, `minutes`, or `correction`. Production passes the lane explicitly. |
| `SIQ_MEETING_AI_CORRECTION_CONFIDENCE` | `0.85` | Minimum confidence for an automatic text patch. |
| `SIQ_MEETING_AI_CORRECTION_DEBOUNCE_SECONDS` | `20` | Maximum wait before a short stable-text correction batch is queued. |
| `SIQ_MEETING_AI_CORRECTION_WINDOW_SEGMENTS` | `5` | Stable sentences per correction batch; allowed range is 3 to 5. |
| `SIQ_MEETING_AI_IMPORT_CORRECTION_WINDOW_SEGMENTS` | `50` | Imported stable segments per deferred correction job; allowed range is 5 to 200. It runs in the correction lane and cannot block final minutes. |
| `SIQ_MEETING_AI_ROLLING_DEBOUNCE_SECONDS` | `45` | Minimum interval between rolling-minutes jobs. |
| `SIQ_MEETING_AI_ROLLING_MIN_NEW_SEGMENTS` | `3` | Minimum new stable sentences before rolling minutes are queued. |
| `SIQ_MEETING_AI_WORKER_ID` | generated | Optional stable process identity. |
| `SIQ_MEETING_AI_LOG_LEVEL` | `INFO` | Worker process log level. Diagnostics are redacted. |
| `SIQ_MEETING_MODEL_CATALOG_TTL_SECONDS` | `15` | Server-side model health cache lifetime; allowed range is 1 to 300 seconds. |
| `SIQ_MEETING_DEFAULT_MODEL_REF` | none | Opaque model reference preferred by new meeting/import forms and eligible auto-selection. It must exist in the generated target pool; users can still select any other available model. |
| `SIQ_MEETING_PROVIDER_CREDENTIAL_FILES` | project and user Hermes `.env` files | Optional path-separated list of trusted provider credential files used by isolated meeting gateways. |
| `SIQ_MEETING_FINAL_ASR_URL` | derived | Optional explicit internal `/v1/finalize-window` URL. By default it is derived from `SIQ_MEETING_ASR_WS_URL`. |
| `SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN` | none | Required internal speech-service credential when final ASR is configured. |
| `SIQ_MEETING_FINAL_ASR_CHUNK_PAGE_SIZE` | `64` | Number of manifest rows fetched per database page. |
| `SIQ_MEETING_FINAL_ASR_WINDOW_SECONDS` | `60` | Maximum PCM duration resident/sent in one final-ASR request. This matches the speech service default limit, reduces cross-window speaker namespaces, and does not change realtime capture frames or streaming decoder cadence. |
| `SIQ_MEETING_FINAL_ASR_MAX_CHUNK_BYTES` | `640000` | Maximum verified manifest chunk read in one operation. |
| `SIQ_MEETING_FINAL_ASR_TIMEOUT_SECONDS` | `60` | Timeout for one bounded speech request. |
| `SIQ_MEETING_FINAL_ASR_MAX_RESPONSE_BYTES` | `2097152` | Maximum response bytes accepted per window. |
| `SIQ_MEETING_FINAL_ASR_MAX_SEGMENTS` | `50000` | Maximum final segments accepted for one meeting job. |
| `SIQ_MEETING_FINAL_ASR_MAX_CONCURRENCY` | `2` | Maximum independent windows held and decoded concurrently by one API worker; allowed range is 1 to 8. Keep this at or below speech-service and downstream model capacity. |
| `SIQ_MEETING_FINAL_ASR_WINDOW_OVERLAP_MS` | `2000` | Audio overlap between adjacent independent windows. It may not exceed half the window duration. |
| `SIQ_MEETING_SPEECH_FINALIZATION_MAX_SESSIONS` | `2` | Global speech-service cap shared by ordered and independent finalization decodes. Capacity exhaustion is retryable. |
| `SIQ_MEETING_SPEECH_FINALIZATION_MAX_CACHED_WINDOWS` | `2048` | Maximum completed/in-flight independent-window idempotency entries retained by one speech-service process. |
| `SIQ_MEETING_SPEECH_HTTP_FINALIZER_MAX_CONCURRENCY` | `2` | Downstream FunASR HTTP decode cap. This must have measured model capacity before being raised. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_EMBEDDING_URL` | derived | Internal `/v1/speaker/embedding` endpoint for whole-meeting diarization. HTTPS is required except on loopback. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_FINAL_DIARIZER_REF` | none | Exact identity from the speech service `/health` `diarizer_ref`. Required and report-bound only for validated cross-key auto-merge. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_ENCODER_REF` | `iic/speech_eres2netv2_sv_zh-cn_16k-common` | Exact encoder identity required in every scoped embedding response. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_POLICY_JSON` | unvalidated defaults | Frozen cross-key merge schema, version, thresholds, validation-report hash, and validation decision. Unknown fields or invalid combinations fail worker startup. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT` | none | Absolute path to the immutable passing report whose raw SHA-256 equals the policy hash. Required only when cross-key auto-merge is enabled. |
| `SIQ_MEETING_SPEAKER_RECLUSTER_AUTO_APPLY_ENABLED` | `0` | Independent operator authorization for embedding-based cross-key automatic merge. It does not control base final-ASR diarization. |

Final-ASR latency is observable without meeting or user labels. Monitor
`meeting_final_asr_window_processing_seconds` for per-window decode time,
`meeting_final_asr_job_processing_seconds` for whole-job wall time, and
`meeting_final_asr_window_total{result=...}` for bounded success/retry/permanent
outcomes. The shipped alert fires when the 15-minute window P95 exceeds 30
seconds; this is an operational warning, not a substitute for the release RTF
evaluation over authorized recordings.

Hermes target failures retry only the AI job. They do not change the meeting
capture state, stable transcript, audio, or ASR availability. A pinned target
is never replaced by another target. Model selection mode `none` records a
skipped AI job without calling Hermes. Each retry uses an attempt-scoped Hermes
session so a failed prompt/output cannot accumulate in the next model context.
Minutes evidence is stored only with canonical segment UUIDs. A model-provided
ordinal is mapped only when it exactly identifies one segment in the frozen
input; unknown or ambiguous evidence still fails closed.

Final-ASR transport/capacity failures likewise retry only `final_transcript`.
Stable ASR text remains readable. Successful final ASR writes
`final_asr_review` revisions and a timestamp-alignment artifact; segments with
human locks or manual/revert revisions are never overwritten. A separate
`speaker_recluster` job then applies anonymous merge/split mappings and emits
auditable mapping events. Manual and user-confirmed voiceprint names are not
automatically reassigned. Only after reclustering succeeds or completes in an
explicit no-change mode is `final_minutes` queued.

The unvalidated default recluster policy accepts up to 128 tracks and selects
at most 512 bounded samples per meeting. Exceeding either cap degrades the
global embedding stage without losing the base final-ASR transcript. The
transcription windows and whole-meeting speaker session are intentionally
separate: final ASR remains independently parallelizable, while temporary
track embeddings are clustered once across the full meeting. Review-only
proposals are stored in the `speaker_recluster` artifact and appear under the
meeting detail's speaker panel; an operator must inspect the prefilled merge
dialog before applying them. Candidate edges below the merge threshold are not
materialized as review proposals, which keeps the durable artifact bounded as
track capacity grows.

The protected API `/metrics` exposes durable, cross-process recluster results
as `meeting_speaker_recluster_durable_total{result="succeeded|degraded|retry_wait|failed"}`.
Decision categories are
`meeting_speaker_recluster_decision_durable_total{result="auto_merge|auto_split|review_proposal|protected_skip|unchanged"}`.
Each decision series counts completed recluster runs that emitted that category;
the event payload may contain an aggregate count, but identifiers and reason
codes never become Prometheus labels. A `degraded` run completed the durable
pipeline while the global embedding stage explicitly fell back; it is not the
same as a failed job.

`SIQMeetingSpeakerReclusterRetryStalled` warns when durable retry wait persists
for five minutes. `SIQMeetingSpeakerReclusterFailed` is critical for terminal
failure. A degraded run does not page because it preserves the base diarization
and is expected while the embedding endpoint or validated auto-merge is off.

## Speaker recluster release policy

The finalization lane first keeps the final-ASR service's provisional
`speaker_track_key` partition as the base diarization. It then sends bounded PCM slices to
`SIQ_MEETING_SPEAKER_RECLUSTER_EMBEDDING_URL` with the internal speech-service
token, `purpose=diarization`, and a meeting/run scope. The endpoint must not
contain credentials, query parameters, or fragments. Its response is accepted
only when the scope and `SIQ_MEETING_SPEAKER_RECLUSTER_ENCODER_REF` match and
`persisted=false` is explicit.

Every final-ASR window also returns the speech engine's stable, non-secret
`diarizer_ref` fingerprint. The API rejects mixed values across a run and writes
the observed value into the final alignment artifact. Cross-key automatic
mapping is allowed only when that observed value exactly matches both the
configured policy and the approved report; an old/missing or mismatched value
degrades to review-only behavior.

These diarization embeddings are temporary in-process values. Neither the raw
sample vectors nor per-track aggregate vectors are written to PostgreSQL,
SQLite, Milvus, artifacts, logs, or metrics. Only durable track mappings,
review proposals, policy/report provenance, and low-cardinality counts cross
the worker boundary. Milvus remains for its existing retrieval use cases; it is
not a speaker-recluster store.

Keep the checked-in environment examples unvalidated and leave the independent
operator flag off. Use the authorized holdout annotations to create a redacted
report in the restricted evidence store:

```bash
python scripts/meeting/evaluate_diarization_release.py \
  --evidence-manifest /secure/evidence/recluster-evidence-manifest.json \
  --reference /secure/evidence/recluster-reference.rttm \
  --hypothesis /secure/evidence/recluster-hypothesis.rttm \
  --output /secure/evidence/recluster-validation-report.json \
  --require-passing
sha256sum /secure/evidence/recluster-validation-report.json
```

The hypothesis must be the end-to-end output after the base final-ASR
`speaker_track_key` partition and the global embedding-based cross-key merge,
not an embedding-only intermediate result. The strict evidence manifest binds
the raw annotation hashes, authorization, independent holdout, candidate
commit, final diarizer identity, encoder identity, observed sample counts, and
exact cross-key merge thresholds. The report's `source_sha256` identifies the
two raw annotation inputs, `evidence_manifest_sha256` binds that metadata, and the separate
`sha256sum` output is the value copied into
`validation_artifact_sha256`. Archive both values and the report location in
the release record. Do not put the private annotations or validation report in
general CI artifacts.

The durable worker uses
`siq.meeting.final_asr.independent_window.v1`. Every request is self-contained,
uses the durable job ID as its stable run ID, sets `final_window=true`, and may
complete out of order. The speech service caches `(run_id, window_index)` by
content checksum: an exact retry shares or replays the original task, while
changed content under the same key fails with a conflict. The API restores
timestamp order after all bounded requests complete.

Adjacent windows overlap by the configured duration. Word timestamp midpoints
determine ownership at the midpoint of each overlap; a segment without word
timestamps is assigned by its own midpoint. Boundary words are trimmed and
segment tokens include the source window so retries and cross-window token
indices cannot collide. Manifest gaps reset overlap ownership. Each independent
window has an anonymous speaker namespace; the following whole-meeting
recluster stage is responsible for evidence-gated cross-window speaker mapping.

The checked-in defaults align API concurrency, speech decode capacity, and the
HTTP finalizer at two. A 2026-07-15 read-only rerun using the former 30-second
window processed 1019.873 seconds of verified PCM as 37 overlapping windows in
272.560 seconds (RTF 0.2672), down from the historical 331.494 seconds / RTF
0.325 by about 17.8%. This proves that the independent-window path produces a
real speedup on the current HTTP finalizer, but it still misses the RTF 0.25
release threshold and one recording is not a P95 report. The current 60-second
default reduces window boundaries and requires a new authorized accuracy/RTF
evaluation; do not reuse the old timing result as release evidence. More
model-instance capacity must be measured before raising concurrency above two.

Embedding-based cross-key automatic merge requires all three controls at the same time:

1. The report passes every fixed quality, coverage, and sample-size gate.
2. `SIQ_MEETING_SPEAKER_RECLUSTER_POLICY_JSON` uses a version containing
   `.validated.`, sets `auto_apply_validated=true`, includes the approved
   64-character lowercase report SHA-256, and freezes the reviewed thresholds;
   the report policy also exactly matches the configured final diarizer and
   embedding encoder references.
3. The report file is mounted at
   `SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT`, and an operator separately sets
   `SIQ_MEETING_SPEAKER_RECLUSTER_AUTO_APPLY_ENABLED=1` under the approved
   rollout change.

Changing the final-ASR window, `max_tracks`, `max_total_samples`, diarizer,
encoder, or merge thresholds requires a new end-to-end hypothesis and
validation report. Existing reports remain historical evidence and cannot
authorize the changed policy automatically.

A missing endpoint/token degrades to no embedding-based cross-key merge; the
base final-ASR diarization remains available. A malformed policy,
a validated policy without a report hash, or a validated policy while the
operator flag remains off fails closed. Turning on the operator flag alone does
nothing while `auto_apply_validated=false`. This gate does not authorize an
automatic split and does not disable or validate the base final-ASR partition.

## Model catalog

`GET /api/meetings/v1/models` returns only opaque model references and
redacted health information. It never returns gateway URLs or credentials.
Results use the short server-side TTL above. An administrator can force a new
runtime health probe without changing any Hermes profile:

```bash
curl -X POST \
  -H "Authorization: Bearer $SIQ_ACCESS_TOKEN" \
  'http://127.0.0.1:18081/api/meetings/v1/models/refresh?purpose=meeting_postprocess'
```

The refresh route requires the meeting administrator permission. Model target
discovery remains read-only and does not write the active Hermes profile.

Each isolated gateway imports only the credential environment variable named
by its selected target. It first uses an already exported process variable;
otherwise it reads that one assignment from the trusted credential files.
Credential files must be regular, non-symlink files owned by the service user
with no group or other permissions. Missing credentials and unsafe file modes
fail closed before the gateway process starts. Credential values are never
written to the target catalog, generated runtime config, logs, or source env
examples.

For meetings whose language is `zh-CN`, each newly generated rolling or final
minutes artifact is instructed to organize its overview, topics, chapters,
decisions, questions, risks, actions, viewpoints, and keywords primarily in
Simplified Chinese. Personal and company names, product names, technical
abbreviations, and English words actually spoken may remain unchanged.
Transcript quotations preserve their original language. This applies only to
new generation and explicit regeneration; existing artifact versions are not
migrated or rewritten.

## Recovery

Expired `leased` and `running` jobs are claimable by another worker, including
a process crash during the last configured attempt. Retryable
failures enter `retry_wait` until the configured delay; terminal failures stay
`failed` and can be queued through the meeting job retry API when allowed.
Every successful AI result is tied to its immutable model snapshot.
