# SIQ DevOps Hardening Observe Round

Scope: P2-03 observe pass from `2026-07-06-siq-risk-calibrated-optimization-plan.md`.

## Landed

- Compose application services now have explicit low-risk runtime boundaries:
  `read_only`, `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges`, pids limits, memory/CPU knobs, and healthchecks.
- `web`, `api`, `pdf-parser`, `document-parser`, and `market-report-rules` run with compose-level non-root users where the image layout can support it without secret or data migration.
- `api` healthcheck checks its own `/health` plus TCP reachability for Postgres, Redis, PDF parser, and document parser.
- Parser and market-report service healthchecks use their existing in-container HTTP health endpoints.
- Postgres and Redis received healthchecks and resource limits only; stronger rootfs/capability restrictions remain deferred because their official entrypoints may need initialization privileges on first local volume creation.
- `scripts/ci/observe_security_artifacts.sh` generates observe-only security artifacts:
  - `artifacts/security/trivy-high-observe.sarif`
  - `artifacts/security/siq-fs-sbom.cyclonedx.json`
  - `artifacts/security/observe-summary.txt`
- CI uploads `artifacts/security/` as `siq-security-observe`.

## Guardrails

- Trivy HIGH/CRITICAL observe output uses `--exit-code 0` and does not block CI or `scripts/check_all.sh`.
- The existing CRITICAL filesystem scan remains the hard security gate.
- If local Trivy is missing, the observe script tries Docker Trivy. If neither is available, it writes skipped artifacts and exits successfully.
- No real secrets, tokens, databases, downloaded filings, runtime state, or generated security artifacts are committed.

## Deferred Risks

- Digest pinning for base images and service images remains observe-only. Pinning requires registry digest validation and a follow-up image refresh path.
- `services/market-report-finder` still needs a Dockerfile-level non-root user and owned `/state` paths before compose can safely force non-root while preserving writable downloads.
- Postgres, Redis, and Grafana still need a service-specific hardening pass for `read_only` and capability drops after validating first-boot volume initialization.
- Healthchecks are now stronger but still intentionally shallow for expensive external model readiness; MinerU/VLM availability should remain a parser readiness signal, not a compose hard fail.
