import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from services.hermes_model_control import describe_all_profile_models, infer_model_mode, set_all_profile_model_modes
from services.path_config import BACKEND_DATA_ROOT, BACKEND_ROOT

ProviderKey = Literal["cloud", "local"]


def _env_value(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


CONFIG_DIR = Path(
    os.environ.get("SIQ_CONFIG_DIR")
    or BACKEND_DATA_ROOT / ".siq"
)
CONFIG_PATH = CONFIG_DIR / "llm_settings.json"
LEGACY_CONFIG_PATHS = (
    BACKEND_ROOT / ".siq" / "llm_settings.json",
)
KIMI_PROVIDER = {
    "enabled": True,
    "providerName": "Hermes / Kimi",
    "baseUrl": _env_value("SIQ_KIMI_LLM_BASE_URL", "hermes://kimi-coding"),
    "apiKey": "",
    "model": _env_value("SIQ_KIMI_LLM_MODEL", "kimi-for-coding"),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 180,
    "chatTemplateKwargs": {},
}
MINIMAX_PROVIDER = {
    "enabled": True,
    "providerName": "Hermes / Minimax",
    "baseUrl": _env_value("SIQ_MINIMAX_LLM_BASE_URL", "hermes://minimax-cn"),
    "apiKey": "",
    "model": _env_value("SIQ_MINIMAX_LLM_MODEL", "MiniMax-M3"),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 180,
    "chatTemplateKwargs": {},
}
STEPFUN_PROVIDER = {
    "enabled": True,
    "providerName": "StepFun / Step-3.7 Flash",
    "baseUrl": _env_value("SIQ_STEPFUN_LLM_BASE_URL", "https://api.stepfun.com/v1"),
    "apiKey": _env_first(
        "SIQ_STEPFUN_LLM_API_KEY",
        "STEPFUN_API_KEY",
        "STEP_API_KEY",
    ),
    "model": _env_value("SIQ_STEPFUN_LLM_MODEL", "step-3.7-flash"),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 180,
    "chatTemplateKwargs": {},
}
LOCAL_GEMMA4_PROVIDER = {
    "enabled": True,
    "providerName": "本地 vLLM / Gemma4",
    "baseUrl": (
        _env_first(
            "SIQ_LOCAL_LLM_BASE_URL",
            "SIQ_GEMMA4_LLM_BASE_URL",
        )
        or "http://127.0.0.1:8006/v1"
    ),
    "apiKey": _env_first(
        "SIQ_LOCAL_LLM_API_KEY",
        "SIQ_GEMMA4_LLM_API_KEY",
    ),
    "model": (
        _env_first(
            "SIQ_LOCAL_LLM_MODEL",
            "SIQ_GEMMA4_LLM_MODEL",
        )
        or "Gemma-4-26B-A4B-it-NVFP4"
    ),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 600,
    "chatTemplateKwargs": {"enable_thinking": False},
}
LOCAL_QWEN_PROVIDER = {
    "enabled": True,
    "providerName": "本地 vLLM / Qwen3.6",
    "baseUrl": _env_value("SIQ_QWEN36_LLM_BASE_URL", "http://127.0.0.1:8004/v1"),
    "apiKey": _env_value("SIQ_QWEN36_LLM_API_KEY"),
    "model": _env_value("SIQ_QWEN36_LLM_MODEL", "Qwen3.6-35B-A3B-FP8"),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 180,
    "chatTemplateKwargs": {"enable_thinking": False},
}
LOCAL_MODEL_PRESETS = {
    "qwen36": deepcopy(LOCAL_QWEN_PROVIDER),
    "gemma4": deepcopy(LOCAL_GEMMA4_PROVIDER),
}
CLOUD_MODEL_PRESETS = {
    "stepfun": deepcopy(STEPFUN_PROVIDER),
    "kimi": deepcopy(KIMI_PROVIDER),
    "minimax": deepcopy(MINIMAX_PROVIDER),
}
LEGACY_LOCAL_BASE_URLS = {"http://localhost:8000/v1", "http://127.0.0.1:8000/v1"}
LEGACY_LOCAL_MODELS = {"local-model", ""}

DEFAULT_SETTINGS: dict[str, Any] = {
    "activeProvider": "local",
    "providers": {
        "cloud": deepcopy(MINIMAX_PROVIDER),
        "local": deepcopy(LOCAL_QWEN_PROVIDER),
    },
    "updatedAt": None,
}


class LLMProviderUpdate(BaseModel):
    enabled: bool = True
    providerName: str = Field(default="", max_length=80)
    baseUrl: str = Field(default="", max_length=300)
    apiKey: str | None = Field(default=None, max_length=500)
    clearApiKey: bool = False
    model: str = Field(default="", max_length=120)
    temperature: float = Field(default=0.2, ge=0, le=2)
    maxTokens: int = Field(default=8192, ge=1, le=262144)
    timeoutSeconds: int = Field(default=60, ge=5, le=600)


class LLMSettingsUpdate(BaseModel):
    activeProvider: ProviderKey = "local"
    providers: dict[ProviderKey, LLMProviderUpdate]


class LLMTestRequest(BaseModel):
    provider: ProviderKey
    message: str = Field(default="请只回复 OK", max_length=2000)
    config: LLMProviderUpdate | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _endpoint(base_url: str, suffix: str) -> str:
    base = _normalize_base_url(base_url)
    if not base:
        raise ValueError("baseUrl is required")
    return f"{base}/{suffix.lstrip('/')}"


def _sanitize_provider(provider: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(provider)
    cleaned["providerName"] = str(cleaned.get("providerName") or "").strip()
    cleaned["baseUrl"] = _normalize_base_url(str(cleaned.get("baseUrl") or ""))
    cleaned["model"] = str(cleaned.get("model") or "").strip()
    cleaned["apiKey"] = str(cleaned.get("apiKey") or "").strip()
    cleaned["enabled"] = bool(cleaned.get("enabled", True))
    cleaned["temperature"] = float(cleaned.get("temperature", 0.2))
    cleaned["maxTokens"] = int(cleaned.get("maxTokens", 4096))
    cleaned["timeoutSeconds"] = int(cleaned.get("timeoutSeconds", 60))
    chat_template_kwargs = cleaned.get("chatTemplateKwargs")
    cleaned["chatTemplateKwargs"] = chat_template_kwargs if isinstance(chat_template_kwargs, dict) else {}
    return cleaned


def _apply_local_model_preset_extras(provider: dict[str, Any]) -> dict[str, Any]:
    model = str(provider.get("model") or "").strip()
    for preset in LOCAL_MODEL_PRESETS.values():
        if model == preset["model"]:
            provider["chatTemplateKwargs"] = deepcopy(preset.get("chatTemplateKwargs") or {})
            break
    return provider


def _apply_cloud_model_preset_extras(provider: dict[str, Any]) -> dict[str, Any]:
    model = str(provider.get("model") or "").strip()
    for preset in CLOUD_MODEL_PRESETS.values():
        if model == preset["model"]:
            provider["chatTemplateKwargs"] = deepcopy(preset.get("chatTemplateKwargs") or {})
            if str(preset.get("baseUrl") or "").startswith("hermes://"):
                provider["apiKey"] = ""
            break
    return provider


def _migrate_legacy_local_provider(settings: dict[str, Any]) -> None:
    local = settings["providers"].get("local", {})
    base_url = _normalize_base_url(str(local.get("baseUrl") or ""))
    model = str(local.get("model") or "").strip()
    if base_url in LEGACY_LOCAL_BASE_URLS and model in LEGACY_LOCAL_MODELS:
        api_key = str(local.get("apiKey") or "").strip()
        settings["providers"]["local"] = deepcopy(LOCAL_QWEN_PROVIDER)
        settings["providers"]["local"]["apiKey"] = api_key


def _minimax_provider_preserving_tunables(provider: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(MINIMAX_PROVIDER)
    for key in ("enabled", "temperature", "maxTokens", "timeoutSeconds"):
        if key in provider:
            migrated[key] = provider[key]
    return migrated


def _migrate_cloud_provider_to_minimax_default(settings: dict[str, Any]) -> None:
    cloud = settings["providers"].get("cloud", {})
    base_url = _normalize_base_url(str(cloud.get("baseUrl") or ""))
    model = str(cloud.get("model") or "").strip()
    provider_name = str(cloud.get("providerName") or "").strip()
    if not base_url and not model:
        settings["providers"]["cloud"] = _minimax_provider_preserving_tunables(cloud)
        return
    if base_url == _normalize_base_url(KIMI_PROVIDER["baseUrl"]) and model == KIMI_PROVIDER["model"]:
        settings["providers"]["cloud"] = _minimax_provider_preserving_tunables(cloud)
        return
    if provider_name in {"云端 OpenAI Compatible", "Hermes / Kimi"} and not model:
        settings["providers"]["cloud"] = _minimax_provider_preserving_tunables(cloud)


def _public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    public = dict(provider)
    public.pop("apiKey", None)
    public["hasApiKey"] = bool(provider.get("apiKey"))
    return public


def _public_local_model_presets() -> dict[str, Any]:
    return {
        key: _public_provider(_sanitize_provider(deepcopy(value)))
        for key, value in LOCAL_MODEL_PRESETS.items()
    }


def _public_cloud_model_presets() -> dict[str, Any]:
    return {
        key: _public_provider(_sanitize_provider(deepcopy(value)))
        for key, value in CLOUD_MODEL_PRESETS.items()
    }


def _settings_source_path() -> Path:
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    for path in LEGACY_CONFIG_PATHS:
        if path.exists():
            return path
    return CONFIG_PATH


def _hermes_mode_for_provider(provider: dict[str, Any]) -> str | None:
    mode = infer_model_mode(
        provider_name=str(provider.get("providerName") or ""),
        model=str(provider.get("model") or ""),
        base_url=str(provider.get("baseUrl") or ""),
    )
    return mode


def _sync_hermes_model_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
    active_provider_key = settings.get("activeProvider", "local")
    provider = (settings.get("providers") or {}).get(active_provider_key) or {}
    if not isinstance(provider, dict) or not provider.get("enabled", True):
        return None

    mode = _hermes_mode_for_provider(provider)
    if not mode:
        return None
    return set_all_profile_model_modes(mode)


def load_llm_settings(include_secrets: bool = False) -> dict[str, Any]:
    settings = deepcopy(DEFAULT_SETTINGS)
    source_path = _settings_source_path()
    if source_path.exists():
        try:
            saved = json.loads(source_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings = _deep_merge(settings, saved)
        except (OSError, json.JSONDecodeError):
            pass

    for key in ("cloud", "local"):
        settings["providers"][key] = _sanitize_provider(settings["providers"].get(key, {}))
    settings["providers"]["local"] = _apply_local_model_preset_extras(settings["providers"]["local"])
    settings["providers"]["cloud"] = _apply_cloud_model_preset_extras(settings["providers"]["cloud"])

    _migrate_legacy_local_provider(settings)
    _migrate_cloud_provider_to_minimax_default(settings)

    if settings.get("activeProvider") not in ("cloud", "local"):
        settings["activeProvider"] = "local"

    if include_secrets:
        return settings

    public = deepcopy(settings)
    public["providers"] = {
        key: _public_provider(value)
        for key, value in settings["providers"].items()
    }
    public["localModelPresets"] = _public_local_model_presets()
    public["cloudModelPresets"] = _public_cloud_model_presets()
    try:
        public["hermesProfiles"] = describe_all_profile_models()
    except Exception:
        public["hermesProfiles"] = {}
    return public


def save_llm_settings(update: LLMSettingsUpdate) -> dict[str, Any]:
    current = load_llm_settings(include_secrets=True)
    next_settings = deepcopy(current)
    next_settings["activeProvider"] = update.activeProvider

    for key, provider_update in update.providers.items():
        incoming = provider_update.model_dump()
        provider = deepcopy(current["providers"].get(key, DEFAULT_SETTINGS["providers"][key]))
        for field in ("enabled", "providerName", "baseUrl", "model", "temperature", "maxTokens", "timeoutSeconds"):
            provider[field] = incoming[field]
        if incoming.get("clearApiKey"):
            provider["apiKey"] = ""
        elif incoming.get("apiKey") is not None and incoming.get("apiKey", "").strip():
            provider["apiKey"] = incoming["apiKey"].strip()
        provider = _sanitize_provider(provider)
        if key == "local":
            provider = _apply_local_model_preset_extras(provider)
        elif key == "cloud":
            provider = _apply_cloud_model_preset_extras(provider)
        next_settings["providers"][key] = provider

    hermes_sync = _sync_hermes_model_settings(next_settings)
    next_settings["updatedAt"] = datetime.now(timezone.utc).isoformat()
    if hermes_sync:
        next_settings["lastHermesSync"] = hermes_sync
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(next_settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return load_llm_settings(include_secrets=False)


def _provider_from_request(request: LLMTestRequest) -> dict[str, Any]:
    saved = load_llm_settings(include_secrets=True)
    provider = deepcopy(saved["providers"][request.provider])
    if request.config is not None:
        incoming = request.config.model_dump()
        for field in ("enabled", "providerName", "baseUrl", "model", "temperature", "maxTokens", "timeoutSeconds"):
            provider[field] = incoming[field]
        if incoming.get("clearApiKey"):
            provider["apiKey"] = ""
        elif incoming.get("apiKey") is not None and incoming.get("apiKey", "").strip():
            provider["apiKey"] = incoming["apiKey"].strip()
    provider = _sanitize_provider(provider)
    if request.provider == "local":
        provider = _apply_local_model_preset_extras(provider)
    elif request.provider == "cloud":
        provider = _apply_cloud_model_preset_extras(provider)
    return provider


async def test_llm_provider(request: LLMTestRequest) -> dict[str, Any]:
    provider = _provider_from_request(request)
    if provider["baseUrl"].startswith("hermes://"):
        mode = _hermes_mode_for_provider(provider)
        if not mode:
            return {
                "ok": False,
                "provider": request.provider,
                "statusCode": None,
                "latencyMs": 0,
                "model": provider["model"],
                "message": f"无法识别 Hermes 模型预设：{provider['model']}",
            }
        profiles = describe_all_profile_models()
        matching_profiles = [item for item in profiles.values() if item.get("mode") == mode]
        state = "当前已同步" if len(matching_profiles) == len(profiles) else "保存后会同步"
        return {
            "ok": True,
            "provider": request.provider,
            "statusCode": None,
            "latencyMs": 0,
            "model": provider["model"],
            "message": f"Hermes 预设有效：{provider['model']}；{state}。实际调用使用 Hermes credential pool。",
        }
    if not provider["baseUrl"]:
        return {"ok": False, "message": "请先填写 Base URL", "provider": request.provider}
    if not provider["model"]:
        return {"ok": False, "message": "请先填写模型名称", "provider": request.provider}

    headers = {"Content-Type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"

    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": "You are a connectivity checker. Reply concisely."},
            {"role": "user", "content": request.message},
        ],
        "temperature": provider["temperature"],
        "max_tokens": min(provider["maxTokens"], 64),
        "stream": False,
        "chat_template_kwargs": provider.get("chatTemplateKwargs") or {},
    }

    started = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=float(provider["timeoutSeconds"])) as client:
            resp = await client.post(
                _endpoint(provider["baseUrl"], "/chat/completions"),
                headers=headers,
                json=payload,
            )
            elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "provider": request.provider,
                    "statusCode": resp.status_code,
                    "latencyMs": elapsed_ms,
                    "message": resp.text[:600],
                }
            data = resp.json()
            content = ""
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = str(message.get("content") or choices[0].get("text") or "")
            return {
                "ok": True,
                "provider": request.provider,
                "statusCode": resp.status_code,
                "latencyMs": elapsed_ms,
                "model": provider["model"],
                "message": content.strip()[:600] or "连接成功",
            }
    except Exception as exc:  # noqa: BLE001 - surface connectivity errors to the UI
        return {
            "ok": False,
            "provider": request.provider,
            "message": str(exc)[:600],
        }
