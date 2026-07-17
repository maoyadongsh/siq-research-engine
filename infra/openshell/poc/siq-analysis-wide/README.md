# siq_analysis OpenShell Wide Business Pilot

> NOT_PRODUCTION. This pilot proves one real SIQ data path through the formal
> OpenShell image and isolation assets. Its result is always
> `readiness_effect=none` and is not formal A/B, rollout, or completion evidence.

This is the next bounded step after `siq-analysis-observe`. It keeps the host
Hermes endpoint at `127.0.0.1:18651` untouched while exposing the pilot only on
`127.0.0.1:28651`. It uses one real company and performs exactly this business
operation:

```text
read <company>/company.json
  -> Hermes /v1/runs and SSE
  -> one terminal tool call
  -> write <company>/analysis/.work/pilot-<12hex>/result.json
  -> validate source digest and result schema
  -> remove result.json and its unique pilot directory
```

The host `data/wiki` mount is read-only. The selected company `analysis/`
directory is the one writable bind required by the formal mount architecture,
but Landlock narrows the task write rule to the new, empty `pilot-*` leaf. The
agent cannot write `company.json`, a sibling `.work` path, project code, prompts,
workflow, OpenShell control state, or another company. A host deletion guard
snapshots and watches the selected `analysis/` tree for the entire pilot.

## Capability Scope

The pilot uses only capabilities already present on the host:

- `siq-minimax-cn-pool`, `siq-stepfun`, `siq-kimi-coding`, and
  `siq-tavily-search` OpenShell providers;
- strict `18792` egress and `18793` read-only data brokers with separate,
  audience-bound HMAC request identities;
- the formal candidate image, credential-free runtime snapshot, fixed seven
  business mounts, compiled task policy, patched Landlock supervisor, loopback
  forward, API authentication, verified sandbox identity, and deletion guard;
- direct Tavily provider validation that emits only success and result count,
  never the query response, URLs, titles, or excerpts.

It does not claim that Exa, local ports `8004/8006`, Milvus formal proof,
fallback parity, report quality, or public rollout is ready. The host Clash Meta
TUN currently maps public DNS into `198.18.0.0/15`; generic egress-guard requests
can therefore fail SSRF public-IP checks. That compatibility issue remains an
explicit blocker and is not hidden by this pilot. Tavily is tested through its
OpenShell provider route instead.

## Isolation Contract

- explicit start acknowledgement:
  `--acknowledge-not-production-wide-pilot`;
- state root: `var/openshell/poc/siq-analysis-wide/`;
- sandbox: `siq-analysis-wide-pilot-<12hex>`;
- lifecycle label: `siq-analysis-wide-pilot-not-production-v1`;
- fixed endpoint: `127.0.0.1:28651`;
- formal transaction and `var/openshell/siq-analysis/active.json` are never
  created or modified;
- host runtime remains the only default traffic path;
- secrets and raw logs remain owner-only under ignored `var/openshell/`;
- manifest records `result_is_formal_evidence=false` and the unresolved formal
  blockers.

Run and failure recovery are documented in
`docs/runbooks/openshell/siq-analysis-wide-pilot.md`.
