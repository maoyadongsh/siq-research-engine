# SIQ Meeting Speech Service

This directory contains the isolated speech process for the additive meeting-transcription domain. It does not import, start, stop, or modify the existing FunASR short-voice service on port `8899`.

The service is an internal model boundary. A browser must connect to the authenticated SIQ meeting stream gateway, not directly to this process. The gateway owns user authorization, one-time stream tickets, durable audio storage, stable-segment transactions, and public event cursors. This service owns bounded PCM ingestion, sequence acknowledgement, VAD, streaming partials, sentence-final ASR, and optional anonymous-speaker hooks.

## Runtime contract

- Default bind: `127.0.0.1:8901`.
- WebSocket: `/v1/stream/{meeting_id}`.
- Health: `/health`, `/health/live`, and `/health/ready`.
- Low-cardinality metrics: `/metrics`.
- Internal authentication: `X-SIQ-Service-Token`; mandatory for `production`, `prod`, and `docker` profiles.
- Browser `Origin` headers are rejected unless explicitly listed in `SIQ_MEETING_SPEECH_ALLOWED_ORIGINS_CSV`.
- FunASR load errors leave the service alive but not ready. There is no automatic Mock fallback.
- Sentence finalization can use the local Paraformer model or an explicitly configured, bounded HTTP call to the existing `8899 /asr`; HTTP failure never silently changes backends.
- Mock mode requires `SIQ_MEETING_SPEECH_ALLOW_DEGRADED_MOCK=1`, is reported as degraded/non-production-capable, and is rejected in protected profiles.

The gateway should use:

```text
SIQ_MEETING_ASR_WS_URL=ws://127.0.0.1:8901/v1/stream/{meeting_id}
SIQ_MEETING_ASR_SERVICE_TOKEN=<same value as SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN>
```

Do not put the service token in the WebSocket URL. Send it only as the internal request header.

## WebSocket v1

The first message must be JSON:

```json
{
  "type": "stream.start",
  "schema_version": "siq.meeting.stream.v1",
  "meeting_id": "6f71e3f8-a550-47c0-b5b5-2cb8cae539f8",
  "client_stream_id": "4da63e17-30d0-443f-937f-d5da3ac36313",
  "stream_epoch": 1,
  "audio": {
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "chunk_ms": 200
  },
  "last_acked_sequence": -1,
  "hotwords": [],
  "hotword_version": 3
}
```

New epochs use `last_acked_sequence=-1`. A reconnect may reuse the same meeting/client/epoch tuple within `SIQ_MEETING_SPEECH_RESUME_TTL_SECONDS`; the client ACK cannot be ahead of retained server state. If state expired, the service returns `RESUME_STATE_NOT_FOUND`. The gateway must open a new epoch and replay its durably stored PCM rather than pretending ASR context survived.

Audio is `16 kHz`, mono, signed `PCM16LE`. The recommended frame is 200 ms; v1 accepts 100-1000 ms by default. Binary payloads use a fixed 32-byte network-byte-order header:

```text
struct !4sBBHIQQI

offset  size  field
0       4     magic = ASCII "SIQA"
4       1     version = 1
5       1     flags (bit 0 END_OF_STREAM, bit 1 DISCONTINUITY)
6       2     header_size = 32
8       4     stream_epoch (uint32)
12      8     sequence (uint64, starts at 0 and increases by one)
20      8     capture_time_ms (uint64 meeting monotonic timeline)
28      4     payload_size (uint32)
32      N     PCM16LE payload
```

Unknown versions/flags, odd PCM byte counts, oversized frames, conflicting duplicate sequences, and unbounded gaps are rejected. Out-of-order frames are retained only inside the configured frame/byte window. `DISCONTINUITY` finalizes any current sentence and resets streaming model context without compressing the meeting timeline.

Text controls all use `siq.meeting.stream.v1`:

- `stream.pause`
- `stream.resume`
- `stream.stop`
- `stream.heartbeat`
- `stream.resume_request` with `last_acked_sequence`
- `stream.hotwords.update` with request, version, boundary sequence, and immutable terms

Internal output uses `siq.meeting.speech.event.v1`. Important event types are `stream.ready`, `audio.ack`, `audio.gap.detected`, `flow.control`, `asr.partial`, `asr.final`, `hotwords.update.ack`, `speaker.track.observed`, `pipeline.degraded`, and `error`. ACK payloads contain `stream_epoch`, `ack_sequence`, `duplicate`, `buffered_frames`, and `buffered_bytes`.

`asr.partial` is ephemeral. `asr.final` deliberately carries `durability="gateway_pending"` and has no durable cursor. The stream gateway must atomically write the stable segment and outbox event, then publish the public `transcript.segment.stable` envelope with its database cursor. Treating a raw speech-service final as durable would violate the meeting taskbook.

### Low-latency and live hotwords

The browser captures 200 ms PCM frames and drains them every 160 ms when it needs to catch up. The outbox remains bounded to 600 frames, preserving the prior 120-second memory/durability window. Paraformer online defaults to the accuracy-first `chunk_size=0,10,5`; for 200 ms input the advertised audio accumulation bound before a complete online window is 600 ms. Model inference and network time remain separately observable through partial latency metrics.

An active meeting can update its immutable lexicon without reconnecting:

```json
{
  "type": "stream.hotwords.update",
  "schema_version": "siq.meeting.stream.v1",
  "request_id": "11111111-1111-4111-8111-111111111111",
  "hotword_version": 4,
  "effective_sequence": 42,
  "hotwords": ["Nemotron"]
}
```

The service first emits `hotwords.update.ack` with `status="queued"`. Before decoding `effective_sequence`, it finalizes any buffered older segment, swaps the decoder vocabulary and cache, then emits `status="applied"` with `applied_sequence`. Frames below the boundary retain the previous version even when they arrive after the control message. Every partial and final carries `hotword_version`; the gateway persists that recognition-time value on the stable segment. Up to eight ordered updates can wait while paused, and request IDs are idempotent with conflicting reuse rejected.

## Bounded behavior

The service never accumulates a whole meeting in memory.

- Per-frame PCM and duration limits are validated before inference.
- Sequence reordering has independent frame-count, byte-count, and gap limits.
- Sentence PCM is capped by `SIQ_MEETING_SPEECH_MAX_SEGMENT_SECONDS` and force-finalized at the boundary.
- Disconnected model sessions have a short, bounded TTL and global resident-session cap.
- Synchronous FunASR calls run off the FastAPI event loop and have an async timeout.
- The connection itself provides backpressure; full bounded queues return explicit `flow.control`/error events.
- Speaker is an optional hook. `speaker_adapter=funasr` uses ERes2NetV2 embeddings and a per-session, bounded cosine-centroid cluster to emit anonymous `speaker-N` tracks. It never guesses a real identity, persists an embedding, or shares centroids across meetings.

### Final-ASR windows

`POST /v1/finalize-window` is an internal, token-protected endpoint used only by
the durable meeting worker after capture stops. Each request contains one
bounded 16 kHz mono PCM16 window and these headers:

- `X-SIQ-Finalization-Id`: UUID for one durable processing attempt.
- `X-SIQ-Finalization-Protocol`: ordered legacy mode or
  `siq.meeting.final_asr.independent_window.v1`.
- `X-SIQ-Window-Index`: stable window index beginning at zero.
- `X-SIQ-Window-Start-Ms`: position on the meeting timeline.
- `X-SIQ-Discontinuity`: whether a manifest gap precedes this window.
- `X-SIQ-Final-Window`: whether this is the last window.
- `X-SIQ-Language` and bounded JSON `X-SIQ-Hotwords`.

Independent mode requires `X-SIQ-Final-Window: true` because every overlapping
window is a complete decoder domain. Windows may arrive and complete out of
order up to `FINALIZATION_MAX_SESSIONS`. An exact repeated `(run ID, index)`
shares or replays the checksum-bound task; changed content returns 409. Cached
tasks have a bounded count and TTL. The ordered protocol remains available for
older callers and retains bounded decoder/anonymous-diarization state between
contiguous windows. Responses identify the accepted protocol and contain final
text, timestamps and anonymous track keys, never audio or speaker embeddings.

Relevant settings are
`SIQ_MEETING_SPEECH_FINALIZATION_ENDPOINT_ENABLED` (default true),
`SIQ_MEETING_SPEECH_FINALIZATION_MAX_WINDOW_SECONDS` (30),
`SIQ_MEETING_SPEECH_FINALIZATION_MAX_SESSIONS` (2),
`SIQ_MEETING_SPEECH_FINALIZATION_MAX_CACHED_WINDOWS` (2048), and
`SIQ_MEETING_SPEECH_FINALIZATION_SESSION_TTL_SECONDS` (300).
When the finalizer is `funasr_http`, also keep
`SIQ_MEETING_SPEECH_HTTP_FINALIZER_MAX_CONCURRENCY` aligned with measured
downstream model capacity.

## Start

The existing `funasr-vllm` Conda environment already contains FastAPI, NumPy, Uvicorn, and the local FunASR checkout. Enable both product flags and provide a token before starting:

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/meeting-speech
export SIQ_MEETINGS_ENABLED=1
export SIQ_MEETING_REALTIME_ASR_ENABLED=1
export SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN='set-outside-source-control'
./start_meeting_speech.sh
```

The script remains a no-op while either feature flag is off. It defaults to port `8901`, CPU Paraformer streaming/final models, FSMN VAD, and punctuation. Model loading happens in the background; liveness can be healthy while readiness reports `initializing` or `unavailable`.

To reuse the existing high-accuracy `8899` service only at VAD sentence boundaries, configure:

```bash
export SIQ_MEETING_SPEECH_FINALIZER=funasr_http
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_URL=http://127.0.0.1:8899/asr
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_HEALTH_URL=http://127.0.0.1:8899/openapi.json
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_MAX_CONCURRENCY=1
```

Each sentence is wrapped as an in-memory 16 kHz mono WAV and sent using the existing multipart fields (`file`, `language`, `hotwords`, `spk=true`, `timestamp=true`). Word timestamps and per-request `segments[].speaker` labels are preserved as `source_speaker_hints`. Those hints are evidence only: because every sentence is a separate 8899 request, `SPK0` is not treated as the same person across sentences. The sentence buffer, concurrent calls, wait time, response bytes, redirects, and request timeout are bounded. The adapter does not call `8899 /ws`, does not change the existing service process, and does not send full-meeting audio. Real-time partials still come from this service's independent Paraformer online model.

### Anonymous speaker and voiceprint worker boundary

Enable evaluated anonymous session clustering with:

```bash
export SIQ_MEETING_SPEECH_SPEAKER_ADAPTER=funasr
export SIQ_MEETING_SPEECH_SPEAKER_MODEL=iic/speech_eres2netv2_sv_zh-cn_16k-common
```

Tracks are capped per session and disappear when retained stream state expires. Track keys include the stream epoch, so a new capture epoch cannot collide with an earlier epoch's anonymous labels. A segment shorter than the configured quality floor, below the RMS floor, or above the clipping-ratio ceiling remains anonymous. New tracks require repeated candidate evidence; borderline matches may be assigned but cannot update the track's bounded robust prototype window. The assignment, update, candidate, Top-2 margin, confirmation, expiry, and signal-quality bounds are independently configurable. If the encoder fails, ASR final text is still returned with an explicit speaker degradation marker.

`/metrics` exposes only fixed speaker-quality outcomes. Use
`meeting_speech_speaker_assignment_total{result="assigned|unassigned|failed"}`
to distinguish a successful assignment, a deliberately anonymous result, and
an adapter failure. Use
`meeting_speech_speaker_track_total{result="created|reused"}` to monitor
fragmentation pressure. These metrics contain no meeting, user, track, name,
text, or embedding labels. `unassigned` includes quality rejection, ambiguous
matches, and candidates awaiting confirmation; investigate it together with
the evaluated policy and audio-quality evidence rather than treating every
sample as a model fault.

`POST /v1/speaker/embedding` is a separate internal worker capability and defaults off. Enabling it requires a configured internal service token even in local mode. The caller must send:

- `X-SIQ-Service-Token`.
- `X-SIQ-Voiceprint-Consent: <UUID>` for an authorization already validated by the business worker.
- `X-SIQ-Voiceprint-Purpose: enrollment` or `match`.
- `X-SIQ-Audio-Encoding: pcm_s16le` or `wav`.
- A 1-15 second 16 kHz mono PCM16 sample by default.

The endpoint returns a normalized embedding and `persisted=false`; it never stores audio, the consent reference, or the vector. The business worker remains responsible for object authorization, consent state, encryption, retention, matching thresholds, audit, revoke, and delete. This model service is not a consent authority.

The same endpoint supports meeting-scoped, non-identity diarization for an already authorized internal finalization worker. Send `X-SIQ-Speaker-Purpose: diarization`, `X-SIQ-Meeting-ID: <UUID>`, and `X-SIQ-Diarization-Run-ID: <UUID>` instead of either `X-SIQ-Voiceprint-*` header. Mixed voiceprint and diarization scopes are rejected. The response uses `siq.meeting.speaker_embedding.v1`, echoes the meeting/run scope, sets `purpose=diarization` and `persisted=false`, and remains subject to the same token, duration, concurrency, and in-memory processing bounds.

The voiceprint worker should configure `SIQ_MEETING_SPEAKER_EMBEDDING_URL=http://127.0.0.1:8901/v1/speaker/embedding` and reuse the internal service token only in its server-side environment.

For a local protocol smoke test without loading models, explicitly opt into Mock:

```bash
export SIQ_MEETING_SPEECH_ADAPTER=mock
export SIQ_MEETING_SPEECH_ALLOW_DEGRADED_MOCK=1
./start_meeting_speech.sh
```

Mock output is a protocol test signal, not transcription, and health always identifies it as non-production-capable.

## Test

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/meeting-speech
pytest -q
```

The focused suite does not download or initialize model weights. A separate M0/M2 quality and soak gate must run the real FunASR adapter on authorized audio before production enablement.
