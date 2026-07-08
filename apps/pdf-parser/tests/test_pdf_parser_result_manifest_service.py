import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_parser_result_manifest_service as manifests


def _write(path: Path, value: str | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _complete_result_dir(tmp_path: Path) -> Path:
    result_dir = tmp_path / "task-1"
    _write(result_dir / "result.md", "# report\n" + "x" * 1200)
    _write(result_dir / "result_complete.md", "# complete\n" + "x" * 1200)
    _write(result_dir / "document_full.json", {"schema_version": 3, "markdown": {"content": "x" * 1200}})
    _write(result_dir / "content_list_enhanced.json", {"schema_version": 10, "table_count": 1, "tables": [{}], "pages": [{}]})
    _write(result_dir / "table_index.json", [{"id": "t1"}])
    _write(result_dir / "table_relations.json", {"schema_version": 1, "candidate_table_count": 1})
    _write(
        result_dir / "financial_data.json",
        {"schema_version": 13, "rule_version": "financial_rules_v14", "market": "HK", "statements": [{}]},
    )
    _write(result_dir / "financial_checks.json", {"schema_version": 12, "rule_version": "financial_rules_v14", "market": "HK"})
    _write(result_dir / "quality_report.json", {"schema_version": 11, "market": "HK"})
    _write(result_dir / "content_list.json", [])
    return result_dir


def test_build_result_contract_infers_market_and_hashes(tmp_path):
    result_dir = _complete_result_dir(tmp_path)
    task = {
        "task_id": "task-1",
        "filename": "TENCENT_HK_00700_2025-12-31_年报_2026-04-09_hkex_691d0e45.pdf",
        "submit_config": {"market": "HK", "backend": "hybrid-http-client", "parse_method": "auto"},
        "status": "completed",
    }

    metadata, artifact_manifest, hash_manifest = manifests.build_result_contract(
        task,
        result_dir,
        repo_root=tmp_path,
        generated_at="2026-07-08T00:00:00Z",
    )

    assert metadata["schema_version"] == "pdf_parser_metadata_v1"
    assert metadata["market"] == "HK"
    assert metadata["ticker"] == "00700"
    assert metadata["fiscal_year"] == 2025
    assert artifact_manifest["core"]["status"] == "ready"
    assert artifact_manifest["core"]["content_issues"] == []
    assert artifact_manifest["artifacts"]["document_full.json"]["sha256"]
    assert artifact_manifest["artifacts"]["financial_data.json"]["market"] == "HK"
    assert hash_manifest["bundle_sha256"] == artifact_manifest["core"]["bundle_sha256"]


def test_build_result_contract_reports_missing_required_file(tmp_path):
    result_dir = _complete_result_dir(tmp_path)
    (result_dir / "table_relations.json").unlink()
    task = {"task_id": "task-1", "filename": "ACME_EU_ACM_2025-12-31_年报_2026-03-01_eu_direct_abcd.pdf"}

    metadata, artifact_manifest, _ = manifests.build_result_contract(task, result_dir, repo_root=tmp_path)

    assert metadata["market"] == "EU"
    assert artifact_manifest["core"]["status"] == "incomplete"
    assert "table_relations.json" in artifact_manifest["core"]["missing"]


def test_build_result_contract_reports_empty_content_artifacts(tmp_path):
    result_dir = _complete_result_dir(tmp_path)
    _write(result_dir / "content_list_enhanced.json", {"schema_version": 10, "table_count": 1, "tables": [{}], "pages": []})
    _write(result_dir / "table_relations.json", {"schema_version": 1, "candidate_table_count": 0})
    _write(result_dir / "financial_data.json", {"schema_version": 13, "rule_version": "financial_rules_v14", "market": "HK", "statements": []})
    task = {
        "task_id": "task-1",
        "filename": "TENCENT_HK_00700_2025-12-31_年报_2026-04-09_hkex_691d0e45.pdf",
    }

    _metadata, artifact_manifest, _hash_manifest = manifests.build_result_contract(task, result_dir, repo_root=tmp_path)

    assert artifact_manifest["core"]["status"] == "incomplete"
    assert artifact_manifest["core"]["ready"] is False
    assert artifact_manifest["core"]["content_issues"] == [
        "content_list_enhanced.pages_empty",
        "financial_data.statements_empty",
        "table_relations.candidates_empty",
    ]
