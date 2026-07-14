import importlib
import json
from pathlib import Path

import pytest


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _document(*, legacy: bool) -> dict:
    hidden_block = {
        "block_id": "hidden-block",
        "source_order": 1,
        "dom_node_id": "hidden-parent",
        "block_type": "paragraph",
        "text": "hidden ixbrl metadata",
        "fact_ids": ["fact-1"],
    }
    visible_block = {
        "block_id": "visible-block",
        "source_order": 2,
        "dom_node_id": "visible-paragraph",
        "block_type": "paragraph",
        "text": "Visible filing text",
        "fact_ids": [],
    }
    blocks = [hidden_block, visible_block] if legacy else [visible_block]
    facts = [
        {
            "fact_id": "fact-1",
            "dom_node_id": "hidden-fact",
            "block_id": "hidden-block" if legacy else None,
        }
    ]
    relations = (
        [
            {
                "relation_id": "old-hidden-relation",
                "relation_type": "block_contains_fact",
                "source_id": "hidden-block",
                "target_id": "fact-1",
            }
        ]
        if legacy
        else []
    )
    quality = {
        "block_count": len(blocks),
        "block_source_map_count": len(blocks),
    }
    return {
        "schema_version": "sec_html_document_full_v1",
        "dom_nodes": [
            {
                "dom_node_id": "body",
                "source_order": 1,
                "tag": "body",
                "attrs": {},
                "parent_id": None,
            },
            {
                "dom_node_id": "hidden-parent",
                "source_order": 2,
                "tag": "div",
                "attrs": {"style": "display: none !important"},
                "parent_id": "body",
            },
            {
                "dom_node_id": "hidden-fact",
                "source_order": 3,
                "tag": "ix:nonfraction",
                "attrs": {"id": "fact-anchor"},
                "parent_id": "hidden-parent",
            },
            {
                "dom_node_id": "visible-paragraph",
                "source_order": 4,
                "tag": "p",
                "attrs": {"id": "visible"},
                "parent_id": "body",
            },
        ],
        "blocks": blocks,
        "facts": facts,
        "relations": relations,
        "quality": quality,
    }


def _protected_artifacts(package_dir: Path) -> None:
    (package_dir / "raw").mkdir(parents=True, exist_ok=True)
    (package_dir / "raw" / "filing.htm").write_text("<html>fixture</html>", encoding="utf-8")
    write_json(package_dir / "sections.json", {"sections": []})
    (package_dir / "sections").mkdir(exist_ok=True)
    (package_dir / "sections" / "business.md").write_text("Business", encoding="utf-8")
    write_json(package_dir / "tables" / "table_index.json", {"tables": []})
    write_json(package_dir / "xbrl" / "facts_raw.json", {"facts": [{"fact_id": "fact-1"}]})
    write_json(package_dir / "xbrl" / "contexts.json", {"contexts": {}})
    write_json(package_dir / "xbrl" / "units.json", {"units": {}})
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": []})
    write_json(package_dir / "metrics" / "financial_checks.json", {"overall_status": "pass"})


def _source_package(source_root: Path) -> Path:
    package = source_root / "companies" / "LOVE-Lovesac-Co" / "reports" / "2026-10-K-fixture"
    _protected_artifacts(package)
    manifest = {
        "schema_version": "market_evidence_package_v1",
        "market": "US",
        "ticker": "LOVE",
        "filing_id": "US:fixture:LOVE",
        "report_id": "2026-10-K-fixture",
        "form": "10-K",
        "local_source_path": "raw/filing.htm",
        "parse_run_id": "old-parse-run",
    }
    write_json(package / "manifest.json", manifest)
    write_json(package / "parser" / "document_full.json", _document(legacy=True))
    (package / "parser" / "report_complete.md").write_text("old report", encoding="utf-8")
    write_json(package / "parser" / "content_list_enhanced.json", {"blocks": ["hidden-block", "visible-block"]})
    write_json(package / "parser" / "table_relations.json", {"relations": []})
    write_json(package / "qa" / "source_map.json", {"entries": []})
    write_json(package / "qa" / "quality_report.json", {"source_map_entry_count": 0})
    company = package.parents[1]
    write_json(company / "company.json", {"ticker": "LOVE"})
    return package


def _write_new_stage(package: Path, parser_results_root: Path) -> None:
    document = _document(legacy=False)
    write_json(package / "parser" / "document_full.json", document)
    (package / "parser" / "report_complete.md").write_text("new visible report", encoding="utf-8")
    write_json(package / "parser" / "content_list_enhanced.json", {"blocks": ["visible-block"]})
    write_json(package / "parser" / "table_relations.json", {"relations": []})
    (package / "sections" / "report_complete.md").write_text("new visible report", encoding="utf-8")
    source_map = {
        "entries": [
            {
                "evidence_id": "visible-evidence",
                "source_type": "sec_html_block",
                "block_id": "visible-block",
                "local_path": "parser/report_complete.md",
                "quote_text": "Visible filing text",
            }
        ]
    }
    write_json(package / "qa" / "source_map.json", source_map)
    write_json(
        package / "qa" / "quality_report.json",
        {
            "resolvable_evidence_count": 0,
            "unresolvable_evidence_count": 0,
            "source_map_entry_count": 1,
            "resolvable_source_map_entry_count": 1,
            "unresolvable_source_map_entry_count": 0,
            "evidence_resolvability_ratio": 1.0,
            "full_document": {"block_count": 1, "block_source_map_count": 1},
            "summary": {
                "source_map": {
                    "source_map_entry_count": 1,
                    "resolvable_source_map_entry_count": 1,
                    "unresolvable_source_map_entry_count": 0,
                    "evidence_resolvability_ratio": 1.0,
                }
            },
        },
    )
    manifest = read_json(package / "manifest.json")
    task_id = "LOVE-10-K-fixture"
    manifest.update(
        {
            "parse_run_id": "new-parse-run",
            "parser_result_task_id": task_id,
            "parser_result_dir": str(parser_results_root / task_id),
        }
    )
    write_json(package / "manifest.json", manifest)
    canonical = parser_results_root / task_id
    write_json(canonical / "document_full.json", document)
    (canonical / "report_complete.md").write_text("new visible report", encoding="utf-8")
    write_json(canonical / "content_list_enhanced.json", {"blocks": ["visible-block"]})
    write_json(canonical / "table_relations.json", {"relations": []})


def test_staging_root_safety_requires_isolation_and_explicit_resume(tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    (source_root / "companies").mkdir(parents=True)

    with pytest.raises(ValueError, match="inside source root"):
        module._validate_roots(source_root, source_root / "staging", resume=False)

    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    with pytest.raises(FileExistsError, match="--resume"):
        module._validate_roots(source_root, staging_root, resume=False)

    source, staging = module._validate_roots(source_root, staging_root, resume=True)
    assert source == source_root.resolve()
    assert staging == staging_root.resolve()

    with pytest.raises(ValueError, match="production US Wiki root"):
        module._validate_roots(source_root, module.DEFAULT_SOURCE_ROOT, resume=True)

    with pytest.raises(ValueError, match="production parser results"):
        module._validate_roots(source_root, module.PRODUCTION_PARSER_RESULTS_ROOT, resume=True)


def test_resume_rejects_a_staging_report_from_another_source(tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    _source_package(source_root)
    staging_root = tmp_path / "staging"
    write_json(
        staging_root / module.REPORT_PATH,
        {"schema_version": module.REPORT_SCHEMA_VERSION, "source_root": str(tmp_path / "different-source")},
    )

    with pytest.raises(ValueError, match="different source root"):
        module.run_staging_audit(source_root, staging_root, tickers={"LOVE"}, resume=True)


def test_audit_staged_package_enforces_hidden_id_source_map_and_mirror_contracts(tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    source_package = _source_package(source_root)
    staging_package = tmp_path / "staging" / source_package.relative_to(source_root)
    module._copytree_replace(source_package, staging_package)
    parser_results_root = tmp_path / "staging" / module.STAGING_PARSER_RESULTS_DIR
    _write_new_stage(staging_package, parser_results_root)

    result = module.audit_staged_package(
        source_package,
        staging_package,
        parser_results_root=parser_results_root,
        backfill_item={"status": "updated", "filing_id": "US:fixture:LOVE"},
    )

    assert result["status"] == "pass"
    assert result["failed_checks"] == []
    assert all(check["passed"] for check in result["checks"])
    assert result["parse_run_id"] == {"before": "old-parse-run", "after": "new-parse-run", "changed": True}
    assert "parser/document_full.json" in result["changed_hashes"]
    assert "metrics/normalized_metrics.json" not in result["changed_hashes"]


def test_audit_staged_package_reports_contract_failures(tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    source_package = _source_package(source_root)
    staging_package = tmp_path / "staging" / source_package.relative_to(source_root)
    module._copytree_replace(source_package, staging_package)
    parser_results_root = tmp_path / "staging" / module.STAGING_PARSER_RESULTS_DIR
    _write_new_stage(staging_package, parser_results_root)

    quality = read_json(staging_package / "qa" / "quality_report.json")
    quality["source_map_entry_count"] = 0
    write_json(staging_package / "qa" / "quality_report.json", quality)
    write_json(staging_package / "metrics" / "normalized_metrics.json", {"metrics": [{"changed": True}]})
    (parser_results_root / "LOVE-10-K-fixture" / "report_complete.md").write_text(
        "canonical mismatch",
        encoding="utf-8",
    )

    result = module.audit_staged_package(
        source_package,
        staging_package,
        parser_results_root=parser_results_root,
    )

    assert result["status"] == "fail"
    assert set(result["failed_checks"]) == {
        "source_map_quality_counts_match",
        "parser_mirrors_match",
        "protected_artifacts_unchanged",
    }


def test_run_staging_audit_copies_fixture_calls_force_no_index_and_resumes(monkeypatch, tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    source_package = _source_package(source_root)
    source_hashes_before = module.compute_artifact_hashes(source_package)
    staging_root = tmp_path / "staging"
    calls = []

    def fake_backfill(output_root, **kwargs):
        calls.append({"output_root": output_root, **kwargs})
        staged_package = output_root / source_package.relative_to(source_root)
        _write_new_stage(staged_package, kwargs["parser_results_root"])
        return {
            "force": kwargs["force"],
            "index": None,
            "items": [
                {
                    "status": "updated",
                    "filing_id": "US:fixture:LOVE",
                    "package_path": str(staged_package),
                    "parse_run_id": "new-parse-run",
                }
            ],
        }

    monkeypatch.setattr(module.backfill_sec_full_document, "backfill_full_documents", fake_backfill)

    report = module.run_staging_audit(source_root, staging_root, tickers={"LOVE"})

    assert report["status"] == "pass"
    assert report["status_counts"] == {"pass": 1}
    assert len(calls) == 1
    assert calls[0]["output_root"] == staging_root.resolve()
    assert calls[0]["tickers"] == {"LOVE"}
    assert calls[0]["force"] is True
    assert calls[0]["no_index"] is True
    assert calls[0]["parser_results_root"] == staging_root.resolve() / module.STAGING_PARSER_RESULTS_DIR
    assert module.compute_artifact_hashes(source_package) == source_hashes_before
    assert read_json(source_package / "parser" / "document_full.json")["blocks"][0]["block_id"] == "hidden-block"
    assert read_json(staging_root / module.REPORT_PATH)["status"] == "pass"
    checkpoint_paths = list((staging_root / module.CHECKPOINT_DIR).glob("*.json"))
    assert len(checkpoint_paths) == 1
    checkpoint = read_json(checkpoint_paths[0])
    assert checkpoint["status"] == "pass"
    assert checkpoint["implementation_hashes_digest"] == report["implementation_hashes_digest"]

    monkeypatch.setattr(
        module.backfill_sec_full_document,
        "backfill_full_documents",
        lambda *args, **kwargs: pytest.fail("a current passing checkpoint must not be rebuilt"),
    )
    resumed = module.run_staging_audit(source_root, staging_root, tickers={"LOVE"}, resume=True)

    assert resumed["status"] == "pass"
    assert resumed["items"][0]["resumed"] is True

    monkeypatch.setattr(module, "_implementation_hashes", lambda: {"sec_html_document.py": "changed"})
    monkeypatch.setattr(module.backfill_sec_full_document, "backfill_full_documents", fake_backfill)
    rebuilt = module.run_staging_audit(source_root, staging_root, tickers={"LOVE"}, resume=True)

    assert rebuilt["status"] == "pass"
    assert len(calls) == 2
    assert rebuilt["items"][0].get("resumed") is not True


def test_run_staging_audit_checkpoints_audit_exceptions(monkeypatch, tmp_path):
    module = importlib.import_module("stage_sec_full_document_backfill")
    source_root = tmp_path / "source"
    _source_package(source_root)
    staging_root = tmp_path / "staging"

    monkeypatch.setattr(
        module.backfill_sec_full_document,
        "backfill_full_documents",
        lambda *args, **kwargs: {
            "items": [{"status": "updated", "filing_id": "US:fixture:LOVE"}],
        },
    )
    monkeypatch.setattr(
        module,
        "audit_staged_package",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit exploded")),
    )

    report = module.run_staging_audit(source_root, staging_root, tickers={"LOVE"})

    assert report["status"] == "fail"
    assert report["items"][0]["failed_checks"] == ["audit"]
    assert report["items"][0]["error"] == "audit exploded"
    checkpoint = next((staging_root / module.CHECKPOINT_DIR).glob("*.json"))
    assert read_json(checkpoint)["failed_checks"] == ["audit"]
