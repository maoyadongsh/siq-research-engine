from __future__ import annotations

import os
import re
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml

from services.hermes_client import HermesProfile, normalize_profile
from services.path_config import HERMES_PROFILE_ROOTS

ModelMode = Literal["local", "qwen36", "gemma4", "nemotron", "cloud", "kimi", "minimax", "stepfun"]


def _custom_provider_slug(provider_name: str) -> str:
    """Match Hermes' canonical key for a named custom provider."""
    return f"custom:{provider_name.strip().lower().replace(' ', '-')}"


GEMMA4_MODEL = "Gemma-4-26B-A4B-it-NVFP4"
GEMMA4_PROVIDER_NAME = "Gemma4 Local"
GEMMA4_PROVIDER = _custom_provider_slug(GEMMA4_PROVIDER_NAME)
GEMMA4_BASE_URL = "http://127.0.0.1:8006/v1"
GEMMA4_CONTEXT_LENGTH = 262144
GEMMA4_TEMPERATURE = 0.2
QWEN36_MODEL = "Qwen3.6-35B-A3B-FP8"
QWEN36_PROVIDER_NAME = "Qwen3.6 Local"
QWEN36_PROVIDER = _custom_provider_slug(QWEN36_PROVIDER_NAME)
QWEN36_BASE_URL = "http://127.0.0.1:8004/v1"
QWEN36_CONTEXT_LENGTH = 262144
QWEN36_TEMPERATURE = 0.2
NEMOTRON_MODEL = "nemotron_3_nano_omni"
NEMOTRON_PROVIDER_NAME = "Nemotron 3 Nano Omni Local"
NEMOTRON_PROVIDER = _custom_provider_slug(NEMOTRON_PROVIDER_NAME)
NEMOTRON_BASE_URL = "http://127.0.0.1:8007/v1"
NEMOTRON_CONTEXT_LENGTH = 262144
NEMOTRON_TEMPERATURE = 0.2
LOCAL_MODEL = QWEN36_MODEL
LOCAL_PROVIDER = QWEN36_PROVIDER
LOCAL_BASE_URL = QWEN36_BASE_URL
LOCAL_CONTEXT_LENGTH = QWEN36_CONTEXT_LENGTH
LOCAL_TEMPERATURE = QWEN36_TEMPERATURE
KIMI_MODEL = "kimi-for-coding"
KIMI_PROVIDER = "kimi-coding"
KIMI_BASE_URL = "https://api.kimi.com/coding"
MINIMAX_MODEL = "MiniMax-M3"
MINIMAX_PROVIDER = "minimax-cn"
STEPFUN_MODEL = "step-3.7-flash"
STEPFUN_PROVIDER_NAME = "StepFun Step-3.7 Flash"
STEPFUN_PROVIDER = _custom_provider_slug(STEPFUN_PROVIDER_NAME)
STEPFUN_BASE_URL = "https://api.stepfun.com/v1"
STEPFUN_CONTEXT_LENGTH = 200000
STEPFUN_TEMPERATURE = 0.2
STEPFUN_KEY_ENV = "SIQ_STEPFUN_LLM_API_KEY"

MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    "local": {
        "label": "本地 Qwen3.6",
        "kind": "local",
        "provider_name": QWEN36_PROVIDER_NAME,
        "provider": LOCAL_PROVIDER,
        "base_url": LOCAL_BASE_URL,
        "model": LOCAL_MODEL,
        "context_length": LOCAL_CONTEXT_LENGTH,
        "temperature": LOCAL_TEMPERATURE,
    },
    "qwen36": {
        "label": "本地 Qwen3.6",
        "kind": "local",
        "provider_name": QWEN36_PROVIDER_NAME,
        "provider": QWEN36_PROVIDER,
        "base_url": QWEN36_BASE_URL,
        "model": QWEN36_MODEL,
        "context_length": QWEN36_CONTEXT_LENGTH,
        "temperature": QWEN36_TEMPERATURE,
    },
    "gemma4": {
        "label": "本地 Gemma4",
        "kind": "local",
        "provider_name": GEMMA4_PROVIDER_NAME,
        "provider": GEMMA4_PROVIDER,
        "base_url": GEMMA4_BASE_URL,
        "model": GEMMA4_MODEL,
        "context_length": GEMMA4_CONTEXT_LENGTH,
        "temperature": GEMMA4_TEMPERATURE,
    },
    "nemotron": {
        "label": "本地 Nemotron 3 Nano Omni",
        "kind": "local",
        "provider_name": NEMOTRON_PROVIDER_NAME,
        "provider": NEMOTRON_PROVIDER,
        "base_url": NEMOTRON_BASE_URL,
        "model": NEMOTRON_MODEL,
        "context_length": NEMOTRON_CONTEXT_LENGTH,
        "temperature": NEMOTRON_TEMPERATURE,
    },
    "cloud": {
        "label": "云端 StepFun",
        "kind": "cloud",
        "provider_name": STEPFUN_PROVIDER_NAME,
        "provider": STEPFUN_PROVIDER,
        "base_url": STEPFUN_BASE_URL,
        "model": STEPFUN_MODEL,
        "api_mode": "openai_chat",
        "context_length": STEPFUN_CONTEXT_LENGTH,
        "temperature": STEPFUN_TEMPERATURE,
        "key_env": STEPFUN_KEY_ENV,
    },
    "kimi": {
        "label": "云端 Kimi",
        "kind": "cloud",
        "provider": KIMI_PROVIDER,
        "model": KIMI_MODEL,
        "base_url": KIMI_BASE_URL,
    },
    "minimax": {
        "label": "云端 Minimax",
        "kind": "cloud",
        "provider": MINIMAX_PROVIDER,
        "model": MINIMAX_MODEL,
    },
    "stepfun": {
        "label": "云端 StepFun",
        "kind": "cloud",
        "provider_name": STEPFUN_PROVIDER_NAME,
        "provider": STEPFUN_PROVIDER,
        "base_url": STEPFUN_BASE_URL,
        "model": STEPFUN_MODEL,
        "api_mode": "openai_chat",
        "context_length": STEPFUN_CONTEXT_LENGTH,
        "temperature": STEPFUN_TEMPERATURE,
        "key_env": STEPFUN_KEY_ENV,
    },
}
CANONICAL_MODEL_MODES: tuple[ModelMode, ...] = ("qwen36", "gemma4", "nemotron", "kimi", "minimax", "stepfun")
LOCAL_MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    key: MODEL_OPTIONS[key]
    for key in ("qwen36", "gemma4", "nemotron")
}
CLOUD_MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    key: MODEL_OPTIONS[key]
    for key in ("kimi", "minimax", "stepfun")
}
PROFILE_CONFIGS: dict[HermesProfile, Path] = {
    "siq_assistant": HERMES_PROFILE_ROOTS["siq_assistant"] / "config.yaml",
    "siq_analysis": HERMES_PROFILE_ROOTS["siq_analysis"] / "config.yaml",
    "siq_factchecker": HERMES_PROFILE_ROOTS["siq_factchecker"] / "config.yaml",
    "siq_tracking": HERMES_PROFILE_ROOTS["siq_tracking"] / "config.yaml",
    "siq_legal": HERMES_PROFILE_ROOTS["siq_legal"] / "config.yaml",
    "siq_ic_master_coordinator": HERMES_PROFILE_ROOTS["siq_ic_master_coordinator"] / "config.yaml",
    "siq_ic_chairman": HERMES_PROFILE_ROOTS["siq_ic_chairman"] / "config.yaml",
    "siq_ic_strategist": HERMES_PROFILE_ROOTS["siq_ic_strategist"] / "config.yaml",
    "siq_ic_sector_expert": HERMES_PROFILE_ROOTS["siq_ic_sector_expert"] / "config.yaml",
    "siq_ic_finance_auditor": HERMES_PROFILE_ROOTS["siq_ic_finance_auditor"] / "config.yaml",
    "siq_ic_legal_scanner": HERMES_PROFILE_ROOTS["siq_ic_legal_scanner"] / "config.yaml",
    "siq_ic_risk_controller": HERMES_PROFILE_ROOTS["siq_ic_risk_controller"] / "config.yaml",
}
LEGACY_LIVE_PROFILE_ALIASES: dict[HermesProfile, str] = {
    "siq_assistant": "finsight_assistant",
    "siq_analysis": "finsight_analysis",
    "siq_factchecker": "finsight_factchecker",
    "siq_tracking": "finsight_tracking",
    "siq_legal": "finsight_legal",
}
PROFILE_ORDER: tuple[HermesProfile, ...] = tuple(PROFILE_CONFIGS.keys())
PROFILE_LABELS: dict[HermesProfile, str] = {
    "siq_assistant": "SIQ Assistant",
    "siq_analysis": "SIQ Analysis",
    "siq_factchecker": "SIQ Factchecker",
    "siq_tracking": "SIQ Tracking",
    "siq_legal": "SIQ Legal",
    "siq_ic_master_coordinator": "SIQ IC Master Coordinator",
    "siq_ic_chairman": "SIQ IC Chairman",
    "siq_ic_strategist": "SIQ IC Strategist",
    "siq_ic_sector_expert": "SIQ IC Sector Expert",
    "siq_ic_finance_auditor": "SIQ IC Finance Auditor",
    "siq_ic_legal_scanner": "SIQ IC Legal Scanner",
    "siq_ic_risk_controller": "SIQ IC Risk Controller",
}

LOCAL_PATTERNS = (
    "切换到本地",
    "切到本地",
    "使用本地",
    "用本地",
    "本地模型",
    "local model",
    "use local",
    "switch to local",
)
GEMMA4_PATTERNS = (
    "切换到gemma",
    "切换到 gemma",
    "切到gemma",
    "切到 gemma",
    "使用gemma",
    "使用 gemma",
    "用gemma",
    "用 gemma",
    "gemma4",
    "gemma 4",
    "gemma",
)
NEMOTRON_PATTERNS = (
    "nemotron-3",
    "nemotron 3",
    "nemotron3",
    "nemotron",
    "nano omni",
    "nvidia nemotron",
    "英伟达 nemotron",
)
CLOUD_PATTERNS = (
    "切换到云端",
    "切回云端",
    "使用云端",
    "用云端",
    "云端模型",
    "kimi",
    "use cloud",
    "switch to cloud",
)
QWEN36_PATTERNS = (
    "qwen3.6",
    "qwen36",
    "qwen 3.6",
    "qwen3",
    "qwen",
    "通义千问",
    "千问",
)
KIMI_PATTERNS = (
    "kimi",
    "moonshot",
    "月之暗面",
)
MINIMAX_PATTERNS = (
    "minimax",
    "mini max",
    "mini-max",
    "MiniMax-M3".lower(),
    "m3",
    "MiniMax-M2.7".lower(),
    "m2.7",
    "海螺",
)
STEPFUN_PATTERNS = (
    "stepfun",
    "step fun",
    "step-3.7",
    "step 3.7",
    "step3.7",
    "step-3.7-flash",
    "阶跃星辰",
    "阶跃",
)
STATUS_PATTERNS = ("模型状态", "当前模型", "查看模型", "model status", "current model")
MODEL_LIST_RE = re.compile(
    r"(可用模型|模型列表|有哪些.*模型|模型.*有哪些|选择模型|available models|model list|list models)",
    re.IGNORECASE,
)
SWITCH_VERB_RE = re.compile(
    r"(切换|切到|切回|改用|换成|换到|使用|启用|设为|设置为|用|switch|change|set|use)",
    re.IGNORECASE,
)
EXPLICIT_SWITCH_RE = re.compile(
    r"(切换|切到|切回|改用|换成|换到|启用|设为|设置为|switch|change|set)",
    re.IGNORECASE,
)
STATUS_RE = re.compile(
    r"(模型状态|当前模型|当前.*模型|查看模型|现在.*模型|正在.*模型|用.*什么模型|"
    r"当前.*(?:使用|用).*(?:吗|么|\?|？)|现在.*(?:使用|用).*(?:吗|么|\?|？)|"
    r"正在.*(?:使用|用).*(?:吗|么|\?|？)|"
    r"使用.*什么模型|什么模型|current model|model status|what model|which model)",
    re.IGNORECASE,
)
CONTROL_HINT = re.compile(
    r"(切换|切到|切回|使用|改用|模型|model|gemma|qwen|nemotron|nano omni|nvidia|kimi|minimax|stepfun|step|local|cloud|云端|本地|阶跃)",
    re.IGNORECASE,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Hermes profile config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Hermes profile config is not a mapping: {path}")
    return data


def _profile_config_paths(profile: HermesProfile) -> tuple[Path, ...]:
    canonical_path = PROFILE_CONFIGS[profile]
    paths = [canonical_path]
    legacy_alias = LEGACY_LIVE_PROFILE_ALIASES.get(profile)
    if legacy_alias and canonical_path.parent.name == profile:
        mirror_path = canonical_path.parent.parent / legacy_alias / "config.yaml"
        if mirror_path.is_file() and mirror_path != canonical_path:
            paths.append(mirror_path)
    return tuple(paths)


def _status_config_path(profile: HermesProfile) -> Path:
    # A legacy finsight_* gateway is the live consumer when its mirror exists.
    return _profile_config_paths(profile)[-1]


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _ensure_custom_provider(config: dict[str, Any]) -> None:
    providers = config.get("custom_providers")
    if not isinstance(providers, list):
        providers = []

    for mode in ("qwen36", "gemma4", "nemotron", "stepfun"):
        option = MODEL_OPTIONS[mode]
        provider_payload = {
            "name": option["provider_name"],
            "base_url": option["base_url"],
            "model": option["model"],
            "api_mode": option.get("api_mode") or "openai_chat",
            "context_length": option["context_length"],
            "temperature": option["temperature"],
            "models": {option["model"]: {"context_length": option["context_length"]}},
        }
        if option.get("key_env"):
            provider_payload["key_env"] = option["key_env"]
        for entry in providers:
            if isinstance(entry, dict) and (
                entry.get("name") == option["provider_name"]
                or str(entry.get("base_url") or "").rstrip("/") == str(option["base_url"]).rstrip("/")
            ):
                entry.update(provider_payload)
                break
        else:
            providers.append(provider_payload)

    config["custom_providers"] = providers


def _fallback_chain_for_mode(mode: ModelMode) -> list[dict[str, Any]]:
    mode = _canonical_mode(mode)
    fallback_modes: tuple[ModelMode, ...]
    if mode in {"local", "qwen36"}:
        fallback_modes = ("stepfun", "minimax", "kimi", "nemotron", "gemma4")
    elif mode == "gemma4":
        fallback_modes = ("stepfun", "minimax", "kimi", "nemotron", "qwen36")
    elif mode == "nemotron":
        fallback_modes = ("stepfun", "minimax", "kimi", "qwen36", "gemma4")
    elif mode == "kimi":
        fallback_modes = ("stepfun", "minimax", "qwen36", "nemotron", "gemma4")
    elif mode == "minimax":
        fallback_modes = ("stepfun", "kimi", "qwen36", "nemotron", "gemma4")
    elif mode == "stepfun":
        fallback_modes = ("qwen36", "nemotron", "gemma4", "minimax", "kimi")
    else:
        fallback_modes = ("qwen36", "nemotron", "gemma4", "stepfun", "kimi", "minimax")

    chain: list[dict[str, Any]] = []
    current = _canonical_mode(mode)
    for fallback_mode in fallback_modes:
        if fallback_mode == current:
            continue
        option = MODEL_OPTIONS[fallback_mode]
        entry = {"provider": option["provider"], "model": option["model"]}
        if option.get("temperature") is not None:
            entry["temperature"] = option["temperature"]
        if option.get("base_url"):
            entry["base_url"] = option["base_url"]
        if option.get("api_mode"):
            entry["api_mode"] = option["api_mode"]
        if option.get("key_env"):
            entry["key_env"] = option["key_env"]
        chain.append(entry)
    return chain


def _ensure_model_fallback(config: dict[str, Any], mode: ModelMode | None = None) -> None:
    if mode is None:
        model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
        provider = str(model_config.get("provider") or "")
        model = str(model_config.get("default") or "")
        mode = "kimi"
        for candidate in CANONICAL_MODEL_MODES:
            option = MODEL_OPTIONS[candidate]
            if provider == option["provider"] or model == option["model"]:
                mode = candidate
                break
    config["fallback_providers"] = _fallback_chain_for_mode(mode)


def _ensure_tool_use_enforcement(config: dict[str, Any]) -> None:
    agent_config = config.get("agent")
    if not isinstance(agent_config, dict):
        agent_config = {}
    agent_config["tool_use_enforcement"] = True
    config["agent"] = agent_config


def _set_model(config: dict[str, Any], mode: ModelMode) -> None:
    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}

    mode = _canonical_mode(mode)
    option = MODEL_OPTIONS[mode]
    if option["kind"] == "local" or str(option["provider"]).startswith("custom:"):
        model_config.update(
            {
                "default": option["model"],
                "provider": option["provider"],
                "base_url": option["base_url"],
                "api_mode": option.get("api_mode") or "openai_chat",
                "context_length": option["context_length"],
                "temperature": option["temperature"],
            }
        )
        if option.get("key_env"):
            model_config["key_env"] = option["key_env"]
    else:
        model_config.update(
            {
                "default": option["model"],
                "provider": option["provider"],
            }
        )
        if option.get("base_url"):
            model_config["base_url"] = option["base_url"]
        else:
            model_config.pop("base_url", None)
        model_config.pop("api_mode", None)
        model_config.pop("context_length", None)
        model_config.pop("temperature", None)
        model_config.pop("key_env", None)

    config["model"] = model_config


def _canonical_mode(mode: ModelMode) -> ModelMode:
    if mode == "local":
        return "qwen36"
    if mode == "cloud":
        return "stepfun"
    return mode


def current_model_mode(profile: HermesProfile | str) -> ModelMode:
    profile = normalize_profile(profile)
    config = _load_yaml(_status_config_path(profile))
    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model_config.get("provider") or "")
    model = str(model_config.get("default") or "")
    base_url = str(model_config.get("base_url") or "")
    normalized_model = re.sub(r"[^a-z0-9]+", "", model.lower())
    for mode in CANONICAL_MODEL_MODES:
        option = MODEL_OPTIONS[mode]
        if provider == option["provider"] or model == option["model"]:
            return mode
        if normalized_model == re.sub(r"[^a-z0-9]+", "", str(option["model"]).lower()):
            return mode
        if option.get("base_url") and base_url.rstrip("/") == str(option["base_url"]).rstrip("/"):
            return mode
    if provider == "custom" and ("qwen" in normalized_model or "qwen" in base_url.lower()):
        return "qwen36"
    if provider == "custom" and ("gemma" in normalized_model or "gemma" in base_url.lower()):
        return "gemma4"
    if provider == "custom" and ("nemotron" in normalized_model or "nemotron" in base_url.lower()):
        return "nemotron"
    if "stepfun" in provider.lower() or "stepfun" in base_url.lower() or "step3" in normalized_model or "step37" in normalized_model:
        return "stepfun"
    if "minimax" in provider.lower() or "minimax" in normalized_model:
        return "minimax"
    if "kimi" in provider.lower() or "kimi" in normalized_model:
        return "kimi"
    return "kimi"


def set_profile_model_mode(profile: HermesProfile | str, mode: ModelMode) -> dict[str, Any]:
    profile = normalize_profile(profile)
    updates = [(path, _load_yaml(path)) for path in _profile_config_paths(profile)]
    for _, config in updates:
        _ensure_custom_provider(config)
        _set_model(config, mode)
        _ensure_model_fallback(config, mode)
        _ensure_tool_use_enforcement(config)
    for path, config in updates:
        _save_yaml(path, config)
    return describe_profile_model(profile)


def set_all_profile_model_modes(mode: ModelMode) -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    for profile in PROFILE_ORDER:
        statuses[profile] = set_profile_model_mode(profile, mode)
    return {
        "mode": _canonical_mode(mode),
        "profiles": statuses,
    }


def ensure_profile_fallback(profile: HermesProfile | str) -> None:
    profile = normalize_profile(profile)
    updates = [(path, _load_yaml(path)) for path in _profile_config_paths(profile)]
    for _, config in updates:
        _ensure_custom_provider(config)
        _ensure_model_fallback(config)
        _ensure_tool_use_enforcement(config)
    for path, config in updates:
        _save_yaml(path, config)


def describe_profile_model(profile: HermesProfile | str) -> dict[str, Any]:
    profile = normalize_profile(profile)
    config = _load_yaml(_status_config_path(profile))
    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    configured_fallbacks = config.get("fallback_providers") or []
    mode = current_model_mode(profile)
    expected_fallbacks = _fallback_chain_for_mode(mode)
    option = MODEL_OPTIONS.get(mode, MODEL_OPTIONS["kimi"])
    return {
        "profile": profile,
        "label": PROFILE_LABELS[profile],
        "mode": mode,
        "modeLabel": option["label"],
        "kind": option["kind"],
        "model": model_config.get("default") or "",
        "provider": model_config.get("provider") or "",
        "baseUrl": model_config.get("base_url") or "",
        "contextLength": model_config.get("context_length") or None,
        "temperature": model_config.get("temperature") if model_config.get("temperature") is not None else None,
        "fallback": configured_fallbacks == expected_fallbacks,
        "fallbackModels": [
            item.get("model", "")
            for item in configured_fallbacks
            if isinstance(item, dict)
        ],
    }


def describe_all_profile_models() -> dict[str, Any]:
    return {
        profile: describe_profile_model(profile)
        for profile in PROFILE_ORDER
    }


def model_catalog() -> dict[str, Any]:
    """Return configured selector fields without credentials or endpoint URLs."""
    return {
        "options": [
            {
                "mode": mode,
                "label": MODEL_OPTIONS[mode]["label"],
                "kind": MODEL_OPTIONS[mode]["kind"],
                "model": MODEL_OPTIONS[mode]["model"],
                "provider": MODEL_OPTIONS[mode]["provider"],
            }
            for mode in CANONICAL_MODEL_MODES
        ],
        "profiles": {
            profile: {
                "mode": status["mode"],
                "model": status["model"],
                "provider": status["provider"],
            }
            for profile, status in describe_all_profile_models().items()
        },
    }


def apply_profile_model_mode(profile: HermesProfile | str, mode: str) -> dict[str, Any]:
    if mode not in CANONICAL_MODEL_MODES:
        raise ValueError(f"Unsupported Hermes model mode: {mode}")
    canonical_profile = normalize_profile(profile)
    if current_model_mode(canonical_profile) == mode:
        return describe_profile_model(canonical_profile)
    return set_profile_model_mode(canonical_profile, mode)  # type: ignore[arg-type]


def infer_model_mode(*, provider_name: str = "", provider: str = "", model: str = "", base_url: str = "") -> ModelMode | None:
    normalized = " ".join([provider_name, provider, model, base_url]).lower()
    if not normalized.strip():
        return None
    if QWEN36_MODEL.lower() in normalized or QWEN36_PROVIDER in normalized or "qwen3.6" in normalized or "qwen36" in normalized:
        return "qwen36"
    if GEMMA4_MODEL.lower() in normalized or GEMMA4_PROVIDER in normalized or "gemma4" in normalized or "gemma 4" in normalized:
        return "gemma4"
    if NEMOTRON_MODEL.lower() in normalized or NEMOTRON_PROVIDER in normalized or "nemotron" in normalized or "nano omni" in normalized:
        return "nemotron"
    if MINIMAX_MODEL.lower() in normalized or MINIMAX_PROVIDER in normalized or "minimax" in normalized:
        return "minimax"
    if KIMI_MODEL.lower() in normalized or KIMI_PROVIDER in normalized or "kimi" in normalized or "moonshot" in normalized:
        return "kimi"
    if STEPFUN_MODEL.lower() in normalized or STEPFUN_PROVIDER in normalized or "stepfun" in normalized or "阶跃星辰" in normalized or "阶跃" in normalized:
        return "stepfun"
    return None


def maybe_handle_model_control(message: str, profile: HermesProfile | str) -> str | None:
    profile = normalize_profile(profile)
    text = message.strip()
    normalized = text.lower()
    if not CONTROL_HINT.search(text):
        return None

    if MODEL_LIST_RE.search(text) and not EXPLICIT_SWITCH_RE.search(text):
        return _model_list_reply()

    if STATUS_RE.search(text) and not EXPLICIT_SWITCH_RE.search(text):
        status = describe_profile_model(profile)
        return _status_reply(status)

    requested_mode = _requested_model_mode(normalized)
    if requested_mode and SWITCH_VERB_RE.search(text):
        status = set_profile_model_mode(profile, requested_mode)
        return _switch_reply(status)

    if STATUS_RE.search(text) or any(pattern in normalized for pattern in STATUS_PATTERNS):
        status = describe_profile_model(profile)
        return _status_reply(status)

    return None


def _requested_model_mode(normalized: str) -> ModelMode | None:
    if any(pattern in normalized for pattern in STEPFUN_PATTERNS):
        return "stepfun"
    if any(pattern in normalized for pattern in GEMMA4_PATTERNS):
        return "gemma4"
    if any(pattern in normalized for pattern in NEMOTRON_PATTERNS):
        return "nemotron"
    if any(pattern in normalized for pattern in QWEN36_PATTERNS):
        return "qwen36"
    if any(pattern in normalized for pattern in MINIMAX_PATTERNS):
        return "minimax"
    if any(pattern in normalized for pattern in KIMI_PATTERNS):
        return "kimi"
    if any(pattern in normalized for pattern in LOCAL_PATTERNS):
        return "qwen36"
    if any(pattern in normalized for pattern in CLOUD_PATTERNS):
        return "stepfun"
    return None


def _status_reply(status: dict[str, Any]) -> str:
    fallback_label = "、".join(status["fallbackModels"]) if status["fallbackModels"] else "未配置"
    temp_label = (
        f"，问答温度：{status['temperature']}"
        if status.get("temperature") is not None
        else ""
    )
    base_url_label = f"，Base URL：{status['baseUrl']}" if status.get("baseUrl") else ""
    return (
        f"{status['label']} 当前使用{status['modeLabel']}：{status['model']} "
        f"({status['provider']}){base_url_label}{temp_label}。备用顺序：{fallback_label}。"
    )


def _model_list_reply() -> str:
    labels = "、".join(MODEL_OPTIONS[mode]["label"] for mode in CANONICAL_MODEL_MODES)
    return f"可切换模型：{labels}。"


def _switch_reply(status: dict[str, Any]) -> str:
    fallback_label = "、".join(status["fallbackModels"]) if status["fallbackModels"] else "未配置"
    detail = ""
    if status.get("contextLength"):
        detail += f"上下文长度已配置为 {status['contextLength']} tokens；"
    if status.get("temperature") is not None:
        detail += f"问答温度已固定为 {status['temperature']}；"
    return (
        f"{status['label']} 已切换到{status['modeLabel']}：{status['model']}。"
        f"{detail}备用顺序：{fallback_label}。"
        "如果该智能体 gateway 正在运行，新的会话/下一轮请求会按新配置创建。"
    )
