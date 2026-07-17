#!/usr/bin/env python3
"""Run the isolated NOT_PRODUCTION OpenShell Milvus boundary proof lifecycle."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    broker_request_identity,
    build_milvus_write_protection_proof as proof_builder,
)
from scripts.openshell.probe_siq_analysis_sandbox import _remove_private_tree  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BROKER_IDENTITY_KEY_RELATIVE,
    PROFILE,
    RUNTIME_SNAPSHOTS_RELATIVE,
    SECURITY_PROBE_SCHEMA_VERSION,
    LifecycleAdapter,
    LifecycleError,
    SandboxIdentity,
    _sha256_bytes,
    _write_json,
    _write_private_atomic,
)

SCHEMA_VERSION = "siq.openshell.milvus-boundary-proof-lifecycle.v1"
NETWORK_MODE = "data-broker-only"
TOKEN_TTL_SECONDS = 15 * 60
REPO_ROOT = Path(__file__).resolve().parents[2]


class BoundaryLifecycleError(RuntimeError):
    """Stable lifecycle error without identifiers, data, or credentials."""


def _manifest(plan: Any, nonce: str) -> dict[str, Any]:
    spec = plan.spec
    return {
        "schema_version": SECURITY_PROBE_SCHEMA_VERSION,
        "mode": "milvus-boundary",
        "phase": "prepared",
        "profile": PROFILE,
        "probe_id": spec.run_id,
        "run_id": spec.run_id,
        "sandbox_name": spec.sandbox_name,
        "network_mode": NETWORK_MODE,
        "formal_business_sandbox": False,
        "hermes_started": False,
        "providers": [],
        "readiness_effect": "milvus_preflight_only",
        "run_nonce_sha256": _sha256_bytes(nonce.encode("ascii")),
        "mount_plan_sha256": plan.mount_plan_sha256,
        "policy_sha256": plan.policy_sha256,
        "sandbox_id": "",
        "container_id": "",
        "cleanup_verified": False,
    }


def _issue_data_token(*, spec: Any, nonce: str, policy_sha256: str) -> str:
    try:
        key = broker_request_identity.read_key_file(spec.project_root / BROKER_IDENTITY_KEY_RELATIVE)
        bundle = broker_request_identity.issue_broker_identities(
            key,
            profile=PROFILE,
            run_id=spec.run_id,
            sandbox_id=spec.sandbox_name,
            session_id=spec.run_id,
            policy_digest=policy_sha256,
            run_nonce_digest=_sha256_bytes(nonce.encode("ascii")),
            ttl_seconds=TOKEN_TTL_SECONDS,
        )
    except broker_request_identity.IdentityError as exc:
        raise BoundaryLifecycleError("broker_identity_issue_failed") from exc
    return bundle.data_token


def _cleanup(
    *,
    adapter: LifecycleAdapter,
    spec: Any,
    nonce: str,
    identity: SandboxIdentity | None,
    create_attempted: bool,
) -> None:
    try:
        if identity is not None:
            adapter.delete_security_probe_sandbox(
                probe_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=identity.sandbox_id,
                expected_container_id=identity.container_id,
            )
        elif create_attempted:
            adapter.recover_security_probe_sandbox(probe_id=spec.run_id, nonce=nonce)
    except Exception as exc:
        raise BoundaryLifecycleError("milvus_boundary_sandbox_cleanup_failed") from exc

    def remove_tree(path: Path, error_code: str) -> None:
        last_error: Exception | None = None
        for delay in (0.0, 0.05, 0.2):
            if delay:
                time.sleep(delay)
            try:
                _remove_private_tree(path, project_root=spec.project_root)
                return
            except Exception as exc:
                last_error = exc
        raise BoundaryLifecycleError(error_code) from last_error

    remove_tree(
        spec.project_root / RUNTIME_SNAPSHOTS_RELATIVE / spec.run_id,
        "milvus_boundary_snapshot_cleanup_failed",
    )
    remove_tree(spec.run_dir, "milvus_boundary_state_cleanup_failed")


def run_boundary_proof(
    *,
    project_root: Path,
    profile: str,
    market: str,
    company: str,
    probe_id: str,
    timeout: int,
    adapter: LifecycleAdapter | None = None,
) -> dict[str, Any]:
    lifecycle = adapter or LifecycleAdapter(project_root=project_root)
    lifecycle.require_security_probe_lock()
    spec = lifecycle.security_probe_spec(
        profile=profile,
        market=market,
        company=company,
        probe_id=probe_id,
    )
    state_preexisting = spec.run_dir.exists() or spec.run_dir.is_symlink()
    snapshot_path = project_root / RUNTIME_SNAPSHOTS_RELATIVE / spec.run_id
    snapshot_preexisting = snapshot_path.exists() or snapshot_path.is_symlink()
    try:
        plan = lifecycle.prepare_security_probe_runtime(spec, network_mode=NETWORK_MODE)
    except Exception as exc:
        try:
            if not snapshot_preexisting:
                _remove_private_tree(snapshot_path, project_root=project_root)
            if not state_preexisting:
                _remove_private_tree(spec.run_dir, project_root=project_root)
        except Exception as cleanup_exc:
            raise BoundaryLifecycleError("milvus_boundary_prepare_cleanup_failed") from cleanup_exc
        if isinstance(exc, LifecycleError):
            raise BoundaryLifecycleError(exc.code) from exc
        raise BoundaryLifecycleError("milvus_boundary_prepare_failed") from exc
    nonce = secrets.token_hex(24)
    manifest = _manifest(plan, nonce)
    manifest_path = spec.run_dir / "probe.json"
    try:
        _write_private_atomic(spec.run_dir / "run.nonce", f"{nonce}\n".encode("ascii"), root=project_root)
        _write_json(manifest_path, manifest, root=project_root)
        token = _issue_data_token(spec=spec, nonce=nonce, policy_sha256=plan.policy_sha256)
    except Exception as exc:
        try:
            _cleanup(
                adapter=lifecycle,
                spec=spec,
                nonce=nonce,
                identity=None,
                create_attempted=False,
            )
        except BoundaryLifecycleError as cleanup_exc:
            raise cleanup_exc from exc
        if isinstance(exc, BoundaryLifecycleError):
            raise
        raise BoundaryLifecycleError("milvus_boundary_intent_failed") from exc

    identity: SandboxIdentity | None = None
    create_attempted = False
    receipt: dict[str, Any] | None = None
    candidate_proof: dict[str, Any] | None = None
    primary_error: Exception | None = None
    try:
        create_attempted = True
        identity = lifecycle.create_security_probe_sandbox(
            probe_id=spec.run_id,
            nonce=nonce,
            image_ref=plan.image_ref,
            mount_plan=plan.mount_plan,
            policy_path=plan.policy_path,
            network_mode=NETWORK_MODE,
            data_identity_token=token,
        )
        manifest.update(
            {
                "phase": "sandbox_created",
                "sandbox_id": identity.sandbox_id,
                "container_id": identity.container_id,
            }
        )
        _write_json(manifest_path, manifest, root=project_root)
        receipt = proof_builder._collect_receipt(
            project_root=project_root,
            manifest=manifest,
            policy_sha256=plan.policy_sha256,
            timeout=timeout,
        )
        _, bridge_sha256 = proof_builder.bridge_binding()
        policy_bytes = proof_builder._safe_regular(plan.policy_path, private=True)
        policy = json.loads(policy_bytes)
        candidate_proof = proof_builder.build_proof(
            project_root=project_root,
            manifest=manifest,
            policy=policy,
            policy_bytes=policy_bytes,
            receipt=receipt,
            bridge_sha256=bridge_sha256,
            now=int(time.time()),
        )
    except Exception as exc:
        primary_error = exc
    try:
        _cleanup(
            adapter=lifecycle,
            spec=spec,
            nonce=nonce,
            identity=identity,
            create_attempted=create_attempted,
        )
    except BoundaryLifecycleError as exc:
        raise exc from primary_error
    if primary_error is not None:
        if isinstance(primary_error, (BoundaryLifecycleError, LifecycleError, proof_builder.MilvusProofError)):
            raise BoundaryLifecycleError(str(primary_error)) from primary_error
        raise BoundaryLifecycleError("milvus_boundary_probe_failed") from primary_error
    if receipt is None or candidate_proof is None:
        raise BoundaryLifecycleError("milvus_boundary_probe_failed")

    proof_builder.write_proof_outputs(
        project_root=project_root,
        receipt=receipt,
        proof=candidate_proof,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO",
        "passed": True,
        "cleanup_verified": True,
        "readiness_effect": "milvus_preflight_only",
        "business_rows_modified": 0,
        "proof": proof_builder.PROOF_RELATIVE.as_posix(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--market", choices=("cn", "eu", "hk", "jp", "kr", "us"), required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--acknowledge-not-production", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        if (
            root != REPO_ROOT
            or not args.acknowledge_not_production
            or args.profile != PROFILE
            or not 10 <= args.timeout <= 60
        ):
            raise BoundaryLifecycleError("milvus_boundary_arguments_invalid")
        report = run_boundary_proof(
            project_root=root,
            profile=args.profile,
            market=args.market,
            company=args.company,
            probe_id=args.probe_id,
            timeout=args.timeout,
        )
    except (BoundaryLifecycleError, LifecycleError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, (BoundaryLifecycleError, LifecycleError)) else "milvus_boundary_failed"
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
