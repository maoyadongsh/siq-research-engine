import re

import pdf_parser_content_list_enhanced_service as content_service


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
