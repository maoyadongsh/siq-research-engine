#!/usr/bin/env python3
"""Repair oversized evidence catalogs in generated non-CN analysis HTML."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_MARKETS = ("hk", "us", "eu", "kr", "jp")
MAX_EVIDENCE_ITEMS = 64
CATALOG_STYLE = (
    ".evidence-catalog-body{max-height:min(70vh,960px);overflow:auto;"
    "overscroll-behavior:contain;padding:0 12px 18px 0}"
)
DETAILS_PATTERN = re.compile(
    r'<details class="evidence-catalog">.*?</details>',
    flags=re.DOTALL,
)
ARTICLE_PATTERN = re.compile(
    r'<article class="evidence-reference" id="evidence-([^\"]+)">.*?</article>',
    flags=re.DOTALL,
)
EVIDENCE_LINK_PATTERN = re.compile(r'href=["\']#evidence-([^"\']+)["\']')
EVIDENCE_ANCHOR_PATTERN = re.compile(
    r'<a\b(?=[^>]*href=["\']#evidence-([^"\']+)["\'])[^>]*>(.*?)</a>',
    flags=re.DOTALL,
)


@dataclass(frozen=True)
class RepairResult:
    path: Path
    changed: bool
    before_items: int
    after_items: int
    before_bytes: int
    after_bytes: int


def _bounded_catalog(html_text: str, catalog: str, *, limit: int) -> tuple[str, int, int]:
    articles = [(match.group(1), match.group(0)) for match in ARTICLE_PATTERN.finditer(catalog)]
    if not articles:
        return catalog, 0, 0
    if len(articles) <= limit:
        return catalog, len(articles), len(articles)
    visible_html = html_text.replace(catalog, "", 1)
    required_ids = list(dict.fromkeys(EVIDENCE_LINK_PATTERN.findall(visible_html)))
    article_by_id = dict(articles)
    selected_ids = [evidence_id for evidence_id in required_ids if evidence_id in article_by_id]
    for evidence_id, _ in articles:
        if len(selected_ids) >= limit:
            break
        if evidence_id not in selected_ids:
            selected_ids.append(evidence_id)
    selected = [article_by_id[evidence_id] for evidence_id in selected_ids[:limit]]
    replacement = (
        '<details class="evidence-catalog">'
        f"<summary>展开核心结论证据（{len(selected)} 条，默认折叠；完整证据见 JSON 结构化附件）</summary>"
        '<div class="evidence-catalog-body"><div class="evidence-group"><h3>核心结论证据</h3>'
        + "".join(selected)
        + "</div></div></details>"
    )
    return replacement, len(articles), len(selected)


def repair_html_text(html_text: str, *, limit: int = MAX_EVIDENCE_ITEMS) -> tuple[str, int, int]:
    match = DETAILS_PATTERN.search(html_text)
    if not match:
        return html_text, 0, 0
    catalog = match.group(0)
    replacement, before_items, after_items = _bounded_catalog(html_text, catalog, limit=limit)
    repaired = html_text[: match.start()] + replacement + html_text[match.end() :]
    if CATALOG_STYLE not in repaired:
        repaired = repaired.replace("</style>", CATALOG_STYLE + "</style>", 1)
    rendered_targets = {match.group(1) for match in ARTICLE_PATTERN.finditer(replacement)}
    repaired = EVIDENCE_ANCHOR_PATTERN.sub(
        lambda anchor: anchor.group(0) if anchor.group(1) in rendered_targets else anchor.group(2),
        repaired,
    )
    visible_links = set(EVIDENCE_LINK_PATTERN.findall(repaired.replace(replacement, "", 1)))
    missing_targets = visible_links - rendered_targets
    if missing_targets:
        raise ValueError(f"repair would remove visible evidence targets: {sorted(missing_targets)}")
    return repaired.rstrip() + "\n", before_items, after_items


def repair_file(path: Path, *, limit: int = MAX_EVIDENCE_ITEMS, dry_run: bool = False) -> RepairResult:
    original = path.read_text(encoding="utf-8")
    repaired, before_items, after_items = repair_html_text(original, limit=limit)
    changed = repaired != original
    if changed and not dry_run:
        path.write_text(repaired, encoding="utf-8")
    return RepairResult(
        path=path,
        changed=changed,
        before_items=before_items,
        after_items=after_items,
        before_bytes=len(original.encode("utf-8")),
        after_bytes=len(repaired.encode("utf-8")),
    )


def iter_analysis_html(wiki_root: Path) -> list[Path]:
    paths: list[Path] = []
    for market in SUPPORTED_MARKETS:
        paths.extend(sorted((wiki_root / market / "companies").glob("*/analysis/*.html")))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wiki-root",
        type=Path,
        default=Path(__file__).resolve().parents[5] / "data" / "wiki",
    )
    parser.add_argument("--limit", type=int, default=MAX_EVIDENCE_ITEMS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changed = 0
    for path in iter_analysis_html(args.wiki_root.resolve()):
        result = repair_file(path, limit=max(1, args.limit), dry_run=args.dry_run)
        if result.changed:
            changed += 1
            print(
                f"{path}: evidence {result.before_items}->{result.after_items}, "
                f"bytes {result.before_bytes}->{result.after_bytes}"
            )
    print(f"repaired={changed} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
