# Meeting Voiceprint Worker

The meeting voiceprint worker is an independent, feature-gated process. It
consumes durable enrollment and cross-meeting match jobs. Encoder, storage, or
voiceprint failures do not change meeting capture, ASR, or stable transcript
state.

## Release prerequisites

- `SIQ_MEETINGS_ENABLED=1` and `SIQ_MEETINGS_VOICEPRINT_ENABLED=1`.
- The internal meeting-speech embedding endpoint is healthy and protected by a
  server-only service token.
- The configured encoder identity exactly matches the endpoint response.
- A versioned AES-256-GCM master key and a separate tombstone HMAC key are
  available from the runtime secret manager.
- The append-only tombstone ledger is mounted outside `SIQ_BACKEND_DATA_ROOT`
  and all database backup media.
- Suggestion thresholds come from an immutable validation artifact generated
  by the [meeting quality gates](./meeting-quality-gates.md). The values in env
  examples are only a schema example.
- Automatic matching remains disabled until both threshold validation and the
  separate false-acceptance release gate are complete.

Each template uses an HKDF-derived per-profile AES key. Revocation or deletion
first appends an authenticated external tombstone, then clears the database
ciphertext and revokes every active consent. Reads, worker startup, and restore
acceptance replay the ledger, so an older backup cannot reactivate the template.

## Required configuration

| Variable | Purpose |
| --- | --- |
| `SIQ_MEETING_SPEAKER_EMBEDDING_URL` | Internal HTTPS endpoint; loopback HTTP is allowed for local development. |
| `SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN` | Shared server-only token for the embedding endpoint. |
| `SIQ_MEETING_VOICEPRINT_ENCODER_REF` | Exact `encoder_ref` expected from meeting-speech. |
| `SIQ_MEETING_VOICEPRINT_ENCODER_NAME` | Stable encoder family stored with templates. |
| `SIQ_MEETING_VOICEPRINT_ENCODER_VERSION` | Immutable encoder/model version stored with templates. |
| `SIQ_MEETINGS_VOICEPRINT_KEY_ID` | Active encryption key identifier. |
| `SIQ_MEETINGS_VOICEPRINT_KEYRING_JSON` | JSON mapping key ids to secret environment variable names. |
| `SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH` | Append-only JSONL ledger outside the database backup domain. |
| `SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY` | Separate base64-encoded 32-byte HMAC key. |
| `SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON` | Versioned score, margin, duration, quality, and auto-gate policy. |
| `SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED` | Explicit auto-match gate; keep `0` until the policy artifact has `auto_match_validated=true`. |

## Threshold evaluation

The release evaluator consumes authorized, independent aggregate trials and
emits the exact value for `SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON`:

```bash
python scripts/meeting/evaluate_voiceprint_release.py \
  --input /secure/evidence/voiceprint-release-input.json \
  --output /secure/evidence/voiceprint-release-report.json \
  --require-passing
```

See [Meeting Quality Gates](./meeting-quality-gates.md) for the strict input
schema, minimum sample sizes, release-mode interpretation, and evidence review
procedure. An insufficient or non-independent evaluation always emits
`auto_match_validated=false`. Automatic matching still requires the separate
`SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED=1` rollout decision.

Generate a local 32-byte key without committing it:

```bash
openssl rand -base64 32
```

The keyring contains environment variable names, not key material:

```bash
export SIQ_MEETINGS_VOICEPRINT_KEY_ID=local-v1
export SIQ_MEETINGS_VOICEPRINT_KEYRING_JSON='{"local-v1":"SIQ_MEETING_VOICEPRINT_KEY_LOCAL_V1"}'
export SIQ_MEETING_VOICEPRINT_KEY_LOCAL_V1='<base64 output>'
export SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY='<different base64 output>'
```

Keep previous keys in the keyring during rotation so existing templates remain
decryptable. New enrollment always uses the active key id.

Create the ledger parent once with owner-only permissions. The worker enforces
directory mode `0700`, file mode `0600`, a regular non-symlink file, and an
HMAC chain. The ledger contains only profile id, owner id, deletion time, and
reason; it must never contain names, audio, embeddings, or ciphertext.

```bash
install -d -m 0700 "$(dirname "$SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH")"
touch "$SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH"
chmod 0600 "$SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH"
```

## Start and stop

From the repository root:

```bash
apps/api/scripts/start_meeting_voiceprint_worker.sh
```

Process one job for diagnostics:

```bash
apps/api/scripts/start_meeting_voiceprint_worker.sh --once \
  --worker-id voiceprint-debug
```

The worker has an independent user unit and must be enabled explicitly:

```bash
install -d -m 0700 ~/.config/systemd/user
ln -sfn "$PWD/infra/systemd-user/siq-meeting-voiceprint-worker.service" \
  ~/.config/systemd/user/siq-meeting-voiceprint-worker.service
systemctl --user daemon-reload
systemctl --user enable --now siq-meeting-voiceprint-worker.service
journalctl --user -u siq-meeting-voiceprint-worker.service -f
```

The checked-in unit expects the repository at `%h/siq-research-engine` and
loads `%h/siq-research-engine/infra/env/local.env`. Use a systemd drop-in to
override both paths for another installation. Keep that env file mode `0600`.
Missing keys, tombstone integrity, thresholds, flags, or service tokens fail
startup closed. `SIGTERM` and `SIGINT` stop the loop after the current bounded
operation.

## Restore acceptance

The required restore matrix automatically runs the tombstone reconciler for
`siq_app` when `SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED=1`. For a
single approved restore, run it against the restored database before allowing
application traffic:

```bash
cd apps/api
uv run --frozen python scripts/reconcile_meeting_voiceprint_tombstones.py --apply
```

The command exits nonzero if HMAC verification fails, a tombstone belongs to a
different owner, ciphertext or a key id remains, or any consent is still
active. Its JSON output contains aggregate counts only.

## Recovery and diagnostics

- Expired leases can be reclaimed; retryable jobs observe the repository
  backoff before another claim.
- Enrollment revalidates consent after model calls and again in the publishing
  transaction.
- Matching reloads the complete owner-private candidate set before publication.
  The final transaction verifies the candidate fingerprint and template
  revision. A missing or unauthenticated template keeps the speaker anonymous.
- Logs may contain job ids and error codes. They must not contain audio,
  transcript text, names, embeddings, ciphertext, keys, or service tokens.
