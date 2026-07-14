# Deterministic Fallback Identity

Deterministic artifacts are allowed for preview, recovery, tests, or explicit model failure. They are not Hermes expert behavior.

Every fallback artifact and API response must include:

- `generation_mode` beginning with `deterministic_`;
- `fallback_reason` and failed/unavailable model phase;
- input artifact and Evidence snapshot identities;
- capability restrictions and known omissions;
- `requires_human_review: true` for formal use.

The UI must display `deterministic fallback` with warning treatment. A fallback cannot be promoted to model output by renaming the file or changing only the status field.
