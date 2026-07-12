#!/usr/bin/env python3
"""Run local-LLM semantic enrichment for company wiki reports.

The rule extractor remains the source of deterministic facts, segments, and
evidence. This script reads that rule layer, asks the configured local
OpenAI-compatible model for higher-level financial-report semantics, validates
every returned item against known segment/evidence ids, and writes a separate
`semantic/llm/<report_id>/` layer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_semantic_profiles import (
    TARGET_SEGMENT_TYPES as PROFILE_TARGET_SEGMENT_TYPES,
    profile_for_market,
    title_boost_keywords_for_market,
)

PROMPT_VERSION = "financial_report_llm_semantic_v2_market_profiles"
DOCUMENT_LINK_PROMPT_VERSION = "financial_report_document_link_semantic_v1"
ENRICHMENT_VERSION = "llm_semantic_enrichment_v1"
ENRICHMENT_OUTPUTS = (
    "enrichment.json",
    "business_profile.json",
    "claims.json",
    "risks.json",
    "events.json",
    "review_queue.json",
    "extraction_log.json",
)
DOCUMENT_LINK_OUTPUTS = (
    "document_links.json",
    "document_links_review_queue.json",
    "document_links_extraction_log.json",
)
DEFAULT_BASE_URL = "http://127.0.0.1:8004/v1"
DEFAULT_MODEL = "Qwen3.6-35B-A3B-FP8"
DEFAULT_CONFIG_DIR = Path("/home/maoyd/finsight/backend/.finsight")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIQ_CONFIG_DIR = Path("/home/maoyd/siq-research-engine/data/backend/.siq")
SIQ_LEGACY_CONFIG_DIR = Path("/home/maoyd/siq-research-engine/apps/api/.siq")

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
HERMES_ENV_PREFIXES = {
    "siq_assistant": "ASSISTANT",
    "siq_analysis": "ANALYSIS",
    "siq_factchecker": "FACTCHECKER",
    "siq_tracking": "TRACKING",
    "siq_legal": "LEGAL",
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
    "cloud": "siq_analysis",
    "local": "siq_analysis",
}
HERMES_PROFILE_MODELS = {
    "siq_assistant": "siq_assistant",
    "siq_analysis": "siq_analysis",
    "siq_factchecker": "siq_factchecker",
    "siq_tracking": "siq_tracking",
    "siq_legal": "siq_legal",
}

TARGET_SEGMENT_TYPES = {
    "business_overview",
    "management_discussion",
    "industry_analysis",
    "segment_performance",
    "product_service",
    "region_market",
    "customer_supplier",
    "rd_innovation",
    "capex_projects",
    "risk_factors",
    "major_events",
    "corporate_governance",
    "esg_social_responsibility",
} | PROFILE_TARGET_SEGMENT_TYPES

TITLE_BOOST_KEYWORDS = [
    "业务",
    "经营",
    "行业",
    "风险",
    "研发",
    "客户",
    "供应商",
    "产品",
    "项目",
    "产能",
    "海外",
    "战略",
    "重大",
    "诉讼",
    "担保",
    "分红",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remove_raw_files(out_dir: Path, names: tuple[str, ...]) -> None:
    raw_dir = out_dir / "raw"
    for name in names:
        try:
            (raw_dir / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    try:
        raw_dir.rmdir()
    except OSError:
        pass


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_env_file_defaults(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
            value = parsed[0] if parsed else ""
        except ValueError:
            value = raw_value.strip().strip('"').strip("'")
        os.environ[key] = value


def load_project_env_defaults() -> None:
    explicit = os.environ.get("SIQ_ENV_FILE")
    candidates = [Path(explicit)] if explicit else []
    candidates.extend([
        PROJECT_ROOT / "infra" / "env" / "local.env",
        PROJECT_ROOT / "env" / "backend.env",
    ])
    for candidate in candidates:
        _load_env_file_defaults(candidate)


def _env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def normalize_hermes_profile(profile: Any) -> str:
    return HERMES_PROFILE_ALIASES.get(str(profile or "").strip(), "siq_analysis")


def hermes_runs_url(profile: str) -> str:
    profile = normalize_hermes_profile(profile)
    env_prefix = HERMES_ENV_PREFIXES[profile]
    explicit = _env_value(
        f"SIQ_HERMES_{env_prefix}_RUNS_URL",
        f"HERMES_{env_prefix}_RUNS_URL",
        "SIQ_LLM_SEMANTIC_HERMES_RUNS_URL",
        "FINSIGHT_LLM_SEMANTIC_HERMES_RUNS_URL",
    )
    if explicit:
        return explicit.rstrip("/")
    host = _env_value(f"SIQ_HERMES_{env_prefix}_HOST", f"HERMES_{env_prefix}_HOST") or "127.0.0.1"
    raw_port = _env_value(f"SIQ_HERMES_{env_prefix}_PORT", f"HERMES_{env_prefix}_PORT")
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


def hermes_profile_model(profile: str) -> str:
    profile = normalize_hermes_profile(profile)
    env_prefix = HERMES_ENV_PREFIXES[profile]
    explicit = _env_value(f"SIQ_HERMES_{env_prefix}_MODEL", f"HERMES_{env_prefix}_MODEL")
    if explicit:
        return explicit
    profiles_root = Path(
        _env_value("SIQ_HERMES_PROFILES_ROOT", "HERMES_PROFILES_ROOT")
        or Path(_env_value("SIQ_HERMES_HOME", "HERMES_HOME") or PROJECT_ROOT / "data" / "hermes" / "home") / "profiles"
    ).expanduser()
    model = HERMES_PROFILE_MODELS[profile]
    if (profiles_root / model / "config.yaml").exists():
        return model
    return profile


def infer_hermes_mode(provider: dict[str, Any]) -> str:
    text = " ".join(
        str(provider.get(key) or "")
        for key in ("providerName", "provider", "model", "baseUrl")
    ).lower()
    if "minimax" in text or "miniMax-m3".lower() in text:
        return "minimax"
    if "kimi" in text or "moonshot" in text:
        return "kimi"
    if "stepfun" in text or "step-3.7" in text or "阶跃" in text:
        return "stepfun"
    if "gemma4" in text or "gemma-4" in text:
        return "gemma4"
    if "qwen3.6" in text or "qwen36" in text:
        return "qwen36"
    return ""


def load_local_provider() -> dict[str, Any]:
    load_project_env_defaults()
    provider = {
        "enabled": True,
        "providerName": "本地 vLLM / Qwen3.6",
        "baseUrl": DEFAULT_BASE_URL,
        "apiKey": "",
        "model": DEFAULT_MODEL,
        "temperature": 0.2,
        "maxTokens": 8192,
        "timeoutSeconds": 180,
        "chatTemplateKwargs": {"enable_thinking": False},
    }
    config_candidates: list[Path] = []
    for env_name in ("FINSIGHT_CONFIG_DIR", "SIQ_CONFIG_DIR"):
        if os.environ.get(env_name):
            config_candidates.append(Path(os.environ[env_name]) / "llm_settings.json")
    config_candidates.extend([
        SIQ_CONFIG_DIR / "llm_settings.json",
        SIQ_LEGACY_CONFIG_DIR / "llm_settings.json",
        DEFAULT_CONFIG_DIR / "llm_settings.json",
    ])
    saved = {}
    for config_path in config_candidates:
        candidate = read_json(config_path, {})
        if isinstance(candidate, dict) and candidate:
            saved = candidate
            break
    if isinstance(saved, dict):
        providers = saved.get("providers") or {}
        active_key = str(saved.get("activeProvider") or "local")
        active_provider = providers.get(active_key) if isinstance(providers, dict) else None
        if not isinstance(active_provider, dict) or not active_provider.get("enabled", True):
            active_provider = (providers.get("local") or {}) if isinstance(providers, dict) else {}
        if isinstance(active_provider, dict):
            provider = deep_merge(provider, active_provider)

    if os.environ.get("FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"):
        provider["baseUrl"] = os.environ["FINSIGHT_LLM_SEMANTIC_PROVIDER_BASE_URL"]
    elif os.environ.get("FINSIGHT_LOCAL_LLM_BASE_URL"):
        provider["baseUrl"] = os.environ["FINSIGHT_LOCAL_LLM_BASE_URL"]
    if "FINSIGHT_LLM_SEMANTIC_API_KEY" in os.environ:
        provider["apiKey"] = os.environ["FINSIGHT_LLM_SEMANTIC_API_KEY"]
    elif os.environ.get("FINSIGHT_LOCAL_LLM_API_KEY"):
        provider["apiKey"] = os.environ["FINSIGHT_LOCAL_LLM_API_KEY"]
    if os.environ.get("FINSIGHT_LLM_SEMANTIC_MODEL"):
        provider["model"] = os.environ["FINSIGHT_LLM_SEMANTIC_MODEL"]
    elif os.environ.get("FINSIGHT_LOCAL_LLM_MODEL"):
        provider["model"] = os.environ["FINSIGHT_LOCAL_LLM_MODEL"]
    if os.environ.get("FINSIGHT_LLM_SEMANTIC_TIMEOUT"):
        provider["timeoutSeconds"] = int(os.environ["FINSIGHT_LLM_SEMANTIC_TIMEOUT"])
    if os.environ.get("FINSIGHT_LLM_SEMANTIC_MAX_TOKENS"):
        provider["maxTokens"] = int(os.environ["FINSIGHT_LLM_SEMANTIC_MAX_TOKENS"])
    if os.environ.get("FINSIGHT_LLM_SEMANTIC_TEMPERATURE"):
        provider["temperature"] = float(os.environ["FINSIGHT_LLM_SEMANTIC_TEMPERATURE"])
    if os.environ.get("FINSIGHT_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"):
        try:
            chat_template_kwargs = json.loads(os.environ["FINSIGHT_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"])
            if isinstance(chat_template_kwargs, dict):
                provider["chatTemplateKwargs"] = chat_template_kwargs
        except json.JSONDecodeError:
            pass

    provider["baseUrl"] = str(provider.get("baseUrl") or "").strip().rstrip("/")
    provider["model"] = str(provider.get("model") or "").strip()
    provider["apiKey"] = str(provider.get("apiKey") or "").strip()
    provider["temperature"] = float(provider.get("temperature", 0.2))
    provider["maxTokens"] = min(int(provider.get("maxTokens") or 8192), 32768)
    provider["timeoutSeconds"] = int(provider.get("timeoutSeconds") or 180)
    if not isinstance(provider.get("chatTemplateKwargs"), dict):
        provider["chatTemplateKwargs"] = {"enable_thinking": False}
    if provider["baseUrl"].startswith("hermes://"):
        provider["transport"] = "hermes_runs"
        provider["hermesProfile"] = normalize_hermes_profile(
            os.environ.get("FINSIGHT_LLM_SEMANTIC_HERMES_PROFILE")
            or os.environ.get("SIQ_LLM_SEMANTIC_HERMES_PROFILE")
            or provider.get("hermesProfile")
            or "siq_analysis"
        )
        provider["hermesRunsUrl"] = hermes_runs_url(provider["hermesProfile"])
        provider["hermesMode"] = (
            str(os.environ.get("FINSIGHT_LLM_SEMANTIC_HERMES_MODE") or os.environ.get("SIQ_LLM_SEMANTIC_HERMES_MODE") or "").strip()
            or infer_hermes_mode(provider)
        )
        provider["model"] = hermes_profile_model(provider["hermesProfile"])
    else:
        provider["transport"] = "openai_chat"
    return provider


def public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    visible = dict(provider)
    visible.pop("apiKey", None)
    visible["hasApiKey"] = bool(provider.get("apiKey"))
    return visible


def endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def hermes_auth_header() -> str:
    load_project_env_defaults()
    token = str(os.environ.get("HERMES_API_KEY") or os.environ.get("HERMES_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Hermes API key is not configured: set HERMES_API_KEY or HERMES_TOKEN")
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def hermes_create_run(provider: dict[str, Any], input_text: str) -> str:
    runs_url = str(provider.get("hermesRunsUrl") or "").strip().rstrip("/")
    if not runs_url:
        raise RuntimeError("Hermes runs URL is empty")
    body = {
        "model": provider.get("model") or provider.get("hermesProfile") or "siq_analysis",
        "input": input_text,
        "conversation_history": [],
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        runs_url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": hermes_auth_header()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(provider.get("timeoutSeconds") or 180)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"Hermes run create HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Hermes run create failed: {exc}") from exc
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError(f"Hermes run create returned no run_id: {payload}")
    return run_id


def hermes_collect_run(provider: dict[str, Any], run_id: str) -> tuple[dict[str, Any], str]:
    runs_url = str(provider.get("hermesRunsUrl") or "").strip().rstrip("/")
    if not runs_url:
        raise RuntimeError("Hermes runs URL is empty")
    events_url = f"{runs_url}/{run_id}/events"
    req = urllib.request.Request(events_url, headers={"Authorization": hermes_auth_header()}, method="GET")
    chunks: list[str] = []
    final_text = ""
    final_event: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req, timeout=float(provider.get("timeoutSeconds") or 180)) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                event_type = event.get("event")
                if event_type == "message.delta" and event.get("delta"):
                    chunks.append(str(event["delta"]))
                elif event_type in {"run.completed", "run.failed", "run.cancelled"}:
                    final_event = event
                    final_text = str(event.get("output") or "")
                    if event_type != "run.completed":
                        raise RuntimeError(f"Hermes {event_type}: {final_text or event}")
                    break
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"Hermes run events HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Hermes run events failed: {exc}") from exc
    content = (final_text or "".join(chunks)).strip()
    if not content:
        raise RuntimeError("Hermes returned empty content")
    return {
        "transport": "hermes_runs",
        "run_id": run_id,
        "final_event": final_event,
        "content": content,
    }, content


def call_hermes_completion(provider: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], str]:
    input_text = f"{system.strip()}\n\n{user.strip()}"
    run_id = hermes_create_run(provider, input_text)
    return hermes_collect_run(provider, run_id)


def trim_text(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def slug(value: Any, fallback: str = "item") -> str:
    text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(value or "")).strip("_")
    return text[:64] or fallback


def compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence.get("evidence_id"),
        "source_type": evidence.get("source_type"),
        "source_file": evidence.get("source_file"),
        "quote": trim_text(evidence.get("quote"), 360),
        "pdf_page_number": evidence.get("pdf_page_number"),
        "md_line_start": evidence.get("md_line_start"),
        "md_line_end": evidence.get("md_line_end"),
        "table_index": evidence.get("table_index"),
    }


def resolve_source_path(company_dir: Path, report_dir: Path, source_file: Any) -> Path | None:
    source = str(source_file or "").strip()
    if not source:
        return None
    candidates = [
        company_dir / source,
        report_dir / source,
        report_dir / source.removeprefix(f"reports/{report_dir.name}/"),
        report_dir / "sections" / source,
        report_dir / "parser" / source,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def line_window(path: Path | None, start: Any, end: Any, *, pad: int = 2, max_chars: int = 1200) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    try:
        line_start = max(1, int(start or 1) - pad)
        line_end = min(len(lines), int(end or start or line_start) + pad)
    except (TypeError, ValueError):
        line_start = 1
        line_end = min(len(lines), 12)
    excerpt = "\n".join(lines[line_start - 1:line_end]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "..."
    return excerpt


def segment_context_excerpt(
    company_dir: Path | None,
    report_dir: Path | None,
    segment: dict[str, Any],
    evidence_ids: list[str],
    evidence_map: dict[str, dict[str, Any]],
) -> str:
    if company_dir is None or report_dir is None:
        return ""
    for evidence_id in evidence_ids:
        evidence = evidence_map.get(evidence_id) or {}
        path = resolve_source_path(company_dir, report_dir, evidence.get("source_file"))
        excerpt = line_window(path, evidence.get("md_line_start"), evidence.get("md_line_end"), pad=2, max_chars=1400)
        if excerpt:
            return excerpt
    path = report_dir / "report.md"
    return line_window(path, segment.get("md_line_start"), segment.get("md_line_end"), pad=2, max_chars=1400)


def compact_segment(
    segment: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
    company_dir: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    evidence_ids = [eid for eid in segment.get("evidence_ids") or [] if eid in evidence_map]
    return {
        "segment_id": segment.get("segment_id"),
        "segment_type": segment.get("segment_type"),
        "title": trim_text(segment.get("title"), 160),
        "summary": trim_text(segment.get("summary"), 900),
        "keywords": segment.get("keywords") or [],
        "importance": segment.get("importance"),
        "md_line_start": segment.get("md_line_start"),
        "md_line_end": segment.get("md_line_end"),
        "pdf_page_start": segment.get("pdf_page_start"),
        "pdf_page_end": segment.get("pdf_page_end"),
        "tables": segment.get("tables") or [],
        "images": segment.get("images") or [],
        "evidence_ids": evidence_ids,
        "evidence": [compact_evidence(evidence_map[eid]) for eid in evidence_ids[:4]],
        "source_context_excerpt": segment_context_excerpt(company_dir, report_dir, segment, evidence_ids, evidence_map),
    }


def compact_fact(fact: dict[str, Any]) -> dict[str, Any]:
    obj = fact.get("object") if isinstance(fact.get("object"), dict) else {}
    subject = fact.get("subject") if isinstance(fact.get("subject"), dict) else {}
    return {
        "fact_id": fact.get("fact_id"),
        "fact_type": fact.get("fact_type"),
        "subject": subject.get("name"),
        "predicate": fact.get("predicate"),
        "object": obj.get("name"),
        "metric_key": obj.get("metric_key"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "period": fact.get("period"),
        "evidence_ids": fact.get("evidence_ids") or [],
    }


def compact_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": claim.get("claim_id"),
        "claim_type": claim.get("claim_type"),
        "statement": trim_text(claim.get("statement"), 220),
        "stance": claim.get("stance"),
        "confidence": claim.get("confidence"),
        "evidence_ids": claim.get("evidence_ids") or [],
    }


def compact_document_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": node.get("kind"),
        "name": node.get("name"),
        "title": node.get("title"),
        "note_ref": node.get("note_ref"),
        "note_title": node.get("note_title"),
        "line": node.get("line") or node.get("md_line"),
        "pdf_page_number": node.get("pdf_page_number"),
        "table_index": node.get("table_index"),
        "heading": trim_text(node.get("heading"), 120),
        "preview": trim_text(node.get("preview"), 420),
    }


def compact_document_link(link: dict[str, Any]) -> dict[str, Any]:
    relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
    return {
        "document_link_id": link.get("document_link_id"),
        "link_type": link.get("link_type"),
        "source": compact_document_node(link.get("source") or {}),
        "target": compact_document_node(link.get("target") or {}),
        "rule_relation": {
            "method": relation.get("method"),
            "semantic_relation": relation.get("semantic_relation"),
            "confidence": relation.get("confidence"),
            "amount_check_status": relation.get("amount_check_status"),
        },
        "evidence_ids": link.get("evidence_ids") or [],
        "confidence": link.get("confidence"),
        "needs_review": link.get("needs_review"),
    }


def compact_note_link(link: dict[str, Any]) -> dict[str, Any]:
    statement = link.get("statement") if isinstance(link.get("statement"), dict) else {}
    note = link.get("note") if isinstance(link.get("note"), dict) else {}
    linkage = link.get("linkage") if isinstance(link.get("linkage"), dict) else {}
    amount_check = linkage.get("amount_check") if isinstance(linkage.get("amount_check"), dict) else {}
    return {
        "note_link_id": link.get("note_link_id"),
        "statement": {
            "item": statement.get("item"),
            "alias": statement.get("alias"),
            "note_ref": statement.get("note_ref"),
            "line": statement.get("line"),
            "pdf_page_number": statement.get("pdf_page_number"),
            "table_index": statement.get("table_index"),
        },
        "note": {
            "title": note.get("title"),
            "ref": note.get("ref"),
            "line": note.get("line"),
            "pdf_page_number": note.get("pdf_page_number"),
        },
        "linkage": {
            "method": linkage.get("method"),
            "confidence": linkage.get("confidence"),
            "precision_level": linkage.get("precision_level"),
            "amount_check_status": amount_check.get("status"),
        },
        "evidence_ids": link.get("evidence_ids") or [],
        "needs_review": link.get("needs_review"),
    }


def compact_table_relation(relation: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation_id": relation.get("relation_id"),
        "relation_type": relation.get("relation_type"),
        "merge_status": relation.get("merge_status"),
        "confidence": relation.get("confidence") or relation.get("merge_confidence"),
        "page_numbers": relation.get("page_numbers") or [],
        "from_table_index": relation.get("from_table_index"),
        "to_table_index": relation.get("to_table_index"),
        "from_page_number": relation.get("from_page_number"),
        "to_page_number": relation.get("to_page_number"),
        "reasons": (relation.get("reasons") or relation.get("merge_reasons") or [])[:6],
    }


def build_full_file_context(
    company_dir: Path,
    report_dir: Path,
    semantic_dir: Path,
    selected_segments: list[dict[str, Any]],
    allowed_evidence_ids: set[str],
) -> dict[str, Any]:
    selected_segment_ids = {item.get("segment_id") for item in selected_segments if item.get("segment_id")}
    retrieval_payload = read_json(semantic_dir / "retrieval_index.json", {}) or {}
    topics = []
    for topic in list_value(retrieval_payload.get("topics")):
        topic_segment_ids = [sid for sid in topic.get("segment_ids") or [] if sid in selected_segment_ids]
        topic_evidence_ids = [eid for eid in topic.get("evidence_ids") or [] if eid in allowed_evidence_ids]
        if not topic_segment_ids and not topic_evidence_ids and len(topics) >= 6:
            continue
        topics.append({
            "topic": topic.get("topic"),
            "topic_type": topic.get("topic_type"),
            "query_aliases": (topic.get("query_aliases") or [])[:10],
            "priority_files": (topic.get("priority_files") or [])[:8],
            "segment_ids": topic_segment_ids[:8],
            "fact_ids": (topic.get("fact_ids") or [])[:12],
            "evidence_ids": topic_evidence_ids[:8],
        })
        if len(topics) >= 16:
            break

    note_payload = read_json(semantic_dir / "note_links.json", {}) or {}
    note_links = []
    for link in list_value(note_payload.get("links")):
        evidence_ids = set(link.get("evidence_ids") or [])
        linkage = link.get("linkage") if isinstance(link.get("linkage"), dict) else {}
        amount_check = linkage.get("amount_check") if isinstance(linkage.get("amount_check"), dict) else {}
        if evidence_ids & allowed_evidence_ids or linkage.get("confidence") == "high" or amount_check.get("status") == "verified":
            note_links.append(compact_note_link(link))
        if len(note_links) >= 14:
            break

    document_payload = read_json(semantic_dir / "document_links.json", {}) or {}
    document_links = []
    for link in list_value(document_payload.get("links")):
        evidence_ids = set(link.get("evidence_ids") or [])
        relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
        if evidence_ids & allowed_evidence_ids or relation.get("confidence") == "high" or relation.get("amount_check_status") == "verified":
            document_links.append(compact_document_link(link))
        if len(document_links) >= 14:
            break

    table_relations_payload = read_json(report_dir / "parser" / "table_relations.json", {}) or {}
    raw_table_relations = table_relations_payload.get("relations") if isinstance(table_relations_payload, dict) else []
    table_relations = []
    for relation in list_value(raw_table_relations):
        if relation.get("relation_type") in {"continuation", "note_table", "statement_note_ref"} or float(relation.get("confidence") or 0) >= 0.9:
            table_relations.append(compact_table_relation(relation))
        if len(table_relations) >= 12:
            break

    return {
        "design_note": "This context is selected from full parser/wiki files. LLM may use it only as evidence-bound context and must not create new IDs.",
        "source_files": {
            "report_markdown": str(report_dir / "report.md"),
            "document_full": str(report_dir / "document_full.json") if (report_dir / "document_full.json").exists() else str(report_dir / "parser" / "document_full.json"),
            "table_relations": str(report_dir / "parser" / "table_relations.json"),
            "table_index": str(report_dir / "tables" / "table_index.json"),
        },
        "retrieval_topics": topics,
        "note_links": note_links,
        "document_links": document_links,
        "table_relations": table_relations,
    }


def segment_score(segment: dict[str, Any], market: str | None = None) -> tuple[int, int]:
    score = 0
    if segment.get("segment_type") in TARGET_SEGMENT_TYPES:
        score += 10
    if segment.get("importance") == "high":
        score += 4
    elif segment.get("importance") == "medium":
        score += 2
    title = str(segment.get("title") or "")
    summary = str(segment.get("summary") or "")
    haystack = (title + " " + summary).lower()
    boosts = TITLE_BOOST_KEYWORDS
    if market:
        boosts = list(dict.fromkeys(TITLE_BOOST_KEYWORDS + title_boost_keywords_for_market(market)))
    score += sum(2 for keyword in boosts if str(keyword).lower() in haystack)
    return score, -int(segment.get("md_line_start") or 0)


def select_segments(segments: list[dict[str, Any]], max_segments: int, market: str | None = None) -> list[dict[str, Any]]:
    candidates = [item for item in segments if item.get("segment_id")]
    candidates.sort(key=lambda item: segment_score(item, market), reverse=True)
    selected = candidates[:max_segments]
    selected.sort(key=lambda item: int(item.get("md_line_start") or 0))
    return selected


def build_request_payload(company_dir: Path, max_segments: int) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic_dir = company_dir / "semantic"
    company = read_json(company_dir / "company.json", {}) or {}
    segments_payload = read_json(semantic_dir / "segments.json", {}) or {}
    evidence_payload = read_json(semantic_dir / "evidence_semantic.json", {}) or {}
    facts_payload = read_json(semantic_dir / "facts.json", {}) or {}
    claims_payload = read_json(semantic_dir / "claims.json", {}) or {}

    report_id = (
        segments_payload.get("report_id")
        or evidence_payload.get("report_id")
        or company.get("primary_report_id")
        or "2025-annual"
    )
    market = str(company.get("market") or segments_payload.get("market") or "").upper()
    profile = profile_for_market(market, company.get("identity_route") == "generic_non_a_share_wiki_import")
    report_dir = company_dir / "reports" / report_id
    evidence = evidence_payload.get("evidence") if isinstance(evidence_payload.get("evidence"), list) else []
    evidence_map = {item.get("evidence_id"): item for item in evidence if item.get("evidence_id")}
    all_segments = segments_payload.get("segments") if isinstance(segments_payload.get("segments"), list) else []
    selected_segments = select_segments(all_segments, max_segments, profile.market)
    allowed_evidence_ids = sorted({
        evidence_id
        for segment in selected_segments
        for evidence_id in (segment.get("evidence_ids") or [])
        if evidence_id in evidence_map
    })

    financial_facts = [
        item for item in (facts_payload.get("facts") or [])
        if item.get("fact_type") in {"financial_metric_fact", "operating_metric_fact", "segment_metric_fact"}
    ][:24]
    rule_claims = (claims_payload.get("claims") or [])[:16]

    payload = {
        "task": "listed_company_financial_report_llm_semantic_enrichment",
        "prompt_version": PROMPT_VERSION,
        "company": {
            "company_dir": company_dir.name,
            "company_id": company.get("company_id") or segments_payload.get("company_id") or company_dir.name,
            "market": company.get("market") or profile.market,
            "stock_code": company.get("stock_code"),
            "ticker": company.get("ticker"),
            "exchange": company.get("exchange"),
            "company_short_name": company.get("company_short_name"),
            "company_full_name": company.get("company_full_name"),
            "company_name": company.get("company_name"),
        },
        "report_id": report_id,
        "market_semantic_profile": {
            "market": profile.market,
            "source_language": profile.source_language,
            "output_language": profile.output_language,
            "focus": profile.llm_focus,
            "guardrails": profile.llm_guardrails,
        },
        "allowed_segment_ids": [item.get("segment_id") for item in selected_segments if item.get("segment_id")],
        "allowed_evidence_ids": allowed_evidence_ids,
        "segments": [compact_segment(item, evidence_map, company_dir, report_dir) for item in selected_segments],
        "full_file_context": build_full_file_context(
            company_dir,
            report_dir,
            semantic_dir,
            selected_segments,
            set(allowed_evidence_ids),
        ),
        "rule_context": {
            "financial_or_operating_facts": [compact_fact(item) for item in financial_facts],
            "rule_claims": [compact_claim(item) for item in rule_claims],
        },
        "output_contract": {
            "must_return_json_only": True,
            "must_use_only_allowed_ids": True,
            "formal_items_need_segment_and_evidence": True,
            "language": "zh-CN",
            "preserve_source_terms": True,
            "must_not_extract_new_financial_numbers": True,
            "full_file_context_must_remain_evidence_bound": True,
        },
    }
    inputs = {
        "company_json_sha256": sha256_file(company_dir / "company.json"),
        "segments_sha256": sha256_file(semantic_dir / "segments.json"),
        "evidence_semantic_sha256": sha256_file(semantic_dir / "evidence_semantic.json"),
        "facts_sha256": sha256_file(semantic_dir / "facts.json"),
        "claims_sha256": sha256_file(semantic_dir / "claims.json"),
        "artifact_manifest_sha256": sha256_file(report_dir / "artifact_manifest.json"),
        "request_payload_sha256": sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    }
    return payload, inputs


def build_document_link_request_payload(company_dir: Path, max_links: int) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic_dir = company_dir / "semantic"
    company = read_json(company_dir / "company.json", {}) or {}
    document_links_payload = read_json(semantic_dir / "document_links.json", {}) or {}
    note_links_payload = read_json(semantic_dir / "note_links.json", {}) or {}
    evidence_payload = read_json(semantic_dir / "evidence_semantic.json", {}) or {}

    report_id = (
        document_links_payload.get("report_id")
        or note_links_payload.get("report_id")
        or evidence_payload.get("report_id")
        or company.get("primary_report_id")
        or "2025-annual"
    )
    market = str(company.get("market") or "").upper()
    profile = profile_for_market(market, company.get("identity_route") == "generic_non_a_share_wiki_import")
    report_dir = company_dir / "reports" / report_id
    raw_links = document_links_payload.get("links") if isinstance(document_links_payload.get("links"), list) else []
    candidate_links = [
        link for link in raw_links
        if isinstance(link, dict)
        and link.get("document_link_id")
        and (link.get("relation") or {}).get("llm_allowed", True)
    ][:max_links]
    allowed_document_link_ids = [link.get("document_link_id") for link in candidate_links if link.get("document_link_id")]
    evidence_ids = sorted({
        evidence_id
        for link in candidate_links
        for evidence_id in (link.get("evidence_ids") or [])
    })

    payload = {
        "task": "listed_company_financial_report_document_link_semantic_validation",
        "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
        "company": {
            "company_dir": company_dir.name,
            "company_id": company.get("company_id") or document_links_payload.get("company_id") or company_dir.name,
            "market": company.get("market") or profile.market,
            "stock_code": company.get("stock_code"),
            "ticker": company.get("ticker"),
            "exchange": company.get("exchange"),
            "company_short_name": company.get("company_short_name"),
            "company_full_name": company.get("company_full_name"),
        },
        "report_id": report_id,
        "market_semantic_profile": {
            "market": profile.market,
            "source_language": profile.source_language,
            "output_language": profile.output_language,
            "guardrails": profile.llm_guardrails,
        },
        "allowed_document_link_ids": allowed_document_link_ids,
        "allowed_evidence_ids": evidence_ids,
        "candidate_document_links": [compact_document_link(link) for link in candidate_links],
        "output_contract": {
            "must_return_json_only": True,
            "must_use_only_allowed_document_link_ids": True,
            "must_not_extract_numeric_values": True,
            "must_not_create_new_nodes": True,
            "language": "zh-CN",
        },
    }
    inputs = {
        "company_json_sha256": sha256_file(company_dir / "company.json"),
        "document_links_sha256": sha256_file(semantic_dir / "document_links.json"),
        "note_links_sha256": sha256_file(semantic_dir / "note_links.json"),
        "evidence_semantic_sha256": sha256_file(semantic_dir / "evidence_semantic.json"),
        "artifact_manifest_sha256": sha256_file(report_dir / "artifact_manifest.json"),
        "request_payload_sha256": sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    }
    return payload, inputs


def system_prompt(payload: dict[str, Any] | None = None) -> str:
    profile = ((payload or {}).get("market_semantic_profile") or {})
    market = profile.get("market") or "CN"
    source_language = profile.get("source_language") or "zh-CN"
    return (
        f"你是上市公司财报语义抽取引擎，当前市场为 {market}，原文语言/披露体系为 {source_language}。"
        "你只能依据用户给出的 JSON 输入进行抽取，"
        "不得使用外部知识，不得补充未出现在证据中的事实。"
        "所有正式条目必须绑定输入中存在的 source_segment_ids 和 evidence_ids。"
        "不得抽取新的财务数值、不得覆盖规则层财务事实、不得创造任何 evidence_id/segment_id/document_link_id。"
        "输入中的 full_file_context 来自全量 report/document/table/note 文件，只能作为召回和理解上下文，"
        "正式输出仍必须绑定 allowed_segment_ids 与 allowed_evidence_ids。"
        "输出统一使用中文；遇到 SEC Item、英文/韩文/日文/繁体中文标题、产品名或披露术语时保留原文术语。"
        "如果证据不足、语义冲突或只是推测，放入 review_queue。"
        "请只返回严格 JSON，不要 Markdown，不要解释。"
    )


def document_link_system_prompt() -> str:
    return (
        "你是上市公司财报文档跳转关系审核引擎。你只能依据用户给出的候选 document links 判断跳转语义，"
        "不得抽取金额、余额、同比、单位换算或任何新的财务数据。"
        "不得创造新节点、页码、表格或证据 ID，只能引用 allowed_document_link_ids。"
        "如果关系不确定，放入 review_queue。请只返回严格 JSON，不要 Markdown，不要解释。"
    )


def user_prompt(payload: dict[str, Any]) -> str:
    profile = payload.get("market_semantic_profile") or {}
    schema = {
        "business_profile": [
            {
                "profile_type": "business|product_service|industry|strategy|region|customer|rd|capex|governance|esg",
                "subject": "主体或主题",
                "description": "事实性描述，避免夸张总结",
                "source_terms": ["保留的原文标题/Item/术语，可为空数组"],
                "source_segment_ids": ["seg_xxx"],
                "evidence_ids": ["ev_xxx"],
                "confidence": "high|medium|low",
                "needs_review": False,
            }
        ],
        "claims": [
            {
                "claim_type": "business_change|operation_driver|strategy_progress|industry_position|rd_innovation|capex_project|customer_supplier|governance_esg|financial_explanation",
                "statement": "可用于分析的中间判断",
                "stance": "positive|negative|neutral|mixed",
                "reasoning_summary": "一句话说明依据，不要链式推理",
                "source_terms": ["保留的原文标题/Item/术语，可为空数组"],
                "source_segment_ids": ["seg_xxx"],
                "evidence_ids": ["ev_xxx"],
                "confidence": "high|medium|low",
                "needs_review": False,
            }
        ],
        "risks": [
            {
                "risk_type": "market|industry|policy|technology|financial|operation|customer_supplier|overseas|legal|other",
                "risk": "风险表述",
                "impact": "可能影响",
                "mitigation": "公司披露的应对措施，没有则为空字符串",
                "source_terms": ["保留的原文标题/Item/术语，可为空数组"],
                "source_segment_ids": ["seg_xxx"],
                "evidence_ids": ["ev_xxx"],
                "confidence": "high|medium|low",
                "needs_review": False,
            }
        ],
        "events": [
            {
                "event_type": "investment|mna|lawsuit|guarantee|dividend|financing|contract|capacity|organization|other",
                "event": "事项",
                "status": "planned|ongoing|completed|disclosed|unknown",
                "impact": "披露的影响，没有则为空字符串",
                "source_terms": ["保留的原文标题/Item/术语，可为空数组"],
                "source_segment_ids": ["seg_xxx"],
                "evidence_ids": ["ev_xxx"],
                "confidence": "high|medium|low",
                "needs_review": False,
            }
        ],
        "review_queue": [
            {
                "topic": "需要人工复核的主题",
                "reason": "证据不足、冲突或模型不确定的原因",
                "source_segment_ids": ["seg_xxx"],
                "evidence_ids": ["ev_xxx"],
            }
        ],
    }
    focus_text = "、".join(str(item) for item in profile.get("focus") or [])
    guardrails = "；".join(str(item) for item in profile.get("guardrails") or [])
    return (
        f"请从以下上市公司年报片段中抽取 LLM 语义增强层。市场/披露规则：{profile.get('market') or 'CN'}。"
        f"本市场重点召回场景：{focus_text or '业务、风险、战略、分部、地区、重大事项'}。"
        f"约束：{guardrails}。"
        "不要重复规则层已经给出的纯财务同比结论，重点抽取业务、风险、战略、重大事项、经营驱动。"
        "可以利用 segments.source_context_excerpt 和 full_file_context 中的原文窗口、检索主题、附注/表格跳转关系增强召回，"
        "但不得把这些上下文当作新 ID 来源。"
        "如果原文标题或披露术语对召回有价值，请写入 source_terms，但正式条目仍必须用中文描述。"
        "返回 JSON 结构必须符合此 schema：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "输入 JSON：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def document_link_user_prompt(payload: dict[str, Any]) -> str:
    schema = {
        "document_links": [
            {
                "document_link_id": "必须来自 allowed_document_link_ids",
                "semantic_relation": "main_statement_to_note|composition_detail|impairment_detail|movement_detail|breakdown_detail|accounting_policy_detail|risk_or_impairment_test|other_detail",
                "target_use_case": "balance_value|composition|impairment|movement|explanation|audit_trace",
                "should_prefer_target_for_questions": ["构成", "明细", "减值准备"],
                "confidence": "high|medium|low",
                "needs_review": False,
                "reason": "一句话说明为什么这条边可用于跳转，不包含金额和数值抽取",
            }
        ],
        "review_queue": [
            {
                "document_link_id": "来自 allowed_document_link_ids",
                "reason": "候选边关系不清、目标表不适合、语义冲突等原因",
            }
        ],
    }
    return (
        "请审核下列财报文档跳转候选边。"
        "你的任务只是在已有候选边上补充语义关系和使用场景，不能抽取或改写任何金额、余额、单位、页码、表格编号。"
        "当用户问余额/账面价值时通常可用主表；当用户问构成、明细、减值准备、变动原因时应优先跳到附注表。"
        "返回 JSON 结构必须符合此 schema：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "输入 JSON：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def call_chat_completion(provider: dict[str, Any], request_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if provider.get("transport") == "hermes_runs":
        return call_hermes_completion(provider, system_prompt(request_payload), user_prompt(request_payload))
    if not provider.get("baseUrl"):
        raise RuntimeError("LLM baseUrl is empty")
    if not provider.get("model"):
        raise RuntimeError("LLM model is empty")

    headers = {"Content-Type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"
    body = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt(request_payload)},
            {"role": "user", "content": user_prompt(request_payload)},
        ],
        "temperature": provider.get("temperature", 0.2),
        "max_tokens": provider.get("maxTokens", 8192),
        "stream": False,
        "chat_template_kwargs": provider.get("chatTemplateKwargs") or {"enable_thinking": False},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint(provider["baseUrl"], "/chat/completions"),
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(provider.get("timeoutSeconds") or 180)) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM connection failed: {exc}") from exc

    response_json = json.loads(raw)
    choices = response_json.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or choices[0].get("text") or "").strip()
    if not content:
        raise RuntimeError("LLM returned empty content")
    return response_json, content


def call_document_link_chat_completion(provider: dict[str, Any], request_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if provider.get("transport") == "hermes_runs":
        return call_hermes_completion(provider, document_link_system_prompt(), document_link_user_prompt(request_payload))
    if not provider.get("baseUrl"):
        raise RuntimeError("LLM baseUrl is empty")
    if not provider.get("model"):
        raise RuntimeError("LLM model is empty")

    headers = {"Content-Type": "application/json"}
    if provider.get("apiKey"):
        headers["Authorization"] = f"Bearer {provider['apiKey']}"
    body = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": document_link_system_prompt()},
            {"role": "user", "content": document_link_user_prompt(request_payload)},
        ],
        "temperature": min(float(provider.get("temperature", 0.2)), 0.1),
        "max_tokens": provider.get("maxTokens", 8192),
        "stream": False,
        "chat_template_kwargs": provider.get("chatTemplateKwargs") or {"enable_thinking": False},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint(provider["baseUrl"], "/chat/completions"),
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(provider.get("timeoutSeconds") or 180)) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM connection failed: {exc}") from exc

    response_json = json.loads(raw)
    choices = response_json.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or choices[0].get("text") or "").strip()
    if not content:
        raise RuntimeError("LLM returned empty content")
    return response_json, content


def parse_model_json(content: str) -> dict[str, Any]:
    text = extract_json_object_text(content)
    errors: list[str] = []
    for candidate in (text, repair_json_text(text)):
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                raise RuntimeError("Model response JSON is not an object")
            return parsed
        except Exception as exc:
            errors.append(str(exc))
        try:
            import json5  # type: ignore

            parsed = json5.loads(candidate)
            if not isinstance(parsed, dict):
                raise RuntimeError("Model response JSON is not an object")
            return parsed
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Model response JSON parse failed: " + " | ".join(errors[-3:]))


def extract_json_object_text(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise RuntimeError("Model response does not contain a JSON object")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    end = text.rfind("}")
    if end < start:
        raise RuntimeError("Model response does not contain a complete JSON object")
    return text[start:end + 1]


def repair_json_text(text: str) -> str:
    repaired = text
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    # Models occasionally omit a comma between adjacent object fields or array objects.
    repaired = re.sub(r'(["}\]\d])\s+("[-A-Za-z0-9_\u4e00-\u9fff]+"\s*:)', r"\1, \2", repaired)
    repaired = re.sub(r'\b(true|false|null)\s+("[-A-Za-z0-9_\u4e00-\u9fff]+"\s*:)', r"\1, \2", repaired)
    repaired = re.sub(r'}\s*{', r"},{", repaired)
    repaired = re.sub(r']\s*\[', r"],[", repaired)
    return repaired


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def clean_ids(ids: Any, allowed: set[str]) -> list[str]:
    result = []
    for item in list_value(ids):
        value = str(item or "").strip()
        if value in allowed and value not in result:
            result.append(value)
    return result


def clean_source_terms(value: Any) -> list[str]:
    terms = []
    for item in list_value(value):
        text = trim_text(item, 120)
        if text and text not in terms:
            terms.append(text)
    return terms[:12]


def item_text(item: dict[str, Any], *fields: str, max_chars: int = 600) -> str:
    for field in fields:
        value = trim_text(item.get(field), max_chars)
        if value:
            return value
    return ""


def confidence(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in {"high", "medium", "low"} else "medium"


def infer_evidence_from_segments(segment_ids: list[str], segment_map: dict[str, dict[str, Any]], allowed_evidence: set[str]) -> list[str]:
    inferred: list[str] = []
    for segment_id in segment_ids:
        segment = segment_map.get(segment_id) or {}
        for evidence_id in segment.get("evidence_ids") or []:
            if evidence_id in allowed_evidence and evidence_id not in inferred:
                inferred.append(evidence_id)
    return inferred[:4]


def normalize_formal_items(
    items: list[Any],
    kind: str,
    company_id: str,
    stock_code: str,
    report_id: str,
    provider: dict[str, Any],
    allowed_segments: set[str],
    allowed_evidence: set[str],
    segment_map: dict[str, dict[str, Any]],
    generated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = []
    review_items = []
    report_token = slug(report_id.replace("-", "_"), "report")
    for raw_index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            continue
        segment_ids = clean_ids(raw_item.get("source_segment_ids"), allowed_segments)
        evidence_ids = clean_ids(raw_item.get("evidence_ids"), allowed_evidence)
        needs_review = bool(raw_item.get("needs_review", False))
        if not segment_ids or not evidence_ids:
            review_items.append({
                "review_id": f"llm_review_{slug(stock_code, 'company')}_{report_token}_{kind}_{raw_index:06d}",
                "topic": item_text(raw_item, "statement", "risk", "event", "subject", "topic", "description"),
                "reason": "模型输出缺少有效的 segment/evidence 绑定，已转入复核队列。",
                "source_segment_ids": segment_ids,
                "evidence_ids": evidence_ids,
                "raw_item": raw_item,
                "source_layer": "llm",
                "model": provider.get("model"),
                "prompt_version": PROMPT_VERSION,
                "generated_at": generated_at,
            })
            continue

        base = {
            "company_id": company_id,
            "report_id": report_id,
            "source_segment_ids": segment_ids,
            "evidence_ids": evidence_ids,
            "source_terms": clean_source_terms(raw_item.get("source_terms")),
            "confidence": confidence(raw_item.get("confidence")),
            "needs_review": needs_review,
            "source_layer": "llm",
            "model": provider.get("model"),
            "prompt_version": PROMPT_VERSION,
            "generated_at": generated_at,
        }
        if kind == "business_profile":
            base.update({
                "profile_id": f"llm_profile_{slug(stock_code, 'company')}_{report_token}_{len(normalized) + 1:06d}",
                "profile_type": trim_text(raw_item.get("profile_type"), 80) or "business",
                "subject": item_text(raw_item, "subject", max_chars=160),
                "description": item_text(raw_item, "description", max_chars=800),
            })
        elif kind == "claims":
            base.update({
                "claim_id": f"llm_claim_{slug(stock_code, 'company')}_{report_token}_{len(normalized) + 1:06d}",
                "claim_type": trim_text(raw_item.get("claim_type"), 80) or "business_change",
                "statement": item_text(raw_item, "statement", max_chars=800),
                "stance": trim_text(raw_item.get("stance"), 40) or "neutral",
                "reasoning_summary": item_text(raw_item, "reasoning_summary", max_chars=500),
            })
        elif kind == "risks":
            base.update({
                "risk_id": f"llm_risk_{slug(stock_code, 'company')}_{report_token}_{len(normalized) + 1:06d}",
                "risk_type": trim_text(raw_item.get("risk_type"), 80) or "other",
                "risk": item_text(raw_item, "risk", max_chars=800),
                "impact": item_text(raw_item, "impact", max_chars=500),
                "mitigation": item_text(raw_item, "mitigation", max_chars=500),
            })
        elif kind == "events":
            base.update({
                "event_id": f"llm_event_{slug(stock_code, 'company')}_{report_token}_{len(normalized) + 1:06d}",
                "event_type": trim_text(raw_item.get("event_type"), 80) or "other",
                "event": item_text(raw_item, "event", max_chars=800),
                "status": trim_text(raw_item.get("status"), 60) or "unknown",
                "impact": item_text(raw_item, "impact", max_chars=500),
            })
        normalized.append(base)
    return normalized, review_items


def normalize_review_queue(
    items: list[Any],
    company_id: str,
    stock_code: str,
    report_id: str,
    provider: dict[str, Any],
    allowed_segments: set[str],
    allowed_evidence: set[str],
    generated_at: str,
) -> list[dict[str, Any]]:
    report_token = slug(report_id.replace("-", "_"), "report")
    normalized = []
    for raw_index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            continue
        normalized.append({
            "review_id": f"llm_review_{slug(stock_code, 'company')}_{report_token}_{raw_index:06d}",
            "company_id": company_id,
            "report_id": report_id,
            "topic": item_text(raw_item, "topic", "statement", "risk", "event", max_chars=300),
            "reason": item_text(raw_item, "reason", "description", max_chars=600),
            "source_segment_ids": clean_ids(raw_item.get("source_segment_ids"), allowed_segments),
            "evidence_ids": clean_ids(raw_item.get("evidence_ids"), allowed_evidence),
            "source_layer": "llm",
            "model": provider.get("model"),
            "prompt_version": PROMPT_VERSION,
            "generated_at": generated_at,
        })
    return normalized


def normalize_response(
    parsed: dict[str, Any],
    request_payload: dict[str, Any],
    provider: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    company = request_payload["company"]
    company_id = company.get("company_id") or company.get("company_dir")
    stock_code = company.get("stock_code") or company.get("company_dir") or "company"
    report_id = request_payload["report_id"]
    allowed_segments = set(request_payload.get("allowed_segment_ids") or [])
    allowed_evidence = set(request_payload.get("allowed_evidence_ids") or [])
    segment_map = {item.get("segment_id"): item for item in request_payload.get("segments") or [] if item.get("segment_id")}

    business_profile, review_from_profile = normalize_formal_items(
        list_value(parsed.get("business_profile")),
        "business_profile",
        company_id,
        stock_code,
        report_id,
        provider,
        allowed_segments,
        allowed_evidence,
        segment_map,
        generated_at,
    )
    claims, review_from_claims = normalize_formal_items(
        list_value(parsed.get("claims")),
        "claims",
        company_id,
        stock_code,
        report_id,
        provider,
        allowed_segments,
        allowed_evidence,
        segment_map,
        generated_at,
    )
    risks, review_from_risks = normalize_formal_items(
        list_value(parsed.get("risks")),
        "risks",
        company_id,
        stock_code,
        report_id,
        provider,
        allowed_segments,
        allowed_evidence,
        segment_map,
        generated_at,
    )
    events, review_from_events = normalize_formal_items(
        list_value(parsed.get("events")),
        "events",
        company_id,
        stock_code,
        report_id,
        provider,
        allowed_segments,
        allowed_evidence,
        segment_map,
        generated_at,
    )
    review_queue = normalize_review_queue(
        list_value(parsed.get("review_queue")),
        company_id,
        stock_code,
        report_id,
        provider,
        allowed_segments,
        allowed_evidence,
        generated_at,
    )
    review_queue.extend(review_from_profile + review_from_claims + review_from_risks + review_from_events)

    return {
        "business_profile": business_profile,
        "claims": claims,
        "risks": risks,
        "events": events,
        "review_queue": review_queue,
    }


def normalize_document_link_response(
    parsed: dict[str, Any],
    request_payload: dict[str, Any],
    provider: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    company = request_payload["company"]
    company_id = company.get("company_id") or company.get("company_dir")
    stock_code = company.get("stock_code") or company.get("company_dir") or "company"
    report_id = request_payload["report_id"]
    allowed_link_ids = set(request_payload.get("allowed_document_link_ids") or [])
    candidate_map = {
        item.get("document_link_id"): item
        for item in request_payload.get("candidate_document_links") or []
        if item.get("document_link_id")
    }
    allowed_relations = {
        "main_statement_to_note",
        "composition_detail",
        "impairment_detail",
        "movement_detail",
        "breakdown_detail",
        "accounting_policy_detail",
        "risk_or_impairment_test",
        "other_detail",
    }
    allowed_use_cases = {"balance_value", "composition", "impairment", "movement", "explanation", "audit_trace"}
    report_token = slug(report_id.replace("-", "_"), "report")
    normalized_links = []
    review_queue = []
    seen: set[str] = set()
    for raw_index, raw_item in enumerate(list_value(parsed.get("document_links")), start=1):
        if not isinstance(raw_item, dict):
            continue
        link_id = str(raw_item.get("document_link_id") or "").strip()
        if link_id not in allowed_link_ids or link_id in seen:
            review_queue.append({
                "review_id": f"llm_doclink_review_{slug(stock_code, 'company')}_{report_token}_{raw_index:06d}",
                "company_id": company_id,
                "report_id": report_id,
                "document_link_id": link_id,
                "reason": "模型输出的 document_link_id 不在候选集合中，已丢弃。",
                "raw_item": raw_item,
                "source_layer": "llm",
                "model": provider.get("model"),
                "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
                "generated_at": generated_at,
            })
            continue
        seen.add(link_id)
        relation = str(raw_item.get("semantic_relation") or "").strip()
        if relation not in allowed_relations:
            relation = "other_detail"
        use_case = str(raw_item.get("target_use_case") or "").strip()
        if use_case not in allowed_use_cases:
            use_case = "audit_trace"
        candidate = candidate_map.get(link_id) or {}
        normalized_links.append({
            "llm_document_link_id": f"llm_doclink_{slug(stock_code, 'company')}_{report_token}_{len(normalized_links) + 1:06d}",
            "company_id": company_id,
            "report_id": report_id,
            "document_link_id": link_id,
            "link_type": candidate.get("link_type"),
            "source": candidate.get("source"),
            "target": candidate.get("target"),
            "semantic_relation": relation,
            "target_use_case": use_case,
            "should_prefer_target_for_questions": [
                trim_text(item, 80)
                for item in list_value(raw_item.get("should_prefer_target_for_questions"))
                if trim_text(item, 80)
            ][:12],
            "reason": item_text(raw_item, "reason", max_chars=300),
            "confidence": confidence(raw_item.get("confidence")),
            "needs_review": bool(raw_item.get("needs_review", False)),
            "source_layer": "llm",
            "model": provider.get("model"),
            "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
            "generated_at": generated_at,
            "guardrails": {
                "numeric_extraction_allowed": False,
                "new_node_creation_allowed": False,
            },
        })

    for raw_index, raw_item in enumerate(list_value(parsed.get("review_queue")), start=1):
        if not isinstance(raw_item, dict):
            continue
        link_id = str(raw_item.get("document_link_id") or "").strip()
        review_queue.append({
            "review_id": f"llm_doclink_review_{slug(stock_code, 'company')}_{report_token}_manual_{raw_index:06d}",
            "company_id": company_id,
            "report_id": report_id,
            "document_link_id": link_id if link_id in allowed_link_ids else "",
            "reason": item_text(raw_item, "reason", "topic", max_chars=600),
            "source_layer": "llm",
            "model": provider.get("model"),
            "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
            "generated_at": generated_at,
        })
    return {
        "document_links": normalized_links,
        "review_queue": review_queue,
    }


def output_dir_for(company_dir: Path, report_id: str) -> Path:
    return company_dir / "semantic" / "llm" / report_id


def fresh_existing_enrichment(company_dir: Path, max_segments: int) -> dict[str, Any] | None:
    try:
        request_payload, inputs = build_request_payload(company_dir, max_segments)
    except Exception:
        return None
    out_dir = output_dir_for(company_dir, request_payload["report_id"])
    if any(not (out_dir / name).is_file() for name in ENRICHMENT_OUTPUTS):
        return None
    log = read_json(out_dir / "extraction_log.json", {}) or {}
    log_inputs = log.get("inputs") if isinstance(log.get("inputs"), dict) else {}
    counts = log.get("counts") if isinstance(log.get("counts"), dict) else {}
    if not log_inputs or int(counts.get("selected_segments") or 0) <= 0 or int(counts.get("allowed_evidence") or 0) <= 0:
        return None
    if any(log_inputs.get(key) != value for key, value in inputs.items()):
        return None
    result = dict(log)
    result["skipped_existing"] = True
    return result


def fresh_existing_document_links(company_dir: Path, max_links: int) -> dict[str, Any] | None:
    try:
        request_payload, inputs = build_document_link_request_payload(company_dir, max_links)
    except Exception:
        return None
    out_dir = output_dir_for(company_dir, request_payload["report_id"])
    if any(not (out_dir / name).is_file() for name in DOCUMENT_LINK_OUTPUTS):
        return None
    log = read_json(out_dir / "document_links_extraction_log.json", {}) or {}
    log_inputs = log.get("inputs") if isinstance(log.get("inputs"), dict) else {}
    counts = log.get("counts") if isinstance(log.get("counts"), dict) else {}
    if not log_inputs or int(counts.get("candidate_document_links") or 0) <= 0:
        return None
    if any(log_inputs.get(key) != value for key, value in inputs.items()):
        return None
    result = dict(log)
    result["skipped_existing"] = True
    return result


def write_outputs(
    company_dir: Path,
    request_payload: dict[str, Any],
    inputs: dict[str, Any],
    provider: dict[str, Any],
    raw_request: dict[str, Any],
    raw_response: dict[str, Any],
    response_content: str,
    parsed: dict[str, Any],
    dry_run: bool = False,
    persist_raw: bool = False,
) -> dict[str, Any]:
    generated_at = now_iso()
    normalized = normalize_response(parsed, request_payload, provider, generated_at)
    report_id = request_payload["report_id"]
    out_dir = output_dir_for(company_dir, report_id)
    counts = {
        "selected_segments": len(request_payload.get("segments") or []),
        "allowed_evidence": len(request_payload.get("allowed_evidence_ids") or []),
        "business_profile": len(normalized["business_profile"]),
        "claims": len(normalized["claims"]),
        "risks": len(normalized["risks"]),
        "events": len(normalized["events"]),
        "review_queue": len(normalized["review_queue"]),
    }
    enrichment = {
        "schema_version": 1,
        "enrichment_version": ENRICHMENT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "generated_at": generated_at,
        "company": request_payload["company"],
        "company_id": request_payload["company"].get("company_id"),
        "market": request_payload["company"].get("market"),
        "report_id": report_id,
        "market_semantic_profile": request_payload.get("market_semantic_profile"),
        "provider": public_provider(provider),
        "inputs": inputs,
        "counts": counts,
        **normalized,
    }
    extraction_log = {
        "schema_version": 1,
        "enrichment_version": ENRICHMENT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "generated_at": generated_at,
        "company_id": request_payload["company"].get("company_id"),
        "company_dir": company_dir.name,
        "market": request_payload["company"].get("market"),
        "report_id": report_id,
        "provider": public_provider(provider),
        "inputs": inputs,
        "counts": counts,
        "output_dir": str(out_dir),
        "raw_request_sha256": sha256_text(json.dumps(raw_request, ensure_ascii=False, sort_keys=True)),
        "raw_response_sha256": sha256_text(json.dumps(raw_response, ensure_ascii=False, sort_keys=True)),
        "response_content_sha256": sha256_text(response_content),
    }
    if not dry_run:
        write_json(out_dir / "enrichment.json", enrichment)
        write_json(out_dir / "business_profile.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": enrichment["company_id"],
            "report_id": report_id,
            "business_profile": normalized["business_profile"],
        })
        write_json(out_dir / "claims.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": enrichment["company_id"],
            "report_id": report_id,
            "claims": normalized["claims"],
        })
        write_json(out_dir / "risks.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": enrichment["company_id"],
            "report_id": report_id,
            "risks": normalized["risks"],
        })
        write_json(out_dir / "events.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": enrichment["company_id"],
            "report_id": report_id,
            "events": normalized["events"],
        })
        write_json(out_dir / "review_queue.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": enrichment["company_id"],
            "report_id": report_id,
            "review_queue": normalized["review_queue"],
        })
        write_json(out_dir / "extraction_log.json", extraction_log)
        if persist_raw:
            write_json(out_dir / "raw" / "request.json", raw_request)
            write_json(out_dir / "raw" / "response.json", raw_response)
        else:
            remove_raw_files(out_dir, ("request.json", "response.json"))
    return extraction_log


def write_document_link_outputs(
    company_dir: Path,
    request_payload: dict[str, Any],
    inputs: dict[str, Any],
    provider: dict[str, Any],
    raw_request: dict[str, Any],
    raw_response: dict[str, Any],
    response_content: str,
    parsed: dict[str, Any],
    dry_run: bool = False,
    persist_raw: bool = False,
) -> dict[str, Any]:
    generated_at = now_iso()
    normalized = normalize_document_link_response(parsed, request_payload, provider, generated_at)
    report_id = request_payload["report_id"]
    out_dir = output_dir_for(company_dir, report_id)
    counts = {
        "candidate_document_links": len(request_payload.get("candidate_document_links") or []),
        "allowed_document_link_ids": len(request_payload.get("allowed_document_link_ids") or []),
        "document_links": len(normalized["document_links"]),
        "review_queue": len(normalized["review_queue"]),
    }
    payload = {
        "schema_version": 1,
        "enrichment_version": ENRICHMENT_VERSION,
        "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
        "generated_at": generated_at,
        "company": request_payload["company"],
        "company_id": request_payload["company"].get("company_id"),
        "report_id": report_id,
        "provider": public_provider(provider),
        "inputs": inputs,
        "counts": counts,
        "design_note": "LLM validates document navigation semantics only; it does not extract numeric financial data.",
        **normalized,
    }
    extraction_log = {
        "schema_version": 1,
        "enrichment_version": ENRICHMENT_VERSION,
        "prompt_version": DOCUMENT_LINK_PROMPT_VERSION,
        "generated_at": generated_at,
        "company_id": request_payload["company"].get("company_id"),
        "company_dir": company_dir.name,
        "report_id": report_id,
        "provider": public_provider(provider),
        "inputs": inputs,
        "counts": counts,
        "output_file": str(out_dir / "document_links.json"),
        "raw_request_sha256": sha256_text(json.dumps(raw_request, ensure_ascii=False, sort_keys=True)),
        "raw_response_sha256": sha256_text(json.dumps(raw_response, ensure_ascii=False, sort_keys=True)),
        "response_content_sha256": sha256_text(response_content),
    }
    if not dry_run:
        write_json(out_dir / "document_links.json", payload)
        write_json(out_dir / "document_links_review_queue.json", {
            "schema_version": 1,
            "generated_at": generated_at,
            "company_id": payload["company_id"],
            "report_id": report_id,
            "review_queue": normalized["review_queue"],
        })
        write_json(out_dir / "document_links_extraction_log.json", extraction_log)
        if persist_raw:
            write_json(out_dir / "raw" / "document_links_request.json", raw_request)
            write_json(out_dir / "raw" / "document_links_response.json", raw_response)
        else:
            remove_raw_files(out_dir, ("document_links_request.json", "document_links_response.json"))
    return extraction_log


def llm_attempt_count() -> int:
    raw = os.environ.get("FINSIGHT_LLM_SEMANTIC_RETRIES") or os.environ.get("SIQ_LLM_SEMANTIC_RETRIES") or "3"
    try:
        return max(1, min(int(raw), 5))
    except ValueError:
        return 3


def write_failure_raw(
    company_dir: Path,
    request_payload: dict[str, Any],
    raw_request: dict[str, Any],
    raw_response: dict[str, Any] | None,
    response_content: str,
    error: Exception,
    *,
    mode: str,
) -> None:
    report_id = request_payload.get("report_id") or "2025-annual"
    out_dir = output_dir_for(company_dir, str(report_id))
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "company_dir": company_dir.name,
        "report_id": report_id,
        "mode": mode,
        "error": str(error),
        "provider": raw_request.get("provider"),
        "response_content_preview": response_content[:4000],
        "response_content_sha256": sha256_text(response_content) if response_content else None,
    }
    write_json(out_dir / "raw" / "request.json", raw_request)
    if raw_response is not None:
        write_json(out_dir / "raw" / "response.json", raw_response)
    if response_content:
        (out_dir / "raw").mkdir(parents=True, exist_ok=True)
        (out_dir / "raw" / "response.txt").write_text(response_content, encoding="utf-8")
    write_json(out_dir / "raw" / "failure.json", payload)


def call_and_parse_with_retries(
    company_dir: Path,
    request_payload: dict[str, Any],
    raw_request: dict[str, Any],
    call_fn,
    provider: dict[str, Any],
    *,
    mode: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    attempts = llm_attempt_count()
    last_error: Exception | None = None
    last_raw_response: dict[str, Any] | None = None
    last_response_content = ""
    for attempt in range(1, attempts + 1):
        try:
            raw_response, response_content = call_fn(provider, request_payload)
            parsed = parse_model_json(response_content)
            if attempt > 1:
                raw_response = dict(raw_response)
                raw_response["recovered_after_attempt"] = attempt
            return raw_response, response_content, parsed
        except Exception as exc:  # noqa: BLE001 - retry gate must catch parser and transport errors
            last_error = exc
            if "raw_response" in locals():
                last_raw_response = locals().get("raw_response")
            if "response_content" in locals():
                last_response_content = str(locals().get("response_content") or "")
            if attempt < attempts:
                time.sleep(min(8, attempt * 2))
                continue
    assert last_error is not None
    write_failure_raw(
        company_dir,
        request_payload,
        raw_request,
        last_raw_response,
        last_response_content,
        last_error,
        mode=mode,
    )
    raise last_error


def enrich_company(
    company_dir: Path,
    provider: dict[str, Any],
    max_segments: int,
    dry_run: bool = False,
    skip_existing: bool = False,
    persist_raw: bool = False,
) -> dict[str, Any]:
    if skip_existing and not dry_run:
        existing = fresh_existing_enrichment(company_dir, max_segments)
        if existing is not None:
            return existing
    request_payload, inputs = build_request_payload(company_dir, max_segments)
    if not request_payload.get("segments"):
        raise RuntimeError(f"No eligible semantic segments for {company_dir.name}")

    raw_request = {
        "provider": public_provider(provider),
        "messages": [
            {"role": "system", "content": system_prompt(request_payload)},
            {"role": "user", "content": user_prompt(request_payload)},
        ],
    }
    if dry_run:
        parsed = {"business_profile": [], "claims": [], "risks": [], "events": [], "review_queue": []}
        raw_response = {"dry_run": True}
        response_content = "{}"
    else:
        raw_response, response_content, parsed = call_and_parse_with_retries(
            company_dir,
            request_payload,
            raw_request,
            call_chat_completion,
            provider,
            mode="enrichment",
        )
    return write_outputs(
        company_dir,
        request_payload,
        inputs,
        provider,
        raw_request,
        raw_response,
        response_content,
        parsed,
        dry_run=dry_run,
        persist_raw=persist_raw,
    )


def enrich_company_document_links(
    company_dir: Path,
    provider: dict[str, Any],
    max_links: int,
    dry_run: bool = False,
    skip_existing: bool = False,
    persist_raw: bool = False,
) -> dict[str, Any]:
    if skip_existing and not dry_run:
        existing = fresh_existing_document_links(company_dir, max_links)
        if existing is not None:
            return existing
    request_payload, inputs = build_document_link_request_payload(company_dir, max_links)
    if not request_payload.get("candidate_document_links"):
        raise RuntimeError(f"No eligible document link candidates for {company_dir.name}")

    raw_request = {
        "provider": public_provider(provider),
        "messages": [
            {"role": "system", "content": document_link_system_prompt()},
            {"role": "user", "content": document_link_user_prompt(request_payload)},
        ],
    }
    if dry_run:
        parsed = {"document_links": [], "review_queue": []}
        raw_response = {"dry_run": True}
        response_content = "{}"
    else:
        raw_response, response_content, parsed = call_and_parse_with_retries(
            company_dir,
            request_payload,
            raw_request,
            call_document_link_chat_completion,
            provider,
            mode="document-links",
        )
    return write_document_link_outputs(
        company_dir,
        request_payload,
        inputs,
        provider,
        raw_request,
        raw_response,
        response_content,
        parsed,
        dry_run=dry_run,
        persist_raw=persist_raw,
    )


def build_manifest(wiki_root: Path, results: list[dict[str, Any]], failures: list[dict[str, Any]], provider: dict[str, Any]) -> None:
    prompt_versions = sorted({
        result.get("prompt_version")
        for result in results
        if result.get("prompt_version")
    }) or [PROMPT_VERSION]
    manifest = {
        "schema_version": 1,
        "enrichment_version": ENRICHMENT_VERSION,
        "prompt_version": prompt_versions[0] if len(prompt_versions) == 1 else "mixed",
        "prompt_versions": prompt_versions,
        "generated_at": now_iso(),
        "provider": public_provider(provider),
        "markets": sorted({str(result.get("market") or "").upper() for result in results if result.get("market")}),
        "company_count": len(results),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
    }
    write_json(wiki_root / "_meta" / "llm_semantic_manifest.json", manifest)


def company_dirs_for(wiki_root: Path, company: str) -> list[Path]:
    companies_root = wiki_root / "companies"
    if company:
        return [companies_root / company]
    return sorted([path for path in companies_root.iterdir() if path.is_dir()])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    parser.add_argument("--company", default="", help="Optional company dir, e.g. 002594-比亚迪")
    parser.add_argument("--mode", choices=("enrichment", "document-links"), default="enrichment")
    parser.add_argument("--max-segments", type=int, default=int(os.environ.get("FINSIGHT_LLM_SEMANTIC_MAX_SEGMENTS", "28")))
    parser.add_argument("--max-links", type=int, default=int(os.environ.get("FINSIGHT_LLM_DOCUMENT_LINK_MAX_LINKS", "120")))
    parser.add_argument("--dry-run", action="store_true", help="Build request payload and empty outputs without calling the model")
    parser.add_argument("--skip-existing", action="store_true", help="Skip companies whose LLM layer matches current rule-layer hashes")
    parser.add_argument(
        "--persist-raw",
        action="store_true",
        default=_env_bool("SIQ_LLM_SEMANTIC_PERSIST_RAW", False) or _env_bool("FINSIGHT_LLM_SEMANTIC_PERSIST_RAW", False),
        help="Persist raw model request/response payloads for debugging",
    )
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root)
    provider = load_local_provider()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for company_dir in company_dirs_for(wiki_root, args.company):
        try:
            if args.mode == "document-links":
                result = enrich_company_document_links(
                    company_dir,
                    provider,
                    max(1, args.max_links),
                    dry_run=args.dry_run,
                    skip_existing=args.skip_existing,
                    persist_raw=args.persist_raw,
                )
            else:
                result = enrich_company(
                    company_dir,
                    provider,
                    max(1, args.max_segments),
                    dry_run=args.dry_run,
                    skip_existing=args.skip_existing,
                    persist_raw=args.persist_raw,
                )
            results.append(result)
            suffix = " skipped" if result.get("skipped_existing") else ""
            print(f"ok {company_dir.name}: {result['counts']}{suffix}")
        except Exception as exc:  # noqa: BLE001 - batch command should continue across companies
            failure = {"company_dir": company_dir.name, "error": str(exc)}
            failures.append(failure)
            print(f"fail {company_dir.name}: {exc}")
    if not args.dry_run:
        build_manifest(wiki_root, results, failures, provider)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
