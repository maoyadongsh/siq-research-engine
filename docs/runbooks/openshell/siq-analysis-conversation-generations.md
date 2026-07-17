# SIQ Analysis Conversation Sandbox Generations

## Purpose

The frontend keeps one visible analysis conversation while the API binds each
Hermes execution to an OpenShell sandbox generation derived from the verified
security scope. A conversation is not permanently attached to one broad
sandbox.

The effective scope is:

```text
tenant/user + profile + company scope + sandbox run + provider/write policy
```

The OpenShell pool admission adds a deterministic conversation-affinity digest
to the company/run namespace. Repeated requests from the same conversation and
company therefore reuse the same Hermes namespace while the sandbox run stays
alive. A company change or sandbox recreation produces a different namespace
and `sandbox_generation_id`.

## Routing Contract

- Only `siq_analysis` is eligible for this lifecycle.
- A verified single-company context is required for automatic provisioning.
- An existing healthy company binding is reused.
- A missing company binding is created on demand and probed before routing.
- Concurrent requests for the same missing scope serialize behind one API lock.
- Pool leases remain the write-concurrency authority.
- Implicit provisioning failure falls back to Host; an explicit OpenShell
  request fails closed.
- Missing or ambiguous company context does not create a sandbox.

The runtime provenance attached to completed responses includes:

```text
runtime_target
canary_run_id
sandbox_generation_id
sandbox_scope_id
sandbox_company
```

No credential, raw owner token, lease identity key, or full internal namespace
is returned in provenance.

## Company Changes

For one frontend conversation:

```text
600104 -> 600104  reuses the current generation
600104 -> 600519  selects or creates a different generation
600519 -> 600104  returns to the valid 600104 generation if still warm
```

History and agent memory are filtered by the verified research identity before
the OpenShell run is created. Sandbox-local sessions, checkpoints, response
stores and writable analysis paths remain bound to the selected company/run.

## Idle Reclamation

Automatic provisioning is controlled by:

```text
SIQ_OPENSHELL_SCOPE_AUTO_PROVISION=1
SIQ_OPENSHELL_SCOPE_IDLE_TTL_SECONDS=300
SIQ_OPENSHELL_SCOPE_SWEEP_SECONDS=30
```

The sweeper only stops a binding after its idle TTL and only when active,
waiting and orphaned lease counts are all zero. Stop uses the same maintenance
lock and identity-checked lifecycle as the operator CLI. The next request can
create a fresh run, which necessarily creates a new generation ID.

## Multi-company Requests

The current automatic provisioner accepts one verified company scope. It must
not widen a single-company sandbox to all company roots based only on model
interpretation of the prompt.

A future multi-company generation requires a separate mount contract:

- every source company is read-only;
- the output is written to an isolated comparison workspace;
- no source company's normal `analysis/` root is directly writable;
- the normalized company set becomes part of the scope and generation ID;
- publication into a company analysis root is a separate Host-side operation.

Until that contract is implemented and tested, multi-company prompts continue
through the existing safe fallback rather than receiving broad sandbox access.

## Operational Verification

```bash
scripts/openshell/switch_siq_analysis_runtime.sh status
python3 scripts/openshell/siq_analysis_pool_registry.py list
python3 scripts/openshell/siq_analysis_pool_concurrency.py status
```

For each active binding, lifecycle status and probe remain authoritative:

```bash
scripts/openshell/run_siq_analysis_pool_lifecycle.sh status \
  --market cn --company COMPANY --run-id RUN_ID

scripts/openshell/run_siq_analysis_pool_lifecycle.sh probe \
  --market cn --company COMPANY --run-id RUN_ID
```
