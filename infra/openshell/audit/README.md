# OpenShell Audit Evidence Pipeline

This directory documents the T8 audit aggregation and competition-evidence export
workflow. It does not contain runtime audit records or exported evidence.

## Components

```text
scripts/openshell/security_audit.py
  writes the fixed siq.openshell.audit.v1 runtime record

scripts/openshell/aggregate_security_audit.py
  reads only explicitly named JSONL files and produces a safe aggregate JSON

scripts/openshell/export_sanitized_evidence.py
  reads only explicitly named JSON/Markdown files and writes paired
  *.sanitized.json and *.sanitized.md files under an explicit output root

scripts/openshell/check_sanitized_artifacts.py
  validates every file produced by the exporter before the export succeeds
```

No script implicitly scans `var/openshell`, a user home, or a log directory. Shell
globs and directories are not accepted as audit inputs. Callers must provide each
regular input file with a separate `--input` argument. Symlinked inputs, output roots,
or parent directories fail closed.

## Aggregate metrics

The aggregate schema is `siq.openshell.audit-summary.v1`. It validates every source
record against the exact field set and value constraints of
`siq.openshell.audit.v1`, then emits:

- policy deny count;
- audit-only count;
- sandbox start failures;
- tool operation count, failure count, and failure rate;
- external upload blocks;
- immutable path write blocks;
- P50/P95 duration for `runtime.route` gateway samples;
- counts by decision, operation class, profile, policy digest, and deny error/rule ID.

Input filenames, input paths, sandbox IDs, run IDs, and session IDs are not copied to
the summary. Source files are represented only by SHA-256 digests and file count.
P50/P95 use linear interpolation over sorted millisecond samples. Tool operations are
records whose projected target scope is `tool`/`tool.*` or whose error code starts
with `tool_`. Upload blocks are denied `network.request` records with a fixed upload
or transfer rule/error ID. Sandbox start failures are denied `sandbox.lifecycle`
records with the fixed start scope/error convention.

Example using explicit paths:

```bash
python scripts/openshell/aggregate_security_audit.py \
  --input /explicit/review-copy/audit-part-1.jsonl \
  --input /explicit/review-copy/audit-part-2.jsonl \
  --output /explicit/review-work/audit-summary.json
```

The example paths are placeholders. Do not point automated tests or CI at real
runtime audit state.

## Sanitized evidence export

The exporter removes sensitive JSON keys and Markdown sections for API credentials,
tokens, Authorization/cookie data, DSNs, user home data, Prompt/user-input bodies,
request bodies, and attachment bodies. It also drops one-run runtime identity fields
(sandbox/container/probe IDs, nonce digests, sentinel names, and run-specific policy
or mount-plan paths); these fields are useful for local cleanup but add no review
value to a committed aggregate. Credential URLs, private-key markers, bearer values,
home references, and POSIX/Windows absolute machine paths embedded in text are
redacted. It retains review-safe values such as profile, rule/error ID, decision,
latency, success/failure metrics, quality score, version, and policy/mount digest.

Each explicit input creates a pair:

```text
<name>.sanitized.json
<name>.sanitized.md
```

Existing outputs are never overwritten. Name collisions fail before writing. After
all files are created, the exporter calls `check_sanitized_artifacts.scan_paths` with
the exact output file list. Any finding removes all files created by that invocation
and returns a non-zero status.

```bash
python scripts/openshell/export_sanitized_evidence.py \
  --input /explicit/review-work/audit-summary.json \
  --input /explicit/review-work/quality-summary.md \
  --output-root /explicit/review-work/sanitized
```

The exporter does not modify Git ignore rules or the tracked artifact manifest. A reviewer
must inspect the sanitized pair and run the tracked-state checks before it can be committed.
Sanitized audit and operational log bundles are publishable competition evidence. Raw JSONL,
unsanitized gateway logs, traces, prompts, attachments, and session databases must never be
committed.

## Unit tests

All tests use generated temporary fixtures and never open a real runtime log:

```bash
PYTHONPATH=. pytest -q scripts/openshell/tests/test_audit_evidence_pipeline.py
PYTHONPATH=. pytest -q scripts/openshell/tests/test_security_audit.py
PYTHONPATH=. pytest -q scripts/openshell/tests/test_check_sanitized_artifacts.py
```
