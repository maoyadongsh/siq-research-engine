from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from repair_overseas_analysis_html import CATALOG_STYLE, repair_html_text  # noqa: E402


def test_repair_bounds_catalog_and_keeps_visible_claim_targets() -> None:
    articles = "".join(
        f'<article class="evidence-reference" id="evidence-ev-{index}"><p>{index}</p></article>'
        for index in range(100)
    )
    source = (
        "<html><head><style>body{margin:0}</style></head><body>"
        '<a href="#evidence-ev-99">关键证据</a>'
        '<details class="evidence-catalog"><summary>证据</summary>'
        f'<div class="evidence-catalog-body">{articles}</div></details>'
        "</body></html>"
    )

    repaired, before_items, after_items = repair_html_text(source, limit=8)

    assert before_items == 100
    assert after_items == 8
    assert repaired.count('class="evidence-reference"') == 8
    assert 'id="evidence-ev-99"' in repaired
    assert CATALOG_STYLE in repaired
    assert "完整证据见 JSON 结构化附件" in repaired


def test_repair_leaves_non_catalog_html_unchanged() -> None:
    source = "<html><body><main>report</main></body></html>"
    repaired, before_items, after_items = repair_html_text(source)
    assert repaired == source
    assert before_items == 0
    assert after_items == 0


def test_repair_degrades_over_limit_evidence_links_to_plain_text() -> None:
    links = "".join(f'<a href="#evidence-ev-{index}">{index}</a>' for index in range(3))
    articles = "".join(
        f'<article class="evidence-reference" id="evidence-ev-{index}"></article>'
        for index in range(3)
    )
    source = (
        f"<html><head><style></style></head><body>{links}"
        f'<details class="evidence-catalog">{articles}</details></body></html>'
    )
    repaired, before_items, after_items = repair_html_text(source, limit=2)
    assert before_items == 3
    assert after_items == 2
    assert repaired.count('href="#evidence-') == 2
    assert ">2</a>" not in repaired
    assert "2" in repaired
