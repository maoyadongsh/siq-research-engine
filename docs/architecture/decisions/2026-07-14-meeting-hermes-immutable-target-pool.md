# ADR: Meeting Hermes Immutable Target Pool

- Status: Accepted for implementation; runtime release evidence pending
- Date: 2026-07-14
- Task: MT-002 / MT-051
- Decision owner: Meeting and Hermes maintainers

## Context

The meeting domain lets each session select an available Hermes model without
changing the model used by another meeting or by an existing SIQ profile. The
current Hermes gateway exposes a run `model` field, but repository inspection
does not establish that it changes the underlying provider for one run without
shared configuration mutation.

Writing a shared profile YAML for each meeting is prohibited. It introduces a
cross-session race, changes existing assistant behavior, and makes a queued job
resolve to a different model from the one selected by the user.

## Decision

Use taskbook Path B: an immutable meeting target pool.

Each allowed model has a dedicated Hermes gateway target. The target has one
provider/model configuration, no fallback providers, no unrelated tools, and
one stable opaque `model_ref`. Runtime target files live outside the source
profile tree. Meeting code resolves `model_ref` to a target and stores an
immutable execution snapshot before queueing the job.

The following invariants are mandatory:

1. Target discovery is read-only over existing Hermes profile source files.
2. Meeting code never calls `set_profile_model_mode()`,
   `set_all_profile_model_modes()`, or another profile YAML writer.
3. A pinned job has no silent fallback. A missing target produces
   `MODEL_TARGET_UNAVAILABLE` while recording and ASR continue.
4. Workers execute from the stored target ID and model snapshot, not the
   meeting's current selection.
5. A target exposes no provider credential, internal authorization value, or
   raw endpoint to the browser model catalog.
6. Adding or removing a target is an administrator action. It does not rewrite
   a running target or an existing SIQ profile.
7. Cloud-bound text follows the meeting data-boundary confirmation and
   pseudonymization contract. Audio and voiceprint data are never input.

## Implementation Boundary

The current implementation is represented by:

- `scripts/hermes/meeting_targets.py`
- `scripts/hermes/run_meeting_gateway.sh`
- `apps/api/services/meeting_model_catalog.py`
- `apps/api/services/meeting_hermes_runner.py`
- `apps/api/services/meeting_ai_worker.py`

Generated target configuration belongs under `SIQ_RUNTIME_ROOT`, not
`agents/hermes/profiles`. Existing profile files are protected by the PR-00
contract baseline gate.

## Evidence Status

Code and unit tests are not sufficient release evidence. Record each item in
the M0/M7 completion audit.

| Evidence | Current status | Release rule |
| --- | --- | --- |
| Read-only discovery and stable opaque references | File/unit evidence present | Focused tests must pass in CI |
| No tools and no fallback in rendered target | File/unit evidence present | Focused tests must pass in CI |
| Existing profile SHA-256 unchanged | Not demonstrated for the mixed worktree | PR-00 verify must pass |
| Existing `create_run()` and global model-control behavior | Not rerun as part of this ADR | Mandatory non-regression tests must pass |
| Meeting A and B use different targets concurrently | Not run | Runtime test must report zero cross-use |
| Pinned target outage never falls back | Unit evidence present; runtime fault injection not run | Runtime fault-injection evidence required |
| Provider credentials remain target-scoped | File/unit evidence present | Deployment secret and log scan required |

Until the two-meeting concurrency test and profile-hash verification pass, this
ADR selects the architecture but does not complete MT-002 or authorize AI
rollout.

## Consequences

- More gateway processes may be needed than with a true run-scoped override.
- Capacity, health, and target lifecycle must be observable independently.
- Model changes affect only new snapshots; historical jobs and artifacts retain
  their original target provenance.
- If Hermes later provides a verified run-scoped override, changing to Path A
  requires a new ADR and the same isolation/non-regression evidence. It must not
  be introduced as an in-place optimization of this decision.

## Rollback

Disable meeting AI and stop accepting new AI jobs. Recording, stable transcript,
anonymous speaker, and playback remain available. Drain or mark target-bound
jobs retryable; do not rewrite snapshots and do not modify existing profiles.
