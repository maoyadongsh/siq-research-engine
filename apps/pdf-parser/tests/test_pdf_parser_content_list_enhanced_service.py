import re

import pdf_parser_content_list_enhanced_service as content_service


def test_content_table_source_helpers_track_exact_normalized_and_printed_pages():
    exact_table = "<table><tr><td>收入</td></tr></table>"
    spaced_table = "<table>\n  <tr><td>成本</td></tr>\n</table>"
    content_list = [
        {"type": "page_number", "page_idx": 0, "text": "封面"},
        {
            "type": "table",
            "table_body": exact_table,
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "img_path": "page-1.png",
            "table_caption": ["收入表"],
            "table_footnote": ["单位：万元"],
        },
        {"type": "table", "table_body": spaced_table, "page_idx": 1},
    ]

    sources = content_service.content_table_sources(content_list)
    exact_sources, normalized_sources = content_service.content_table_source_maps(sources)
    used_source_ids = set()

    assert content_service.printed_page_numbers_by_pdf_page_map(content_list) == {1: "封面"}
    assert sources[0]["source_id"] == 1
    assert sources[0]["pdf_page_number"] == 1
    assert sources[0]["printed_page_number"] == "封面"
    assert sources[0]["bbox"] == [1, 2, 3, 4]
    assert sources[0]["image_path"] == "page-1.png"
    assert sources[0]["caption"] == ["收入表"]
    assert sources[0]["footnote"] == ["单位：万元"]

    exact_match = content_service.pop_unused_content_table_source(
        exact_table,
        exact_sources,
        normalized_sources,
        used_source_ids,
    )
    normalized_match = content_service.pop_unused_content_table_source(
        "<table><tr><td>成本</td></tr></table>",
        exact_sources,
        normalized_sources,
        used_source_ids,
    )
    duplicate_match = content_service.pop_unused_content_table_source(
        exact_table,
        exact_sources,
        normalized_sources,
        used_source_ids,
    )

    assert exact_match["source_match"] == "content_list_body_exact"
    assert exact_match["source_id"] == 1
    assert normalized_match["source_match"] == "content_list_body_normalized"
    assert normalized_match["source_id"] == 2
    assert duplicate_match == {}


def test_inferred_pdf_page_for_line_and_source_confidence():
    markers = [
        {"line": 10, "page_number": 1},
        {"line": 120, "page_number": 2},
    ]

    assert content_service.inferred_pdf_page_for_line(20, markers) == (1, "between_ordered_markers")
    assert content_service.inferred_pdf_page_for_line(119, markers) == (1, "between_ordered_markers")
    assert content_service.inferred_pdf_page_for_line(180, markers) == (2, "tail_near_previous_marker")
    assert content_service.inferred_pdf_page_for_line(400, markers) == (None, "no_safe_marker")
    assert content_service.inferred_pdf_page_for_line(None, markers) == (None, "")

    assert content_service.table_source_confidence("content_list_body_exact") == "high"
    assert content_service.table_source_confidence("content_list_body_normalized") == "high"
    assert content_service.table_source_confidence("markdown_marker_inferred") == "medium"
    assert content_service.table_source_confidence("unresolved") == "low"


def test_markdown_line_offsets_find_lines_from_character_offsets():
    markdown = "alpha\nbeta¹\n"
    offsets = content_service.markdown_line_offsets(markdown)

    assert content_service.markdown_line_offsets("") == [0]
    assert content_service.line_number_for_offset(offsets, 0) == 1
    assert content_service.line_number_for_offset(offsets, markdown.index("¹")) == 2


def test_build_enhanced_footnotes_binds_markdown_and_content_list_definitions():
    markdown = (
        "[PDF_PAGE: 3]\n"
        "# 公司简介\n"
        "打造四有¹银行。\n"
        "¹ 指有担当、有价值、有温度、有特色。\n"
        "指标1增长。\n"
        "第1页不应作为脚注引用。\n"
    )
    content_list = [
        {"type": "table", "table_footnote": ["2 表格口径为合并口径"], "page_idx": 4},
        {"type": "image", "image_footnote": ["图注3"], "page_idx": 5},
    ]

    footnotes = content_service.build_enhanced_footnotes(
        markdown,
        content_list=content_list,
        pdf_page_markers_by_line=lambda _text: [{"line": 1, "page_number": 3}],
    )

    assert footnotes["summary"] == {
        "reference_count": 2,
        "definition_count": 3,
        "bound_count": 1,
        "unbound_count": 1,
        "inline_digit_refs_suppressed": False,
    }
    assert [item["source"] for item in footnotes["references"]] == [
        "markdown_superscript",
        "markdown_inline_digit",
    ]
    assert footnotes["references"][0]["pdf_page_number"] == 3
    assert footnotes["bindings"][0]["status"] == "bound"
    assert footnotes["bindings"][1]["status"] == "unbound"
    content_definitions = [
        item for item in footnotes["definitions"] if item["source"] == "content_list_footnote"
    ]
    assert [item["pdf_page_number"] for item in content_definitions] == [5, 6]


def test_build_enhanced_toc_extracts_markdown_and_content_list_headings():
    markdown = (
        "[PDF_PAGE: 1]\n"
        "# 目录\n"
        "第一章 公司简介 …… 8\n"
        "[PDF_PAGE: 8]\n"
        "## 第一章 公司简介\n"
    )
    content_list = [
        {"type": "text", "text": "公司简介", "text_level": 1, "page_idx": 7},
        {"type": "text", "text": "Ignored", "text_level": 0, "page_idx": 7},
    ]

    toc = content_service.build_enhanced_toc(
        markdown,
        content_list=content_list,
        pdf_page_markers_by_line=lambda _text: [
            {"line": 1, "page_number": 1},
            {"line": 4, "page_number": 8},
        ],
    )

    assert [item["title"] for item in toc["headings"]] == ["目录", "第一章 公司简介"]
    assert toc["headings"][0]["pdf_page_number"] == 1
    assert toc["headings"][1]["pdf_page_number"] == 8
    assert toc["toc_candidates"][0]["title"] == "第一章 公司简介"
    assert toc["toc_candidates"][0]["level"] == 1
    assert toc["toc_candidates"][0]["target_page_number"] == 8
    assert toc["content_headings"] == [
        {
            "title": "公司简介",
            "level": 1,
            "line": None,
            "pdf_page_number": 8,
            "pdf_page_source": "content_list",
            "pdf_page_inference_reason": "",
            "source": "content_list_text_level",
        }
    ]
    assert toc["summary"] == {
        "heading_count": 2,
        "toc_candidate_count": 1,
        "content_heading_count": 1,
        "headings_with_page": 2,
        "toc_candidates_with_target_page": 1,
    }


def test_build_enhanced_quality_signals_aggregates_tables_notes_and_images():
    signals = content_service.build_enhanced_quality_signals(
        [
            {
                "source": "content_list_body_exact",
                "pdf_page_number": 1,
                "structure": {"multi_level_header_candidate": True},
            },
            {
                "source": "markdown_marker_inferred",
                "pdf_page_number": 2,
                "structure": {},
            },
            {
                "source": "unresolved",
                "pdf_page_number": None,
                "structure": {},
            },
        ],
        {
            "summary": {
                "reference_count": 4,
                "definition_count": 3,
                "unbound_count": 1,
            }
        },
        {
            "summary": {
                "heading_count": 5,
                "toc_candidate_count": 2,
                "content_heading_count": 1,
            }
        },
        [{"page_number": 1}, {"page_number": 2}],
        financial_note_links={"summary": {"linked_item_count": 7}},
        image_semantic_blocks=[
            {
                "semantic_kind": "chart",
                "actionability": "data_usable",
                "recognized_content": "table",
                "display_content": "chart",
                "show_in_complete": True,
            },
            {
                "semantic_kind": "natural_image",
                "actionability": "needs_ocr",
                "ocr_vlm_candidate": {"needed": True},
            },
        ],
    )

    assert signals["table_exact_rate"] == 0.3333
    assert signals["table_inferred_rate"] == 0.3333
    assert signals["table_missing_page_count"] == 1
    assert signals["multi_level_header_table_count"] == 1
    assert signals["footnote_reference_count"] == 4
    assert signals["toc_heading_count"] == 5
    assert signals["content_heading_count"] == 1
    assert signals["page_count_with_content_blocks"] == 2
    assert signals["financial_note_link_count"] == 7
    assert signals["image_semantic_kind_counts"] == {"chart": 1, "natural_image": 1}
    assert signals["image_semantic_actionability_counts"] == {"data_usable": 1, "needs_ocr": 1}
    assert signals["image_semantic_recognized_count"] == 1
    assert signals["image_semantic_display_count"] == 1
    assert signals["image_semantic_show_count"] == 1
    assert signals["image_semantic_ocr_candidate_count"] == 1


def test_build_content_list_enhanced_payload_uses_injected_table_sources_and_aggregates():
    exact_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>"
    inferred_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>成本</td><td>50</td></tr></table>"
    markdown = f"{exact_table}\n[PDF_PAGE: 2]\n{inferred_table}\n"
    source = {
        "source_id": 7,
        "table_body": exact_table,
        "pdf_page_index": 0,
        "pdf_page_number": 1,
        "printed_page_number": "1",
        "bbox": [1, 2, 3, 4],
        "image_path": "page-1.png",
        "caption": ["收入表"],
        "footnote": ["单位：万元"],
    }

    def content_table_sources(_content_list):
        return [source]

    def content_table_source_maps(table_sources):
        return {exact_table: list(table_sources)}, {}

    def pop_unused_content_table_source(table_html, exact_sources, _normalized_sources, used_source_ids):
        bucket = exact_sources.get(table_html) or []
        for item in bucket:
            source_id = item.get("source_id")
            if source_id in used_source_ids:
                continue
            used_source_ids.add(source_id)
            return {**item, "source_match": "content_list_body_exact"}
        return {}

    def build_enhanced_quality_signals(tables, footnotes, toc, pages, financial_note_links=None, image_semantic_blocks=None):
        return {
            "table_indexes": [item["table_index"] for item in tables],
            "footnote_count": len(footnotes.get("items", [])),
            "toc_count": len(toc.get("items", [])),
            "page_count": len(pages),
            "link_count": len(financial_note_links or []),
            "image_count": len(image_semantic_blocks or []),
        }

    payload = content_service.build_content_list_enhanced_payload(
        markdown,
        schema_version=11,
        content_table_sources=content_table_sources,
        content_table_source_maps=content_table_source_maps,
        pop_unused_content_table_source=pop_unused_content_table_source,
        pdf_page_markers_by_line=lambda _text: [{"line": 2, "page_number": 2}],
        printed_page_numbers_by_pdf_page=lambda _content_list: {1: "1", 2: "2"},
        inferred_pdf_page_for_line=lambda line, _markers: (2, "tail_near_previous_marker") if line > 2 else (None, "no_safe_marker"),
        strip_html=lambda html: re.sub(r"<[^>]+>", "", str(html or "")),
        table_structure_signals=lambda _html: {"expanded_rows": 2, "expanded_columns": 2},
        table_source_confidence=lambda source_name: "high" if source_name == "content_list_body_exact" else "medium",
        count_table_rows=lambda _html: 2,
        count_table_cells=lambda _html: 4,
        build_enhanced_page_blocks=lambda _content_list: [{"page_number": 1}, {"page_number": 2}],
        build_enhanced_footnotes=lambda _markdown, content_list=None: {"items": [{"text": "footnote"}]},
        build_enhanced_toc=lambda _markdown, content_list=None: {"items": [{"title": "目录"}]},
        build_financial_note_links=lambda _markdown, tables, _page_markers: [{"table_index": tables[0]["table_index"]}],
        build_image_semantic_blocks=lambda _markdown, content_list=None: [{"image_path": "chart.png"}],
        build_enhanced_quality_signals=build_enhanced_quality_signals,
        content_list=[],
        report_year=2025,
    )

    assert payload["schema_version"] == 11
    assert payload["report_year"] == 2025
    assert payload["table_count"] == 2
    assert payload["content_table_body_count"] == 1
    assert payload["source_counts"] == {"content_list_body_exact": 1, "markdown_marker_inferred": 1}
    assert payload["tables"][0]["confidence"] == "high"
    assert payload["tables"][0]["pdf_page_number"] == 1
    assert payload["tables"][0]["content_table_source_id"] == 7
    assert payload["tables"][0]["rows"] == 2
    assert payload["tables"][0]["cells"] == 4
    assert payload["tables"][0]["structure"] == {"expanded_rows": 2, "expanded_columns": 2}
    assert payload["tables"][1]["source"] == "markdown_marker_inferred"
    assert payload["tables"][1]["confidence"] == "medium"
    assert payload["tables"][1]["pdf_page_number"] == 2
    assert payload["tables"][1]["pdf_page_index"] == 1
    assert payload["tables"][1]["printed_page_number"] == "2"
    assert payload["tables"][1]["pdf_page_inference_reason"] == "tail_near_previous_marker"
    assert payload["quality_signals"] == {
        "table_indexes": [1, 2],
        "footnote_count": 1,
        "toc_count": 1,
        "page_count": 2,
        "link_count": 1,
        "image_count": 1,
    }


def test_build_image_semantic_blocks_extracts_chart_and_natural_image():
    markdown = (
        "[PDF_PAGE: 1]\n"
        "![](images/chart.jpg)\n"
        "<details>\n"
        "<summary>bar</summary>\n\n"
        "| Year | Value |\n"
        "|---|---|\n"
        "| 2025 | 100 |\n"
        "</details>\n"
        "![](images/photo.jpg)\n"
        "<details>\n"
        "<summary>natural_image</summary>\n\n"
        "Portrait of a man in formal suit and tie against a light blue background (no visible text or symbols)\n"
        "</details>\n"
    )
    content_list = [
        {
            "type": "chart",
            "sub_type": "bar",
            "img_path": "images/chart.jpg",
            "bbox": [10, 20, 100, 120],
            "page_idx": 0,
        },
        {
            "type": "image",
            "sub_type": "natural_image",
            "img_path": "images/photo.jpg",
            "bbox": [10, 140, 100, 220],
            "page_idx": 0,
        },
    ]

    blocks = content_service.build_image_semantic_blocks(markdown, content_list=content_list)

    assert blocks[0]["semantic_kind"] == "chart"
    assert blocks[0]["chart_data"]["headers"] == ["年份", "数值"]
    assert blocks[0]["show_in_complete"] is True
    assert "年份" in blocks[0]["display_content"]
    assert blocks[1]["semantic_kind"] == "natural_image"
    assert "人物肖像图片" in blocks[1]["display_content"]
    assert blocks[1]["show_in_complete"] is False


def test_build_image_semantic_blocks_extracts_flowchart_graph_for_complete_appendix():
    markdown = (
        "[PDF_PAGE: 2]\n"
        "![](images/flow.png)\n"
        "<details>\n"
        "<summary>flowchart</summary>\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "A[开始] --> B{审核}\n"
        "B -->|通过| C[结束]\n"
        "```\n"
        "</details>\n"
    )
    content_list = [
        {
            "type": "image",
            "sub_type": "flowchart",
            "img_path": "images/flow.png",
            "bbox": [10, 20, 260, 220],
            "page_idx": 1,
        }
    ]

    blocks = content_service.build_image_semantic_blocks(markdown, content_list=content_list)

    assert blocks[0]["semantic_kind"] == "flowchart"
    assert blocks[0]["flowchart_graph"]["node_count"] == 3
    assert blocks[0]["flowchart_graph"]["edge_count"] == 2
    assert blocks[0]["actionability"] == "structure_usable"
    assert blocks[0]["show_in_complete"] is True

    appendix = content_service.complete_markdown_appendix(
        {
            "table_count": 0,
            "source_counts": {},
            "quality_signals": {
                "image_semantic_block_count": 1,
                "image_semantic_show_count": 1,
                "image_semantic_ocr_candidate_count": 0,
            },
            "toc": {},
            "footnotes": {},
            "financial_note_links": {},
            "image_semantic_blocks": blocks,
            "tables": [],
        }
    )

    assert "## 图片、图表与公式增强识别" in appendix
    assert "可用性 structure_usable" in appendix
    assert "流程结构：3 个节点，2 条关系" in appendix


def test_build_image_semantic_blocks_marks_large_blank_image_as_ocr_candidate():
    markdown = "[PDF_PAGE: 3]\n![](images/big.png)\n"
    content_list = [
        {
            "type": "image",
            "sub_type": "natural_image",
            "img_path": "images/big.png",
            "bbox": [0, 0, 500, 400],
            "page_idx": 2,
        }
    ]

    blocks = content_service.build_image_semantic_blocks(markdown, content_list=content_list)

    candidate = blocks[0]["ocr_vlm_candidate"]
    assert candidate["needed"] is True
    assert candidate["priority"] == "high"
    assert blocks[0]["actionability"] == "needs_ocr"
    assert blocks[0]["show_in_complete"] is False

    appendix = content_service.complete_markdown_appendix(
        {
            "table_count": 0,
            "source_counts": {},
            "quality_signals": {
                "image_semantic_block_count": 1,
                "image_semantic_show_count": 0,
                "image_semantic_ocr_candidate_count": 1,
            },
            "toc": {},
            "footnotes": {},
            "financial_note_links": {},
            "image_semantic_blocks": blocks,
            "tables": [],
        }
    )

    assert "## 图片、图表与公式增强识别" not in appendix
    assert "## 按需 OCR/VLM 候选图像" in appendix
    assert "优先级 high" in appendix


def test_complete_markdown_content_applies_table_corrections():
    markdown = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>\n"
    enhanced = {
        "table_count": 0,
        "source_counts": {},
        "quality_signals": {},
        "toc": {},
        "footnotes": {},
        "financial_note_links": {},
        "image_semantic_blocks": [],
        "tables": [],
    }

    def apply_table_corrections(text, corrections):
        return text.replace("100", "200"), 1

    complete = content_service.complete_markdown_content(
        markdown,
        enhanced,
        corrections={"tables": {}},
        apply_table_corrections=apply_table_corrections,
    )

    assert "<td>200</td>" in complete
    assert "PDF 可恢复信息附录" in complete


def test_write_complete_markdown_artifact_writes_result_file(tmp_path):
    task = {"task_id": "complete-task"}
    enhanced = {
        "table_count": 1,
        "source_counts": {"content_list_body_exact": 1},
        "quality_signals": {},
        "toc": {},
        "footnotes": {},
        "financial_note_links": {},
        "image_semantic_blocks": [],
        "tables": [],
    }

    path = content_service.write_complete_markdown_artifact(
        task,
        "# 标题\n",
        enhanced,
        result_dir=lambda value: str(tmp_path / value["task_id"]),
    )

    assert path == str(tmp_path / "complete-task" / "result_complete.md")
    assert (tmp_path / "complete-task" / "result_complete.md").exists()
