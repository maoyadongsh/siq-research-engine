#!/usr/bin/python3 -IB
"""Run the deletion guard for a fixed NOT_PRODUCTION SIQ analysis lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell.destructive_action_guard import (  # noqa: E402
    DestructiveActionGuard,
    DestructiveActionGuardError,
    SandboxTerminator,
)
from scripts.openshell.security_audit import SecurityRunContext  # noqa: E402
from scripts.openshell.siq_analysis_canary import (  # noqa: E402
    CANARY_SETTINGS,
    POOL_SLOT_ID_RE,
    POOL_SLOTS_RELATIVE,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    NONCE_RE,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    _read_json,
    _read_private,
    _sha256_bytes,
    _write_json,
)
from scripts.openshell.siq_analysis_wide_pilot import (  # noqa: E402
    GUARD_READY_NAME,
    MODE,
    READINESS_EFFECT,
    WIDE_PILOT_SETTINGS,
    NonProductionLifecycleSettings,
)

OUTCOME_SCHEMA = "siq.openshell.siq_analysis_wide_pilot_guard.v1"
CANARY_OUTCOME_SCHEMA = "siq.openshell.siq_analysis_canary_guard.v1"


class PilotTerminator(SandboxTerminator):
    def __init__(
        self,
        *,
        adapter: LifecycleAdapter,
        run_id: str,
        sandbox_name: str,
        state_dir: Path,
        settings: NonProductionLifecycleSettings,
    ) -> None:
        self.adapter = adapter
        self.run_id = run_id
        self.sandbox_name = sandbox_name
        self.state_dir = state_dir
        self.settings = settings

    def terminate(self, *, sandbox_id: str, reason_code: str) -> None:
        del reason_code
        if sandbox_id != self.sandbox_name:
            raise LifecycleError(f"{self.settings.error_prefix}_guard_sandbox_name_mismatch")
        self.adapter.backend.acquire_maintenance_lock(timeout_seconds=30)
        manifest = _read_json(self.state_dir / self.settings.manifest_name, root=self.adapter.project_root)
        nonce = (
            _read_private(
                self.state_dir / "run.nonce",
                root=self.adapter.project_root,
                max_bytes=256,
            )
            .decode("ascii")
            .strip()
        )
        if (
            manifest.get("schema_version") != self.settings.schema_version
            or manifest.get("mode") != self.settings.mode
            or manifest.get("readiness_effect") != READINESS_EFFECT
            or manifest.get(self.settings.identity_field) != self.run_id
            or manifest.get("sandbox_name") != self.sandbox_name
            or manifest.get("lifecycle_label") != self.settings.lifecycle_label
            or not NONCE_RE.fullmatch(nonce)
            or manifest.get("run_nonce_sha256") != _sha256_bytes(nonce.encode("ascii"))
        ):
            raise LifecycleError(f"{self.settings.error_prefix}_guard_manifest_identity_mismatch")
        self.adapter._delete_verified_sandbox(
            sandbox_name=self.sandbox_name,
            run_id=self.run_id,
            nonce=nonce,
            expected_sandbox_id=str(manifest.get("sandbox_id") or ""),
            expected_container_id=str(manifest.get("container_id") or ""),
            lifecycle_label=self.settings.lifecycle_label,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle-mode", required=True, choices=(MODE, CANARY_SETTINGS.mode))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--pilot-id")
    identity.add_argument("--run-id")
    parser.add_argument("--sandbox-name", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--pool-slot-id")
    return parser


def _guard_settings(
    lifecycle_mode: str,
    pool_slot_id: str | None,
) -> NonProductionLifecycleSettings:
    if lifecycle_mode == MODE:
        if pool_slot_id is not None:
            raise LifecycleError("wide_pilot_guard_pool_slot_forbidden")
        return WIDE_PILOT_SETTINGS
    if pool_slot_id is None:
        return CANARY_SETTINGS
    if POOL_SLOT_ID_RE.fullmatch(pool_slot_id) is None:
        raise LifecycleError("canary_guard_pool_slot_invalid")
    return replace(
        CANARY_SETTINGS,
        state_relative=POOL_SLOTS_RELATIVE / pool_slot_id,
        pool_managed=True,
        pool_slot_id=pool_slot_id,
    )


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    adapter = LifecycleAdapter(project_root=args.project_root.resolve(strict=True))
    settings = _guard_settings(args.lifecycle_mode, args.pool_slot_id)
    outcome_schema = OUTCOME_SCHEMA if settings is WIDE_PILOT_SETTINGS else CANARY_OUTCOME_SCHEMA
    run_id = args.pilot_id if settings.identity_field == "pilot_id" else args.run_id
    expected_argument = args.run_id if settings.identity_field == "run_id" else args.pilot_id
    if not isinstance(run_id, str) or run_id != expected_argument:
        raise LifecycleError(f"{settings.error_prefix}_guard_input_invalid")
    expected_state = adapter.project_root / settings.runs_relative / run_id
    if (
        not settings.run_id_re.fullmatch(run_id)
        or args.sandbox_name != f"{settings.sandbox_prefix}{run_id}"
        or args.state_dir != expected_state
        or not re_full_sha256(args.policy_digest)
    ):
        raise LifecycleError(f"{settings.error_prefix}_guard_input_invalid")
    manifest = _read_json(args.state_dir / settings.manifest_name, root=adapter.project_root)
    if (
        manifest.get("schema_version") != settings.schema_version
        or manifest.get("mode") != settings.mode
        or manifest.get("phase") != "prepared"
        or manifest.get("policy_sha256") != args.policy_digest
        or manifest.get("sandbox_name") != args.sandbox_name
        or manifest.get(settings.identity_field) != run_id
    ):
        raise LifecycleError(f"{settings.error_prefix}_guard_manifest_invalid")

    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    outcome = args.state_dir / "guard.outcome.json"
    trigger = args.state_dir / "guard.trigger.json"
    context = SecurityRunContext(
        profile=PROFILE,
        sandbox_id=args.sandbox_name,
        run_id=run_id,
        session_id=run_id,
        policy_digest=args.policy_digest,
    )
    terminator = PilotTerminator(
        adapter=adapter,
        run_id=run_id,
        sandbox_name=args.sandbox_name,
        state_dir=args.state_dir,
        settings=settings,
    )

    def persist_trigger(reason_code: str, deleted_paths: tuple[str, ...]) -> None:
        _write_json(
            trigger,
            {
                "schema_version": outcome_schema,
                "mode": settings.mode,
                "readiness_effect": READINESS_EFFECT,
                "status": "triggered",
                settings.identity_field: run_id,
                "pid": os.getpid(),
                "reason_code": reason_code,
                "deleted_paths": list(deleted_paths),
            },
            root=adapter.project_root,
        )

    try:
        with DestructiveActionGuard(
            project_root=adapter.project_root,
            analysis_root=args.analysis_root,
            audit_context=context,
            terminator=terminator,
            before_terminate=persist_trigger,
        ) as guard:
            snapshot = guard.prepare()
            _write_json(
                args.state_dir / GUARD_READY_NAME,
                {
                    "schema_version": outcome_schema,
                    "mode": settings.mode,
                    "readiness_effect": READINESS_EFFECT,
                    "status": "ready",
                    settings.identity_field: run_id,
                    "pid": os.getpid(),
                    "snapshot": snapshot.path.relative_to(adapter.project_root).as_posix(),
                },
                root=adapter.project_root,
            )
            while not stop_requested:
                result = guard.monitor(timeout_seconds=0.25)
                if result.triggered:
                    _write_json(
                        outcome,
                        {
                            "schema_version": outcome_schema,
                            "mode": settings.mode,
                            "readiness_effect": READINESS_EFFECT,
                            "status": "triggered",
                            settings.identity_field: run_id,
                            "pid": os.getpid(),
                            "reason_code": result.reason_code,
                            "baseline_file_count": result.baseline_file_count,
                            "observed_deleted_file_count": result.observed_deleted_file_count,
                            "restored_file_count": result.restored_file_count,
                            "deleted_paths": list(result.deleted_paths),
                        },
                        root=adapter.project_root,
                    )
                    return 70
            _write_json(
                outcome,
                {
                    "schema_version": outcome_schema,
                    "mode": settings.mode,
                    "readiness_effect": READINESS_EFFECT,
                    "status": "stopped",
                    settings.identity_field: run_id,
                    "pid": os.getpid(),
                    "reason_code": "operator_stop",
                    "deleted_paths": [],
                },
                root=adapter.project_root,
            )
            return 0
    except (DestructiveActionGuardError, LifecycleError, OSError):
        _write_json(
            outcome,
            {
                "schema_version": outcome_schema,
                "mode": settings.mode,
                "readiness_effect": READINESS_EFFECT,
                "status": "failed",
                settings.identity_field: run_id,
                "pid": os.getpid(),
                "reason_code": f"{settings.error_prefix}_guard_failed",
                "deleted_paths": [],
            },
            root=adapter.project_root,
        )
        return 2


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LifecycleError, OSError, TypeError, UnicodeError, json.JSONDecodeError):
        print("siq_analysis non-production deletion guard failed closed", file=sys.stderr)
        raise SystemExit(2) from None
