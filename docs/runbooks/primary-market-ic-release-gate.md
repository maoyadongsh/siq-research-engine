# Primary-market IC release gate

The Primary-market IC workflow has two deliberately separate levels:

- Every pull request calls the reusable contract job from `.github/workflows/ci.yml`. It checks that all 15 authoritative IC JSON Schemas are exported, then runs the PMIC contract, smoke-runner, and release-gate tests. It does not call a live model.
- The scheduled or manually dispatched release job runs on a self-hosted runner and validates a previously produced real R0-R4 behavior bundle. Missing external evidence is a release failure, never a skip.

## Required repository variables

Configure these GitHub Actions repository variables as absolute paths visible to the self-hosted runner:

| Variable | Required artifact |
| --- | --- |
| `SIQ_PMIC_RELEASE_BUNDLE` | Deal package containing the authoritative phase, task, handoff, audit, decision, and `release/golden_case_bindings.json` artifacts |
| `SIQ_PMIC_FACTCHECK_REPORT` | Raw-model-bound factchecker report for the same Deal, workflow run, report revision, and Evidence snapshot |
| `SIQ_PMIC_REAL_SMOKE_REPORT` | Successful `siq_ic_real_smoke_result_v1` report from real Hermes execution |
| `SIQ_PMIC_HUMAN_APPROVAL` | Named `siq_ic_human_methodology_approval_v3` attestation bound to the exact evaluated golden-suite bindings digest |

All four paths must resolve outside `GITHUB_WORKSPACE`. Mount or publish the evidence read-only where possible. The workflow writes only its v3 JSON and Markdown reports under `artifacts/eval-runs/primary-market-ic/ci-release/`.

The release Deal does not require a sixth Deal. `SIQ_PMIC_RELEASE_BUNDLE` may resolve to one of the five distinct candidate Deal packages for conditional support, material risk, insufficient evidence, full R3, and stale snapshot; when it does, that same directory appears exactly once in the golden binding and the other four candidates remain siblings under the same suite root. The gate still requires five unique Deal IDs, fixture bundle paths, real-smoke run IDs, result IDs, and result SHA-256 digests; copying one fixture or run under several case IDs fails. The methodology approval must include `golden_case_bindings_sha256`, the SHA-256 digest of the exact `release/golden_case_bindings.json` bytes reviewed by the approver.

## Prepare independent golden inputs

The committed suite contains five synthetic, input-only Deal packages. Regenerate the four scenario variants and verify all committed bytes before any live run:

```bash
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py --check
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py --check
```

`eval_datasets/primary_market_ic_real_smoke/golden_suite_manifest.json` is an input inventory only. Its per-case `input_identity` binds the deterministic input bundle digest, fixture contract digest, initial Evidence snapshot, and file count; the generator's `--check` validates those bindings as well as every fixture byte. Every case remains `result_status: not_run` and `quality_accepted: false` until an isolated real run is evaluated. Use one dedicated suite run root for the five fixtures. One of those five outputs may also be `SIQ_PMIC_RELEASE_BUNDLE`, so no separate release-only Deal is required. The runner still isolates each case under `wiki/deals/$DEAL_ID`; the shared parent is required so the binding evaluator can prove that all five Deal packages belong to the same reviewed suite:

```bash
export GOLDEN_SUITE_RUN_ROOT="artifacts/eval-runs/primary-market-ic/golden-suite-$(date +%Y%m%d)"

uv run --project apps/api python scripts/hermes/run_primary_market_ic_real_smoke.py \
  --fixture "eval_datasets/primary_market_ic_real_smoke/$DEAL_ID" \
  --run-root "$GOLDEN_SUITE_RUN_ROOT" \
  --phase R0 --phase R1 --phase R1.5 --phase R2 --phase R3 --phase R4 \
  --timeout 2400 --real
```

## Record a trusted R4 human confirmation

Human confirmation is an authenticated API action, not a fixture-generation or release-gate action. The caller must be a named SIQ user with the `report.create` permission and write access to the Deal. The router derives `confirmed_by.id` and `confirmed_by.username` from the authenticated user; never add `confirmed_by`, an actor ID, or an approval timestamp to the request body.

First inspect the current decision and record the exact `report_id`, `revision`, `workflow_run_id`, and `evidence_snapshot_hash`. The report quality must allow human confirmation, and the quality and factcheck identities must match that same report revision and snapshot:

```bash
export SIQ_API_BASE="https://siq.example.internal"
export SIQ_USER_ACCESS_TOKEN="<token issued by the normal SIQ login flow>"
export DEAL_ID="DEAL-PMIC-POSITIVE-COND-2026"

curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $SIQ_USER_ACCESS_TOKEN" \
  "$SIQ_API_BASE/api/deals/$DEAL_ID/decision" \
  | jq '{
      report_id: .decision.report_id,
      revision: .decision.revision,
      workflow_run_id: .decision.workflow_run_id,
      evidence_snapshot_hash: .decision.evidence_snapshot_hash,
      quality: (.quality | {report_id, report_revision, evidence_snapshot_hash, status, allowed_for_human_confirmation}),
      factcheck: (.factcheck | {report_id, report_revision, evidence_snapshot_hash, status})
    }'
```

Use the same endpoint for preview and write. Always run the preview first and review its `confirmation_gate`, server-derived actor, report identity, snapshot, and `decision_sha256`, `quality_sha256`, and `factcheck_sha256`. A preview must return `dry_run: true` and `would_write: false`:

```bash
curl --fail-with-body --silent --show-error \
  -X POST \
  -H "Authorization: Bearer $SIQ_USER_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"status":"confirmed","dry_run":true}' \
  "$SIQ_API_BASE/api/deals/$DEAL_ID/decision/human-confirmation" \
  | jq -e '.dry_run == true and .would_write == false and .confirmation_gate.allowed == true'
```

Only after the named user has reviewed that exact preview may the user perform the write:

```bash
curl --fail-with-body --silent --show-error \
  -X POST \
  -H "Authorization: Bearer $SIQ_USER_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"status":"confirmed","dry_run":false}' \
  "$SIQ_API_BASE/api/deals/$DEAL_ID/decision/human-confirmation" \
  | jq -e '.dry_run == false and .decision_contract.human_confirmation.status == "confirmed"'
```

Re-read the decision and audit endpoints. Reading the audit endpoint additionally requires `audit.view` and Deal view access; an independent named audit user may perform that verification when the confirmer does not hold `audit.view`. The stored confirmation must contain `siq_ic_human_confirmation_attestation_v1`, the expected report revision and snapshot, all three 64-character digests, and exactly one matching `r4_human_confirmation_updated` audit event. Treat the stored write response as authoritative; if its identity differs from the reviewed preview, do not use it in a golden result or methodology approval.

`rejected`, `needs_revision`, and `overridden` require an `override_reason`. They are valid review outcomes, but they do not make a release candidate eligible: the v3 release gate requires a trusted `confirmed` R4 confirmation. The conditional-support candidate and the initial stale-snapshot candidate each require their own authenticated confirmation; one confirmation cannot be copied to another Deal or revision.

The stale-snapshot case is two-stage. Complete and human-confirm its initial R0-R4 run first, then activate the committed update source through the normal Evidence refresh path:

```bash
uv run --project apps/api python scripts/maintenance/activate_primary_market_ic_stale_fixture.py \
  --package "$SNAPSHOT_STALE_BUNDLE" \
  --wiki-root "$(dirname "$(dirname "$SNAPSHOT_STALE_BUNDLE")")"
```

The activation command verifies the trusted human attestation against the exact completed workflow run and unique confirmation audit, then verifies staged archive/content hashes and identities before registering the source. It must change the snapshot, stale prior receipts, and set `decision_review_required`; it never creates a confirmation or golden pass. Replaying the same successful activation is idempotent and returns the already-invalidated snapshot, while a changed source, path, or artifact fails closed.

## Build golden candidate evidence

Evaluate each real Deal package with the case declared in the candidate manifest. The evaluator reads the persisted smoke, Evidence, task, report, decision, audit, factcheck, and confirmation artifacts needed by that case. It writes one recomputable path result per required path and one `release/golden_case_result.json` candidate result:

```bash
python3 scripts/maintenance/run_primary_market_ic_golden_evaluator.py evaluate \
  --bundle "$CASE_BUNDLE" \
  --case-id GOLDEN-PMIC-CONDITIONAL-SUPPORT

python3 scripts/maintenance/run_primary_market_ic_golden_evaluator.py validate \
  --bundle "$CASE_BUNDLE"
```

Both commands return nonzero when an identity, source artifact, required path, assertion, or digest is absent or stale. `validate` recomputes assertions from the current source artifacts; it does not trust booleans already stored in the candidate result.

After all five independent candidates validate, bind them to the release Deal. The release Deal may itself be one of the five `--case-bundle` values; in that five-project topology, pass it exactly once. The conditional-support bundle is the normal release candidate because the release gate separately requires a current, factchecked, human-confirmed R4 decision. Repeat `--case-bundle` once for each required scenario:

```bash
python3 scripts/maintenance/run_primary_market_ic_golden_evaluator.py bind \
  --release-bundle "$SIQ_PMIC_RELEASE_BUNDLE" \
  --suite-id "$GOLDEN_CASE_SUITE_ID" \
  --case-bundle "$CONDITIONAL_SUPPORT_BUNDLE" \
  --case-bundle "$MATERIAL_RISK_BUNDLE" \
  --case-bundle "$INSUFFICIENT_EVIDENCE_BUNDLE" \
  --case-bundle "$FULL_R3_BUNDLE" \
  --case-bundle "$SNAPSHOT_STALE_BUNDLE"
```

The binding remains a candidate artifact with `quality_accepted: false`. The evaluator never changes the manifest, marks a case accepted, or converts a failed path into a passing assertion. Exactly five distinct Deal IDs, fixture bundle paths, real-smoke run IDs, result IDs, and result SHA-256 digests are mandatory. The release Deal may occupy one of those five candidate slots, but a repeated self candidate, sibling candidate, case ID, fixture path, run, result, or digest fails closed.

## Record independent methodology approval

The methodology approver must be a named person independent from the R4 confirmer. The approver reviews the source artifacts, not only stored `passed` booleans. At minimum, review and revalidate these five candidate bundles:

- `GOLDEN-PMIC-CONDITIONAL-SUPPORT`
- `GOLDEN-PMIC-MATERIAL-RISK`
- `GOLDEN-PMIC-INSUFFICIENT-EVIDENCE`
- `GOLDEN-PMIC-FULL-R3`
- `GOLDEN-PMIC-SNAPSHOT-STALE`

For `GOLDEN-PMIC-FULL-R3`, R1.5 must formally resolve every dispute before R2. At least one high- or
critical-severity dispute must retain two genuinely opposing expert positions after the chairman's
model ruling, and the same workflow must then complete R2 and a full R3 debate. An unresolved dispute
is a fail-closed Evidence-loop outcome; it must never be treated as the trigger for R2 or R3.

For every candidate, run `run_primary_market_ic_golden_evaluator.py validate` and inspect `release/golden_case_result.json`, every referenced `evaluation/golden/*.json` artifact, the fixture contract and input identity, the real-smoke run ID, Deal ID, Evidence snapshot, and source artifact digests. Then inspect `release/golden_case_bindings.json`: it must contain five distinct Deal, fixture bundle-path, run, result, and result-digest identities and have `status: passed`; if `SIQ_PMIC_RELEASE_BUNDLE` is one candidate, it must appear exactly once. Compute the digest only after that review and do not change the binding file afterward:

```bash
sha256sum "$SIQ_PMIC_RELEASE_BUNDLE/release/golden_case_bindings.json"
```

The approver must also review the release Deal's current `phases/r4_decision.json`, `decision/report_quality.json`, `decision/factcheck.json`, completed workflow run, and unique human-confirmation audit event. The approval is stored outside the mutable checkout and supplied through `SIQ_PMIC_HUMAN_APPROVAL`. The following is a field template for the governed review record; it is not a generator and placeholders must be replaced by the approver or the organization's approval system from the reviewed artifacts:

```json
{
  "schema_version": "siq_ic_human_methodology_approval_v3",
  "deal_id": "<release Deal ID>",
  "status": "approved",
  "approved_by": {
    "id": "<methodology owner directory ID>",
    "name": "<methodology owner full name>"
  },
  "approved_at": "<timezone-aware ISO-8601 timestamp>",
  "methodology_version": "<reviewed PMIC methodology version>",
  "scope": "primary_market_ic_behavior_release",
  "golden_case_suite_id": "<suite_id from golden_case_bindings.json>",
  "golden_case_bindings_sha256": "<SHA-256 of the exact reviewed binding file bytes>",
  "report_binding": {
    "report_id": "<current R4 report_id>",
    "revision": 1,
    "evidence_snapshot_hash": "<current R4 Evidence snapshot hash>"
  },
  "human_confirmation_binding": {
    "status": "confirmed",
    "confirmed_by": {
      "id": "<exact server-derived confirmer ID>",
      "username": "<exact server-derived confirmer username>"
    },
    "confirmed_at": "<exact stored confirmation timestamp>",
    "audit_event_created_at": "<exact matching audit event timestamp>",
    "attestation_schema_version": "siq_ic_human_confirmation_attestation_v1",
    "report_id": "<exact attested report_id>",
    "report_revision": 1,
    "workflow_run_id": "<exact attested workflow_run_id>",
    "evidence_snapshot_hash": "<exact attested snapshot hash>",
    "decision_sha256": "<exact server-generated attestation digest>",
    "quality_sha256": "<exact server-generated attestation digest>",
    "factcheck_sha256": "<exact server-generated attestation digest>"
  }
}
```

The template's revision value `1` is illustrative. Replace both revision fields with the exact current integer revision. Copy the confirmation attestation fields exactly; do not recompute them with a different JSON serializer. Time ordering is mandatory: `confirmed_at` must be no later than the matching audit event, and `approved_at` must be later than both the confirmation and its audit event. Any later change to a candidate result, binding file, R4 decision, quality report, factcheck report, confirmation, or audit invalidates the approval and requires a new review.

## Run the gate

Use the `Primary-market IC release gate` workflow manually with `run-release=true`, or let the nightly schedule execute it. A missing variable, missing golden binding, invalid report, nonzero gate result, `passed != true`, or `release_eligible != true` fails the job. The JSON/Markdown report is uploaded even when the v3 evaluation produces blockers.

The equivalent local command is:

```bash
python3 scripts/maintenance/run_primary_market_ic_release_gate.py \
  --bundle "$SIQ_PMIC_RELEASE_BUNDLE" \
  --manifest agents/hermes/profiles/siq_ic_shared/golden_case_manifest.json \
  --profile-matrix agents/hermes/profiles/siq_ic_shared/ic_profile_matrix.json \
  --factcheck-report "$SIQ_PMIC_FACTCHECK_REPORT" \
  --real-smoke-report "$SIQ_PMIC_REAL_SMOKE_REPORT" \
  --human-approval "$SIQ_PMIC_HUMAN_APPROVAL" \
  --output-json artifacts/eval-runs/primary-market-ic/ci-release/release-gate.json \
  --output-markdown artifacts/eval-runs/primary-market-ic/ci-release/release-gate.md
```

The release report schema must be `siq_primary_market_ic_behavior_release_gate_v3`. Versioned domain artifacts may still legitimately use their current v1/v2 schema IDs; that does not mean the workflow is invoking an older release gate. Formal model tasks must use prompt contract v5, and v3 validates that binding. A valid release result has `passed: true` and `release_eligible: true`, while retaining `quality_accepted_written: false` and `candidate_promotion_performed: false`.

## Promote only through a reviewed change

A passing gate establishes release eligibility only. It does not mutate the golden manifest, promote a candidate, or write `quality_accepted`. Archive the immutable v3 JSON/Markdown gate result and the reviewed input digests before promotion.

Promotion is a separate pull request reviewed by the designated PMIC governance owner. That pull request may update `agents/hermes/profiles/siq_ic_shared/golden_case_manifest.json` and the corresponding entries in `openclaw_script_migration_matrix.json`; it must cite the PASS gate artifact, golden binding digest, methodology approval identity, and reviewed methodology version. Update only behaviors covered by the accepted evidence, preserve known gaps for uncovered behavior, and record the named reviewer and review time according to repository governance.

Do not add an automatic promotion command to the evaluator, release gate, CI workflow, or fixture generator. Do not commit an approval synthesized by a test or service account. The candidate gate intentionally evaluates pre-promotion state, so obtain and archive its PASS result before the reviewed manifest/matrix change; future candidate suites must use a new suite identity rather than rewriting the evidence behind an accepted one.
