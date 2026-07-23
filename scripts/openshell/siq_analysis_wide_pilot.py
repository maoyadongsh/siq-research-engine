#!/usr/bin/python3 -IB
"""Manage one real-path, NOT_PRODUCTION siq_analysis OpenShell wide pilot."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import (  # noqa: E402
    broker_request_identity,
    build_siq_analysis_mount_plan,
    snapshot_siq_analysis_runtime,
    test_siq_analysis_wide_pilot_contract as pilot_contract,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BROKER_IDENTITY_KEY_RELATIVE,
    BROKER_IDENTITY_TTL_SECONDS,
    BUSINESS_MOUNT_COUNT,
    FORWARD_HOST,
    FORWARD_PORT,
    KEY_RE,
    NONCE_RE,
    PROFILE,
    SYSTEM_PYTHON,
    WIDE_PILOT_LIFECYCLE_LABEL,
    CommandResult,
    LifecycleAdapter,
    LifecycleError,
    ProcessRecord,
    RunSpec,
    _assert_no_symlink_chain,
    _host_receipt_sha256,
    _mkdir_private,
    _read_json,
    _read_private,
    _remove_private,
    _sandbox_entrypoint_env_arguments,
    _sha256_bytes,
    _sha256_file,
    _write_json,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_wide_pilot_lifecycle.v1"
MODE = "NOT_PRODUCTION_WIDE_PILOT"
READINESS_EFFECT = "none"
ACKNOWLEDGEMENT = "--acknowledge-not-production-wide-pilot"
LIFECYCLE_LABEL = WIDE_PILOT_LIFECYCLE_LABEL
STATE_RELATIVE = Path("var/openshell/poc/siq-analysis-wide")
RUNS_RELATIVE = STATE_RELATIVE / "runs"
ACTIVE_RELATIVE = STATE_RELATIVE / "active.json"
PILOT_ID_RE = pilot_contract.PILOT_ID_RE
PROVIDERS = (
    "siq-minimax-cn-pool",
    "siq-stepfun",
    "siq-kimi-coding",
    "siq-tavily-search",
)
KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED = (
    "siq-exa-search_not_configured",
    "local_model_8004_not_required",
    "local_model_8006_not_required",
    "milvus_formal_proof_not_required",
    "clash_fake_ip_egress_guard_compatibility_unresolved",
)
SECRET_NAMES = (
    "api.key",
    "run.nonce",
    "egress.identity.token",
    "data.identity.token",
)
MANIFEST_NAME = "pilot.json"
FORWARD_PROCESS_NAME = "forward.process.json"
GUARD_PROCESS_NAME = "guard.process.json"
GUARD_READY_NAME = "guard.ready.json"
PROBE_RECEIPT_NAME = "probe.sanitized.json"
STOP_RECEIPT_NAME = "stop.sanitized.json"


@dataclass(frozen=True)
class NonProductionLifecycleSettings:
    """Fixed identity and state boundary for one non-production lifecycle."""

    schema_version: str
    mode: str
    lifecycle_label: str
    state_relative: Path
    run_id_re: re.Pattern[str]
    sandbox_prefix: str
    identity_field: str
    error_prefix: str
    guard_worker_relative: Path
    guard_identity_argument: str
    sandbox_mode_environment: tuple[str, str]
    entrypoint_log: str
    manifest_name: str
    local_port: int = FORWARD_PORT
    target_port: int = FORWARD_PORT
    pool_managed: bool = False
    pool_slot_id: str | None = None

    @property
    def runs_relative(self) -> Path:
        return self.state_relative / "runs"

    @property
    def active_relative(self) -> Path:
        return self.state_relative / "active.json"


WIDE_PILOT_SETTINGS = NonProductionLifecycleSettings(
    schema_version=SCHEMA_VERSION,
    mode=MODE,
    lifecycle_label=LIFECYCLE_LABEL,
    state_relative=STATE_RELATIVE,
    run_id_re=PILOT_ID_RE,
    sandbox_prefix="siq-analysis-wide-",
    identity_field="pilot_id",
    error_prefix="wide_pilot",
    guard_worker_relative=Path("scripts/openshell/siq_analysis_wide_pilot_guard.py"),
    guard_identity_argument="--pilot-id",
    sandbox_mode_environment=("SIQ_WIDE_PILOT", "1"),
    entrypoint_log="/tmp/siq-wide-pilot-entrypoint.log",
    manifest_name=MANIFEST_NAME,
)


class WidePilotError(RuntimeError):
    """A stable, secret-free pilot lifecycle failure."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code):
            code = "wide_pilot_invalid_error"
        self.code = code
        super().__init__(code)


def _command_json(result: CommandResult, *, code: str) -> dict[str, Any]:
    if result.returncode != 0 or len(result.stdout.encode("utf-8", errors="replace")) > 256 * 1024:
        raise WidePilotError(code)
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WidePilotError(code) from exc
    if not isinstance(value, dict):
        raise WidePilotError(code)
    return value


def _private_process(path: Path, *, root: Path, role: str, error_prefix: str = "wide_pilot") -> ProcessRecord:
    value = _read_json(path, root=root)
    if set(value) != set(ProcessRecord.__dataclass_fields__):
        raise WidePilotError(f"{error_prefix}_{role}_identity_invalid")
    try:
        record = ProcessRecord(**value)
    except TypeError as exc:
        raise WidePilotError(f"{error_prefix}_{role}_identity_invalid") from exc
    if (
        record.role != role
        or record.pid <= 1
        or not record.executable
        or not re.fullmatch(r"[0-9a-f]{64}", record.argv_sha256)
    ):
        raise WidePilotError(f"{error_prefix}_{role}_identity_invalid")
    return record


class WidePilotLifecycle:
    def __init__(
        self,
        *,
        project_root: Path = REPO_ROOT,
        adapter: LifecycleAdapter | None = None,
        settings: NonProductionLifecycleSettings = WIDE_PILOT_SETTINGS,
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        if self.project_root != project_root.absolute():
            raise WidePilotError("wide_pilot_project_root_invalid")
        self.settings = settings
        if (
            not isinstance(settings.local_port, int)
            or isinstance(settings.local_port, bool)
            or not 1024 <= settings.local_port <= 65535
            or settings.target_port != FORWARD_PORT
        ):
            raise WidePilotError("wide_pilot_endpoint_invalid")
        self.adapter = adapter or LifecycleAdapter(project_root=self.project_root)
        self.state_root = self.project_root / settings.state_relative
        self.active_path = self.project_root / settings.active_relative
        self.policy_builder = self.project_root / "scripts/openshell/build_policy.py"
        self.broker_lifecycle = self.project_root / "scripts/openshell/broker_lifecycle.py"
        self.guard_worker = self.project_root / settings.guard_worker_relative

    def _code(self, suffix: str) -> str:
        return f"{self.settings.error_prefix}_{suffix}"

    def _identity(self, run_id: str) -> dict[str, str]:
        return {self.settings.identity_field: run_id}

    def _identity_value(self, value: Mapping[str, Any]) -> str:
        identity = value.get(self.settings.identity_field)
        return identity if isinstance(identity, str) else ""

    def _prepare_host_business_root(self, *, market: str, company: str, pilot_id: str) -> None:
        """Hook for lifecycles whose normal business contract permits host materialization."""

    def _validate_start_scope(self, *, market: str, company: str, pilot_id: str) -> None:
        del market, company, pilot_id

    def _pool_owned_sandboxes(self) -> Mapping[str, Mapping[str, str]]:
        return {}

    def _after_active(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        del spec, manifest

    def _prepare_stop(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        del spec, manifest

    def _after_stop_marked(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        del spec, manifest

    def _require_lock(self) -> None:
        checker = getattr(self.adapter.backend, "maintenance_lock_held", None)
        if not callable(checker) or checker() is not True:
            raise WidePilotError(self._code("maintenance_lock_required"))

    def _spec(self, *, market: str, company: str, pilot_id: str) -> RunSpec:
        if not self.settings.run_id_re.fullmatch(pilot_id):
            raise WidePilotError(self._code("identity_invalid"))
        try:
            formal = self.adapter.spec(
                profile=PROFILE,
                market=market,
                company=company,
                run_id=pilot_id,
            )
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc
        return replace(
            formal,
            sandbox_name=f"{self.settings.sandbox_prefix}{pilot_id}",
            run_dir=self.project_root / self.settings.runs_relative / pilot_id,
        )

    def _paths(self, spec: RunSpec) -> pilot_contract.PilotPaths:
        try:
            return pilot_contract.resolve_pilot_paths(
                self.project_root,
                market=spec.market,
                company=spec.company,
                pilot_id=spec.run_id,
            )
        except (OSError, pilot_contract.PilotContractError) as exc:
            code = str(exc) if isinstance(exc, pilot_contract.PilotContractError) else "pilot_path_os_error"
            raise WidePilotError(self._code(code)) from exc

    def _require_brokers(self) -> None:
        result = self.adapter.backend.run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.broker_lifecycle),
                "status",
                "--project-root",
                str(self.project_root),
                "--require-request-identity",
            ]
        )
        payload = _command_json(result, code=self._code("broker_preflight_no_go"))
        brokers = payload.get("brokers")
        bridge = payload.get("bridge")
        if (
            payload.get("ok") is not True
            or payload.get("action") != "status"
            or bridge != {"network": "siq-openshell-dev", "alias": "host.openshell.internal"}
            or not isinstance(brokers, dict)
            or set(brokers) != {"egress", "data"}
        ):
            raise WidePilotError(self._code("broker_preflight_invalid"))
        expected = {"egress": 18792, "data": 18793}
        for name, port in expected.items():
            item = brokers[name]
            if (
                not isinstance(item, dict)
                or item.get("state") != "running"
                or item.get("port") != port
                or item.get("request_identity_required") is not True
            ):
                raise WidePilotError(self._code("broker_preflight_invalid"))

    def _require_provider_subset(self) -> None:
        try:
            names = set(
                self.adapter._run_cli(
                    ["provider", "list", "--names"],
                    self._code("provider_inventory_failed"),
                ).splitlines()
            )
        except LifecycleError as exc:
            raise WidePilotError(exc.code) from exc
        if not set(PROVIDERS).issubset(names):
            raise WidePilotError(self._code("provider_subset_missing"))

    def _prepare_output_root(self, spec: RunSpec) -> pilot_contract.PilotPaths:
        work_root = spec.analysis_root / ".work"
        _assert_no_symlink_chain(self.project_root, work_root)
        if not work_root.is_dir() or work_root.is_symlink():
            raise WidePilotError(self._code("work_root_missing"))
        output_root = work_root / spec.run_id
        if output_root.exists() or output_root.is_symlink():
            raise WidePilotError(self._code("output_conflict"))
        output_root.mkdir(mode=0o700)
        return self._paths(spec)

    def _manifest_scope_fields(self, paths: pilot_contract.PilotPaths) -> dict[str, Any]:
        return {"output_relative_path": paths.output.relative_to(self.project_root).as_posix()}

    def _active_extra_fields(self, spec: RunSpec, manifest: Mapping[str, Any]) -> dict[str, Any]:
        del spec, manifest
        return {}

    def _before_stop(self, spec: RunSpec, manifest: dict[str, Any]) -> None:
        del spec, manifest

    def _prepare_runtime(
        self,
        spec: RunSpec,
        paths: pilot_contract.PilotPaths,
        *,
        expected_runtime_config_sha256: str,
    ) -> tuple[Path, Path, str, Path, str]:
        snapshot = self.project_root / "var/openshell/siq-analysis/runtime-snapshots" / spec.run_id
        try:
            snapshot_manifest = snapshot_siq_analysis_runtime.snapshot_runtime(
                project_root=self.project_root,
                destination=snapshot,
                compile_config=True,
                fresh=True,
            )
            config = snapshot_manifest["inventory"]["config"]
            if (
                snapshot_manifest.get("schema_version") != snapshot_siq_analysis_runtime.SCHEMA_VERSION
                or snapshot_manifest.get("snapshot_mode") != "fresh"
                or snapshot_manifest.get("host_runtime_records_copied") is not False
                or config.get("compiled_sha256") != expected_runtime_config_sha256
            ):
                raise WidePilotError(self._code("runtime_config_provenance_mismatch"))
            compiled_mounts = build_siq_analysis_mount_plan.compile_mount_plan(
                project_root=self.project_root,
                snapshot=snapshot,
                analysis_dir=spec.analysis_root,
            )
            mount_plan, _mount_summary = build_siq_analysis_mount_plan.write_compiled_mount_plan(
                compiled_mounts,
                project_root=self.project_root,
            )
        except WidePilotError:
            raise
        except (OSError, RuntimeError, KeyError, TypeError) as exc:
            raise WidePilotError(self._code("runtime_prepare_failed")) from exc
        if compiled_mounts.summary.get("mount_count") != BUSINESS_MOUNT_COUNT:
            raise WidePilotError(self._code("mount_plan_invalid"))

        policy = spec.run_dir / "task-policy.yaml"
        policy_summary = spec.run_dir / "task-policy.summary.json"
        result = self.adapter.backend.run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.policy_builder),
                "--project-root",
                str(self.project_root),
                "--output",
                str(policy),
                "--summary-output",
                str(policy_summary),
                "--runtime-file-source",
                "candidate-image",
                "--writable-path",
                str(paths.output_root),
            ]
        )
        if result.returncode != 0:
            raise WidePilotError(self._code("policy_compile_failed"))
        summary = _read_json(policy_summary, root=self.project_root)
        policy_value = _read_json(policy, root=self.project_root)
        filesystem = policy_value.get("filesystem_policy")
        self._validate_policy_scope(spec, paths, summary=summary, filesystem=filesystem)
        return snapshot, mount_plan, compiled_mounts.digest, policy, _sha256_file(policy)

    def _prepare_state_directories(self) -> None:
        _mkdir_private(self.state_root, root=self.project_root)
        _mkdir_private(
            self.project_root / self.settings.runs_relative,
            root=self.project_root,
        )

    def _validate_policy_scope(
        self,
        spec: RunSpec,
        paths: pilot_contract.PilotPaths,
        *,
        summary: Mapping[str, Any],
        filesystem: Any,
    ) -> None:
        if (
            summary.get("profile") != PROFILE
            or summary.get("task_scoped_write_count") != 1
            or not isinstance(filesystem, dict)
            or str(paths.output_root) not in filesystem.get("read_write", [])
            or str(spec.analysis_root) in filesystem.get("read_write", [])
        ):
            raise WidePilotError(self._code("policy_scope_invalid"))

    def _write_secret(self, path: Path, value: str, pattern: re.Pattern[str]) -> None:
        try:
            self.adapter._write_secret(path, value, pattern)
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc

    def _issue_secrets(self, spec: RunSpec, *, policy_digest: str) -> tuple[str, str, Any]:
        key = secrets.token_hex(32)
        nonce = secrets.token_hex(24)
        self._write_secret(spec.run_dir / "api.key", key, KEY_RE)
        self._write_secret(spec.run_dir / "run.nonce", nonce, NONCE_RE)
        try:
            signing_key = broker_request_identity.read_key_file(self.project_root / BROKER_IDENTITY_KEY_RELATIVE)
            identities = broker_request_identity.issue_broker_identities(
                signing_key,
                profile=PROFILE,
                run_id=spec.run_id,
                sandbox_id=spec.sandbox_name,
                session_id=spec.run_id,
                policy_digest=policy_digest,
                run_nonce_digest=_sha256_bytes(nonce.encode("ascii")),
                ttl_seconds=BROKER_IDENTITY_TTL_SECONDS,
            )
        except broker_request_identity.IdentityError as exc:
            raise WidePilotError(self._code("broker_identity_issue_failed")) from exc
        self._write_secret(
            spec.run_dir / "egress.identity.token",
            identities.egress_token,
            broker_request_identity.TOKEN_RE,
        )
        self._write_secret(
            spec.run_dir / "data.identity.token",
            identities.data_token,
            broker_request_identity.TOKEN_RE,
        )
        return key, nonce, identities

    def _guard_arguments(self, spec: RunSpec, policy_digest: str) -> list[str]:
        arguments = [
            SYSTEM_PYTHON,
            "-I",
            "-B",
            str(self.guard_worker),
            "--lifecycle-mode",
            self.settings.mode,
            "--project-root",
            str(self.project_root),
            "--analysis-root",
            str(spec.analysis_root),
            self.settings.guard_identity_argument,
            spec.run_id,
            "--sandbox-name",
            spec.sandbox_name,
            "--state-dir",
            str(spec.run_dir),
            "--policy-digest",
            policy_digest,
        ]
        if self.settings.pool_managed:
            pool_slot_id = self.settings.pool_slot_id
            if (
                not isinstance(pool_slot_id, str)
                or re.fullmatch(r"[0-9a-f]{24}", pool_slot_id) is None
            ):
                raise WidePilotError(self._code("pool_slot_invalid"))
            arguments.extend(("--pool-slot-id", pool_slot_id))
        return arguments

    def _spawn_guard(self, spec: RunSpec, policy_digest: str) -> tuple[ProcessRecord, int]:
        arguments = self._guard_arguments(spec, policy_digest)
        pid = self.adapter.backend.spawn(arguments, log_path=spec.run_dir / "guard.log")
        try:
            record = self.adapter._wait_process_identity(pid, "guard", expected_argv=arguments)
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc
        _write_json(spec.run_dir / GUARD_PROCESS_NAME, asdict(record), root=self.project_root)
        ready = spec.run_dir / GUARD_READY_NAME
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                value = _read_json(ready, root=self.project_root)
                if (
                    self._identity_value(value) != spec.run_id
                    or value.get("pid") != pid
                    or value.get("status") != "ready"
                ):
                    raise WidePilotError(self._code("guard_ready_invalid"))
                return record, pid
            if self.adapter.backend.process_snapshot(pid, "guard") is None:
                raise WidePilotError(self._code("guard_failed_before_ready"))
            time.sleep(0.05)
        raise WidePilotError(self._code("guard_ready_timeout"))

    def _spawn_forward(self, spec: RunSpec) -> tuple[ProcessRecord, int]:
        arguments = self.adapter._forward_arguments(
            spec,
            local_port=self.settings.local_port,
            target_port=self.settings.target_port,
        )
        pid = self.adapter.backend.spawn(arguments, log_path=spec.run_dir / "forward.log")
        try:
            record = self.adapter._wait_process_identity(
                pid,
                "forward",
                expected_argv=[
                    str(self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell"),
                    "forward",
                    "service",
                    spec.sandbox_name,
                    "--target-port",
                    str(self.settings.target_port),
                    "--local",
                    f"{FORWARD_HOST}:{self.settings.local_port}",
                ],
                expected_executable=str(
                    (self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell").resolve(strict=True)
                ),
            )
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc
        _write_json(spec.run_dir / FORWARD_PROCESS_NAME, asdict(record), root=self.project_root)
        return record, pid

    def _authenticated_forward_health(self, key: str) -> bool:
        request = urllib.request.Request(
            f"http://{FORWARD_HOST}:{self.settings.local_port}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.load(response)
                return (
                    response.status == 200
                    and isinstance(payload, dict)
                    and payload.get("object") == "list"
                    and isinstance(payload.get("data"), list)
                    and bool(payload["data"])
                )
        except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
            return False

    def _authenticated_sandbox_exec_health(self, spec: RunSpec) -> bool:
        marker = "SIQ_AUTHENTICATED_SANDBOX_HEALTH_OK"
        script = "\n".join(
            (
                "import json, os, urllib.request",
                "key = os.environ.get('API_SERVER_KEY', '')",
                "assert len(key) == 64",
                (
                    "request = urllib.request.Request("
                    f"'http://127.0.0.1:{self.settings.target_port}/v1/models', "
                    "headers={'Authorization': 'Bearer ' + key})"
                ),
                "with urllib.request.urlopen(request, timeout=2) as response:",
                "    payload = json.load(response)",
                "    assert response.status == 200",
                "    assert payload.get('object') == 'list'",
                "    assert isinstance(payload.get('data'), list) and payload['data']",
                f"print('{marker}')",
            )
        )
        try:
            output = self.adapter._run_cli(
                [
                    "sandbox",
                    "exec",
                    "--name",
                    spec.sandbox_name,
                    "--timeout",
                    "5",
                    "--no-tty",
                    "--",
                    "/opt/siq/hermes/venv/bin/python",
                    "-I",
                    "-B",
                    "-c",
                    script,
                ],
                self._code("sandbox_authenticated_health_failed"),
                timeout_seconds=10.0,
            )
        except LifecycleError:
            return False
        return output.strip() == marker

    def _verify_sandbox(
        self,
        spec: RunSpec,
        nonce: str,
        *,
        sandbox_id: str = "",
        container_id: str = "",
    ):
        try:
            return self.adapter.verify_sandbox_identity(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=sandbox_id,
                expected_container_id=container_id,
                lifecycle_label=self.settings.lifecycle_label,
            )
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc

    def _delete_sandbox(
        self,
        spec: RunSpec,
        nonce: str,
        *,
        sandbox_id: str = "",
        container_id: str = "",
    ) -> None:
        try:
            self.adapter._delete_verified_sandbox(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=sandbox_id,
                expected_container_id=container_id,
                lifecycle_label=self.settings.lifecycle_label,
            )
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc

    def _manifest(self, spec: RunSpec) -> dict[str, Any]:
        value = _read_json(spec.run_dir / self.settings.manifest_name, root=self.project_root)
        if (
            value.get("schema_version") != self.settings.schema_version
            or value.get("mode") != self.settings.mode
            or value.get("readiness_effect") != READINESS_EFFECT
            or self._identity_value(value) != spec.run_id
            or value.get("sandbox_name") != spec.sandbox_name
            or value.get("lifecycle_label") != self.settings.lifecycle_label
        ):
            raise WidePilotError(self._code("manifest_invalid"))
        return value

    def _write_manifest(self, spec: RunSpec, value: Mapping[str, Any]) -> None:
        _write_json(spec.run_dir / self.settings.manifest_name, value, root=self.project_root)

    def _active(self) -> dict[str, Any]:
        value = _read_json(self.active_path, root=self.project_root)
        if value.get("schema_version") != self.settings.schema_version or value.get("mode") != self.settings.mode:
            raise WidePilotError(self._code("active_state_invalid"))
        return value

    def _terminate_record(self, record: ProcessRecord) -> None:
        try:
            self.adapter.backend.terminate(record)
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc

    def _cleanup_output(self, spec: RunSpec, manifest: Mapping[str, Any], *, allow_missing: bool) -> None:
        output_root = spec.analysis_root / ".work" / spec.run_id
        _assert_no_symlink_chain(self.project_root, output_root)
        if not output_root.exists():
            if allow_missing:
                return
            raise WidePilotError(self._code("output_cleanup_unsafe"))
        if output_root.is_symlink() or not output_root.is_dir():
            raise WidePilotError(self._code("output_cleanup_unsafe"))
        entries = list(output_root.iterdir())
        if not entries:
            output_root.rmdir()
            return
        paths = pilot_contract.PilotPaths(
            company_root=spec.analysis_root.parent,
            source=spec.analysis_root.parent / "company.json",
            analysis_root=spec.analysis_root,
            work_root=spec.analysis_root / ".work",
            output_root=output_root,
            output=output_root / "result.json",
        )
        try:
            pilot_contract.remove_exact_output(
                paths,
                pilot_id=spec.run_id,
                stock_code=str(manifest["source_stock_code"]),
                source_sha256=str(manifest["source_sha256"]),
            )
        except (OSError, KeyError, pilot_contract.PilotContractError) as exc:
            raise WidePilotError(self._code("output_cleanup_unsafe")) from exc

    def _cleanup_uncommitted_business_scope(self, spec: RunSpec) -> None:
        output_root = spec.analysis_root / ".work" / spec.run_id
        _assert_no_symlink_chain(self.project_root, output_root)
        if output_root.exists():
            if output_root.is_symlink() or not output_root.is_dir() or any(output_root.iterdir()):
                raise WidePilotError(self._code("output_cleanup_unsafe"))
            output_root.rmdir()

    def _remove_secret_state(self, spec: RunSpec) -> None:
        for name in SECRET_NAMES:
            try:
                _remove_private(spec.run_dir / name, root=self.project_root)
            except LifecycleError as exc:
                raise WidePilotError(self._code(exc.code)) from exc

    def _remove_runtime_snapshot(self, manifest: Mapping[str, Any]) -> None:
        relative = manifest.get("runtime_snapshot")
        if not isinstance(relative, str):
            raise WidePilotError(self._code("runtime_snapshot_invalid"))
        path = self.project_root / relative
        expected_parent = self.project_root / "var/openshell/siq-analysis/runtime-snapshots"
        if path.parent != expected_parent or not self.settings.run_id_re.fullmatch(path.name):
            raise WidePilotError(self._code("runtime_snapshot_invalid"))
        _assert_no_symlink_chain(self.project_root, path)
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise WidePilotError(self._code("runtime_snapshot_invalid"))
            shutil.rmtree(path)

    def _remove_guard_snapshot(self, spec: RunSpec) -> None:
        path = self.project_root / "var/openshell/siq-analysis/deletion-snapshots" / spec.run_id
        _assert_no_symlink_chain(self.project_root, path)
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise WidePilotError(self._code("guard_snapshot_invalid"))
            shutil.rmtree(path)

    def _check_host_receipt(self, manifest: Mapping[str, Any]) -> None:
        try:
            receipt = self.adapter._stable_host_receipt(after_stop=True)
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc
        if _host_receipt_sha256(receipt) != manifest.get("host_hermes_receipt_sha256"):
            raise WidePilotError(self._code("host_hermes_identity_changed"))

    def _cleanup_started(
        self,
        spec: RunSpec,
        *,
        manifest: Mapping[str, Any] | None,
        nonce: str,
        guard_record: ProcessRecord | None,
        forward_record: ProcessRecord | None,
        guard_pid: int,
        forward_pid: int,
        sandbox_attempted: bool,
        allow_missing_output: bool,
    ) -> None:
        forward_managed = forward_record is not None or forward_pid > 1
        if forward_record is not None:
            self._terminate_record(forward_record)
        elif forward_pid > 1:
            current = self.adapter.backend.process_snapshot(forward_pid, "forward")
            if current is not None:
                self._terminate_record(current)
        if forward_managed and not self.adapter.backend.port_listener_absent(
            FORWARD_HOST,
            self.settings.local_port,
        ):
            raise WidePilotError(self._code("forward_port_remained_after_stop"))
        if sandbox_attempted:
            self._delete_sandbox(
                spec,
                nonce,
                sandbox_id=str((manifest or {}).get("sandbox_id") or ""),
                container_id=str((manifest or {}).get("container_id") or ""),
            )
        if guard_record is not None:
            self._terminate_record(guard_record)
        elif guard_pid > 1:
            current = self.adapter.backend.process_snapshot(guard_pid, "guard")
            if current is not None:
                self._terminate_record(current)
        if manifest is not None:
            self._check_host_receipt(manifest)
            self._cleanup_output(spec, manifest, allow_missing=allow_missing_output)
            self._remove_runtime_snapshot(manifest)
            self._remove_guard_snapshot(spec)
        else:
            self._cleanup_uncommitted_business_scope(spec)
            snapshot = self.project_root / "var/openshell/siq-analysis/runtime-snapshots" / spec.run_id
            if snapshot.exists():
                if snapshot.is_symlink() or not snapshot.is_dir():
                    raise WidePilotError(self._code("runtime_snapshot_invalid"))
                shutil.rmtree(snapshot)
            self._remove_guard_snapshot(spec)
        self._remove_secret_state(spec)
        if self.active_path.exists():
            _remove_private(self.active_path, root=self.project_root)

    def start(self, *, market: str, company: str, pilot_id: str) -> dict[str, Any]:
        self._require_lock()
        if self.active_path.exists() or self.active_path.is_symlink():
            raise WidePilotError(self._code("already_active"))
        self._validate_start_scope(market=market, company=company, pilot_id=pilot_id)
        self._prepare_host_business_root(market=market, company=company, pilot_id=pilot_id)
        spec = self._spec(market=market, company=company, pilot_id=pilot_id)
        if spec.run_dir.exists() or spec.run_dir.is_symlink():
            raise WidePilotError(self._code("run_state_conflict"))
        manifest: dict[str, Any] | None = None
        guard_record: ProcessRecord | None = None
        forward_record: ProcessRecord | None = None
        guard_pid = 0
        forward_pid = 0
        sandbox_attempted = False
        nonce = ""
        cleanup_failed = False
        self._prepare_state_directories()
        try:
            try:
                if self.settings.pool_managed:
                    image_ref, image_id, runtime_config_sha256 = (
                        self.adapter.validate_security_probe_prerequisites(
                            allowed_pool_sandboxes=self._pool_owned_sandboxes(),
                            forward_port=self.settings.local_port,
                        )
                    )
                else:
                    image_ref, image_id, runtime_config_sha256 = (
                        self.adapter.validate_security_probe_prerequisites()
                    )
                baseline = self.adapter._stable_host_receipt()
            except LifecycleError as exc:
                raise WidePilotError(self._code(exc.code)) from exc
            self._require_provider_subset()
            self._require_brokers()
            _mkdir_private(spec.run_dir, root=self.project_root)
            paths = self._prepare_output_root(spec)
            _source, source_sha256, stock_code = pilot_contract.source_contract(paths.source)
            snapshot, mount_plan, mount_digest, policy, policy_digest = self._prepare_runtime(
                spec,
                paths,
                expected_runtime_config_sha256=runtime_config_sha256,
            )
            key, nonce, identities = self._issue_secrets(spec, policy_digest=policy_digest)
            manifest = {
                "schema_version": self.settings.schema_version,
                "mode": self.settings.mode,
                "readiness_effect": READINESS_EFFECT,
                "phase": "prepared",
                "profile": PROFILE,
                **self._identity(spec.run_id),
                "market": spec.market,
                "company": spec.company,
                "analysis_relative_path": spec.analysis_relative_path,
                **self._manifest_scope_fields(paths),
                "source_sha256": source_sha256,
                "source_stock_code": stock_code,
                "sandbox_name": spec.sandbox_name,
                "lifecycle_label": self.settings.lifecycle_label,
                "image_ref": image_ref,
                "image_id": image_id,
                "runtime_snapshot": snapshot.relative_to(self.project_root).as_posix(),
                "mount_plan": mount_plan.relative_to(self.project_root).as_posix(),
                "mount_plan_sha256": mount_digest,
                "mount_count": BUSINESS_MOUNT_COUNT,
                "policy": policy.relative_to(self.project_root).as_posix(),
                "policy_sha256": policy_digest,
                "providers": list(PROVIDERS),
                "formal_blockers_not_overridden": list(KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED),
                "broker_request_identity_required": True,
                "api_key_sha256": _sha256_bytes(key.encode("ascii")),
                "run_nonce_sha256": _sha256_bytes(nonce.encode("ascii")),
                "host_hermes_receipt_sha256": _host_receipt_sha256(baseline),
                "sandbox_id": "",
                "container_id": "",
                "guard_process": GUARD_PROCESS_NAME,
                "forward_process": FORWARD_PROCESS_NAME,
                "result_is_formal_evidence": False,
            }
            self._write_manifest(spec, manifest)
            guard_record, guard_pid = self._spawn_guard(spec, policy_digest)

            driver_config = _read_private(mount_plan, root=self.project_root).decode("utf-8").strip()
            arguments = [
                "sandbox",
                "create",
                "--name",
                spec.sandbox_name,
                "--from",
                image_ref,
                "--cpu",
                "4",
                "--memory",
                "8Gi",
                "--driver-config-json",
                driver_config,
                "--policy",
                str(policy),
                "--label",
                f"ai.siq.run-nonce={nonce}",
                "--label",
                f"ai.siq.run-id={spec.run_id}",
                "--label",
                f"ai.siq.profile={PROFILE}",
                "--label",
                f"ai.siq.lifecycle={self.settings.lifecycle_label}",
                "--label",
                "ai.siq.readiness-effect=none",
                *_sandbox_entrypoint_env_arguments(self.project_root),
                "--env",
                f"API_SERVER_KEY={key}",
                "--env",
                f"SIQ_RUN_ID={spec.run_id}",
                "--env",
                "SIQ_PG_QUERY_BROKER_URL=http://host.openshell.internal:18793",
                "--env",
                "SIQ_REQUIRE_OPENSHELL_PROVIDERS=0",
                "--env",
                "SIQ_OPENSHELL_SANDBOX=1",
                "--env",
                f"{self.settings.sandbox_mode_environment[0]}={self.settings.sandbox_mode_environment[1]}",
                "--env",
                f"{broker_request_identity.EGRESS_TOKEN_ENV}={identities.egress_token}",
                "--env",
                f"{broker_request_identity.DATA_TOKEN_ENV}={identities.data_token}",
                "--env",
                "NO_PROXY=127.0.0.1,localhost,::1",
                "--env",
                "no_proxy=127.0.0.1,localhost,::1",
            ]
            for provider in PROVIDERS:
                arguments.extend(["--provider", provider])
            arguments.extend(
                [
                    "--no-auto-providers",
                    "--no-tty",
                    "--",
                    "/bin/sh",
                    "-c",
                    f"nohup setsid /opt/siq/entrypoint.sh >{self.settings.entrypoint_log} 2>&1 </dev/null &",
                ]
            )
            sandbox_attempted = True
            try:
                self.adapter._run_cli(
                    arguments,
                    self._code("sandbox_create_failed"),
                    secret_values=(key, nonce, *identities.secret_values()),
                )
            except LifecycleError as exc:
                raise WidePilotError(exc.code) from exc
            identity = self._verify_sandbox(spec, nonce)
            manifest.update(
                {
                    "phase": "sandbox_created",
                    "sandbox_id": identity.sandbox_id,
                    "container_id": identity.container_id,
                }
            )
            self._write_manifest(spec, manifest)
            forward_record, forward_pid = self._spawn_forward(spec)
            try:
                self.adapter._wait_health(
                    key,
                    forward_record,
                    local_port=self.settings.local_port,
                )
            except LifecycleError as exc:
                raise WidePilotError(self._code(exc.code)) from exc
            if not self._authenticated_forward_health(key):
                raise WidePilotError(self._code("forward_authenticated_health_failed"))
            if not self._authenticated_sandbox_exec_health(spec):
                raise WidePilotError(self._code("sandbox_authenticated_health_failed"))
            if self.adapter.backend.process_snapshot(guard_record.pid, "guard") != guard_record:
                raise WidePilotError(self._code("guard_process_exited"))
            manifest["phase"] = "running"
            self._write_manifest(spec, manifest)
            _write_json(
                self.active_path,
                {
                    "schema_version": self.settings.schema_version,
                    "mode": self.settings.mode,
                    "readiness_effect": READINESS_EFFECT,
                    "profile": PROFILE,
                    **self._identity(spec.run_id),
                    "market": spec.market,
                    "company": spec.company,
                    "run_state": spec.run_dir.relative_to(self.project_root).as_posix(),
                    **self._active_extra_fields(spec, manifest),
                },
                root=self.project_root,
            )
            self._after_active(spec, manifest)
            return {
                "ok": True,
                "schema_version": self.settings.schema_version,
                "mode": self.settings.mode,
                "readiness_effect": READINESS_EFFECT,
                "profile": PROFILE,
                **self._identity(spec.run_id),
                "status": "running",
                "runs_url": f"http://{FORWARD_HOST}:{self.settings.local_port}/v1/runs",
                "mount_count": BUSINESS_MOUNT_COUNT,
                "providers": list(PROVIDERS),
                "formal_readiness": "unchanged_no_go",
            }
        except Exception as exc:
            if spec.run_dir.exists():
                try:
                    self._cleanup_started(
                        spec,
                        manifest=manifest,
                        nonce=nonce,
                        guard_record=guard_record,
                        forward_record=forward_record,
                        guard_pid=guard_pid,
                        forward_pid=forward_pid,
                        sandbox_attempted=sandbox_attempted and bool(nonce),
                        allow_missing_output=True,
                    )
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise WidePilotError(self._code("rollback_incomplete")) from exc
            if isinstance(exc, WidePilotError):
                raise
            if isinstance(exc, pilot_contract.PilotContractError):
                raise WidePilotError(self._code(str(exc))) from exc
            raise WidePilotError(self._code("start_failed")) from exc

    def _load_active_spec(self, pilot_id: str) -> tuple[RunSpec, dict[str, Any]]:
        active = self._active()
        if self._identity_value(active) != pilot_id:
            raise WidePilotError(self._code("active_identity_mismatch"))
        spec = self._spec(
            market=str(active.get("market") or ""),
            company=str(active.get("company") or ""),
            pilot_id=pilot_id,
        )
        if active.get("run_state") != spec.run_dir.relative_to(self.project_root).as_posix():
            raise WidePilotError(self._code("active_state_invalid"))
        return spec, self._manifest(spec)

    def stop(self, *, pilot_id: str) -> dict[str, Any]:
        self._require_lock()
        spec, manifest = self._load_active_spec(pilot_id)
        nonce = _read_private(spec.run_dir / "run.nonce", root=self.project_root, max_bytes=256).decode().strip()
        if not NONCE_RE.fullmatch(nonce) or _sha256_bytes(nonce.encode()) != manifest.get("run_nonce_sha256"):
            raise WidePilotError(self._code("nonce_invalid"))
        forward = _private_process(
            spec.run_dir / FORWARD_PROCESS_NAME,
            root=self.project_root,
            role="forward",
            error_prefix=self.settings.error_prefix,
        )
        guard = _private_process(
            spec.run_dir / GUARD_PROCESS_NAME,
            root=self.project_root,
            role="guard",
            error_prefix=self.settings.error_prefix,
        )
        self._prepare_stop(spec, manifest)
        self._before_stop(spec, manifest)
        self._after_stop_marked(spec, manifest)
        self._cleanup_started(
            spec,
            manifest=manifest,
            nonce=nonce,
            guard_record=guard,
            forward_record=forward,
            guard_pid=guard.pid,
            forward_pid=forward.pid,
            sandbox_attempted=True,
            allow_missing_output=True,
        )
        manifest.update({"phase": "stopped", "source_stock_code": "<redacted>"})
        self._write_manifest(spec, manifest)
        result = {
            "ok": True,
            "schema_version": self.settings.schema_version,
            "mode": self.settings.mode,
            "readiness_effect": READINESS_EFFECT,
            "profile": PROFILE,
            **self._identity(pilot_id),
            "status": "stopped",
            "host_runtime_unchanged": True,
            "formal_readiness": "unchanged_no_go",
        }
        _write_json(spec.run_dir / STOP_RECEIPT_NAME, result, root=self.project_root)
        return result

    def status(self, *, pilot_id: str) -> dict[str, Any]:
        spec, manifest = self._load_active_spec(pilot_id)
        key = _read_private(spec.run_dir / "api.key", root=self.project_root, max_bytes=256).decode().strip()
        nonce = _read_private(spec.run_dir / "run.nonce", root=self.project_root, max_bytes=256).decode().strip()
        forward = _private_process(
            spec.run_dir / FORWARD_PROCESS_NAME,
            root=self.project_root,
            role="forward",
            error_prefix=self.settings.error_prefix,
        )
        guard = _private_process(
            spec.run_dir / GUARD_PROCESS_NAME,
            root=self.project_root,
            role="guard",
            error_prefix=self.settings.error_prefix,
        )
        forward_ok = self.adapter.backend.process_snapshot(forward.pid, "forward") == forward
        guard_ok = self.adapter.backend.process_snapshot(guard.pid, "guard") == guard
        try:
            self._verify_sandbox(
                spec,
                nonce,
                sandbox_id=str(manifest.get("sandbox_id") or ""),
                container_id=str(manifest.get("container_id") or ""),
            )
            sandbox_ok = True
        except WidePilotError:
            sandbox_ok = False
        forward_health_ok = forward_ok and self._authenticated_forward_health(key)
        sandbox_exec_health_ok = sandbox_ok and self._authenticated_sandbox_exec_health(spec)
        healthy = (
            manifest.get("phase") == "running"
            and guard_ok
            and forward_ok
            and sandbox_ok
            and forward_health_ok
            and sandbox_exec_health_ok
        )
        return {
            "ok": healthy,
            "schema_version": self.settings.schema_version,
            "mode": self.settings.mode,
            "readiness_effect": READINESS_EFFECT,
            "profile": PROFILE,
            **self._identity(pilot_id),
            "status": "running" if healthy else "degraded",
            "guard": guard_ok,
            "forward": forward_ok,
            "sandbox": sandbox_ok,
            "health": forward_health_ok and sandbox_exec_health_ok,
            "forward_authenticated_health": forward_health_ok,
            "sandbox_exec_authenticated_health": sandbox_exec_health_ok,
            "formal_readiness": "unchanged_no_go",
        }

    def probe(self, *, pilot_id: str) -> dict[str, Any]:
        spec, manifest = self._load_active_spec(pilot_id)
        status = self.status(pilot_id=pilot_id)
        if not status["ok"]:
            raise WidePilotError("wide_pilot_runtime_degraded")
        source = spec.analysis_root.parent / "company.json"
        source_before = _sha256_file(source)
        output_root = spec.analysis_root / ".work" / spec.run_id
        forbidden = spec.analysis_root / ".work" / f"forbidden-{spec.run_id}"
        subset_names = ",".join(PROVIDERS)
        checks = [
            "/bin/sh",
            "-c",
            (
                'test "$(id -u)" = 1000 && '
                'test "$HERMES_RUNTIME_HOME" = /sandbox/siq-analysis-runtime-state && '
                'touch "$HERMES_RUNTIME_HOME/wide-pilot-runtime-probe" && '
                'rm -f "$HERMES_RUNTIME_HOME/wide-pilot-runtime-probe"'
            ),
        ]
        try:
            self.adapter._run_cli(
                ["sandbox", "exec", "--name", spec.sandbox_name, "--timeout", "10", "--no-tty", "--", *checks],
                "wide_pilot_runtime_write_probe_failed",
            )
            denial_command = (
                f"if touch {str(forbidden)!r}; then exit 41; fi; "
                f"if printf x >> {str(source)!r}; then exit 42; fi; "
                f"test -d {str(output_root)!r}"
            )
            self.adapter._run_cli(
                [
                    "sandbox",
                    "exec",
                    "--name",
                    spec.sandbox_name,
                    "--timeout",
                    "10",
                    "--no-tty",
                    "--",
                    "/bin/sh",
                    "-c",
                    denial_command,
                ],
                "wide_pilot_filesystem_boundary_failed",
            )
            environment_probe = (
                "import os,re;"
                f"names={subset_names.split(',')!r};"
                "p=lambda n:re.fullmatch(r'openshell:resolve:env:(?:v[1-9][0-9]*_)?'+re.escape(n),os.environ.get(n,''));"
                "assert all(p(n) for n in ['KIMI_API_KEY','SIQ_MINIMAX_CN_BACKUP','SIQ_MINIMAX_CN_PRIMARY','SIQ_STEPFUN_LLM_API_KEY','TAVILY_API_KEY']);"
                "assert 'EXA_API_KEY' not in os.environ;"
                "assert os.environ.get('SIQ_WIDE_PILOT')=='1';"
                "assert os.environ.get('SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN','').startswith('v1.');"
                "assert os.environ.get('SIQ_OPENSHELL_DATA_IDENTITY_TOKEN','').startswith('v1.')"
            )
            self.adapter._run_cli(
                [
                    "sandbox",
                    "exec",
                    "--name",
                    spec.sandbox_name,
                    "--timeout",
                    "10",
                    "--no-tty",
                    "--",
                    "/opt/siq/hermes/venv/bin/python",
                    "-c",
                    environment_probe,
                ],
                "wide_pilot_provider_environment_probe_failed",
            )
            tavily_probe = (
                "import json;"
                "from plugins.web.tavily.provider import TavilyWebSearchProvider;"
                "r=TavilyWebSearchProvider().search('NVIDIA OpenShell official documentation',2);"
                "w=r.get('data',{}).get('web',[]) if isinstance(r,dict) else [];"
                "assert r.get('success') is True and len(w)>0;"
                "print('SIQ_TAVILY_PROVIDER_PROBE:'+str(len(w)))"
            )
            tavily_output = self.adapter._run_cli(
                [
                    "sandbox",
                    "exec",
                    "--name",
                    spec.sandbox_name,
                    "--timeout",
                    "30",
                    "--no-tty",
                    "--",
                    "/opt/siq/hermes/venv/bin/python",
                    "-c",
                    tavily_probe,
                ],
                "wide_pilot_tavily_provider_probe_failed",
            )
        except LifecycleError as exc:
            raise WidePilotError(exc.code) from exc
        tavily_match = re.search(r"(?:^|\n)SIQ_TAVILY_PROVIDER_PROBE:([1-9][0-9]*)(?:\n|$)", tavily_output)
        if tavily_match is None:
            raise WidePilotError("wide_pilot_tavily_provider_probe_invalid")
        tavily_result_count = int(tavily_match.group(1))
        if forbidden.exists() or _sha256_file(source) != source_before:
            raise WidePilotError("wide_pilot_host_source_changed")

        mount_plan = _read_json(self.project_root / str(manifest["mount_plan"]), root=self.project_root)
        expected_mounts = mount_plan.get("docker", {}).get("mounts")
        if not isinstance(expected_mounts, list) or len(expected_mounts) != BUSINESS_MOUNT_COUNT:
            raise WidePilotError("wide_pilot_mount_plan_invalid")
        try:
            actual_mounts = json.loads(
                self.adapter._docker_run(
                    ["inspect", str(manifest["container_id"]), "--format", "{{json .Mounts}}"],
                    "wide_pilot_mount_inspection_failed",
                )
            )
        except (LifecycleError, json.JSONDecodeError) as exc:
            raise WidePilotError("wide_pilot_mount_inspection_failed") from exc
        expected = {(item["source"], item["target"], item["read_only"] is False) for item in expected_mounts}
        actual = {(item.get("Source"), item.get("Destination"), item.get("RW")) for item in actual_mounts}
        if not expected.issubset(actual):
            raise WidePilotError("wide_pilot_business_mount_mismatch")
        controls = [
            item
            for item in actual_mounts
            if (item.get("Source"), item.get("Destination"), item.get("RW")) not in expected
        ]
        state_prefix = f"{self.project_root}/var/openshell/"
        allowed_control_targets = {
            "/opt/openshell/bin/openshell-sandbox",
            "/etc/openshell/auth/sandbox.jwt",
            "/etc/openshell/tls/client/ca.crt",
            "/etc/openshell/tls/client/tls.crt",
            "/etc/openshell/tls/client/tls.key",
        }
        if (
            len(controls) != 5
            or any(item.get("RW") is not False for item in controls)
            or {item.get("Destination") for item in controls} != allowed_control_targets
            or any(not str(item.get("Source") or "").startswith(state_prefix) for item in controls)
        ):
            raise WidePilotError("wide_pilot_control_mount_mismatch")
        result = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "readiness_effect": READINESS_EFFECT,
            "profile": PROFILE,
            "pilot_id": pilot_id,
            "status": "probe_passed",
            "source_read_only": True,
            "pilot_output_only": True,
            "runtime_state_writable": True,
            "broker_identity_present": True,
            "tavily_provider_status": "passed",
            "tavily_result_count": tavily_result_count,
            "provider_subset_count": len(PROVIDERS),
            "business_mount_count": BUSINESS_MOUNT_COUNT,
            "control_mount_count": len(controls),
            "formal_readiness": "unchanged_no_go",
        }
        _write_json(spec.run_dir / PROBE_RECEIPT_NAME, result, root=self.project_root)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument(ACKNOWLEDGEMENT, action="store_true", required=True)
    start.add_argument("--market", required=True, choices=sorted(pilot_contract.MARKET_ROOTS))
    start.add_argument("--company", required=True)
    start.add_argument("--pilot-id", required=True)
    for name in ("stop", "status", "probe"):
        child = subparsers.add_parser(name)
        child.add_argument("--pilot-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    try:
        lifecycle = WidePilotLifecycle()
        if args.command == "start":
            result = lifecycle.start(market=args.market, company=args.company, pilot_id=args.pilot_id)
        elif args.command == "stop":
            result = lifecycle.stop(pilot_id=args.pilot_id)
        elif args.command == "status":
            result = lifecycle.status(pilot_id=args.pilot_id)
        else:
            result = lifecycle.probe(pilot_id=args.pilot_id)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ok") is True else 1
    except (OSError, TypeError, ValueError, UnicodeError, LifecycleError, WidePilotError) as exc:
        if isinstance(exc, WidePilotError):
            code = exc.code
        elif isinstance(exc, LifecycleError):
            code = f"wide_pilot_{exc.code}"
        else:
            code = "wide_pilot_os_error"
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema_version": SCHEMA_VERSION,
                    "mode": MODE,
                    "readiness_effect": READINESS_EFFECT,
                    "error_code": code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
