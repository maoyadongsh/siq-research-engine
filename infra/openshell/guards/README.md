# SIQ Destructive-Action Guard

This directory documents the host-side deletion guard for the `siq_analysis`
OpenShell runtime. The implementation is
`scripts/openshell/destructive_action_guard.py`.

The guard is connected to the formal `siq_analysis` lifecycle worker. A trigger
is written durably before sandbox fencing; the worker then removes the
forwarder, sandbox and one-time credentials, records the guard process as
pending exit, and an independent lock-free watchdog performs idempotent
lifecycle recovery after any non-clean guard exit. Long-running guard and
forward processes never inherit the start operation's maintenance lock; a
trigger or watchdog obtains a fresh bounded lock before changing lifecycle
state. The default
`start_all.sh` host runtime is still unchanged, and a real business-sandbox
acceptance run remains required before cutover.

## Fixed scope

One guard instance owns exactly one task and one company's direct `analysis/`
bind root. Accepted roots have one of these shapes:

```text
data/wiki/companies/<company>/analysis
data/wiki/{eu,hk,jp,kr,us}/companies/<company>/analysis
```

The guard rejects a project root reached through a symlink, any broader Wiki
root, nested analysis directory, or second company. Its recovery snapshot is
always written below:

```text
var/openshell/siq-analysis/deletion-snapshots/<siq-run-id>/
```

The state directory and lock are host-only, owner-controlled, and private. The
sandbox must receive only the task's analysis bind, never this snapshot root.

## Lifecycle contract

The eventual runtime integration must use this order:

1. Construct a `SandboxTerminator` implementation with a fixed sandbox API.
2. Construct the guard with a validated `SecurityRunContext` and exact analysis
   root.
3. Call `prepare()` before starting the sandbox. Do not start the sandbox unless
   snapshot creation and recursive watch installation both succeed.
4. Start only the sandbox named by the run context and keep `monitor()` active
   for its task lifetime.
5. Call `close()` after the task ends.
6. On a trigger, persist `guard.trigger.json` before termination and leave the
   transaction in `stopping` until the guard process has exited and recovery
   finalizes it. The watchdog also treats a missing guard event as a fail-closed
   guard failure and restores the verified baseline snapshot.

`SandboxTerminator.terminate()` has no command or script parameter. A production
implementation must synchronously stop and fence the named sandbox before it
returns. The guard restores files only after that call succeeds.

## Events and thresholds

The implementation uses Linux inotify through the Python standard library and
libc. It watches the complete tree, adds watches when directories appear, and
handles `DELETE`, `MOVED_FROM`, `DELETE_SELF`, and `MOVE_SELF` events. It does
not inspect shell command text, so shell, Python, Node, and direct syscall
deletions share the same event path.

Only regular, persistent files present when `prepare()` runs count toward a
threshold. Files created during the task do not count. Pre-task and newly
created files below `.cache`, `.work`, `cache`, `tmp`, `temp`, or `__pycache__`
are excluded as disposable task state.

The first matching condition terminates the sandbox:

| Condition | Result code |
| --- | --- |
| More than 500 baseline files deleted or moved | `deletion_count_gt_500` |
| At least 20 baseline files and at least 50% of the baseline deleted or moved | `deletion_ratio_threshold` |
| Guarded analysis root deleted or moved | `analysis_root_self_deleted` |
| inotify queue overflow | `inotify_queue_overflow` |
| inotify monitor failure | `inotify_monitor_failure` |

Small normal deletions remain in place and do not terminate the sandbox.

## Snapshot and recovery safety

Snapshot publication uses a private staging directory followed by an atomic
rename while an exclusive per-analysis-root lock is held. Snapshot files are
`0600`; internal directories are `0700`. Every persistent file is recorded with
size, mode, and SHA-256 digest.

Preparation fails closed if the analysis tree contains symlinks, hard links,
special files, set-ID files, credential-like names, private-key material,
bearer credentials, or credential-bearing URLs. Ephemeral directories are not
copied, but are still scanned for unsafe entry types and names.

On a trigger, recovery is limited to the same analysis root. It recreates
missing baseline directories and atomically replaces each missing, moved, or
changed replacement at an observed baseline path from a digest-verified
snapshot. Existing symlink, hard-link, or special-file targets cause recovery
to fail closed. The guard does not alter Hermes sessions, host code, another
company, a move destination, or any path outside the guarded analysis root.

Recovery writes one minimal `filesystem.delete` denial through
`scripts/openshell/security_audit.py`. Targets are projected rather than stored
as paths, and file contents are never written to the audit record.

## Operating boundary

- Linux inotify and sufficient `max_user_watches` capacity are prerequisites.
- Detection occurs after filesystem events. Some files can disappear before
  the sandbox is terminated; qualifying baseline files are then restored.
- Recovery is atomic per file, not as a whole-tree transaction. The snapshot is
  retained if the host exits partway through recovery.
- A concurrent operator stop or rollback keeps its already-durable terminal
  action, but honors a previously persisted guard trigger and restores the
  snapshot before finalization.
- A move destination is intentionally not removed. Recovery restores the
  baseline source path without touching an unapproved destination.
- Small deletions below both thresholds are intentionally allowed and are not
  restored.
- File modification without a delete or move event is outside T6.1.
- Runtime snapshots remain ignored by Git. Retain each deletion snapshot until
  the transaction is terminal, its sanitized acceptance evidence has been
  reviewed, and the corresponding analysis artifact is backed up. Garbage
  collection must run under the maintenance lock and must never remove the
  active run or any non-terminal transaction snapshot.
