# siq_analysis OpenShell Observe PoC

> NOT_PRODUCTION / OBSERVE-ONLY. This path proves feasibility. It must not
> receive SIQ API traffic and it is not evidence that V0.6 is production-ready.

This PoC is the shortest real `siq_analysis` path through OpenShell. It uses the
production-shaped SIQ image and the configured `siq-minimax-cn-pool` provider,
but deliberately omits the formal task mounts, Wiki/database access, egress and
data brokers, Exa, and local fallback readiness requirements.

The full start/smoke/stop sequence passed on 2026-07-16. The sanitized result is
stored under `artifacts/openshell/v0.6/siq-analysis-observe-20260716/`. This is a
repeatable feasibility result, not a production-readiness or quality result.

The isolation contract is intentionally simple:

- sandbox name is fixed to `siq-analysis-observe-poc`;
- host exposure is loopback-only at `127.0.0.1:28651`;
- host Hermes remains at `127.0.0.1:18651` and is never stopped or reconfigured;
- no host project path, Wiki, database, profile state, Docker socket, credential
  file, or OpenShell runtime directory is mounted;
- the embedded project/profile is read-only;
- Hermes writes config, SQLite/WAL, sessions, logs and gateway state only under
  the disposable `/sandbox/siq-analysis-observe/hermes-home`;
- the already configured MiniMax OpenShell provider remains primary;
- the only direct internal-service route is the currently available Nemotron
  fallback at `host.openshell.internal:8007`; offline `8004/8006`, Exa and all
  database/API ports are outside this PoC policy;
- the API key and run nonce are random `0600` files under ignored
  `var/openshell/poc/siq-analysis-observe/` and are deleted on a verified stop.

## What It Proves

The smoke contract requires all of the following over the OpenShell forward:

1. authenticated Hermes `/health` succeeds and unauthenticated `/v1/runs` is
   rejected;
2. a real `siq_analysis` run is created with HTTP 202;
3. SSE emits `message.delta`, `tool.started`, `tool.completed` and
   `run.completed`;
4. the model invokes one terminal calculation and returns
   `SIQ_OBSERVE_SUM=16`;
5. a second run accepts `/stop` and terminates as `run.cancelled`;
6. the embedded project remains read-only while the disposable Hermes home is
   writable;
7. Docker inspection finds no business-data or host-state mount.

It does **not** prove report quality, fallback parity, Tavily/Exa, Wiki/database
behavior, immutable-path enforcement, broker identity, upload controls, A/B
equivalence, rollback of formal task state, or production readiness.

## Run Manually

The start command requires an explicit acknowledgement and refuses any existing
owner of port `28651` or the fixed sandbox name:

```bash
scripts/openshell/start_siq_analysis_observe_poc.sh --acknowledge-not-production
scripts/openshell/smoke_siq_analysis_observe_poc.sh
scripts/openshell/stop_siq_analysis_observe_poc.sh
```

`start_siq_analysis_observe_poc.sh` builds the pinned SIQ image if necessary,
checks only the isolated OpenShell gateway/supervisor and the one required
provider, and then starts the disposable sandbox. It does not require the full
formal service preflight and does not alter `start_all.sh`; `8007` availability
remains a useful fallback, not a prerequisite that the script starts or repairs.

Always run the stop command after a failed smoke. Start has its own best-effort,
identity-checked rollback; if rollback cannot prove the resource identity it
fails closed and retains the nonce/PID state for manual inspection instead of
deleting by name alone.
