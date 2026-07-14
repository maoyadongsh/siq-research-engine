# iOS Native Capture Finalization Worker

This worker packages sealed iOS native meeting captures outside API request
processes. It is enabled only when both the meeting domain and
`SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED=1` are enabled. It does not provide a
Swift recorder and does not make Web/PWA background-recording claims.

With the native flag off, API startup does not create, reflect, or require the
native tables. Before enabling it, apply migrations `004`, `005`, `007`, and
`008` in order and run the native migration tests. PostgreSQL is intentionally
fail-closed when an enabled deployment lacks the epoch digest column.

## State Contract

The server keeps capture, ingest, realtime, and finalization checkpoints
separate:

- `pending_upload`: the sealed manifest still has an unaccounted sequence or
  sample range. No WAV or final-transcript job is published.
- `queued`: every declared range is backed by a verified batch or an explicit
  durable unrecoverable gap.
- `processing` / `retry_wait`: a lease owner is registering canonical audio
  chunks or publishing the WAV. Playback is not reported ready.
- `ready`: the canonical WAV is atomically published and hash recorded. Only
  then is the existing `{meeting_id}:finalize:v1` transcript job queued.
- `failed`: the retry limit was reached. Existing native and canonical files
  are retained for diagnosis and controlled retry.

An unrecoverable gap must be owner/capture-token scoped, inside the sealed
manifest boundary, and non-overlapping with persisted audio. The WAV contains
silence for that explicit interval; the checkpoint and durable event continue
to report the gap.

## Start

The unified meeting service group starts this worker automatically when native
capture is enabled:

```bash
SIQ_MEETINGS_ENABLED=1 \
SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED=1 \
scripts/meeting/run_meeting_services.sh
```

Run one recovery or packaging action for diagnostics:

```bash
cd apps/api
uv run python scripts/meeting_native_capture_worker.py \
  --once --worker-id native-finalization-debug
```

Runtime settings:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_BATCH_BYTES` | `8388608` | Maximum accepted PCM payload for one batch. |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_TOTAL_BYTES` | `4294967296` | Hard byte limit for one capture. |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_RETAINED_BYTES_PER_OWNER` | `17179869184` | Hard aggregate byte limit across all retained captures for one owner; must be at least the single-capture limit. |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_DURATION_SECONDS` | `14400` | Hard sample-timeline duration for one capture. |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_ACTIVE_PER_OWNER` | `2` | Maximum concurrently active captures for one owner. |
| `SIQ_MEETING_NATIVE_CAPTURE_MAX_BATCH_CONCURRENCY` | `8` | Process-local batch-ingress concurrency. |
| `SIQ_MEETING_NATIVE_CAPTURE_BATCH_QUEUE_TIMEOUT_SECONDS` | `2` | Ingress wait before returning a bounded busy response. |
| `SIQ_MEETING_NATIVE_CAPTURE_MIN_STORAGE_FREE_BYTES` | `536870912` | Absolute free-space floor checked before a batch write. |
| `SIQ_MEETING_NATIVE_FINALIZATION_LEASE_SECONDS` | `300` | Processing lease and crash-takeover bound. |
| `SIQ_MEETING_NATIVE_FINALIZATION_RETRY_DELAY_SECONDS` | `20` | Delay after retryable storage or integrity failures. |
| `SIQ_MEETING_NATIVE_FINALIZATION_POLL_SECONDS` | `1` | Idle queue polling interval. |
| `SIQ_MEETING_NATIVE_FINALIZATION_MAX_ATTEMPTS` | `5` | Automatic attempts before terminal `failed`. |

## Metrics And Capacity

Scrape the protected API `/metrics` endpoint and sum process counters across
all API replicas. Native-capture metrics deliberately expose no capture,
meeting, user, token, device-installation, storage-key, or filesystem-path
labels.

| Metric | Type | Meaning |
| --- | --- | --- |
| `meeting_native_capture_operational` | gauge | `1` only when both meeting and native capture are enabled and configuration is valid. |
| `meeting_native_capture_storage_probe_success` | gauge | Capacity probe result for the configured storage filesystem. |
| `meeting_native_capture_storage_free_bytes` | gauge | Free bytes on that filesystem; the path is never a label. |
| `meeting_native_capture_storage_required_free_bytes` | gauge | Safety headroom: at least 5 GiB and at least twice one configured maximum capture. |
| `meeting_native_capture_auth_failure_total{reason}` | counter | Bounded token, device, or scope authentication failures. |
| `meeting_native_capture_batch_total{result}` | counter | Accepted, replayed, conflicted, invalid, or capacity/storage-rejected batches. |
| `meeting_native_capture_batch_bytes_total{result}` | counter | Accepted or replayed ingress bytes. |
| `meeting_native_capture_storage_rejection_total{reason}` | counter | Writes rejected for unavailable storage, low space, quota, capacity, or integrity. |
| `meeting_native_capture_durable_batch_count` | gauge | Batches represented in the durable manifest. |
| `meeting_native_capture_durable_batch_bytes` | gauge | Bytes represented in the durable manifest. |
| `meeting_native_capture_gap_durable_total{reason}` | gauge | Durable unrecoverable gaps by the fixed gap-reason enum. |
| `meeting_native_capture_finalization_backlog{state}` | gauge | `pending_upload`, `queued`, `processing`, and `retry_wait` counts. |
| `meeting_native_capture_finalization_oldest_age_seconds{state}` | gauge | Age of the oldest finalization in each backlog state. |
| `meeting_native_capture_finalization_failed` | gauge | Terminally failed finalizations requiring operator action. |

The database gauges are additive-schema safe: they are omitted when the native
tables are not installed. This is expected while the feature flag is off or
during a rolling migration. Do not interpret an absent native database series
as zero without also checking migration state and
`meeting_native_capture_operational`.

The critical free-space threshold is the larger of 5 GiB and twice the
configured maximum capture size. This reserves space for uploaded PCM, the
canonical WAV, and retry overlap. The owner retained-byte setting separately
serializes and caps all retained captures for one owner. Neither limit replaces
a filesystem-wide capacity forecast for concurrent owners and retention
backlog.

## Alert Triage

`SIQMeetingNativeCaptureAuthFailureBurst` indicates more than 20 bounded auth
failures in five minutes. Compare the fixed `reason` values and deployment
time, but never log or inspect bearer tokens or raw device identifiers.

For `SIQMeetingNativeCaptureBatchConflict`, stop automatic retry of the
conflicting sequence and compare only manifest coordinates, byte counts, and
SHA-256 values. Reusing a sequence for different audio is not a recoverable
replay.

For `SIQMeetingNativeCaptureStorageProbeFailed`,
`SIQMeetingNativeCaptureStorageLow`, or
`SIQMeetingNativeCaptureStorageWriteRejected`, verify mount availability,
permissions, inode/byte capacity, and retention-worker health. Preserve
existing batches. Do not delete capture directories by hand to clear the
alert.

For an old `pending_upload`, inspect the checkpoint's aggregate missing ranges
and confirm whether the device outbox is still retrying. For old `queued`,
`processing`, or `retry_wait` work, inspect worker lease and retry state before
using the controlled retry command below. A terminal `failed` alert remains
active until the underlying cause is repaired and the capture is explicitly
retried or retained under the incident policy.

`SIQMeetingNativeCaptureGapRecorded` is evidence loss, not merely worker
latency. Confirm that the client declared one of the fixed unrecoverable gap
reasons and record the release-impact decision without copying transcript or
audio content into the incident ticket.

Repository checks validate the monitoring YAML structure and dashboard JSON.
An official `promtool` rule check, notification routing, receiver credentials,
and a real alert delivery have not been verified by these checks and remain
deployment evidence.

## Recovery

Expired `processing` leases are reclaimable even if the crashed process was on
its last configured attempt. Canonical chunk names, provenance links, WAV
publication, meeting stop transitions, and the final-transcript idempotency key
are all safe to replay.

For `pending_upload`, inspect the checkpoint and resend missing batches in
order. Declare a gap only when the native client has determined that the local
audio is irrecoverable; do not use gaps to bypass a temporary upload failure.

The retention worker also scans old terminal native captures whose meeting
never reached `stopped` after seal. It deletes native PCM only when there is no
valid upload token, active meeting job, runnable/leased finalization, or
canonical audio link. Active captures and unique device-side copies are never
eligible. Transcript, manifest entries, epochs, and gaps remain as audit
metadata after server audio expiry.

For `retry_wait` or `failed`, first repair the reported storage/integrity cause.
Then reset only that finalization:

```bash
cd apps/api
uv run python scripts/meeting_native_capture_worker.py \
  --retry-capture "$CAPTURE_ID" \
  --worker-id native-finalization-recovery
```

The retry command recalculates coverage. It returns the job to `pending_upload`
when data is still missing and cannot force playback ready.

## Integrity And Privacy

Each native source batch is read with the configured batch-size bound and
verified against its durable byte count and SHA-256. Symlinks, path traversal,
cross-owner storage keys, conflicting canonical coordinates, and overlapping
audio are rejected. A provenance link records
`capture_id + epoch + sequence -> MeetingAudioChunk`; matching WebSocket audio
is reused rather than registered twice.

Logs and diagnostics must not include audio bytes, capture tokens, JWTs,
absolute storage paths, transcript text, or playback tickets. Feature rollback
stops the worker and disables the native flag; it does not delete captures,
gaps, provenance, canonical chunks, or WAV files. Schema additions are in
`apps/api/migrations/004_create_meeting_native_capture_tables.sql`,
`005_create_meeting_native_capture_finalization_tables.sql`, and
`007_create_meeting_native_capture_manifest_entries.sql`, followed by
`008_add_meeting_native_capture_epoch_manifest_digest.sql`. Apply and verify all
four migrations before enabling the flag.
