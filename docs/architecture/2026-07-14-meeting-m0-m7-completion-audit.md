# Meeting M0/M7 Completion Audit

This document is the evidence index for MT-000 through MT-003 and MT-070
through MT-073. It is intentionally incomplete until real commands and
authorized evaluations are run. Source files, mocks, and unit tests must not be
reported as real CER, voiceprint, latency, soak, privacy, or rollout evidence.

Allowed status values are `not_run`, `running`, `pass`, `fail`, and `blocked`.
Do not use `implemented` as an acceptance status.

## Release Decision

| Field | Value |
| --- | --- |
| Candidate commit | `not_run` |
| Baseline commit | `6727ce3441f2415c7e6dbc65f78c9e1983941168` |
| Audit owner | `not_run` |
| Security reviewer | `not_run` |
| Privacy reviewer | `not_run` |
| Overall status | `blocked` |
| Blocking reason | Mixed worktree has not passed the additive-only contract gate; real M0/M7 evidence is absent |

The release status can become `pass` only when every required row below is
`pass` and its artifact checksum is recorded.

## MT-000 Contract Baseline

The checked-in baseline was captured from the explicit pre-meeting commit, not
from the current worktree:

```bash
apps/api/.venv/bin/python scripts/meeting/meeting_contract_baseline.py capture \
  --source-ref 6727ce3441f2415c7e6dbc65f78c9e1983941168 \
  --python apps/api/.venv/bin/python \
  --output scripts/meeting/baselines/pre-meeting-6727ce3.contract.json
```

Reproduce the immutable baseline before relying on it:

```bash
apps/api/.venv/bin/python scripts/meeting/meeting_contract_baseline.py verify \
  --baseline scripts/meeting/baselines/pre-meeting-6727ce3.contract.json \
  --candidate-ref 6727ce3441f2415c7e6dbc65f78c9e1983941168 \
  --python apps/api/.venv/bin/python \
  --report scripts/meeting/baselines/pre-meeting-6727ce3.self-verify.json
```

Verify a release candidate or the current worktree:

```bash
apps/api/.venv/bin/python scripts/meeting/meeting_contract_baseline.py verify \
  --baseline scripts/meeting/baselines/pre-meeting-6727ce3.contract.json \
  --candidate-ref WORKTREE \
  --python apps/api/.venv/bin/python \
  --report artifacts/meeting/m0/candidate-contract-verify.json
```

Capture without `--source-ref` uses the merge base of `HEAD` and
`origin/master` (or `origin/main`). Capture rejects `WORKTREE`. Verification
permits additions only under `/api/meetings/v1`, `meeting_*` database tables,
and a new `siq_meeting` Hermes profile directory. It reports changed paths and
hashes, never contract values.

| Evidence | Status | Artifact | SHA-256 / notes |
| --- | --- | --- | --- |
| Baseline capture is deterministic | `pass` | `scripts/meeting/baselines/pre-meeting-6727ce3.contract.json` | `bf72d31d4fe4a2b4be384d0ba985ef72c3817e93aaed7520e05f80a38a277781` |
| Baseline self-verification | `pass` | `scripts/meeting/baselines/pre-meeting-6727ce3.self-verify.json` | `393e81b20c1ac7ced5f30ad99493190434be78676bf210773865f65eef6bc662`; CI rerun still required |
| Candidate legacy OpenAPI unchanged | `fail` | `artifacts/meeting/m0/candidate-contract-verify.json` | 46 differences; report SHA-256 `0fe59044dcb4bdcef90dad0e1e47d312e853837651a6b9b613181933e7b2ca4e`; meeting paths excluded |
| Candidate legacy DB tables/columns/indexes unchanged | `fail` | same report | 2 differences; `meeting_*` excluded |
| Existing Hermes profile files unchanged | `fail` | same report | 70 differences; new `siq_meeting` directory only |
| Default service/port/health declarations unchanged | `fail` | same report | 7 differences; meeting declarations excluded |
| Chat voice performance baseline and comparison | `not_run` | `artifacts/meeting/m0/chat-voice-performance.json` | Required threshold: degradation <= 5% |
| Existing full regression | `fail` | Local WORKTREE development run; CI artifact still required | Frozen-source complete API passed `2304 passed, 7 skipped`; repository touched-Python quality still fails with 49 occurrences / 28 non-meeting fingerprints |

## MT-001 Real ASR Selection

Use only authorized evaluation audio. Do not commit production meetings,
speaker templates, or raw sensitive transcripts.

| Evidence | Status | Artifact / measured value |
| --- | --- | --- |
| Dataset authorization and manifest | `not_run` | |
| Candidate engines and rejected alternatives | `not_run` | |
| 30-60 minute financial meeting set | `not_run` | |
| 2/4/8 speaker and overlap cases | `not_run` | |
| First partial P95, target <= 1.2s | `not_run` | |
| Stable P95, target <= 2.5s | `not_run` | |
| Stable DB commit P95, target <= 200ms | `not_run` | |
| Stable-to-visible additional P95, target <= 250ms | `not_run` | |
| ACK P95, target <= 300ms | `not_run` | |
| Timestamp error P95, target <= 500ms | `not_run` | |
| Streaming final CER delta, target <= 2 percentage points | `not_run` | |
| Four-hour memory/handle/queue soak | `not_run` | |
| Existing 8899 endpoints unchanged | `not_run` | MT-000 and endpoint smoke |

The ASR release evaluator is `scripts/meeting/evaluate_asr_release.py`. Its v2
input schema requires at least 30 minutes of authorized audio, three sessions,
2/4/8-speaker and required-condition coverage, 20 distinct cases, 100 latency
observations, and 20 paired lexicon cases. It also gates stable-to-database and
stable-to-visible P95, and requires the postprocessed final transcript not to
degrade CER relative to streaming final. The report contains only aggregate
metrics and fixed category names; dataset labels, authorization references,
audio paths, and transcript text are not emitted.

## MT-002 Hermes Isolation

Architecture is fixed by the immutable-target-pool ADR. Completion still
requires runtime evidence.

| Evidence | Status | Artifact / measured value |
| --- | --- | --- |
| ADR accepted | `pass` | `docs/architecture/decisions/2026-07-14-meeting-hermes-immutable-target-pool.md` |
| Meeting A/B concurrent different-model test | `not_run` | Cross-use count must be 0 |
| Pinned outage fault injection | `not_run` | Fallback count must be 0 |
| Existing profile hash before/after | `not_run` | MT-000 candidate report |
| Existing Hermes client/model-control tests | `not_run` | CI link |
| Credential and catalog redaction scan | `not_run` | Security artifact |

## MT-003 Voiceprint and Privacy Baseline

`auto_match` remains disabled unless the independently held-out result meets
the release gate. A configured threshold is not an evaluation result.

| Evidence | Status | Artifact / measured value |
| --- | --- | --- |
| Development/validation split and authorization | `not_run` | |
| Encoder and immutable version | `not_run` | |
| Device/noise/duration/multi-speaker matrix | `not_run` | |
| 2-8 speaker DER, target <= 15% | `not_run` | |
| Suggestion Top-1 precision, target >= 95% | `not_run` | |
| Auto-match false acceptance, target <= 0.1% | `not_run` | |
| Consent policy and user-private scope review | `not_run` | |
| Encryption/key rotation review | `not_run` | |
| Revoke/delete/backup restore test | `not_run` | Future match count must be 0 |
| Security and privacy approval | `not_run` | Named reviewers required |

## MT-070 Security and Privacy

| Required test | Status | Artifact |
| --- | --- | --- |
| Two-user BOLA across session/audio/transcript/artifact/export/voiceprint | `not_run` | |
| Ticket expiry, one-time use, replay, user/meeting/Origin binding | `not_run` | |
| Oversized frame, rate, duration, concurrency and malformed protocol | `not_run` | |
| Path traversal, symlink and SSRF | `not_run` | |
| Log/metric/error/export/cloud request sensitive-data scan | `not_run` | |
| Transcript prompt injection | `not_run` | |
| Revoke/delete race and restored-backup behavior | `not_run` | |
| Security and privacy sign-off | `not_run` | |

## MT-071 Performance and Recovery

| Required test | Status | Artifact / measured value |
| --- | --- | --- |
| Release concurrency `C_release` frozen | `not_run` | |
| `C_release + 20%` latency and queue run | `not_run` | |
| Upload-complete to ready P95 RTF, repository policy <= 0.30 | `not_run` | `artifacts/meeting/m7/performance-release.json` |
| Final ASR P95 RTF, repository policy <= 0.25 | `not_run` | same report |
| AI enqueue P95 <= 50ms with at least 20 observations | `not_run` | same report |
| AI job queue-to-complete P95 <= 180s with at least 20 observations | `not_run` | same report |
| Rolling minutes freshness P95 <= 90s with at least 20 observations | `not_run` | same report |
| Final minutes after last stable P95 <= 180s with at least 20 observations | `not_run` | same report |
| Four-hour meeting soak | `not_run` | |
| Four-hour RSS/handle/queue samples and bounded steady-state slopes | `not_run` | same report; <= 60s sample interval |
| Gateway restart and client replay | `not_run` | Stable loss/duplicate must be 0 |
| Worker/API restart and lease recovery | `not_run` | Duplicate artifact must be 0 |
| Database outage and storage failure | `not_run` | Explicit gap/safe stop required |
| Hermes unavailable for 30 minutes | `not_run` | Caption degradation <= 10% |
| Two simultaneous model targets | `not_run` | Cross-use count must be 0 |

The aggregate performance contract is
`scripts/meeting/evaluate_performance_release.py`. The repository release
policy currently fixes import-to-ready P95 RTF at `<= 0.30` and final-ASR P95
RTF at `<= 0.25`; changing either limit requires an explicit review rather than
an evidence-input override. It additionally enforces the taskbook limits for
AI enqueue P95 (`<= 50ms`), AI job completion P95 (`<= 180s`), rolling minutes
freshness P95 (`<= 90s`), final minutes after last stable P95 (`<= 180s`), the
30-minute Hermes outage comparison, and restart/outage recovery counts.

Four-hour soak evidence must contain numeric samples at intervals no larger
than 60 seconds and cover at least 95% of the declared schedule. The evaluator
calculates RSS, open-handle, and queue-depth slopes after a 30-minute warm-up,
checks net growth and configured capacity headroom, and rejects short, sparse,
missing, or non-finite measurements. These checks define a reproducible report
schema; they do not replace the required real four-hour run.

## MT-072 Operations

| Required evidence | Status | Artifact |
| --- | --- | --- |
| Low-cardinality metrics | `not_run` | Runtime scrape |
| Dashboard | `not_run` | Export/version |
| Alerts and test notifications | `not_run` | Rule file and firing proof |
| Service start/stop/status/log/health procedures | `not_run` | Runbook revision |
| ASR, voiceprint and Hermes layered diagnosis | `not_run` | Drill notes |
| Gap, queue, unavailable model and revoke procedures | `not_run` | Drill notes |

## MT-073 Rollout and Rollback

Each stage requires a 30-minute authorized internal meeting, one network
recovery, one optional-component failure, and one flag rollback.

| Required rehearsal | Status | Artifact |
| --- | --- | --- |
| Internal allowlist | `not_run` | |
| 5% -> 25% -> 100% expansion | `not_run` | |
| Disable AI; captions continue | `not_run` | |
| Disable voiceprint; anonymous speaker continues | `not_run` | |
| Stop worker and recover idempotently | `not_run` | |
| Rolling gateway restart | `not_run` | |
| Disable meeting entry; other pages unchanged | `not_run` | |
| Old application ignores new tables | `not_run` | |
| Restore after voiceprint delete does not reactivate template | `not_run` | |

## Mandatory Non-Regression Commands

Record the commit, command, start/end time, exit code, test count, and artifact
checksum for each command. A pasted terminal summary is not sufficient.

```bash
cd apps/web && npm run test:unit
cd apps/web && npm run check:frontend
cd apps/api && uv run python -m pytest tests
scripts/check_all.sh
```

The following tests must remain in the executed set:

```text
apps/api/tests/test_chat_voice_transcription.py
apps/api/tests/test_primary_market_meeting_router.py
apps/api/tests/test_hermes_client.py
apps/api/tests/test_hermes_model_control.py
apps/web/e2e/tests/chat-voice.spec.ts
apps/web/src/app/routes.test.ts
```

### Current WORKTREE development snapshot

This snapshot records local development verification only. It is not a clean
release-candidate artifact and does not satisfy any real-audio, four-hour,
hardware, security-review, or privacy-review row above.

| Command / scope | Result | Release interpretation |
| --- | --- | --- |
| Meeting API targeted suite | `207 passed` | Development regression passed; all `test_meeting*` also passed in the complete API run |
| Complete API suite | `2304 passed, 7 skipped, 0 failed`; exit `0`; 591.12s | Frozen-source development run from 2026-07-14 19:42:00 to 19:52:27 +0800; source mtimes were unchanged |
| Earlier concurrent API/check-all attempts | invalid snapshot | Both loaded an intermediate untracked IC fixture while that file was being patched; exact replay failed `schema.r4` for seven then-missing required fields, so those runs are not used as the final API verdict |
| `scripts/check_all.sh` composite result | `fail` | Frozen API and manually continued stages passed except touched Python quality; no single clean-candidate artifact exists |
| PDF / Document / Finder / Rules / Contracts | `496 passed, 10 skipped` / `63` / `116` / `89` / `23` | Development checks passed |
| Security / changed-large-file / PostgreSQL contract | exit `0` | Development checks passed |
| Touched Python quality | `fail`: 49 occurrences / 28 fingerprints | All reported fingerprints are outside the meeting domain; meeting diagnostics are 0, but the repository gate remains failed |
| Web unit / lint / TypeScript / production build | `394/394`; all checks passed | Development checks passed; no clean-candidate artifact |
| Web meeting E2E | disabled `1/1`; enabled `10/10`; default `52 passed, 1 skipped`; chat voice mock `1/1` | Browser/mock coverage only, not real microphone or iPhone evidence |
| Meeting speech / release-tool tests | `29` / `48` passed | Protocol and fail-closed evaluator coverage only |
| iOS static contracts | Node `9/9`; Swift tree-sitter `9/9` | Linux static checks only; Xcode, XCTest and iPhone were not run |

## Reproducible Release Evidence Commands

Keep authorized inputs outside Git and ordinary CI artifacts. Only the
redacted JSON/Markdown outputs and their SHA-256 values belong in the release
evidence index.

```bash
uv run --project apps/api python scripts/meeting/evaluate_asr_release.py \
  --input "$ASR_AGGREGATE_INPUT" \
  --output artifacts/meeting/m0/asr-release.json \
  --markdown artifacts/meeting/m0/asr-release.md \
  --require-passing

uv run --project apps/api python scripts/meeting/evaluate_voiceprint_release.py \
  --input "$VOICEPRINT_AGGREGATE_INPUT" \
  --output artifacts/meeting/m0/voiceprint-release.json \
  --require-passing

uv run --project apps/api python scripts/meeting/evaluate_performance_release.py \
  --input "$PERFORMANCE_AGGREGATE_INPUT" \
  --output artifacts/meeting/m7/performance-release.json \
  --markdown artifacts/meeting/m7/performance-release.md \
  --require-passing

uv run --project apps/api python scripts/meeting/verify_release_evidence_bundle.py \
  --asr artifacts/meeting/m0/asr-release.json \
  --voiceprint artifacts/meeting/m0/voiceprint-release.json \
  --performance artifacts/meeting/m7/performance-release.json \
  --candidate-commit "$CANDIDATE_COMMIT" \
  --output artifacts/meeting/release-evidence-receipt.json
```

The checked-in templates are deliberately `not_run`/non-passing. CI executes
all three templates and requires exit code `1` plus `passed=false`; this is only
a fail-closed schema test and must never be cited as real ASR, voiceprint,
performance, recovery, or soak evidence. A malformed or sensitive-field input
is rejected with exit code `2` and no report.

For the release CI boundary, upload only the three redacted JSON reports as
`asr-release.json`, `voiceprint-release.json`, and
`performance-release.json` in an artifact named
`meeting-release-redacted-evidence`. Invoke
`.github/workflows/meeting-release-evidence-gate.yml` with the producing run
ID. The workflow rejects a non-passing report, an unexpected schema/policy,
prohibited sensitive fields, candidate or environment mismatches, and emits a
receipt containing the input/report SHA-256 values. The private ASR comparison
text and authorized audio never enter that artifact.

## Artifact Rules

- Store machine-readable JSON plus a short human-readable summary.
- Record SHA-256, candidate commit, environment profile, and exact command.
- Keep authorized raw audio outside Git and ordinary CI artifacts.
- Never store transcript text, names, voice embeddings, credentials, tokens,
  private endpoints, or absolute protected storage paths in the report.
- `not_run` cannot be converted to `pass` from source inspection.
- Failed or blocked evidence remains in the audit history; do not overwrite it
  with an empty successful run.
