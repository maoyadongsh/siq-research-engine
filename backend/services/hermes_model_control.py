from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml

from services.hermes_client import HermesProfile

ModelMode = Literal["local", "cloud"]

HERMES_HOME = Path("/home/maoyd/.hermes")
LOCAL_MODEL = "Qwen3.6-35B-A3B-FP8"
LOCAL_PROVIDER = "custom:qwen3.6-local"
LOCAL_BASE_URL = "http://127.0.0.1:8004/v1"
LOCAL_CONTEXT_LENGTH = 262144
CLOUD_MODEL = "kimi-for-coding"
CLOUD_PROVIDER = "kimi-coding"
CLOUD_BASE_URL = "https://api.kimi.com/coding"
FALLBACK_PROVIDER = {"provider": LOCAL_PROVIDER, "model": LOCAL_MODEL}
PROFILE_CONFIGS: dict[HermesProfile, Path] = {
    "finsight_assistant": HERMES_HOME / "profiles/finsight_assistant/config.yaml",
    "analysis": HERMES_HOME / "profiles/finsight_analysis/config.yaml",
    "factchecker": HERMES_HOME / "profiles/finsight_factchecker/config.yaml",
    "tracking": HERMES_HOME / "profiles/finsight_tracking/config.yaml",
    "legal": HERMES_HOME / "profiles/finsight_legal/config.yaml",
}
PROFILE_LABELS: dict[HermesProfile, str] = {
    "finsight_assistant": "FinSight Assistant",
    "analysis": "FinSight Analysis",
    "factchecker": "FinSight Factchecker",
    "tracking": "FinSight Tracking",
    "legal": "FinSight Legal",
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
    "qwen3.6",
    "qwen",
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
STATUS_PATTERNS = ("模型状态", "当前模型", "查看模型", "model status", "current model")
CONTROL_HINT = re.compile(r"(切换|切到|切回|使用|改用|模型|model|qwen|kimi|local|cloud)", re.IGNORECASE)


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

    for entry in providers:
        if isinstance(entry, dict) and (
            entry.get("name") == "Qwen3.6 Local"
            or str(entry.get("base_url") or "").rstrip("/") == LOCAL_BASE_URL.rstrip("/")
        ):
            entry.update(
                {
                    "name": "Qwen3.6 Local",
                    "base_url": LOCAL_BASE_URL,
                    "model": LOCAL_MODEL,
                    "api_mode": "openai_chat",
                    "context_length": LOCAL_CONTEXT_LENGTH,
                    "models": {LOCAL_MODEL: {"context_length": LOCAL_CONTEXT_LENGTH}},
                }
            )
            break
    else:
        providers.append(
            {
                "name": "Qwen3.6 Local",
                "base_url": LOCAL_BASE_URL,
                "model": LOCAL_MODEL,
                "api_mode": "openai_chat",
                "context_length": LOCAL_CONTEXT_LENGTH,
                "models": {LOCAL_MODEL: {"context_length": LOCAL_CONTEXT_LENGTH}},
            }
        )

    config["custom_providers"] = providers


def _ensure_local_fallback(config: dict[str, Any]) -> None:
    fallbacks = config.get("fallback_providers")
    if not isinstance(fallbacks, list):
        fallbacks = []

    exists = any(
        isinstance(entry, dict)
        and entry.get("provider") == LOCAL_PROVIDER
        and entry.get("model") == LOCAL_MODEL
        for entry in fallbacks
    )
    if not exists:
        fallbacks.append(deepcopy(FALLBACK_PROVIDER))
    config["fallback_providers"] = fallbacks


def _set_model(config: dict[str, Any], mode: ModelMode) -> None:
    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}

    if mode == "local":
        model_config.update(
            {
                "default": LOCAL_MODEL,
                "provider": LOCAL_PROVIDER,
                "base_url": LOCAL_BASE_URL,
                "api_mode": "openai_chat",
                "context_length": LOCAL_CONTEXT_LENGTH,
            }
        )
    else:
        model_config.update(
            {
                "default": CLOUD_MODEL,
                "provider": CLOUD_PROVIDER,
                "base_url": CLOUD_BASE_URL,
            }
        )
        model_config.pop("api_mode", None)
        model_config.pop("context_length", None)

    config["model"] = model_config


def current_model_mode(profile: HermesProfile) -> ModelMode:
    config = _load_yaml(PROFILE_CONFIGS[profile])
    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model_config.get("provider") or "")
    model = str(model_config.get("default") or "")
    if provider == LOCAL_PROVIDER or model == LOCAL_MODEL:
        return "local"
    return "cloud"


def set_profile_model_mode(profile: HermesProfile, mode: ModelMode) -> dict[str, Any]:
    path = PROFILE_CONFIGS[profile]
    config = _load_yaml(path)
    _ensure_local_provider(config)
    _ensure_local_fallback(config)
    _set_model(config, mode)
    _save_yaml(path, config)
    return describe_profile_model(profile)


def ensure_profile_fallback(profile: HermesProfile) -> None:
    path = PROFILE_CONFIGS[profile]
    config = _load_yaml(path)
    _ensure_local_provider(config)
    _ensure_local_fallback(config)
    _save_yaml(path, config)


def describe_profile_model(profile: HermesProfile) -> dict[str, Any]:
    config = _load_yaml(PROFILE_CONFIGS[profile])
    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    return {
        "profile": profile,
        "label": PROFILE_LABELS[profile],
        "mode": current_model_mode(profile),
        "model": model_config.get("default") or "",
        "provider": model_config.get("provider") or "",
        "baseUrl": model_config.get("base_url") or "",
        "contextLength": model_config.get("context_length") or None,
        "fallback": FALLBACK_PROVIDER in (config.get("fallback_providers") or []),
    }


def maybe_handle_model_control(message: str, profile: HermesProfile) -> str | None:
    text = message.strip()
    normalized = text.lower()
    if not CONTROL_HINT.search(text):
        return None

    if any(pattern in normalized for pattern in LOCAL_PATTERNS):
        status = set_profile_model_mode(profile, "local")
        return (
            f"{status['label']} 已切换到本地模型：{LOCAL_MODEL}。"
            f"上下文长度已配置为 {LOCAL_CONTEXT_LENGTH} tokens；本地模型仍保留在 fallback 链中。"
            "如果该智能体 gateway 正在运行，新的会话/下一轮请求会按新配置创建。"
        )

    if any(pattern in normalized for pattern in CLOUD_PATTERNS):
        status = set_profile_model_mode(profile, "cloud")
        return (
            f"{status['label']} 已切回云端模型：{CLOUD_MODEL}。"
            f"本地 {LOCAL_MODEL} 仍作为备用模型保留。"
        )

    if any(pattern in normalized for pattern in STATUS_PATTERNS):
        status = describe_profile_model(profile)
        mode_label = "本地" if status["mode"] == "local" else "云端"
        fallback_label = "已配置" if status["fallback"] else "未配置"
        return (
            f"{status['label']} 当前使用{mode_label}模型：{status['model']} "
            f"({status['provider']})。本地备用模型：{fallback_label}。"
        )

    return None
