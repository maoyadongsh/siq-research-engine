#!/usr/bin/env python3
"""Run the six-market exact-identity release smoke against the local Wiki."""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
CONTRACTS_SRC = REPO_ROOT / "packages" / "market-contracts" / "src"
for import_root in (API_ROOT, CONTRACTS_SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from services.analysis_report_workflow import (  # noqa: E402
    AnalysisReportWorkflowRequest,
    run_analysis_report_workflow,
)
from services.factcheck_workflow import FactcheckWorkflowRequest, run_factcheck_workflow  # noqa: E402
from services.research_report_package import (  # noqa: E402
    ResolvedReportPackage,
    enumerate_companies,
    enumerate_report_packages,
    resolve_report_package_from_context,
)
from services.research_universe import resolve_artifact  # noqa: E402
from services.tracking_workflow import TrackingWorkflowRequest, run_tracking_workflow  # noqa: E402
from tests.fact_surface_hash import (  # noqa: E402
    assert_fact_surface_unchanged,
    snapshot_company_fact_surface,
)

MARKETS = ("CN", "HK", "US", "EU", "KR", "JP")
CN_MARKET = "CN"
FORMAL_MARKETS = tuple(market for market in MARKETS if market != CN_MARKET)
SUCCESS_STATUSES = frozenset({"completed", "degraded"})
SCHEMA_VERSION = "siq_secondary_market_multi_market_real_smoke_v1"
PREFERRED_COMPANY_CODES = {
    "CN": "000333",
    "HK": "00005",
    "US": "AAPL",
    "EU": "AD",
    "KR": "000270",
    "JP": "3382",
}
CN_GOLDEN_COMPANY_CODE = "000333"
CN_GOLDEN_ANALYSIS_STEM = "000333-美的集团-2025-siq-depth-20260712T-income-bridge-segments2"
CN_GOLDEN_FACTCHECK_FILENAME = "000333-美的集团-2025-factcheck.html"
CN_GOLDEN_TRACKING_FILENAME = "000333-美的集团-跟踪报告-2026-07-11.html"
CN_GOLDEN_TRACKING_MANIFEST = "report_manifest.json"
HTML_MIN_HAN_CHARACTERS = {
    "analysis": 1_800,
    "factcheck": 900,
    "tracking": 1_200,
}
MAX_HTML_BYTES_PER_WORKFLOW = 512 * 1024
MAX_PRESENTATION_EVIDENCE_ITEMS = 64
MAX_RAW_EVIDENCE_LINKS_PER_SECTION = 6
_HAN_CHARACTER_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HTML_LANG_ZH_CN_RE = re.compile(
    r"<html\b[^>]*\blang\s*=\s*([\"'])zh-CN\1",
    flags=re.IGNORECASE,
)
_DETAILS_RE = re.compile(
    r"<details\b(?P<attrs>[^>]*)>(?P<body>.*?)</details\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_SECTION_RE = re.compile(r"<section\b[^>]*>(.*?)</section\s*>", re.IGNORECASE | re.DOTALL)
_RAW_EVIDENCE_LINK_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*([\"'])#evidence-[^\"']+\1[^>]*>\s*"
    r"(?:<[^>]+>\s*)*<code\b[^>]*>[^<]+</code>",
    flags=re.IGNORECASE | re.DOTALL,
)
_EVIDENCE_REFERENCE_RE = re.compile(
    r'<(?:article|div)\b[^>]*\bclass\s*=\s*(["\'])evidence-reference\1',
    flags=re.IGNORECASE,
)
_EVIDENCE_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref\s*=\s*(["\'])#evidence-[^"\']+\1[^>]*>(.*?)</a\s*>',
    flags=re.IGNORECASE | re.DOTALL,
)
_APPROVE_VERDICTS = frozenset({"approve", "approved", "pass", "passed"})
_SENTIMENT_SKIP_DISCLOSURES = ("未执行", "已跳过", "来源不可用")
_SENTIMENT_ABSENCE_PATTERNS = (
    re.compile(r"(?:暂无|没有|无)(?:相关|重大|负面|异常)?舆情(?:数据|信息|事件|记录)?"),
    re.compile(r"未(?:发现|检索到|识别到).{0,8}舆情"),
)
_NO_WARNING_PATTERNS = (
    re.compile(r"无(?:活跃|重大|明显|新增|异常)?预警"),
    re.compile(r"未(?:发现|识别|触发|检出).{0,10}预警"),
    re.compile(r"没有.{0,8}预警"),
)
_NO_WARNING_CAVEATS = (
    "无法",
    "不能",
    "不足",
    "缺少",
    "不可",
    "不得",
    "不代表",
    "尚未执行",
)
_STABILITY_CLAIM_RE = re.compile(
    r"(?:整体|总体|经营|财务|趋势|指标|表现|状态|基本面)?"
    r"(?:保持|维持|延续|相对|总体)?稳定"
)
_STABILITY_NEGATIONS = ("无法", "不能", "不足", "不可", "不宜", "未能", "尚难")
_SENTIMENT_ABSENCE_NEGATIONS = ("不能据此判断", "无法据此判断", "不得判断", "不代表")
_CLAUSE_BOUNDARIES = "。；！？\n"
_FORMAL_PIPELINE_MARKERS = (
    "formal_analysis_input_bundle",
    "siq_analysis_input_bundle_v1",
    "siq_analysis_report_v2",
)


class PipelineRegression(RuntimeError):
    """Raised when a market crosses the CN/overseas workflow boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _flag_enabled(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _load_seed(seed_root: Path, market: str) -> dict[str, Any]:
    candidates = sorted((seed_root / market.lower() / "analysis").glob("*.artifact.json"))
    if len(candidates) != 1:
        raise RuntimeError(f"{market}: expected exactly one authoritative smoke seed")
    payload = json.loads(candidates[0].read_text(encoding="utf-8"))
    target = payload.get("research_target") if isinstance(payload, Mapping) else None
    if not isinstance(target, Mapping):
        raise RuntimeError(f"{market}: seed does not contain ResearchTargetV1")
    identity = target.get("research_identity")
    report = target.get("source_report")
    if not isinstance(identity, Mapping) or not isinstance(report, Mapping):
        raise RuntimeError(f"{market}: seed target is incomplete")
    if str(identity.get("market") or "").upper() != market:
        raise RuntimeError(f"{market}: seed market mismatch")
    return dict(target)


def _select_target_from_universe(
    market: str,
    *,
    wiki_root: Path | None = None,
) -> dict[str, Any]:
    preferred_code = PREFERRED_COMPANY_CODES[market].casefold()
    companies = sorted(
        enumerate_companies(wiki_root=wiki_root, markets=(market,)),
        key=lambda item: (
            0 if item.display_code.casefold() == preferred_code else 1,
            item.display_code.casefold(),
            item.display_name.casefold(),
            item.company_id,
        ),
    )
    candidates: list[ResolvedReportPackage] = []
    for company in companies:
        packages = enumerate_report_packages(
            company,
            agent_type="analysis",
            include_unready=False,
        )
        if not packages:
            continue
        candidates.extend(packages)
        if company.display_code.casefold() == preferred_code:
            break
        if candidates:
            break
    if not candidates:
        raise RuntimeError(f"{market}: no parsed-ready analysis package")

    def report_rank(package: ResolvedReportPackage) -> tuple[int, int, str, str, str]:
        source_report = package.research_target.source_report
        form = str(source_report.form_type or "").upper()
        report_type = str(source_report.report_type or "").casefold()
        preferred_form = (
            form == "10-K"
            if market == "US"
            else "annual" in report_type or "年报" in report_type or "annual" in form.casefold()
        )
        return (
            0 if preferred_form else 1,
            0 if source_report.quality_status == "pass" else 1,
            str(source_report.period_end or ""),
            str(source_report.published_at or ""),
            package.report_id,
        )

    preferred = [item for item in candidates if report_rank(item)[0] == 0]
    selected_pool = preferred or candidates
    selected = sorted(
        selected_pool,
        key=lambda item: (
            report_rank(item)[0],
            report_rank(item)[1],
            # Packages are already newest-first; preserve that authoritative
            # ordering while using report_id as a deterministic tie breaker.
            candidates.index(item),
            item.report_id,
        ),
    )[0]
    return selected.to_research_target_dict()


def _research_context(target: Mapping[str, Any], *, baseline_id: str = "") -> dict[str, Any]:
    identity = dict(target["research_identity"])
    source_report = dict(target["source_report"])
    context: dict[str, Any] = {
        "market": identity["market"],
        "company_key": target["company_key"],
        "report_id": source_report["report_id"],
        "research_identity": identity,
        "research_target": dict(target),
        "company": {
            "market": identity["market"],
            "company_key": target["company_key"],
            "company_id": identity["company_id"],
            "code": target["display_code"],
            "name": target["display_name"],
        },
        "source_report": {**source_report, **identity},
    }
    if baseline_id:
        context["upstream_analysis_artifact_id"] = baseline_id
    return context


def _find_artifact_files(
    package: ResolvedReportPackage,
    artifact_type: str,
    artifact_id: str,
) -> tuple[dict[str, Any], Path, Path]:
    resolved = resolve_artifact(
        artifact_id,
        market=package.market,
        company_key=package.company_key,
        report_id=package.report_id,
        artifact_type=artifact_type,
    )
    if resolved.sidecar_path is None or resolved.legacy_unbound:
        raise RuntimeError(f"{artifact_type}: exact AgentArtifactV2 sidecar was not discoverable")
    return resolved.artifact.to_dict(), resolved.sidecar_path, resolved.html_path


def _visible_html_text(html_text: str) -> str:
    without_non_narrative = re.sub(
        r"<(?:script|style|noscript|template|svg)\b[^>]*>.*?</(?:script|style|noscript|template|svg)\s*>",
        " ",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_comments = re.sub(r"<!--.*?-->", " ", without_non_narrative, flags=re.DOTALL)
    without_tags = re.sub(r"<[^>]+>", " ", without_comments)
    return re.sub(r"\s+", " ", html_module.unescape(without_tags)).strip()


def _safe_companion_json(
    artifact: Mapping[str, Any],
    sidecar_path: Path,
) -> Mapping[str, Any] | None:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    filename = str(metadata.get("json_file") or "").strip()
    relative = Path(filename)
    if not filename or relative.is_absolute() or len(relative.parts) != 1 or relative.suffix.lower() != ".json":
        return None
    output_dir = sidecar_path.parent.resolve()
    candidate = (output_dir / relative).resolve()
    try:
        candidate.relative_to(output_dir)
    except ValueError:
        return None
    try:
        raw = candidate.read_bytes()
    except OSError:
        return None
    content_hashes = metadata.get("content_hashes") if isinstance(metadata.get("content_hashes"), Mapping) else {}
    expected_hash = str(content_hashes.get("json") or "").removeprefix("sha256:").lower()
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _analysis_claims(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    claims = metadata.get("claims")
    if not isinstance(claims, list):
        return []
    return [item for item in claims if isinstance(item, Mapping)]


def _claim_has_evidence(claim: Mapping[str, Any]) -> bool:
    evidence_refs = claim.get("evidence_refs")
    if isinstance(evidence_refs, list) and any(isinstance(item, Mapping) for item in evidence_refs):
        return True
    evidence_ids = claim.get("evidence_ids")
    return isinstance(evidence_ids, list) and any(str(item or "").strip() for item in evidence_ids)


def _claim_is_structured(claim: Mapping[str, Any]) -> bool:
    return bool(str(claim.get("claim_id") or "").strip()) and bool(str(claim.get("claim") or "").strip())


def _evidence_id(evidence: Mapping[str, Any]) -> str:
    explicit = str(evidence.get("evidence_id") or "").strip()
    if explicit:
        return explicit
    material = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    return "evidence_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _evidence_anchor(evidence_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in evidence_id)
    return f"evidence-{safe}"


def _analysis_evidence_ids(report: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(report, Mapping):
        return []
    evidence = report.get("evidence_refs")
    if not isinstance(evidence, list):
        return []
    return list(dict.fromkeys(_evidence_id(item) for item in evidence if isinstance(item, Mapping)))


def _claim_evidence_ids(claims: list[Mapping[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(evidence_id)
            for claim in claims
            for evidence_id in claim.get("evidence_ids") or ()
            if evidence_id
        )
    )


def _evidence_catalog_blocks(html_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in _DETAILS_RE.finditer(html_text):
        attrs = match.group("attrs")
        body = match.group("body")
        marker_text = _visible_html_text(body)
        attrs_lower = attrs.lower()
        is_catalog = "evidence" in attrs_lower or (
            "证据" in marker_text and any(marker in marker_text for marker in ("目录", "索引", "溯源", "完整"))
        )
        if is_catalog:
            blocks.append((attrs, body))
    return blocks


def _catalog_contains_anchor(catalog_html: str, evidence_id: str) -> bool:
    anchor = re.escape(_evidence_anchor(evidence_id))
    return bool(
        re.search(
            rf"\bid\s*=\s*(?:[\"']{anchor}[\"']|{anchor}(?=\s|>))",
            catalog_html,
            flags=re.IGNORECASE,
        )
    )


def _has_raw_evidence_link_flood(html_text: str) -> bool:
    without_details = _DETAILS_RE.sub("", html_text)
    return any(
        len(_RAW_EVIDENCE_LINK_RE.findall(section)) > MAX_RAW_EVIDENCE_LINKS_PER_SECTION
        for section in _SECTION_RE.findall(without_details)
    )


def _has_bare_evidence_id_link(html_text: str) -> bool:
    for _quote, link_body in _EVIDENCE_LINK_RE.findall(html_text):
        label = _visible_html_text(link_body)
        if re.fullmatch(r"(?:evidence[-_:])?[0-9a-f]{24,64}|ev[-_:][\w.-]+", label, re.IGNORECASE):
            return True
    return False


def _has_comparable_periods(report: Mapping[str, Any] | None) -> bool:
    if not isinstance(report, Mapping) or not isinstance(report.get("facts"), list):
        return False
    grouped_periods: dict[tuple[str, str, str, str], set[str]] = {}
    for fact in report["facts"]:
        if not isinstance(fact, Mapping):
            continue
        metric_key = str(fact.get("metric_key") or fact.get("canonical_name") or "").strip()
        period = str(fact.get("period") or fact.get("period_end") or "").strip()
        if not metric_key or not period:
            continue
        dimensions = fact.get("dimensions") if isinstance(fact.get("dimensions"), Mapping) else {}
        key = (
            metric_key,
            str(fact.get("currency") or "").upper(),
            str(fact.get("scope") or "").lower(),
            json.dumps(dimensions, ensure_ascii=False, sort_keys=True, default=str),
        )
        grouped_periods.setdefault(key, set()).add(period)
    return any(len(periods) >= 2 for periods in grouped_periods.values())


def _claim_clause(text: str, start: int, end: int) -> str:
    left = max(text.rfind(boundary, 0, start) for boundary in _CLAUSE_BOUNDARIES)
    right_candidates = [
        position
        for boundary in _CLAUSE_BOUNDARIES
        if (position := text.find(boundary, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right]


def _tracking_overclaims_stability(text: str) -> bool:
    for pattern in _NO_WARNING_PATTERNS:
        for match in pattern.finditer(text):
            context = _claim_clause(text, match.start(), match.end())
            if not any(caveat in context for caveat in _NO_WARNING_CAVEATS):
                return True
    for match in _STABILITY_CLAIM_RE.finditer(text):
        clause = _claim_clause(text, match.start(), match.end())
        prefix = clause[: clause.find(match.group(0))]
        if not any(negation in prefix for negation in _STABILITY_NEGATIONS):
            return True
    return False


def _tracking_misrepresents_sentiment_absence(text: str) -> bool:
    for pattern in _SENTIMENT_ABSENCE_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 18) : match.start()]
            if not any(negation in prefix for negation in _SENTIMENT_ABSENCE_NEGATIONS):
                return True
    return False


def _quality_check(
    checks: list[dict[str, Any]],
    code: str,
    passed: bool,
    **metrics: int | bool,
) -> None:
    item: dict[str, Any] = {"code": code, "passed": bool(passed)}
    item.update(metrics)
    checks.append(item)


def _safe_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _expected_pipeline(market: str) -> str:
    if market == CN_MARKET:
        return "legacy_golden_readonly"
    if market in FORMAL_MARKETS:
        return "formal_bundle_v2"
    raise ValueError(f"unsupported smoke market: {market}")


def _request_uses_formal_pipeline(workflow: str, request: Any) -> bool:
    if workflow == "analysis":
        return bool(getattr(request, "formal_target", False))
    if workflow in {"factcheck", "tracking"}:
        return bool(getattr(request, "research_context", None)) or bool(
            str(getattr(request, "upstream_analysis_artifact_id", "") or "").strip()
        )
    raise ValueError(f"unsupported workflow: {workflow}")


def _assert_pipeline_request(market: str, workflow: str, request: Any) -> None:
    uses_formal = _request_uses_formal_pipeline(workflow, request)
    if market == CN_MARKET:
        if uses_formal:
            raise PipelineRegression("cn_pipeline_regression")
        if workflow == "analysis" and any(
            (
                getattr(request, "context_payload", None),
                str(getattr(request, "company_key", "") or "").strip(),
                str(getattr(request, "report_id", "") or "").strip(),
                getattr(request, "research_identity", None),
            )
        ):
            raise PipelineRegression("cn_pipeline_regression")
        if workflow == "factcheck" and getattr(request, "report_path", None) is None:
            raise PipelineRegression("cn_pipeline_regression")
        return
    if market not in FORMAL_MARKETS or not uses_formal:
        raise PipelineRegression("non_cn_pipeline_regression")
    if workflow == "analysis":
        if not getattr(request, "context_payload", None) or not getattr(
            request, "research_identity", None
        ):
            raise PipelineRegression("non_cn_pipeline_regression")
    elif not str(getattr(request, "upstream_analysis_artifact_id", "") or "").strip():
        raise PipelineRegression("non_cn_pipeline_regression")


def _contains_formal_pipeline_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key == "adapter_version" and bool(item):
                return True
            if (
                normalized_key == "adapter"
                and isinstance(item, Mapping)
                and any(adapter_key in item for adapter_key in ("source_family", "version", "adapter_version"))
            ):
                return True
            if normalized_key.startswith("agent_artifact_v2_") and bool(item):
                return True
            if _contains_formal_pipeline_marker(item):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_contains_formal_pipeline_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _FORMAL_PIPELINE_MARKERS)
    return False


def _assert_pipeline_response(
    market: str,
    workflow: str,
    result: Mapping[str, Any],
    *,
    html_text: str = "",
) -> None:
    if market == CN_MARKET:
        artifact = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
        if (
            _contains_formal_pipeline_marker(result)
            or _contains_formal_pipeline_marker(html_text)
            or artifact.get("schema_version") == "siq_agent_artifact_v2"
        ):
            raise PipelineRegression("cn_pipeline_regression")
        return
    if market not in FORMAL_MARKETS:
        raise PipelineRegression("non_cn_pipeline_regression")
    if workflow == "analysis":
        uses_formal = (
            str(result.get("pipeline_mode") or "") == "formal_analysis_input_bundle"
            and bool(str(result.get("artifact_id") or "").strip())
        )
    else:
        artifact = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
        uses_formal = artifact.get("schema_version") == "siq_agent_artifact_v2"
    if not uses_formal:
        raise PipelineRegression("non_cn_pipeline_regression")


def _safe_company_artifact_path(company_dir: Path, subdir: str, filename: str) -> Path:
    relative = Path(filename)
    if relative.is_absolute() or relative.parts != (filename,):
        raise PipelineRegression("cn_pipeline_regression")
    company_root = company_dir.resolve()
    artifact_root = (company_root / subdir).resolve()
    try:
        artifact_root.relative_to(company_root)
    except ValueError as exc:
        raise PipelineRegression("cn_pipeline_regression") from exc
    candidate = (artifact_root / relative).resolve()
    try:
        candidate.relative_to(artifact_root)
    except ValueError as exc:
        raise PipelineRegression("cn_pipeline_regression") from exc
    if not candidate.is_file():
        raise RuntimeError(f"{subdir}: golden_artifact_missing")
    return candidate


def _read_utf8(path: Path, workflow: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{workflow}: golden_artifact_unreadable") from exc


def _golden_workflow_summary(html_text: str, **metadata: bool | str) -> dict[str, Any]:
    return {
        "status": "verified",
        "pipeline_mode": "legacy_golden_readonly",
        "generated": False,
        "html_present": True,
        "html_content_hash": "sha256:" + hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
        "formal_markers_absent": not _contains_formal_pipeline_marker(html_text),
        **metadata,
    }


def _read_cn_golden_artifacts(company_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    company_path = _safe_company_artifact_path(company_dir, ".", "company.json")
    try:
        company_payload = json.loads(_read_utf8(company_path, "analysis"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("analysis: golden_company_metadata_invalid") from exc
    if not isinstance(company_payload, Mapping) or str(company_payload.get("stock_code") or "") != CN_GOLDEN_COMPANY_CODE:
        raise PipelineRegression("cn_pipeline_regression")

    analysis_html_path = _safe_company_artifact_path(
        company_dir,
        "analysis",
        f"{CN_GOLDEN_ANALYSIS_STEM}.html",
    )
    analysis_md_path = _safe_company_artifact_path(
        company_dir,
        "analysis",
        f"{CN_GOLDEN_ANALYSIS_STEM}.md",
    )
    analysis_json_path = _safe_company_artifact_path(
        company_dir,
        "analysis",
        f"{CN_GOLDEN_ANALYSIS_STEM}.json",
    )
    factcheck_html_path = _safe_company_artifact_path(
        company_dir,
        "factcheck",
        CN_GOLDEN_FACTCHECK_FILENAME,
    )
    tracking_manifest_path = _safe_company_artifact_path(
        company_dir,
        "tracking",
        CN_GOLDEN_TRACKING_MANIFEST,
    )
    try:
        tracking_manifest = json.loads(_read_utf8(tracking_manifest_path, "tracking"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("tracking: golden_manifest_invalid") from exc
    latest_report = (
        str(tracking_manifest.get("latest_report") or "")
        if isinstance(tracking_manifest, Mapping)
        else ""
    )
    if latest_report != CN_GOLDEN_TRACKING_FILENAME:
        raise PipelineRegression("cn_pipeline_regression")
    tracking_html_path = _safe_company_artifact_path(
        company_dir,
        "tracking",
        latest_report,
    )

    html_payloads = {
        "analysis": _read_utf8(analysis_html_path, "analysis"),
        "factcheck": _read_utf8(factcheck_html_path, "factcheck"),
        "tracking": _read_utf8(tracking_html_path, "tracking"),
    }
    if any(_contains_formal_pipeline_marker(text) for text in html_payloads.values()):
        raise PipelineRegression("cn_pipeline_regression")

    workflows = {
        "analysis": _golden_workflow_summary(
            html_payloads["analysis"],
            markdown_present=analysis_md_path.is_file(),
            json_present=analysis_json_path.is_file(),
        ),
        "factcheck": _golden_workflow_summary(html_payloads["factcheck"]),
        "tracking": _golden_workflow_summary(
            html_payloads["tracking"],
            manifest_latest_verified=True,
        ),
    }
    checks: list[dict[str, Any]] = []
    _quality_check(checks, "cn_golden_company_verified", True)
    for workflow, html_text in html_payloads.items():
        _quality_check(checks, f"{workflow}_golden_html_readable", bool(html_text.strip()))
        _quality_check(
            checks,
            f"{workflow}_formal_markers_absent",
            not _contains_formal_pipeline_marker(html_text),
        )
    _quality_check(checks, "analysis_golden_markdown_present", analysis_md_path.is_file())
    _quality_check(checks, "analysis_golden_json_present", analysis_json_path.is_file())
    _quality_check(checks, "tracking_manifest_latest_verified", True)
    failure_codes = [str(item["code"]) for item in checks if not item["passed"]]
    return workflows, {
        "status": "passed" if not failure_codes else "failed",
        "failure_codes": failure_codes,
        "checks": checks,
    }


def _run_content_quality_checks(
    market: str,
    artifacts: Mapping[str, tuple[Mapping[str, Any], Path, Path]],
    *,
    skip_sentiment: bool,
) -> dict[str, Any]:
    if market not in FORMAL_MARKETS:
        raise ValueError("formal v2 content quality checks only apply to overseas markets")
    checks: list[dict[str, Any]] = []
    html_by_workflow: dict[str, str] = {}
    text_by_workflow: dict[str, str] = {}
    for workflow in ("analysis", "factcheck", "tracking"):
        _artifact, _sidecar_path, html_path = artifacts[workflow]
        try:
            html_text = html_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            html_text = ""
        # Collapsed audit/technical payloads must not inflate the narrative gate.
        visible_text = _visible_html_text(_DETAILS_RE.sub("", html_text))
        html_by_workflow[workflow] = html_text
        text_by_workflow[workflow] = visible_text
        han_count = len(_HAN_CHARACTER_RE.findall(visible_text))
        _quality_check(
            checks,
            f"{workflow}_html_lang_zh_cn",
            bool(_HTML_LANG_ZH_CN_RE.search(html_text)),
        )
        _quality_check(
            checks,
            f"{workflow}_html_chinese_narrative",
            han_count >= HTML_MIN_HAN_CHARACTERS[workflow],
            observed_count=han_count,
            minimum_count=HTML_MIN_HAN_CHARACTERS[workflow],
        )
        html_bytes = len(html_text.encode("utf-8"))
        _quality_check(
            checks,
            f"{workflow}_html_payload_bounded",
            html_bytes <= MAX_HTML_BYTES_PER_WORKFLOW,
            observed_count=html_bytes,
            maximum_count=MAX_HTML_BYTES_PER_WORKFLOW,
        )
        if market != "CN":
            _quality_check(
                checks,
                f"{workflow}_html_market_unit",
                "亿元" not in html_text,
            )

    analysis_artifact, analysis_sidecar, _analysis_html = artifacts["analysis"]
    analysis_report = _safe_companion_json(analysis_artifact, analysis_sidecar)
    claims = _analysis_claims(analysis_artifact)
    _quality_check(checks, "analysis_structured_claims_present", bool(claims))
    _quality_check(
        checks,
        "analysis_structured_claims_valid",
        bool(claims) and all(_claim_is_structured(claim) for claim in claims),
        claim_count=len(claims),
    )
    _quality_check(
        checks,
        "analysis_claims_evidence_bound",
        bool(claims) and all(_claim_has_evidence(claim) for claim in claims),
        claim_count=len(claims),
    )
    _quality_check(checks, "analysis_companion_json_available", analysis_report is not None)

    catalog_blocks = _evidence_catalog_blocks(html_by_workflow["analysis"])
    _quality_check(checks, "analysis_evidence_catalog_present", bool(catalog_blocks))
    catalogs_collapsed = bool(catalog_blocks) and all(
        not re.search(r"(?:^|\s)open(?:\s|=|$)", attrs, flags=re.IGNORECASE) for attrs, _body in catalog_blocks
    )
    _quality_check(checks, "analysis_evidence_catalog_collapsed", catalogs_collapsed)
    full_evidence_ids = _analysis_evidence_ids(analysis_report)
    expected_evidence_ids = _claim_evidence_ids(claims)
    catalog_html = "".join(body for _attrs, body in catalog_blocks)
    catalog_complete = bool(expected_evidence_ids) and all(
        _catalog_contains_anchor(catalog_html, evidence_id) for evidence_id in expected_evidence_ids
    )
    _quality_check(
        checks,
        "analysis_evidence_catalog_complete",
        catalog_complete,
        evidence_count=len(expected_evidence_ids),
    )
    rendered_catalog_count = len(_EVIDENCE_REFERENCE_RE.findall(catalog_html))
    _quality_check(
        checks,
        "analysis_evidence_catalog_bounded",
        0 < rendered_catalog_count <= MAX_PRESENTATION_EVIDENCE_ITEMS,
        observed_count=rendered_catalog_count,
        maximum_count=MAX_PRESENTATION_EVIDENCE_ITEMS,
    )
    analysis_metadata = (
        analysis_artifact.get("metadata")
        if isinstance(analysis_artifact.get("metadata"), Mapping)
        else {}
    )
    catalog_metadata = (
        analysis_metadata.get("evidence_catalog")
        if isinstance(analysis_metadata.get("evidence_catalog"), Mapping)
        else {}
    )
    _quality_check(
        checks,
        "analysis_full_evidence_preserved_in_companion",
        bool(full_evidence_ids)
        and _safe_int(catalog_metadata.get("total_count")) == len(full_evidence_ids)
        and _safe_int(catalog_metadata.get("rendered_count")) == rendered_catalog_count
        and str(catalog_metadata.get("full_evidence_file") or "").endswith(".json"),
        evidence_count=len(full_evidence_ids),
        rendered_count=rendered_catalog_count,
    )
    _quality_check(
        checks,
        "analysis_no_bare_evidence_id_links",
        not _has_bare_evidence_id_link(html_by_workflow["analysis"]),
    )
    _quality_check(
        checks,
        "analysis_sections_without_raw_evidence_flood",
        not _has_raw_evidence_link_flood(html_by_workflow["analysis"]),
        maximum_links_per_section=MAX_RAW_EVIDENCE_LINKS_PER_SECTION,
    )

    factcheck_artifact, factcheck_sidecar, _factcheck_html = artifacts["factcheck"]
    factcheck_metadata = (
        factcheck_artifact.get("metadata") if isinstance(factcheck_artifact.get("metadata"), Mapping) else {}
    )
    factcheck_report = _safe_companion_json(factcheck_artifact, factcheck_sidecar)
    verdict = str(factcheck_metadata.get("verdict") or "").strip().lower()
    if not verdict and isinstance(factcheck_report, Mapping):
        verdict = str(factcheck_report.get("verdict") or "").strip().lower()
    _quality_check(
        checks,
        "factcheck_never_approves_without_claims",
        bool(claims) or verdict not in _APPROVE_VERDICTS,
    )
    checked_claim_count = _safe_int(factcheck_metadata.get("checked_claim_count"))
    verified_claim_count = _safe_int(factcheck_metadata.get("verified_claim_count"))
    contradicted_claim_count = _safe_int(factcheck_metadata.get("contradicted_claim_count"))
    unsupported_claim_count = _safe_int(factcheck_metadata.get("unsupported_claim_count"))
    _quality_check(
        checks,
        "factcheck_all_analysis_claims_checked",
        bool(claims) and checked_claim_count == len(claims),
        analysis_claim_count=len(claims),
        checked_claim_count=checked_claim_count,
    )
    _quality_check(
        checks,
        "factcheck_claims_supported",
        checked_claim_count > 0
        and verified_claim_count == checked_claim_count
        and contradicted_claim_count == 0
        and unsupported_claim_count == 0,
        checked_claim_count=checked_claim_count,
        verified_claim_count=verified_claim_count,
        contradicted_claim_count=contradicted_claim_count,
        unsupported_claim_count=unsupported_claim_count,
    )

    tracking_text = text_by_workflow["tracking"]
    if skip_sentiment:
        _quality_check(
            checks,
            "tracking_sentiment_skip_disclosed",
            any(marker in tracking_text for marker in _SENTIMENT_SKIP_DISCLOSURES),
        )
        _quality_check(
            checks,
            "tracking_sentiment_skip_not_misrepresented",
            not _tracking_misrepresents_sentiment_absence(tracking_text),
        )
    has_comparable_periods = _has_comparable_periods(analysis_report)
    _quality_check(
        checks,
        "tracking_no_comparable_period_not_overclaimed",
        has_comparable_periods or not _tracking_overclaims_stability(tracking_text),
        comparable_periods_available=has_comparable_periods,
    )

    failure_codes = [str(item["code"]) for item in checks if not item["passed"]]
    return {
        "status": "passed" if not failure_codes else "failed",
        "failure_codes": failure_codes,
        "checks": checks,
    }


def _artifact_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    quality = payload.get("quality") if isinstance(payload.get("quality"), Mapping) else {}
    evidence = payload.get("evidence_summary") if isinstance(payload.get("evidence_summary"), Mapping) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    safe_metadata: dict[str, Any] = {}
    for key in (
        "research_pack_validation_status",
        "verdict",
        "checked_claim_count",
        "verified_claim_count",
        "contradicted_claim_count",
        "unsupported_claim_count",
        "identity_mismatch_count",
        "citation_locator_failure_count",
        "pipeline_status",
        "degraded_reasons",
        "module_status",
        "analysis_baseline_content_hash",
        "evidence_catalog",
    ):
        if key in metadata:
            safe_metadata[key] = metadata[key]
    return {
        "artifact_id": payload.get("artifact_id"),
        "status": payload.get("status"),
        "source_report_id": payload.get("source_report_id"),
        "source_family": payload.get("source_family"),
        "adapter_version": payload.get("adapter_version"),
        "upstream_artifact_ids": list(payload.get("upstream_artifact_ids") or []),
        "content_hash": payload.get("content_hash"),
        "quality": {
            "status": quality.get("status"),
            "warnings": list(quality.get("warnings") or []),
        },
        "evidence_summary": {
            "citation_count": evidence.get("citation_count"),
            "unresolved_count": evidence.get("unresolved_count"),
        },
        "metadata": safe_metadata,
    }


def _artifact_id(result: Mapping[str, Any], artifact_type: str) -> str:
    if artifact_type == "analysis":
        value = result.get("artifact_id")
    else:
        artifact = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
        value = artifact.get("artifact_id") if artifact.get("schema_version") == "siq_agent_artifact_v2" else ""
    artifact_id = str(value or "").strip()
    if not artifact_id:
        raise RuntimeError(f"{artifact_type}: workflow did not publish AgentArtifactV2")
    return artifact_id


def _run_market(seed_root: Path | None, market: str, timeout: float) -> dict[str, Any]:
    target = (
        _select_target_from_universe(market)
        if market == CN_MARKET or seed_root is None
        else _load_seed(seed_root, market)
    )
    if market == CN_MARKET and str(target.get("display_code") or "") != CN_GOLDEN_COMPANY_CODE:
        raise PipelineRegression("cn_pipeline_regression")
    context = _research_context(target)
    package = resolve_report_package_from_context(context, agent_type="analysis")
    before = snapshot_company_fact_surface(package.company_dir)
    expected_pipeline = _expected_pipeline(market)
    record: dict[str, Any] = {
        "market": market,
        "pipeline": {
            "expected": expected_pipeline,
            "validated": False,
            "generated": market != CN_MARKET,
        },
        "input": {
            "company_key": target["company_key"],
            "company_wiki_id": target["company_wiki_id"],
            "display_code": target["display_code"],
            "display_name": target["display_name"],
            "research_identity": dict(target["research_identity"]),
            "source_report": {
                key: target["source_report"].get(key)
                for key in (
                    "report_id",
                    "source_family",
                    "document_format",
                    "report_type",
                    "form_type",
                    "fiscal_year",
                    "period_end",
                    "accounting_standard",
                    "quality_status",
                )
            },
        },
        "workflows": {},
        "fact_surface": {
            "before_digest": before.digest,
            "before_file_count": len(before.files),
        },
    }
    error: dict[str, str] | None = None
    artifact_files: dict[str, tuple[Mapping[str, Any], Path, Path]] = {}
    try:
        if market == CN_MARKET:
            golden_workflows, compatibility_checks = _read_cn_golden_artifacts(
                package.company_dir
            )
            record["workflows"].update(golden_workflows)
            record["legacy_compatibility_checks"] = compatibility_checks
            if compatibility_checks["status"] != "passed":
                raise PipelineRegression("cn_pipeline_regression")
        else:
            source_report = target["source_report"]
            request_year = int(source_report.get("fiscal_year") or 2025)
            analysis_request = AnalysisReportWorkflowRequest(
                company_query=str(target["display_code"]),
                year=request_year,
                formal_target=True,
                context_payload=context,
                company_key=str(target["company_key"]),
                report_id=str(source_report["report_id"]),
                research_identity=dict(target["research_identity"]),
            )
            _assert_pipeline_request(market, "analysis", analysis_request)
            analysis_response = run_analysis_report_workflow(analysis_request, timeout=timeout)
            if not analysis_response.result.get("ok"):
                raise RuntimeError(
                    f"analysis: {analysis_response.result.get('stage') or 'workflow_failed'}"
                )
            _assert_pipeline_response(market, "analysis", analysis_response.result)
            analysis_id = _artifact_id(analysis_response.result, "analysis")
            analysis_bundle = _find_artifact_files(package, "analysis", analysis_id)
            analysis = analysis_bundle[0]
            if analysis.get("status") not in SUCCESS_STATUSES:
                raise RuntimeError("analysis: non-terminal artifact status")
            artifact_files["analysis"] = analysis_bundle
            record["workflows"]["analysis"] = _artifact_summary(analysis)

            specialist_context = _research_context(target, baseline_id=analysis_id)
            factcheck_request = FactcheckWorkflowRequest(
                company_query=str(target["display_code"]),
                year=request_year,
                research_context=specialist_context,
                upstream_analysis_artifact_id=analysis_id,
            )
            _assert_pipeline_request(market, "factcheck", factcheck_request)
            factcheck_response = run_factcheck_workflow(factcheck_request, timeout=timeout)
            if not factcheck_response.result.get("ok"):
                raise RuntimeError(
                    f"factcheck: {factcheck_response.result.get('stage') or 'workflow_failed'}"
                )
            _assert_pipeline_response(market, "factcheck", factcheck_response.result)
            factcheck_id = _artifact_id(factcheck_response.result, "factcheck")
            factcheck_bundle = _find_artifact_files(package, "factcheck", factcheck_id)
            factcheck = factcheck_bundle[0]
            if factcheck.get("status") not in SUCCESS_STATUSES:
                raise RuntimeError("factcheck: non-terminal artifact status")
            if factcheck.get("upstream_artifact_ids") != [analysis_id]:
                raise RuntimeError("factcheck: exact analysis baseline mismatch")
            artifact_files["factcheck"] = factcheck_bundle
            record["workflows"]["factcheck"] = _artifact_summary(factcheck)

            tracking_request = TrackingWorkflowRequest(
                company_query=str(target["display_code"]),
                research_context=specialist_context,
                upstream_analysis_artifact_id=analysis_id,
                skip_sentiment=True,
                use_search=False,
            )
            _assert_pipeline_request(market, "tracking", tracking_request)
            tracking_response = run_tracking_workflow(tracking_request, timeout=timeout)
            if not tracking_response.result.get("ok"):
                raise RuntimeError(
                    f"tracking: {tracking_response.result.get('stage') or 'workflow_failed'}"
                )
            _assert_pipeline_response(market, "tracking", tracking_response.result)
            tracking_id = _artifact_id(tracking_response.result, "tracking")
            tracking_bundle = _find_artifact_files(package, "tracking", tracking_id)
            tracking = tracking_bundle[0]
            if tracking.get("status") not in SUCCESS_STATUSES:
                raise RuntimeError("tracking: non-terminal artifact status")
            if tracking.get("upstream_artifact_ids") != [analysis_id]:
                raise RuntimeError("tracking: exact analysis baseline mismatch")
            artifact_files["tracking"] = tracking_bundle
            record["workflows"]["tracking"] = _artifact_summary(tracking)
            quality_checks = _run_content_quality_checks(
                market,
                artifact_files,
                skip_sentiment=True,
            )
            record["quality_checks"] = quality_checks
            if quality_checks["status"] != "passed":
                error = {
                    "stage": "quality",
                    "code": str(quality_checks["failure_codes"][0]),
                }
        record["pipeline"]["validated"] = error is None
    except PipelineRegression as exc:
        error = {"stage": "pipeline", "code": exc.code}
    except Exception as exc:
        text = str(exc)
        stage, _, detail = text.partition(":")
        error = {
            "stage": stage if stage in {"analysis", "factcheck", "tracking"} else "smoke",
            "code": detail.strip() or type(exc).__name__,
        }
    finally:
        after = snapshot_company_fact_surface(package.company_dir)
        unchanged = before == after
        record["fact_surface"].update(
            {
                "after_digest": after.digest,
                "after_file_count": len(after.files),
                "unchanged": unchanged,
            }
        )
        if unchanged:
            assert_fact_surface_unchanged(before, after)
        elif error is None:
            error = {"stage": "fact_surface", "code": "protected_inputs_changed"}
    if error is not None:
        record["error"] = error
    record["pipeline"]["validated"] = error is None and len(record["workflows"]) == 3
    record["passed"] = bool(record["pipeline"]["validated"])
    return record


def _reject_sensitive_strings(value: Any) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_sensitive_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_sensitive_strings(item)
    elif isinstance(value, str):
        lowered = value.lower()
        if value.startswith("/") or "/home/" in lowered or "file://" in lowered:
            raise RuntimeError("sanitized smoke output contains a local filesystem path")


def run(seed_root: Path | None, output: Path, timeout: float) -> dict[str, Any]:
    if not _flag_enabled("SIQ_MULTI_MARKET_RESEARCH_ENABLED"):
        raise RuntimeError("SIQ_MULTI_MARKET_RESEARCH_ENABLED must be enabled")
    if not _flag_enabled("SIQ_US_SEC_ANALYSIS_ENABLED"):
        raise RuntimeError("SIQ_US_SEC_ANALYSIS_ENABLED must be enabled")
    records = [_run_market(seed_root, market, timeout) for market in MARKETS]
    passed = all(item["passed"] for item in records)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_gate": "passed" if passed else "failed",
        "scope": {
            "markets": list(MARKETS),
            "workflows": ["analysis", "factcheck", "tracking"],
            "source": "authoritative_local_wiki",
            "target_selection": (
                "explicit_seed_override" if seed_root is not None else "deterministic_research_universe"
            ),
            "tracking_external_sources": "disabled_for_deterministic_smoke",
            "pipeline_strategy": {
                "CN": "legacy_golden_readonly",
                "overseas": "formal_bundle_v2",
            },
            "cn_target_selection": "fixed_golden_000333",
            "formal_quality_gate_markets": list(FORMAL_MARKETS),
        },
        "summary": {
            "market_count": len(records),
            "passed_market_count": sum(1 for item in records if item["passed"]),
            "formal_quality_market_count": len(FORMAL_MARKETS),
            "quality_passed_market_count": sum(
                1 for item in records if item.get("quality_checks", {}).get("status") == "passed"
            ),
            "legacy_compatibility_market_count": 1,
            "legacy_compatibility_passed_market_count": sum(
                1
                for item in records
                if item.get("legacy_compatibility_checks", {}).get("status") == "passed"
            ),
            "fact_surface_unchanged_count": sum(1 for item in records if item["fact_surface"]["unchanged"]),
        },
        "markets": records,
        "sanitization": {
            "report_bodies": "excluded",
            "prompts": "excluded",
            "credentials": "excluded",
            "local_paths": "excluded",
        },
    }
    _reject_sensitive_strings(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed-root",
        type=Path,
        default=None,
        help="Optional override containing one authoritative analysis smoke sidecar per market.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "artifacts/secondary-market-multi-market/real-smoke.sanitized.json",
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    seed_root = args.seed_root.resolve() if args.seed_root is not None else None
    payload = run(seed_root, args.output.resolve(), args.timeout)
    print(
        json.dumps(
            {
                "release_gate": payload["release_gate"],
                **payload["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["release_gate"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
