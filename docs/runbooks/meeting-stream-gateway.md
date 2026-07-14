# Meeting Stream Gateway

The meeting stream gateway is an additive, feature-gated WebSocket path. It
accepts one-time producer tickets, persists validated PCM chunks before ASR
acknowledgement, and proxies only the meeting audio protocol to the internal
speech service.

## Process Boundary

Protected deployments default to `SIQ_MEETING_STREAM_GATEWAY_MODE=external`.
The aggregate API then exposes only the authenticated stream/playback ticket
control plane. WebSocket capture and ticket-authenticated Range replay run in
the standalone ASGI process:

```bash
cd apps/api
uv run uvicorn meeting_stream_gateway:app --host 127.0.0.1 --port 18082
```

Route `/api/meetings/v1/sessions/{meeting_id}/audio` at the same public origin
to that process and keep all other `/api/meetings/v1` routes on the aggregate
API. `scripts/meeting/run_meeting_services.sh` starts the gateway when mode is
`external`. Development defaults to `embedded` for focused tests; protected
profiles reject embedded mode. The gateway has no normal meeting REST routes,
does not serve API documentation, and accepts business traffic only through
purpose-bound producer/playback tickets.

Playback endpoints never assemble audio. Stop/finalization must publish the
verified WAV first; until then ticket creation and replay return the stable
`AUDIO_NOT_AVAILABLE` response. This keeps multi-hour audio packaging out of
API and gateway request paths.

## Resource Accounting

Active-meeting capacity is derived from `meeting_stream_leases`, not process
memory. One unexpired lease for a session in `connecting`, `live`, or
`reconnecting` consumes one slot. The gateway excludes the target meeting from
the capacity query, so reconnecting the same meeting updates its existing lease
without consuming another user or global slot.

Normal stop, transport close, and handled protocol/storage/ASR errors expire
the connection lease before closing the WebSocket. If a process is killed or
the database cannot be reached during cleanup, `SIQ_MEETING_STREAM_LEASE_TTL_SECONDS`
is the bounded recovery path. Expired leases and leases for stopped/archived
sessions never count as active, so a crashed process cannot reserve capacity
permanently.

## Stop Completion

`POST /api/meetings/v1/sessions/{meeting_id}/stop` first applies the existing
capture `stop` transition. It then checks the meeting's producer lease:

- An unexpired lease returns `state=stopping`,
  `finalization_path=stream_gateway`, and `audio_status=pending`. The gateway
  remains responsible for packing WAV and applying `mark_stopped`.
- A missing or expired lease lets the REST fallback validate the durable PCM
  manifest, atomically pack WAV with `MeetingAudioStore`, and apply the existing
  `mark_stopped` transition. Repeating `/stop` after the lease TTL is therefore
  the bounded recovery action for a crashed gateway.
- A draft with no PCM chunks still reaches `stopped` and reports
  `audio_status=unavailable`. Its playback endpoint returns the explicit
  `AUDIO_NOT_AVAILABLE` 404.

Both gateway and REST paths use the same owner/meeting storage layout and
idempotent `pack_wav` implementation. `SIQ_MEETING_AUDIO_ROOT` is canonical;
`SIQ_MEETINGS_AUDIO_ROOT` remains a legacy fallback. Invalid or tampered PCM
returns `MEETING_STOP_FINALIZATION_CONFLICT` (409) and leaves the session in
`stopping` for a later retry. Object ownership failures remain 404. This stop
fallback does not invoke or relax the independent `/finalize` state machine.

PostgreSQL capacity decisions use a transaction advisory lock before the lease
upsert. This serializes concurrent gateway processes and prevents two connects
from both observing the last available slot.

## Limits

| Variable | Default | Valid range | Meaning |
| --- | ---: | ---: | --- |
| `SIQ_MEETING_MAX_ACTIVE_PER_USER` | `1` | 1-100 | Concurrent active meeting leases for one owner. |
| `SIQ_MEETING_MAX_ACTIVE_TOTAL` | `4` | 1-1000 | Concurrent active meeting leases across all owners. |
| `SIQ_MEETING_AUDIO_MAX_FRAMES_PER_SECOND` | `20` | 1-1000 | Refill rate for each connection's frame token bucket. |
| `SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND` | `128000` | 32000-16777216 | Refill rate for PCM payload bytes on each connection. |
| `SIQ_MEETING_AUDIO_RATE_BURST_SECONDS` | `2` | 1-10 | Token-bucket burst capacity in seconds. |
| `SIQ_MEETING_AUDIO_MAX_FRAME_BYTES` | `262144` | 1024-4194304 | Absolute payload size accepted by the frame decoder. |
| `SIQ_MEETING_STREAM_LEASE_TTL_SECONDS` | `20` | 5-120 | Crash-recovery lifetime for an unrenewed producer lease. |

The byte limiter counts PCM payload bytes. The independent frame limiter also
bounds empty end-of-stream frames and header amplification. A connection that
exceeds either bucket receives `AUDIO_FRAME_RATE_LIMIT` or
`AUDIO_BYTE_RATE_LIMIT` and closes with policy code `1008`.

`SIQ_MEETING_MAX_ACTIVE_PER_USER` must not exceed the global limit. All values
are parsed at startup through `MeetingSettings`; malformed, zero, negative, or
out-of-range values populate `configuration_errors`, make meeting capabilities
unavailable, and prevent the gateway from accepting streams.

## Capacity Errors

The authoritative capacity check runs when a producer ticket is consumed:

- `MEETING_ACTIVE_LIMIT_PER_USER`: the owner has no remaining slot.
- `MEETING_ACTIVE_LIMIT_TOTAL`: the deployment has no remaining slot.

These are temporary-capacity WebSocket closes (`1013`). Issuing a ticket does
not reserve capacity, and rejected tickets are not marked consumed.

Inspect sanitized limits and configuration status through:

```text
GET /api/meetings/v1/capabilities
```

For stale-capacity diagnosis, inspect unexpired lease rows together with the
session state. Do not delete lease rows while a gateway is live; allowing the
lease to expire or stopping the owning session preserves the single-producer
invariant.
