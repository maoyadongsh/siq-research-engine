from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import scripts.openshell.destructive_action_guard as guard_module  # noqa: E402
import scripts.openshell.runtime_state_lifecycle_smoke as runtime_smoke  # noqa: E402
import scripts.openshell.siq_analysis_lifecycle as lifecycle_module  # noqa: E402
from scripts.openshell.build_siq_analysis_mount_plan import BUSINESS_MOUNT_COUNT  # noqa: E402
from scripts.openshell.siq_analysis_guard_worker import VerifiedSandboxTerminator  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    CHILD_PATH,
    DEFERRED_PROVIDERS,
    DOCKER_HOST,
    HOST_HERMES_RECEIPT_SCHEMA_VERSION,
    IMAGE_SMOKE_CHECKS,
    PROCESS_SCHEMA_VERSION,
    PROVIDERS,
    SECURITY_PROBE_LIFECYCLE_LABEL,
    SECURITY_PROBE_SCHEMA_VERSION,
    SYSTEM_PYTHON,
    WIDE_PILOT_LIFECYCLE_LABEL,
    CommandResult,
    HostHermesReceipt,
    LifecycleAdapter,
    LifecycleError,
    ProcessRecord,
    SystemBackend,
    _argv_sha256,
    _minimal_child_environment,
)

IMAGE_REF = "siq/hermes-openshell-siq-analysis:" + "b" * 24
IMAGE_ID = "sha256:" + "a" * 64
CONTEXT_SHA256 = "c" * 64
RUNTIME_CONFIG_BYTES = b"compiled runtime config\n"
CONFIG_SHA256 = hashlib.sha256(RUNTIME_CONFIG_BYTES).hexdigest()
HERMES_COMMIT = "ddb8d8fa842283ef651a6e4514f8f561f736c72e"
SANDBOX_ID = "11111111-1111-1111-1111-111111111111"
CONTAINER_ID = "f" * 64
BRIDGE_GATEWAY_CIDR = "172.28.0.1/32"


def _bridge_endpoint(port: int) -> dict[str, object]:
    return {
        "host": "host.openshell.internal",
        "port": port,
        "allowed_ips": [BRIDGE_GATEWAY_CIDR],
    }


def _write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _write_json(path: Path, payload: object, mode: int = 0o600) -> None:
    _write(path, (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(), mode)


def _prepare_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    analysis = project / "data/wiki/companies/600104-上汽集团/analysis"
    analysis.mkdir(parents=True)
    (analysis / "baseline.md").write_text("baseline\n", encoding="utf-8")
    state = project / "var/openshell/siq-analysis"
    state.mkdir(parents=True)
    for directory in (project / "var/openshell", state):
        directory.chmod(0o700)
    proof_dir = project / "var/openshell/proofs"
    proof_dir.mkdir(parents=True)
    proof_dir.chmod(0o700)
    secret_dir = project / "var/openshell/secrets"
    secret_dir.mkdir(mode=0o700)
    secret_dir.chmod(0o700)
    lifecycle_module.broker_request_identity.ensure_key_file(
        project / lifecycle_module.BROKER_IDENTITY_KEY_RELATIVE
    )
    _write_json(
        proof_dir / "service-security.json",
        {
            "schema_version": lifecycle_module.SERVICE_PROOF_SCHEMA,
            "postgres_readonly_identity": True,
            "milvus_write_protection": True,
        },
    )
    _write_json(
        proof_dir / "milvus-write-protection.json",
        {
            "schema_version": "fixture.validated-by-fake-service-preflight",
            "passed": True,
        },
    )

    smoke_script = project / "scripts/openshell/smoke_siq_analysis_image.sh"
    _write(smoke_script, b"#!/usr/bin/env bash\n# fixture smoke\n", 0o755)
    for name in (
        "run_cli.sh",
        "siq_analysis_guard_worker.py",
        "snapshot_siq_analysis_runtime.py",
        "build_siq_analysis_mount_plan.py",
        "build_policy.py",
        "render_gateway_config.py",
        "publish_company_index.py",
    ):
        _write(project / "scripts/openshell" / name, b"fixture\n", 0o755)

    provider_manifest = {
        "schema_version": "siq.openshell.provider_manifest.v1",
        "openshell_version": "0.0.83",
        "gateway": "siq-openshell-dev",
        "providers": [{"name": name} for name in (*PROVIDERS, *DEFERRED_PROVIDERS)],
    }
    _write_json(project / "infra/openshell/providers/manifest.json", provider_manifest, 0o644)
    patch = project / "infra/openshell/patches/v0.0.83/0001-landlock-mask-file-access.patch"
    patch.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "infra/openshell/patches/v0.0.83/0001-landlock-mask-file-access.patch", patch)
    patch.chmod(0o644)
    openshell = project / "var/openshell/toolchains/v0.0.83/bin/openshell"
    supervisor = project / "var/openshell/toolchains/v0.0.83/bin/openshell-sandbox"
    _write(openshell, b"fake openshell\n", 0o755)
    _write(supervisor, b"fake patched supervisor\n", 0o755)
    supervisor_sha = hashlib.sha256(supervisor.read_bytes()).hexdigest()
    record = (
        "schema=siq.openshell.supervisor_patch.v1\n"
        "active=patched\n"
        "version=0.0.83\n"
        "patch_sha256=f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f\n"
        f"patched_binary_sha256={supervisor_sha}\n"
        f"active_binary_sha256={supervisor_sha}\n"
    )
    _write(project / "var/openshell/build/v0.0.83/supervisor-patch.runtime", record.encode())

    candidate = {
        "schema_version": "siq.openshell.candidate_image.v1",
        "image_ref": IMAGE_REF,
        "image_id": IMAGE_ID,
        "architecture": "arm64",
        "user": "sandbox:sandbox",
        "hermes_commit": HERMES_COMMIT,
        "context_sha256": CONTEXT_SHA256,
        "runtime_config_sha256": CONFIG_SHA256,
    }
    candidate_path = state / "current-image.json"
    _write_json(candidate_path, candidate)
    runtime_root = tmp_path / "runtime-lifecycle-evidence"
    runtime_root.mkdir(mode=0o700)
    runtime_lifecycle = runtime_smoke.run_lifecycle_smoke(runtime_root)
    runtime_root.rmdir()
    smoke = {
        "schema_version": "siq.openshell.candidate_image_smoke.v1",
        "status": "passed",
        "profile": "siq_analysis",
        "image_ref": IMAGE_REF,
        "image_id": IMAGE_ID,
        "candidate_state_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "smoke_script_sha256": hashlib.sha256(smoke_script.read_bytes()).hexdigest(),
        "readiness_effect": "none",
        "runtime_lifecycle": runtime_lifecycle,
        "verified_at": "2026-07-15T00:00:00Z",
        "checks": IMAGE_SMOKE_CHECKS,
    }
    _write_json(state / "current-image.smoke.json", smoke)
    return project


class FakeBackend:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.events: list[tuple[str, object]] = []
        self.processes: dict[int, ProcessRecord] = {}
        self.next_pid = 4100
        self.sandbox: dict | None = None
        self.orphan: dict | None = None
        self.api_key = ""
        self.identity_tokens: dict[str, str] = {}
        self.nonce = ""
        self.policy_blocked = False
        self.create_failure = False
        self.snapshot_runtime_config_sha = CONFIG_SHA256
        self.service_preflight_ok = True
        self.service_protocol_ok = True
        self.broker_preflight_ok = True
        self.broker_identity_required = True
        self.publisher_ok = True
        self.host_health = True
        self.host_receipt = HostHermesReceipt(
            schema_version=HOST_HERMES_RECEIPT_SCHEMA_VERSION,
            profile="siq_analysis",
            host="127.0.0.1",
            port=18651,
            pid=3901,
            start_ticks=900001,
            executable="/fixture/hermes/python",
            argv_sha256="e" * 64,
            state_identity_sha256="f" * 64,
        )
        self.host_receipt_sequence: list[HostHermesReceipt] = []
        self.maintenance_lock = True
        self.deleted_container_linger_checks = 0
        self.container_linger_checks = 0
        self.secret_values_seen: list[tuple[str, ...]] = []

    def maintenance_lock_held(self) -> bool:
        return self.maintenance_lock

    def acquire_maintenance_lock(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds in {
            lifecycle_module.GUARD_ACTION_LOCK_TIMEOUT_SECONDS,
            lifecycle_module.GUARD_RECOVERY_LOCK_TIMEOUT_SECONDS,
        }
        self.maintenance_lock = True
        self.events.append(("maintenance_lock_acquire", timeout_seconds))

    def _result(self, stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
        return CommandResult(code, stdout, stderr)

    def run(self, argv: Sequence[str], *, secrets_to_redact: Sequence[str] = ()) -> CommandResult:
        args = list(argv)
        self.secret_values_seen.append(tuple(secrets_to_redact))
        first = Path(args[0]).name
        if first == "run_cli.sh":
            command = args[1:]
            self.events.append(("cli", command[0:3]))
            if command == ["--version"]:
                return self._result("openshell 0.0.83\n")
            if command == ["status"]:
                return self._result(
                    "Gateway: siq-openshell-dev\nServer: https://127.0.0.1:17671\nStatus: Connected\nVersion: 0.0.83\n"
                )
            if command == ["provider", "list", "--names"]:
                return self._result("\n".join(PROVIDERS) + "\n")
            if command[:4] == ["sandbox", "list", "-o", "json"]:
                inventory = [self.sandbox] if self.sandbox is not None else []
                return self._result(json.dumps(inventory))
            if command[:2] == ["sandbox", "create"]:
                labels: dict[str, str] = {}
                for index, value in enumerate(command):
                    if value == "--label":
                        key, label_value = command[index + 1].split("=", 1)
                        labels[key] = label_value
                    elif value == "--env" and command[index + 1].startswith("API_SERVER_KEY="):
                        self.api_key = command[index + 1].split("=", 1)[1]
                    elif value == "--env" and "_IDENTITY_TOKEN=" in command[index + 1]:
                        name, token = command[index + 1].split("=", 1)
                        self.identity_tokens[name] = token
                self.nonce = labels["ai.siq.run-nonce"]
                self.sandbox = {
                    "id": SANDBOX_ID,
                    "name": command[command.index("--name") + 1],
                    "labels": labels,
                    "phase": "Ready",
                }
                self.events.append(("sandbox_create", tuple(command)))
                if self.create_failure:
                    return self._result(stderr=f"failure {self.api_key} {self.nonce}", code=2)
                return self._result(stdout=f"created {self.api_key} {self.nonce}\n")
            if command[:2] == ["sandbox", "delete"]:
                self.events.append(("sandbox_delete", command[2]))
                self.sandbox = None
                self.deleted_container_linger_checks = self.container_linger_checks
                return self._result("deleted\n")
            raise AssertionError(f"unexpected run_cli command: {command}")

        if first == "docker":
            command = args[1:]
            self.events.append(("docker", command[0:2]))
            if command[:2] == ["ps", "-aq"]:
                if self.deleted_container_linger_checks > 0:
                    self.deleted_container_linger_checks -= 1
                    return self._result(CONTAINER_ID + "\n")
                return self._result(
                    (CONTAINER_ID + "\n") if self.sandbox is not None or self.orphan is not None else ""
                )
            if command and command[0] == "inspect":
                active = self.sandbox or self.orphan
                assert active is not None
                labels = {
                    "openshell.ai/managed-by": "openshell",
                    "openshell.ai/sandbox-namespace": "siq-openshell-dev",
                    "openshell.ai/sandbox-name": active["name"],
                    "openshell.ai/sandbox-id": SANDBOX_ID,
                }
                return self._result(json.dumps(labels))
            if command[:2] == ["rm", "-f"]:
                self.orphan = None
                return self._result(CONTAINER_ID + "\n")
            if command[:2] == ["image", "inspect"]:
                format_value = command[-1]
                values = {
                    "{{.Id}}": IMAGE_ID,
                    "{{.Architecture}}": "arm64",
                    "{{.Config.User}}": "sandbox:sandbox",
                    "{{json .Config.Cmd}}": '["/opt/siq/entrypoint.sh"]',
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': HERMES_COMMIT,
                    '{{index .Config.Labels "ai.siq.openshell.context-sha256"}}': CONTEXT_SHA256,
                    '{{index .Config.Labels "ai.siq.openshell.runtime-config-sha256"}}': CONFIG_SHA256,
                }
                return self._result(values[format_value] + "\n")
            raise AssertionError(f"unexpected docker command: {command}")

        if args[0] in {sys.executable, SYSTEM_PYTHON}:
            script_index = next(index for index, value in enumerate(args[1:], start=1) if value.endswith(".py"))
            script = Path(args[script_index]).name
            args = [args[0], *args[script_index:]]
            self.events.append(("builder", script))
            if script == "render_gateway_config.py":
                return self._result()
            if script == "check_siq_services.py":
                if not self.service_preflight_ok:
                    return self._result(
                        json.dumps(
                            {
                                "schema_version": lifecycle_module.SERVICE_PREFLIGHT_SCHEMA,
                                "decision": "NO_GO",
                                "passed": False,
                                "blockers": [{"error_code": "fixture_service_unavailable"}],
                            }
                        ),
                        code=1,
                    )
                services = []
                for service_id, port in lifecycle_module.FORMAL_SERVICE_PORTS.items():
                    required = service_id in lifecycle_module.FORMAL_REQUIRED_SERVICES
                    expected_protocol = lifecycle_module.FORMAL_PROTOCOL_CONTRACTS.get(service_id)
                    services.append(
                        {
                            "service_id": service_id,
                            "port": port,
                            "requirement": "required" if required else "optional",
                            "blocking": required,
                            "reachable": True,
                            "status": "pass",
                            "protocol_check": {
                                "contract": expected_protocol[0] if expected_protocol else "not_applicable",
                                "method": "GET" if expected_protocol else "",
                                "path": expected_protocol[1] if expected_protocol else "",
                                "checked": bool(expected_protocol),
                                "available": True if expected_protocol else None,
                                "status": "pass" if expected_protocol else "not_applicable",
                            },
                        }
                    )
                if not self.service_protocol_ok:
                    target = next(item for item in services if item["service_id"] == "embedding")
                    target["protocol_check"]["available"] = False
                    target["protocol_check"]["status"] = "no_go"
                checks = [
                    {
                        "check_id": check_id,
                        "status": "pass",
                        "proof_present": True,
                        "proof_source": "proof_file",
                    }
                    for check_id in ("postgres_readonly_identity", "milvus_write_protection")
                ]
                return self._result(
                    json.dumps(
                        {
                            "schema_version": lifecycle_module.SERVICE_PREFLIGHT_SCHEMA,
                            "decision": "GO",
                            "passed": True,
                            "probe_scope": {
                                "read_only": True,
                                "protocol": lifecycle_module.SERVICE_PREFLIGHT_PROTOCOL,
                                "host_alias_kind": "loopback",
                                "http_method": "GET",
                                "request_body_sent": False,
                                "redirects_followed": False,
                                "response_body_recorded": False,
                            },
                            "services": services,
                            "security_checks": checks,
                            "blockers": [],
                            "summary": {
                                "required_total": len(lifecycle_module.FORMAL_REQUIRED_SERVICES),
                                "required_reachable": len(lifecycle_module.FORMAL_REQUIRED_SERVICES),
                                "required_protocol_total": len(
                                    lifecycle_module.FORMAL_REQUIRED_SERVICES
                                    & lifecycle_module.FORMAL_PROTOCOL_CONTRACTS.keys()
                                ),
                                "required_protocol_available": len(
                                    lifecycle_module.FORMAL_REQUIRED_SERVICES
                                    & lifecycle_module.FORMAL_PROTOCOL_CONTRACTS.keys()
                                ),
                                "optional_protocol_total": len(
                                    (set(lifecycle_module.FORMAL_SERVICE_PORTS)
                                     - lifecycle_module.FORMAL_REQUIRED_SERVICES)
                                    & lifecycle_module.FORMAL_PROTOCOL_CONTRACTS.keys()
                                ),
                                "optional_protocol_available": len(
                                    (set(lifecycle_module.FORMAL_SERVICE_PORTS)
                                     - lifecycle_module.FORMAL_REQUIRED_SERVICES)
                                    & lifecycle_module.FORMAL_PROTOCOL_CONTRACTS.keys()
                                ),
                                "security_proofs_required": 2,
                                "security_proofs_present": 2,
                                "blocking_count": 0,
                            },
                        }
                    )
                )
            if script == "broker_lifecycle.py":
                if not self.broker_preflight_ok:
                    return self._result(
                        json.dumps({"ok": False, "action": "status", "error_code": "fixture_broker_unavailable"}),
                        code=2,
                    )
                return self._result(
                    json.dumps(
                        {
                            "ok": True,
                            "action": "status",
                            "bridge": {"network": lifecycle_module.NAMESPACE, "alias": "host.openshell.internal"},
                            "brokers": {
                                name: {
                                    "state": "running",
                                    "port": port,
                                    "pid": 5000 + index,
                                    "request_identity_required": self.broker_identity_required,
                                }
                                for index, (name, port) in enumerate(
                                    lifecycle_module.FORMAL_BROKER_PORTS.items(), start=1
                                )
                            },
                        }
                    )
                )
            if script == "publish_company_index.py":
                if not self.publisher_ok:
                    return self._result(code=2)
                market = args[args.index("--market") + 1]
                company = args[args.index("--company-id") + 1]
                return self._result(
                    json.dumps(
                        {
                            "schema_version": lifecycle_module.PUBLISHER_SCHEMA,
                            "ok": True,
                            "market": market,
                            "company_projection": hashlib.sha256(f"{market}:{company}".encode()).hexdigest()[:24],
                            "index_schema_version": 1,
                        }
                    )
                )
            if script == "snapshot_siq_analysis_runtime.py":
                assert "--compile-config" in args
                assert "--fresh" in args
                output = Path(args[args.index("--output") + 1])
                output.mkdir(parents=True)
                output.chmod(0o700)
                _write(output / "config.yaml", RUNTIME_CONFIG_BYTES)
                _write_json(
                    output / "snapshot-manifest.json",
                    {
                        "schema_version": "siq.openshell.siq_analysis_runtime_snapshot.v3",
                        "profile": "siq_analysis",
                        "snapshot_mode": "fresh",
                        "host_runtime_records_copied": False,
                        "source_scope": "current_project_siq_analysis_config_only",
                        "inventory": {
                            "config": {
                                "byte_count": len(RUNTIME_CONFIG_BYTES),
                                "tree_sha256": CONFIG_SHA256,
                                "source_sha256": "e" * 64,
                                "compiled": True,
                                "compiled_sha256": self.snapshot_runtime_config_sha,
                                "compiler_schema_version": "siq.openshell.hermes_runtime_config.v1",
                            }
                        },
                    },
                )
                return self._result(
                    json.dumps(
                        {
                            "profile": "siq_analysis",
                            "snapshot": str(output),
                            "runtime_config_sha256": self.snapshot_runtime_config_sha,
                            "source_config_sha256": "e" * 64,
                            "snapshot_mode": "fresh",
                            "host_runtime_records_copied": False,
                        }
                    )
                )
            if script == "build_siq_analysis_mount_plan.py":
                output_root = self.project / "var/openshell/siq-analysis/mount-plans"
                output_root.mkdir(parents=True, exist_ok=True)
                output_root.chmod(0o700)
                plan_document = {
                    "docker": {
                        "mounts": [
                            {
                                "type": "bind",
                                "source": f"/fixture/source/{index}",
                                "target": f"/fixture/target/{index}",
                                "read_only": index == 0,
                            }
                            for index in range(BUSINESS_MOUNT_COUNT)
                        ]
                    }
                }
                content = (json.dumps(plan_document, separators=(",", ":"), sort_keys=True) + "\n").encode()
                digest = hashlib.sha256(content).hexdigest()
                plan = output_root / f"{digest}.driver-config.json"
                summary = output_root / f"{digest}.summary.json"
                _write(plan, content)
                company = args[args.index("--analysis-dir") + 1]
                relative = Path(company).relative_to(self.project).as_posix()
                _write_json(
                    summary,
                    {
                        "profile": "siq_analysis",
                        "mount_count": BUSINESS_MOUNT_COUNT,
                        "analysis_relative_path": relative,
                        "driver_config_sha256": digest,
                    },
                )
                return self._result(
                    json.dumps(
                        {
                            "mount_count": BUSINESS_MOUNT_COUNT,
                            "sha256": digest,
                            "driver_config": plan.relative_to(self.project).as_posix(),
                            "summary": summary.relative_to(self.project).as_posix(),
                        }
                    )
                )
            if script == "build_policy.py":
                if self.policy_blocked:
                    return self._result(
                        stderr="required Hermes runtime file is missing or not regular",
                        code=2,
                    )
                output = Path(args[args.index("--output") + 1])
                summary = Path(args[args.index("--summary-output") + 1])
                _write_json(
                    output,
                    {
                        "version": 1,
                        "profile": "siq_analysis",
                        "network_policies": {
                            "siq_data_broker": {
                                "endpoints": [_bridge_endpoint(18793)],
                                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                            },
                            "siq_egress_guard": {
                                "endpoints": [_bridge_endpoint(18792)],
                                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                            },
                            "siq_internal_services": {
                                "endpoints": [
                                    _bridge_endpoint(port)
                                    for port in (8004, 8006, 8007, 8013)
                                ],
                                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                            },
                        },
                    },
                )
                _write_json(
                    summary,
                    {
                        "profile": "siq_analysis",
                        "task_scoped_write_count": 1,
                        "project_root": "${SIQ_PROJECT_ROOT}",
                    },
                )
                return self._result("compiled\n")
        raise AssertionError(f"unexpected command: {args}")

    def spawn(self, argv: Sequence[str], *, log_path: Path) -> int:
        args = list(argv)
        _write(log_path, b"")
        self.next_pid += 1
        pid = self.next_pid
        script_index = next(
            (index for index, value in enumerate(args[1:], start=1) if value.endswith(".py")),
            None,
        )
        if script_index is not None and Path(args[script_index]).name == "siq_analysis_guard_worker.py":
            role = "guard"
            effective_argv = args
            executable = str(Path(args[0]).resolve())
            run_dir = Path(args[args.index("--run-dir") + 1])
            run_id = args[args.index("--run-id") + 1]
            analysis_root = Path(args[args.index("--analysis-root") + 1])
            guard_module._create_snapshot(
                project_root=self.project,
                analysis_root=analysis_root,
                analysis_relative_path=analysis_root.relative_to(self.project).as_posix(),
                run_id=run_id,
            )
            _write_json(
                run_dir / "guard.ready.json",
                {"status": "ready", "run_id": run_id, "pid": pid},
            )
        else:
            role = "forward"
            executable = str((self.project / "var/openshell/toolchains/v0.0.83/bin/openshell").resolve())
            effective_argv = [executable, *args[1:]]
        record = ProcessRecord(
            schema_version=PROCESS_SCHEMA_VERSION,
            role=role,
            pid=pid,
            start_ticks=100000 + pid,
            executable=executable,
            argv_sha256=_argv_sha256(effective_argv),
        )
        self.processes[pid] = record
        self.events.append(("spawn", role))
        return pid

    def process_snapshot(self, pid: int, role: str) -> ProcessRecord | None:
        record = self.processes.get(pid)
        return record if record is not None and record.role == role else None

    def terminate(self, record: ProcessRecord, *, timeout_seconds: float = 8.0) -> None:
        del timeout_seconds
        if self.processes.get(record.pid) != record:
            raise LifecycleError(f"{record.role}_pid_identity_mismatch")
        self.events.append(("terminate", record.role))
        self.processes.pop(record.pid)

    def port_available(self, host: str, port: int) -> bool:
        del host, port
        return not any(record.role == "forward" for record in self.processes.values())

    def port_listener_absent(self, host: str, port: int) -> bool:
        return self.port_available(host, port)

    def hermes_health(self, host: str, port: int, key: str | None) -> bool:
        del host
        if port == 18651:
            self.events.append(("host_health", self.host_health))
            return self.host_health
        return (
            self.sandbox is not None
            and any(record.role == "forward" for record in self.processes.values())
            and key == self.api_key
        )

    def host_hermes_receipt(self, *, profile: str, host: str, port: int) -> HostHermesReceipt:
        assert (profile, host, port) == ("siq_analysis", "127.0.0.1", 18651)
        receipt = self.host_receipt_sequence.pop(0) if self.host_receipt_sequence else self.host_receipt
        self.events.append(("host_receipt", receipt))
        return receipt

    def hermes_rejects(self, host: str, port: int, key: str | None) -> bool:
        del host, port
        return key != self.api_key


@pytest.fixture
def lifecycle(tmp_path: Path) -> tuple[Path, FakeBackend, LifecycleAdapter]:
    project = _prepare_project(tmp_path)
    backend = FakeBackend(project)
    return project, backend, LifecycleAdapter(project_root=project, backend=backend)


def _start(adapter: LifecycleAdapter) -> dict:
    spec = adapter.spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        run_id="task-001",
    )
    return adapter.start(spec)


def test_system_backend_checks_auth_on_protected_runs_endpoint(tmp_path: Path, monkeypatch) -> None:
    requests = []

    def reject(request, *, timeout):
        assert timeout == 1
        requests.append(request)
        raise lifecycle_module.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(lifecycle_module.urllib.request, "urlopen", reject)
    backend = lifecycle_module.SystemBackend(project_root=tmp_path.resolve())

    assert backend.hermes_rejects("127.0.0.1", 28651, None) is True
    assert backend.hermes_rejects("127.0.0.1", 28651, "0" * 64) is True
    assert [request.get_method() for request in requests] == ["POST", "POST"]
    assert [request.full_url for request in requests] == [
        "http://127.0.0.1:28651/v1/runs",
        "http://127.0.0.1:28651/v1/runs",
    ]
    assert all(request.data == b"{}" for request in requests)
    assert requests[0].get_header("Authorization") is None
    assert requests[1].get_header("Authorization") == f"Bearer {'0' * 64}"


def test_pool_preflight_allows_only_exact_registered_canary_inventory(lifecycle) -> None:
    _project, backend, adapter = lifecycle
    run_id = "canary-0123456789ab"
    sandbox_name = f"siq-analysis-{run_id}"
    backend.sandbox = {
        "id": SANDBOX_ID,
        "name": sandbox_name,
        "phase": "Ready",
        "labels": {
            "ai.siq.lifecycle": lifecycle_module.CANARY_LIFECYCLE_LABEL,
            "ai.siq.profile": "siq_analysis",
            "ai.siq.readiness-effect": "none",
            "ai.siq.run-id": run_id,
        },
    }
    allowed = {
        sandbox_name: {
            "run_id": run_id,
            "sandbox_id": SANDBOX_ID,
            "container_id": CONTAINER_ID,
        }
    }

    adapter._registered_pool_sandboxes = lambda: allowed

    assert adapter.validate_security_probe_prerequisites(
        forward_port=28652,
    ) == (IMAGE_REF, IMAGE_ID, CONFIG_SHA256)

    backend.sandbox["name"] = "unregistered-sandbox"
    with pytest.raises(LifecycleError, match="pool_sandbox_inventory_mismatch"):
        adapter.validate_security_probe_prerequisites(
            forward_port=28652,
        )


def test_registered_pool_inventory_is_rebound_to_private_manifest(lifecycle, monkeypatch) -> None:
    project, _backend, adapter = lifecycle
    from scripts.openshell import siq_analysis_pool_registry as pool_registry

    run_id = "canary-0123456789ab"
    sandbox_name = f"siq-analysis-{run_id}"
    short_container_id = "e2e743c5cfa4"
    manifest = project / "var/openshell/canary/siq-analysis/runs" / run_id / "canary.json"
    _write_json(
        manifest,
        {
            "run_id": run_id,
            "sandbox_name": sandbox_name,
            "lifecycle_label": lifecycle_module.CANARY_LIFECYCLE_LABEL,
            "sandbox_id": SANDBOX_ID,
            "container_id": short_container_id,
        },
    )
    entry = {
        "market": "cn",
        "company": "600104-上汽集团",
        "run_id": run_id,
        "manifest": manifest.relative_to(project).as_posix(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "sandbox_name": sandbox_name,
        "local_port": 28652,
    }
    monkeypatch.setattr(
        pool_registry,
        "load_registry",
        lambda **_kwargs: {"bindings": [entry]},
    )
    monkeypatch.setattr(
        pool_registry,
        "resolve",
        lambda **_kwargs: pool_registry.PoolRoute(
            target="openshell",
            market="cn",
            company="600104-上汽集团",
            base="http://127.0.0.1:28652/v1/runs",
            run_id=run_id,
        ),
    )

    assert adapter._registered_pool_sandboxes() == {
        sandbox_name: {
            "run_id": run_id,
            "sandbox_id": SANDBOX_ID,
            "container_id": short_container_id,
        }
    }

    entry["manifest_sha256"] = "0" * 64
    with pytest.raises(LifecycleError, match="pool_sandbox_registry_invalid"):
        adapter._registered_pool_sandboxes()

    invalid = json.loads(manifest.read_text(encoding="utf-8"))
    invalid["container_id"] = "E2E743C5CFA4"
    _write_json(manifest, invalid)
    entry["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(LifecycleError, match="pool_sandbox_registry_invalid"):
        adapter._registered_pool_sandboxes()


def _prepare_security_probe_intent(adapter: LifecycleAdapter) -> tuple[object, str]:
    spec = adapter.security_probe_spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        probe_id="probe-123456789abc",
    )
    plan = adapter.prepare_security_probe_runtime(spec)
    nonce = "1" * 48
    _write(spec.run_dir / "run.nonce", f"{nonce}\n".encode())
    _write_json(
        spec.run_dir / "probe.json",
        {
            "schema_version": SECURITY_PROBE_SCHEMA_VERSION,
            "phase": "prepared",
            "probe_id": spec.run_id,
            "sandbox_name": spec.sandbox_name,
            "run_nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
            "mount_plan_sha256": plan.mount_plan_sha256,
            "policy_sha256": plan.policy_sha256,
        },
    )
    return plan, nonce


def test_wide_pilot_label_is_identity_verified_but_unknown_labels_are_rejected(lifecycle) -> None:
    _project, backend, adapter = lifecycle
    nonce = "8" * 48
    run_id = "pilot-123456789abc"
    sandbox_name = f"siq-analysis-wide-{run_id}"
    backend.sandbox = {
        "id": SANDBOX_ID,
        "name": sandbox_name,
        "labels": {
            "ai.siq.run-nonce": nonce,
            "ai.siq.run-id": run_id,
            "ai.siq.profile": "siq_analysis",
            "ai.siq.lifecycle": WIDE_PILOT_LIFECYCLE_LABEL,
        },
        "phase": "Ready",
    }

    identity = adapter.verify_sandbox_identity(
        sandbox_name=sandbox_name,
        run_id=run_id,
        nonce=nonce,
        lifecycle_label=WIDE_PILOT_LIFECYCLE_LABEL,
    )

    assert identity.sandbox_id == SANDBOX_ID
    assert identity.container_id == CONTAINER_ID
    with pytest.raises(LifecycleError, match="sandbox_identity_input_invalid"):
        adapter.verify_sandbox_identity(
            sandbox_name=sandbox_name,
            run_id=run_id,
            nonce=nonce,
            lifecycle_label="unreviewed-pilot-label",
        )


def test_start_uses_fixed_order_mounts_providers_and_secret_free_state(lifecycle) -> None:
    project, backend, adapter = lifecycle

    result = _start(adapter)

    assert result["mount_count"] == BUSINESS_MOUNT_COUNT
    assert result["providers"] == list(PROVIDERS)
    assert "28651" in result["runs_url"]
    event_names = [(kind, value) for kind, value in backend.events]
    policy_index = event_names.index(("builder", "build_policy.py"))
    guard_index = event_names.index(("spawn", "guard"))
    create_index = next(index for index, item in enumerate(event_names) if item[0] == "sandbox_create")
    forward_index = event_names.index(("spawn", "forward"))
    assert policy_index < guard_index < create_index < forward_index

    create = next(value for kind, value in backend.events if kind == "sandbox_create")
    assert create.count("--provider") == len(PROVIDERS)
    assert "--no-auto-providers" in create
    assert "--driver-config-json" in create
    assert "SIQ_PG_QUERY_BROKER_URL=http://host.openshell.internal:18793" in create
    assert "SIQ_REQUIRE_OPENSHELL_PROVIDERS=1" in create
    assert "SIQ_OPENSHELL_SANDBOX=1" in create
    assert f"SIQ_PROJECT_ROOT={project}" in create
    assert f"HERMES_HOME={project}/data/hermes/home/profiles/siq_analysis" in create
    assert "HERMES_RUNTIME_HOME=/sandbox/siq-analysis-runtime-state" in create
    assert "HERMES_AUTH_FILE=/sandbox/runtime-auth/auth.json" in create
    assert "API_SERVER_ENABLED=true" in create
    assert "API_SERVER_HOST=127.0.0.1" in create
    assert "API_SERVER_PORT=28651" in create
    assert "API_SERVER_MODEL_NAME=siq_analysis" in create
    assert "PYTHONPYCACHEPREFIX=/tmp/siq-pycache" in create
    assert any(value.startswith("PATH=/opt/siq/hermes/venv/bin:") for value in create)
    assert "NO_PROXY=127.0.0.1,localhost,::1" in create
    assert "no_proxy=127.0.0.1,localhost,::1" in create
    assert not any("NO_PROXY=" in value and "host.openshell.internal" in value for value in create)
    assert not any("no_proxy=" in value and "host.openshell.internal" in value for value in create)
    assert any(value.startswith("SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN=v1.") for value in create)
    assert any(value.startswith("SIQ_OPENSHELL_DATA_IDENTITY_TOKEN=v1.") for value in create)
    assert "--upload" not in create
    assert "nemoclaw" not in " ".join(create)

    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    key = (run_dir / "api.key").read_text(encoding="ascii").strip()
    nonce = (run_dir / "run.nonce").read_text(encoding="ascii").strip()
    egress_token = (run_dir / "egress.identity.token").read_text(encoding="ascii").strip()
    data_token = (run_dir / "data.identity.token").read_text(encoding="ascii").strip()
    assert len(key) == 64 and len(nonce) == 48
    assert (run_dir / "api.key").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "run.nonce").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "egress.identity.token").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "data.identity.token").stat().st_mode & 0o777 == 0o600
    identity_key = lifecycle_module.broker_request_identity.read_key_file(
        project / lifecycle_module.BROKER_IDENTITY_KEY_RELATIVE
    )
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    egress_identity = lifecycle_module.broker_request_identity.verify_identity(egress_token, identity_key)
    data_identity = lifecycle_module.broker_request_identity.verify_identity(data_token, identity_key)
    assert egress_identity.audience == "siq-egress-guard"
    assert data_identity.audience == "siq-read-only-data-broker"
    for identity in (egress_identity, data_identity):
        assert identity.profile == "siq_analysis"
        assert identity.run_id == "task-001"
        assert identity.sandbox_id == "siq-analysis-task-001"
        assert identity.session_id == "task-001"
        assert identity.policy_digest == manifest["policy_sha256"]
        assert identity.run_nonce_digest == manifest["run_nonce_sha256"]
        assert identity.expires_at - identity.issued_at == lifecycle_module.BROKER_IDENTITY_TTL_SECONDS
    assert manifest["phase"] == "running"
    assert manifest["sandbox_id"] == SANDBOX_ID
    assert manifest["container_id"] == CONTAINER_ID
    assert key not in json.dumps(result)
    assert nonce not in json.dumps(result)
    active = json.loads((project / "var/openshell/siq-analysis/active-run.json").read_text(encoding="utf-8"))
    assert active["schema_version"] == lifecycle_module.transaction.ACTIVE_SCHEMA
    journal_path = project / active["journal"]
    journal_text = journal_path.read_text(encoding="utf-8")
    journal = json.loads(journal_text)
    assert journal["phase"] == "running"
    assert all(item["state"] == "present" for item in journal["resources"].values())
    assert key not in journal_text
    assert nonce not in journal_text
    for path in run_dir.iterdir():
        if path.name in {"api.key", "run.nonce", *lifecycle_module.BROKER_IDENTITY_SECRET_FILES} or not path.is_file():
            continue
        content = path.read_bytes()
        assert key.encode() not in content
        assert nonce.encode() not in content
    assert any(
        key in values and nonce in values and egress_token in values and data_token in values
        for values in backend.secret_values_seen
    )


def test_fallback_fault_injection_is_exact_programmatic_and_transaction_bound(lifecycle) -> None:
    project, backend, adapter = lifecycle
    spec = adapter.spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        run_id="fallback-123456789abc",
    )

    adapter.start(spec, sandbox_environment_overrides=lifecycle_module.FALLBACK_FAULT_ENVIRONMENT)

    create = next(value for kind, value in backend.events if kind == "sandbox_create")
    assert "MINIMAX_CN_BASE_URL=http://host.openshell.internal:8004/v1" in create
    injection = spec.run_dir / lifecycle_module.FALLBACK_FAULT_INJECTION_NAME
    assert injection.stat().st_mode & 0o777 == 0o600
    assert json.loads(injection.read_text(encoding="utf-8")) == {
        "schema_version": lifecycle_module.FALLBACK_FAULT_INJECTION_SCHEMA,
        "kind": "primary_http_503",
        "environment": lifecycle_module.FALLBACK_FAULT_ENVIRONMENT,
        "bind_scope": "verified_docker_bridge_gateway_only",
        "expected_status": 503,
    }
    active = json.loads((project / "var/openshell/siq-analysis/active-run.json").read_text(encoding="utf-8"))
    journal = lifecycle_module.transaction.load(project, active["transaction_id"])
    manifest = json.loads((spec.run_dir / "run.json").read_text(encoding="utf-8"))
    assert journal["resources"]["sandbox"]["intent_sha256"] == adapter._sandbox_intent_sha(spec, manifest)


def test_fallback_fault_injection_rejects_any_other_run_or_environment_before_start(lifecycle) -> None:
    project, backend, adapter = lifecycle
    spec = adapter.spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        run_id="task-001",
    )

    with pytest.raises(LifecycleError, match="fallback_fault_injection_invalid"):
        adapter.start(spec, sandbox_environment_overrides=lifecycle_module.FALLBACK_FAULT_ENVIRONMENT)
    with pytest.raises(LifecycleError, match="fallback_fault_injection_invalid"):
        adapter.start(
            spec,
            sandbox_environment_overrides={"MINIMAX_CN_BASE_URL": "http://127.0.0.1:8004/v1"},
        )

    assert not spec.run_dir.exists()
    assert not backend.events
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()


def test_prepare_analysis_root_for_start_creates_only_leaf(lifecycle) -> None:
    project, _backend, adapter = lifecycle
    company_root = project / "data/wiki/companies/600104-new"
    company_root.mkdir(parents=True)

    spec = adapter.prepare_analysis_root_for_start(
        profile="siq_analysis", market="cn", company="600104-new", run_id="task-new"
    )

    assert spec.analysis_root == company_root / "analysis"
    assert spec.analysis_root.is_dir()
    assert spec.analysis_root.stat().st_mode & 0o077 == 0


def test_prepare_analysis_root_for_start_never_creates_company(lifecycle) -> None:
    project, _backend, adapter = lifecycle

    with pytest.raises(LifecycleError, match="company_root_missing"):
        adapter.prepare_analysis_root_for_start(
            profile="siq_analysis", market="cn", company="600104-missing", run_id="task-new"
        )

    assert not (project / "data/wiki/companies/600104-missing").exists()


def test_deferred_provider_remains_in_manifest_without_becoming_required() -> None:
    manifest = json.loads((ROOT / "infra/openshell/providers/manifest.json").read_text(encoding="utf-8"))
    manifest_names = [item["name"] for item in manifest["providers"]]

    assert manifest_names == [*PROVIDERS, *DEFERRED_PROVIDERS]
    assert "siq-exa-search" not in PROVIDERS


def test_start_rejects_runtime_snapshot_not_bound_to_candidate_image(lifecycle) -> None:
    _project, backend, adapter = lifecycle
    backend.snapshot_runtime_config_sha = "f" * 64

    with pytest.raises(LifecycleError, match="runtime_snapshot_result_invalid"):
        _start(adapter)

    assert not any(kind in {"spawn", "sandbox_create"} for kind, _ in backend.events)


def test_start_fails_before_transaction_when_service_preflight_is_no_go(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.service_preflight_ok = False

    with pytest.raises(LifecycleError, match="service_preflight_no_go"):
        _start(adapter)

    state = project / "var/openshell/siq-analysis"
    assert backend.sandbox is None
    assert not (state / "active-run.json").exists()
    assert not (state / "transactions/tx-task-001.json").exists()
    assert not (state / "runs/task-001").exists()
    assert not any(kind == "spawn" for kind, _ in backend.events)
    assert not any(value == "snapshot_siq_analysis_runtime.py" for kind, value in backend.events if kind == "builder")


def test_start_rejects_inconsistent_protocol_preflight_before_transaction(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.service_protocol_ok = False

    with pytest.raises(LifecycleError, match="service_preflight_invalid"):
        _start(adapter)

    state = project / "var/openshell/siq-analysis"
    assert backend.sandbox is None
    assert not (state / "active-run.json").exists()


def test_start_fails_before_transaction_when_broker_preflight_is_no_go(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.broker_preflight_ok = False

    with pytest.raises(LifecycleError, match="broker_preflight_no_go"):
        _start(adapter)

    state = project / "var/openshell/siq-analysis"
    assert backend.sandbox is None
    assert not (state / "active-run.json").exists()
    assert not (state / "transactions/tx-task-001.json").exists()


def test_start_rejects_permissive_brokers_before_transaction(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.broker_identity_required = False

    with pytest.raises(LifecycleError, match="broker_preflight_invalid"):
        _start(adapter)

    state = project / "var/openshell/siq-analysis"
    assert backend.sandbox is None
    assert not (state / "active-run.json").exists()
    assert not (state / "transactions/tx-task-001.json").exists()
    assert not (state / "runs/task-001").exists()
    assert not any(kind == "spawn" for kind, _ in backend.events)
    assert not any(value == "snapshot_siq_analysis_runtime.py" for kind, value in backend.events if kind == "builder")


def test_identity_issuance_failure_rolls_back_partial_run_secrets(lifecycle) -> None:
    project, backend, adapter = lifecycle
    (project / lifecycle_module.BROKER_IDENTITY_KEY_RELATIVE).unlink()

    with pytest.raises(LifecycleError, match="broker_identity_issue_failed"):
        _start(adapter)

    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    assert backend.sandbox is None
    assert all(
        not (run_dir / name).exists()
        for name in ("api.key", "run.nonce", *lifecycle_module.BROKER_IDENTITY_SECRET_FILES)
    )


def test_start_fails_closed_when_database_security_proof_is_incomplete(lifecycle) -> None:
    project, backend, adapter = lifecycle
    (project / lifecycle_module.MILVUS_PROOF_RELATIVE).unlink()

    with pytest.raises(LifecycleError, match="service_security_proof_incomplete"):
        _start(adapter)

    state = project / "var/openshell/siq-analysis"
    assert backend.sandbox is None
    assert not (state / "active-run.json").exists()
    assert not (state / "transactions/tx-task-001.json").exists()
    assert not any(kind == "spawn" for kind, _ in backend.events)


def test_provider_independent_plan_skips_provider_inventory_and_forces_network_deny(lifecycle) -> None:
    project, backend, adapter = lifecycle

    plan, _ = _prepare_security_probe_intent(adapter)

    assert not any(kind == "cli" and value == ["provider", "list", "--names"] for kind, value in backend.events)
    policy = json.loads(plan.policy_path.read_text(encoding="utf-8"))
    assert policy["network_policies"] == {}
    mounts = json.loads(plan.mount_plan.read_text(encoding="utf-8"))["docker"]["mounts"]
    assert len(mounts) == BUSINESS_MOUNT_COUNT
    assert plan.spec.run_dir == project / "var/openshell/siq-analysis/security-probes/probe-123456789abc"
    assert not (project / "var/openshell/siq-analysis/runs/probe-123456789abc").exists()
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()


def test_milvus_boundary_probe_retains_only_data_broker_and_uses_short_lived_identity(
    lifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, backend, adapter = lifecycle
    spec = adapter.security_probe_spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        probe_id="probe-123456789abc",
    )

    def prepare_runtime(_spec, *, expected_runtime_config_sha256: str):
        assert expected_runtime_config_sha256 == CONFIG_SHA256
        snapshot = project / "var/openshell/siq-analysis/runtime-snapshots/probe-123456789abc"
        snapshot.mkdir(parents=True)
        mount = spec.run_dir / "mount.json"
        _write_json(mount, {"docker": {"mounts": []}})
        policy_path = spec.run_dir / "task-policy.yaml"
        _write_json(
            policy_path,
            {
                "network_policies": {
                    "siq_data_broker": {
                        "endpoints": [_bridge_endpoint(18793)],
                        "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                    },
                    "siq_egress_guard": {
                        "endpoints": [_bridge_endpoint(18792)],
                        "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                    },
                    "siq_internal_services": {
                        "endpoints": [
                            _bridge_endpoint(port)
                            for port in (8004, 8006, 8007, 8013)
                        ],
                        "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
                    },
                }
            },
        )
        mount_sha = hashlib.sha256(mount.read_bytes()).hexdigest()
        return snapshot, mount, mount_sha, policy_path, hashlib.sha256(policy_path.read_bytes()).hexdigest()

    monkeypatch.setattr(adapter, "_prepare_runtime", prepare_runtime)
    plan = adapter.prepare_security_probe_runtime(spec, network_mode="data-broker-only")
    policy = json.loads(plan.policy_path.read_text(encoding="utf-8"))
    assert set(policy["network_policies"]) == {"siq_data_broker"}
    assert policy["network_policies"]["siq_data_broker"]["endpoints"] == [_bridge_endpoint(18793)]

    nonce = "1" * 48
    _write(spec.run_dir / "run.nonce", f"{nonce}\n".encode())
    _write_json(
        spec.run_dir / "probe.json",
        {
            "schema_version": SECURITY_PROBE_SCHEMA_VERSION,
            "phase": "prepared",
            "probe_id": spec.run_id,
            "sandbox_name": spec.sandbox_name,
            "network_mode": "data-broker-only",
            "run_nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
            "mount_plan_sha256": plan.mount_plan_sha256,
            "policy_sha256": plan.policy_sha256,
        },
    )
    key = lifecycle_module.broker_request_identity.read_key_file(
        project / lifecycle_module.BROKER_IDENTITY_KEY_RELATIVE
    )
    token = lifecycle_module.broker_request_identity.issue_broker_identities(
        key,
        profile="siq_analysis",
        run_id=spec.run_id,
        sandbox_id=spec.sandbox_name,
        session_id=spec.run_id,
        policy_digest=plan.policy_sha256,
        run_nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),
        ttl_seconds=900,
    ).data_token

    identity = adapter.create_security_probe_sandbox(
        probe_id=spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
        network_mode="data-broker-only",
        data_identity_token=token,
    )

    create = next(value for kind, value in backend.events if kind == "sandbox_create")
    assert "--provider" not in create
    assert any(value.startswith("SIQ_OPENSHELL_DATA_IDENTITY_TOKEN=v1.") for value in create)
    assert not any(value.startswith("SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN=") for value in create)
    adapter.delete_security_probe_sandbox(
        probe_id=spec.run_id,
        nonce=nonce,
        expected_sandbox_id=identity.sandbox_id,
        expected_container_id=identity.container_id,
    )


def test_provider_independent_create_requires_persisted_intent_and_has_no_provider_or_hermes(lifecycle) -> None:
    project, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)

    identity = adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )

    create = next(value for kind, value in backend.events if kind == "sandbox_create")
    assert "--driver-config-json" in create
    assert "--provider" not in create
    assert "--env" not in create
    assert "--no-auto-providers" in create
    assert "/opt/siq/entrypoint.sh" not in " ".join(create)
    assert "/bin/sleep 300" in " ".join(create)
    assert f"ai.siq.lifecycle={SECURITY_PROBE_LIFECYCLE_LABEL}" in create
    assert json.loads((plan.spec.run_dir / "probe.json").read_text(encoding="utf-8"))["phase"] == "prepared"
    assert identity.sandbox_id == SANDBOX_ID
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()

    adapter.delete_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        expected_sandbox_id=identity.sandbox_id,
        expected_container_id=identity.container_id,
    )
    assert backend.sandbox is None


def test_provider_independent_create_fails_closed_without_intent(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    (plan.spec.run_dir / "probe.json").unlink()

    with pytest.raises(LifecycleError, match="required_state_missing"):
        adapter.create_security_probe_sandbox(
            probe_id=plan.spec.run_id,
            nonce=nonce,
            image_ref=plan.image_ref,
            mount_plan=plan.mount_plan,
            policy_path=plan.policy_path,
        )


def test_security_probe_delete_waits_for_async_container_removal(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    identity = adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )
    backend.container_linger_checks = 2

    adapter.delete_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        expected_sandbox_id=identity.sandbox_id,
        expected_container_id=identity.container_id,
    )

    docker_ps_checks = [
        event for event in backend.events if event == ("docker", ["ps", "-aq"])
    ]
    assert len(docker_ps_checks) >= 3

    assert backend.sandbox is None


def test_provider_independent_mutation_requires_maintenance_lock(lifecycle) -> None:
    _, backend, adapter = lifecycle
    backend.maintenance_lock = False
    spec = adapter.security_probe_spec(
        profile="siq_analysis",
        market="cn",
        company="600104-上汽集团",
        probe_id="probe-123456789abc",
    )

    with pytest.raises(LifecycleError, match="security_probe_maintenance_lock_required"):
        adapter.prepare_security_probe_runtime(spec)

    assert backend.sandbox is None


def test_formal_lifecycle_mutations_require_maintenance_lock(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.maintenance_lock = False

    with pytest.raises(LifecycleError, match="formal_maintenance_lock_required"):
        _start(adapter)

    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()
    assert backend.sandbox is None
    backend.maintenance_lock = True
    _start(adapter)
    active = project / "var/openshell/siq-analysis/active-run.json"
    active_before = active.read_bytes()
    backend.maintenance_lock = False
    before = len(backend.events)

    for operation in (
        lambda: adapter.stop(profile="siq_analysis", run_id="task-001"),
        lambda: adapter.rollback_to_host(profile="siq_analysis", run_id="task-001"),
        lambda: adapter.recover(profile="siq_analysis", run_id="task-001"),
    ):
        with pytest.raises(LifecycleError, match="formal_maintenance_lock_required"):
            operation()

    assert active.read_bytes() == active_before
    assert backend.sandbox is not None
    assert not any(kind in {"terminate", "sandbox_delete"} for kind, _ in backend.events[before:])
    assert adapter.status(profile="siq_analysis", run_id="task-001")["status"] == "running"


def test_provider_independent_recovery_removes_verified_docker_orphan(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    identity = adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )
    backend.orphan = backend.sandbox
    backend.sandbox = None

    adapter.recover_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        expected_sandbox_id=identity.sandbox_id,
        expected_container_id=identity.container_id,
    )

    assert backend.orphan is None
    assert any(kind == "docker" and value == ["rm", "-f"] for kind, value in backend.events)


def test_provider_independent_delete_requires_maintenance_lock(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    identity = adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )
    backend.maintenance_lock = False

    with pytest.raises(LifecycleError, match="security_probe_maintenance_lock_required"):
        adapter.delete_security_probe_sandbox(
            probe_id=plan.spec.run_id,
            nonce=nonce,
            expected_sandbox_id=identity.sandbox_id,
            expected_container_id=identity.container_id,
        )

    assert backend.sandbox is not None


def test_provider_independent_recovery_rejects_orphan_with_wrong_sandbox_id_label(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    identity = adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )
    backend.orphan = backend.sandbox
    backend.sandbox = None
    original_run = backend.run

    def wrong_sandbox_id(argv, *, secrets_to_redact=()):
        result = original_run(argv, secrets_to_redact=secrets_to_redact)
        if len(argv) > 1 and Path(argv[0]).name == "docker" and argv[1] == "inspect":
            labels = json.loads(result.stdout)
            labels["openshell.ai/sandbox-id"] = "22222222-2222-2222-2222-222222222222"
            return CommandResult(0, json.dumps(labels), "")
        return result

    backend.run = wrong_sandbox_id

    with pytest.raises(LifecycleError, match="security_probe_orphan_identity_failed"):
        adapter.recover_security_probe_sandbox(
            probe_id=plan.spec.run_id,
            nonce=nonce,
            expected_sandbox_id=identity.sandbox_id,
            expected_container_id=identity.container_id,
        )

    assert backend.orphan is not None


def test_provider_independent_recovery_refuses_unreceipted_docker_orphan(lifecycle) -> None:
    _, backend, adapter = lifecycle
    plan, nonce = _prepare_security_probe_intent(adapter)
    adapter.create_security_probe_sandbox(
        probe_id=plan.spec.run_id,
        nonce=nonce,
        image_ref=plan.image_ref,
        mount_plan=plan.mount_plan,
        policy_path=plan.policy_path,
    )
    backend.orphan = backend.sandbox
    backend.sandbox = None

    with pytest.raises(LifecycleError, match="security_probe_orphan_identity_failed"):
        adapter.recover_security_probe_sandbox(
            probe_id=plan.spec.run_id,
            nonce=nonce,
        )

    assert backend.orphan is not None


def test_stale_smoke_fails_before_snapshot_or_sandbox(lifecycle) -> None:
    project, backend, adapter = lifecycle
    smoke_script = project / "scripts/openshell/smoke_siq_analysis_image.sh"
    smoke_script.write_text("changed after smoke\n", encoding="utf-8")

    with pytest.raises(LifecycleError, match="candidate_image_smoke_missing_or_stale"):
        _start(adapter)

    assert backend.sandbox is None
    assert not any(item == ("builder", "snapshot_siq_analysis_runtime.py") for item in backend.events)


def test_tampered_runtime_lifecycle_evidence_fails_before_snapshot_or_sandbox(lifecycle) -> None:
    project, backend, adapter = lifecycle
    smoke_path = project / "var/openshell/siq-analysis/current-image.smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke["runtime_lifecycle"]["rounds"][1]["generation"] = 1
    _write_json(smoke_path, smoke)

    with pytest.raises(LifecycleError, match="candidate_image_smoke_missing_or_stale"):
        _start(adapter)

    assert backend.sandbox is None
    assert not any(item == ("builder", "snapshot_siq_analysis_runtime.py") for item in backend.events)


def test_policy_runtime_file_blocker_is_explicit_and_retains_snapshot(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.policy_blocked = True

    with pytest.raises(LifecycleError, match="policy_runtime_file_validation_blocked"):
        _start(adapter)

    assert backend.sandbox is None
    assert not any(item == ("spawn", "guard") for item in backend.events)
    assert (project / "var/openshell/siq-analysis/runtime-snapshots/task-001").is_dir()
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    assert not (run_dir / "api.key").exists()
    assert not (run_dir / "run.nonce").exists()
    assert all(not (run_dir / name).exists() for name in lifecycle_module.BROKER_IDENTITY_SECRET_FILES)


def test_create_failure_rolls_back_verified_sandbox_guard_and_secrets(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.create_failure = True

    with pytest.raises(LifecycleError, match="sandbox_create_failed"):
        _start(adapter)

    assert backend.sandbox is None
    assert not backend.processes
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    assert not (run_dir / "api.key").exists()
    assert not (run_dir / "run.nonce").exists()
    assert all(not (run_dir / name).exists() for name in lifecycle_module.BROKER_IDENTITY_SECRET_FILES)
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["phase"] == "failed_rolled_back"
    journal = json.loads(
        (project / "var/openshell/siq-analysis/transactions/tx-task-001.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "rolled_back"
    assert journal["terminal_action"] == "failed_start"
    assert journal["resources"]["run_dir"]["state"] == "present"
    assert all(item["state"] == "removed" for name, item in journal["resources"].items() if name != "run_dir")
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()
    assert (project / "var/openshell/siq-analysis/runtime-snapshots/task-001").is_dir()
    assert (project / "var/openshell/siq-analysis/deletion-snapshots/task-001").is_dir()


def test_stop_cross_checks_pid_before_any_signal_or_sandbox_delete(lifecycle) -> None:
    _, backend, adapter = lifecycle
    _start(adapter)
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    backend.processes[guard_pid] = replace(backend.processes[guard_pid], start_ticks=999999)
    before = len(backend.events)

    with pytest.raises(LifecycleError, match="guard_pid_identity_mismatch"):
        adapter.stop(profile="siq_analysis", run_id="task-001")

    new_events = backend.events[before:]
    assert not any(kind in {"terminate", "sandbox_delete"} for kind, _ in new_events)
    assert backend.sandbox is not None


def test_stop_fences_sandbox_then_stops_guard_and_preserves_snapshots(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    before = len(backend.events)

    result = adapter.stop(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "stopped"
    assert result["publisher"] == {"status": "published"}
    new_events = backend.events[before:]
    forward_index = new_events.index(("terminate", "forward"))
    delete_index = next(index for index, item in enumerate(new_events) if item[0] == "sandbox_delete")
    guard_index = new_events.index(("terminate", "guard"))
    assert forward_index < delete_index < guard_index
    assert backend.sandbox is None
    assert not backend.processes
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    assert not (run_dir / "api.key").exists()
    assert not (run_dir / "run.nonce").exists()
    assert all(not (run_dir / name).exists() for name in lifecycle_module.BROKER_IDENTITY_SECRET_FILES)
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()
    assert (run_dir / "guard.process.json").is_file()
    assert (run_dir / "forward.process.json").is_file()
    assert (run_dir / "guard.ready.json").is_file()
    journal = json.loads(
        (project / "var/openshell/siq-analysis/transactions/tx-task-001.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "stopped"
    assert journal["resources"]["run_dir"]["state"] == "present"
    assert all(item["state"] == "removed" for name, item in journal["resources"].items() if name != "run_dir")
    assert (project / "var/openshell/siq-analysis/runtime-snapshots/task-001").is_dir()
    assert (project / "var/openshell/siq-analysis/deletion-snapshots/task-001").is_dir()


def test_company_index_publish_failure_is_audit_only_and_does_not_fail_stop(lifecycle) -> None:
    project, backend, adapter = lifecycle
    backend.publisher_ok = False

    _start(adapter)
    result = adapter.stop(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "stopped"
    assert result["publisher"] == {"status": "deferred", "error_code": "company_index_publish_failed"}
    audit_files = list((project / "var/openshell/audit").glob("*.jsonl"))
    assert audit_files
    audit_text = "\n".join(path.read_text(encoding="utf-8") for path in audit_files)
    assert '"operation_class":"publisher.index"' in audit_text
    assert '"decision":"audit_only"' in audit_text


def test_company_index_publish_timeout_is_bounded_and_does_not_leave_stopping_transaction(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)

    def timed_out(_arguments, *, timeout_seconds):
        assert timeout_seconds == lifecycle_module.PUBLISHER_TIMEOUT_SECONDS
        return CommandResult(124, "", "")

    backend.run_bounded = timed_out  # type: ignore[attr-defined]
    result = adapter.stop(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "stopped"
    assert result["publisher"] == {
        "status": "deferred",
        "error_code": "company_index_publish_timeout",
    }
    journal = lifecycle_module.transaction.load(project, "tx-task-001")
    assert journal["phase"] == "stopped"
    assert not backend.processes


def test_publisher_audit_failure_is_explicitly_deferred(lifecycle, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, adapter = lifecycle
    _start(adapter)

    def fail_audit(**_kwargs):
        raise OSError("fixture")

    monkeypatch.setattr(adapter, "_audit_publisher", fail_audit)
    result = adapter.stop(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "stopped"
    assert result["publisher"] == {
        "status": "published",
        "audit": "deferred",
        "error_code": "publisher_audit_deferred",
    }


def test_recover_resumes_after_process_exit_before_removed_receipt(
    lifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    real_update = lifecycle_module.transaction.update_resource
    injected = False

    def fail_forward_removed(project_root, transaction_id, **kwargs):
        nonlocal injected
        if kwargs.get("resource") == "forward" and kwargs.get("state") == "removed" and not injected:
            injected = True
            raise lifecycle_module.transaction.TransactionError("injected_removed_commit_failure")
        return real_update(project_root, transaction_id, **kwargs)

    monkeypatch.setattr(lifecycle_module.transaction, "update_resource", fail_forward_removed)
    with pytest.raises(LifecycleError, match="injected_removed_commit_failure"):
        adapter.stop(profile="siq_analysis", run_id="task-001")

    journal_path = project / "var/openshell/siq-analysis/transactions/tx-task-001.json"
    interrupted = json.loads(journal_path.read_text(encoding="utf-8"))
    assert interrupted["phase"] == "stopping"
    assert interrupted["resources"]["forward"]["state"] == "removing"
    assert (project / "var/openshell/siq-analysis/runs/task-001/forward.process.json").is_file()
    assert not any(record.role == "forward" for record in backend.processes.values())

    monkeypatch.setattr(lifecycle_module.transaction, "update_resource", real_update)
    result = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "stopped"
    assert result["recovered"] is True
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "stopped"
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()


def test_recover_claims_running_orphan_without_destructive_action(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    active = project / "var/openshell/siq-analysis/active-run.json"
    active.unlink()
    before = len(backend.events)

    result = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert result["ok"] is False
    assert result["status"] == "running_recovery_required"
    assert active.is_file()
    assert not any(kind in {"terminate", "sandbox_delete"} for kind, _ in backend.events[before:])


def test_recover_finalizes_terminal_journal_after_finalize_interruption(
    lifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _, adapter = lifecycle
    _start(adapter)
    real_finalize = lifecycle_module.transaction.finalize
    injected = False

    def fail_once(project_root, transaction_id):
        nonlocal injected
        if not injected:
            injected = True
            raise lifecycle_module.transaction.TransactionError("injected_finalize_failure")
        return real_finalize(project_root, transaction_id)

    monkeypatch.setattr(lifecycle_module.transaction, "finalize", fail_once)
    with pytest.raises(LifecycleError, match="injected_finalize_failure"):
        adapter.stop(profile="siq_analysis", run_id="task-001")
    assert (project / "var/openshell/siq-analysis/active-run.json").is_file()

    monkeypatch.setattr(lifecycle_module.transaction, "finalize", real_finalize)
    result = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "finalized"
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()


def test_recover_rolls_back_starting_transaction_without_guessing_resources(
    lifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, backend, adapter = lifecycle
    backend.policy_blocked = True
    real_rollback = adapter._rollback_failed_start
    monkeypatch.setattr(adapter, "_rollback_failed_start", lambda *args, **kwargs: "simulated_crash")

    with pytest.raises(LifecycleError, match="simulated_crash"):
        _start(adapter)
    journal_path = project / "var/openshell/siq-analysis/transactions/tx-task-001.json"
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "starting"

    monkeypatch.setattr(adapter, "_rollback_failed_start", real_rollback)
    result = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert result["status"] == "rolled_back"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "rolled_back"
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()


def test_system_backend_terminate_rechecks_identity_after_pidfd_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
    backend = SystemBackend(project_root=tmp_path)
    record = ProcessRecord(
        schema_version=PROCESS_SCHEMA_VERSION,
        role="guard",
        pid=4242,
        start_ticks=100,
        executable="/fixture/python",
        argv_sha256="a" * 64,
    )
    replacement = replace(record, start_ticks=101)
    snapshots = iter((record, replacement))
    monkeypatch.setattr(backend, "process_snapshot", lambda pid, role: next(snapshots))
    descriptor = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(lifecycle_module, "_pidfd_open", lambda pid: descriptor)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        lifecycle_module,
        "_pidfd_send_signal",
        lambda pidfd, signum: signals.append((pidfd, signum)),
    )

    with pytest.raises(LifecycleError, match="guard_pid_identity_mismatch"):
        backend.terminate(record)

    assert signals == []


def test_system_backend_terminate_signals_only_through_pidfd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
    backend = SystemBackend(project_root=tmp_path)
    record = ProcessRecord(
        schema_version=PROCESS_SCHEMA_VERSION,
        role="forward",
        pid=4343,
        start_ticks=200,
        executable="/fixture/openshell",
        argv_sha256="b" * 64,
    )
    snapshots = iter((record, record, None))
    monkeypatch.setattr(backend, "process_snapshot", lambda pid, role: next(snapshots))
    descriptor = os.open("/dev/null", os.O_RDONLY)
    opened: list[int] = []
    signals: list[tuple[int, int]] = []

    def open_pidfd(pid: int) -> int:
        opened.append(pid)
        return descriptor

    monkeypatch.setattr(lifecycle_module, "_pidfd_open", open_pidfd)
    monkeypatch.setattr(
        lifecycle_module,
        "_pidfd_send_signal",
        lambda pidfd, signum: signals.append((pidfd, signum)),
    )

    backend.terminate(record)

    assert opened == [record.pid]
    assert signals == [(descriptor, lifecycle_module.signal.SIGTERM)]


def test_system_backend_terminate_fails_closed_without_pidfd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
    backend = SystemBackend(project_root=tmp_path)
    record = ProcessRecord(
        schema_version=PROCESS_SCHEMA_VERSION,
        role="guard",
        pid=4444,
        start_ticks=300,
        executable="/fixture/python",
        argv_sha256="c" * 64,
    )
    monkeypatch.setattr(backend, "process_snapshot", lambda pid, role: record)

    def fail_pidfd(pid: int) -> int:
        raise lifecycle_module.GatewayRuntimeError("pidfd unavailable")

    monkeypatch.setattr(lifecycle_module, "_pidfd_open", fail_pidfd)
    with pytest.raises(LifecycleError, match="guard_pidfd_open_failed"):
        backend.terminate(record)


def test_system_backend_host_receipt_cross_checks_state_process_environment_and_listener(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
    profile_root = tmp_path / "data/hermes/home/profiles/siq_analysis"
    profile_root.mkdir(parents=True)
    expected_argv = [
        str(Path(lifecycle_module.pwd.getpwuid(os.geteuid()).pw_dir) / ".local/bin/hermes"),
        "gateway",
        "run",
        "--replace",
        "--accept-hooks",
    ]
    _write_json(
        profile_root / "gateway_state.json",
        {
            "pid": 4545,
            "kind": "hermes-gateway",
            "argv": expected_argv,
            "start_time": 400,
            "gateway_state": "running",
        },
    )
    record = ProcessRecord(
        schema_version=PROCESS_SCHEMA_VERSION,
        role="host_hermes",
        pid=4545,
        start_ticks=400,
        executable="/fixture/python",
        argv_sha256="d" * 64,
    )
    monkeypatch.setattr(lifecycle_module, "_process_snapshot", lambda pid, role: record)
    monkeypatch.setattr(lifecycle_module, "_proc_argv", lambda pid: ["/fixture/python", *expected_argv])
    monkeypatch.setattr(
        lifecycle_module,
        "_proc_environment",
        lambda pid: {
            b"HERMES_HOME": os.fsencode(profile_root),
            b"API_SERVER_MODEL_NAME": b"siq_analysis",
        },
    )
    monkeypatch.setattr(lifecycle_module, "_loopback_listener_inodes", lambda port: {9001})
    monkeypatch.setattr(lifecycle_module, "_process_socket_inodes", lambda pid: {9001, 9002})
    backend = SystemBackend(project_root=tmp_path)

    receipt = backend.host_hermes_receipt(
        profile="siq_analysis",
        host="127.0.0.1",
        port=18651,
    )

    assert receipt.pid == record.pid
    assert receipt.start_ticks == record.start_ticks
    assert receipt.argv_sha256 == record.argv_sha256
    assert len(receipt.state_identity_sha256) == 64

    monkeypatch.setattr(lifecycle_module, "_process_socket_inodes", lambda pid: {9002})
    with pytest.raises(LifecycleError, match="host_hermes_listener_identity_mismatch"):
        backend.host_hermes_receipt(
            profile="siq_analysis",
            host="127.0.0.1",
            port=18651,
        )


def test_rollback_requires_healthy_host_without_touching_it(lifecycle) -> None:
    _, backend, adapter = lifecycle
    _start(adapter)
    backend.host_health = False

    with pytest.raises(LifecycleError, match="host_hermes_not_healthy"):
        adapter.rollback_to_host(profile="siq_analysis", run_id="task-001")
    assert backend.sandbox is not None

    backend.host_health = True
    result = adapter.rollback_to_host(profile="siq_analysis", run_id="task-001")
    assert result["runtime"] == "host"
    assert result["host_runtime_unchanged"] is True
    assert result["host_runs_url"] == "http://127.0.0.1:18651/v1/runs"
    assert len(result["host_receipt_sha256"]) == 64
    assert [value for kind, value in backend.events if kind == "host_health"][-2:] == [True, True]
    assert not any(kind == "cli" and value and value[0] == "host" for kind, value in backend.events)


def test_rollback_refuses_host_identity_drift_before_stop(lifecycle) -> None:
    _, backend, adapter = lifecycle
    _start(adapter)
    changed = replace(backend.host_receipt, start_ticks=backend.host_receipt.start_ticks + 1)
    backend.host_receipt_sequence = [backend.host_receipt, changed]

    with pytest.raises(LifecycleError, match="host_hermes_identity_changed_before_stop"):
        adapter.rollback_to_host(profile="siq_analysis", run_id="task-001")

    assert backend.sandbox is not None
    assert not any(kind == "terminate" for kind, _ in backend.events)


def test_recover_rechecks_host_identity_before_resuming_rollback_cleanup(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    transaction_id = "tx-task-001"
    record = lifecycle_module.transaction.load(project, transaction_id)
    record = lifecycle_module.transaction.set_terminal_action(
        project,
        transaction_id,
        expected_generation=record["generation"],
        action="rollback_to_host",
    )
    lifecycle_module.transaction.transition(
        project,
        transaction_id,
        expected_generation=record["generation"],
        phase="stopping",
    )
    backend.host_receipt = replace(
        backend.host_receipt,
        start_ticks=backend.host_receipt.start_ticks + 1,
    )
    before = len(backend.events)

    with pytest.raises(LifecycleError, match="host_hermes_identity_changed_before_stop"):
        adapter.recover(profile="siq_analysis", run_id="task-001")

    new_events = backend.events[before:]
    assert backend.sandbox is not None
    assert backend.processes
    assert not any(kind in {"terminate", "sandbox_delete"} for kind, _ in new_events)
    assert (project / "var/openshell/siq-analysis/active-run.json").is_file()


def test_rollback_detects_host_identity_drift_after_stop(lifecycle) -> None:
    _, backend, adapter = lifecycle
    _start(adapter)
    changed = replace(backend.host_receipt, start_ticks=backend.host_receipt.start_ticks + 1)
    backend.host_receipt_sequence = [backend.host_receipt, backend.host_receipt, changed]

    with pytest.raises(LifecycleError, match="host_hermes_identity_changed_after_stop"):
        adapter.rollback_to_host(profile="siq_analysis", run_id="task-001")

    assert backend.sandbox is None
    assert not backend.processes


def test_guard_terminator_uses_same_nonce_and_docker_identity(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    terminator = VerifiedSandboxTerminator(
        adapter=adapter,
        run_id="task-001",
        sandbox_name="siq-analysis-task-001",
        run_dir=run_dir,
    )

    terminator.terminate(sandbox_id="siq-analysis-task-001", reason_code="deletion_ratio_threshold")

    assert backend.sandbox is None
    assert any(kind == "sandbox_delete" for kind, _ in backend.events)


def test_guard_trigger_fences_run_and_recover_finalizes_transaction(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    _write_json(
        run_dir / lifecycle_module.GUARD_TRIGGER_NAME,
        {
            "schema_version": lifecycle_module.GUARD_EVENT_SCHEMA,
            "status": "trigger_requested",
            "profile": "siq_analysis",
            "run_id": "task-001",
            "pid": guard_pid,
            "reason_code": "deletion_ratio_threshold",
        },
    )

    result = adapter.handle_guard_trigger(
        profile="siq_analysis",
        run_id="task-001",
        reason_code="deletion_ratio_threshold",
    )

    assert result["status"] == "cleanup_pending_guard_exit"
    assert backend.sandbox is None
    assert all(record.role == "guard" for record in backend.processes.values())
    assert not (run_dir / "api.key").exists()
    assert not (run_dir / "run.nonce").exists()
    assert all(not (run_dir / name).exists() for name in lifecycle_module.BROKER_IDENTITY_SECRET_FILES)
    journal = lifecycle_module.transaction.load(project, "tx-task-001")
    assert journal["phase"] == "stopping"
    assert journal["terminal_action"] == "stop"
    assert journal["resources"]["guard"]["state"] == "removing"

    adapter.finalize_guard_worker_exit(profile="siq_analysis", run_id="task-001", pid=guard_pid)
    backend.processes.pop(guard_pid)
    recovered = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert recovered["ok"] is True
    assert recovered["guard_triggered"] is True
    assert not backend.processes
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()
    final_journal = lifecycle_module.transaction.load(project, "tx-task-001")
    assert final_journal["phase"] == "stopped"
    assert all(item["state"] == "removed" for name, item in final_journal["resources"].items() if name != "run_dir")


def test_recover_uses_durable_guard_trigger_after_worker_crash(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    _write_json(
        run_dir / lifecycle_module.GUARD_TRIGGER_NAME,
        {
            "schema_version": lifecycle_module.GUARD_EVENT_SCHEMA,
            "status": "trigger_requested",
            "profile": "siq_analysis",
            "run_id": "task-001",
            "pid": guard_pid,
            "reason_code": "inotify_monitor_failure",
        },
    )
    # Simulate the worker dying before it could fence anything.  Recovery must
    # take the normal identity-checked cleanup path, not return a vague
    # running_recovery_required status.
    recovered = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert recovered["ok"] is True
    assert recovered["guard_triggered"] is True
    assert backend.sandbox is None
    assert not backend.processes


def test_recover_restores_snapshot_when_guard_dies_before_trigger_event(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    analysis_file = project / "data/wiki/companies/600104-上汽集团/analysis/baseline.md"
    analysis_file.unlink()
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    backend.processes.pop(guard_pid)

    recovered = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert recovered["ok"] is True
    assert recovered["guard_triggered"] is False
    assert recovered["guard_failure"] is True
    assert analysis_file.read_text(encoding="utf-8") == "baseline\n"
    assert backend.sandbox is None
    assert not backend.processes


def test_recover_restores_snapshot_when_guard_event_is_malformed_after_crash(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    analysis_file = project / "data/wiki/companies/600104-上汽集团/analysis/baseline.md"
    analysis_file.unlink()
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    _write_json(run_dir / lifecycle_module.GUARD_OUTCOME_NAME, {"status": "tampered"})
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    backend.processes.pop(guard_pid)

    recovered = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert recovered["ok"] is True
    assert recovered["guard_failure"] is True
    assert analysis_file.read_text(encoding="utf-8") == "baseline\n"
    assert backend.sandbox is None
    assert not backend.processes


def test_operator_stop_honors_a_concurrent_durable_guard_trigger(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    analysis_file = project / "data/wiki/companies/600104-上汽集团/analysis/baseline.md"
    analysis_file.unlink()
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    _write_json(
        run_dir / lifecycle_module.GUARD_TRIGGER_NAME,
        {
            "schema_version": lifecycle_module.GUARD_EVENT_SCHEMA,
            "status": "trigger_requested",
            "profile": "siq_analysis",
            "run_id": "task-001",
            "pid": guard_pid,
            "reason_code": "deletion_ratio_threshold",
            "deleted_paths": ["baseline.md"],
        },
    )

    stopped = adapter.stop(profile="siq_analysis", run_id="task-001")

    assert stopped["status"] == "stopped"
    assert analysis_file.read_text(encoding="utf-8") == "baseline\n"
    assert backend.sandbox is None
    assert not backend.processes


def test_recover_preserves_rollback_action_when_guard_triggers_concurrently(lifecycle) -> None:
    project, backend, adapter = lifecycle
    _start(adapter)
    analysis_file = project / "data/wiki/companies/600104-上汽集团/analysis/baseline.md"
    analysis_file.unlink()
    run_dir = project / "var/openshell/siq-analysis/runs/task-001"
    guard_pid = next(pid for pid, record in backend.processes.items() if record.role == "guard")
    _write_json(
        run_dir / lifecycle_module.GUARD_TRIGGER_NAME,
        {
            "schema_version": lifecycle_module.GUARD_EVENT_SCHEMA,
            "status": "trigger_requested",
            "profile": "siq_analysis",
            "run_id": "task-001",
            "pid": guard_pid,
            "reason_code": "deletion_ratio_threshold",
            "deleted_paths": ["baseline.md"],
        },
    )
    record = lifecycle_module.transaction.load(project, "tx-task-001")
    record = lifecycle_module.transaction.set_terminal_action(
        project,
        "tx-task-001",
        expected_generation=record["generation"],
        action="rollback_to_host",
    )
    lifecycle_module.transaction.transition(
        project,
        "tx-task-001",
        expected_generation=record["generation"],
        phase="stopping",
    )

    recovered = adapter.recover(profile="siq_analysis", run_id="task-001")

    assert recovered["runtime"] == "host"
    assert recovered["guard_triggered"] is True
    assert recovered["guard_failure"] is False
    assert analysis_file.read_text(encoding="utf-8") == "baseline\n"
    assert backend.sandbox is None
    assert not backend.processes


def test_repair_is_an_identity_bound_recovery_alias(lifecycle) -> None:
    _project, _backend, adapter = lifecycle

    result = adapter.repair(profile="siq_analysis")

    assert result == {
        "ok": True,
        "profile": "siq_analysis",
        "status": "nothing_to_recover",
        "operation": "repair",
    }


def test_shell_wrappers_are_fixed_and_do_not_touch_default_runtime() -> None:
    wrappers = {
        "run_hermes_gateway.sh": "start",
        "stop_hermes_gateway.sh": "stop",
        "status_hermes_gateway.sh": "status",
        "rollback_to_host.sh": "rollback",
        "recover_hermes_gateway.sh": "recover",
        "repair_hermes_gateway.sh": "repair",
    }
    for name, command in wrappers.items():
        path = ROOT / "scripts/openshell" / name
        content = path.read_text(encoding="utf-8")
        assert path.stat().st_mode & 0o111
        assert 'source "$SCRIPT_DIR/env.sh"' in content
        assert f'siq_analysis_lifecycle.py" {command}' in content
        assert "nemoclaw" not in content
        assert "start_all.sh" not in content
    assert "siq_openshell_acquire_maintenance_lock" not in (
        ROOT / "scripts/openshell/status_hermes_gateway.sh"
    ).read_text(encoding="utf-8")


def test_minimal_child_environment_is_project_pinned_and_excludes_host_injection(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    blocked = {
        "BASH_ENV": "/tmp/host-bash-env",
        "PYTHONPATH": "/tmp/host-pythonpath",
        "PYTHONHOME": "/tmp/host-pythonhome",
        "LD_PRELOAD": "/tmp/host-preload.so",
        "LD_LIBRARY_PATH": "/tmp/host-library-path",
        "HTTP_PROXY": "http://proxy.invalid",
        "HTTPS_PROXY": "http://proxy.invalid",
        "ALL_PROXY": "socks5://proxy.invalid",
        "NO_PROXY": "host-provided",
        "AWS_SECRET_ACCESS_KEY": "host-secret",
        "TAVILY_API_KEY": "host-secret",
        "OPENSHELL_GATEWAY_ENDPOINT": "https://attacker.invalid",
        "DOCKER_CONTEXT": "attacker-context",
    }
    for name, value in blocked.items():
        monkeypatch.setenv(name, value)

    child = _minimal_child_environment(project)

    assert not blocked.keys() & child.keys()
    assert child["PATH"] == CHILD_PATH
    assert child["DOCKER_HOST"] == DOCKER_HOST
    assert child["DOCKER_CONFIG"] == str(project / "var/openshell/docker-cli-config")
    assert child["SIQ_PROJECT_ROOT"] == str(project)
    assert child["SIQ_OPENSHELL_BIN"] == str(project / "var/openshell/toolchains/v0.0.83/bin/openshell")
    assert child["OPENSHELL_GATEWAY"] == "siq-openshell-dev"
    assert child["XDG_STATE_HOME"] == str(project / "var/openshell/xdg/state")


def test_system_backend_uses_minimal_environment_for_run_and_spawn(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    captured: list[dict[str, str]] = []

    def fake_run(*args, **kwargs):
        del args
        assert kwargs["cwd"] == project
        captured.append(dict(kwargs["env"]))
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    class FakeProcess:
        pid = 4101

    def fake_popen(*args, **kwargs):
        del args
        assert kwargs["cwd"] == project
        captured.append(dict(kwargs["env"]))
        return FakeProcess()

    monkeypatch.setattr("scripts.openshell.siq_analysis_lifecycle.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.openshell.siq_analysis_lifecycle.subprocess.Popen", fake_popen)
    monkeypatch.setenv("BASH_ENV", "/tmp/injected")
    monkeypatch.setenv("HOST_SECRET", "must-not-cross")
    backend = SystemBackend(project_root=project)

    backend.run(["/bin/true"])
    backend.spawn(["/bin/true"], log_path=tmp_path / "child.log")

    assert len(captured) == 2
    for environment in captured:
        assert "BASH_ENV" not in environment
        assert "HOST_SECRET" not in environment
        assert environment["DOCKER_HOST"] == DOCKER_HOST
        assert environment["OPENSHELL_GATEWAY"] == "siq-openshell-dev"


def test_system_backend_passes_only_validated_maintenance_descriptor(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    lock_dir = project / "var/openshell/locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / "maintenance.lock"
    lock_path.touch(mode=0o600)
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        del args
        captured.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.openshell.siq_analysis_lifecycle.subprocess.run", fake_run)
    descriptor = os.open(lock_path, os.O_WRONLY)
    try:
        monkeypatch.setenv("SIQ_OPENSHELL_MAINTENANCE_FD", str(descriptor))
        backend = SystemBackend(project_root=project)
        backend.run(["/bin/true"])
    finally:
        os.close(descriptor)

    assert captured["pass_fds"] == (descriptor,)
    assert captured["env"]["SIQ_OPENSHELL_MAINTENANCE_FD"] == str(descriptor)


def test_system_backend_long_lived_spawn_drops_maintenance_descriptor(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    lock_path = project / "var/openshell/locks/maintenance.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4102

    def fake_popen(*args, **kwargs):
        del args
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("scripts.openshell.siq_analysis_lifecycle.subprocess.Popen", fake_popen)
    descriptor = os.open(lock_path, os.O_WRONLY)
    try:
        monkeypatch.setenv("SIQ_OPENSHELL_MAINTENANCE_FD", str(descriptor))
        backend = SystemBackend(project_root=project)
        backend.spawn(["/bin/true"], log_path=tmp_path / "child.log")
    finally:
        os.close(descriptor)

    assert captured["pass_fds"] == ()
    assert "SIQ_OPENSHELL_MAINTENANCE_FD" not in captured["env"]


def test_spawned_child_cannot_keep_launcher_maintenance_lock(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    lock_path = project / "var/openshell/locks/maintenance.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    descriptor = os.open(lock_path, os.O_WRONLY)
    candidate = -1
    pid = -1
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setenv("SIQ_OPENSHELL_MAINTENANCE_FD", str(descriptor))
        backend = SystemBackend(project_root=project)
        pid = backend.spawn(["/bin/sleep", "5"], log_path=tmp_path / "sleep.log")
        os.close(descriptor)
        descriptor = -1
        monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
        candidate = os.open(lock_path, os.O_WRONLY)

        fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.kill(pid, 0)
    finally:
        if candidate >= 0:
            os.close(candidate)
        if descriptor >= 0:
            os.close(descriptor)
        if pid > 1:
            try:
                os.kill(pid, lifecycle_module.signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


def test_system_backend_rejects_maintenance_descriptor_from_another_root(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    foreign_lock = tmp_path / "foreign/locks/maintenance.lock"
    foreign_lock.parent.mkdir(parents=True)
    foreign_lock.touch(mode=0o600)
    descriptor = os.open(foreign_lock, os.O_WRONLY)
    try:
        monkeypatch.setenv("SIQ_OPENSHELL_MAINTENANCE_FD", str(descriptor))
        with pytest.raises(LifecycleError, match="invalid_maintenance_lock"):
            SystemBackend(project_root=project)
    finally:
        os.close(descriptor)


def test_system_backend_maintenance_lock_detects_competing_open_description(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    lock_path = project / "var/openshell/locks/maintenance.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    owner = os.open(lock_path, os.O_WRONLY)
    candidate = os.open(lock_path, os.O_WRONLY)
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setenv("SIQ_OPENSHELL_MAINTENANCE_FD", str(candidate))
        backend = SystemBackend(project_root=project)

        assert backend.maintenance_lock_held() is False
    finally:
        os.close(candidate)
        os.close(owner)


def test_background_backend_acquires_a_fresh_operation_lock(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    lock_path = project / "var/openshell/locks/maintenance.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)
    monkeypatch.delenv("SIQ_OPENSHELL_MAINTENANCE_FD", raising=False)
    backend = SystemBackend(project_root=project)

    backend.acquire_maintenance_lock(timeout_seconds=0.5)

    assert backend.maintenance_lock_held() is True
    assert backend._maintenance_fd is not None
    os.close(backend._maintenance_fd)
    backend._maintenance_fd = None
    # acquire_maintenance_lock writes this variable outside MonkeyPatch. Using
    # monkeypatch.delenv here would make fixture teardown restore the now-closed
    # descriptor into later tests.
    os.environ.pop("SIQ_OPENSHELL_MAINTENANCE_FD", None)


def test_formal_wrappers_clear_environment_before_isolated_python() -> None:
    wrappers = {
        "run_hermes_gateway.sh": True,
        "stop_hermes_gateway.sh": True,
        "status_hermes_gateway.sh": False,
        "rollback_to_host.sh": True,
        "recover_hermes_gateway.sh": True,
        "repair_hermes_gateway.sh": True,
    }
    for name, mutating in wrappers.items():
        content = (ROOT / "scripts/openshell" / name).read_text(encoding="utf-8")
        assert content.startswith("#!/bin/bash -p\n")
        assert 'export PATH="$SAFE_PATH"' in content
        assert "export LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=dumb" in content
        assert "unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH" in content
        assert "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy" in content
        assert "exec /usr/bin/env -i" in content
        assert "DOCKER_HOST=unix:///var/run/docker.sock" in content
        assert "/usr/bin/python3 -I -B" in content
        assert ("SIQ_OPENSHELL_MAINTENANCE_FD=" in content) is mutating


def test_python_entrypoints_use_absolute_isolated_interpreter() -> None:
    for name in ("siq_analysis_lifecycle.py", "probe_siq_analysis_sandbox.py"):
        content = (ROOT / "scripts/openshell" / name).read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/python3 -IB\n")


def test_image_smoke_revokes_old_proof_and_writes_hash_bound_attestation() -> None:
    content = (ROOT / "scripts/openshell/smoke_siq_analysis_image.sh").read_text(encoding="utf-8")
    assert 'rm -f -- "$SMOKE_STATE_FILE"' in content
    assert '"siq.openshell.candidate_image_smoke.v1"' in content
    assert '"candidate_state_sha256"' in content
    assert '"smoke_script_sha256"' in content
    assert 'chmod 0600 -- "$temporary_smoke_state"' in content
    assert 'mv -fT -- "$temporary_smoke_state" "$SMOKE_STATE_FILE"' in content
