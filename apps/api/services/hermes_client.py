import json
import os
import socket
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
}
HERMES_COMPAT_PORTS = {
    "siq_assistant": 8642,
    "siq_analysis": 8651,
    "siq_factchecker": 8649,
    "siq_tracking": 8650,
    "siq_legal": 8652,
}

HERMES_PROFILE_ALIASES = {
    "assistant": "siq_assistant",
    "analysis": "siq_analysis",
    "factchecker": "siq_factchecker",
    "tracking": "siq_tracking",
    "legal": "siq_legal",
    "siq_assistant": "siq_assistant",
    "siq_analysis": "siq_analysis",
    "siq_factchecker": "siq_factchecker",
    "siq_tracking": "siq_tracking",
    "siq_legal": "siq_legal",
}
HERMES_ENV_PREFIXES = {
    "siq_assistant": "ASSISTANT",
    "siq_analysis": "ANALYSIS",
    "siq_factchecker": "FACTCHECKER",
    "siq_tracking": "TRACKING",
    "siq_legal": "LEGAL",
}
HERMES_PROFILE_MODELS = {
    "siq_assistant": "siq_assistant",
    "siq_analysis": "siq_analysis",
    "siq_factchecker": "siq_factchecker",
    "siq_tracking": "siq_tracking",
    "siq_legal": "siq_legal",
}


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


HermesProfile = Literal["siq_assistant", "siq_analysis", "siq_factchecker", "siq_tracking", "siq_legal"]
HERMES_PROFILE_ORDER: tuple[HermesProfile, ...] = (
    "siq_assistant",
    "siq_analysis",
    "siq_factchecker",
    "siq_tracking",
    "siq_legal",
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


def normalize_profile(profile: str) -> HermesProfile:
    try:
        return HERMES_PROFILE_ALIASES[profile]
    except KeyError as exc:
        raise KeyError(f"Unknown Hermes profile: {profile}") from exc


def _get_profile(profile: HermesProfile | str) -> dict:
    return hermes_profile_config(profile)


def _hermes_auth_header() -> str:
    raw = os.getenv("HERMES_API_KEY") or os.getenv("HERMES_TOKEN") or ""
    token = raw.strip()
    if not token:
        raise RuntimeError("HERMES_API_KEY or HERMES_TOKEN must be set before calling Hermes.")
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _build_run_payload(
    model: str,
    input: str | list[dict[str, Any]],
    conversation_history: list[dict[str, Any]],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": input,
    }
    if session_id:
        payload["session_id"] = session_id
    if conversation_history:
        payload["conversation_history"] = conversation_history
    return payload


async def create_run(
    input: str | list[dict[str, Any]],
    conversation_history: list[dict[str, Any]],
    *,
    profile: HermesProfile | str = "siq_assistant",
    session_id: str | None = None,
) -> str:
    """POST /v1/runs, return run_id."""
    cfg = _get_profile(profile)
    headers = {
        "Authorization": _hermes_auth_header(),
        "Content-Type": "application/json",
    }
    payload = _build_run_payload(
        cfg["model"],
        input,
        conversation_history,
        session_id=session_id,
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
) -> AsyncGenerator[StreamEvent, None]:
    """Subscribe to run SSE events, yield structured StreamEvent objects."""
    cfg = _get_profile(profile)
    headers = {"Authorization": _hermes_auth_header()}
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
                    status = event_type.removeprefix("run.")
                    yield StreamEvent(
                        type="done" if status == "completed" else status,
                        text=output,
                        error=status != "completed",
                        status=status,
                    )
                    break


async def stop_run(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
) -> dict:
    """POST /v1/runs/{run_id}/stop and return the Hermes response."""
    cfg = _get_profile(profile)
    headers = {"Authorization": _hermes_auth_header()}
    url = f"{cfg['base']}/{run_id}/stop"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def collect_run_result(
    run_id: str,
    *,
    profile: HermesProfile | str = "siq_assistant",
    timeout: float | httpx.Timeout | None = None,
) -> str:
    """Stream a run to completion and return the full text output."""
    full_text = ""
    async for ev in stream_run(run_id, profile=profile, timeout=timeout):
        if ev.type == "delta":
            full_text += ev.text
        elif ev.type == "done":
            if ev.text:
                return ev.text
        elif ev.type in {"failed", "cancelled"}:
            status_label = "失败" if ev.type == "failed" else "已取消"
            detail = ev.text.strip() if ev.text else f"Hermes run {ev.type}"
            return f"[{status_label}] {detail}"
    return full_text
