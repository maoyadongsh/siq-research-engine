#!/usr/bin/python3 -IB
"""Manage one real-path, NOT_PRODUCTION SIQ analysis OpenShell canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import test_siq_analysis_wide_pilot_contract as pilot_contract  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BUSINESS_MOUNT_COUNT,
    CANARY_LIFECYCLE_LABEL,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    RunSpec,
    _read_json,
    _sha256_file,
    _write_json,
)
from scripts.openshell.siq_analysis_wide_pilot import (  # noqa: E402
    PROBE_RECEIPT_NAME,
    PROVIDERS,
    READINESS_EFFECT,
    NonProductionLifecycleSettings,
    WidePilotError,
    WidePilotLifecycle,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_canary_lifecycle.v1"
MODE = "NOT_PRODUCTION_CANARY"
ACKNOWLEDGEMENT = "--acknowledge-not-production-canary"
STATE_RELATIVE = Path("var/openshell/canary/siq-analysis")
RUNS_RELATIVE = STATE_RELATIVE / "runs"
ACTIVE_RELATIVE = STATE_RELATIVE / "active.json"
RUN_ID_RE = re.compile(r"canary-[0-9a-f]{12}\Z")
MANIFEST_NAME = "canary.json"
ROLLBACK_RECEIPT_NAME = "rollback.sanitized.json"
SANDBOX_PREFIX = "siq-analysis-"
POOL_SLOTS_RELATIVE = Path("var/openshell/canary/siq-analysis/pool/slots")
POOL_SLOT_ID_RE = re.compile(r"[0-9a-f]{24}\Z")
ACTIVE_FIELDS = {
    "schema_version",
    "mode",
    "readiness_effect",
    "profile",
    "run_id",
    "market",
    "company",
    "run_state",
    "manifest",
    "manifest_sha256",
    "api_key_sha256",
}
MANIFEST_FIELDS = {
    "schema_version",
    "mode",
    "readiness_effect",
    "phase",
    "profile",
    "run_id",
    "market",
    "company",
    "analysis_relative_path",
    "writable_relative_path",
    "write_scope",
    "normal_business_mutations",
    "source_sha256",
    "source_stock_code",
    "sandbox_name",
    "lifecycle_label",
    "image_ref",
    "image_id",
    "runtime_snapshot",
    "mount_plan",
    "mount_plan_sha256",
    "mount_count",
    "policy",
    "policy_sha256",
    "providers",
    "formal_blockers_not_overridden",
    "broker_request_identity_required",
    "api_key_sha256",
    "run_nonce_sha256",
    "host_hermes_receipt_sha256",
    "sandbox_id",
    "container_id",
    "guard_process",
    "forward_process",
    "result_is_formal_evidence",
}
CANARY_SETTINGS = NonProductionLifecycleSettings(
    schema_version=SCHEMA_VERSION,
    mode=MODE,
    lifecycle_label=CANARY_LIFECYCLE_LABEL,
    state_relative=STATE_RELATIVE,
    run_id_re=RUN_ID_RE,
    sandbox_prefix=SANDBOX_PREFIX,
    identity_field="run_id",
    error_prefix="canary",
    guard_worker_relative=Path("scripts/openshell/siq_analysis_wide_pilot_guard.py"),
    guard_identity_argument="--run-id",
    sandbox_mode_environment=("SIQ_OPENSHELL_CANARY", "1"),
    entrypoint_log="/tmp/siq-analysis-canary-entrypoint.log",
    manifest_name=MANIFEST_NAME,
)


def _regular_single_link(path: Path, *, code: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WidePilotError(code) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WidePilotError(code)


class CanaryLifecycle(WidePilotLifecycle):
    """Wide-pilot mechanics with a full analysis-root business write scope."""

    def __init__(
        self,
        *,
        project_root: Path = REPO_ROOT,
        adapter: LifecycleAdapter | None = None,
        pool_slot_id: str | None = None,
        local_port: int = 28651,
        reservation_token: str | None = None,
        allow_pool_orphan_stop: bool = False,
    ) -> None:
        if pool_slot_id is None:
            if local_port != 28651 or reservation_token is not None:
                raise WidePilotError("canary_legacy_endpoint_invalid")
            settings = CANARY_SETTINGS
        else:
            if (
                POOL_SLOT_ID_RE.fullmatch(pool_slot_id) is None
                or not 28652 <= local_port <= 28750
            ):
                raise WidePilotError("canary_pool_slot_invalid")
            settings = replace(
                CANARY_SETTINGS,
                state_relative=POOL_SLOTS_RELATIVE / pool_slot_id,
                local_port=local_port,
                target_port=28651,
                pool_managed=True,
                pool_slot_id=pool_slot_id,
            )
        self.pool_slot_id = pool_slot_id
        self.reservation_token = reservation_token
        self.allow_pool_orphan_stop = allow_pool_orphan_stop
        super().__init__(project_root=project_root, adapter=adapter, settings=settings)

    def _validate_start_scope(self, *, market: str, company: str, pilot_id: str) -> None:
        del pilot_id
        if self.pool_slot_id is None:
            return
        expected = hashlib.sha256(f"{market}\0{company}".encode("utf-8")).hexdigest()[:24]
        if expected != self.pool_slot_id or not self.reservation_token:
            raise WidePilotError(self._code("pool_slot_scope_mismatch"))
        try:
            from scripts.openshell import siq_analysis_pool_registry as pool_registry

            pool_registry.validate_port_reservation(
                local_port=self.settings.local_port,
                reservation_token=self.reservation_token,
                project_root=self.project_root,
            )
        except Exception as exc:
            code = getattr(exc, "code", "openshell_pool_port_reservation_invalid")
            raise WidePilotError(self._code(str(code))) from exc

    def _pool_owned_sandboxes(self) -> Mapping[str, Mapping[str, str]]:
        if self.pool_slot_id is None:
            return {}
        try:
            from scripts.openshell import siq_analysis_pool_registry as pool_registry

            registry = pool_registry.load_registry(project_root=self.project_root)
            result: dict[str, Mapping[str, str]] = {}
            for entry in registry["bindings"]:
                pool_registry.resolve(
                    market=str(entry["market"]),
                    company=str(entry["company"]),
                    project_root=self.project_root,
                )
                manifest = _read_json(self.project_root / str(entry["manifest"]), root=self.project_root)
                name = str(entry["sandbox_name"])
                if name in result:
                    raise WidePilotError(self._code("pool_sandbox_inventory_invalid"))
                result[name] = {
                    "run_id": str(entry["run_id"]),
                    "sandbox_id": str(manifest.get("sandbox_id") or ""),
                    "container_id": str(manifest.get("container_id") or ""),
                }
            return result
        except WidePilotError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "openshell_pool_registry_invalid")
            raise WidePilotError(self._code(str(code))) from exc

    def _after_active(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        del manifest
        if self.pool_slot_id is None:
            return
        try:
            from scripts.openshell import siq_analysis_pool_registry as pool_registry

            pool_registry.register_active(
                active=self.settings.active_relative,
                local_port=self.settings.local_port,
                reservation_token=self.reservation_token,
                project_root=self.project_root,
            )
        except Exception as exc:
            code = getattr(exc, "code", "openshell_pool_register_failed")
            raise WidePilotError(self._code(str(code))) from exc

    def _prepare_stop(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        if self.pool_slot_id is None:
            return
        try:
            from scripts.openshell import (
                siq_analysis_pool_concurrency as pool_concurrency,
                siq_analysis_pool_registry as pool_registry,
            )

            registry = pool_registry.load_registry(project_root=self.project_root)
            entry = next(
                (
                    item
                    for item in registry["bindings"]
                    if item["scope_id"] == self.pool_slot_id and item["run_id"] == spec.run_id
                ),
                None,
            )
            if entry is None:
                if manifest.get("phase") == "stopping" or self.allow_pool_orphan_stop:
                    return
                raise WidePilotError(self._code("pool_binding_missing"))
            if manifest.get("phase") == "stopping":
                scheduler = pool_concurrency.status(project_root=self.project_root)
                control = next(
                    (
                        item
                        for item in scheduler["bindings"]
                        if item["scope_id"] == self.pool_slot_id and item["run_id"] == spec.run_id
                    ),
                    None,
                )
                if (
                    control is None
                    or control.get("traffic_state") != "draining"
                    or any(
                        control.get(field) != 0
                        for field in ("active_leases", "orphaned_leases", "waiting_leases")
                    )
                ):
                    raise WidePilotError(self._code("pool_live_leases_require_terminal"))
                return
            drained = pool_concurrency.set_traffic_state(
                market=spec.market,
                company=spec.company,
                run_id=spec.run_id,
                traffic_state="draining",
                project_root=self.project_root,
            )
            if drained.get("drained") is not True:
                raise WidePilotError(self._code("pool_live_leases_require_terminal"))
        except WidePilotError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "openshell_pool_stop_prepare_failed")
            raise WidePilotError(self._code(str(code))) from exc

    def _after_stop_marked(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        if self.pool_slot_id is None:
            return
        try:
            from scripts.openshell import siq_analysis_pool_registry as pool_registry

            registry = pool_registry.load_registry(project_root=self.project_root)
            entry = next(
                (
                    item
                    for item in registry["bindings"]
                    if item["scope_id"] == self.pool_slot_id and item["run_id"] == spec.run_id
                ),
                None,
            )
            if entry is None:
                if manifest.get("phase") == "stopping":
                    return
                raise WidePilotError(self._code("pool_binding_missing"))
            pool_registry.unregister(
                market=spec.market,
                company=spec.company,
                run_id=spec.run_id,
                project_root=self.project_root,
            )
        except WidePilotError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "openshell_pool_unregister_failed")
            raise WidePilotError(self._code(str(code))) from exc

    def _prepare_host_business_root(self, *, market: str, company: str, pilot_id: str) -> None:
        try:
            self.adapter.prepare_analysis_root_for_start(
                profile=PROFILE,
                market=market,
                company=company,
                run_id=pilot_id,
            )
        except LifecycleError as exc:
            raise WidePilotError(self._code(exc.code)) from exc

    def _paths(self, spec: RunSpec) -> pilot_contract.PilotPaths:
        company_root = spec.analysis_root.parent
        source = company_root / "company.json"
        reports = company_root / "reports"
        for path in (company_root, source, reports, spec.analysis_root):
            try:
                path.relative_to(self.project_root)
            except ValueError as exc:
                raise WidePilotError(self._code("path_outside_project")) from exc
            current = self.project_root
            for part in path.relative_to(self.project_root).parts:
                current /= part
                try:
                    mode = current.lstat().st_mode
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(mode):
                    raise WidePilotError(self._code("path_symlinked"))
        if not company_root.is_dir() or not spec.analysis_root.is_dir():
            raise WidePilotError(self._code("company_layout_invalid"))
        _regular_single_link(source, code=self._code("source_invalid"))
        # Reports can be absent for a newly ingested company. Its parent company
        # root remains covered by the read-only wiki mount and policy.
        if reports.exists() and not reports.is_dir():
            raise WidePilotError(self._code("reports_path_invalid"))
        return pilot_contract.PilotPaths(
            company_root=company_root,
            source=source,
            analysis_root=spec.analysis_root,
            work_root=spec.analysis_root / ".work",
            output_root=spec.analysis_root,
            output=spec.analysis_root,
        )

    def _prepare_output_root(self, spec: RunSpec) -> pilot_contract.PilotPaths:
        # The company and analysis directories are ingestion-owned. Canary may
        # create task children, but must never create a company or analysis root.
        return self._paths(spec)

    def _active(self) -> dict[str, Any]:
        value = super()._active()
        if set(value) != ACTIVE_FIELDS or value.get("profile") != PROFILE:
            raise WidePilotError(self._code("active_state_invalid"))
        run_id = value.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            raise WidePilotError(self._code("active_state_invalid"))
        expected_state = (self.settings.runs_relative / run_id).as_posix()
        expected_manifest = f"{expected_state}/{MANIFEST_NAME}"
        manifest_path = self.project_root / expected_manifest
        try:
            manifest = _read_json(manifest_path, root=self.project_root)
        except (LifecycleError, OSError) as exc:
            raise WidePilotError(self._code("active_state_invalid")) from exc
        if (
            value.get("run_state") != expected_state
            or value.get("manifest") != expected_manifest
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("manifest_sha256") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("api_key_sha256") or ""))
            or not manifest_path.is_file()
            or manifest_path.is_symlink()
            or _sha256_file(manifest_path) != value.get("manifest_sha256")
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("mode") != MODE
            or manifest.get("phase") not in {"running", "stopping"}
            or manifest.get("run_id") != run_id
            or manifest.get("market") != value.get("market")
            or manifest.get("company") != value.get("company")
            or manifest.get("api_key_sha256") != value.get("api_key_sha256")
        ):
            raise WidePilotError(self._code("active_state_invalid"))
        return value

    def _active_extra_fields(self, spec: RunSpec, manifest: Mapping[str, Any]) -> dict[str, Any]:
        manifest_path = spec.run_dir / MANIFEST_NAME
        return {
            "manifest": manifest_path.relative_to(self.project_root).as_posix(),
            "manifest_sha256": _sha256_file(manifest_path),
            "api_key_sha256": str(manifest["api_key_sha256"]),
        }

    def _before_stop(self, spec: RunSpec, manifest: dict[str, Any]) -> None:
        manifest["phase"] = "stopping"
        self._write_manifest(spec, manifest)
        active = _read_json(self.active_path, root=self.project_root)
        if set(active) != ACTIVE_FIELDS or active.get("run_id") != spec.run_id:
            raise WidePilotError(self._code("active_state_invalid"))
        active["manifest_sha256"] = _sha256_file(spec.run_dir / MANIFEST_NAME)
        _write_json(self.active_path, active, root=self.project_root)

    def _manifest(self, spec: RunSpec) -> dict[str, Any]:
        value = super()._manifest(spec)
        if (
            set(value) != MANIFEST_FIELDS
            or value.get("profile") != PROFILE
            or value.get("analysis_relative_path") != spec.analysis_relative_path
            or value.get("writable_relative_path") != spec.analysis_relative_path
            or value.get("write_scope") != "current_company_analysis_root"
            or value.get("normal_business_mutations") != ["create", "modify", "rename", "delete"]
            or value.get("mount_count") != BUSINESS_MOUNT_COUNT
            or value.get("providers") != list(PROVIDERS)
            or value.get("broker_request_identity_required") is not True
            or value.get("result_is_formal_evidence") is not False
        ):
            raise WidePilotError(self._code("manifest_invalid"))
        return value

    def _manifest_scope_fields(self, paths: pilot_contract.PilotPaths) -> dict[str, Any]:
        return {
            "writable_relative_path": paths.analysis_root.relative_to(self.project_root).as_posix(),
            "write_scope": "current_company_analysis_root",
            "normal_business_mutations": ["create", "modify", "rename", "delete"],
        }

    def _validate_policy_scope(
        self,
        spec: RunSpec,
        paths: pilot_contract.PilotPaths,
        *,
        summary: Mapping[str, Any],
        filesystem: Any,
    ) -> None:
        if not isinstance(filesystem, dict):
            raise WidePilotError(self._code("policy_scope_invalid"))
        read_write = filesystem.get("read_write")
        read_only = filesystem.get("read_only")
        company_root = spec.analysis_root.parent
        immutable_siblings = {
            company_root,
            company_root / "company.json",
            company_root / "reports",
            company_root / "metrics",
            self.project_root / "scripts",
            self.project_root / "infra",
            self.project_root / "agents/hermes/profiles",
        }
        if (
            summary.get("profile") != PROFILE
            or summary.get("task_scoped_write_count") != 1
            or not isinstance(read_write, list)
            or not isinstance(read_only, list)
            or str(paths.analysis_root) not in read_write
            or any(str(path) in read_write for path in immutable_siblings)
            or str(self.project_root) not in read_only
        ):
            raise WidePilotError(self._code("policy_scope_invalid"))

    def _cleanup_output(self, spec: RunSpec, manifest: Mapping[str, Any], *, allow_missing: bool) -> None:
        del spec, manifest, allow_missing
        # Business output is the purpose of the canary and follows the existing
        # SIQ path contract. Lifecycle cleanup only removes sandbox/control state.

    def _cleanup_uncommitted_business_scope(self, spec: RunSpec) -> None:
        del spec
        # Start never creates a business directory, so failed-start rollback has
        # no task-owned filesystem object to guess or recursively remove.

    def probe(self, *, pilot_id: str) -> dict[str, Any]:
        spec, manifest = self._load_active_spec(pilot_id)
        status = self.status(pilot_id=pilot_id)
        if not status["ok"]:
            raise WidePilotError(self._code("runtime_degraded"))

        source = spec.analysis_root.parent / "company.json"
        reports_probe = spec.analysis_root.parent / "reports" / f".{pilot_id}.probe"
        company_probe = spec.analysis_root.parent / f".{pilot_id}.probe"
        cross_company_probe = spec.analysis_root.parent.parent / f".{pilot_id}.probe"
        code_probe = self.project_root / "scripts/openshell" / f".{pilot_id}.probe"
        source_before = _sha256_file(source)
        probe_root = spec.analysis_root / f".{pilot_id}.probe"
        # Keep the Python body readable and avoid shell interpolation of company
        # names. All paths are passed as argv after strict host validation.
        python_probe = "\n".join(
            (
                "import pathlib, sys",
                "analysis = pathlib.Path(sys.argv[1])",
                "source = pathlib.Path(sys.argv[2])",
                "reports = pathlib.Path(sys.argv[3])",
                "company = pathlib.Path(sys.argv[4])",
                "cross_company = pathlib.Path(sys.argv[5])",
                "code = pathlib.Path(sys.argv[6])",
                "run_id = sys.argv[7]",
                "probe = analysis / ('.' + run_id + '.probe')",
                "probe.mkdir()",
                "for name in ('parsed', 'checkpoint', 'charts', 'derived-report'):",
                "    child = probe / name",
                "    child.mkdir()",
                "    item = child / 'item.json'",
                "    item.write_text('{}\\n', encoding='ascii')",
                "    item.write_text('{\"ok\":true}\\n', encoding='ascii')",
                "    moved = child / 'renamed.json'",
                "    item.rename(moved)",
                "    moved.unlink()",
                "    child.rmdir()",
                "probe.rmdir()",
                "try:",
                "    with source.open('ab') as handle:",
                "        handle.write(b'')",
                "except OSError:",
                "    pass",
                "else:",
                "    raise SystemExit(40)",
                "for forbidden in (reports, company, cross_company, code):",
                "    try:",
                "        forbidden.open('x', encoding='ascii').close()",
                "    except OSError:",
                "        continue",
                "    forbidden.unlink(missing_ok=True)",
                "    raise SystemExit(41)",
            )
        )
        environment_probe = (
            "import os,re;"
            "p=lambda n:re.fullmatch(r'openshell:resolve:env:(?:v[1-9][0-9]*_)?'+re.escape(n),os.environ.get(n,''));"
            "assert all(p(n) for n in ['KIMI_API_KEY','SIQ_MINIMAX_CN_BACKUP','SIQ_MINIMAX_CN_PRIMARY','SIQ_STEPFUN_LLM_API_KEY','TAVILY_API_KEY']);"
            "assert 'EXA_API_KEY' not in os.environ;"
            "assert os.environ.get('SIQ_OPENSHELL_CANARY')=='1';"
            "assert os.environ.get('SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN','').startswith('v1.');"
            "assert os.environ.get('SIQ_OPENSHELL_DATA_IDENTITY_TOKEN','').startswith('v1.')"
        )
        try:
            self.adapter._run_cli(
                [
                    "sandbox",
                    "exec",
                    "--name",
                    spec.sandbox_name,
                    "--timeout",
                    "15",
                    "--no-tty",
                    "--",
                    "/opt/siq/hermes/venv/bin/python",
                    "-c",
                    python_probe,
                    str(spec.analysis_root),
                    str(source),
                    str(reports_probe),
                    str(company_probe),
                    str(cross_company_probe),
                    str(code_probe),
                    pilot_id,
                ],
                self._code("filesystem_probe_failed"),
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
                self._code("provider_environment_probe_failed"),
            )
        except LifecycleError as exc:
            raise WidePilotError(exc.code) from exc
        if (
            probe_root.exists()
            or reports_probe.exists()
            or company_probe.exists()
            or cross_company_probe.exists()
            or code_probe.exists()
            or _sha256_file(source) != source_before
        ):
            raise WidePilotError(self._code("filesystem_probe_residue"))

        mount_plan = _read_json(self.project_root / str(manifest["mount_plan"]), root=self.project_root)
        expected_mounts = mount_plan.get("docker", {}).get("mounts")
        if not isinstance(expected_mounts, list) or len(expected_mounts) != BUSINESS_MOUNT_COUNT:
            raise WidePilotError(self._code("mount_plan_invalid"))
        try:
            actual_mounts = json.loads(
                self.adapter._docker_run(
                    ["inspect", str(manifest["container_id"]), "--format", "{{json .Mounts}}"],
                    self._code("mount_inspection_failed"),
                )
            )
        except (LifecycleError, json.JSONDecodeError) as exc:
            raise WidePilotError(self._code("mount_inspection_failed")) from exc
        expected = {(item["source"], item["target"], item["read_only"] is False) for item in expected_mounts}
        actual = {(item.get("Source"), item.get("Destination"), item.get("RW")) for item in actual_mounts}
        controls = [
            item
            for item in actual_mounts
            if (item.get("Source"), item.get("Destination"), item.get("RW")) not in expected
        ]
        control_targets = {
            "/opt/openshell/bin/openshell-sandbox",
            "/etc/openshell/auth/sandbox.jwt",
            "/etc/openshell/tls/client/ca.crt",
            "/etc/openshell/tls/client/tls.crt",
            "/etc/openshell/tls/client/tls.key",
        }
        if (
            not expected.issubset(actual)
            or len(controls) != 5
            or any(item.get("RW") is not False for item in controls)
            or {item.get("Destination") for item in controls} != control_targets
            or any(
                not str(item.get("Source") or "").startswith(f"{self.project_root}/var/openshell/") for item in controls
            )
        ):
            raise WidePilotError(self._code("mount_contract_invalid"))
        result = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "readiness_effect": READINESS_EFFECT,
            "profile": PROFILE,
            "run_id": pilot_id,
            "status": "probe_passed",
            "analysis_root_business_mutations": ["create", "modify", "rename", "delete"],
            "immutable_company_assets_read_only": True,
            "project_control_plane_read_only": True,
            "cross_company_write_denied": True,
            "broker_identity_present": True,
            "provider_subset_count": len(PROVIDERS),
            "business_mount_count": BUSINESS_MOUNT_COUNT,
            "control_mount_count": len(controls),
            "formal_readiness": "unchanged_no_go",
            "result_is_formal_evidence": False,
        }
        _write_json(spec.run_dir / PROBE_RECEIPT_NAME, result, root=self.project_root)
        return result

    def rollback(self, *, run_id: str) -> dict[str, Any]:
        result = self.stop(pilot_id=run_id)
        receipt = {
            **result,
            "status": "rolled_back_to_host",
            "host_runtime_unchanged": True,
            "canary_route_must_be_disabled": True,
        }
        run_dir = self.project_root / self.settings.runs_relative / run_id
        _write_json(run_dir / ROLLBACK_RECEIPT_NAME, receipt, root=self.project_root)
        return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument(ACKNOWLEDGEMENT, action="store_true", required=True)
    start.add_argument("--market", required=True, choices=sorted(pilot_contract.MARKET_ROOTS))
    start.add_argument("--company", required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--pool-slot-id")
    start.add_argument("--local-port", type=int)
    start.add_argument("--reservation-token")
    for name in ("stop", "status", "probe", "rollback"):
        child = subparsers.add_parser(name)
        child.add_argument("--run-id", required=True)
        child.add_argument("--pool-slot-id")
        child.add_argument("--local-port", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    try:
        slot_id = getattr(args, "pool_slot_id", None)
        local_port = getattr(args, "local_port", None)
        reservation_token = getattr(args, "reservation_token", None)
        if slot_id is None:
            if local_port is not None or reservation_token is not None:
                raise WidePilotError("canary_pool_slot_arguments_incomplete")
            lifecycle = CanaryLifecycle()
        else:
            if local_port is None or (args.command == "start" and not reservation_token):
                raise WidePilotError("canary_pool_slot_arguments_incomplete")
            lifecycle = CanaryLifecycle(
                pool_slot_id=slot_id,
                local_port=local_port,
                reservation_token=reservation_token,
            )
        if args.command == "start":
            result = lifecycle.start(market=args.market, company=args.company, pilot_id=args.run_id)
        elif args.command == "stop":
            result = lifecycle.stop(pilot_id=args.run_id)
        elif args.command == "status":
            result = lifecycle.status(pilot_id=args.run_id)
        elif args.command == "probe":
            result = lifecycle.probe(pilot_id=args.run_id)
        else:
            result = lifecycle.rollback(run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ok") is True else 1
    except (OSError, TypeError, ValueError, UnicodeError, LifecycleError, WidePilotError) as exc:
        if isinstance(exc, WidePilotError):
            code = exc.code
        elif isinstance(exc, LifecycleError):
            code = f"canary_{exc.code}"
        else:
            code = "canary_os_error"
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
