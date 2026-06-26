import unittest
import types
import sys
import tempfile
import os
import json


class _DummyFlask:
    def __init__(self, *args, **kwargs):
        self.config = {}

    def route(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator

    def before_request(self, func=None):
        def decorator(func):
            return func

        return decorator if func is None else func

    def errorhandler(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


sys.modules.setdefault(
    "flask",
    types.SimpleNamespace(
        Flask=_DummyFlask,
        jsonify=lambda *args, **kwargs: None,
        make_response=lambda value: types.SimpleNamespace(
            value=value,
            set_cookie=lambda *args, **kwargs: None,
        ),
        render_template=lambda *args, **kwargs: "",
        request=types.SimpleNamespace(
            args={},
            files={},
            form={},
            headers={},
            cookies={},
            get_json=lambda silent=True: {},
        ),
        send_file=lambda *args, **kwargs: None,
    ),
)

import app


class PageMarkerInjectionTests(unittest.TestCase):
    def test_import_does_not_start_queue_worker(self):
        self.assertFalse(app._queue_worker_started)

    def test_read_markdown_does_not_refresh_page_markers_on_view(self):
        old_results_folder = app.RESULTS_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.RESULTS_FOLDER = tmpdir
                task = {"task_id": "view-task", "markdown_path": os.path.join(tmpdir, "view-task", "result.md")}
                os.makedirs(os.path.dirname(task["markdown_path"]), exist_ok=True)
                with open(task["markdown_path"], "w", encoding="utf-8") as outfile:
                    outfile.write("# 原始内容\n")

                markdown = app._read_markdown(task)

                self.assertEqual(markdown, "# 原始内容\n")
        finally:
            app.RESULTS_FOLDER = old_results_folder

    def test_cached_text_invalidates_when_file_changes(self):
        app._file_cache.clear()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
            tmp.write("first")
            path = tmp.name
        try:
            self.assertEqual(app._read_text_cached(path), "first")
            with open(path, "w", encoding="utf-8") as outfile:
                outfile.write("second-longer")
            self.assertEqual(app._read_text_cached(path), "second-longer")
        finally:
            os.unlink(path)

    def test_table_index_infers_pdf_page_from_markdown_markers(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 附注\n"
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>\n"
            "[PDF_PAGE: 11]\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "infer-page", "filename": "测试2025年年度报告.pdf"},
            content_list=[],
        )
        table = report["table_index"][0]

        self.assertEqual(table["pdf_page_number"], 10)
        self.assertEqual(table["pdf_page_index"], 9)
        self.assertEqual(table["pdf_page_source"], "markdown_marker_inferred")

    def test_table_index_matches_content_list_table_body_before_sequence(self):
        first_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>"
        second_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>成本</td><td>80</td></tr></table>"
        markdown = f"{second_table}\n\n{first_table}\n"
        content_list = [
            {
                "type": "table",
                "table_body": first_table,
                "page_idx": 4,
                "bbox": [10, 20, 30, 40],
            },
            {
                "type": "table",
                "table_body": second_table,
                "page_idx": 8,
                "bbox": [50, 60, 70, 80],
            },
        ]

        report = app._build_quality_report(
            markdown,
            {"task_id": "content-table-match", "filename": "测试2025年年度报告.pdf"},
            content_list=content_list,
        )
        first_seen, second_seen = report["table_index"]

        self.assertEqual(first_seen["pdf_page_number"], 9)
        self.assertEqual(first_seen["pdf_page_source"], "content_list_body_exact")
        self.assertEqual(first_seen["bbox"], [50, 60, 70, 80])
        self.assertEqual(second_seen["pdf_page_number"], 5)
        self.assertEqual(second_seen["pdf_page_source"], "content_list_body_exact")
        self.assertEqual(second_seen["bbox"], [10, 20, 30, 40])

    def test_build_content_list_enhanced_tracks_exact_and_inferred_sources(self):
        exact_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>"
        extra_table = "<table><tr><td>项目</td><td>金额</td></tr><tr><td>成本</td><td>80</td></tr></table>"
        markdown = f"{exact_table}\n[PDF_PAGE: 20]\n{extra_table}\n"
        content_list = [
            {
                "type": "table",
                "table_body": exact_table,
                "page_idx": 6,
                "bbox": [10, 20, 30, 40],
            },
            {
                "type": "page_number",
                "text": "5 ",
                "page_idx": 6,
            },
            {
                "type": "page_number",
                "text": "18 ",
                "page_idx": 19,
            },
        ]

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)
        exact, inferred = enhanced["tables"]

        self.assertEqual(enhanced["schema_version"], app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION)
        self.assertEqual(enhanced["source_counts"]["content_list_body_exact"], 1)
        self.assertEqual(enhanced["source_counts"]["markdown_marker_inferred"], 1)
        self.assertEqual(exact["source"], "content_list_body_exact")
        self.assertEqual(exact["confidence"], "high")
        self.assertEqual(exact["pdf_page_number"], 7)
        self.assertEqual(exact["printed_page_number"], "5")
        self.assertEqual(exact["bbox"], [10, 20, 30, 40])
        self.assertEqual(inferred["source"], "markdown_marker_inferred")
        self.assertEqual(inferred["confidence"], "medium")
        self.assertEqual(inferred["pdf_page_number"], 20)
        self.assertEqual(inferred["printed_page_number"], "18")
        self.assertEqual(inferred["bbox"], [])
        pages = {item["page_number"]: item for item in enhanced["pages"]}
        self.assertEqual(pages[7]["pdf_page_number"], 7)
        self.assertEqual(pages[7]["printed_page_number"], "5")
        self.assertEqual(pages[20]["printed_page_number"], "18")
        self.assertIn("structure", exact)
        self.assertIn("footnotes", enhanced)
        self.assertIn("toc", enhanced)
        self.assertIn("quality_signals", enhanced)

    def test_source_page_payload_uses_endpoint_table_index_and_printed_page(self):
        first_table = "<table><tr><td>股东总数</td><td>100</td></tr></table>"
        second_table = "<table><tr><td>前十名普通股股东</td><td>上海联和</td></tr></table>"
        content_list = [
            {"type": "table", "table_body": first_table, "page_idx": 133, "bbox": [1, 2, 3, 4]},
            {"type": "table", "table_body": second_table, "page_idx": 133, "bbox": [5, 6, 7, 8]},
            {"type": "page_number", "text": "133 ", "page_idx": 133},
        ]
        report = {
            "table_index": [
                {"table_index": 89, "content_table_source_id": 1, "line": 2421, "pdf_page_number": 134},
                {"table_index": 90, "content_table_source_id": 2, "line": 2428, "pdf_page_number": 134},
            ]
        }

        payload = app.page_content_payload_from_content_list(content_list, 134, report=report, focus_table=90)
        tables = [item for item in payload["blocks"] if item.get("type") == "table"]

        self.assertEqual(payload["printed_page_number"], "133")
        self.assertEqual([item["table_index"] for item in tables], [89, 90])
        self.assertEqual(tables[1]["line"], 2428)
        self.assertTrue(tables[1]["is_focus_table"])

    def test_build_content_list_enhanced_extracts_recoverable_footnotes_and_toc(self):
        markdown = (
            "[PDF_PAGE: 1]\n"
            "# 目录\n"
            "第一章 公司简介 …… 8\n"
            "[PDF_PAGE: 8]\n"
            "# 第一章 公司简介\n"
            "打造四有¹银行。\n"
            "¹ 指有担当、有价值、有温度、有特色。\n"
            "<table><tr><th colspan=\"2\">项目</th></tr><tr><th>本年</th><th>上年</th></tr>"
            "<tr><td>收入</td><td>100</td></tr></table>\n"
        )
        content_list = [
            {"type": "text", "text": "目录", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "第一章 公司简介", "text_level": 1, "page_idx": 7},
        ]

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)

        self.assertEqual(enhanced["schema_version"], app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION)
        self.assertEqual(enhanced["footnotes"]["summary"]["reference_count"], 1)
        self.assertGreaterEqual(enhanced["footnotes"]["summary"]["definition_count"], 1)
        self.assertGreaterEqual(enhanced["toc"]["summary"]["toc_candidate_count"], 1)
        self.assertGreaterEqual(enhanced["toc"]["summary"]["content_heading_count"], 2)
        self.assertTrue(enhanced["tables"][0]["structure"]["multi_level_header_candidate"])
        self.assertEqual(enhanced["quality_signals"]["multi_level_header_table_count"], 1)

    def test_content_list_enhanced_links_financial_statement_items_to_notes(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td></tr>"
            "<tr><td>货币资金</td><td>100</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七、合并财务报表项目注释\n"
            "1、货币资金\n"
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>库存现金</td><td>10</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        links = enhanced["financial_note_links"]["links"]

        self.assertTrue(any(item["statement_item"] == "货币资金" for item in links))
        self.assertGreaterEqual(enhanced["quality_signals"]["financial_note_link_count"], 1)

    def test_content_list_enhanced_links_statement_note_ref_to_note_title(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>附注</td><td>2025年12月31日</td></tr>"
            "<tr><td>货币资金</td><td>七、1</td><td>100</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七、合并财务报表项目注释\n"
            "1、货币资金\n"
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>库存现金</td><td>10</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        links = enhanced["financial_note_links"]["links"]
        link = next(item for item in links if item["statement_item"] == "货币资金")

        self.assertEqual(link["statement_note_ref"], "七、1")
        self.assertEqual(link["note_ref"], "七、1")
        self.assertEqual(link["confidence"], "high")
        self.assertEqual(link["method"], "statement_note_ref_to_note_title")
        self.assertIn("note_ref_exact", link["evidence"])
        self.assertEqual(enhanced["financial_note_links"]["summary"]["high_confidence_link_count"], 1)

    def test_content_list_enhanced_verifies_statement_note_amount(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>附注</td><td>2025年12月31日</td></tr>"
            "<tr><td>货币资金</td><td>七、1</td><td>1,000.00</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七、合并财务报表项目注释\n"
            "1、货币资金\n"
            "<table><tr><td>项目</td><td>期末余额</td></tr>"
            "<tr><td>合计</td><td>1,000.00</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        link = next(item for item in enhanced["financial_note_links"]["links"] if item["statement_item"] == "货币资金")

        self.assertEqual(link["amount_check"]["status"], "verified")
        self.assertEqual(link["amount_check"]["confidence"], "high")
        self.assertEqual(link["precision_level"], "audit_ready_navigation")
        self.assertEqual(enhanced["financial_note_links"]["summary"]["amount_verified_table_count"], 1)
        self.assertEqual(enhanced["financial_note_links"]["summary"]["audit_ready_navigation_count"], 1)

    def test_content_list_enhanced_verifies_amount_without_note_column(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td></tr>"
            "<tr><td>货币资金</td><td>1,000.00</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七、合并财务报表项目注释\n"
            "1、货币资金\n"
            "<table><tr><td>项目</td><td>期末余额</td></tr>"
            "<tr><td>合计</td><td>1,000.00</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        link = next(item for item in enhanced["financial_note_links"]["links"] if item["statement_item"] == "货币资金")

        self.assertEqual(link["confidence"], "medium")
        self.assertEqual(link["amount_check"]["status"], "verified")
        self.assertEqual(link["amount_check"]["confidence"], "high")
        self.assertEqual(enhanced["financial_note_links"]["summary"]["amount_verified_table_count"], 1)

    def test_content_list_enhanced_links_bank_note_titles_with_space_and_continued_marker(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>附注</td><td>2025年12月31日</td></tr>"
            "<tr><td>固定资产</td><td>七 10</td><td>1,000.00</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七 财务报表主要项目附注 (续)\n"
            "# 10 固定资产 (续)\n"
            "<table><tr><td>项目</td><td>期末余额</td></tr>"
            "<tr><td>合计</td><td>1,000.00</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        link = next(item for item in enhanced["financial_note_links"]["links"] if item["statement_item"] == "固定资产")

        self.assertEqual(link["statement_note_ref"], "七、10")
        self.assertEqual(link["note_ref"], "七、10")
        self.assertEqual(link["confidence"], "high")
        self.assertEqual(link["amount_check"]["status"], "verified")

    def test_content_list_enhanced_links_statement_items_without_note_column_from_statement_tables(self):
        markdown = (
            "[PDF_PAGE: 10]\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>交易性金融资产</td><td>2,000.00</td><td>1,500.00</td></tr></table>\n"
            "[PDF_PAGE: 80]\n"
            "# 七、合并财务报表项目注释\n"
            "2、交易性金融资产\n"
            "<table><tr><td>项目</td><td>期末余额</td></tr>"
            "<tr><td>合计</td><td>2,000.00</td></tr></table>\n"
        )

        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        link = next(item for item in enhanced["financial_note_links"]["links"] if item["statement_item"] == "交易性金融资产")

        self.assertEqual(link["method"], "statement_item_to_note_title_alias")
        self.assertEqual(link["amount_check"]["status"], "verified")
        self.assertEqual(enhanced["financial_note_links"]["summary"]["linked_item_count"], 1)

    def test_document_full_backfills_standalone_content_list_enhanced(self):
        old_results_folder = app.RESULTS_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.RESULTS_FOLDER = tmpdir
                task = {
                    "task_id": "doc-full-backfill",
                    "filename": "公司2025年年度报告.md",
                    "upload_path": "",
                }
                markdown = (
                    "[PDF_PAGE: 10]\n"
                    "# 合并资产负债表\n"
                    "<table><tr><td>项目</td><td>附注</td><td>2025年12月31日</td></tr>"
                    "<tr><td>货币资金</td><td>七、1</td><td>100</td></tr></table>\n"
                    "[PDF_PAGE: 80]\n"
                    "# 七、合并财务报表项目注释\n"
                    "1、货币资金\n"
                    "<table><tr><td>项目</td><td>期末余额</td></tr><tr><td>合计</td><td>100</td></tr></table>\n"
                )

                enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
                app._write_document_full_artifact(task, markdown, enhanced, {"warnings": []})
                standalone_path = os.path.join(tmpdir, task["task_id"], "content_list_enhanced.json")
                self.assertFalse(os.path.exists(standalone_path))

                app._ensure_document_full_artifact(task, markdown, {"warnings": []})

                self.assertTrue(os.path.exists(standalone_path))
                with open(standalone_path, "r", encoding="utf-8") as fh:
                    standalone = json.load(fh)
                self.assertEqual(standalone["schema_version"], app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION)
                self.assertEqual(
                    standalone["financial_note_links"]["summary"]["audit_ready_navigation_count"],
                    1,
                )
        finally:
            app.RESULTS_FOLDER = old_results_folder

    def test_complete_markdown_can_apply_table_corrections(self):
        markdown = (
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>\n"
        )
        enhanced = app._build_content_list_enhanced(markdown, content_list=[], report_year=2025)
        corrections = {
            "tables": {
                "1": {
                    "review_status": "fixed",
                    "table_markdown": "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>200</td></tr></table>",
                }
            }
        }

        complete = app._complete_markdown_content(markdown, enhanced, corrections=corrections)

        self.assertIn("<td>200</td>", complete)
        self.assertIn("PDF 可恢复信息附录", complete)

    def test_content_list_enhanced_extracts_image_semantic_blocks(self):
        markdown = (
            "[PDF_PAGE: 1]\n"
            "![](images/chart.jpg)\n"
            "<details>\n"
            "<summary>bar</summary>\n\n"
            "| 年份 | 金额 |\n"
            "|---|---|\n"
            "| 2025 | 100 |\n"
            "</details>\n"
            "![](images/formula.jpg)\n"
            "<details>\n"
            "<summary>equation</summary>\n\n"
            "$$E=mc^2$$\n"
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
                "type": "equation",
                "img_path": "images/formula.jpg",
                "bbox": [10, 140, 100, 180],
                "page_idx": 0,
            },
        ]

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)
        blocks = enhanced["image_semantic_blocks"]

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["semantic_kind"], "chart")
        self.assertEqual(blocks[0]["content_format"], "markdown_table")
        self.assertEqual(blocks[0]["confidence"], "high")
        self.assertEqual(blocks[0]["actionability"], "data_usable")
        self.assertEqual(blocks[0]["chart_data"]["headers"], ["年份", "金额"])
        self.assertEqual(blocks[0]["chart_data"]["rows"][0]["金额"], "100")
        self.assertTrue(blocks[0]["show_in_complete"])
        self.assertIn("2025", blocks[0]["recognized_content"])
        self.assertEqual(blocks[1]["semantic_kind"], "formula")
        self.assertIn("E=mc", blocks[1]["recognized_content"])
        self.assertEqual(blocks[1]["actionability"], "formula_candidate")
        self.assertEqual(enhanced["quality_signals"]["image_semantic_block_count"], 2)
        self.assertEqual(enhanced["quality_signals"]["image_semantic_recognized_count"], 2)
        self.assertEqual(enhanced["quality_signals"]["image_semantic_show_count"], 2)

        complete = app._complete_markdown_content(markdown, enhanced)
        self.assertIn("图片、图表与公式增强识别", complete)
        self.assertIn("识别预览", complete)
        self.assertIn("图表数据", complete)

    def test_image_semantic_blocks_prefer_chinese_display_content(self):
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

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)
        chart, photo = enhanced["image_semantic_blocks"]

        self.assertEqual(chart["recognized_language"], "en")
        self.assertIn("年份", chart["display_content"])
        self.assertIn("数值", chart["display_content"])
        self.assertEqual(chart["chart_data"]["headers"], ["年份", "数值"])
        self.assertEqual(photo["recognized_language"], "en")
        self.assertIn("人物肖像图片", photo["display_content"])
        self.assertIn("未见可读文字或符号", photo["display_content"])
        self.assertEqual(photo["actionability"], "visual_context_only")
        self.assertFalse(photo["show_in_complete"])

        complete = app._complete_markdown_content(markdown, enhanced)
        self.assertIn("年份", complete)
        self.assertNotIn("人物肖像图片", complete)
        self.assertNotIn("未见可读文字或符号", complete)

    def test_image_semantic_blocks_extract_flowchart_graph(self):
        markdown = (
            "[PDF_PAGE: 1]\n"
            "![](images/flow.jpg)\n"
            "<details>\n"
            "<summary>flowchart</summary>\n\n"
            "```mermaid\n"
            "graph TD\n"
            "A[开始] --> B[审批]\n"
            "B -->|通过| C[结束]\n"
            "```\n"
            "</details>\n"
        )
        content_list = [
            {
                "type": "image",
                "sub_type": "flowchart",
                "img_path": "images/flow.jpg",
                "bbox": [0, 0, 500, 500],
                "page_idx": 0,
            }
        ]

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)
        block = enhanced["image_semantic_blocks"][0]

        self.assertEqual(block["semantic_kind"], "flowchart")
        self.assertEqual(block["content_format"], "mermaid")
        self.assertEqual(block["actionability"], "structure_usable")
        self.assertEqual(block["flowchart_graph"]["node_count"], 3)
        self.assertEqual(block["flowchart_graph"]["edge_count"], 2)
        self.assertTrue(block["show_in_complete"])

        complete = app._complete_markdown_content(markdown, enhanced)
        self.assertIn("流程结构", complete)

    def test_low_confidence_large_image_is_ocr_candidate_not_main_appendix_noise(self):
        markdown = "[PDF_PAGE: 1]\n![](images/large.jpg)\n"
        content_list = [
            {
                "type": "image",
                "sub_type": "natural_image",
                "img_path": "images/large.jpg",
                "bbox": [0, 0, 800, 600],
                "page_idx": 0,
            }
        ]

        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)
        block = enhanced["image_semantic_blocks"][0]

        self.assertEqual(block["actionability"], "needs_ocr")
        self.assertTrue(block["ocr_vlm_candidate"]["needed"])
        self.assertFalse(block["show_in_complete"])
        self.assertEqual(enhanced["quality_signals"]["image_semantic_ocr_candidate_count"], 1)

        complete = app._complete_markdown_content(markdown, enhanced)
        self.assertIn("按需 OCR/VLM 候选图像", complete)
        self.assertNotIn("图片、图表与公式增强识别", complete)

    def test_table_index_does_not_infer_far_ambiguous_page(self):
        markdown = "[PDF_PAGE: 10]\n" + ("\n" * 250) + (
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>\n"
            + ("\n" * 120) +
            "[PDF_PAGE: 11]\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "infer-page-far", "filename": "测试2025年年度报告.pdf"},
            content_list=[],
        )
        table = report["table_index"][0]

        self.assertIsNone(table["pdf_page_number"])
        self.assertEqual(table["pdf_page_source"], "")

    def test_table_index_infers_pdf_page_from_near_next_marker(self):
        markdown = "[PDF_PAGE: 10]\n" + ("\n" * 250) + (
            "<table><tr><td>项目</td><td>金额</td></tr><tr><td>收入</td><td>100</td></tr></table>\n"
            "[PDF_PAGE: 11]\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "infer-page-near-next", "filename": "测试2025年年度报告.pdf"},
            content_list=[],
        )
        table = report["table_index"][0]

        self.assertEqual(table["pdf_page_number"], 11)
        self.assertEqual(table["pdf_page_index"], 10)
        self.assertEqual(table["pdf_page_source"], "markdown_marker_inferred")
        self.assertEqual(table["pdf_page_inference_reason"], "near_next_marker")

    def test_injects_page_markers_before_page_anchors(self):
        markdown = (
            "# 封面\n\n"
            "重要提示、目录和释义\n\n"
            "# 目录\n\n"
            "# 第一节 重要提示、目录和释义\n\n"
            "# 第二节 公司简介\n"
        )
        content_list = [
            {"type": "text", "text": "封面", "page_idx": 0},
            {"type": "text", "text": "目录", "page_idx": 1},
            {"type": "text", "text": "第一节 重要提示、目录和释义", "page_idx": 1},
            {"type": "text", "text": "第二节 公司简介", "page_idx": 2},
        ]

        marked = app._inject_pdf_page_markers(markdown, content_list)

        self.assertEqual(marked.count("[PDF_PAGE:"), 3)
        self.assertTrue(marked.startswith("[PDF_PAGE: 1]\n"))
        self.assertLess(marked.index("[PDF_PAGE: 2]"), marked.index("# 目录"))
        self.assertLess(marked.index("[PDF_PAGE: 3]"), marked.index("# 第二节 公司简介"))

    def test_rebuilds_existing_markers_without_duplication(self):
        markdown = (
            "<!-- PDF_PAGE: 1 -->\n"
            "# 封面\n\n"
            "<!-- PDF_PAGE: 2 -->\n"
            "# 目录\n"
        )
        content_list = [
            {"type": "text", "text": "封面", "page_idx": 0},
            {"type": "text", "text": "目录", "page_idx": 1},
        ]

        marked = app._inject_pdf_page_markers(markdown, content_list)

        self.assertEqual(marked.count("[PDF_PAGE:"), 2)
        self.assertEqual(marked.splitlines()[0], "[PDF_PAGE: 1]")

    def test_fills_missing_pages_between_known_page_anchors(self):
        markdown = "# 封面\n\n第二页缺少唯一锚点，但原始 Markdown 仍有一段正文。\n\n# 第三页\n"
        content_list = [
            {"type": "text", "text": "封面", "page_idx": 0},
            {"type": "text", "text": "第三页", "page_idx": 2},
        ]

        marked = app._inject_pdf_page_markers(markdown, content_list, total_pages=3)

        self.assertEqual(marked.count("[PDF_PAGE:"), 3)
        self.assertLess(marked.index("[PDF_PAGE: 2]"), marked.index("[PDF_PAGE: 3]"))
        self.assertLess(marked.index("[PDF_PAGE: 3]"), marked.index("# 第三页"))
        between_pages = marked[
            marked.index("[PDF_PAGE: 2]") + len("[PDF_PAGE: 2]") : marked.index("[PDF_PAGE: 3]")
        ]
        self.assertNotEqual(between_pages.strip(), "")

    def test_page_marker_mapping_handles_expanding_lowercase(self):
        markdown = "# İSTANBUL\n\n# 第二页\n"
        content_list = [
            {"type": "text", "text": "İSTANBUL", "page_idx": 0},
            {"type": "text", "text": "第二页", "page_idx": 1},
        ]

        marked = app._inject_pdf_page_markers(markdown, content_list)

        self.assertEqual(marked.count("[PDF_PAGE:"), 2)
        self.assertIn("[PDF_PAGE: 2]\n# 第二页", marked)

    def test_backfills_sparse_page_from_content_list(self):
        markdown = "[PDF_PAGE: 1]\n# 正常页\n\n[PDF_PAGE: 2]\n"
        content_list = [
            {"type": "text", "text": "正常页", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "第二页标题", "text_level": 1, "page_idx": 1},
            {"type": "text", "text": "第二页正文内容", "page_idx": 1},
        ]

        rebuilt, restored = app._backfill_sparse_markdown_pages(markdown, content_list)

        self.assertEqual(restored, [1, 2])
        self.assertIn("[PDF_PAGE: 2]", rebuilt)
        self.assertIn("# 第二页标题", rebuilt)
        self.assertIn("第二页正文内容", rebuilt)


class ValidationTests(unittest.TestCase):
    def test_parse_submit_config_defaults_enable_formula_and_table(self):
        config = app._parse_submit_config(
            {
                "backend": "hybrid-http-client",
                "parse_method": "auto",
            }
        )

        self.assertTrue(config["formula_enable"])
        self.assertTrue(config["table_enable"])

    def test_parse_submit_config_rejects_bad_page_range(self):
        with self.assertRaises(ValueError):
            app._parse_submit_config(
                {
                    "backend": "hybrid-http-client",
                    "parse_method": "auto",
                    "start_page_id": "3",
                    "end_page_id": "2",
                }
            )

    def test_safe_client_filename_strips_paths_and_controls(self):
        self.assertEqual(app._safe_client_filename("C:\\tmp\\bad\r\n.pdf"), "bad__.pdf")

    def test_pdf_magic_check(self):
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"%PDF-1.7\n")
            tmp.flush()
            self.assertTrue(app._looks_like_pdf(tmp.name))
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"not a pdf")
            tmp.flush()
            self.assertFalse(app._looks_like_pdf(tmp.name))

    def test_quality_report_schema_version_is_current(self):
        report = app._build_quality_report(
            "<table><tr><td>资产总额</td><td>100</td></tr></table>",
            {"task_id": "task-1", "filename": "测试2025年年度报告.pdf"},
        )
        self.assertEqual(report["schema_version"], app.QUALITY_SCHEMA_VERSION)

    def test_markdown_image_reference_is_info_not_warning(self):
        report = app._build_quality_report(
            "![公式](images/formula_1.png)\n<table><tr><td>资产总额</td><td>100</td></tr></table>",
            {"task_id": "task-1", "filename": "测试2025年年度报告.pdf"},
        )

        self.assertEqual(report["image_ref_count"], 1)
        self.assertTrue(any("图片引用" in item for item in report.get("info_messages") or []))
        self.assertFalse(any("图片引用" in item for item in report.get("warnings") or []))

    def test_quality_candidates_do_not_promote_revenue_from_core_tables(self):
        markdown = (
            "# 主要会计数据\n"
            "<table><tr><td>项目</td><td>2025年</td></tr>"
            "<tr><td>营业收入</td><td>100</td></tr>"
            "<tr><td>归属于上市公司股东的净利润</td><td>10</td></tr></table>\n"
            "# 合并利润表\n"
            "<table><tr><td>项目</td><td>2025年度</td></tr>"
            "<tr><td>一、营业收入</td><td>100</td></tr>"
            "<tr><td>二、营业利润</td><td>12</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "task-1", "filename": "测试2025年年报.pdf"},
        )

        self.assertEqual(report["report_year"], 2025)
        self.assertIn("主要会计数据", report["key_table_candidates"])
        self.assertIn("利润表", report["key_table_candidates"])
        self.assertNotIn("营业收入", report["key_table_candidates"])

    def test_quality_candidates_split_indicator_tables(self):
        markdown = (
            "# 营业收入构成情况\n"
            "<table><tr><td>分行业</td><td>营业收入</td><td>营业成本</td></tr>"
            "<tr><td>制造业</td><td>100</td><td>70</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "task-1", "filename": "测试2025年年度报告.pdf"},
        )

        self.assertIn("营业收入", report["key_table_candidates"])
        self.assertIn("分行业", report["key_table_candidates"])
        indicators = {item["name"]: item for item in report["indicator_table_candidates"]}
        self.assertEqual(indicators["营业收入"]["status"], "found")
        self.assertEqual(indicators["分行业"]["status"], "found")

    def test_financial_artifact_freshness_checks_rule_version(self):
        current_data = {
            "schema_version": app.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": app.FINANCIAL_RULE_VERSION,
        }
        current_checks = {
            "schema_version": app.FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": app.FINANCIAL_RULE_VERSION,
        }
        stale_data = dict(current_data, rule_version="old_rules")

        self.assertTrue(app._financial_artifacts_are_current(current_data, current_checks))
        self.assertFalse(app._financial_artifacts_are_current(stale_data, current_checks))

    def test_ensure_quality_report_refreshes_stale_financial_artifacts(self):
        old_results_folder = app.RESULTS_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.RESULTS_FOLDER = tmpdir
                task = {"task_id": "task-cache", "filename": "测试2025年年度报告.pdf"}
                result_dir = os.path.join(tmpdir, task["task_id"])
                os.makedirs(result_dir, exist_ok=True)
                with open(os.path.join(result_dir, "financial_data.json"), "w", encoding="utf-8") as outfile:
                    outfile.write('{"schema_version": 1, "rule_version": "old", "statements": []}')
                with open(os.path.join(result_dir, "financial_checks.json"), "w", encoding="utf-8") as outfile:
                    outfile.write('{"schema_version": 1, "rule_version": "old", "checks": [], "summary": {}}')
                markdown = (
                    "# 主要会计数据\n"
                    "<table><tr><td>项目</td><td>2025年</td></tr>"
                    "<tr><td>营业收入</td><td>100</td></tr>"
                    "<tr><td>归属于上市公司股东的净利润</td><td>10</td></tr></table>\n"
                )

                report = app._ensure_quality_report(task, markdown)
                financial_data, financial_checks = app._read_financial_artifacts(task)

                self.assertEqual(report["schema_version"], app.QUALITY_SCHEMA_VERSION)
                self.assertTrue(app._financial_artifacts_are_current(financial_data, financial_checks))
        finally:
            app.RESULTS_FOLDER = old_results_folder

    def test_summary_reports_surface_full_report_requirement(self):
        markdown = (
            "# 中信建投证券股份有限公司2025 年年度报告摘要\n"
            "一、本年度报告摘要来自年度报告全文。\n"
            "# 三、公司主要会计数据和财务指标\n"
            "<table><tr><td>项目</td><td>2025年</td></tr><tr><td>资产总额</td><td>100</td></tr></table>\n"
        )
        data = app.build_financial_data(markdown, task_id="summary-task", filename="中信建投：2025年年度报告摘要.md")
        checks = app.build_financial_checks(data)
        report = app._build_quality_report(markdown, {"task_id": "summary-task", "filename": "中信建投：2025年年度报告摘要.md"})
        report = app._merge_quality_candidates_from_financial_data(report, data)
        report["warnings"] = app._quality_report_warnings(report, data)

        self.assertEqual(data["report_kind"], "annual_report_summary")
        self.assertEqual(checks["overall_status"], "skipped")
        self.assertTrue(any("不应将摘要文件当作完整年报" in item for item in checks["warnings"]))
        self.assertTrue(any("摘要文件不提供完整三大表" in item for item in report["warnings"]))

    def test_formal_statement_titles_allow_spaced_year_prefixes(self):
        markdown = (
            "# 测试股份有限公司\n"
            "# 2025 年度合并利润表\n"
            "<table><tr><td></td><td>附注</td><td>2025年度合并</td><td>2024年度合并</td></tr>"
            "<tr><td>一、营业收入</td><td></td><td>100</td><td>90</td></tr>"
            "<tr><td>二、营业利润</td><td></td><td>20</td><td>18</td></tr>"
            "<tr><td>三、利润总额</td><td></td><td>19</td><td>17</td></tr>"
            "<tr><td>四、净利润</td><td></td><td>15</td><td>13</td></tr>"
            "<tr><td>归属于母公司股东的净利润</td><td></td><td>14</td><td>12</td></tr></table>\n"
            "# 2025 年度合并及公司现金流量表\n"
            "<table><tr><td>项目</td><td>附注</td><td>2025年度合并</td><td>2024年度合并</td><td>2025年度公司</td><td>2024年度公司</td></tr>"
            "<tr><td>一、经营活动产生的现金流量</td><td></td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>经营活动现金流入小计</td><td></td><td>120</td><td>110</td><td>60</td><td>55</td></tr>"
            "<tr><td>经营活动现金流出小计</td><td></td><td>80</td><td>75</td><td>40</td><td>35</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td></td><td>40</td><td>35</td><td>20</td><td>20</td></tr>"
            "<tr><td>投资活动产生的现金流量净额</td><td></td><td>5</td><td>4</td><td>2</td><td>1</td></tr>"
            "<tr><td>筹资活动产生的现金流量净额</td><td></td><td>3</td><td>2</td><td>1</td><td>1</td></tr>"
            "<tr><td>现金及现金等价物净增加额</td><td></td><td>48</td><td>41</td><td>23</td><td>22</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="formal-title", filename="测试2025年年度报告.md")
        consolidated = {
            item["statement_type"]
            for item in data["statements"]
            if item.get("scope") == "consolidated"
        }

        self.assertIn("income_statement", consolidated)
        self.assertIn("cash_flow_statement", consolidated)
        self.assertTrue(any("资产负债表" in item for item in data["warnings"]))

    def test_spaced_statement_heading_and_split_cash_flow_parts(self):
        markdown = (
            "# 合 并 利 润 表\n"
            "年度\n"
            "<table><tr><td></td><td>附注七</td><td>2025年度</td><td>2024年度</td></tr>"
            "<tr><td>一、营业总收入</td><td></td><td>100</td><td>90</td></tr>"
            "<tr><td>营业利润</td><td></td><td>20</td><td>18</td></tr>"
            "<tr><td>利润总额</td><td></td><td>19</td><td>17</td></tr>"
            "<tr><td>净利润</td><td></td><td>15</td><td>13</td></tr>"
            "<tr><td>归属于母公司股东的净利润</td><td></td><td>14</td><td>12</td></tr></table>\n"
            "2025年\n2024年\n"
            "# 一、经营活动产生的现金流量\n"
            "<table><tr><td>销售商品、提供劳务收到的现金</td><td></td><td>120</td><td>110</td></tr>"
            "<tr><td>经营活动现金流入小计</td><td></td><td>120</td><td>110</td></tr>"
            "<tr><td>经营活动现金流出小计</td><td></td><td>80</td><td>75</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td></td><td>40</td><td>35</td></tr></table>\n"
            "# 二、投资活动产生的现金流量\n"
            "<table><tr><td>投资活动现金流入小计</td><td>10</td><td>9</td></tr>"
            "<tr><td>投资活动现金流出小计</td><td>5</td><td>5</td></tr>"
            "<tr><td>投资活动产生的现金流量净额</td><td>5</td><td>4</td></tr></table>\n"
            "<table><tr><td></td><td>附注七</td><td>2025年</td><td>2024年</td></tr>"
            "<tr><td>三、筹资活动产生的现金流量</td><td></td><td></td><td></td></tr>"
            "<tr><td>筹资活动现金流入小计</td><td></td><td>3</td><td>2</td></tr>"
            "<tr><td>筹资活动现金流出小计</td><td></td><td>0</td><td>0</td></tr>"
            "<tr><td>筹资活动产生的现金流量净额</td><td></td><td>3</td><td>2</td></tr>"
            "<tr><td>现金及现金等价物净增加额</td><td></td><td>48</td><td>41</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="split-cash", filename="测试2025年年度报告.md")
        income = next((item for item in data["statements"] if item["statement_type"] == "income_statement" and item["scope"] == "consolidated"), None)
        cash_flow = next((item for item in data["statements"] if item["statement_type"] == "cash_flow_statement" and item["scope"] == "consolidated"), None)

        self.assertIsNotNone(income)
        self.assertIsNotNone(cash_flow)
        self.assertGreaterEqual(len(cash_flow["table_indexes"]), 3)
        self.assertFalse(any("现金流量表" in item and "未识别到合并三大表" in item for item in data["warnings"]))

    def test_change_analysis_cash_flow_table_is_not_promoted(self):
        markdown = (
            "# 4、现金流\n"
            "<table><tr><td>项目</td><td>2025 年</td><td>2024 年</td><td>同比增减</td></tr>"
            "<tr><td>经营活动现金流入小计</td><td>120</td><td>110</td><td>8.9%</td></tr>"
            "<tr><td>经营活动现金流出小计</td><td>80</td><td>75</td><td>6.7%</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td>40</td><td>35</td><td>14.3%</td></tr>"
            "<tr><td>投资活动产生的现金流量净额</td><td>5</td><td>4</td><td>25%</td></tr>"
            "<tr><td>筹资活动产生的现金流量净额</td><td>3</td><td>2</td><td>50%</td></tr>"
            "<tr><td>现金及现金等价物净增加额</td><td>48</td><td>41</td><td>17.1%</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="analysis-cash", filename="测试2025年年度报告.md")

        cash_flow = next((item for item in data["statements"] if item["statement_type"] == "cash_flow_statement" and item["scope"] == "consolidated"), None)

        self.assertIsNone(cash_flow)
        self.assertTrue(any("完整年报未识别到合并三大表" in item for item in data["warnings"]))

    def test_signature_page_under_statement_heading_is_not_statement(self):
        markdown = (
            "# 合并利润表和母公司利润表 (续)\n"
            "<table><tr><td>张三</td><td>李四</td><td>王五</td></tr>"
            "<tr><td>法定代表人</td><td>主管会计工作的公司负责人</td><td>会计机构负责人</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="signature-table", filename="测试2025年年度报告.md")

        self.assertFalse(data["statements"])
        self.assertFalse(any("未识别到可校验期间列" in item for item in data["warnings"]))

    def test_formal_balance_sheet_body_survives_stale_audit_heading(self):
        markdown = (
            "# 三、关键审计事项（续）\n"
            "<table><tr><td>资产</td><td>附注五</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>流动资产</td><td></td><td></td><td></td></tr>"
            "<tr><td>货币资金</td><td>1</td><td>33,751,116</td><td>43,885,348</td></tr>"
            "<tr><td>交易性金融资产</td><td>2</td><td>17,396,415</td><td>13,768,781</td></tr>"
            "<tr><td>应收账款</td><td>3</td><td>21,670,066</td><td>21,288,393</td></tr>"
            "<tr><td>存货</td><td>4</td><td>47,017,122</td><td>41,257,657</td></tr>"
            "<tr><td>合同资产</td><td>5</td><td>5,881,166</td><td>4,972,074</td></tr>"
            "<tr><td>其他流动资产</td><td>6</td><td>3,000,000</td><td>2,000,000</td></tr>"
            "<tr><td>非流动资产</td><td></td><td></td><td></td></tr>"
            "<tr><td>固定资产</td><td>7</td><td>12,000</td><td>10,000</td></tr>"
            "<tr><td>无形资产</td><td>8</td><td>9,000</td><td>8,000</td></tr>"
            "<tr><td>递延所得税资产</td><td>9</td><td>7,000</td><td>6,000</td></tr>"
            "<tr><td>资产总计</td><td></td><td>220,000,000</td><td>210,000,000</td></tr></table>\n"
            "# 合并利润表\n"
            "<table><tr><td>项目</td><td>2025年</td><td>2024年</td></tr>"
            "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
            "<tr><td>营业利润</td><td>20</td><td>18</td></tr>"
            "<tr><td>利润总额</td><td>19</td><td>17</td></tr>"
            "<tr><td>净利润</td><td>15</td><td>13</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="stale-heading", filename="测试2025年年度报告.md")
        balance = next((item for item in data["statements"] if item["statement_type"] == "balance_sheet"), None)

        self.assertIsNotNone(balance)
        self.assertEqual(balance["scope"], "consolidated")
        self.assertFalse(any("未提取到合并资产负债表" in item for item in app.build_financial_checks(data)["warnings"]))

    def test_securities_split_balance_sheet_and_truncated_minority_profit(self):
        markdown = (
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td></td><td>本集团</td><td>本集团</td><td>本公司</td><td>本公司</td></tr>"
            "<tr><td>资产</td><td>附注六</td><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>货币资金</td><td>1</td><td>26,369,103,532.49</td><td>21,189,017,904.64</td><td>21,751,783,708.51</td><td>17,237,818,769.77</td></tr>"
            "<tr><td>结算备付金</td><td>2</td><td>2,743,072,459.77</td><td>3,494,401,180.60</td><td>2,994,604,193.41</td><td>3,608,200,811.99</td></tr>"
            "<tr><td>融出资金</td><td>3</td><td>11,993,171,506.41</td><td>9,645,782,550.70</td><td>11,993,171,506.41</td><td>9,645,782,550.70</td></tr>"
            "<tr><td>交易性金融资产</td><td>4</td><td>15,077,696,844.37</td><td>14,269,077,551.92</td><td>14,000,000,000.00</td><td>13,000,000,000.00</td></tr>"
            "<tr><td>资产总计</td><td></td><td>68,889,174,616.74</td><td>59,591,009,117.50</td><td>58,864,683,091.49</td><td>51,537,500,128.94</td></tr></table>\n"
            "<table><tr><td>王某</td><td>李某</td><td>赵某</td></tr>"
            "<tr><td>法定代表人</td><td>主管会计工作负责人</td><td>会计机构负责人</td></tr></table>\n"
            "<table><tr><td>项目</td><td></td><td>本集团</td><td>本集团</td><td>本公司</td><td>本公司</td></tr>"
            "<tr><td>负债和股东权益</td><td>附注六</td><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>负债:</td><td></td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>应付短期融资款</td><td>21</td><td>2,891,931,457.55</td><td>349,066,569.00</td><td>2,891,931,457.55</td><td>349,066,569.00</td></tr>"
            "<tr><td>代理买卖证券款</td><td>25</td><td>27,109,845,707.70</td><td>19,276,176,151.13</td><td>20,213,909,964.47</td><td>14,178,380,731.53</td></tr>"
            "<tr><td>负债合计</td><td></td><td>38,654,600,000.00</td><td>31,482,598,249.23</td><td>31,000,000,000.00</td><td>25,000,000,000.00</td></tr>"
            "<tr><td>归属于母公司股东权益合计</td><td></td><td>29,424,574,616.74</td><td>27,288,410,868.27</td><td></td><td></td></tr>"
            "<tr><td>少数股东权益</td><td></td><td>810,000,000.00</td><td>820,000,868.27</td><td></td><td></td></tr>"
            "<tr><td>股东权益合计</td><td></td><td>30,234,574,616.74</td><td>28,108,410,868.27</td><td>27,864,683,091.49</td><td>26,537,500,128.94</td></tr>"
            "<tr><td>负债和股东权益总计</td><td></td><td>68,889,174,616.74</td><td>59,591,009,117.50</td><td>58,864,683,091.49</td><td>51,537,500,128.94</td></tr></table>\n"
            "# 合并利润表\n"
            "<table><tr><td>项目</td><td></td><td>本集团</td><td>本集团</td></tr>"
            "<tr><td>项目</td><td>附注六</td><td>2025年度</td><td>2024年度</td></tr>"
            "<tr><td>一、营业收入</td><td></td><td>3,454,731,967.10</td><td>3,222,018,543.33</td></tr>"
            "<tr><td>二、营业利润</td><td></td><td>1,130,000,000.00</td><td>700,000,000.00</td></tr>"
            "<tr><td>三、利润总额</td><td></td><td>1,130,000,000.00</td><td>700,000,000.00</td></tr>"
            "<tr><td>减：所得税费用</td><td></td><td>280,250,922.08</td><td>188,679,010.48</td></tr>"
            "<tr><td>五、净利润</td><td></td><td>849,749,077.92</td><td>511,320,989.52</td></tr>"
            "<tr><td>1、归属于母公司股东的净利润</td><td></td><td>769,223,948.65</td><td>428,379,469.63</td></tr>"
            "<tr><td>2、少数股东损益</td><td></td><td>8</td><td>82,941,519.89</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="securities-split", filename="证券公司2025年年度报告.md")
        checks = app.build_financial_checks(data)
        balance = next((item for item in data["statements"] if item["statement_type"] == "balance_sheet" and item["scope"] == "consolidated"), None)
        attribution = next(
            item
            for item in checks["checks"]
            if item.get("rule_id") == "is.net_profit_attribution" and item.get("period") == "2025"
        )

        self.assertIsNotNone(balance)
        self.assertIn("derived_minority_profit_loss", attribution["inputs"])
        self.assertEqual(attribution["status"], "pass")
        self.assertFalse(any("资产负债表" in item for item in checks["warnings"]))

    def test_balance_sheet_continuation_inherits_period_columns_without_note_pollution(self):
        markdown = (
            "# 合并资产负债表\n"
            "<table><tr><td>附注</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>附注</td><td>人民币百万元</td><td>人民币百万元</td></tr></table>\n"
            "# 合并资产负债表\n"
            "<table><tr><td>货币资金</td><td>5</td><td>152,318</td><td>146,799</td></tr>"
            "<tr><td>其他流动资产</td><td></td><td>38,666</td><td>33,065</td></tr>"
            "<tr><td>流动资产合计</td><td></td><td>522,741</td><td>524,515</td></tr>"
            "<tr><td>非流动资产</td><td>非流动资产</td><td>非流动资产</td><td>非流动资产</td></tr>"
            "<tr><td>长期股权投资</td><td>12</td><td>252,114</td><td>246,819</td></tr>"
            "<tr><td>非流动资产合计</td><td></td><td>1,632,876</td><td>1,560,256</td></tr>"
            "<tr><td>资产总计</td><td></td><td>2,155,617</td><td>2,084,771</td></tr></table>\n"
            "# 合并资产负债表（续）\n"
            "<table><tr><td></td><td>附注</td><td>2025年12月31日人民币百万元</td><td>2024年12月31日人民币百万元</td></tr>"
            "<tr><td>负债和股东权益</td><td></td><td></td><td></td></tr>"
            "<tr><td>负债合计</td><td></td><td>1,165,845</td><td>1,108,478</td></tr>"
            "<tr><td>股东权益合计</td><td></td><td>989,772</td><td>976,293</td></tr>"
            "<tr><td>负债和股东权益总计</td><td></td><td>2,155,617</td><td>2,084,771</td></tr></table>\n"
            "# 本集团主要合营公司的简明资产负债表及至投资账面价值的调节列示如下：\n"
            "<table><tr><td></td><td>福建联合石化</td><td>福建联合石化</td></tr>"
            "<tr><td></td><td>2025年12月31日人民币百万元</td><td>2024年12月31日人民币百万元</td></tr>"
            "<tr><td>流动资产</td><td>流动资产</td><td>流动资产</td></tr>"
            "<tr><td>流动资产合计</td><td>11,607</td><td>14,380</td></tr>"
            "<tr><td>非流动资产合计</td><td>11,129</td><td>11,873</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="sinopec-continuation", filename="中国石化2025年度报告.md")
        checks = app.build_financial_checks(data)
        balance = next(item for item in data["statements"] if item["statement_type"] == "balance_sheet" and item["scope"] == "consolidated")
        fail_rules = [item.get("rule_id") for item in checks["checks"] if item.get("status") == "fail"]

        self.assertIn(2, balance["table_indexes"])
        self.assertNotIn(4, balance["table_indexes"])
        self.assertFalse(fail_rules)

    def test_investee_financial_note_is_not_promoted_to_formal_statement(self):
        markdown = (
            "# 母公司资产负债表\n"
            "<table><tr><td>资产</td><td>附注十七</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>货币资金</td><td></td><td>100</td><td>90</td></tr>"
            "<tr><td>资产总计</td><td></td><td>200</td><td>180</td></tr></table>\n"
            "# 母公司资产负债表（续）\n"
            "<table><tr><td>负债和所有者权益</td><td>附注十七</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>负债合计</td><td></td><td>80</td><td>70</td></tr>"
            "<tr><td>应付账款</td><td></td><td>20</td><td>18</td></tr>"
            "<tr><td>合同负债</td><td></td><td>10</td><td>9</td></tr>"
            "<tr><td>其他应付款</td><td></td><td>15</td><td>14</td></tr>"
            "<tr><td>长期借款</td><td></td><td>15</td><td>14</td></tr>"
            "<tr><td>递延收益</td><td></td><td>20</td><td>15</td></tr>"
            "<tr><td>所有者权益合计</td><td></td><td>120</td><td>110</td></tr>"
            "<tr><td>负债和所有者权益总计</td><td></td><td>200</td><td>180</td></tr></table>\n"
            "# 八、在其他主体中的权益\n"
            "# 重要联营企业的主要财务信息\n"
            "下表列示了被投资单位的财务信息，这些财务信息按照权益法调节至投资账面价值：\n"
            "<table><tr><td></td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>流动资产</td><td>300</td><td>280</td></tr>"
            "<tr><td>非流动资产</td><td>200</td><td>190</td></tr>"
            "<tr><td>资产合计</td><td>500</td><td>470</td></tr>"
            "<tr><td>负债合计</td><td>260</td><td>250</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="investee-note", filename="公司2025年年度报告.md")
        parent = next(item for item in data["statements"] if item["statement_type"] == "balance_sheet" and item["scope"] == "parent_company")

        self.assertEqual(parent["table_indexes"], [1, 2])
        self.assertFalse(any(3 in item.get("table_indexes", []) for item in data["statements"]))

    def test_body_balance_sheet_scope_hint_keeps_consolidated_liability_part(self):
        markdown = (
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>资产:</td><td></td><td></td></tr>"
            "<tr><td>货币资金</td><td>50</td><td>45</td></tr>"
            "<tr><td>资产总计</td><td>100</td><td>90</td></tr></table>\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>负债:</td><td></td><td></td></tr>"
            "<tr><td>负债合计</td><td>60</td><td>55</td></tr>"
            "<tr><td>归属于母公司股东权益合计</td><td>35</td><td>30</td></tr>"
            "<tr><td>少数股东权益</td><td>5</td><td>5</td></tr>"
            "<tr><td>股东权益合计</td><td>40</td><td>35</td></tr>"
            "<tr><td>负债和股东权益总计</td><td>100</td><td>90</td></tr></table>\n"
            "# 母公司资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>资产:</td><td></td><td></td></tr>"
            "<tr><td>货币资金</td><td>80</td><td>70</td></tr>"
            "<tr><td>资产总计</td><td>100</td><td>90</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="scope-hint", filename="证券公司2025年年度报告.md")
        checks = app.build_financial_checks(data)
        consolidated = next(item for item in data["statements"] if item["statement_type"] == "balance_sheet" and item["scope"] == "consolidated")

        self.assertEqual(consolidated["table_indexes"], [1, 2])
        self.assertFalse([item for item in checks["checks"] if item.get("status") == "fail"])

    def test_parent_company_balance_sheet_skips_minority_interest_bridge(self):
        markdown = (
            "# 母公司资产负债表\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>资产总计</td><td>100</td><td>90</td></tr>"
            "<tr><td>负债合计</td><td>60</td><td>55</td></tr>"
            "<tr><td>所有者权益合计</td><td>40</td><td>35</td></tr>"
            "<tr><td>归属于母公司股东权益合计</td><td>999</td><td>999</td></tr>"
            "<tr><td>负债和所有者权益总计</td><td>100</td><td>90</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="parent-minority", filename="公司2025年年度报告.md")
        checks = app.build_financial_checks(data)
        bridge_rules = [
            item for item in checks["checks"]
            if item.get("rule_id") == "bs.parent_equity_plus_minority" and item.get("scope") == "parent_company"
        ]

        self.assertFalse(bridge_rules)

    def test_equity_change_table_allows_accumulated_losses_label(self):
        markdown = (
            "# 合并股东权益变动表\n"
            "<table><tr><td>2025年</td><td>归属于母公司股东权益</td><td>归属于母公司股东权益</td><td>归属于母公司股东权益</td><td>归属于母公司股东权益</td><td>归属于母公司股东权益</td><td>归属于母公司股东权益</td><td>少数股东权益</td><td>股东权益合计</td></tr>"
            "<tr><td></td><td>股本</td><td>资本公积</td><td>其他综合收益</td><td>盈余公积</td><td>未弥补亏损</td><td>一般风险准备</td><td></td><td></td></tr>"
            "<tr><td>一、本年年初余额</td><td>17,448,421</td><td>46,150,983</td><td>550,334</td><td>11,564,287</td><td>(30,744,120)</td><td>177,506</td><td>(4,202,202)</td><td>40,945,209</td></tr>"
            "<tr><td>二、本年增减变动金额</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>(一)综合收益总额</td><td>-</td><td>-</td><td>(788,371)</td><td>-</td><td>(1,770,393)</td><td>-</td><td>(1,645,469)</td><td>(4,204,233)</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "airchina-equity", "filename": "中国国航2025年度报告.md"},
        )
        candidates = {item["name"]: item for item in report["core_financial_table_candidates"]}

        self.assertEqual(candidates["所有者权益变动表"]["status"], "found")
        self.assertEqual(candidates["所有者权益变动表"]["confidence"], "high")

    def test_cash_flow_supplement_under_continued_heading_is_not_cash_flow_statement(self):
        markdown = (
            "# 银行现金流量表(续)\n"
            "2025年度\n"
            "# 补充资料\n"
            "<table><tr><td>净利润</td><td></td><td>41,158</td><td>42,586</td></tr>"
            "<tr><td>信用减值损失</td><td>45</td><td>40,399</td><td>48,944</td></tr>"
            "<tr><td>固定资产折旧</td><td>44</td><td>1,315</td><td>1,579</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="cash-flow-supplement", filename="测试2025年年度报告.md")

        self.assertFalse(data["statements"])
        self.assertFalse(any("未识别到可校验期间列" in item for item in data["warnings"]))

    def test_currency_exposure_note_table_is_not_balance_sheet(self):
        markdown = (
            "# 2024 年 12 月 31 日\n"
            "# 本行 2025年12月31日\n"
            "<table><tr><td></td><td>人民币</td><td>美元(折人民币)</td><td>港币(折人民币)</td><td>其他(折人民币)</td><td>合计</td></tr>"
            "<tr><td>资产</td><td></td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>现金及存放中央银行款项</td><td>327,032</td><td>12,720</td><td>907</td><td>256</td><td>340,915</td></tr>"
            "<tr><td>发放贷款及垫款</td><td>5,311,058</td><td>144,969</td><td>113,703</td><td>31,720</td><td>5,601,450</td></tr>"
            "<tr><td>信贷承诺</td><td>2,177,513</td><td>85,138</td><td>34,644</td><td>12,485</td><td>2,309,780</td></tr>"
            "<tr><td>衍生金融工具</td><td>30,130</td><td>713</td><td>26,829</td><td>(15,715)</td><td>41,957</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="currency-exposure", filename="银行2025年年度报告.md")

        self.assertFalse(data["statements"])
        self.assertFalse(any("未识别到可校验期间列" in item for item in data["warnings"]))

    def test_bank_financial_summary_aliases_are_key_candidates(self):
        markdown = (
            "# 1.4 财务概要\n"
            "# 1.4.1 经营业绩\n"
            "单位：百万元人民币\n"
            "<table><tr><td>项目</td><td>2025年</td><td>2024年</td></tr>"
            "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
            "<tr><td>营业利润</td><td>30</td><td>28</td></tr>"
            "<tr><td>利润总额</td><td>29</td><td>27</td></tr>"
            "<tr><td>归属于本行股东的净利润</td><td>20</td><td>18</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td>15</td><td>14</td></tr></table>\n"
            "# 1.4.2 盈利能力指标\n"
            "<table><tr><td>项目</td><td>2025年</td><td>2024年</td></tr>"
            "<tr><td>平均总资产回报率</td><td>0.7%</td><td>0.6%</td></tr>"
            "<tr><td>加权平均净资产收益率</td><td>8.0%</td><td>7.5%</td></tr>"
            "<tr><td>成本收入比</td><td>28%</td><td>29%</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "bank-summary", "filename": "银行2025年年度报告.md"},
        )

        candidates = {item["name"]: item for item in report["core_financial_table_candidates"]}
        self.assertEqual(candidates["主要会计数据"]["status"], "found")
        self.assertEqual(candidates["主要会计数据"]["confidence"], "high")
        self.assertEqual(candidates["主要财务指标"]["status"], "found")
        self.assertEqual(candidates["主要财务指标"]["confidence"], "high")

    def test_bank_financial_summary_aliases_feed_key_metrics(self):
        markdown = (
            "# 1.4 财务概要\n"
            "# 1.4.1 经营业绩\n"
            "单位：百万元人民币\n"
            "<table><tr><td>项目</td><td>2025年</td><td>2024年</td></tr>"
            "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
            "<tr><td>营业利润</td><td>30</td><td>28</td></tr>"
            "<tr><td>利润总额</td><td>29</td><td>27</td></tr>"
            "<tr><td>归属于本行股东的净利润</td><td>20</td><td>18</td></tr>"
            "<tr><td>归属于本行股东扣除非经常性损益的净利润</td><td>19</td><td>17</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td>15</td><td>14</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="bank-key-metrics", filename="银行2025年年度报告.md")
        canonical_names = {item["canonical_name"] for item in data["key_metrics"]}

        self.assertIn("operating_revenue", canonical_names)
        self.assertIn("parent_net_profit", canonical_names)
        self.assertIn("deducted_parent_net_profit", canonical_names)
        self.assertIn("operating_cash_flow_net", canonical_names)

    def test_bank_scale_indicators_feed_key_metrics(self):
        markdown = (
            "# 1.4 财务概要\n"
            "# 1.4.3 规模指标\n"
            "单位：百万元人民币\n"
            "<table><tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>"
            "<tr><td>总资产</td><td>1000</td><td>900</td></tr>"
            "<tr><td>贷款及垫款总额</td><td>600</td><td>560</td></tr>"
            "<tr><td>总负债</td><td>800</td><td>720</td></tr>"
            "<tr><td>客户存款总额</td><td>700</td><td>650</td></tr>"
            "<tr><td>归属于本行股东的权益总额</td><td>180</td><td>160</td></tr></table>\n"
        )

        data = app.build_financial_data(markdown, task_id="bank-scale", filename="银行2025年年度报告.md")
        report = app._build_quality_report(markdown, {"task_id": "bank-scale", "filename": "银行2025年年度报告.md"})
        canonical_names = {item["canonical_name"] for item in data["key_metrics"]}
        candidates = {item["name"]: item for item in report["core_financial_table_candidates"]}

        self.assertIn("total_assets", canonical_names)
        self.assertIn("total_liabilities", canonical_names)
        self.assertIn("equity_attributable_parent", canonical_names)
        self.assertEqual(candidates["主要会计数据"]["status"], "found")
        self.assertEqual(candidates["主要会计数据"]["confidence"], "high")

    def test_nonrecurring_gain_loss_detects_unit_heading_table(self):
        markdown = (
            "# 1.6 非经常性损益项目和金额\n"
            "单位：人民币百万元\n"
            "<table><tr><td>项目</td><td>2025年</td></tr>"
            "<tr><td>非流动性资产处置损益</td><td>66</td></tr>"
            "<tr><td>政府补助</td><td>597</td></tr>"
            "<tr><td>其他营业外收支净额</td><td>-17</td></tr>"
            "<tr><td>所得税影响</td><td>-226</td></tr>"
            "<tr><td>合计</td><td>420</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "nonrecurring", "filename": "银行2025年年度报告.md"},
        )

        candidates = {item["name"]: item for item in report["core_financial_table_candidates"]}
        self.assertEqual(candidates["非经常性损益"]["status"], "found")
        self.assertEqual(candidates["非经常性损益"]["confidence"], "high")

    def test_shareholders_equity_change_alias_maps_to_owner_equity_candidate(self):
        markdown = (
            "# 合并股东权益变动表\n"
            "2025 年度\n"
            "单位：百万元\n"
            "<table><tr><td rowspan=\"2\">项目</td><td colspan=\"4\">归属于母公司股东权益</td><td rowspan=\"2\">少数股东权益</td><td rowspan=\"2\">股东权益合计</td></tr>"
            "<tr><td>股本</td><td>资本公积</td><td>其他综合收益</td><td>未分配利润</td></tr>"
            "<tr><td>2025年1月1日年初余额</td><td>100</td><td>20</td><td>3</td><td>300</td><td>5</td><td>428</td></tr>"
            "<tr><td>本年增减变动额</td><td>10</td><td>2</td><td>1</td><td>30</td><td>1</td><td>44</td></tr>"
            "<tr><td>综合收益总额</td><td></td><td></td><td>1</td><td>30</td><td>1</td><td>32</td></tr>"
            "<tr><td>2025年12月31日年末余额</td><td>110</td><td>22</td><td>4</td><td>330</td><td>6</td><td>472</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "shareholder-equity", "filename": "公司2025年年度报告.md"},
        )

        candidates = {item["name"]: item for item in report["core_financial_table_candidates"]}
        self.assertEqual(candidates["所有者权益变动表"]["status"], "found")
        self.assertEqual(candidates["所有者权益变动表"]["confidence"], "high")

    def test_quarterly_report_filename_sets_kind_and_year(self):
        markdown = (
            "# 上海浦东发展银行股份有限公司\n"
            "# 主要会计数据和财务指标\n"
            "<table><tr><td>项目</td><td>2026年3月31日</td><td>2025年12月31日</td></tr>"
            "<tr><td>总资产</td><td>100</td><td>90</td></tr></table>\n"
            "2010 年历史沿革说明\n"
        )

        data = app.build_financial_data(markdown, task_id="quarterly", filename="浦发银行2026年一季报.md")

        self.assertEqual(data["report_kind"], "quarterly_report")
        self.assertEqual(data["report_year"], 2026)

    def test_quarterly_quality_report_does_not_require_equity_change_statement(self):
        markdown = (
            "# 上海浦东发展银行股份有限公司\n"
            "# 2026 年第一季度报告\n"
            "# 主要会计数据和财务指标\n"
            "<table><tr><td>项目</td><td>2026年1-3月</td><td>2025年1-3月</td></tr>"
            "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
            "<tr><td>归属于母公司股东的净利润</td><td>10</td><td>9</td></tr></table>\n"
            "# 合并资产负债表\n"
            "<table><tr><td>项目</td><td>2026年3月31日</td><td>2025年12月31日</td></tr>"
            "<tr><td>资产总计</td><td>100</td><td>90</td></tr>"
            "<tr><td>负债合计</td><td>60</td><td>55</td></tr>"
            "<tr><td>所有者权益合计</td><td>40</td><td>35</td></tr></table>\n"
            "# 合并利润表\n"
            "<table><tr><td>项目</td><td>2026年1-3月</td><td>2025年1-3月</td></tr>"
            "<tr><td>营业收入</td><td>100</td><td>90</td></tr>"
            "<tr><td>利润总额</td><td>12</td><td>10</td></tr>"
            "<tr><td>净利润</td><td>10</td><td>9</td></tr></table>\n"
            "# 合并现金流量表\n"
            "<table><tr><td>项目</td><td>2026年1-3月</td><td>2025年1-3月</td></tr>"
            "<tr><td>经营活动现金流入小计</td><td>120</td><td>110</td></tr>"
            "<tr><td>经营活动现金流出小计</td><td>80</td><td>75</td></tr>"
            "<tr><td>经营活动产生的现金流量净额</td><td>40</td><td>35</td></tr>"
            "<tr><td>投资活动现金流入小计</td><td>10</td><td>9</td></tr>"
            "<tr><td>投资活动现金流出小计</td><td>5</td><td>5</td></tr>"
            "<tr><td>投资活动产生的现金流量净额</td><td>5</td><td>4</td></tr>"
            "<tr><td>筹资活动现金流入小计</td><td>3</td><td>2</td></tr>"
            "<tr><td>筹资活动现金流出小计</td><td>1</td><td>1</td></tr>"
            "<tr><td>筹资活动产生的现金流量净额</td><td>2</td><td>1</td></tr>"
            "<tr><td>现金及现金等价物净增加额</td><td>47</td><td>40</td></tr></table>\n"
        )

        report = app._build_quality_report(
            markdown,
            {"task_id": "quarterly-quality", "filename": "浦发银行2026年一季报.md"},
        )

        names = [item["name"] for item in report["core_financial_table_candidates"]]
        self.assertNotIn("所有者权益变动表", names)
        self.assertIn("资产负债表", names)
        self.assertIn("利润表", names)
        self.assertIn("现金流量表", names)


if __name__ == "__main__":
    unittest.main()
