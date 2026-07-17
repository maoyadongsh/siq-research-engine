# Minimal Hermes PoC

This fixture validates the existing SIQ-patched Hermes `0.13.0` API inside
OpenShell `0.0.83`. It is deliberately not an output-quality benchmark.

Security boundaries:

- rebuild Hermes from the frozen rollback bundle, commit and dirty patch;
- copy a source allowlist into an ignored, mount-scanned build context;
- do not mount the SIQ repository or any host state;
- use no real model, search or database credential;
- keep the deterministic model stub and Hermes API on sandbox loopback;
- expose only the dedicated host port `127.0.0.1:28642` through OpenShell;
- run Hermes as uid/gid `10001`, with Landlock required and no public network
  policy.

The ARM64 `0.0.83` supervisor has two observed image requirements that are
stricter than the published generic support floor: glibc must provide
`GLIBC_2.38` and `GLIBC_2.39`, and `iproute2` must provide a trusted `ip`
binary. The pinned Python 3.11.15 trixie image satisfies both and includes
`nftables` so proxy-bypass detection does not start in degraded mode.

OpenShell `0.0.83` also applies directory-only `ReadDir` rights to every
Landlock rule. Individual `/dev/null` and `/dev/urandom` rules from upstream
examples therefore fail in `hard_requirement` mode. SIQ keeps those exact
paths and uses the project patch under `infra/openshell/patches/v0.0.83/` to
select file-only rights from the already-open file descriptor. `/dev` is not
broadened and Landlock remains a hard requirement.

Run through the project scripts:

```bash
scripts/openshell/build_patched_supervisor.sh
scripts/openshell/prepare_hermes_poc.sh
scripts/openshell/build_hermes_poc.sh
scripts/openshell/start_hermes_poc.sh
scripts/openshell/smoke_hermes_poc.sh
scripts/openshell/stop_hermes_poc.sh
```

`start_hermes_poc.sh` creates a random, one-run Bearer key under the ignored
`var/openshell/poc/hermes-minimal/api.key`, passes it explicitly to the
sandbox, and removes it during stop/rollback. The key is never part of the
image or Git. The script also injects `HOME` and `HERMES_HOME` explicitly;
OpenShell initial commands must not assume image environment variables are
present.

Each run also gets a 192-bit nonce stored in ignored `0600` state and in the
gateway sandbox labels. Stop and rollback cross-check that nonce with the
gateway sandbox ID and Docker's managed sandbox ID/name/namespace before
deletion. Missing or conflicting identity state fails closed. The contract
test verifies that missing and incorrect Bearer keys are rejected with HTTP
401 before exercising normal, terminal-tool and cancellation flows.

The PoC is intentionally one profile at a time. The production shape is one
independent sandbox and policy per Hermes profile, with the existing host
runtime remaining the default until profile-specific A/B checks pass.
