from __future__ import annotations

import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sec_html_document import build_full_document_artifacts, clean_text  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sec_ixbrl_hidden_header.htm"
FILING_ID = "US:0000000001:0000000001-26-000001"


def _build(raw_html: str, facts: list[dict] | None = None):
    return build_full_document_artifacts(
        package_dir=Path("unused-package"),
        manifest={
            "market": "US",
            "filing_id": FILING_ID,
            "report_id": "2025-10-K-0000000001-26-000001",
            "ticker": "ACME",
            "company_name": "ACME Corp",
            "form": "10-K",
            "fiscal_year": 2025,
            "source_url": "https://www.sec.gov/Archives/example.htm",
        },
        raw_html=raw_html,
        sections_payload={"sections": []},
        table_index_payload={"tables": []},
        facts_payload={"facts": facts or []},
    )


def _block(artifacts, text: str) -> dict:
    return next(block for block in artifacts.document_full["blocks"] if text in block["text"])


def _facts() -> list[dict]:
    return [
        {
            "fact_id": "fact-hidden-header",
            "concept": "dei:DocumentType",
            "value_text": "SECRET_HIDDEN_HEADER",
            "context_ref": "duration",
            "html_anchor": "hidden-doc-type",
        },
        {
            "fact_id": "fact-hidden-assets",
            "concept": "us-gaap:Assets",
            "value_text": "123456",
            "context_ref": "instant",
            "unit_ref": "usd",
            "html_anchor": "hidden-assets",
        },
        {
            "fact_id": "fact-visible-name",
            "concept": "dei:EntityRegistrantName",
            "value_text": "ACME CORP",
            "context_ref": "duration",
            "html_anchor": "visible-name",
        },
        {
            "fact_id": "fact-hidden-inline",
            "concept": "us-gaap:Assets",
            "value_text": "SECRET_INLINE",
            "context_ref": "instant",
            "unit_ref": "usd",
            "html_anchor": "hidden-inline",
        },
    ]


@pytest.mark.parametrize(
    "hidden_markup",
    [
        '<p style="display : NONE !important">SECRET<span>NESTED</span></p>',
        '<p style="visibility:hidden !important">SECRET<span>NESTED</span></p>',
        '<p hidden>SECRET<span>NESTED</span></p>',
        '<p aria-hidden="TRUE">SECRET<span>NESTED</span></p>',
        '<ix:header><p>SECRET<span>NESTED</span></p></ix:header>',
        '<ix:hidden><p>SECRET<span>NESTED</span></p></ix:hidden>',
        '<table style="display:none"></table>',
    ],
)
def test_hidden_ancestor_candidates_are_filtered_but_consume_source_order(hidden_markup):
    raw_html = (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>'
        f'{hidden_markup}<p id="visible">VISIBLE</p></body></html>'
    )
    artifacts = _build(raw_html)

    assert [block["text"] for block in artifacts.document_full["blocks"]] == ["VISIBLE"]
    assert artifacts.document_full["blocks"][0]["source_order"] == 2


def test_hidden_candidate_does_not_shift_later_visible_block_id():
    template = (
        '<html><body><div style="{style}"><p id="candidate">SECRET</p></div>'
        '<p id="visible">VISIBLE</p></body></html>'
    )
    visible = _build(template.format(style="display:block"))
    hidden = _build(template.format(style="display:none"))

    assert _block(visible, "VISIBLE")["source_order"] == 2
    assert _block(hidden, "VISIBLE")["source_order"] == 2
    assert _block(hidden, "VISIBLE")["block_id"] == _block(visible, "VISIBLE")["block_id"]
    assert all("SECRET" not in block["text"] for block in hidden.document_full["blocks"])


def test_sec_hidden_header_preserves_dom_facts_coordinates_and_visible_ids():
    raw_html = FIXTURE.read_text(encoding="utf-8")
    artifacts = _build(raw_html, _facts())
    document = artifacts.document_full
    blocks_text = " ".join(block["text"] for block in document["blocks"])

    assert "SECRET_HIDDEN_HEADER" not in blocks_text
    assert "SECRET_INLINE" not in blocks_text
    assert "SECRET_HIDDEN_HEADER" not in artifacts.report_complete_md
    assert {fact["fact_id"] for fact in document["facts"]} == {fact["fact_id"] for fact in _facts()}
    assert any("SECRET_HIDDEN_HEADER" in (node.get("text_preview") or "") for node in document["dom_nodes"])

    facts = {fact["fact_id"]: fact for fact in document["facts"]}
    assert facts["fact-hidden-header"]["block_id"] is None
    assert facts["fact-hidden-assets"]["block_id"] is None
    assert facts["fact-hidden-inline"]["block_id"] is None
    assert facts["fact-visible-name"]["block_id"] == _block(artifacts, "Visible narrative")["block_id"]
    hidden_fact_ids = {"fact-hidden-header", "fact-hidden-assets", "fact-hidden-inline"}
    assert not any(
        relation["relation_type"] == "block_contains_fact" and relation["target_id"] in hidden_fact_ids
        for relation in document["relations"]
    )

    heading = _block(artifacts, "Item 1. Business")
    mixed = _block(artifacts, "Visible before")
    assert heading["source_order"] == 2
    assert mixed["source_order"] == 4
    assert mixed["text"] == "Visible before visible after."

    soup = BeautifulSoup(raw_html, "lxml")
    body_text = clean_text(soup.body.get_text(" ", strip=True))
    source_text = clean_text(soup.find(id="mixed-copy").get_text(" ", strip=True))
    assert mixed["char_start"] == body_text.find(source_text)
    assert mixed["char_end"] == mixed["char_start"] + len(source_text)

    unhidden = _build(raw_html.replace('aria-hidden="true"', 'aria-hidden="false"'), _facts())
    assert _block(unhidden, "Visible before")["block_id"] == mixed["block_id"]
    assert "SECRET_INLINE" in _block(unhidden, "Visible before")["text"]
