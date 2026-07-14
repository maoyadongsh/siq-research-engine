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

The bundle's golden binding must point to five distinct Deal/run/result packages for conditional support, material risk, insufficient evidence, full R3, and stale snapshot. The gate verifies their file digests and scenario assertions; copying one run under several case IDs fails. The methodology approval must include `golden_case_bindings_sha256`, the SHA-256 digest of the exact `release/golden_case_bindings.json` bytes reviewed by the approver.

## Prepare independent golden inputs

The committed suite contains five synthetic, input-only Deal packages. Regenerate the four scenario variants and verify all committed bytes before any live run:

```bash
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py --check
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py
uv run --project apps/api python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py --check
```

`eval_datasets/primary_market_ic_real_smoke/golden_suite_manifest.json` is an input inventory only. Its per-case `input_identity` binds the deterministic input bundle digest, fixture contract digest, initial Evidence snapshot, and file count; the generator's `--check` validates those bindings as well as every fixture byte. Every case remains `result_status: not_run` and `quality_accepted: false` until an isolated real run is evaluated. Use a different run root for each fixture:

```bash
uv run --project apps/api python scripts/hermes/run_primary_market_ic_real_smoke.py \
  --fixture "eval_datasets/primary_market_ic_real_smoke/$DEAL_ID" \
  --run-root "artifacts/eval-runs/primary-market-ic/golden/$DEAL_ID" \
  --phase R0 --phase R1 --phase R1.5 --phase R2 --phase R3 --phase R4 \
  --timeout 2400 --real
```

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

After all five independent candidates validate, bind them to the release Deal. Repeat `--case-bundle` once for each required scenario:

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

The binding remains a candidate artifact with `quality_accepted: false`. The evaluator never changes the manifest, marks a case accepted, or converts a failed path into a passing assertion. Distinct Deal IDs, real-smoke run IDs, result IDs, bundle paths, and result SHA-256 digests are mandatory.

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

The release report schema must be `siq_primary_market_ic_behavior_release_gate_v3`. Versioned domain artifacts may still legitimately use their current v1/v2 schema IDs; that does not mean the workflow is invoking an older release gate. Formal model tasks must use prompt contract v5, and v3 validates that binding.

A passing gate establishes release eligibility only. It does not mutate the golden manifest, promote a candidate, or write `quality_accepted`; promotion remains a separate reviewed governance action.
