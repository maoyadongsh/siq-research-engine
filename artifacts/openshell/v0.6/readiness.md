# SIQ OpenShell V0.6 Readiness

Decision: **NO_GO for formal Hermes traffic cutover**. `SIQ_HERMES_RUNTIME=host`
remains the default and only automatic business traffic path.

Verified through 2026-07-16:

- OpenShell `0.0.83`, Hermes `0.13.0`, and the current ARM64 candidate image
  smoke passed.
- The project gateway and strict `18792/18793` host brokers are healthy. Broker
  status/stop now verify the recorded command independently of the Python used
  by the checker, while retaining PID, start ticks, exact argv, command digest,
  listener, bridge, identity-key, and PIDFD revalidation.
- The current provider-independent deny-all probe passed `44` controls with the
  `7` business + `5` read-only control mount contract. It verified immutable
  source/code/configuration/Prompt/workflow boundaries, writable task/session/
  memory surfaces, process hardening, unknown-upload denial, and cleanup.
- A NOT_PRODUCTION wide business Pilot passed on the current candidate image. It
  mounted a real finalized source read-only, allowed only one analysis output
  leaf, started Hermes, used four OpenShell providers, completed a real Tavily
  query and a Bearer-protected `/v1/runs` terminal workflow, preserved the
  source, removed the output, deleted the sandbox, and left host Hermes unchanged.
- The Pilot has `readiness_effect=none`. It is a single-company, single-run
  feasibility result and is not formal A/B or quality-equivalence evidence.
- PostgreSQL uses six fixed database/schema routes with a verified dedicated
  read-only role. The refreshed Milvus boundary proof passed: broker describe,
  get, query, and search are available, direct `19530` and mutation routes are
  denied, business rows modified remain `0`, and cleanup is verified.
- Host-owned conversation memory remains writable through the logical
  `siq_agent_memory_active` alias for primary and secondary market groups. A
  real zero-residual probe passed PostgreSQL insert/readback/rollback and Milvus
  upsert/get/search/delete for both groups. The OpenShell sandbox receives no
  direct Milvus or PostgreSQL write credentials.
- The host egress broker passed a real component proof for public GET/HEAD,
  unknown small JSON audit-only, strict identity, multipart/octet-stream/PUT and
  metadata denial. Its `readiness_effect=none`; formal sandbox bypass/provider
  evidence remains required.
- The service preflight is now blocked only by required local fallbacks `8004`
  and `8006`. Ports `8007` and `8013`, SIQ API, host Hermes, PostgreSQL, Milvus,
  and both database security proofs pass. No service was started or removed from
  policy to change this result.
- MiniMax, StepFun, Kimi, and Tavily providers are configured. Exa remains
  missing. The earlier host Kimi authentication/fallback observation and SSE
  transport reset remain baseline blockers; no model, Prompt, fallback order,
  or credential was changed to hide them.
- The OpenShell test suite passes `930` tests. API memory suites pass `58` tests.
  Sanitized artifact scanning and the manifest-bound Git index allowlist scan
  both pass with zero findings.

Cutover remains blocked by Exa, offline `8004/8006` fallback routes, unresolved
host baseline observations, unverified external reverse proxy behavior, missing
formal multi-case A/B and quality equivalence, missing formal rollback/delete
evidence bindings, and human architecture/security review. No claim is made that
SIQ business quality is unchanged until those formal gates pass.
