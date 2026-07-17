# Formal Egress And Structured Audit Evidence

This runbook produces the two completion-eligible network artifacts from one
already-running formal `siq_analysis` transaction. It does not create, stop,
repair, or cut traffic to a sandbox.

## Scope

The runner proves all of the following against one unchanged transaction,
image, policy, mount contract, runtime config, sandbox, and host receipt:

- public GET and HEAD are allowed through the explicit egress broker;
- a bounded JSON POST is `audit_only` and still reaches the public target;
- multipart, octet-stream, PUT, oversized JSON and an approved-host rule
  mismatch are denied before any upload;
- metadata, direct TCP, direct UDP and direct WebSocket paths are denied;
- the real `curl`, `scp`, `sftp`, `rsync` and `rclone` binaries have no policy
  permission for a controlled TCP/UDP receiver bound to the verified Docker
  bridge gateway, and that receiver observes zero connections;
- all 17 cases have canonical `siq.openshell.audit.v1` records bound to the
  formal run and one policy digest;
- only counts, stable rules, decisions and SHA-256 projections enter the
  public evidence.

It does not replace the same-transaction business run or A/B evaluation.
Before this runner, the formal transaction must separately complete the
protected `/v1/runs` terminal flow that exercises the intended model,
approved search provider, and normal task download/parse workflow. Provider
traffic continues to use OpenShell providers; the generic egress broker must
not be used as a substitute provider path.

## Prerequisites

1. Rebuild the candidate image after the Dockerfile change and pass both
   disconnected image smoke checks.
2. Restart the strict host brokers from the current source bundle. A broker
   started from an older `egress_guard.py` cannot emit the parse-denial audit
   record and must fail the source binding.
3. Refresh the host component proof:

   ```bash
   python3 scripts/openshell/run_egress_boundary_proof.py --project-root "$PWD"
   ```

4. Start exactly one formal lifecycle transaction, complete the normal
   business checks described above, and leave it in `running` state.
5. Confirm that none of these output files already exists:

   ```text
   artifacts/openshell/v0.6/formal-egress-sandbox.sanitized.json
   artifacts/openshell/v0.6/formal-egress-sandbox.sanitized.md
   artifacts/openshell/v0.6/formal-structured-audit.sanitized.json
   artifacts/openshell/v0.6/formal-structured-audit.sanitized.md
   ```

## Run

Use the exact lifecycle run ID; do not pass a sandbox name or invent a
transaction receipt:

```bash
python3 scripts/openshell/run_formal_egress_audit.py \
  --project-root "$PWD" \
  --run-id "$RUN_ID"
```

The runner attaches through the existing lifecycle identity, validates the
active OpenShell policy and 7+5 mount contract before and after the probes,
and checks the strict broker source bundle twice. It never reads an API key,
broker token, request body, response body, Prompt, SQL, vector or business
artifact on the host. The sandbox probe reads its already-scoped broker token
from its own environment and never prints it.

A transfer-client case is not accepted merely because the command exits
non-zero. The runner rejects local syntax failures and any protocol/authentication
failure that reaches the controlled receiver; only an explicit network-refusal
class plus zero observed TCP/UDP connections and no controlled-endpoint permission
can produce `direct_egress_denied`.

## Outputs

Success prints only the two schema versions, case count, selected audit count,
decision and private receipt mode. The four public files are installed with
exclusive creation only after both schemas and the sanitizer pass.

Private source material remains ignored under:

```text
var/openshell/proofs/formal-egress-audit/<run-id>/
```

`selected-audit.jsonl` contains only the 20 canonical records selected from
the real audit append window: 17 security cases plus lifecycle-before,
loopback health preflight and lifecycle-after observations. These three runner observations are not
classified as business events; formal business capability is supplied only by the separately validated
business-route receipt. `receipt.json`
contains runtime identifiers and before/after SHA receipts, but no credential
or request/response material. Neither private file is publishable.

The loopback health preflight is recorded as `service.preflight`, not
`runtime.route`; consequently this artifact publishes zero gateway-route
latency samples. Route capability and output quality remain the responsibility
of the separately bound route/A-B evidence.

After success, continue with the remaining formal probes and the normal
`rollback_to_host` lifecycle action. Do not stop the sandbox before all
same-transaction evidence runners finish.

## Failure And Retry

Any missing client, unexpected allow, audit gap, extra same-run record,
identity mismatch, stale host proof, policy drift, mount drift, transaction
change, sanitizer finding or existing output returns `NO_GO`. Public evidence
is not created. Minimal failed probe audit records may remain in the private
append-only audit file; they contain no payload and do not make a later retry
pass because each attempt uses a fresh byte cursor.

Do not repair a failure by widening the policy or routing through host Hermes.
Fix the concrete runtime or evidence issue, confirm the same transaction is
still healthy and unchanged, and retry. If the transaction changed or stopped,
start a new formal transaction and regenerate every evidence item that must
share its provenance.

## Publication Checks

After all formal evidence is complete, add the four public files to the exact
tracked artifact manifests and run the repository sanitizer, tracked-state
check, completion gate, Git index secret scan and stage-zero binding. Never
add `var/openshell/audit` or `var/openshell/proofs` to Git.
