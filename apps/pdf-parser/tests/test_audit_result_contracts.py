import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_result_contracts.py"
SPEC = importlib.util.spec_from_file_location("audit_result_contracts", SCRIPT_PATH)
audit_result_contracts = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit_result_contracts)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_audit_one_passes_complete_result_contract(tmp_path):
    result_dir = tmp_path / "task-1"
    artifacts = {}
    for name in audit_result_contracts.manifests.REQUIRED_ARTIFACTS:
        (result_dir / name).parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            write_json(result_dir / name, {"schema_version": 1, "market": "HK"})
        else:
            (result_dir / name).write_text("x" * 1200, encoding="utf-8")
        artifacts[name] = {"exists": True, "sha256": "abc"}
    write_json(result_dir / "metadata.json", {"schema_version": "pdf_parser_metadata_v1", "market": "HK"})
    write_json(
        result_dir / "artifact_manifest.json",
        {
            "schema_version": "pdf_parser_artifact_manifest_v1",
            "core": {"ready": True, "missing": [], "invalid_json": [], "bundle_sha256": "bundle"},
            "artifacts": artifacts,
        },
    )
    write_json(result_dir / "hash_manifest.json", {"schema_version": "pdf_parser_hash_manifest_v1"})
    write_json(
        result_dir / "financial_data.json",
        {"schema_version": 13, "market": "HK", "profile_rule_version": "v", "statements": [{"items": []}], "key_metrics": [{}]},
    )
    write_json(
        result_dir / "financial_checks.json",
        {"schema_version": 12, "market": "HK", "profile_rule_version": "v", "summary": {"fail": 0}},
    )
    write_json(result_dir / "quality_report.json", {"schema_version": 11, "financial_overall_status": "pass"})
    write_json(result_dir / "document_full.json", {"schema_version": 3, "markdown": {"content": "x" * 1200}})
    write_json(result_dir / "content_list_enhanced.json", {"schema_version": 1, "table_count": 1, "tables": [{}], "pages": [{}]})
    write_json(result_dir / "table_index.json", [{}])
    write_json(result_dir / "table_relations.json", {"schema_version": "document_table_relations_v1", "candidate_table_count": 1, "relations": [{}]})

    item = audit_result_contracts.audit_one(result_dir)

    assert item["aligned"] is True
    assert item["stats"]["relation_count"] == 1
    assert item["stats"]["relation_candidate_count"] == 1
    assert item["stats"]["enhanced_page_count"] == 1
    assert item["stats"]["statement_count"] == 1
