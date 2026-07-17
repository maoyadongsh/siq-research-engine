# SIQ Analysis OpenShell Canary

This directory documents the independent `NOT_PRODUCTION_CANARY` lifecycle.
The implementation intentionally reuses the reviewed wide-pilot lifecycle
mechanics instead of maintaining a second sandbox creation stack.

Contract:

- state: `var/openshell/canary/siq-analysis/`;
- explicit acknowledgement: `--acknowledge-not-production-canary`;
- run ID: `canary-<12hex>`;
- endpoint: `127.0.0.1:28651`;
- providers: MiniMax, StepFun, Kimi, Tavily;
- mounts: seven business mounts plus five read-only OpenShell control mounts;
- write scope: the existing selected-company `analysis/` root;
- immutable scope: company facts/reports, other companies, code, configuration,
  prompts, workflows and OpenShell control state;
- normal create/modify/rename/delete is allowed inside `analysis/`; only root or
  bulk destructive deletion crosses the deletion-guard threshold;
- host runtime and formal readiness remain unchanged.

Operational commands and failure recovery are defined in
`docs/runbooks/openshell/siq-analysis-canary.md`.
