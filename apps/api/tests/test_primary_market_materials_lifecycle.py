import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services import deal_store, primary_market_materials as materials

DEAL_ID = "DEAL-PMM-LIFECYCLE-001"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _deal(tmp_path: Path) -> Path:
    wiki_root = tmp_path / "wiki"
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Lifecycle Issuer",
        wiki_root=wiki_root,
    )
    return wiki_root


def _upload(wiki_root: Path, content: bytes = b"%PDF-1.7\nprospectus") -> dict:
    return materials.create_prospectus_document(
        deal_id=DEAL_ID,
        filename="issuer.pdf",
        content_type="application/pdf",
        stream=io.BytesIO(content),
        exchange="SSE",
        board="star",
        filing_stage="registration_draft",
        document_date="2026-07-01",
        created_by={"id": 7, "username": "owner"},
        wiki_root=wiki_root,
    )


def _parser_result(root: Path, task_id: str) -> Path:
    result = root / task_id
    result.mkdir(parents=True)
    markdown = "\n".join(
        [
            "# 重大事项提示",
            "# 风险因素",
            "# 发行人基本情况与股权结构",
            "# 业务与技术",
            "# 行业与竞争格局及市场地位",
            "# 公司治理、独立性与关联交易",
            "# 财务会计信息与管理层分析",
            "# 募集资金运用",
            "# 投资者保护、重要合同与诉讼",
        ]
    ) + "\n" + "招股说明书正文。" * 180
    (result / "result.md").write_text(markdown, encoding="utf-8")
    _write_json(result / "content_list.json", [{"id": "b1", "page": 1, "text": "风险因素"}])
    _write_json(result / "financial_checks.json", {"overall_status": "pass"})
    _write_json(result / "financial_data.json", {"statements": [{"period": "2025"}]})
    artifact_names = ("result.md", "content_list.json", "financial_checks.json", "financial_data.json")
    artifacts = {}
    for name in artifact_names:
        path = result / name
        artifacts[name] = {
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    _write_json(
        result / "artifact_manifest.json",
        {"schema_version": "pdf_parser_artifact_manifest_v1", "task_id": task_id, "artifacts": artifacts},
    )
    return result


def test_upload_validates_pdf_and_reuses_identical_active_material(tmp_path: Path):
    wiki_root = _deal(tmp_path)

    first = _upload(wiki_root)
    second = _upload(wiki_root)

    assert first["reused"] is False
    assert second["reused"] is True
    assert second["document"]["document_id"] == first["document"]["document_id"]
    raw = materials.deal_raw_pdf_path(DEAL_ID, first["document"]["document_id"], wiki_root=wiki_root)
    assert raw.read_bytes().startswith(b"%PDF-")
    assert len(materials.list_primary_market_materials(DEAL_ID, wiki_root=wiki_root)) == 1

    with pytest.raises(ValueError, match="invalid_pdf"):
        materials.create_prospectus_document(
            deal_id=DEAL_ID,
            filename="fake.pdf",
            content_type="application/pdf",
            stream=io.BytesIO(b"not pdf"),
            wiki_root=wiki_root,
        )
    with pytest.raises(ValueError, match="prospectus_too_large"):
        materials.create_prospectus_document(
            deal_id=DEAL_ID,
            filename="large.pdf",
            content_type="application/pdf",
            stream=io.BytesIO(b"%PDF-" + b"x" * 20),
            max_bytes=10,
            wiki_root=wiki_root,
        )


def test_parse_run_promotion_is_immutable_and_activates_source(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 7},
        wiki_root=wiki_root,
    )
    task_id = "pmm-parser-task-1"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    _parser_result(parser_root, task_id)

    promoted = materials.promote_parse_run_artifacts(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        promoted_by={"id": 7},
        wiki_root=wiki_root,
        results_root=parser_root,
    )

    assert promoted["status"] == "promoted"
    assert promoted["quality"]["status"] == "ready"
    assert promoted["analysis_source"]["status"] == "ready"
    current = json.loads(
        materials.deal_current_parse_run_path(
            DEAL_ID, document["document_id"], wiki_root=wiki_root
        ).read_text()
    )
    assert current["parse_run_id"] == run["parse_run_id"]

    repeated = materials.promote_parse_run_artifacts(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        wiki_root=wiki_root,
        results_root=parser_root,
    )
    assert repeated["status"] == "existing"

    archive = materials.deal_parse_run_dir(
        DEAL_ID, document["document_id"], run["parse_run_id"], wiki_root=wiki_root
    )
    (archive / "document.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash conflict"):
        materials.promote_parse_run_artifacts(
            DEAL_ID,
            document["document_id"],
            run["parse_run_id"],
            wiki_root=wiki_root,
            results_root=parser_root,
        )


def test_failed_promotion_does_not_update_current_pointer(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(DEAL_ID, document["document_id"], wiki_root=wiki_root)
    task_id = "pmm-parser-task-missing-markdown"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    result = parser_root / task_id
    result.mkdir(parents=True)
    _write_json(result / "artifact_manifest.json", {"task_id": task_id, "artifacts": {}})

    with pytest.raises(ValueError, match="canonical parser Markdown"):
        materials.promote_parse_run_artifacts(
            DEAL_ID,
            document["document_id"],
            run["parse_run_id"],
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    assert not materials.deal_current_parse_run_path(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    ).exists()


def test_completed_task_reconcile_is_concurrent_and_restart_safe(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(DEAL_ID, document["document_id"], wiki_root=wiki_root)
    task_id = "pmm-parser-task-recovery"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    _parser_result(parser_root, task_id)

    def reconcile():
        return materials.reconcile_parse_run(
            DEAL_ID,
            document["document_id"],
            parser_task={"task_id": task_id, "status": "completed"},
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile(), range(2)))

    assert all(result["document"]["parse_status"] == "succeeded" for result in results)
    run_dir = materials.deal_parse_run_dir(
        DEAL_ID, document["document_id"], run["parse_run_id"], wiki_root=wiki_root
    )
    assert (run_dir / "archive_manifest.json").is_file()
    assert not list(run_dir.parent.glob(".staging-*"))


def test_reconcile_archive_failure_is_diagnostic_and_keeps_current_unchanged(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(DEAL_ID, document["document_id"], wiki_root=wiki_root)
    task_id = "pmm-parser-task-recovery-fail"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    result = parser_root / task_id
    result.mkdir(parents=True)
    _write_json(result / "artifact_manifest.json", {"task_id": task_id, "artifacts": {}})

    with pytest.raises(materials.ArtifactPromotionError):
        materials.reconcile_parse_run(
            DEAL_ID,
            document["document_id"],
            parser_task={"status": "completed"},
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    status = materials.read_material_parse_status(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    )
    assert status["parse_run"]["status"] == "failed"
    assert status["parse_run"]["failure_code"] == "artifact_promotion_failed"
    assert not materials.deal_current_parse_run_path(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    ).exists()
