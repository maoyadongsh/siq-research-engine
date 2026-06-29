import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


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
            headers={},
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


def _first_fragment_html():
    return (
        "<table><tr><td>项目</td><td>2025年度</td><td>其他权益工具</td></tr>"
        "<tr><td>一、上年年末余额</td><td>1,216,123,535.00</td><td>-</td></tr>"
        "<tr><td>二、本年期初余额</td><td>1,216,123,535.00</td><td>-</td></tr>"
        "<tr><td>三、本期增减变动金额</td><td>-6,363,646.00</td><td>-</td></tr>"
        "</table>"
    )


class PdfTableRelationsTests(unittest.TestCase):
    def test_missing_body_target_table_generates_formal_relation_artifact(self):
        old_results_folder = app.RESULTS_FOLDER
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.RESULTS_FOLDER = tmpdir
                task = {
                    "task_id": "relation-task",
                    "filename": "豪威集成电路2025年年度报告.pdf",
                    "upload_path": "",
                }
                markdown = f"[PDF_PAGE: 134]\n# 合并所有者权益变动表\n{_first_fragment_html()}\n"
                content_list = [
                    {"type": "header", "page_idx": 133, "bbox": [111, 68, 225, 92], "text": "OMNIVISION"},
                    {"type": "table", "page_idx": 133, "bbox": [26, 188, 970, 789], "table_body": _first_fragment_html()},
                    {"type": "page_number", "page_idx": 133, "bbox": [478, 899, 520, 915], "text": "134 / 280"},
                    {"type": "header", "page_idx": 134, "bbox": [111, 68, 225, 92], "text": "OMNIVISION"},
                    {"type": "header", "page_idx": 134, "bbox": [640, 70, 891, 90], "text": "豪威集成电路（集团）股份有限公司2025年年度报告"},
                    {"type": "table", "page_idx": 134, "bbox": [26, 117, 970, 715]},
                    {"type": "page_number", "page_idx": 134, "bbox": [478, 899, 520, 915], "text": "135 / 280"},
                ]
                enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)

                relations = app._write_table_relations_artifact(task, markdown, enhanced=enhanced, content_list=content_list)
                relation_path = os.path.join(tmpdir, "relation-task", "table_relations.json")

                self.assertTrue(os.path.exists(relation_path))
                self.assertEqual(relations["schema_version"], "document_table_relations_v1")
                self.assertEqual(len(relations["relations"]), 1)
                relation = relations["relations"][0]
                self.assertEqual(relation["page_numbers"], [134, 135])
                self.assertEqual(relation["relation_type"], "continuation")
                self.assertEqual(relation["from_bbox"], [26.0, 188.0, 970.0, 789.0])
                self.assertEqual(relation["to_bbox"], [26.0, 117.0, 970.0, 715.0])

                app._write_document_full_artifact(task, markdown, enhanced, {"warnings": []}, table_relations=relations)
                with open(os.path.join(tmpdir, "relation-task", "document_full.json"), "r", encoding="utf-8") as fh:
                    document_full = json.load(fh)
                self.assertEqual(document_full["table_relations"]["relations"][0]["page_numbers"], [134, 135])
                self.assertTrue(document_full["artifacts"]["table_relations.json"]["exists"])
                self.assertTrue(app._artifact_status(task)["table_relations.json"]["exists"])
        finally:
            app.RESULTS_FOLDER = old_results_folder

    def test_real_title_before_target_table_blocks_relation(self):
        markdown = f"[PDF_PAGE: 134]\n{_first_fragment_html()}\n"
        content_list = [
            {"type": "table", "page_idx": 133, "bbox": [26, 188, 970, 789], "table_body": _first_fragment_html()},
            {"type": "text", "sub_type": "1", "page_idx": 134, "bbox": [120, 96, 740, 120], "text": "一、公司信息"},
            {"type": "table", "page_idx": 134, "bbox": [26, 150, 970, 715]},
        ]
        enhanced = app._build_content_list_enhanced(markdown, content_list=content_list, report_year=2025)

        relations = app._build_table_relations_artifact(
            {"task_id": "blocked-relation", "filename": "阻断测试.pdf"},
            markdown,
            enhanced=enhanced,
            content_list=content_list,
        )

        self.assertEqual(relations["relations"], [])


if __name__ == "__main__":
    unittest.main()
