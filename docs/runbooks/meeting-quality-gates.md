# Meeting Quality Gates

The offline quality evaluators turn real, authorized validation evidence into
deterministic release reports. They do not collect evidence, call model
providers, or make placeholder data count as a release pass.

Keep private inputs and generated reports in an access-controlled evidence
store outside the repository. A report records the input SHA-256 so the source
used for approval can be identified without copying private content into the
report.

## ASR release evaluation

`scripts/meeting/evaluate_asr_release.py` evaluates the frozen baseline and
candidate CER, streaming latency, entity recall, and paired lexicon behavior.
Its input contains authorized transcript references and hypotheses. The JSON
and optional Markdown outputs contain only aggregate metrics and never contain
the transcript strings.

Run it from the repository root:

```bash
python scripts/meeting/evaluate_asr_release.py \
  --input /secure/evidence/asr-release-input.json \
  --output /secure/evidence/asr-release-report.json \
  --markdown /secure/evidence/asr-release-report.md \
  --require-passing
```

`--require-passing` exits nonzero when any hard release limit fails. An input
must record an authorization approval and a non-sensitive approval reference.
Do not use production meetings or historical chat recordings without explicit
authorization.

## Voiceprint release evaluation

`scripts/meeting/evaluate_voiceprint_release.py` accepts only anonymized,
aggregate trial counts. The strict schema rejects extra fields, so names,
recordings, transcript content, sample paths, embeddings, and per-person trial
rows cannot enter the evaluator or its report.

The non-passing input template is:

```text
scripts/meeting/templates/voiceprint-release-evidence.v1.json
```

The checked-in template is intentionally unauthorized, non-independent, and
empty. Running it can never produce a passing policy. Populate a separate file
in the secure evidence store only after the trial owner has produced real
aggregate results.

Required evidence sections are:

| Section | Required proof |
| --- | --- |
| `authorization` | Approval, all-trial authorization, zero unauthorized production/history trials, and an opaque approval reference. |
| `split` | Independent holdout, independent from both training and threshold tuning, with zero speaker and recording overlap. |
| `threshold_policy` | The exact frozen score, margin, duration, and quality policy used for every reported trial. |
| `aggregates.diarization` | Clean 2-8 speaker coverage and aggregate missed, false-alarm, confusion, and reference time. |
| `aggregates.matching` | Aggregate genuine, suggestion, and impostor counts; no trial or subject identifiers. |
| `aggregates.revocation` | Aggregate trials performed after consent revocation and their new-match count. |
| `aggregates.template_authorization` | Complete persistent-template inventory audit and unauthorized count. |

The v1 quality limits are fixed in the evaluator:

| Gate | Limit |
| --- | --- |
| Clean 2-8 speaker DER | `<= 15%` |
| Suggestion Top-1 precision | `>= 95%` |
| Auto-match false acceptance rate | `<= 0.1%` |
| New matches after revocation | `0` |
| Unauthorized persistent templates | `0` |

The minimum evidence is 14 diarization sessions covering every speaker count
from 2 through 8, one hour of reference speaker time, 100 genuine trials, 100
Top-1 predictions, 3,000 independent impostor trials, and 100 post-revocation
trials. These are hard lower bounds, not target sample sizes. The approved
statistical plan should use more trials when needed for the desired confidence
interval; never reduce the evaluator constants to make a release pass.

Run the gate:

```bash
python scripts/meeting/evaluate_voiceprint_release.py \
  --input /secure/evidence/voiceprint-release-input.json \
  --output /secure/evidence/voiceprint-release-report.json \
  --require-passing
```

`--require-passing` is the full auto-match gate. It returns nonzero for an
insufficient impostor sample even when suggestion quality is acceptable. Read
the explicit decisions rather than inferring a release from process exit alone:

| `release_mode` | Meaning |
| --- | --- |
| `auto_match` | Suggestion and every auto-match gate passed. |
| `suggestion_only` | Suggestion gates passed, but auto-match evidence did not. |
| `blocked` | Authorization, independence, privacy, sample, DER, suggestion, revocation, or inventory evidence failed. |

Any non-independent split or insufficient sample forces
`auto_match_validated=false`. Revocation or template-authorization violations
block both suggestion and auto-match release.

The report embeds the exact JSON accepted by
`VoiceprintThresholdPolicy.from_json`:

```bash
export SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON="$(
  jq -r '.environment.SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON' \
    /secure/evidence/voiceprint-release-report.json
)"
```

This sets the calibrated threshold artifact only. Keep
`SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED=0` for `suggestion_only` and
`blocked` reports. Even an `auto_match` report does not replace the separate
privacy, security, and rollout approval required before explicitly enabling
automatic naming.

## Evidence review

Before approving either report:

1. Match the report `source_sha256` to the immutable evidence object.
2. Confirm dataset authorization and development/validation separation with
   the evidence owner.
3. Confirm the model, encoder, lexicon, and threshold versions match the build
   being released.
4. Archive the report and approval decision together; do not archive raw
   biometric or transcript material in general CI artifacts.
5. Treat a missing metric, malformed input, nonzero gate exit, or
   `auto_match_validated=false` as a fail-closed result.

## CI enforcement

The main workflow calls `.github/workflows/meeting-contract-gate.yml` as a
required job. That reusable workflow runs every test under
`scripts/meeting/tests`, reproduces the immutable pre-meeting baseline, and
verifies the candidate worktree against it. The candidate report is uploaded
even when verification fails.

Meeting browser coverage is split by build-time feature state:

| Suite | Feature state | Scope |
| --- | --- | --- |
| `e2e:default` | Meetings off | Existing product E2E, including `chat-voice.spec.ts`; all `meeting-*.spec.ts` files are excluded. |
| `e2e:meeting:disabled` | Meetings off | Direct meeting routes render the disabled page without meeting API or microphone access. |
| `e2e:meeting:enabled` | Meetings on | Positive meeting workflows and responsive coverage with fixed API fixtures. |

Run the same suite discovery used by CI before changing Playwright filters:

```bash
cd apps/web
npm run e2e:default -- --list
npm run e2e:meeting:disabled -- --list
npm run e2e:meeting:enabled -- --list
```

The default listing must contain `chat-voice.spec.ts` and no
`meeting-*.spec.ts`. The disabled listing must contain only
`meeting-feature-disabled.spec.ts`; the enabled listing must contain only the
positive meeting specification selected by `playwright.meeting.config.ts`.

Run the deterministic browser gates locally with:

```bash
cd apps/web
npm run e2e:meeting:disabled
npm run e2e:meeting:enabled
```

Validate all GitHub Actions workflow YAML, including duplicate keys and invalid
reusable-workflow calls, with the same pinned `actionlint` used by CI:

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7
```

Run the meeting gate tool tests independently with:

```bash
uv run --project apps/api python -m pytest -q scripts/meeting/tests
```
