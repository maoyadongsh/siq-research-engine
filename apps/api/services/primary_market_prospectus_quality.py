"""Quality and capability assessment for archived A-share prospectus runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services import deal_store

PROSPECTUS_QUALITY_SCHEMA = "siq_primary_market_prospectus_quality_v1"
MIN_CANONICAL_TEXT_CHARS = 1_000
MIN_TRACEABLE_BLOCK_RATIO = 0.5

PROSPECTUS_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("offering_and_material_matters", ("发行概况", "本次发行", "重大事项提示")),
    ("risk_factors", ("风险因素", "风险提示")),
    ("issuer_and_ownership", ("发行人基本情况", "历史沿革", "股权结构")),
    ("business_and_technology", ("业务与技术", "主营业务", "核心技术")),
    ("industry_and_competition", ("行业", "竞争格局", "市场地位")),
    ("governance_and_related_parties", ("公司治理", "独立性", "同业竞争", "关联交易")),
    ("financial_and_mda", ("财务会计信息", "管理层分析", "财务状况")),
    ("use_of_proceeds", ("募集资金运用", "募集资金用途")),
    ("investor_protection_and_legal", ("投资者保护", "重要合同", "诉讼", "重大事项")),
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _canonical_markdown(run_dir: Path) -> tuple[str, str | None]:
    for name in ("document.md", "result_complete.md", "result.md"):
        path = run_dir / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if text.strip():
            return text, name

    document_full = _read_json(run_dir / "document_full.json")
    if isinstance(document_full, dict):
        markdown = document_full.get("markdown")
        if isinstance(markdown, dict):
            text = str(markdown.get("content") or "")
            if text.strip():
                return text, "document_full.json#markdown.content"
    return "", None


def _content_blocks(run_dir: Path) -> list[dict[str, Any]]:
    for name in ("content_list_enhanced.json", "content_list.json"):
        payload = _read_json(run_dir / name)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("blocks", "items", "content_list"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            pages = payload.get("pages")
            if isinstance(pages, list):
                blocks: list[dict[str, Any]] = []
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    page_number = page.get("page") or page.get("page_number") or page.get("page_idx")
                    page_blocks = page.get("blocks") or page.get("items") or []
                    if isinstance(page_blocks, list):
                        for block in page_blocks:
                            if isinstance(block, dict):
                                blocks.append({"_page": page_number, **block})
                if blocks:
                    return blocks
    return []


def _traceable(block: dict[str, Any]) -> bool:
    page = block.get("page")
    if page is None:
        page = block.get("page_number")
    if page is None:
        page = block.get("page_idx")
    if page is None:
        page = block.get("_page")
    return page is not None


def _financial_status(run_dir: Path) -> tuple[str, list[str]]:
    checks = _read_json(run_dir / "financial_checks.json")
    data = _read_json(run_dir / "financial_data.json")
    warnings: list[str] = []
    if not isinstance(checks, dict):
        return "blocked", ["financial_checks_missing_or_invalid"]

    overall = str(checks.get("overall_status") or checks.get("status") or "").strip().lower()
    if overall in {"fail", "failed", "error", "blocked", "invalid"}:
        return "blocked", [f"financial_checks_{overall}"]
    if overall in {"pass", "passed", "ok", "ready", "success"}:
        if not isinstance(data, dict):
            return "blocked", ["financial_data_missing_or_invalid"]
        return "ready", warnings
    warnings.append("financial_checks_not_conclusive")
    return "blocked", warnings


def _section_coverage(markdown: str) -> dict[str, Any]:
    found: list[str] = []
    missing: list[str] = []
    matches: dict[str, list[str]] = {}
    for key, aliases in PROSPECTUS_SECTIONS:
        hits = [alias for alias in aliases if alias in markdown]
        matches[key] = hits
        (found if hits else missing).append(key)
    ratio = len(found) / len(PROSPECTUS_SECTIONS)
    return {
        "required_count": len(PROSPECTUS_SECTIONS),
        "found_count": len(found),
        "coverage_ratio": round(ratio, 4),
        "found": found,
        "missing": missing,
        "matches": matches,
    }


def evaluate_prospectus_quality(
    run_dir: Path | str,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate stable analysis capabilities from an immutable Deal parse run."""

    root = Path(run_dir)
    markdown, markdown_source = _canonical_markdown(root)
    markdown_chars = len(markdown.strip())
    text_ready = markdown_chars >= MIN_CANONICAL_TEXT_CHARS

    blocks = _content_blocks(root)
    traceable_count = sum(1 for block in blocks if _traceable(block))
    trace_ratio = traceable_count / len(blocks) if blocks else 0.0
    trace_ready = bool(blocks) and trace_ratio >= MIN_TRACEABLE_BLOCK_RATIO

    financial_capability, financial_warnings = _financial_status(root)
    coverage = _section_coverage(markdown)
    warnings = list(financial_warnings)
    blockers: list[str] = []
    if not text_ready:
        blockers.append("canonical_markdown_missing_or_too_short")
    if not trace_ready:
        blockers.append("source_page_trace_unavailable")
    if text_ready and coverage["coverage_ratio"] < 0.45:
        warnings.append("prospectus_section_coverage_low")

    capabilities = {
        "text_evidence": "ready" if text_ready else "blocked",
        "source_page_trace": "ready" if trace_ready else "blocked",
        "financial_facts": financial_capability,
        "semantic_index": "pending",
    }
    if not text_ready or not trace_ready:
        source_status = "blocked"
    elif financial_capability == "ready" and not warnings:
        source_status = "ready"
    elif warnings and financial_capability == "ready":
        source_status = "review_required"
    else:
        source_status = "ready_with_restrictions"

    return {
        "schema_version": PROSPECTUS_QUALITY_SCHEMA,
        "generated_at": generated_at or deal_store.utc_now_iso(),
        "status": source_status,
        "capabilities": capabilities,
        "canonical_markdown": {
            "artifact": markdown_source,
            "characters": markdown_chars,
            "minimum_characters": MIN_CANONICAL_TEXT_CHARS,
        },
        "source_trace": {
            "block_count": len(blocks),
            "traceable_block_count": traceable_count,
            "traceable_ratio": round(trace_ratio, 4),
            "minimum_ratio": MIN_TRACEABLE_BLOCK_RATIO,
        },
        "section_coverage": coverage,
        "warnings": sorted(set(warnings)),
        "blockers": sorted(set(blockers)),
    }


def write_prospectus_quality_report(
    run_dir: Path | str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = Path(run_dir)
    path = root / "quality_report.json"
    if path.exists() and not overwrite:
        existing = _read_json(path)
        if isinstance(existing, dict) and existing.get("schema_version") == PROSPECTUS_QUALITY_SCHEMA:
            return existing
    report = evaluate_prospectus_quality(root)
    deal_store.write_json(path, report)
    return report
