from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml

from services.hermes_client import HermesProfile, normalize_profile
from services.path_config import HERMES_PROFILE_ROOTS

ModelMode = Literal["local", "qwen36", "gemma4", "cloud", "kimi", "minimax"]

GEMMA4_MODEL = "Gemma-4-26B-A4B-it-NVFP4"
GEMMA4_PROVIDER = "custom:gemma4-local"
GEMMA4_BASE_URL = "http://127.0.0.1:8006/v1"
GEMMA4_CONTEXT_LENGTH = 262144
GEMMA4_TEMPERATURE = 0.2
QWEN36_MODEL = "Qwen3.6-35B-A3B-FP8"
QWEN36_PROVIDER = "custom:qwen3.6-local"
QWEN36_BASE_URL = "http://127.0.0.1:8004/v1"
QWEN36_CONTEXT_LENGTH = 262144
QWEN36_TEMPERATURE = 0.2
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

MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    "local": {
        "label": "本地 Qwen3.6",
        "kind": "local",
        "provider_name": "Qwen3.6 Local",
        "provider": LOCAL_PROVIDER,
        "base_url": LOCAL_BASE_URL,
        "model": LOCAL_MODEL,
        "context_length": LOCAL_CONTEXT_LENGTH,
        "temperature": LOCAL_TEMPERATURE,
    },
    "qwen36": {
        "label": "本地 Qwen3.6",
        "kind": "local",
        "provider_name": "Qwen3.6 Local",
        "provider": QWEN36_PROVIDER,
        "base_url": QWEN36_BASE_URL,
        "model": QWEN36_MODEL,
        "context_length": QWEN36_CONTEXT_LENGTH,
        "temperature": QWEN36_TEMPERATURE,
    },
    "gemma4": {
        "label": "本地 Gemma4",
        "kind": "local",
        "provider_name": "Gemma4 Local",
        "provider": GEMMA4_PROVIDER,
        "base_url": GEMMA4_BASE_URL,
        "model": GEMMA4_MODEL,
        "context_length": GEMMA4_CONTEXT_LENGTH,
        "temperature": GEMMA4_TEMPERATURE,
    },
    "cloud": {
        "label": "云端 Minimax",
        "kind": "cloud",
        "provider": MINIMAX_PROVIDER,
        "model": MINIMAX_MODEL,
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
}
CANONICAL_MODEL_MODES: tuple[ModelMode, ...] = ("qwen36", "gemma4", "kimi", "minimax")
LOCAL_MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    key: MODEL_OPTIONS[key]
    for key in ("qwen36", "gemma4")
}
CLOUD_MODEL_OPTIONS: dict[ModelMode, dict[str, Any]] = {
    key: MODEL_OPTIONS[key]
    for key in ("kimi", "minimax")
}
PROFILE_CONFIGS: dict[HermesProfile, Path] = {
    "siq_assistant": HERMES_PROFILE_ROOTS["siq_assistant"] / "config.yaml",
    "siq_analysis": HERMES_PROFILE_ROOTS["siq_analysis"] / "config.yaml",
    "siq_factchecker": HERMES_PROFILE_ROOTS["siq_factchecker"] / "config.yaml",
    "siq_tracking": HERMES_PROFILE_ROOTS["siq_tracking"] / "config.yaml",
    "siq_legal": HERMES_PROFILE_ROOTS["siq_legal"] / "config.yaml",
}
PROFILE_ORDER: tuple[HermesProfile, ...] = tuple(PROFILE_CONFIGS.keys())
PROFILE_LABELS: dict[HermesProfile, str] = {
    "siq_assistant": "SIQ Assistant",
    "siq_analysis": "SIQ Analysis",
    "siq_factchecker": "SIQ Factchecker",
    "siq_tracking": "SIQ Tracking",
    "siq_legal": "SIQ Legal",
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
STATUS_PATTERNS = ("模型状态", "当前模型", "查看模型", "model status", "current model")
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
    r"(切换|切到|切回|使用|改用|模型|model|gemma|qwen|kimi|minimax|local|cloud|云端|本地)",
    re.IGNORECASE,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Hermes profile config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Hermes profile config is not a mapping: {path}")
    return data


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _ensure_local_provider(config: dict[str, Any]) -> None:
    providers = config.get("custom_providers")
    if not isinstance(providers, list):
        providers = []

    for mode in ("qwen36", "gemma4"):
        option = MODEL_OPTIONS[mode]
        provider_payload = {
            "name": option["provider_name"],
            "base_url": option["base_url"],
            "model": option["model"],
            "api_mode": "openai_chat",
            "context_length": option["context_length"],
            "temperature": option["temperature"],
            "models": {option["model"]: {"context_length": option["context_length"]}},
        }
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
        fallback_modes = ("minimax", "kimi", "gemma4")
    elif mode == "gemma4":
        fallback_modes = ("minimax", "kimi", "qwen36")
    elif mode == "kimi":
        fallback_modes = ("minimax", "qwen36", "gemma4")
    elif mode == "minimax":
        fallback_modes = ("kimi", "qwen36", "gemma4")
    else:
        fallback_modes = ("qwen36", "gemma4", "kimi", "minimax")

    chain: list[dict[str, Any]] = []
    current = _canonical_mode(mode)
    for fallback_mode in fallback_modes:
        if fallback_mode == current:
            continue
        option = MODEL_OPTIONS[fallback_mode]
        entry = {"provider": option["provider"], "model": option["model"]}
        if option.get("temperature") is not None:
            entry["temperature"] = option["temperature"]
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
    if option["kind"] == "local":
        model_config.update(
            {
                "default": option["model"],
                "provider": option["provider"],
                "base_url": option["base_url"],
                "api_mode": "openai_chat",
                "context_length": option["context_length"],
                "temperature": option["temperature"],
            }
        )
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

    config["model"] = model_config


def _canonical_mode(mode: ModelMode) -> ModelMode:
    if mode == "local":
        return "qwen36"
    if mode == "cloud":
        return "minimax"
    return mode


def current_model_mode(profile: HermesProfile | str) -> ModelMode:
    profile = normalize_profile(profile)
    config = _load_yaml(PROFILE_CONFIGS[profile])
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
    if "minimax" in provider.lower() or "minimax" in normalized_model:
        return "minimax"
    if "kimi" in provider.lower() or "kimi" in normalized_model:
        return "kimi"
    return "kimi"


def set_profile_model_mode(profile: HermesProfile | str, mode: ModelMode) -> dict[str, Any]:
    profile = normalize_profile(profile)
    path = PROFILE_CONFIGS[profile]
    config = _load_yaml(path)
    _ensure_local_provider(config)
    _set_model(config, mode)
    _ensure_model_fallback(config, mode)
    _ensure_tool_use_enforcement(config)
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
    path = PROFILE_CONFIGS[profile]
    config = _load_yaml(path)
    _ensure_local_provider(config)
    _ensure_model_fallback(config)
    _ensure_tool_use_enforcement(config)
    _save_yaml(path, config)


def describe_profile_model(profile: HermesProfile | str) -> dict[str, Any]:
    profile = normalize_profile(profile)
    config = _load_yaml(PROFILE_CONFIGS[profile])
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


def infer_model_mode(*, provider_name: str = "", provider: str = "", model: str = "", base_url: str = "") -> ModelMode | None:
    normalized = " ".join([provider_name, provider, model, base_url]).lower()
    if not normalized.strip():
        return None
    if QWEN36_MODEL.lower() in normalized or QWEN36_PROVIDER in normalized or "qwen3.6" in normalized or "qwen36" in normalized:
        return "qwen36"
    if GEMMA4_MODEL.lower() in normalized or GEMMA4_PROVIDER in normalized or "gemma4" in normalized or "gemma 4" in normalized:
        return "gemma4"
    if MINIMAX_MODEL.lower() in normalized or MINIMAX_PROVIDER in normalized or "minimax" in normalized:
        return "minimax"
    if KIMI_MODEL.lower() in normalized or KIMI_PROVIDER in normalized or "kimi" in normalized or "moonshot" in normalized:
        return "kimi"
    return None


def maybe_handle_model_control(message: str, profile: HermesProfile | str) -> str | None:
    profile = normalize_profile(profile)
    text = message.strip()
    normalized = text.lower()
    if not CONTROL_HINT.search(text):
        return None

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
    if any(pattern in normalized for pattern in GEMMA4_PATTERNS):
        return "gemma4"
    if any(pattern in normalized for pattern in QWEN36_PATTERNS):
        return "qwen36"
    if any(pattern in normalized for pattern in MINIMAX_PATTERNS):
        return "minimax"
    if any(pattern in normalized for pattern in KIMI_PATTERNS):
        return "kimi"
    if any(pattern in normalized for pattern in LOCAL_PATTERNS):
        return "qwen36"
    if any(pattern in normalized for pattern in CLOUD_PATTERNS):
        return "minimax"
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
