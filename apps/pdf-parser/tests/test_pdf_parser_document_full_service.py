import pdf_parser_document_full_service as document_full


def test_relation_tables_from_artifacts_merges_enhanced_and_content_list_tables():
    enhanced = {
        "tables": [
            {
                "table_index": 1,
                "bbox": [1, 2, 3, 4],
                "pdf_page_number": 2,
                "printed_page_number": "1",
                "preview": "营业收入",
                "source": "markdown_marker_inferred",
                "structure": {"expanded_rows": 3, "expanded_columns": 2},
            }
        ]
    }
    content_list = [
        {
            "type": "table",
            "table_body": "<table><tr><td>营业收入</td></tr></table>",
            "page_idx": 1,
            "bbox": [1, 2, 3, 4],
            "table_caption": ["营业收入表"],
            "table_footnote": ["单位：万元"],
        }
    ]

    relation_tables = document_full.relation_tables_from_artifacts(enhanced, content_list)

    assert relation_tables == [
        {
            "table_id": "pt-000001",
            "table_index": 1,
            "content_table_source_id": 1,
            "page_number": 2,
            "pdf_page_number": 2,
            "printed_page_number": "1",
            "bbox": [1.0, 2.0, 3.0, 4.0],
            "title": "营业收入",
            "caption": "营业收入",
            "html": "<table><tr><td>营业收入</td></tr></table>",
            "markdown": "",
            "text": "营业收入",
            "quality": {"row_count": 3, "column_count": 2},
            "missing_body": False,
            "source": "markdown_marker_inferred",
        }
    ]


def test_relation_blocks_and_payload_helpers_normalize_table_relations():
    content_list = [
        {"type": "text", "page_idx": 0, "text": "标题"},
        {"type": "table", "page_idx": 1, "table_body": "<table><tr><td>表</td></tr></table>", "bbox": [1, 2, 3, 4]},
        {"type": "list", "page_idx": 2, "list_items": ["A", "B"]},
    ]
    relation_blocks = document_full.relation_blocks_from_content_list(content_list)

    assert relation_blocks[1]["text"] == "表"
    assert relation_blocks[2]["text"] == "A B"

    payload = document_full.build_table_relations_artifact_payload(
        {"task_id": "doc-full", "filename": "sample.pdf"},
        "[PDF_PAGE: 1]\n正文",
        enhanced={"tables": []},
        content_list=content_list,
        build_table_relations=lambda task_id, relation_tables, blocks, markdown: {
            "task_id": task_id,
            "relations": [
                {
                    "from_table_id": relation_tables[0]["table_id"],
                    "to_table_id": relation_tables[0]["table_id"],
                }
            ],
            "relation_tables": relation_tables,
            "blocks": blocks,
            "markdown": markdown,
        },
        now_iso=lambda: "2026-06-30T00:00:00+00:00",
        table_relation_ruleset_version="v1",
    )

    assert payload["schema_version"] == "document_table_relations_v1"
    assert payload["ruleset_version"] == "v1"
    assert payload["task_id"] == "doc-full"
    assert payload["generated_at"] == "2026-06-30T00:00:00+00:00"
    assert payload["physical_table_count"] == 1
    assert payload["relations"][0]["from_table_index"] is None


def test_augment_table_relations_supports_source_target_aliases_and_non_list_relations():
    relation_tables = [
        {"table_id": "source-1", "table_index": 7, "bbox": [1, 2, 3, 4], "page_number": 3},
        {"table_id": "target-1", "table_index": 8, "bbox": [5, 6, 7, 8], "page_number": 4},
    ]
    payload = {
        "relations": [
            {
                "source_table_id": "source-1",
                "target_table_id": "target-1",
            }
        ]
    }

    augmented = document_full.augment_table_relations(payload, relation_tables)

    assert augmented is payload
    assert augmented["relations"][0]["from_table_index"] == 7
    assert augmented["relations"][0]["from_bbox"] == [1, 2, 3, 4]
    assert augmented["relations"][0]["from_page_number"] == 3
    assert augmented["relations"][0]["to_table_index"] == 8
    assert augmented["relations"][0]["to_bbox"] == [5, 6, 7, 8]
    assert augmented["relations"][0]["to_page_number"] == 4
    assert document_full.augment_table_relations({"relations": "invalid"}, relation_tables) == {"relations": "invalid"}


def test_relation_tables_from_artifacts_filters_invalid_tables_and_keeps_content_list_only_table():
    enhanced = {
        "tables": [
            {"table_index": 1, "bbox": [1, 2, 3], "pdf_page_number": 1, "preview": "invalid bbox"},
            {"table_index": 2, "bbox": [1, 2, 3, 4], "pdf_page_number": 0, "preview": "invalid page"},
        ]
    }
    content_list = [
        {"type": "table", "table_body": "<table><tr><td>skip</td></tr></table>", "page_idx": "1", "bbox": [1, 2, 3, 4]},
        {"type": "table", "table_body": "<table><tr><td>keep</td></tr></table>", "page_idx": 0, "bbox": [10, 20, 30, 40]},
    ]

    relation_tables = document_full.relation_tables_from_artifacts(enhanced, content_list)

    assert len(relation_tables) == 1
    assert relation_tables[0]["table_id"] == "pt-p0001-10-20-30-40"
    assert relation_tables[0]["content_table_source_id"] == 2
    assert relation_tables[0]["source"] == "content_list_table_block"
    assert relation_tables[0]["text"] == "keep"


def test_resource_indexes_include_images_and_rendered_pages(tmp_path):
    task = {"task_id": "doc-full"}
    result_root = tmp_path / "doc-full"
    images = result_root / "images"
    pages = result_root / "pdf_pages"
    images.mkdir(parents=True)
    pages.mkdir()
    (images / "figure_2.webp").write_bytes(b"img2")
    (images / "figure_1.png").write_bytes(b"img1")
    (images / "notes.txt").write_text("skip", encoding="utf-8")
    (pages / "page_2.png").write_bytes(b"page2")
    (pages / "page_x.png").write_bytes(b"pagex")

    def result_dir(_task):
        return str(result_root)

    image_index = document_full.image_resource_index(task, result_dir)
    page_index = document_full.pdf_page_resource_index(task, result_dir)

    assert [item["name"] for item in image_index["items"]] == ["figure_1.png", "figure_2.webp"]
    assert image_index["summary"] == {"count": 2, "total_size_bytes": 8}
    assert image_index["items"][0]["url"] == "/api/artifact/doc-full/images/figure_1.png"
    assert [item["name"] for item in page_index["items"]] == ["page_2.png", "page_x.png"]
    assert page_index["items"][0]["page_number"] == 2
    assert page_index["items"][0]["url"] == "/api/pdf_page/doc-full/2"
    assert page_index["items"][1]["page_number"] is None
    assert page_index["items"][1]["url"] == ""


def test_file_reference_payload_handles_missing_directory_and_file(tmp_path):
    missing = document_full.file_reference_payload(str(tmp_path / "missing.pdf"), "/download/missing.pdf", kind="pdf")
    directory = tmp_path / "pages"
    directory.mkdir()
    existing_file = tmp_path / "result.md"
    existing_file.write_text("hello", encoding="utf-8")

    directory_payload = document_full.file_reference_payload(str(directory), "/api/pages", kind="directory")
    file_payload = document_full.file_reference_payload(str(existing_file), "/api/result.md", kind="markdown")

    assert missing == {"path": "", "exists": False, "url": "/download/missing.pdf", "kind": "pdf"}
    assert directory_payload == {
        "path": str(directory),
        "exists": True,
        "url": "/api/pages",
        "kind": "directory",
    }
    assert file_payload["path"] == str(existing_file)
    assert file_payload["exists"] is True
    assert file_payload["url"] == "/api/result.md"
    assert file_payload["kind"] == "markdown"
    assert file_payload["size_bytes"] == 5
    assert file_payload["mtime"]


def test_build_document_full_json_uses_injected_artifact_readers(tmp_path):
    task = {
        "task_id": "doc-full",
        "filename": "sample.pdf",
        "upload_path": str(tmp_path / "sample.pdf"),
        "markdown_path": str(tmp_path / "doc-full" / "result.md"),
        "status": "completed",
        "submit_config": {"backend": "mineru"},
    }
    result_root = tmp_path / "doc-full"
    result_root.mkdir()
    (tmp_path / "sample.pdf").write_bytes(b"pdf")
    (result_root / "result.md").write_text("[PDF_PAGE: 1]\n正文", encoding="utf-8")

    artifacts = {
        "content_list.json": [{"type": "text"}],
        "middle.json": {"pages": 1},
        "model_output.json": {"ok": True},
        "result_payload_summary.json": {"status": "ready"},
    }

    def result_dir(_task):
        return str(result_root)

    def load_json_artifact(_task, name):
        return artifacts.get(name)

    payload = document_full.build_document_full_json(
        task,
        "[PDF_PAGE: 1]\n正文",
        {"schema_version": 10},
        {"warnings": []},
        financial_data={"schema_version": "financial_data_v1"},
        financial_checks={"overall_status": "pass"},
        table_relations={"relations": []},
        result_dir=result_dir,
        load_json_artifact=load_json_artifact,
        artifact_status=lambda _task: {"result.md": {"exists": True}},
        markdown_page_index=lambda markdown, content_list=None: [{"page_number": 1, "content_count": len(content_list or [])}],
        now_iso=lambda: "2026-06-30T00:00:00+00:00",
        document_full_schema_version=3,
    )

    assert payload["schema_version"] == 3
    assert payload["generated_at"] == "2026-06-30T00:00:00+00:00"
    assert payload["task"]["task_id"] == "doc-full"
    assert payload["source_files"]["pdf"]["exists"] is True
    assert payload["source_files"]["markdown"]["url"] == "/api/artifact/doc-full/result.md"
    assert payload["markdown"]["pages"] == [{"page_number": 1, "content_count": 1}]
    assert payload["content_list"] == [{"type": "text"}]
    assert payload["middle_json"] == {"pages": 1}
    assert payload["financial_data"] == {"schema_version": "financial_data_v1"}
    assert payload["financial_checks"] == {"overall_status": "pass"}
    assert payload["table_relations"] == {"relations": []}
    assert payload["artifacts"] == {"result.md": {"exists": True}}
    assert payload["resources"]["images"]["summary"]["count"] == 0
    assert payload["resources"]["pdf_pages"]["directory"]["exists"] is False
    assert payload["resources"]["pdf_pages"]["directory"]["kind"] == "directory"


def test_build_document_full_json_marks_missing_source_files_without_embedding_resources(tmp_path):
    task = {"task_id": "doc-full", "filename": "sample.pdf", "status": "processing"}
    result_root = tmp_path / "doc-full"
    result_root.mkdir()

    payload = document_full.build_document_full_json(
        task,
        "",
        {},
        {},
        result_dir=lambda _task: str(result_root),
        load_json_artifact=lambda _task, _name: None,
        artifact_status=lambda _task: {},
        markdown_page_index=lambda markdown, content_list=None: [],
        now_iso=lambda: "2026-07-01T00:00:00+00:00",
        document_full_schema_version=3,
    )

    assert payload["source_files"]["pdf"] is None
    assert payload["source_files"]["markdown"]["exists"] is False
    assert payload["source_files"]["markdown"]["path"] == ""
    assert payload["source_files"]["complete_markdown"]["exists"] is False
    assert payload["resources"]["images"]["items"] == []
    assert payload["resources"]["pdf_pages"]["items"] == []
    assert payload["markdown"]["content"] == ""
    assert payload["content_list"] is None


def test_apply_content_list_enhanced_update_preserves_existing_metadata():
    original = {
        "artifacts": {
            "result.md": {"exists": True, "path": "/tmp/result.md", "url": "/api/artifact/doc-full/result.md"},
        },
        "source_files": {
            "pdf": {"exists": True, "path": "/tmp/sample.pdf", "kind": "pdf"},
            "complete_markdown": {"exists": False, "path": "", "url": ""},
        },
        "content_list_enhanced": {"old": True},
        "table_relations": {"old": True},
    }
    enhanced = {"tables": [{"table_index": 1}]}
    table_relations = {"relations": [{"from_table_id": "t1"}]}

    updated = document_full.apply_content_list_enhanced_update_to_document_full(
        original,
        task_id="doc-full",
        enhanced=enhanced,
        table_relations=table_relations,
        content_list_enhanced_path="/tmp/content_list_enhanced.json",
        table_relations_path="/tmp/table_relations.json",
        complete_markdown_path="/tmp/result_complete.md",
        complete_markdown_exists=True,
    )

    assert updated is not original
    assert updated["artifacts"]["result.md"] == original["artifacts"]["result.md"]
    assert updated["artifacts"]["content_list_enhanced.json"] == {
        "exists": True,
        "path": "/tmp/content_list_enhanced.json",
        "url": "/api/artifact/doc-full/content_list_enhanced.json",
    }
    assert updated["artifacts"]["table_relations.json"] == {
        "exists": True,
        "path": "/tmp/table_relations.json",
        "url": "/api/artifact/doc-full/table_relations.json",
    }
    assert updated["source_files"]["pdf"] == original["source_files"]["pdf"]
    assert updated["source_files"]["complete_markdown"] == {
        "exists": True,
        "path": "/tmp/result_complete.md",
        "url": "/api/artifact/doc-full/result_complete.md",
    }
    assert updated["content_list_enhanced"] is enhanced
    assert updated["table_relations"] is table_relations
    assert original["content_list_enhanced"] == {"old": True}
    assert original["table_relations"] == {"old": True}


def test_apply_content_list_enhanced_update_initializes_missing_metadata():
    updated = document_full.apply_content_list_enhanced_update_to_document_full(
        {},
        task_id="doc-full",
        enhanced={"tables": []},
        table_relations={"relations": []},
        content_list_enhanced_path="/tmp/content_list_enhanced.json",
        table_relations_path="/tmp/table_relations.json",
        complete_markdown_path="/tmp/result_complete.md",
        complete_markdown_exists=False,
    )

    assert sorted(updated["artifacts"]) == ["content_list_enhanced.json", "table_relations.json"]
    assert updated["source_files"]["complete_markdown"] == {
        "exists": False,
        "path": "/tmp/result_complete.md",
        "url": "/api/artifact/doc-full/result_complete.md",
    }
    assert updated["content_list_enhanced"] == {"tables": []}
    assert updated["table_relations"] == {"relations": []}
