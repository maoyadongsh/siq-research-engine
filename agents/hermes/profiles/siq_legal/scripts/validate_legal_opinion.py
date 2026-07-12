#!/usr/bin/env python3
"""Validate SIQ legal opinion artifacts before finalizing.

Checks:
- File exists and is non-empty.
- Required sections (摘要 / 事实背景 / 适用法规 / 法律分析 / 风险提示 / 结论 / 引用来源 / 免责声明) present.
- Citation block has >= 3 entries with source_path + chunk_index fields.
- No unresolved placeholder ({{...}}, TODO, 待补充).
- HTML <section>/<h2> structure balanced if applicable.
- No dark-theme color codes for HTML.
- Disclaimer phrase present ("不替代执业律师").
- Legal/professional tone: no casual assurances or absolute legal promises.

Exit codes: 0 OK, 2 failures, 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = [
    "摘要",
    "事实背景",
    "适用法规",
    "法律分析",
    "风险提示",
    "结论",
    "引用来源",
    "免责声明",
]

DARK_THEME_PATTERNS = [
    r"background-color\s*:\s*#0[0-9a-f]",  # #0xx very dark
    r"background-color\s*:\s*#1[0-2]",     # #10x..#12x dark
    r"background-color\s*:\s*#000",
    r"--bg-primary\s*:\s*#0[0-9a-f]",
    r"--bg-primary\s*:\s*#1[0-2]",
    r"linear-gradient\(135deg,\s*#0",
    r"linear-gradient\(135deg,\s*#1",
]

PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}|TODO|待补充|<占位>")
CITATION_LINE_RE = re.compile(
    r"\[\d+\][^\n]*?source\s*=[^\n]*?source_path\s*=[^\n]*?chunk_index\s*="
)
DISCLAIMER_TERMS = ["不替代执业律师", "执业律师判断", "不构成最终法律意见"]
MECHANICAL_TEMPLATE_PHRASES = [
    "同上结构",
    "核心问题一",
    "核心问题二",
    "用一段自然段",
    "填入 hybrid_search",
    "与本意见的关联",
]
CASUAL_OR_ABSOLUTE_PATTERNS = [
    r"完全没问题",
    r"放心",
    r"百分之百",
    r"包过",
    r"绝对(?:合法|合规|安全|不会)",
    r"一定(?:合法|合规|不会|能通过|无需)",
    r"肯定(?:合法|合规|不会|无需)",
    r"无需任何(?:披露|审批|核实|复核)",
    r"不会被(?:处罚|问询|监管关注)",
]
CONDITIONAL_TONE_TERMS = [
    "基于现有事实",
    "基于目前",
    "初步",
    "倾向",
    "待核实",
    "需补充",
    "需进一步核实",
    "检索局限",
]
ACTION_TONE_TERMS = [
    "建议",
    "措施",
    "整改",
    "补充",
    "核实",
    "提交",
    "披露",
    "跟踪",
    "复核",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_failures(text: str, kind: str) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    for section in REQUIRED_SECTIONS:
        if section not in text:
            failures.append(f"missing_section:{section}")

    placeholders = sorted(set(PLACEHOLDER_RE.findall(text)))
    if placeholders:
        failures.append("unresolved_placeholders:" + ",".join(placeholders[:10]))

    mechanical = [phrase for phrase in MECHANICAL_TEMPLATE_PHRASES if phrase in text]
    if mechanical:
        failures.append("mechanical_template_residue:" + ",".join(mechanical[:10]))

    citation_lines = CITATION_LINE_RE.findall(text)
    if len(citation_lines) < 3:
        failures.append(f"too_few_citations:{len(citation_lines)}")
    elif len(citation_lines) < 5:
        warnings.append(f"weak_citation_coverage:{len(citation_lines)}")

    if not any(term in text for term in DISCLAIMER_TERMS):
        failures.append("missing_disclaimer")

    if kind == "html":
        # Section balance.
        open_sections = len(re.findall(r"<section\b", text))
        close_sections = text.count("</section>")
        if open_sections != close_sections:
            failures.append(f"html_section_unbalanced:{open_sections}!={close_sections}")
        for pattern in DARK_THEME_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                failures.append(f"dark_theme_detected:{pattern}")
        h2_count = text.count("<h2")
        if h2_count < len(REQUIRED_SECTIONS) - 1:
            warnings.append(f"weak_h2_structure:{h2_count}")

    # Quote quality: each citation should ideally include a quote field.
    quoted = len(re.findall(r"quote\s*=", text))
    if quoted < max(1, len(citation_lines) // 2):
        warnings.append(f"weak_quote_coverage:{quoted}/{len(citation_lines)}")

    # Forbidden language: model must not over-claim violations without source.
    for phrase in ["已构成违法", "已构成犯罪", "确属违规", "违法犯罪"]:
        if phrase in text and "监管处罚" not in text and "立案调查" not in text:
            failures.append(f"unsupported_definitive_language:{phrase}")

    for pattern in CASUAL_OR_ABSOLUTE_PATTERNS:
        if re.search(pattern, text):
            failures.append(f"unprofessional_or_absolute_tone:{pattern}")

    if not any(term in text for term in CONDITIONAL_TONE_TERMS):
        warnings.append("weak_legal_tone:missing_conditional_language")
    if not any(term in text for term in ACTION_TONE_TERMS):
        warnings.append("weak_legal_tone:missing_actionable_recommendation")

    return failures, warnings


def validate(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "failures": [f"file_not_found:{path}"], "warnings": []}
    text = read(path)
    if not text.strip():
        return {"ok": False, "failures": ["empty_file"], "warnings": []}

    suffix = path.suffix.lstrip(".").lower()
    kind = "html" if suffix == "html" else "md"
    failures, warnings = collect_failures(text, kind)
    return {
        "ok": not failures,
        "kind": kind,
        "path": str(path),
        "failures": failures,
        "warnings": warnings,
        "size_bytes": path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a legal opinion artifact")
    parser.add_argument("path", type=Path, help="Path to legal opinion .md or .html")
    parser.add_argument("--write-json", type=Path, help="Optional path to write the result JSON")
    args = parser.parse_args()

    result = validate(args.path)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_json.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - guard final fallback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
