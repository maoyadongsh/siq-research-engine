from __future__ import annotations

import sys
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from table_merge import build_table_relations  # noqa: E402


def _table(
    table_id: str,
    page_number: int,
    bbox: list[float],
    markdown: str,
    *,
    title: str = "",
    columns: int = 3,
) -> dict:
    return {
        "table_id": table_id,
        "page_number": page_number,
        "bbox": bbox,
        "title": title,
        "caption": title,
        "markdown": markdown,
        "quality": {"row_count": 3, "column_count": columns},
        "cells": [],
    }


def _block(
    block_id: str,
    page_number: int,
    bbox: list[float],
    text: str,
    *,
    block_type: str = "paragraph",
    sub_type: str = "",
    markdown: str = "",
) -> dict:
    return {
        "block_id": block_id,
        "type": block_type,
        "sub_type": sub_type,
        "page_number": page_number,
        "bbox": bbox,
        "text": text,
        "markdown": markdown or text,
    }


def test_title_before_target_table_blocks_continuation() -> None:
    tables = [
        _table("pt-1", 5, [80, 120, 900, 790], "| A | B | C |\n| --- | --- | --- |\n| old | x | y |"),
        _table("pt-2", 6, [80, 180, 900, 640], "| D | E | F |\n| --- | --- | --- |\n| new | x | y |"),
    ]
    blocks = [
        _block("h1", 6, [200, 100, 700, 125], "第二节 公司简介和主要财务指标", sub_type="1", markdown="# 第二节 公司简介和主要财务指标"),
        _block("h2", 6, [80, 150, 240, 168], "一、公司信息", sub_type="1", markdown="# 一、公司信息"),
        _block("t2", 6, [80, 180, 900, 640], "", block_type="table"),
    ]
    markdown = """
<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>old</td><td>x</td><td>y</td></tr></table>

# 第二节 公司简介和主要财务指标

# 一、公司信息

<table><tr><td>D</td><td>E</td><td>F</td></tr><tr><td>new</td><td>x</td><td>y</td></tr></table>
"""

    relations = build_table_relations("task", tables, blocks=blocks, markdown=markdown)["relations"]

    assert relations == []


def test_header_footer_and_page_number_do_not_block_true_continuation() -> None:
    tables = [
        _table("pt-1", 1, [100, 720, 900, 920], "| A | B | C |\n| --- | --- | --- |\n| row 1 | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 340], "| A | B | C |\n| --- | --- | --- |\n| row 2 | x | y |"),
    ]
    blocks = [
        _block("header", 2, [650, 45, 900, 62], "公司 2025 年年度报告", block_type="title"),
        _block("page", 2, [490, 920, 510, 932], "2"),
        _block("t2", 2, [100, 110, 900, 340], "", block_type="table"),
    ]
    markdown = """
<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>row 1</td><td>x</td><td>y</td></tr><tr><td>row 2</td><td>x</td><td>y</td></tr></table>
"""

    relations = build_table_relations("task", tables, blocks=blocks, markdown=markdown)["relations"]

    assert [(item["from_table_id"], item["to_table_id"]) for item in relations] == [("pt-1", "pt-2")]
    assert relations[0]["relation_type"] == "continuation"
    assert "rendered_markdown_same_table" in relations[0]["reasons"]


def test_rendered_markdown_separate_tables_block_similar_tables() -> None:
    tables = [
        _table("pt-1", 1, [100, 720, 900, 920], "| A | B | C |\n| --- | --- | --- |\n| alpha | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 340], "| A | B | C |\n| --- | --- | --- |\n| beta | x | y |"),
    ]
    blocks = [
        _block("t2", 2, [100, 110, 900, 340], "", block_type="table"),
    ]
    markdown = """
<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>alpha</td><td>x</td><td>y</td></tr></table>

<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>beta</td><td>x</td><td>y</td></tr></table>
"""

    relations = build_table_relations("task", tables, blocks=blocks, markdown=markdown)["relations"]

    assert relations == []


def test_non_first_table_on_target_page_is_not_continuation() -> None:
    tables = [
        _table("pt-1", 1, [100, 720, 900, 920], "| A | B | C |\n| --- | --- | --- |\n| source | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 250], "| unrelated | table |\n| --- | --- |\n| first | x |", columns=2),
        _table("pt-3", 2, [100, 420, 900, 620], "| A | B | C |\n| --- | --- | --- |\n| similar | x | y |"),
    ]
    blocks = [
        _block("t2", 2, [100, 110, 900, 250], "", block_type="table"),
        _block("t3", 2, [100, 420, 900, 620], "", block_type="table"),
    ]

    relations = build_table_relations("task", tables, blocks=blocks, markdown="")["relations"]

    assert relations == []


def test_source_table_must_reach_page_bottom_without_markdown_evidence() -> None:
    tables = [
        _table("pt-1", 1, [100, 220, 900, 520], "| A | B | C |\n| --- | --- | --- |\n| source | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 340], "| A | B | C |\n| --- | --- | --- |\n| target | x | y |"),
    ]
    blocks = [
        _block("t2", 2, [100, 110, 900, 340], "", block_type="table"),
    ]

    relations = build_table_relations("task", tables, blocks=blocks, markdown="")["relations"]

    assert relations == []


def test_body_text_after_source_table_blocks_false_continuation() -> None:
    tables = [
        _table("pt-1", 1, [100, 420, 900, 760], "| A | B | C |\n| --- | --- | --- |\n| source | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 340], "| A | B | C |\n| --- | --- | --- |\n| target | x | y |"),
    ]
    blocks = [
        _block("t1", 1, [100, 420, 900, 760], "", block_type="table"),
        _block("body", 1, [100, 790, 900, 830], "这是上一页表格后的正文内容，不应跨页合并。"),
        _block("page", 1, [490, 930, 510, 942], "1"),
        _block("t2", 2, [100, 110, 900, 340], "", block_type="table"),
    ]

    relations = build_table_relations("task", tables, blocks=blocks, markdown="")["relations"]

    assert relations == []


def test_rendered_same_table_allows_text_after_source_fragment() -> None:
    tables = [
        _table("pt-1", 1, [100, 420, 900, 760], "| A | B | C |\n| --- | --- | --- |\n| source | x | y |"),
        _table("pt-2", 2, [100, 110, 900, 340], "| A | B | C |\n| --- | --- | --- |\n| target | x | y |"),
    ]
    blocks = [
        _block("t1", 1, [100, 420, 900, 760], "", block_type="table"),
        _block("note", 1, [100, 790, 900, 830], "续表注释仍由 Markdown 合并证据兜底。"),
        _block("t2", 2, [100, 110, 900, 340], "", block_type="table"),
    ]
    markdown = """
<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>source</td><td>x</td><td>y</td></tr><tr><td>target</td><td>x</td><td>y</td></tr></table>
"""

    relations = build_table_relations("task", tables, blocks=blocks, markdown=markdown)["relations"]

    assert [(item["from_table_id"], item["to_table_id"]) for item in relations] == [("pt-1", "pt-2")]
    assert relations[0]["relation_type"] == "continuation"
    assert "rendered_markdown_same_table" in relations[0]["reasons"]
