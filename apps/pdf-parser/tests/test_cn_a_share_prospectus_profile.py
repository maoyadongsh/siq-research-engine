from __future__ import annotations

import io
import json
import os
from pathlib import Path

import app
import pdf_parser_request_utils as request_utils
import pdf_parser_result_manifest_service as manifests
from cn_a_share_prospectus_profile import (
    build_profile_analysis,
    chapter_coverage,
    reporting_period_check,
)

SOURCE_CONTEXT = {
    "domain": "primary_market",
    "deal_id": "DEAL-EXAMPLE-001",
    "document_id": "DOC-0123456789ABCDEF",
    "source_type": "primary_market_prospectus",
    "parse_run_id": "PRUN-20260713-0123456789ABCDEF",
    "origin": "primary_market_materials",
}


def _use_temp_app_paths(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tasks.db")
    results_dir = str(tmp_path / "results")
    uploads_dir = str(tmp_path / "uploads")
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    monkeypatch.setattr(app, "DB_PATH", db_path)
    monkeypatch.setattr(app, "RESULTS_FOLDER", results_dir)
    monkeypatch.setattr(app, "UPLOAD_FOLDER", uploads_dir)
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_cleanup_old_data", lambda: None)
    monkeypatch.setattr(app, "_wake_queue_worker", lambda: None)
    monkeypatch.setattr(app, "_looks_like_pdf", lambda _path: True)
    monkeypatch.setattr(app, "_get_pdf_page_count", lambda _path: 8)
    app._init_db()


def _write(path: Path, value: str | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _prospectus_markdown() -> str:
    return "\n".join(
        [
            "[PDF_PAGE: 1]",
            "# 本次发行基本情况",
            "# 重大风险提示",
            "# 发行人概况",
            "# 业务与技术",
            "# 公司治理与独立性",
            "# 财务会计信息与管理层讨论与分析",
            "报告期包括 2023年度、2024年度和2025年度。",
            "资产负债表日为2023年12月31日、2024年12月31日、2025年12月31日。",
            "# 募集资金投资项目",
            "[PDF_PAGE: 8]",
            "正文" * 600,
        ]
    )


def _complete_result_dir(tmp_path: Path) -> Path:
    result_dir = tmp_path / "task-prospectus"
    markdown = _prospectus_markdown()
    _write(result_dir / "result.md", markdown)
    _write(result_dir / "result_complete.md", markdown)
    _write(result_dir / "document_full.json", {"schema_version": 3, "markdown": {"content": markdown}})
    _write(
        result_dir / "content_list_enhanced.json",
        {"schema_version": 10, "table_count": 1, "tables": [{}], "pages": [{"pdf_page_number": 1}]},
    )
    _write(result_dir / "table_index.json", [{"id": "t1"}])
    _write(result_dir / "table_relations.json", {"schema_version": 1, "candidate_table_count": 1})
    _write(result_dir / "financial_data.json", {"schema_version": 13, "market": "CN", "statements": [{}]})
    _write(result_dir / "financial_checks.json", {"schema_version": 12, "market": "CN"})
    _write(result_dir / "quality_report.json", {"schema_version": 11, "market": "CN"})
    _write(result_dir / "content_list.json", [])
    return result_dir


def test_profile_changes_config_hash_without_changing_generic_hash():
    generic = {
        "backend": "hybrid-http-client",
        "parse_method": "auto",
        "market": "CN",
        "start_page_id": "",
        "end_page_id": "",
        "formula_enable": True,
        "table_enable": True,
    }
    generic_with_empty_profile = {**generic, "document_profile": ""}
    prospectus = {**generic, "document_profile": "cn_a_share_prospectus"}

    assert request_utils._parse_config_hash(generic) == request_utils._parse_config_hash(generic_with_empty_profile)
    assert request_utils._parse_config_hash(generic) != request_utils._parse_config_hash(prospectus)
    assert request_utils._parse_config_hash(prospectus) == request_utils._parse_config_hash(
        {**prospectus, "profile_version": "ignored-client-value"}
    )
    assert request_utils._canonical_parse_config(generic) == {
        "parser_version": request_utils.PARSER_CONFIG_VERSION,
        **generic,
    }


def test_submit_config_validates_profile_market_and_controlled_source_context():
    config = request_utils._parse_submit_config(
        {
            "market": "CN",
            "document_profile": "cn_a_share_prospectus",
            "source_context": json.dumps(SOURCE_CONTEXT),
        }
    )
    assert config["document_profile"] == "cn_a_share_prospectus"
    assert config["profile_version"] == "cn_a_share_prospectus_v1"
    assert config["parser_version"] == request_utils.PARSER_CONFIG_VERSION
    assert config["source_context"] == SOURCE_CONTEXT
    assert config["parse_method"] == "auto"
    assert config["formula_enable"] is True
    assert config["table_enable"] is True

    for invalid in (
        {"market": "HK", "document_profile": "cn_a_share_prospectus"},
        {"market": "CN", "document_profile": "annual_report"},
        {
            "market": "CN",
            "document_profile": "cn_a_share_prospectus",
            "source_context": json.dumps({**SOURCE_CONTEXT, "artifact_path": "/tmp/deal"}),
        },
        {"market": "CN", "source_context": json.dumps(SOURCE_CONTEXT)},
    ):
        try:
            request_utils._parse_submit_config(invalid)
        except ValueError:
            pass
        else:  # pragma: no cover - guards all invalid examples above
            raise AssertionError(f"invalid config accepted: {invalid}")


def test_upload_accepts_and_persists_prospectus_profile(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)

    response = app.app.test_client().post(
        "/api/upload",
        data={
            "task_id": "prospectus-task",
            "market": "CN",
            "document_profile": "cn_a_share_prospectus",
            "source_context": json.dumps(SOURCE_CONTEXT),
            "files": [(io.BytesIO(b"%PDF-1.4\nprospectus"), "prospectus.pdf")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tasks"][0]["document_profile"] == "cn_a_share_prospectus"
    assert payload["tasks"][0]["parser_version"] == request_utils.PARSER_CONFIG_VERSION
    assert payload["tasks"][0]["source_context"] == SOURCE_CONTEXT
    task = app._get_task("prospectus-task")
    assert task["submit_config"]["document_profile"] == "cn_a_share_prospectus"
    assert task["submit_config"]["source_context"] == SOURCE_CONTEXT
    status_payload = app._build_status_response(task)
    assert status_payload["document_profile"] == "cn_a_share_prospectus"
    assert status_payload["parser_version"] == request_utils.PARSER_CONFIG_VERSION
    assert status_payload["source_context"] == SOURCE_CONTEXT


def test_profile_analysis_recognizes_heading_variants_and_reporting_periods(tmp_path):
    markdown = _prospectus_markdown()
    coverage = chapter_coverage(markdown)
    periods = reporting_period_check(markdown)

    assert coverage["status"] == "pass"
    assert coverage["matched_count"] == coverage["required_count"]
    assert periods["status"] == "pass"
    assert periods["years"] == [2023, 2024, 2025]

    result_dir = _complete_result_dir(tmp_path)
    analysis = build_profile_analysis("cn_a_share_prospectus", result_dir)
    assert analysis["quality_status"] == "pass"
    assert analysis["capabilities"]["page_trace"]["available"] is True
    assert analysis["capabilities"]["financial_statements"]["available"] is True


def test_heading_regression_samples_have_full_core_section_coverage():
    fixture_path = Path(__file__).parent / "fixtures" / "cn_a_share_prospectus_heading_samples.json"
    samples = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert len(samples) >= 3
    for sample in samples:
        markdown = "\n".join(f"# {heading}" for heading in sample["headings"])
        coverage = chapter_coverage(markdown)
        assert coverage["status"] == "pass", sample["sample_id"]
        assert coverage["matched_count"] == coverage["required_count"], sample["sample_id"]


def test_same_pdf_can_be_submitted_with_generic_and_prospectus_profiles(tmp_path, monkeypatch):
    _use_temp_app_paths(tmp_path, monkeypatch)
    content = b"%PDF-1.4\nsame-content"
    client = app.app.test_client()

    generic = client.post(
        "/api/upload",
        data={"market": "CN", "files": [(io.BytesIO(content), "generic.pdf")]},
        content_type="multipart/form-data",
    )
    prospectus = client.post(
        "/api/upload",
        data={
            "market": "CN",
            "document_profile": "cn_a_share_prospectus",
            "source_context": json.dumps(SOURCE_CONTEXT),
            "files": [(io.BytesIO(content), "prospectus.pdf")],
        },
        content_type="multipart/form-data",
    )

    assert generic.status_code == 200
    assert prospectus.status_code == 200
    generic_task = app._get_task(generic.get_json()["task_id"])
    profile_task = app._get_task(prospectus.get_json()["task_id"])
    assert generic_task["file_sha256"] == profile_task["file_sha256"]
    assert generic_task["parse_config_hash"] != profile_task["parse_config_hash"]


def test_result_manifest_exposes_profile_identity_capabilities_and_quality(tmp_path):
    result_dir = _complete_result_dir(tmp_path)
    task = {
        "task_id": "task-prospectus",
        "filename": "issuer_CN_600000_2025-12-31_招股书.pdf",
        "file_sha256": "a" * 64,
        "parse_config_hash": "b" * 64,
        "submit_config": {
            "market": "CN",
            "backend": "hybrid-http-client",
            "parse_method": "auto",
            "formula_enable": True,
            "table_enable": True,
            "document_profile": "cn_a_share_prospectus",
            "profile_version": "cn_a_share_prospectus_v1",
            "source_context": SOURCE_CONTEXT,
        },
        "status": "completed",
    }

    metadata, artifact_manifest, hash_manifest = manifests.build_result_contract(
        task,
        result_dir,
        repo_root=tmp_path,
        generated_at="2026-07-13T00:00:00Z",
    )

    assert metadata["document_profile"] == "cn_a_share_prospectus"
    assert metadata["raw_sha256"] == "a" * 64
    assert metadata["parse_config_hash"] == "b" * 64
    assert metadata["parser"]["version"] == request_utils.PARSER_CONFIG_VERSION
    assert artifact_manifest["identity"] == {
        "parser_version": request_utils.PARSER_CONFIG_VERSION,
        "market": "CN",
        "document_profile": "cn_a_share_prospectus",
        "raw_sha256": "a" * 64,
        "parse_config_hash": "b" * 64,
        "source_context": SOURCE_CONTEXT,
    }
    assert artifact_manifest["quality"]["status"] == "pass"
    assert artifact_manifest["quality"]["chapter_coverage"]["coverage_ratio"] == 1.0
    assert artifact_manifest["capabilities"]["reporting_periods"]["distinct_year_count"] == 3
    assert hash_manifest["identity"]["document_profile"] == "cn_a_share_prospectus"
