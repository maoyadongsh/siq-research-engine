# Provider-Independent OpenShell Probe

- Result: `PASS`
- Scope: deny-all, no Hermes business process or provider call
- Mount contract: 7 business + 5 read-only control mounts
- OpenShell control credentials: runtime-readable, not writable
- Other sensitive paths: hidden
- Sandbox, sentinels and runtime snapshot: removed

Runtime identities, paths, credentials and business content are excluded.
