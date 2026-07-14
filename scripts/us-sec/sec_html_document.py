from __future__ import annotations

import hashlib
import html
import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from bs4.element import NavigableString, Tag

SCHEMA_VERSION = "sec_html_document_full_v1"
CONTENT_SCHEMA_VERSION = "sec_html_content_list_enhanced_v1"
TABLE_RELATION_SCHEMA_VERSION = "sec_html_table_relations_v1"
REPORT_COMPLETE_PATH = "report_complete.md"
WIKI_REPORT_COMPLETE_PATH = "sections/report_complete.md"

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"}
INLINE_BLOCK_TAGS = {"div", "span"}
SKIP_TAGS = {"script", "style", "noscript", "ix:header", "header"}
HIDDEN_READING_TAGS = SKIP_TAGS | {"ix:hidden"}
HTML_SNIPPET_LIMIT = 2000
TEXT_PREVIEW_LIMIT = 1200
QUOTE_LIMIT = 700


@dataclass
class FullDocumentArtifacts:
    document_full: dict[str, Any]
    content_list_enhanced: dict[str, Any]
    table_relations: dict[str, Any]
    report_complete_md: str
    source_map_entries: list[dict[str, Any]]
    quality: dict[str, Any]
    warnings: list[str]


def build_full_document_artifacts(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    raw_html: str,
    sections_payload: dict[str, Any],
    table_index_payload: dict[str, Any],
    facts_payload: dict[str, Any],
    contexts_payload: dict[str, Any] | None = None,
    units_payload: dict[str, Any] | None = None,
    normalized_metrics_payload: dict[str, Any] | None = None,
) -> FullDocumentArtifacts:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(raw_html, "lxml")
    root = soup.find("body") or soup.find("html") or soup
    raw_sha256 = sha256_text(raw_html)
    filing_id = str(manifest.get("filing_id") or manifest.get("report_id") or package_dir.name)
    dom_nodes, tag_id_by_object, tag_by_dom_id = _dom_nodes(soup, filing_id)
    source_order_by_dom_id = {str(node["dom_node_id"]): node["source_order"] for node in dom_nodes}
    sections = _sections(sections_payload)
    section_ranges = _section_ranges(sections)
    tables = _tables(root, filing_id, table_index_payload, tag_id_by_object, source_order_by_dom_id, sections)
    table_by_dom_id = {table["dom_node_id"]: table for table in tables if table.get("dom_node_id")}
    facts = _facts(filing_id, facts_payload, tag_id_by_object, tag_by_dom_id, table_by_dom_id)
    _normalize_table_fact_ids(tables, facts)
    body_text = clean_text(root.get_text(" ", strip=True))
    blocks = _blocks(root, filing_id, tag_id_by_object, tables, facts, body_text, section_ranges)
    block_by_dom_id = {block["dom_node_id"]: block for block in blocks if block.get("dom_node_id")}
    _attach_fact_blocks(facts, block_by_dom_id, tag_by_dom_id, tag_id_by_object)
    _attach_table_blocks(tables, block_by_dom_id)
    relations = _relations(
        manifest=manifest,
        tables=tables,
        facts=facts,
        contexts_payload=contexts_payload or {},
        units_payload=units_payload or {},
        normalized_metrics_payload=normalized_metrics_payload or {},
    )
    report_complete_md, md_line_by_block = _markdown(manifest, blocks, content_list_enhanced=None, table_relations=None)
    for block in blocks:
        line_info = md_line_by_block.get(block["block_id"])
        if line_info:
            block["md_line_start"] = line_info["start"]
            block["md_line_end"] = line_info["end"]
    block_source_map = _block_source_map(manifest, blocks)
    quality_warnings = _quality_warnings(
        table_index_payload=table_index_payload,
        facts_payload=facts_payload,
        tables=tables,
        facts=facts,
        blocks=blocks,
        report_complete_md=report_complete_md,
    )
    quality = {
        "schema_version": "sec_html_document_quality_v1",
        "raw_sha256": raw_sha256,
        "raw_size_bytes": len(raw_html.encode("utf-8")),
        "dom_node_count": len(dom_nodes),
        "block_count": len(blocks),
        "markdown_chars": len(report_complete_md),
        "table_count": len(tables),
        "fact_count": len(facts),
        "table_relation_count": len(relations),
        "block_source_map_count": len(block_source_map),
        "fact_linkage_ratio": _ratio(sum(1 for fact in facts if fact.get("dom_node_id")), len(facts)),
        "table_linkage_ratio": _ratio(sum(1 for table in tables if table.get("dom_node_id")), len(tables)),
        "warnings": quality_warnings,
    }
    content_list_enhanced = _content_list_enhanced(
        manifest=manifest,
        blocks=blocks,
        sections=sections,
        tables=tables,
        facts=facts,
        quality=quality,
    )
    table_relations = {
        "schema_version": TABLE_RELATION_SCHEMA_VERSION,
        "filing_id": manifest.get("filing_id"),
        "table_count": len(tables),
        "fact_count": len(facts),
        "relation_count": len(relations),
        "relations": relations,
        "tables": [
            {
                "table_id": table.get("table_id"),
                "table_index": table.get("table_index"),
                "section_id": table.get("section_id"),
                "block_id": table.get("block_id"),
                "dom_node_id": table.get("dom_node_id"),
                "html_anchor": table.get("html_anchor"),
                "heading": table.get("heading"),
                "row_count": table.get("row_count"),
                "column_count": table.get("column_count"),
                "fact_count": len(table.get("fact_ids") or []),
            }
            for table in tables
        ],
    }
    report_complete_md, md_line_by_block = _markdown(
        manifest,
        blocks,
        content_list_enhanced=content_list_enhanced,
        table_relations=table_relations,
    )
    for block in blocks:
        line_info = md_line_by_block.get(block["block_id"])
        if line_info:
            block["md_line_start"] = line_info["start"]
            block["md_line_end"] = line_info["end"]
    block_source_map = _block_source_map(manifest, blocks)
    quality["markdown_chars"] = len(report_complete_md)
    quality["block_source_map_count"] = len(block_source_map)
    document_full = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "raw_path": "raw/filing.htm",
            "source_url": manifest.get("source_url"),
            "raw_sha256": raw_sha256,
            "raw_size_bytes": len(raw_html.encode("utf-8")),
            "parser": "sec_html_document_v1",
        },
        "filing": {
            "market": manifest.get("market"),
            "filing_id": manifest.get("filing_id"),
            "report_id": manifest.get("report_id"),
            "ticker": manifest.get("ticker"),
            "company_name": manifest.get("company_name"),
            "form": manifest.get("form"),
            "fiscal_year": manifest.get("fiscal_year"),
            "period_end": manifest.get("period_end"),
            "accession_number": manifest.get("accession_number"),
        },
        "dom_nodes": dom_nodes,
        "sections": sections,
        "blocks": blocks,
        "tables": tables,
        "facts": facts,
        "relations": relations,
        "markdown": {
            "path": REPORT_COMPLETE_PATH,
            "wiki_path": WIKI_REPORT_COMPLETE_PATH,
            "content_sha256": sha256_text(report_complete_md),
            "char_count": len(report_complete_md),
            "block_count": len(blocks),
        },
        "quality": quality,
    }
    return FullDocumentArtifacts(
        document_full=document_full,
        content_list_enhanced=content_list_enhanced,
        table_relations=table_relations,
        report_complete_md=report_complete_md,
        source_map_entries=block_source_map,
        quality=quality,
        warnings=quality_warnings,
    )


def clean_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


def _dom_nodes(soup: BeautifulSoup, filing_id: str) -> tuple[list[dict[str, Any]], dict[int, str], dict[str, Tag]]:
    tags = [tag for tag in soup.find_all(True) if isinstance(tag, Tag)]
    tag_id_by_object: dict[int, str] = {}
    xpath_by_object: dict[int, str] = {}
    for order, tag in enumerate(tags, start=1):
        xpath = _xpath(tag)
        dom_node_id = stable_id(filing_id, "dom", xpath, order, _tag_name(tag))
        tag_id_by_object[id(tag)] = dom_node_id
        xpath_by_object[id(tag)] = xpath
    nodes: list[dict[str, Any]] = []
    tag_by_dom_id: dict[str, Tag] = {}
    for order, tag in enumerate(tags, start=1):
        dom_node_id = tag_id_by_object[id(tag)]
        parent = tag.parent if isinstance(tag.parent, Tag) else None
        parent_id = tag_id_by_object.get(id(parent)) if parent else None
        children = [tag_id_by_object[id(child)] for child in tag.find_all(True, recursive=False) if id(child) in tag_id_by_object]
        node_html = str(tag)
        node_text = clean_text(tag.get_text(" ", strip=True))
        attrs = _attrs(tag)
        nodes.append(
            {
                "dom_node_id": dom_node_id,
                "source_order": order,
                "tag": _tag_name(tag),
                "attrs": attrs,
                "parent_id": parent_id,
                "child_ids": children,
                "depth": len(list(tag.parents)) - 1,
                "xpath": xpath_by_object[id(tag)],
                "dom_path": xpath_by_object[id(tag)],
                "html_anchor": attrs.get("id") or attrs.get("name"),
                "text_preview": node_text[:TEXT_PREVIEW_LIMIT],
                "text_hash": sha256_text(node_text) if node_text else None,
                "html_hash": sha256_text(node_html),
                "html_snippet": node_html[:HTML_SNIPPET_LIMIT],
            }
        )
        tag_by_dom_id[dom_node_id] = tag
    return nodes, tag_id_by_object, tag_by_dom_id


def _attrs(tag: Tag) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key, value in tag.attrs.items():
        key_text = str(key)
        if isinstance(value, list):
            attrs[key_text] = [str(item)[:500] for item in value]
        else:
            attrs[key_text] = str(value)[:2000]
    return attrs


def _xpath(tag: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = tag
    while current is not None and isinstance(current, Tag) and current.name not in {"[document]"}:
        name = _tag_name(current)
        same_before = 0
        sibling = current.previous_sibling
        while sibling is not None:
            if isinstance(sibling, Tag) and _tag_name(sibling) == name:
                same_before += 1
            sibling = sibling.previous_sibling
        parts.append(f"{name}[{same_before + 1}]")
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return "/" + "/".join(reversed(parts))


def _tag_name(tag: Tag) -> str:
    return str(tag.name or "").lower()


def _sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = payload.get("sections") if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for index, section in enumerate(sections if isinstance(sections, list) else [], start=1):
        if not isinstance(section, dict):
            continue
        result.append(
            {
                "section_id": section.get("section_id"),
                "section_title": section.get("section_title"),
                "section_order": section.get("section_order") or index,
                "file": section.get("file"),
                "html_anchor": section.get("html_anchor"),
                "char_start": section.get("char_start"),
                "char_end": section.get("char_end"),
                "text_hash": section.get("text_hash"),
                "text_length": section.get("text_length"),
                "raw": section,
            }
        )
    return result


def _section_ranges(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges = []
    for section in sections:
        try:
            start = int(section.get("char_start"))
            end = int(section.get("char_end"))
        except (TypeError, ValueError):
            continue
        ranges.append({"section_id": section.get("section_id"), "start": start, "end": end})
    return sorted(ranges, key=lambda item: item["start"])


def _section_for_char(char_start: int | None, ranges: list[dict[str, Any]]) -> str | None:
    if char_start is None:
        return None
    for item in ranges:
        if item["start"] <= char_start < item["end"]:
            return item.get("section_id")
    return ranges[-1].get("section_id") if ranges and char_start >= ranges[-1]["start"] else None


def _tables(
    root: BeautifulSoup | Tag,
    filing_id: str,
    table_index_payload: dict[str, Any],
    tag_id_by_object: dict[int, str],
    source_order_by_dom_id: dict[str, int],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed = table_index_payload.get("tables") if isinstance(table_index_payload, dict) else []
    indexed = indexed if isinstance(indexed, list) else []
    section_fallback = sections[-1].get("section_id") if sections else None
    tables: list[dict[str, Any]] = []
    for order, table_tag in enumerate(root.find_all("table"), start=1):
        indexed_item = indexed[order - 1] if order - 1 < len(indexed) and isinstance(indexed[order - 1], dict) else {}
        table_id = indexed_item.get("table_id") or stable_id(filing_id, "table", order, str(table_tag)[:500])
        rows = []
        fact_ids: list[str] = []
        for row_index, tr in enumerate(table_tag.find_all("tr"), start=1):
            cells = []
            for col_index, cell in enumerate(tr.find_all(["th", "td"], recursive=False), start=1):
                text = clean_text(cell.get_text(" ", strip=True))
                cell_fact_anchors = _fact_anchors(cell)
                fact_ids.extend(cell_fact_anchors)
                cells.append(
                    {
                        "cell_id": stable_id(table_id, "cell", row_index, col_index, text, cell.get("colspan"), cell.get("rowspan")),
                        "row_index": row_index,
                        "column_index": col_index,
                        "tag": _tag_name(cell),
                        "text": text,
                        "text_hash": sha256_text(text) if text else None,
                        "colspan": _int_or_none(cell.get("colspan")) or 1,
                        "rowspan": _int_or_none(cell.get("rowspan")) or 1,
                        "fact_anchors": cell_fact_anchors,
                        "html_hash": sha256_text(str(cell)),
                        "html_snippet": str(cell)[:HTML_SNIPPET_LIMIT],
                    }
                )
            if cells:
                rows.append({"row_index": row_index, "cells": cells})
        heading = indexed_item.get("title") or _nearest_heading(table_tag)
        tables.append(
            {
                "table_id": table_id,
                "table_index": indexed_item.get("table_index") or order,
                "source_order": _source_order(tag_id_by_object, source_order_by_dom_id, table_tag),
                "dom_node_id": tag_id_by_object.get(id(table_tag)),
                "xpath": _xpath(table_tag),
                "html_anchor": table_tag.get("id") or indexed_item.get("html_anchor") or f"table_{order:04d}",
                "section_id": indexed_item.get("section_id") or section_fallback,
                "heading": heading,
                "row_count": len(rows),
                "column_count": max((len(row["cells"]) for row in rows), default=0),
                "fact_ids": sorted(set(fact_ids)),
                "rows": rows,
                "html_hash": sha256_text(str(table_tag)),
                "html_snippet": str(table_tag)[:HTML_SNIPPET_LIMIT],
                "raw_index": indexed_item,
            }
        )
    return tables


def _fact_anchors(tag: Tag) -> list[str]:
    anchors = []
    for fact_tag in tag.find_all(_is_ixbrl_fact):
        fact_id = fact_tag.get("id")
        if fact_id:
            anchors.append(str(fact_id))
    return anchors


def _is_ixbrl_fact(tag: Tag) -> bool:
    name = _tag_name(tag).split(":")[-1]
    return name in {"nonfraction", "nonnumeric"}


def _nearest_heading(tag: Tag) -> str | None:
    node = tag
    for _ in range(8):
        node = node.find_previous(["h1", "h2", "h3", "h4", "h5", "h6", "div", "p", "span"])
        if not node:
            break
        text = clean_text(node.get_text(" ", strip=True))
        if 4 <= len(text) <= 220:
            return text
    return None


def _source_order(tag_id_by_object: dict[int, str], source_order_by_dom_id: dict[str, int], tag: Tag) -> int | None:
    dom_id = tag_id_by_object.get(id(tag))
    if not dom_id:
        return None
    return source_order_by_dom_id.get(dom_id)


def _normalize_table_fact_ids(tables: list[dict[str, Any]], facts: list[dict[str, Any]]) -> None:
    fact_id_by_anchor = {
        str(fact.get("html_anchor")): fact.get("fact_id")
        for fact in facts
        if fact.get("html_anchor") and fact.get("fact_id")
    }
    for table in tables:
        normalized: list[str] = []
        for row in table.get("rows") or []:
            for cell in row.get("cells") or []:
                cell_fact_ids = [
                    fact_id_by_anchor[anchor]
                    for anchor in cell.get("fact_anchors") or []
                    if anchor in fact_id_by_anchor
                ]
                cell["fact_ids"] = cell_fact_ids
                normalized.extend(cell_fact_ids)
        table["fact_ids"] = sorted(set(normalized))


def _facts(
    filing_id: str,
    facts_payload: dict[str, Any],
    tag_id_by_object: dict[int, str],
    tag_by_dom_id: dict[str, Tag],
    table_by_dom_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_facts = facts_payload.get("facts") if isinstance(facts_payload, dict) else []
    raw_facts = raw_facts if isinstance(raw_facts, list) else []
    anchor_to_dom_id = {
        str(tag.get("id")): dom_id
        for dom_id, tag in tag_by_dom_id.items()
        if isinstance(tag, Tag) and tag.get("id")
    }
    facts: list[dict[str, Any]] = []
    for index, fact in enumerate(raw_facts, start=1):
        if not isinstance(fact, dict):
            continue
        anchor = fact.get("html_anchor") or (fact.get("raw") or {}).get("id")
        dom_node_id = anchor_to_dom_id.get(str(anchor)) if anchor else None
        tag = tag_by_dom_id.get(dom_node_id or "")
        table = _ancestor_table(tag, tag_id_by_object, table_by_dom_id) if tag else None
        facts.append(
            {
                "fact_id": fact.get("fact_id") or stable_id(filing_id, "fact", index, fact.get("concept"), fact.get("context_ref")),
                "source_order": index,
                "concept": fact.get("concept"),
                "label": fact.get("label"),
                "value_text": fact.get("value_text"),
                "value_numeric": fact.get("value_numeric"),
                "context_ref": fact.get("context_ref"),
                "unit_ref": fact.get("unit_ref"),
                "unit": fact.get("unit"),
                "period_start": fact.get("period_start"),
                "period_end": fact.get("period_end"),
                "dimensions": fact.get("dimensions") or {},
                "html_anchor": anchor,
                "dom_node_id": dom_node_id,
                "xpath": _xpath(tag) if tag else fact.get("xpath"),
                "table_id": table.get("table_id") if table else None,
                "table_index": table.get("table_index") if table else None,
                "block_id": None,
                "raw": fact,
            }
        )
    return facts


def _ancestor_table(tag: Tag | None, tag_id_by_object: dict[int, str], table_by_dom_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    current = tag
    while current is not None and isinstance(current, Tag):
        if _tag_name(current) == "table":
            dom_id = tag_id_by_object.get(id(current))
            return table_by_dom_id.get(dom_id or "")
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return None


def _blocks(
    root: BeautifulSoup | Tag,
    filing_id: str,
    tag_id_by_object: dict[int, str],
    tables: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    body_text: str,
    section_ranges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    table_by_dom_id = {table["dom_node_id"]: table for table in tables if table.get("dom_node_id")}
    fact_anchors_by_dom_id = {fact.get("dom_node_id"): fact.get("fact_id") for fact in facts if fact.get("dom_node_id")}
    covered: set[int] = set()
    blocks: list[dict[str, Any]] = []
    candidate_source_order = 0
    search_cursor = 0

    for node in root.descendants:
        if isinstance(node, NavigableString):
            parent = node.parent if isinstance(node.parent, Tag) else None
            if parent is None or _has_covered_ancestor(parent, covered) or _inside_table(parent):
                continue
            if _tag_name(parent) not in {"body", "html"}:
                continue
            text = clean_text(str(node))
            if not text:
                continue
            candidate_source_order += 1
            if _is_hidden_for_reading(parent):
                continue
            block, search_cursor = _text_block(
                filing_id=filing_id,
                source_order=candidate_source_order,
                block_type="paragraph",
                text=text,
                tag=None,
                tag_id_by_object=tag_id_by_object,
                body_text=body_text,
                search_cursor=search_cursor,
                section_ranges=section_ranges,
                fact_ids=[],
            )
            blocks.append(block)
            continue

        if not isinstance(node, Tag) or _tag_name(node) in SKIP_TAGS:
            continue
        if _has_covered_ancestor(node, covered):
            continue
        tag_name = _tag_name(node)
        if tag_name == "table":
            table = table_by_dom_id.get(tag_id_by_object.get(id(node), ""))
            text = _table_markdown(table) if table else clean_text(node.get_text(" ", strip=True))
            candidate_source_order += 1
            if _is_hidden_for_reading(node):
                covered.add(id(node))
                continue
            visible_fact_ids = [
                fact_anchors_by_dom_id.get(tag_id_by_object.get(id(fact_tag)))
                for fact_tag in node.find_all(_is_ixbrl_fact)
                if not _is_hidden_for_reading(fact_tag)
            ]
            block, search_cursor = _text_block(
                filing_id=filing_id,
                source_order=candidate_source_order,
                block_type="table",
                text=text,
                tag=node,
                tag_id_by_object=tag_id_by_object,
                body_text=body_text,
                search_cursor=search_cursor,
                section_ranges=section_ranges,
                fact_ids=[item for item in visible_fact_ids if item],
                table=table,
            )
            blocks.append(block)
            covered.add(id(node))
            continue
        if not _is_reading_block(node):
            continue
        source_text = clean_text(node.get_text(" ", strip=True))
        if not source_text:
            continue
        candidate_source_order += 1
        text = _reading_text(node)
        if _is_hidden_for_reading(node) or not text:
            covered.add(id(node))
            continue
        fact_ids = [
            fact_anchors_by_dom_id.get(tag_id_by_object.get(id(fact_tag)))
            for fact_tag in node.find_all(_is_ixbrl_fact)
            if not _is_hidden_for_reading(fact_tag)
        ]
        block, search_cursor = _text_block(
            filing_id=filing_id,
            source_order=candidate_source_order,
            block_type=_block_type(node),
            text=text,
            tag=node,
            tag_id_by_object=tag_id_by_object,
            body_text=body_text,
            search_cursor=search_cursor,
            section_ranges=section_ranges,
            fact_ids=[item for item in fact_ids if item],
            source_text=source_text,
        )
        blocks.append(block)
        covered.add(id(node))
    return blocks


def _is_reading_block(tag: Tag) -> bool:
    name = _tag_name(tag)
    if name in BLOCK_TAGS - {"table"}:
        return True
    if name not in INLINE_BLOCK_TAGS:
        return False
    if _inside_table(tag):
        return False
    child_block = tag.find(lambda item: isinstance(item, Tag) and _tag_name(item) in BLOCK_TAGS.union(INLINE_BLOCK_TAGS))
    return child_block is None


def _has_covered_ancestor(tag: Tag, covered: set[int]) -> bool:
    current = tag.parent
    while current is not None and isinstance(current, Tag):
        if id(current) in covered:
            return True
        current = current.parent
    return False


def _reading_text(tag: Tag) -> str:
    parts: list[str] = []
    for node in tag.descendants:
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent if isinstance(node.parent, Tag) else None
        if parent is None or _is_hidden_for_reading(parent):
            continue
        parts.append(str(node))
    return clean_text(" ".join(parts))


def _is_hidden_for_reading(tag: Tag) -> bool:
    current: Tag | None = tag
    while current is not None and isinstance(current, Tag):
        if _tag_name(current) in HIDDEN_READING_TAGS:
            return True
        if current.has_attr("hidden"):
            return True
        aria_hidden = current.get("aria-hidden")
        if aria_hidden is not None and str(aria_hidden).strip().lower() in {"true", "1"}:
            return True
        if _style_hides_content(current.get("style")):
            return True
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return False


def _style_hides_content(style: Any) -> bool:
    if style is None:
        return False
    for declaration in str(style).split(";"):
        property_name, separator, value = declaration.partition(":")
        if not separator:
            continue
        property_name = property_name.strip().lower()
        value = re.sub(r"\s*!important\s*$", "", value, flags=re.IGNORECASE).strip().lower()
        if property_name == "display" and value == "none":
            return True
        if property_name == "visibility" and value == "hidden":
            return True
    return False


def _inside_table(tag: Tag) -> bool:
    current = tag
    while current is not None and isinstance(current, Tag):
        if _tag_name(current) == "table":
            return True
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return False


def _block_type(tag: Tag) -> str:
    name = _tag_name(tag)
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "heading"
    if name == "li":
        return "list_item"
    return "paragraph"


def _text_block(
    *,
    filing_id: str,
    source_order: int,
    block_type: str,
    text: str,
    tag: Tag | None,
    tag_id_by_object: dict[int, str],
    body_text: str,
    search_cursor: int,
    section_ranges: list[dict[str, Any]],
    fact_ids: list[str],
    table: dict[str, Any] | None = None,
    source_text: str | None = None,
) -> tuple[dict[str, Any], int]:
    identity_text = source_text if source_text is not None else text
    char_start = body_text.find(identity_text[:120], search_cursor) if identity_text else -1
    if char_start < 0:
        char_start = body_text.find(identity_text[:120]) if identity_text else -1
    char_end = char_start + len(identity_text) if char_start >= 0 else None
    next_cursor = char_end if isinstance(char_end, int) else search_cursor
    dom_node_id = tag_id_by_object.get(id(tag)) if tag is not None else None
    block_id = stable_id(filing_id, "block", source_order, block_type, dom_node_id, identity_text[:300])
    heading_level = None
    if tag is not None and _tag_name(tag).startswith("h") and _tag_name(tag)[1:].isdigit():
        heading_level = int(_tag_name(tag)[1:])
    block = {
        "block_id": block_id,
        "source_order": source_order,
        "block_type": block_type,
        "dom_node_id": dom_node_id,
        "xpath": _xpath(tag) if tag else None,
        "html_anchor": tag.get("id") if tag else None,
        "section_id": (table or {}).get("section_id") or _section_for_char(char_start if char_start >= 0 else None, section_ranges),
        "heading_level": heading_level,
        "text": text,
        "text_hash": sha256_text(text),
        "char_start": char_start if char_start >= 0 else None,
        "char_end": char_end,
        "fact_ids": sorted(set(fact_ids)),
        "table_id": (table or {}).get("table_id"),
        "table_index": (table or {}).get("table_index"),
        "html_hash": sha256_text(str(tag)) if tag is not None else None,
    }
    return block, next_cursor


def _attach_fact_blocks(
    facts: list[dict[str, Any]],
    block_by_dom_id: dict[str, dict[str, Any]],
    tag_by_dom_id: dict[str, Tag],
    tag_id_by_object: dict[int, str],
) -> None:
    for fact in facts:
        dom_node_id = fact.get("dom_node_id")
        tag = tag_by_dom_id.get(str(dom_node_id or ""))
        if tag is not None and _is_hidden_for_reading(tag):
            continue
        current = tag
        while current is not None and isinstance(current, Tag):
            block = block_by_dom_id.get(str(tag_id_by_object.get(id(current)) or ""))
            if block:
                fact["block_id"] = block.get("block_id")
                if fact.get("fact_id") not in block["fact_ids"]:
                    block["fact_ids"].append(fact.get("fact_id"))
                    block["fact_ids"] = sorted(set(block["fact_ids"]))
                break
            current = current.parent if isinstance(current.parent, Tag) else None


def _attach_table_blocks(tables: list[dict[str, Any]], block_by_dom_id: dict[str, dict[str, Any]]) -> None:
    for table in tables:
        block = block_by_dom_id.get(str(table.get("dom_node_id") or ""))
        if block:
            table["block_id"] = block.get("block_id")


def _relations(
    *,
    manifest: dict[str, Any],
    tables: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    contexts_payload: dict[str, Any],
    units_payload: dict[str, Any],
    normalized_metrics_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    filing_id = str(manifest.get("filing_id") or "")
    for table in tables:
        if table.get("section_id"):
            relations.append(_relation(filing_id, "table_in_section", table.get("table_id"), table.get("section_id")))
        if table.get("heading"):
            relations.append(_relation(filing_id, "table_has_heading", table.get("table_id"), table.get("heading")))
        for row in table.get("rows") or []:
            for cell in row.get("cells") or []:
                relations.append(_relation(filing_id, "table_contains_cell", table.get("table_id"), cell.get("cell_id")))
                for anchor in cell.get("fact_anchors") or []:
                    fact = next((item for item in facts if str(item.get("html_anchor")) == str(anchor)), None)
                    if fact:
                        relations.append(_relation(filing_id, "table_cell_contains_fact", cell.get("cell_id"), fact.get("fact_id")))
    contexts = contexts_payload.get("contexts") if isinstance(contexts_payload, dict) else {}
    units = units_payload.get("units") if isinstance(units_payload, dict) else {}
    for fact in facts:
        if fact.get("context_ref"):
            relations.append(
                _relation(
                    filing_id,
                    "fact_has_context",
                    fact.get("fact_id"),
                    fact.get("context_ref"),
                    {"context": (contexts or {}).get(str(fact.get("context_ref")))},
                )
            )
        if fact.get("unit_ref"):
            relations.append(
                _relation(
                    filing_id,
                    "fact_has_unit",
                    fact.get("fact_id"),
                    fact.get("unit_ref"),
                    {"unit": (units or {}).get(str(fact.get("unit_ref")))},
                )
            )
        if fact.get("table_id"):
            relations.append(_relation(filing_id, "fact_in_table", fact.get("fact_id"), fact.get("table_id")))
        if fact.get("block_id"):
            relations.append(_relation(filing_id, "block_contains_fact", fact.get("block_id"), fact.get("fact_id")))
    for metric in normalized_metrics_payload.get("metrics") or []:
        if not isinstance(metric, dict) or not metric.get("raw_fact_id"):
            continue
        relations.append(
            _relation(
                filing_id,
                "metric_uses_fact",
                metric.get("metric_id") or metric.get("canonical_name"),
                metric.get("raw_fact_id"),
                {"canonical_name": metric.get("canonical_name"), "period_key": metric.get("period_key")},
            )
        )
    return relations


def _relation(filing_id: str, relation_type: str, source_id: Any, target_id: Any, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "relation_id": stable_id(filing_id, relation_type, source_id, target_id),
        "relation_type": relation_type,
        "source_id": source_id,
        "target_id": target_id,
        "raw": raw or {},
    }


def _markdown(
    manifest: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    content_list_enhanced: dict[str, Any] | None = None,
    table_relations: dict[str, Any] | None = None,
) -> tuple[str, dict[str, dict[str, int]]]:
    lines = [
        "---",
        "schema_version: sec_report_complete_markdown_v1",
        f"market: {manifest.get('market') or 'US'}",
        f"ticker: {manifest.get('ticker') or ''}",
        f"filing_id: {manifest.get('filing_id') or ''}",
        f"form: {manifest.get('form') or ''}",
        f"source_url: {manifest.get('source_url') or ''}",
        "---",
        "",
        f"# {manifest.get('ticker') or ''} {manifest.get('fiscal_year') or ''} {manifest.get('form') or ''}".strip(),
        "",
    ]
    line_by_block: dict[str, dict[str, int]] = {}
    for block in blocks:
        start_line = len(lines) + 1
        lines.append(_block_comment(block))
        if block["block_type"] == "heading":
            level = max(1, min(int(block.get("heading_level") or 2), 6))
            lines.append(f"{'#' * level} {block['text']}")
        elif block["block_type"] == "list_item":
            lines.append(f"- {block['text']}")
        elif block["block_type"] == "table":
            if block.get("table_id"):
                lines.append(f"<!-- siq:table_id={block.get('table_id')} table_index={block.get('table_index') or ''} -->")
            lines.extend(block["text"].splitlines() or [""])
        else:
            lines.append(block["text"])
        lines.append("")
        end_line = len(lines)
        line_by_block[block["block_id"]] = {"start": start_line, "end": end_line}
    if content_list_enhanced or table_relations:
        lines.extend(_enhanced_summary_markdown(content_list_enhanced or {}, table_relations or {}))
    return "\n".join(lines).rstrip() + "\n", line_by_block


def _enhanced_summary_markdown(content_list_enhanced: dict[str, Any], table_relations: dict[str, Any]) -> list[str]:
    relation_type_counts: dict[str, int] = {}
    for relation in table_relations.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        relation_type = str(relation.get("relation_type") or "unknown")
        relation_type_counts[relation_type] = relation_type_counts.get(relation_type, 0) + 1
    payload = {
        "schema_version": "sec_report_complete_enhanced_summary_v1",
        "source_counts": content_list_enhanced.get("source_counts") or {},
        "outline": content_list_enhanced.get("outline") or [],
        "tables": table_relations.get("tables") or [],
        "relation_type_counts": relation_type_counts,
        "quality_warnings": ((content_list_enhanced.get("quality") or {}).get("warnings") if isinstance(content_list_enhanced.get("quality"), dict) else []) or [],
    }
    return [
        "",
        "## SIQ Enhanced Relation Summary",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ]


def _block_comment(block: dict[str, Any]) -> str:
    attrs = {
        "block_id": block.get("block_id"),
        "dom_node_id": block.get("dom_node_id"),
        "section_id": block.get("section_id"),
        "source_order": block.get("source_order"),
    }
    return "<!-- siq:" + " ".join(f"{key}={value}" for key, value in attrs.items() if value is not None) + " -->"


def _table_markdown(table: dict[str, Any] | None) -> str:
    if not table:
        return ""
    rows = []
    for row in table.get("rows") or []:
        rows.append([_escape_md_cell(cell.get("text") or "") for cell in row.get("cells") or []])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    output = []
    if table.get("heading"):
        output.append(f"**{table['heading']}**")
        output.append("")
    output.append("| " + " | ".join(padded[0]) + " |")
    output.append("| " + " | ".join(["---"] * width) + " |")
    for row in padded[1:]:
        output.append("| " + " | ".join(row) + " |")
    return "\n".join(output)


def _escape_md_cell(text: str) -> str:
    return clean_text(text).replace("|", "\\|")


def _block_source_map(manifest: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for block in blocks:
        evidence_id = stable_id(manifest.get("filing_id"), "block", block.get("block_id"))
        target = manifest.get("source_url") or ""
        if block.get("html_anchor"):
            target = f"{target}#{block.get('html_anchor')}"
        entries.append(
            {
                "evidence_id": evidence_id,
                "source_type": "sec_html_block",
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "section_id": block.get("section_id"),
                "dom_node_id": block.get("dom_node_id"),
                "html_anchor": block.get("html_anchor"),
                "xpath": block.get("xpath"),
                "local_path": "parser/report_complete.md",
                "wiki_path": WIKI_REPORT_COMPLETE_PATH,
                "source_url": manifest.get("source_url"),
                "target": target,
                "quote_text": str(block.get("text") or "")[:QUOTE_LIMIT],
                "md_line_start": block.get("md_line_start"),
                "md_line_end": block.get("md_line_end"),
                "raw": {
                    "text_hash": block.get("text_hash"),
                    "char_start": block.get("char_start"),
                    "char_end": block.get("char_end"),
                    "source_order": block.get("source_order"),
                    "table_id": block.get("table_id"),
                    "fact_ids": block.get("fact_ids") or [],
                },
            }
        )
    return entries


def _content_list_enhanced(
    *,
    manifest: dict[str, Any],
    blocks: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CONTENT_SCHEMA_VERSION,
        "filing_id": manifest.get("filing_id"),
        "report_id": manifest.get("report_id"),
        "source_counts": {
            "sections": len(sections),
            "blocks": len(blocks),
            "tables": len(tables),
            "facts": len(facts),
        },
        "outline": [
            {
                "section_id": section.get("section_id"),
                "section_title": section.get("section_title"),
                "section_order": section.get("section_order"),
                "file": section.get("file"),
            }
            for section in sections
        ],
        "blocks": [
            {
                "block_id": block.get("block_id"),
                "block_type": block.get("block_type"),
                "source_order": block.get("source_order"),
                "section_id": block.get("section_id"),
                "dom_node_id": block.get("dom_node_id"),
                "md_line_start": block.get("md_line_start"),
                "md_line_end": block.get("md_line_end"),
                "text_hash": block.get("text_hash"),
                "char_start": block.get("char_start"),
                "char_end": block.get("char_end"),
                "table_id": block.get("table_id"),
                "fact_ids": block.get("fact_ids") or [],
            }
            for block in blocks
        ],
        "tables": [
            {
                "table_id": table.get("table_id"),
                "table_index": table.get("table_index"),
                "section_id": table.get("section_id"),
                "block_id": table.get("block_id"),
                "heading": table.get("heading"),
                "row_count": table.get("row_count"),
                "column_count": table.get("column_count"),
                "fact_ids": table.get("fact_ids") or [],
            }
            for table in tables
        ],
        "facts": [
            {
                "fact_id": fact.get("fact_id"),
                "concept": fact.get("concept"),
                "context_ref": fact.get("context_ref"),
                "unit_ref": fact.get("unit_ref"),
                "block_id": fact.get("block_id"),
                "table_id": fact.get("table_id"),
                "html_anchor": fact.get("html_anchor"),
            }
            for fact in facts
        ],
        "quality": quality,
    }


def _quality_warnings(
    *,
    table_index_payload: dict[str, Any],
    facts_payload: dict[str, Any],
    tables: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    report_complete_md: str,
) -> list[str]:
    warnings = []
    indexed_tables = table_index_payload.get("tables") if isinstance(table_index_payload, dict) else []
    indexed_table_count = len(indexed_tables) if isinstance(indexed_tables, list) else 0
    raw_facts = facts_payload.get("facts") if isinstance(facts_payload, dict) else []
    raw_fact_count = len(raw_facts) if isinstance(raw_facts, list) else 0
    if indexed_table_count != len(tables):
        warnings.append(f"HTML table count {len(tables)} differs from tables/table_index.json count {indexed_table_count}.")
    if raw_fact_count != len(facts):
        warnings.append(f"HTML fact count {len(facts)} differs from xbrl/facts_raw.json count {raw_fact_count}.")
    if facts and not any(fact.get("dom_node_id") for fact in facts):
        warnings.append("No XBRL facts could be linked back to DOM nodes.")
    if tables and not any(table.get("dom_node_id") for table in tables):
        warnings.append("No HTML tables could be linked back to DOM nodes.")
    if not blocks:
        warnings.append("No readable HTML blocks were generated.")
    if len(report_complete_md.strip()) < 100:
        warnings.append("Generated report_complete.md is unexpectedly short.")
    return warnings


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
