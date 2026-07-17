# SIQ Hermes BYOC Sandbox

This image is the production-shaped `siq_analysis` sandbox. It is not enabled
by default and does not change `scripts/hermes/run_gateway.sh`.

The image contains only:

- the frozen Hermes `0.13.0` source and reviewed SIQ patch;
- the tracked `siq_analysis` and shared profile code;
- a compiled, secret-free copy of the current runtime config;
- pinned Python/Node dependencies and Chinese fonts;
- empty writable Hermes state paths required before Landlock starts.

It deliberately excludes Wiki data, `.env`, `auth.json`, sessions, logs,
database files from the host, OpenShell state and Docker control sockets.
`data/wiki` and selected writable task/state paths are separate runtime mounts;
the whole repository must never be mounted into this image.

The lifecycle-only smoke uses a private directory bind and records two WAL and
runtime-metadata generations without starting Hermes. Its proof is scoped to
the image/container path (`readiness_effect=none`); it does not replace formal
OpenShell mount, Landlock, or gateway evidence.

The image healthcheck has two deliberately separate contracts. A direct
`docker run` verifies authenticated Hermes loopback HTTP. An OpenShell-managed
container verifies the exact outer supervisor and Hermes child identities,
because Docker health commands do not join the nested sandbox network
namespace. Formal business readiness remains the lifecycle's authenticated
host-forward plus `sandbox exec` HTTP checks; Docker health alone is never a
traffic-readiness receipt.

Build without starting a sandbox:

```bash
scripts/openshell/prepare_siq_analysis_context.sh
scripts/openshell/build_siq_analysis_image.sh
scripts/openshell/smoke_siq_analysis_image.sh
scripts/openshell/smoke_siq_analysis_image.sh --runtime-lifecycle-only
```

The generated context and image metadata live under ignored
`var/openshell/siq-analysis/`. A later lifecycle step must still prove exact
mount identity, read-only database access, model/search routing, snapshot
recovery and A/B quality before traffic can switch from the host runtime.

The same image contains `/opt/siq/observe-entrypoint.sh` for the explicitly
acknowledged, disposable feasibility path documented under
`infra/openshell/poc/siq-analysis-observe/`. That entrypoint copies the embedded
profile into `/sandbox`, never mounts host business data, and has no effect on
formal readiness or the default host runtime.
