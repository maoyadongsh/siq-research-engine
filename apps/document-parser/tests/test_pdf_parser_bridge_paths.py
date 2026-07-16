from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from contracts import ParseConfig, SourceFile
from mineru_import import copy_mineru_images_to_result, parse_mineru_output_dir
from providers import simple


def _ready_result(root: Path, task_id: str) -> Path:
    result_dir = root / task_id
    result_dir.mkdir(parents=True)
    (result_dir / "document_full.json").write_text("{}", encoding="utf-8")
    (result_dir / "result.md").write_text("# Ready\n", encoding="utf-8")
    return result_dir.resolve()


def _configure_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    artifacts_root = tmp_path / "artifacts"
    data_dir = tmp_path / "runtime" / "pdf-parser"
    monkeypatch.setenv("SIQ_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("SIQ_ARTIFACTS_ROOT", str(artifacts_root))
    monkeypatch.setenv("SIQ_PDF2MD_DATA_DIR", str(data_dir))
    monkeypatch.delenv("SIQ_PDF_RESULTS_ROOT", raising=False)
    monkeypatch.delenv("RESULTS_FOLDER", raising=False)
    return artifacts_root / "pdf-parser" / "results", data_dir / "results"


def test_pdf_bridge_prefers_ready_artifact_root_and_keeps_legacy_candidate(monkeypatch, tmp_path):
    artifact_results, legacy_results = _configure_roots(monkeypatch, tmp_path)
    task_id = "doc-path-migration"
    legacy_results.mkdir(parents=True)
    ready = _ready_result(artifact_results, task_id)

    assert simple._pdf_parser_result_dir(task_id) == ready
    assert simple._pdf_parser_results_roots()[:2] == (
        artifact_results.resolve(),
        legacy_results.resolve(),
    )


def test_pdf_bridge_does_not_cross_fallback_from_partial_canonical_task(monkeypatch, tmp_path):
    artifact_results, legacy_results = _configure_roots(monkeypatch, tmp_path)
    task_id = "doc-partial-canonical"
    canonical = artifact_results / task_id
    canonical.mkdir(parents=True)
    (canonical / "document_full.json").write_text("{}", encoding="utf-8")
    _ready_result(legacy_results, task_id)

    assert simple._pdf_parser_result_dir(task_id) == canonical.resolve()
    assert simple._result_dir_looks_ready(canonical) is False


def test_pdf_bridge_accepts_only_allowlisted_result_payload_paths(monkeypatch, tmp_path):
    artifact_results, _legacy_results = _configure_roots(monkeypatch, tmp_path)
    task_id = "doc-result-payload"
    ready = _ready_result(artifact_results, task_id)
    payload = {
        "artifacts": {
            "document_full.json": {
                "exists": True,
                "path": str(ready / "document_full.json"),
            },
            "result.md": {"exists": True, "path": str(ready / "result.md")},
        }
    }

    assert simple._pdf_parser_result_dir_from_payload(task_id, payload) == ready

    outside = _ready_result(tmp_path / "outside", task_id)
    poisoned = {
        "artifacts": {
            "document_full.json": {
                "exists": True,
                "path": str(outside / "document_full.json"),
            },
            "result.md": {"exists": True, "path": str(outside / "result.md")},
        }
    }
    with pytest.raises(RuntimeError, match="outside allowlisted roots"):
        simple._pdf_parser_result_dir_from_payload(task_id, poisoned)


def test_pdf_bridge_rejects_task_path_escape(monkeypatch, tmp_path):
    _configure_roots(monkeypatch, tmp_path)
    root = simple._pdf_parser_results_root()

    for task_id in ("../escape", "nested/task", r"nested\task", "/absolute", ".", ".."):
        assert simple._pdf_parser_task_dir(root, task_id) is None

    outside = tmp_path / "outside-task"
    outside.mkdir()
    root.mkdir(parents=True)
    (root / "doc-link").symlink_to(outside, target_is_directory=True)
    assert simple._pdf_parser_task_dir(root, "doc-link") is None


def test_pdf_bridge_rejects_conflicting_artifact_manifest(monkeypatch, tmp_path):
    artifact_results, _legacy_results = _configure_roots(monkeypatch, tmp_path)
    task_id = "doc-manifest-identity"
    ready = _ready_result(artifact_results, task_id)
    (ready / "artifact_manifest.json").write_text(
        '{"task_id":"doc-other"}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="task identity mismatch"):
        simple._pdf_parser_result_dir(task_id)


def test_forced_api_transport_uses_task_private_staging_and_ignores_foreign_paths(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT", "api")
    upload_dir = tmp_path / "uploads" / "document-task"
    upload_dir.mkdir(parents=True)
    pdf_path = upload_dir / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    source = SourceFile(
        path=pdf_path,
        filename="source.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="sha",
    )
    staged_dir = upload_dir / ".pdf-parser-staging" / "doc-api-transport"
    seen = {}

    def fake_stage(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(result_dir=staged_dir)

    monkeypatch.setattr(simple, "stage_pdf_parser_artifacts", fake_stage)
    result = simple._materialize_pdf_parser_result(
        "document-task",
        "doc-api-transport",
        source,
        result_payload={
            "artifacts": {
                "document_full.json": {
                    "exists": True,
                    "path": "/foreign/container/document_full.json",
                    "url": "/api/artifact/doc-api-transport/document_full.json",
                }
            }
        },
    )

    assert result == staged_dir
    assert seen["staging_root"] == upload_dir / ".pdf-parser-staging"
    assert seen["task_id"] == "doc-api-transport"


def test_mineru_import_drops_absolute_traversal_and_symlink_image_paths(tmp_path):
    source_dir = tmp_path / "mineru"
    images_dir = source_dir / "images"
    images_dir.mkdir(parents=True)
    (source_dir / "result.md").write_text("# Imported\n", encoding="utf-8")
    (images_dir / "safe.png").write_bytes(b"safe")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    (images_dir / "linked.png").symlink_to(outside)
    (source_dir / "metadata.json").write_text(
        json.dumps(
            {
                "task_id": "doc-import-paths",
                "filename": "issuer.pdf",
                "source_files": {"pdf": {"path": str(outside)}},
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "content_list.json").write_text(
        json.dumps(
            [
                {"type": "image", "img_path": str(outside)},
                {"type": "image", "img_path": "images/../../outside.png"},
                {"type": "image", "img_path": "images/linked.png"},
                {"type": "image", "img_path": "images/safe.png"},
                {"type": "table", "source_image_path": str(outside)},
            ]
        ),
        encoding="utf-8",
    )

    source, output = parse_mineru_output_dir("doc-import-paths", source_dir, ParseConfig())

    assert source.path == source_dir / "result.md"
    serialized = json.dumps(
        {"blocks": output.blocks, "figures": output.figures, "tables": output.tables},
        ensure_ascii=False,
    )
    assert str(outside) not in serialized
    assert "../" not in serialized
    assert [figure["image_path"] for figure in output.figures] == ["", "images/safe.png"]
    assert output.tables[0]["source_image_path"] == ""

    copied = tmp_path / "copied"
    copy_mineru_images_to_result(source_dir, copied)
    assert (copied / "images" / "original" / "safe.png").read_bytes() == b"safe"
    assert not (copied / "images" / "original" / "linked.png").exists()
