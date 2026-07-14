# Meeting Exports

Meeting exports are private, owner-scoped artifacts. The API only creates a
durable queued job and returns `202`; it never renders an export in the request
path. The independent worker claims and renders queued or interrupted jobs,
while the UI polls only as long as an export remains queued or running.

## API

All routes require `meeting.export` and enforce the meeting owner in the
database query.

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/meetings/v1/sessions/{meeting_id}/exports` | Create an idempotent queued export and return `202`. |
| `GET` | `/api/meetings/v1/sessions/{meeting_id}/exports` | List export records without download tokens. |
| `GET` | `/api/meetings/v1/sessions/{meeting_id}/exports/{export_id}` | Read status and issue a short download URL when ready. |
| `POST` | `/api/meetings/v1/sessions/{meeting_id}/exports/{export_id}/ticket` | Issue a fresh one-time download URL. |
| `GET` | `/api/meetings/v1/sessions/{meeting_id}/exports/{export_id}/download?ticket=...` | Download once as an attachment. |

Transcript exports support `txt`, `markdown`, `docx`, `srt`, `vtt`, and
`json`, with `transcript_source=display` or `asr`. Minutes exports support
`markdown`, `docx`, and `json` and require `artifact_id` or
`artifact_version`. If a version is ambiguous between rolling and final
minutes, the caller must provide the artifact ID.

DOCX files are generated locally as Office Open XML attachments. They include
the selected transcript layer, timestamps, speaker labels, artifact version,
and source-segment evidence. Chinese body and heading styles explicitly use
Microsoft YaHei, and invalid XML/control characters are removed before the
document is written. `pdf` requests are accepted but return a durable failed
export with `EXPORT_FORMAT_NOT_AVAILABLE`; PDF availability does not block any
supported format.

## Worker

The worker is required for queued exports to become downloadable. A worker
failure cannot block the API request or meeting transcript persistence; queued
jobs remain recoverable through their durable lease state.

From the repository root:

```bash
cd apps/api
uv run python scripts/meeting_export_worker.py
```

Process at most one recovery job:

```bash
cd apps/api
uv run python scripts/meeting_export_worker.py --once --worker-id export-debug
```

## Configuration

| Variable | Default | Meaning |
| --- | ---: | --- |
| `SIQ_MEETING_EXPORT_ROOT` | API data root | Private generated-file directory. |
| `SIQ_MEETING_EXPORT_MAX_BYTES` | `20971520` | Maximum generated file size. |
| `SIQ_MEETING_EXPORT_MAX_SEGMENTS` | `200000` | Maximum transcript segments per export. |
| `SIQ_MEETING_EXPORT_TICKET_TTL_SECONDS` | `120` | One-time ticket lifetime. |
| `SIQ_MEETING_EXPORT_LEASE_SECONDS` | `120` | Recovery worker lease. |
| `SIQ_MEETING_EXPORT_RETRY_DELAY_SECONDS` | `20` | Delay for retryable storage failures. |
| `SIQ_MEETING_EXPORT_WORKER_ID` | generated | Optional stable worker identity. |
| `SIQ_MEETING_EXPORT_LOG_LEVEL` | `INFO` | Worker log level. |

## Security

- Storage keys are server-generated from validated IDs and never accepted from
  the client.
- Files are created with private permissions and verified by size and SHA-256
  before every ticket issue and download.
- Download tickets are hashed at rest, owner-bound, short-lived, and consumed
  atomically once.
- Filenames use only the meeting title's first line and cannot inject response
  headers or path separators.
- Markdown and subtitle control syntax is escaped. DOCX text is inserted only
  through the document object model after XML/control-character filtering.
  JSON and DOCX remain attachments with `nosniff`, a sandbox policy, and
  no-store caching.
- Audit events record format, artifact version, size, digest, ticket issue, and
  download without storing transcript text in the event payload.
