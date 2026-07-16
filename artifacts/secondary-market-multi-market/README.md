# Secondary-Market Multi-Market Acceptance Evidence

This directory contains only sanitized release-gate evidence for the CN, HK,
US, EU, KR, and JP analysis, fact-checking, and tracking rollout.

- `real-smoke.sanitized.json` records authoritative identity fields, adapter
  family/version, terminal status, artifact IDs, content hashes, and immutable
  fact-surface hashes. It excludes report bodies, prompts, credentials, and
  local filesystem paths.
- `ui-analysis-mobile-375.png` and `ui-analysis-desktop-1440.png` are mocked-API
  UI acceptance screenshots used to check control order, text fit, and layout.

The synthetic golden contract matrix is versioned separately at
`apps/api/tests/golden/secondary_market_multi_market_sidecars.json`.

Reproduce the real-Wiki gate from the repository root with deterministic
parsed-ready sample selection:

```bash
SIQ_MULTI_MARKET_RESEARCH_ENABLED=1 \
SIQ_US_SEC_ANALYSIS_ENABLED=1 \
uv run --project apps/api python \
  scripts/maintenance/run_secondary_market_multi_market_real_smoke.py
```

The runner treats CN as a read-only legacy golden regression and never invokes
the new renderer for it. HK, US, EU, KR, and JP must each publish exact-identity
analysis, factcheck, and tracking artifacts. The gate also requires every
protected fact-surface digest to remain unchanged. Tracking runs with external
search and sentiment disabled, so unavailable source coverage is recorded as
degraded rather than simulated success.
