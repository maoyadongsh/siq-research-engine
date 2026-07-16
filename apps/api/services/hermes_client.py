from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import stat
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Literal

import httpx

SIQ_HERMES_DEFAULT_PORTS = {
    "siq_assistant": 18642,
    "siq_analysis": 18651,
    "siq_factchecker": 18649,
    "siq_tracking": 18650,
    "siq_legal": 18652,
    "siq_ic_master_coordinator": 18660,
    "siq_ic_chairman": 18661,
    "siq_ic_strategist": 18662,
    "siq_ic_sector_expert": 18663,
    "siq_ic_finance_auditor": 18664,
    "siq_ic_legal_scanner": 18665,
    "siq_ic_risk_controller": 18666,
}
HERMES_COMPAT_PORTS = {
    "siq_assistant": 8642,
    "siq_analysis": 8651,
    "siq_factchecker": 8649,
    "siq_tracking": 8650,
    "siq_legal": 8652,
    "siq_ic_master_coordinator": 8660,
    "siq_ic_chairman": 8661,
    "siq_ic_strategist": 8662,
    "siq_ic_sector_expert": 8663,
    "siq_ic_finance_auditor": 8664,
    "siq_ic_legal_scanner": 8665,
    "siq_ic_risk_controller": 8666,
}

HERMES_PROFILE_ALIASES = {
    "assistant": "siq_assistant",
    "analysis": "siq_analysis",
    "factchecker": "siq_factchecker",
    "tracking": "siq_tracking",
    "legal": "siq_legal",
    "ic_master": "siq_ic_master_coordinator",
    "ic_coordinator": "siq_ic_master_coordinator",
    "ic_chairman": "siq_ic_chairman",
    "ic_strategy": "siq_ic_strategist",
    "ic_strategist": "siq_ic_strategist",
    "ic_sector": "siq_ic_sector_expert",
    "ic_finance": "siq_ic_finance_auditor",
    "ic_legal": "siq_ic_legal_scanner",
    "ic_risk": "siq_ic_risk_controller",
    "siq_assistant": "siq_assistant",
    "siq_analysis": "siq_analysis",
    "siq_factchecker": "siq_factchecker",
    "siq_tracking": "siq_tracking",
    "siq_legal": "siq_legal",
    "siq_ic_master_coordinator": "siq_ic_master_coordinator",
    "siq_ic_chairman": "siq_ic_chairman",
    "siq_ic_strategist": "siq_ic_strategist",
    "siq_ic_sector_expert": "siq_ic_sector_expert",
    "siq_ic_finance_auditor": "siq_ic_finance_auditor",
    "siq_ic_legal_scanner": "siq_ic_legal_scanner",
    "siq_ic_risk_controller": "siq_ic_risk_controller",
}
HERMES_ENV_PREFIXES = {
    "siq_assistant": "ASSISTANT",
    "siq_analysis": "ANALYSIS",
    "siq_factchecker": "FACTCHECKER",
    "siq_tracking": "TRACKING",
    "siq_legal": "LEGAL",
    "siq_ic_master_coordinator": "IC_MASTER",
    "siq_ic_chairman": "IC_CHAIRMAN",
    "siq_ic_strategist": "IC_STRATEGIST",
    "siq_ic_sector_expert": "IC_SECTOR",
    "siq_ic_finance_auditor": "IC_FINANCE",
    "siq_ic_legal_scanner": "IC_LEGAL",
    "siq_ic_risk_controller": "IC_RISK",
}
HERMES_PROFILE_MODELS = {
    "siq_assistant": "siq_assistant",
    "siq_analysis": "siq_analysis",
    "siq_factchecker": "siq_factchecker",
    "siq_tracking": "siq_tracking",
    "siq_legal": "siq_legal",
    "siq_ic_master_coordinator": "siq_ic_master_coordinator",
    "siq_ic_chairman": "siq_ic_chairman",
    "siq_ic_strategist": "siq_ic_strategist",
    "siq_ic_sector_expert": "siq_ic_sector_expert",
    "siq_ic_finance_auditor": "siq_ic_finance_auditor",
    "siq_ic_legal_scanner": "siq_ic_legal_scanner",
    "siq_ic_risk_controller": "siq_ic_risk_controller",
}

HermesRuntimeTarget = Literal["host", "openshell"]
OPENSHELL_ANALYSIS_RUNS_URL = "http://127.0.0.1:28651/v1/runs"
OPENSHELL_CANARY_STATE_RELATIVE = Path("var/openshell/canary/siq-analysis")
OPENSHELL_CANARY_ACTIVE_RELATIVE = OPENSHELL_CANARY_STATE_RELATIVE / "active.json"
_CANARY_RUN_ID_RE = re.compile(r"canary-[0-9a-f]{12}\Z")
_CANARY_API_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
_CANARY_ACTIVE_SCHEMA = "siq.openshell.siq_analysis_canary_lifecycle.v1"
_CANARY_ACTIVE_FIELDS = {
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
_CANARY_MANIFEST_FIELDS = {
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
_CANARY_PROVIDERS = [
    "siq-minimax-cn-pool",
    "siq-stepfun",
    "siq-kimi-coding",
    "siq-tavily-search",
]
_CANARY_FORMAL_BLOCKERS = [
    "siq-exa-search_not_configured",
    "local_model_8004_not_required",
    "local_model_8006_not_required",
    "milvus_formal_proof_not_required",
    "clash_fake_ip_egress_guard_compatibility_unresolved",
]


class HermesRuntimeSelectionError(RuntimeError):
    """A secret-free failure to authorize or resolve a requested runtime."""


@dataclass(frozen=True, repr=False)
class HermesRunRoute:
    """Immutable endpoint identity shared by create, stream, and stop."""

    target: HermesRuntimeTarget
    base: str
    model: str
    authorization: str
    session_namespace: str
    canary_run_id: str | None = None

    def __repr__(self) -> str:
        return (
            "HermesRunRoute("
            f"target={self.target!r}, base={self.base!r}, model={self.model!r}, "
            f"session_namespace={self.session_namespace!r}, canary_run_id={self.canary_run_id!r}, "
            "authorization='<redacted>')"
        )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite_json(_value: str) -> None:
    raise ValueError("non-finite JSON")


def _read_private_runtime_file(path: Path, *, max_bytes: int) -> bytes:
    """Read one owner-only regular runtime file without following symlinks."""

    project_root = _project_root()
    expected_root = project_root / "var" / "openshell"
    try:
        relative = path.relative_to(expected_root)
    except ValueError as exc:
        raise HermesRuntimeSelectionError("openshell_canary_state_path_invalid") from exc
    if not relative.parts:
        raise HermesRuntimeSelectionError("openshell_canary_state_path_invalid")

    current = expected_root
    try:
        root_info = current.lstat()
        if (
            stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or stat.S_IMODE(root_info.st_mode) & 0o077
        ):
            raise HermesRuntimeSelectionError("openshell_canary_state_directory_invalid")
        for part in relative.parts[:-1]:
            current /= part
            info = current.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise HermesRuntimeSelectionError("openshell_canary_state_directory_invalid")

        path_info = path.lstat()
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or path_info.st_uid != os.geteuid()
            or path_info.st_nlink != 1
            or stat.S_IMODE(path_info.st_mode) & 0o077
        ):
            raise HermesRuntimeSelectionError("openshell_canary_state_file_invalid")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
                or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
            ):
                raise HermesRuntimeSelectionError("openshell_canary_state_file_invalid")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            os.close(descriptor)
    except HermesRuntimeSelectionError:
        raise
    except OSError as exc:
        raise HermesRuntimeSelectionError("openshell_canary_state_unavailable") from exc
    if len(content) > max_bytes:
        raise HermesRuntimeSelectionError("openshell_canary_state_file_invalid")
    return content


def _parse_private_runtime_json(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        raise HermesRuntimeSelectionError("openshell_canary_state_json_invalid") from exc
    if not isinstance(value, dict):
        raise HermesRuntimeSelectionError("openshell_canary_state_json_invalid")
    return value


def _read_private_runtime_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    return _parse_private_runtime_json(_read_private_runtime_file(path, max_bytes=max_bytes))


def _active_openshell_canary() -> tuple[str, str]:
    active_path = _project_root() / OPENSHELL_CANARY_ACTIVE_RELATIVE
    active = _read_private_runtime_json(active_path, max_bytes=4096)
    if set(active) != _CANARY_ACTIVE_FIELDS:
        raise HermesRuntimeSelectionError("openshell_canary_active_invalid")

    run_id = active.get("run_id")
    expected_run_state = OPENSHELL_CANARY_STATE_RELATIVE / "runs" / str(run_id or "")
    expected_manifest = expected_run_state / "canary.json"
    if (
        active.get("schema_version") != _CANARY_ACTIVE_SCHEMA
        or active.get("mode") != "NOT_PRODUCTION_CANARY"
        or active.get("readiness_effect") != "none"
        or active.get("profile") != "siq_analysis"
        or not isinstance(run_id, str)
        or _CANARY_RUN_ID_RE.fullmatch(run_id) is None
        or active.get("run_state") != expected_run_state.as_posix()
        or active.get("manifest") != expected_manifest.as_posix()
        or not isinstance(active.get("manifest_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", active["manifest_sha256"]) is None
        or not isinstance(active.get("api_key_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", active["api_key_sha256"]) is None
        or not isinstance(active.get("market"), str)
        or not isinstance(active.get("company"), str)
    ):
        raise HermesRuntimeSelectionError("openshell_canary_active_invalid")

    market = str(active["market"])
    company = str(active["company"])
    if (
        market not in {"cn", "eu", "hk", "jp", "kr", "us"}
        or not company
        or company in {".", ".."}
        or Path(company).name != company
    ):
        raise HermesRuntimeSelectionError("openshell_canary_active_invalid")
    market_root = Path("data/wiki/companies") if market == "cn" else Path(f"data/wiki/{market}/companies")
    expected_analysis = (market_root / company / "analysis").as_posix()

    manifest_path = _project_root() / expected_manifest
    manifest_content = _read_private_runtime_file(manifest_path, max_bytes=64 * 1024)
    if hashlib.sha256(manifest_content).hexdigest() != active["manifest_sha256"]:
        raise HermesRuntimeSelectionError("openshell_canary_manifest_mismatch")
    manifest = _parse_private_runtime_json(manifest_content)
    if (
        set(manifest) != _CANARY_MANIFEST_FIELDS
        or manifest.get("schema_version") != _CANARY_ACTIVE_SCHEMA
        or manifest.get("mode") != "NOT_PRODUCTION_CANARY"
        or manifest.get("readiness_effect") != "none"
        or manifest.get("phase") != "running"
        or manifest.get("profile") != "siq_analysis"
        or manifest.get("run_id") != run_id
        or manifest.get("market") != market
        or manifest.get("company") != company
        or manifest.get("analysis_relative_path") != expected_analysis
        or manifest.get("writable_relative_path") != expected_analysis
        or manifest.get("write_scope") != "current_company_analysis_root"
        or manifest.get("normal_business_mutations") != ["create", "modify", "rename", "delete"]
        or manifest.get("lifecycle_label") != "siq-analysis-canary-not-production-v1"
        or manifest.get("mount_count") != 7
        or manifest.get("providers") != _CANARY_PROVIDERS
        or manifest.get("formal_blockers_not_overridden") != _CANARY_FORMAL_BLOCKERS
        or manifest.get("broker_request_identity_required") is not True
        or manifest.get("result_is_formal_evidence") is not False
        or not isinstance(manifest.get("api_key_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest["api_key_sha256"]) is None
        or manifest.get("api_key_sha256") != active.get("api_key_sha256")
    ):
        raise HermesRuntimeSelectionError("openshell_canary_manifest_invalid")

    key_path = _project_root() / expected_run_state / "api.key"
    try:
        key = _read_private_runtime_file(key_path, max_bytes=256).decode("ascii").strip()
    except HermesRuntimeSelectionError:
        raise
    except UnicodeError as exc:
        raise HermesRuntimeSelectionError("openshell_canary_api_key_invalid") from exc
    if _CANARY_API_KEY_RE.fullmatch(key) is None:
        raise HermesRuntimeSelectionError("openshell_canary_api_key_invalid")
    if hashlib.sha256(key.encode("ascii")).hexdigest() != manifest["api_key_sha256"]:
        raise HermesRuntimeSelectionError("openshell_canary_api_key_mismatch")
    return run_id, key


def _env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _runs_url(profile: str, env_prefix: str) -> str:
    explicit = _env_value(
        f"SIQ_HERMES_{env_prefix}_RUNS_URL",
        f"HERMES_{env_prefix}_RUNS_URL",
    )
    if explicit:
        return explicit.rstrip("/")
    host = _env_value(f"SIQ_HERMES_{env_prefix}_HOST", f"HERMES_{env_prefix}_HOST") or "127.0.0.1"
    raw_port = _env_value(
        f"SIQ_HERMES_{env_prefix}_PORT",
        f"HERMES_{env_prefix}_PORT",
    )
    default_port = SIQ_HERMES_DEFAULT_PORTS[profile]
    port = int(raw_port or default_port)
    candidates = [port]
    compat_port = HERMES_COMPAT_PORTS[profile]
    if (
        port == default_port
        and compat_port not in candidates
        and _env_bool("SIQ_HERMES_ALLOW_COMPAT_PORTS", False)
    ):
        candidates.append(compat_port)
    for candidate in candidates:
        if _is_tcp_port_open(host, candidate):
            return f"http://{host}:{candidate}/v1/runs"
    return f"http://{host}:{port}/v1/runs"


def _profile_model_name(profile: str, env_prefix: str) -> str:
    explicit = _env_value(
        f"SIQ_HERMES_{env_prefix}_MODEL",
        f"HERMES_{env_prefix}_MODEL",
    )
    if explicit:
        return explicit

    project_root = Path(__file__).resolve().parents[3]
    default_hermes_home = project_root / "data" / "hermes" / "home"
    profiles_root = Path(
        _env_value("SIQ_HERMES_PROFILES_ROOT", "HERMES_PROFILES_ROOT")
        or Path(_env_value("SIQ_HERMES_HOME", "HERMES_HOME") or default_hermes_home) / "profiles"
    ).expanduser()
    model = HERMES_PROFILE_MODELS[profile]
    if (profiles_root / model / "config.yaml").exists():
        return model
    return profile


HermesProfile = Literal[
    "siq_assistant",
    "siq_analysis",
    "siq_factchecker",
    "siq_tracking",
    "siq_legal",
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
]
HERMES_PROFILE_ORDER: tuple[HermesProfile, ...] = (
    "siq_assistant",
    "siq_analysis",
    "siq_factchecker",
    "siq_tracking",
    "siq_legal",
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)


def hermes_profile_config(profile: HermesProfile | str) -> dict[str, str]:
    canonical = normalize_profile(profile)
    env_prefix = HERMES_ENV_PREFIXES[canonical]
    return {
        "base": _runs_url(canonical, env_prefix),
        "model": _profile_model_name(canonical, env_prefix),
    }


def hermes_profiles_config() -> dict[HermesProfile, dict[str, str]]:
    return {profile: hermes_profile_config(profile) for profile in HERMES_PROFILE_ORDER}


class _HermesProfilesMapping(dict):
    def __getitem__(self, key: str) -> dict[str, str]:
        return hermes_profile_config(key)

    def get(self, key: str, default: Any = None) -> dict[str, str] | Any:
        try:
            return hermes_profile_config(key)
        except KeyError:
            return default

    def items(self):
        return hermes_profiles_config().items()

    def keys(self):
        return HERMES_PROFILE_ORDER

    def values(self):
        return hermes_profiles_config().values()


HERMES_PROFILES: dict[HermesProfile, dict[str, str]] = _HermesProfilesMapping()


@dataclass
class StreamEvent:
    """Unified event yielded by stream_run."""
    type: str  # "delta" | "tool.started" | "tool.completed" | "reasoning" | "done" | "failed" | "cancelled"
    text: str = ""
    tool: str = ""
    preview: str | None = None
    duration: float | None = None
    error: bool = False
    status: str = ""
    error_code: str | None = None
    retryable: bool | None = None
    runtime: RunRuntimeMetadata | None = None


RunTerminalStatus = Literal["succeeded", "failed", "cancelled", "timed_out", "protocol_eof"]
RUN_TERMINAL_SCHEMA_VERSION = "siq.hermes.run_terminal.v1"
RUN_RUNTIME_SCHEMA_VERSION = "hermes.run_runtime.v1"
_RUNTIME_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}$")


@dataclass(frozen=True)
class RunRuntimeMetadata:
    """Strict, secret-free projection of Hermes runtime provenance."""

    requested_model: str | None
    configured_provider: str | None
    configured_model: str | None
    effective_provider: str | None
    effective_model: str | None
    fallback_activated: bool | None
    schema_version: str = RUN_RUNTIME_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "requested_model": self.requested_model,
            "configured": {
                "provider": self.configured_provider,
                "model": self.configured_model,
            },
            "effective": {
                "provider": self.effective_provider,
                "model": self.effective_model,
            },
            "fallback": {"activated": self.fallback_activated},
        }


def _runtime_label(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("runtime label must be a string or null")
    normalized = value.strip()
    if (
        not _RUNTIME_LABEL_RE.fullmatch(normalized)
        or "://" in normalized
        or normalized.lower().startswith("bearer")
    ):
        raise ValueError("runtime label is not a safe identifier")
    return normalized


def normalize_run_runtime(value: Any) -> RunRuntimeMetadata | None:
    """Accept only the versioned runtime envelope and discard all extra keys."""

    if not isinstance(value, dict) or value.get("schema_version") != RUN_RUNTIME_SCHEMA_VERSION:
        return None
    configured = value.get("configured")
    effective = value.get("effective")
    fallback = value.get("fallback")
    if not isinstance(configured, dict) or not isinstance(effective, dict) or not isinstance(fallback, dict):
        return None
    activated = fallback.get("activated")
    if activated is not None and not isinstance(activated, bool):
        return None
    try:
        return RunRuntimeMetadata(
            requested_model=_runtime_label(value.get("requested_model")),
            configured_provider=_runtime_label(configured.get("provider")),
            configured_model=_runtime_label(configured.get("model")),
            effective_provider=_runtime_label(effective.get("provider")),
            effective_model=_runtime_label(effective.get("model")),
            fallback_activated=activated,
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class RunTerminalResult:
    """Versioned business terminal shared by streamed and collected Hermes runs."""

    run_id: str
    status: RunTerminalStatus
    received_text: str = ""
    error_code: str | None = None
    retryable: bool = False
    diagnostic: str | None = None
    runtime: RunRuntimeMetadata | None = None
    schema_version: str = RUN_TERMINAL_SCHEMA_VERSION

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "received_text": self.received_text,
            "diagnostic": self.diagnostic,
        }
        if self.runtime is not None:
            payload["runtime"] = self.runtime.to_payload()
        return payload


class RunTerminalError(RuntimeError):
    """Raised by the legacy text collector when Hermes did not succeed."""

    def __init__(self, result: RunTerminalResult):
        self.result = result
        super().__init__(result.diagnostic or result.error_code or result.status)


_RECENT_RUN_TERMINALS: OrderedDict[str, RunTerminalResult] = OrderedDict()
_RECENT_RUN_TERMINAL_LIMIT = 256


def _remember_run_terminal(result: RunTerminalResult) -> RunTerminalResult:
    _RECENT_RUN_TERMINALS[result.run_id] = result
    _RECENT_RUN_TERMINALS.move_to_end(result.run_id)
    while len(_RECENT_RUN_TERMINALS) > _RECENT_RUN_TERMINAL_LIMIT:
        _RECENT_RUN_TERMINALS.popitem(last=False)
    return result


def discard_run_terminal_result(run_id: str) -> None:
    _RECENT_RUN_TERMINALS.pop(str(run_id), None)


def pop_run_terminal_result(run_id: str) -> RunTerminalResult | None:
    """Consume the terminal captured by the compatibility text collector."""

    return _RECENT_RUN_TERMINALS.pop(str(run_id), None)


class RunTerminalAccumulator:
    """Project Hermes events into exactly one immutable terminal result."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.received_text = ""
        self.terminal: RunTerminalResult | None = None

    def accept(self, event: StreamEvent) -> RunTerminalResult | None:
        if self.terminal is not None:
            return self.terminal
        if event.type == "delta":
            self.received_text += event.text
            return None
        if event.type not in {"done", "failed", "cancelled"}:
            return None

        if event.type == "done":
            text = _merge_terminal_text(self.received_text, event.text)
            self.terminal = RunTerminalResult(
                run_id=self.run_id,
                status="succeeded",
                received_text=text,
                runtime=event.runtime,
            )
            return self.terminal

        status: RunTerminalStatus = "failed" if event.type == "failed" else "cancelled"
        error_code = event.error_code or f"hermes_run_{status}"
        retryable = event.retryable if event.retryable is not None else status == "failed"
        self.terminal = RunTerminalResult(
            run_id=self.run_id,
            status=status,
            received_text=self.received_text,
            error_code=error_code,
            retryable=retryable,
            diagnostic=event.text.strip() or None,
            runtime=event.runtime,
        )
        return self.terminal

    def protocol_eof(self) -> RunTerminalResult:
        if self.terminal is None:
            self.terminal = RunTerminalResult(
                run_id=self.run_id,
                status="protocol_eof",
                received_text=self.received_text,
                error_code="hermes_protocol_eof",
                retryable=True,
                diagnostic="Hermes event stream ended without a terminal event",
            )
        return self.terminal

    def timed_out(self, diagnostic: str | None = None) -> RunTerminalResult:
        if self.terminal is None:
            self.terminal = RunTerminalResult(
                run_id=self.run_id,
                status="timed_out",
                received_text=self.received_text,
                error_code="hermes_run_timed_out",
                retryable=True,
                diagnostic=diagnostic or "Hermes run timed out",
            )
        return self.terminal


def _merge_terminal_text(received_text: str, terminal_text: str) -> str:
    return terminal_text or received_text


def terminal_result_from_exception(
    run_id: str,
    exc: BaseException,
    *,
    received_text: str = "",
) -> RunTerminalResult:
    return RunTerminalResult(
        run_id=run_id,
        status="timed_out",
        received_text=received_text,
        error_code="hermes_run_timed_out",
        retryable=True,
        diagnostic=str(exc) or exc.__class__.__name__,
    )


def normalize_profile(profile: str) -> HermesProfile:
    try:
        return HERMES_PROFILE_ALIASES[profile]
    except KeyError as exc:
        raise KeyError(f"Unknown Hermes profile: {profile}") from exc


def _get_profile(profile: HermesProfile | str) -> dict:
    return hermes_profile_config(profile)


def _hermes_auth_header(profile: HermesProfile | str) -> str:
    canonical = normalize_profile(profile)
    env_prefix = HERMES_ENV_PREFIXES[canonical]
    raw = _env_value(
        f"SIQ_HERMES_{env_prefix}_API_KEY",
        f"HERMES_{env_prefix}_API_KEY",
        f"SIQ_HERMES_{env_prefix}_TOKEN",
        f"HERMES_{env_prefix}_TOKEN",
        "HERMES_API_KEY",
        "HERMES_TOKEN",
    )
    token = raw.strip()
    if not token:
        raise RuntimeError(f"Hermes API key is not configured for profile {canonical}.")
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def normalize_runtime_target(
    profile: HermesProfile | str,
    requested_target: str | None,
    *,
    session_id: str | None = None,
) -> HermesRuntimeTarget:
    """Authorize a request target, with Host remaining the rollback default."""

    canonical = normalize_profile(profile)
    configured_target = os.getenv("SIQ_HERMES_RUNTIME", "host")
    target = str(requested_target if requested_target is not None else configured_target).strip().lower()
    if target == "host":
        return "host"
    if target != "openshell":
        raise HermesRuntimeSelectionError("hermes_runtime_target_invalid")
    if canonical != "siq_analysis":
        return "host"
    if not _env_bool("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", False):
        raise HermesRuntimeSelectionError("openshell_canary_not_enabled")

    session_mode = os.getenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "allowlist").strip().lower()
    if session_mode == "all":
        return "openshell"
    if session_mode != "allowlist":
        raise HermesRuntimeSelectionError("openshell_canary_session_mode_invalid")

    allowed_sessions = {
        item.strip()
        for item in os.getenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "").split(",")
        if item.strip()
    }
    if not allowed_sessions or str(session_id or "") not in allowed_sessions:
        raise HermesRuntimeSelectionError("openshell_canary_session_not_authorized")
    return "openshell"


def resolve_run_route(
    profile: HermesProfile | str,
    requested_target: str | None = None,
    *,
    session_id: str | None = None,
) -> HermesRunRoute:
    """Resolve an immutable, credential-bound route before creating a run."""

    canonical = normalize_profile(profile)
    target = normalize_runtime_target(canonical, requested_target, session_id=session_id)
    if target == "host":
        config = hermes_profile_config(canonical)
        return HermesRunRoute(
            target="host",
            base=config["base"],
            model=config["model"],
            authorization=_hermes_auth_header(canonical),
            session_namespace=f"siq:{canonical}",
        )

    run_id, key = _active_openshell_canary()
    return HermesRunRoute(
        target="openshell",
        base=OPENSHELL_ANALYSIS_RUNS_URL,
        model=_profile_model_name(canonical, HERMES_ENV_PREFIXES[canonical]),
        authorization=f"Bearer {key}",
        session_namespace=f"siq:openshell:{run_id}:{canonical}",
        canary_run_id=run_id,
    )


def route_session_id(
    route: HermesRunRoute,
    profile: HermesProfile | str,
    session_id: str,
) -> str:
    canonical = normalize_profile(profile)
    if route.target == "host":
        return f"siq:{canonical}:{session_id}"
    return f"{route.session_namespace}:{session_id}"


def _build_run_payload(
    model: str,
    input: str | list[dict[str, Any]],
    conversation_history: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    instructions: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": input,
    }
    if session_id:
        payload["session_id"] = session_id
    if instructions:
        payload["instructions"] = instructions
    if conversation_history:
        payload["conversation_history"] = conversation_history
    return payload


async def create_run(
    input: str | list[dict[str, Any]],
    conversation_history: list[dict[str, Any]],
    *,
    profile: HermesProfile | str = "siq_assistant",
    session_id: str | None = None,
    instructions: str | None = None,
    route: HermesRunRoute | None = None,
) -> str:
    """POST /v1/runs, return run_id."""
    cfg = _get_profile(profile) if route is None else {"base": route.base, "model": route.model}
    headers = {
        "Authorization": _hermes_auth_header(profile) if route is None else route.authorization,
        "Content-Type": "application/json",
    }
    payload = _build_run_payload(
        cfg["model"],
        input,
        conversation_history,
        session_id=session_id,
        instructions=instructions,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(cfg["base"], headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["run_id"]


async def stream_run(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
    timeout: float | httpx.Timeout | None = None,
    route: HermesRunRoute | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Subscribe to run SSE events, yield structured StreamEvent objects."""
    cfg = _get_profile(profile) if route is None else {"base": route.base}
    headers = {"Authorization": _hermes_auth_header(profile) if route is None else route.authorization}
    url = f"{cfg['base']}/{run_id}/events"

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event", "")

                if event_type == "message.delta":
                    delta = event.get("delta", "")
                    if delta:
                        yield StreamEvent(type="delta", text=delta)

                elif event_type == "tool.started":
                    yield StreamEvent(
                        type="tool.started",
                        tool=event.get("tool", ""),
                        preview=event.get("preview"),
                    )

                elif event_type == "tool.completed":
                    yield StreamEvent(
                        type="tool.completed",
                        tool=event.get("tool", ""),
                        duration=event.get("duration"),
                        error=event.get("error", False),
                    )

                elif event_type == "reasoning.available":
                    text = event.get("text", "")
                    if text:
                        yield StreamEvent(type="reasoning", text=text)

                elif event_type in ("run.completed", "run.failed", "run.cancelled"):
                    output = event.get("output", "")
                    if not isinstance(output, str):
                        output = json.dumps(output, ensure_ascii=False)
                    status = event_type.removeprefix("run.")
                    error_payload = event.get("error")
                    diagnostic = output
                    error_code = None
                    retryable = None
                    if isinstance(error_payload, dict):
                        error_code = str(error_payload.get("code") or "").strip() or None
                        retryable_value = error_payload.get("retryable")
                        retryable = retryable_value if isinstance(retryable_value, bool) else None
                        diagnostic = str(
                            error_payload.get("message") or error_payload.get("detail") or output or ""
                        )
                    elif error_payload and not output:
                        diagnostic = str(error_payload)
                    yield StreamEvent(
                        type="done" if status == "completed" else status,
                        text=diagnostic,
                        error=status != "completed",
                        status=status,
                        error_code=error_code,
                        retryable=retryable,
                        runtime=normalize_run_runtime(event.get("runtime")),
                    )
                    break


async def stop_run(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
    route: HermesRunRoute | None = None,
) -> dict:
    """POST /v1/runs/{run_id}/stop and return the Hermes response."""
    cfg = _get_profile(profile) if route is None else {"base": route.base}
    headers = {"Authorization": _hermes_auth_header(profile) if route is None else route.authorization}
    url = f"{cfg['base']}/{run_id}/stop"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def collect_run_terminal_result(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
    timeout: float | httpx.Timeout | None = None,
    route: HermesRunRoute | None = None,
) -> RunTerminalResult:
    """Collect a Hermes stream into the canonical versioned terminal contract."""
    accumulator = RunTerminalAccumulator(run_id)
    try:
        stream = (
            stream_run(run_id, profile=profile, timeout=timeout)
            if route is None
            else stream_run(run_id, profile=profile, timeout=timeout, route=route)
        )
        async for event in stream:
            terminal = accumulator.accept(event)
            if terminal is not None:
                return _remember_run_terminal(terminal)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        return _remember_run_terminal(accumulator.timed_out(str(exc) or exc.__class__.__name__))
    return _remember_run_terminal(accumulator.protocol_eof())


async def collect_run_result(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
    timeout: float | httpx.Timeout | None = None,
    route: HermesRunRoute | None = None,
) -> str:
    """Compatibility text API that only returns successful Hermes output."""
    if route is None:
        result = await collect_run_terminal_result(run_id, profile=profile, timeout=timeout)
    else:
        result = await collect_run_terminal_result(run_id, profile=profile, timeout=timeout, route=route)
    if not result.succeeded:
        raise RunTerminalError(result)
    return result.received_text
