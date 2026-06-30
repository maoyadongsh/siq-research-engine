import pdf_parser_document_full_service as document_full


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
