#!/usr/bin/env python3
"""Run the task-scoped deletion guard for one formal siq_analysis sandbox."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

_MODULE_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_MODULE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_REPO_ROOT))

from scripts.openshell.destructive_action_guard import (  # noqa: E402
    DestructiveActionGuard,
    DestructiveActionGuardError,
    SandboxTerminator,
)
from scripts.openshell.security_audit import SecurityRunContext  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    GUARD_ACTION_LOCK_TIMEOUT_SECONDS,
    GUARD_EVENT_SCHEMA,
    GUARD_OUTCOME_NAME,
    GUARD_RECOVERY_LOCK_TIMEOUT_SECONDS,
    GUARD_TRIGGER_NAME,
    NONCE_RE,
    PROFILE,
    RUNS_RELATIVE,
    LifecycleAdapter,
    LifecycleError,
    SystemBackend,
    _read_private,
    _sha256_bytes,
    _write_json,
)


class VerifiedSandboxTerminator(SandboxTerminator):
    """Delete only the sandbox bound to this run's fixed nonce and labels."""

    def __init__(self, *, adapter: LifecycleAdapter, run_id: str, sandbox_name: str, run_dir: Path) -> None:
        self.adapter = adapter
        self.run_id = run_id
        self.sandbox_name = sandbox_name
        self.run_dir = run_dir

    def terminate(self, *, sandbox_id: str, reason_code: str) -> None:
        del reason_code
        if sandbox_id != self.sandbox_name:
            raise LifecycleError("guard_sandbox_name_mismatch")
        self.adapter.backend.acquire_maintenance_lock(
            timeout_seconds=GUARD_ACTION_LOCK_TIMEOUT_SECONDS,
        )
        _, manifest = self.adapter._load_manifest(self.run_id)
        if manifest["sandbox_name"] != self.sandbox_name:
            raise LifecycleError("guard_manifest_identity_mismatch")
        nonce_path = self.run_dir / "run.nonce"
        if not nonce_path.exists() and not nonce_path.is_symlink():
            named = [item for item in self.adapter._sandbox_inventory() if item.get("name") == self.sandbox_name]
            if named or self.adapter._docker_container_ids(self.sandbox_name):
                raise LifecycleError("guard_nonce_missing_for_existing_sandbox")
            return
        nonce = _read_private(nonce_path, root=self.adapter.project_root, max_bytes=256).decode("ascii").strip()
        if not NONCE_RE.fullmatch(nonce):
            raise LifecycleError("guard_nonce_invalid")
        expected_digest = str(manifest.get("run_nonce_sha256") or "")
        if expected_digest and _sha256_bytes(nonce.encode()) != expected_digest:
            raise LifecycleError("guard_nonce_digest_mismatch")
        self.adapter._delete_verified_sandbox(
            sandbox_name=self.sandbox_name,
            run_id=self.run_id,
            nonce=nonce,
            expected_sandbox_id=str(manifest.get("sandbox_id") or ""),
            expected_container_id=str(manifest.get("container_id") or ""),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sandbox-name", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--policy-digest", required=True)
    return parser


def _schedule_guard_recovery(adapter: LifecycleAdapter, run_id: str, parent_pid: int) -> bool:
    """Start a lock-free watchdog that recovers after the guard exits."""

    try:
        child_pid = os.fork()
    except (AttributeError, OSError):
        return False
    if child_pid != 0:
        return True
    try:
        os.setsid()
        while os.getppid() == parent_pid:
            time.sleep(0.1)
        outcome_path = adapter.project_root / RUNS_RELATIVE / run_id / GUARD_OUTCOME_NAME
        try:
            payload = json.loads(
                _read_private(
                    outcome_path,
                    root=adapter.project_root,
                    max_bytes=64 * 1024,
                )
            )
        except Exception:
            payload = {}
        if (
            payload.get("schema_version") == GUARD_EVENT_SCHEMA
            and payload.get("status") == "stopped"
            and payload.get("profile") == PROFILE
            and payload.get("run_id") == run_id
            and payload.get("pid") == parent_pid
        ):
            os._exit(0)
        try:
            backend = SystemBackend(project_root=adapter.project_root)
            backend.acquire_maintenance_lock(timeout_seconds=GUARD_RECOVERY_LOCK_TIMEOUT_SECONDS)
            recovery_adapter = LifecycleAdapter(project_root=adapter.project_root, backend=backend)
            recovery_adapter.recover(profile=PROFILE, run_id=run_id)
        except Exception:
            # The durable trigger remains for an operator retry; never emit
            # child output that could contain local paths or provider details.
            pass
    finally:
        os._exit(0)


def _persist_initialization_failure(
    *,
    project_root: Path,
    run_dir: Path,
    run_id: str,
) -> None:
    """Leave a minimal durable failure marker even when startup validation fails."""

    try:
        root = project_root.resolve(strict=True)
        candidate = run_dir if run_dir.is_absolute() else root / run_dir
        resolved = candidate.resolve(strict=False)
        if root not in (resolved, *resolved.parents) or not resolved.is_dir():
            return
        _write_json(
            resolved / "guard.outcome.json",
            {
                "schema_version": GUARD_EVENT_SCHEMA,
                "status": "failed",
                "profile": PROFILE,
                "run_id": run_id,
                "pid": os.getpid(),
                "reason_code": "guard_worker_initialization_failed",
                "deleted_paths": [],
            },
            root=root,
        )
    except Exception:
        # The outer process still exits non-zero. Never turn a failed guard
        # initialization into an exception containing local paths or secrets.
        return


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend: SystemBackend | None = None
    adapter: LifecycleAdapter | None = None
    spec = None
    manifest = None
    try:
        backend = SystemBackend(project_root=args.project_root)
        adapter = LifecycleAdapter(project_root=args.project_root, backend=backend)
        spec, manifest = adapter._load_manifest(args.run_id)
        if (
            spec.analysis_root != args.analysis_root
            or spec.sandbox_name != args.sandbox_name
            or spec.run_dir != args.run_dir
            or spec.run_dir != adapter.project_root / RUNS_RELATIVE / args.run_id
            or manifest["policy_sha256"] != args.policy_digest
        ):
            raise LifecycleError("guard_worker_identity_mismatch")
    except Exception:
        _persist_initialization_failure(
            project_root=args.project_root,
            run_dir=args.run_dir,
            run_id=args.run_id,
        )
        if adapter is not None:
            try:
                _schedule_guard_recovery(adapter, args.run_id, os.getpid())
            except Exception:
                pass
        return 2

    assert adapter is not None and spec is not None and manifest is not None

    stop_requested = False

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    terminator = VerifiedSandboxTerminator(
        adapter=adapter,
        run_id=spec.run_id,
        sandbox_name=spec.sandbox_name,
        run_dir=spec.run_dir,
    )
    context = SecurityRunContext(
        profile=PROFILE,
        sandbox_id=spec.sandbox_name,
        run_id=spec.run_id,
        session_id=spec.run_id,
        policy_digest=args.policy_digest,
    )
    ready_path = spec.run_dir / "guard.ready.json"
    outcome_path = spec.run_dir / "guard.outcome.json"

    def persist_trigger_intent(reason_code: str, deleted_paths: tuple[str, ...]) -> None:
        _write_json(
            spec.run_dir / GUARD_TRIGGER_NAME,
            {
                "schema_version": GUARD_EVENT_SCHEMA,
                "status": "trigger_requested",
                "profile": PROFILE,
                "run_id": spec.run_id,
                "pid": os.getpid(),
                "reason_code": reason_code,
                "deleted_paths": list(deleted_paths),
            },
            root=adapter.project_root,
        )

    watchdog_started = _schedule_guard_recovery(adapter, spec.run_id, os.getpid())
    try:
        if not watchdog_started:
            raise LifecycleError("guard_watchdog_start_failed")
        triggered = False
        with DestructiveActionGuard(
            project_root=adapter.project_root,
            analysis_root=spec.analysis_root,
            audit_context=context,
            terminator=terminator,
            before_terminate=persist_trigger_intent,
        ) as guard:
            snapshot = guard.prepare()
            _write_json(
                ready_path,
                {
                    "schema_version": "siq.openshell.deletion_guard_worker.v1",
                    "status": "ready",
                    "profile": PROFILE,
                    "run_id": spec.run_id,
                    "pid": os.getpid(),
                    "snapshot": snapshot.path.relative_to(adapter.project_root).as_posix(),
                },
                root=adapter.project_root,
            )
            while not stop_requested:
                result = guard.monitor(timeout_seconds=0.25)
                if result.triggered:
                    _write_json(
                        outcome_path,
                        {
                            "schema_version": GUARD_EVENT_SCHEMA,
                            "status": "triggered",
                            "profile": PROFILE,
                            "run_id": spec.run_id,
                            "pid": os.getpid(),
                            "reason_code": result.reason_code,
                            "baseline_file_count": result.baseline_file_count,
                            "observed_deleted_file_count": result.observed_deleted_file_count,
                            "restored_file_count": result.restored_file_count,
                            "deleted_paths": list(result.deleted_paths),
                        },
                        root=adapter.project_root,
                    )
                    adapter.handle_guard_trigger(
                        profile=PROFILE,
                        run_id=spec.run_id,
                        reason_code=result.reason_code,
                    )
                    adapter.finalize_guard_worker_exit(
                        profile=PROFILE,
                        run_id=spec.run_id,
                        pid=os.getpid(),
                    )
                    triggered = True
                    break
            if triggered:
                pass
            else:
                _write_json(
                    outcome_path,
                    {
                        "schema_version": GUARD_EVENT_SCHEMA,
                        "status": "stopped",
                        "profile": PROFILE,
                        "run_id": spec.run_id,
                        "pid": os.getpid(),
                        "reason_code": "operator_stop",
                        "deleted_paths": [],
                    },
                    root=adapter.project_root,
                )
                return 0
        if triggered:
            return 70
    except (OSError, DestructiveActionGuardError, LifecycleError):
        _write_json(
            outcome_path,
            {
                "schema_version": GUARD_EVENT_SCHEMA,
                "status": "failed",
                "profile": PROFILE,
                "run_id": spec.run_id,
                "pid": os.getpid(),
                "reason_code": "guard_worker_failed",
                "deleted_paths": [],
            },
            root=adapter.project_root,
        )
        if not watchdog_started:
            _schedule_guard_recovery(adapter, spec.run_id, os.getpid())
        return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LifecycleError, OSError, UnicodeError, json.JSONDecodeError):
        print("siq_analysis deletion guard worker failed closed", file=sys.stderr)
        raise SystemExit(2) from None
