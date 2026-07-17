# SIQ Runtime Lifecycle Smoke

The image smoke has a provider-independent mode:

```bash
scripts/openshell/smoke_siq_analysis_image.sh --runtime-lifecycle-only
```

This mode validates the candidate image and then runs
`runtime_state_lifecycle_smoke.py` through `infra/openshell/sandbox/entrypoint.sh`.
The probe uses `--network none`, a read-only container root, dropped
capabilities, and one private empty directory bind. It never starts Hermes,
contacts a provider, or uses the current host gateway.

The probe performs two complete generations. Each generation:

1. preallocates empty SQLite `-wal` and `-shm` files;
2. opens both databases in WAL mode, writes a row, and verifies the WAL header,
   non-empty shared-memory file, and `integrity_check`;
3. creates `gateway.pid`, `gateway.lock`, `gateway_state.json`, and
   `processes.json`, including an atomic create-and-replace update for the JSON
   files;
4. closes SQLite connections, unlinks every sidecar and metadata file, and
   verifies that the lifecycle set is absent before generation two.

The final result must be empty again. Any I/O, WAL, atomic-replace, or unlink
failure returns a stable `error_code` and does not write a passed image proof.
The passed result is explicitly scoped to the directory bind and carries
`readiness_effect: none`.

## Formal sandbox blockers

The probe does not claim that the current OpenShell contract supports this
lifecycle. Its result keeps these reason codes until a formal sandbox run is
available:

- `formal_sqlite_sidecar_file_bind_blocks_unlink_recreate`: the current plan
  bind-mounts each SQLite database, WAL, and SHM as separate files. A file
  mountpoint cannot be unlinked and recreated from inside the container.
- `formal_runtime_metadata_parent_atomic_replace_not_authorized`: the policy
  grants write access to individual metadata files but not the profile parent.
  Hermes uses temporary-file plus `rename` for JSON state and removes PID/lock
  files, so parent create/remove access is required.

These are fail-closed evidence markers, not a readiness pass. The formal
OpenShell supervisor, Landlock policy, strict bind contract, and a real
gateway start/stop/restart still need external evidence.

## Minimal repair boundary

The smallest repair is a task-scoped runtime state directory (or a dedicated
preallocated state volume) whose parent is writable only for the sandbox
runtime. It must contain only the two databases, WAL/SHM sidecars, gateway
metadata, and the required runtime directories. Grant create/remove/rename
permissions at that directory boundary, keep the compiled config separately
read-only, and retain read-only mounts for code, prompts, Wiki, and immutable
data. Do not broaden write access to the repository root, `agents/`, `data/wiki`,
`var/openshell`, provider credentials, or Docker control paths.

After that boundary is implemented, repeat the two-generation probe inside the
formal sandbox and record the supervisor identity, active policy, exact mounts,
WAL evidence while the process is live, and cleanup/rebuild receipts. Until
then the image smoke is useful for packaging regression detection only and
must not change traffic readiness.
