import pdf_parser_content_list_enhanced_service as content_service


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
