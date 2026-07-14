from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest_hk_pdf_wiki as hk_ingest
from ingest_hk_pdf_wiki import (
    DEFAULT_DOWNLOADS_ROOT,
    DEFAULT_OUTPUT_ROOT,
    build_evidence_index,
    build_package_financial_data,
    build_three_statements,
    canonical_hk_identity,
    load_current_hk_financial_artifacts,
    normalize_hk_financial_data_currency,
    resolve_hk_sidecar,
    run,
    statement_reporting_period,
)
from market_report_rules_service.evidence_package import validate_evidence_package
from package_facade import _manifest_payload, _quality_report, write_report_package_facade


def _row() -> dict:
    return {
        "financial_data": {
            "currency": "HKD",
            "accounting_standard": "HKFRS",
            "statements": [
                {
                    "statement_type": "income_statement",
                    "statement_name": "Income statement",
                    "unit": "RMB million",
                    "currency": "HKD",
                    "items": [
                        {
                            "canonical_name": "operating_revenue",
                            "local_name": "Revenue",
                            "unit": "RMB million",
                            "currency": "HKD",
                            "period_key": "2025-12-31",
                            "value": "751766",
                            "raw_value": "751,766",
                        }
                    ],
                }
            ],
        },
        "result_dir": Path("."),
        "table_index": [],
        "period_end": "2025-12-31",
        "company_name": "Tencent",
        "ticker": "00700",
        "report_id": "2025-annual",
        "report_kind": "annual_report",
        "report_type": "annual",
        "task_id": "hk-contract",
        "company_wiki_id": "00700-TENCENT",
        "report_year": 2025,
        "published_at": "2026-03-20",
        "source_id": "hkex",
    }


def test_hk_wiki_builder_prefers_rmb_unit_over_stale_hkd():
    row = _row()

    normalized = normalize_hk_financial_data_currency(row["financial_data"])
    statements = build_three_statements(row)

    assert normalized["statements"][0]["currency"] == "CNY"
    assert normalized["statements"][0]["items"][0]["currency"] == "CNY"
    assert statements["metrics"][0]["currency"] == "CNY"
    assert statements["metrics"][0]["unit"] == "RMB million"


def test_hk_wiki_builder_promotes_bank_net_interest_income():
    row = _row()
    row["financial_data"]["statements"][0]["items"].append(
        {
            "canonical_name": "net_interest_income",
            "local_name": "Net interest income",
            "unit": "USD million",
            "currency": "USD",
            "period_key": "2025-12-31",
            "value": "34794",
            "raw_value": "34,794",
        }
    )

    statements = build_three_statements(row)

    assert {metric["metric_key"] for metric in statements["metrics"]} == {
        "net_interest_income",
        "operating_revenue",
    }


def test_hk_staging_builder_rebuilds_stale_financial_artifacts_in_memory(tmp_path, monkeypatch):
    result_dir = tmp_path / "task"
    result_dir.mkdir()
    stale_data = {"market": "HK", "profile_rule_version": "hk-pdf-financial-profile-v2"}
    stale_checks = {"market": "HK", "profile_rule_version": "hk-pdf-financial-profile-v2"}
    (result_dir / "financial_data.json").write_text(json.dumps(stale_data), encoding="utf-8")
    (result_dir / "financial_checks.json").write_text(json.dumps(stale_checks), encoding="utf-8")
    (result_dir / "result.md").write_text("# report\n", encoding="utf-8")
    calls = []

    def rebuild(task, markdown, **kwargs):
        calls.append((task, markdown, kwargs))
        return (
            {"market": "HK", "profile_rule_version": hk_ingest.HK_FINANCIAL_PROFILE_VERSION, "statements": []},
            {"market": "HK", "profile_rule_version": hk_ingest.HK_FINANCIAL_PROFILE_VERSION, "overall_status": "pass"},
        )

    monkeypatch.setattr(hk_ingest, "build_hk_financial_artifacts", rebuild)

    data, checks, source = load_current_hk_financial_artifacts(
        result_dir,
        metadata={"filename": "TENCENT_HK_00700_2025-12-31_annual_hkex.pdf"},
        document_full={"task": {"task_id": "task"}},
    )

    assert source == "rebuilt_in_memory"
    assert data["profile_rule_version"] == hk_ingest.HK_FINANCIAL_PROFILE_VERSION
    assert checks["overall_status"] == "pass"
    assert calls[0][0]["filename"] == "TENCENT_HK_00700_2025-12-31_annual_hkex.pdf"
    assert json.loads((result_dir / "financial_data.json").read_text(encoding="utf-8")) == stale_data


def test_hk_sidecar_resolves_canonical_identity_and_source_hash(tmp_path):
    filename = "TENCENT_HK_00700_2025-12-31_annual_2026-03-20_hkex_deadbeef.pdf"
    source_pdf = tmp_path / filename
    source_pdf.write_bytes(b"official hkex pdf fixture")
    source_sha256 = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    sidecar = tmp_path / f"{filename}.metadata.json"
    sidecar.write_text(
        json.dumps(
            {
                "candidate": {
                    "company_id": "00700",
                    "report_end": "2025-12-31",
                    "accession_number": "12100024",
                    "report_family": "annual",
                    "document_url": "https://www1.hkexnews.hk/filing.pdf",
                },
                "downloaded_file": {
                    "content_sha256": source_sha256,
                    "saved_path": str(source_pdf),
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_hk_sidecar(filename, downloads_root=tmp_path)
    identity = canonical_hk_identity(
        resolved,
        ticker="00700",
        period_end="2025-12-31",
        report_family="annual_report",
    )

    assert resolved["status"] == "resolved"
    assert resolved["source_pdf_hash_verified"] is True
    assert resolved["source_pdf_path"] == str(source_pdf)
    assert identity == {
        "filing_id": "HK:00700:12100024",
        "parse_run_id": f"HK:00700:12100024:{source_sha256[:16]}",
        "source_url": "https://www1.hkexnews.hk/filing.pdf",
        "source_sha256": source_sha256,
    }


def test_hk_sidecar_identity_rejects_period_mismatch(tmp_path):
    filename = "TENCENT_HK_00700_2025-12-31_annual_2026-03-20_hkex_deadbeef.pdf"
    source_pdf = tmp_path / filename
    source_pdf.write_bytes(b"official hkex pdf fixture")
    source_sha256 = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    (tmp_path / f"{filename}.metadata.json").write_text(
        json.dumps(
            {
                "candidate": {
                    "company_id": "00700",
                    "report_end": "2025-06-30",
                    "report_family": "annual",
                    "accession_number": "12100024",
                    "document_url": "https://www1.hkexnews.hk/filing.pdf",
                },
                "downloaded_file": {"content_sha256": source_sha256, "saved_path": str(source_pdf)},
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_hk_sidecar(filename, downloads_root=tmp_path)

    assert canonical_hk_identity(resolved, ticker="00700", period_end="2025-12-31", report_family="annual") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_pdf_hash_verified", False),
        ("content_sha256", "z" * 64),
        ("source_url", "https://example.test/filing.pdf"),
        ("report_family", "interim"),
    ],
)
def test_hk_canonical_identity_rejects_unverified_sidecar(field, value):
    sidecar = {
        "status": "resolved",
        "ticker": "00700",
        "period_end": "2025-12-31",
        "report_family": "annual",
        "accession_number": "12100024",
        "source_url": "https://www1.hkexnews.hk/filing.pdf",
        "content_sha256": "a" * 64,
        "source_pdf_hash_verified": True,
    }
    sidecar[field] = value

    assert canonical_hk_identity(sidecar, ticker="00700", period_end="2025-12-31", report_family="annual") is None


def test_hk_canonical_identity_allows_same_year_period_correction_only_with_three_statement_evidence():
    sidecar = {
        "status": "resolved",
        "ticker": "09988",
        "period_end": "2025-12-31",
        "report_family": "annual",
        "accession_number": "11727038",
        "source_url": "https://www1.hkexnews.hk/filing.pdf",
        "content_sha256": "a" * 64,
        "source_pdf_hash_verified": True,
    }

    assert canonical_hk_identity(sidecar, ticker="09988", period_end="2025-03-31", report_family="annual") is None
    assert canonical_hk_identity(
        sidecar,
        ticker="09988",
        period_end="2025-03-31",
        report_family="annual",
        statement_period_verified=True,
    ) == {
        "filing_id": "HK:09988:11727038",
        "parse_run_id": "HK:09988:11727038:aaaaaaaaaaaaaaaa",
        "source_url": "https://www1.hkexnews.hk/filing.pdf",
        "source_sha256": "a" * 64,
    }


def test_hk_reporting_period_uses_latest_period_shared_by_all_three_statements():
    financial_data = {
        "statements": [
            {
                "statement_type": statement_type,
                "scope": "consolidated",
                "items": [
                    {"canonical_name": canonical_name, "period_key": period}
                    for period in ("2024-03-31", "2025-03-31", "2026-03-31")
                ],
            }
            for statement_type, canonical_name in (
                ("balance_sheet", "total_assets"),
                ("income_statement", "operating_revenue"),
                ("cash_flow_statement", "operating_cash_flow_net"),
            )
        ]
    }

    result = statement_reporting_period(financial_data, max_period="2025-12-31")

    assert result["verified"] is True
    assert result["period_end"] == "2025-03-31"
    assert result["common_periods"] == ["2024-03-31", "2025-03-31", "2026-03-31"]
    assert result["eligible_periods"] == ["2024-03-31", "2025-03-31"]


def test_hk_package_manifest_preserves_canonical_sidecar_identity(tmp_path):
    row = _row()
    row.update(
        {
            "filing_id": "HK:00700:12100024",
            "parse_run_id": "HK:00700:12100024:aaaaaaaaaaaaaaaa",
            "accession_number": "12100024",
            "source_url": "https://www1.hkexnews.hk/filing.pdf",
            "source_sha256": "a" * 64,
            "source_tier": "official_regulator",
            "source_verification_status": "official_verified",
            "official_source_verified": True,
            "regulator_host_verified": True,
            "sidecar": {"accession_number": "12100024", "content_sha256": "a" * 64},
        }
    )

    manifest = _manifest_payload(
        market="HK",
        row=row,
        report_json={"identity": {"company_id": "HK:00700"}},
        report_dir=tmp_path / "companies" / "00700-TENCENT" / "reports" / "2025-annual",
        quality_status="pass",
        artifact_hashes={"raw/report.pdf": "a" * 64, "parser/document_full.json": "b" * 64},
    )

    assert manifest["accession_number"] == "12100024"
    assert manifest["source_url"] == "https://www1.hkexnews.hk/filing.pdf"
    assert manifest["source_manifest"]["content_sha256"] == "a" * 64
    assert manifest["source_tier"] == "official_regulator"
    assert manifest["official_source_verified"] is True
    assert manifest["local_source_path"] == "raw/report.pdf"


def test_hk_package_facade_archives_verified_pdf_and_parser_contract(tmp_path):
    result_dir = tmp_path / "parser-result"
    report_dir = tmp_path / "wiki" / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    metrics_dir = tmp_path / "wiki" / "companies" / "00700-TENCENT" / "metrics" / "reports" / "2025-annual"
    source_pdf = tmp_path / "official.pdf"
    result_dir.mkdir()
    report_dir.mkdir(parents=True)
    metrics_dir.mkdir(parents=True)
    source_pdf.write_bytes(b"official hkex pdf fixture")
    parser_files = (
        "result.md",
        "result_complete.md",
        "document_full.json",
        "content_list.json",
        "content_list_enhanced.json",
        "table_index.json",
        "table_relations.json",
        "financial_data.json",
        "financial_checks.json",
        "quality_report.json",
    )
    for name in parser_files:
        payload = "# report\n" if name.endswith(".md") else "{}\n"
        (result_dir / name).write_text(payload, encoding="utf-8")
    (report_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (report_dir / "document_full.json").write_text("{}\n", encoding="utf-8")
    for name in ("financial_data.json", "financial_checks.json", "normalized_metrics.json"):
        (metrics_dir / name).write_text("{}\n", encoding="utf-8")
    row = _row()
    row.update(
        {
            "result_dir": result_dir,
            "source_pdf_path": source_pdf,
            "filing_id": "HK:00700:12100024",
            "parse_run_id": "HK:00700:12100024:aaaaaaaaaaaaaaaa",
            "accession_number": "12100024",
            "source_url": "https://www1.hkexnews.hk/filing.pdf",
            "source_sha256": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
            "source_tier": "official_regulator",
            "source_verification_status": "official_verified",
            "official_source_verified": True,
            "regulator_host_verified": True,
            "financial_checks": {"overall_status": "pass"},
            "quality": {},
        }
    )
    package_financial_data = {
        "schema_version": "hk_package_financial_data_v1",
        "market": "HK",
        "statements": [
            {
                "statement_type": "income_statement",
                "items": [
                    {
                        "name": "Revenue",
                        "canonical_name": "operating_revenue",
                        "statement_type": "income_statement",
                        "values": {"2025-12-31": 751766},
                        "raw_values": {"2025-12-31": "751,766"},
                        "sources": {
                            "2025-12-31": {
                                "artifact_path": "parser/result_complete.md",
                                "line": 1,
                                "quote_text": "Revenue | 751,766",
                            }
                        },
                        "unit": "RMB million",
                        "currency": "CNY",
                    }
                ],
            }
        ],
        "key_metrics": [],
        "operating_metrics": [],
    }

    manifest = write_report_package_facade(
        market="HK",
        company_dir=report_dir.parents[1],
        report_dir=report_dir,
        metrics_dir=metrics_dir,
        row=row,
        report_json={"status": "ready", "identity": {"company_id": "HK:00700"}},
        three_statements={"metrics": []},
        key_metrics=[],
        validation={},
        evidence_items=[],
        package_financial_data=package_financial_data,
    )

    assert (report_dir / "raw" / "report.pdf").read_bytes() == source_pdf.read_bytes()
    assert all((report_dir / "parser" / name).is_file() for name in parser_files)
    assert (report_dir / "tables" / "table_relations.json").is_file()
    assert manifest["artifact_hashes"]["raw/report.pdf"] == hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    validation = validate_evidence_package(report_dir)
    assert validation.ok, validation.errors


def test_package_facade_classifies_only_nonfinancial_suspicious_tables_as_advisory(tmp_path):
    message = "发现 2 张可疑表样本，建议在前端‘优先复核表’中逐项打开可视化溯源。"
    row = _row()
    row.update(
        {
            "result_dir": tmp_path,
            "warnings": [],
            "financial_checks": {"overall_status": "pass"},
            "quality": {
                "warnings": [message, "Markdown 字符数相对页数偏少。"],
                "suspicious_tables": [
                    {
                        "table_index": 9,
                        "table_type": "dimension",
                        "matched_financial_names": [],
                        "classification_reasons": [],
                        "year_binding_required": False,
                    }
                ],
                "core_financial_table_candidates": [
                    {"name": "Statement of Profit or Loss", "table_index": 3, "status": "found"}
                ],
            },
        }
    )

    advisory_report = _quality_report(
        "HK",
        row,
        {"status": "ready"},
        {"metrics": [{"statement_type": "income_statement"}]},
        [],
    )

    assert advisory_report["rule_advisories"] == [message]
    assert advisory_report["rule_warnings"] == ["Markdown 字符数相对页数偏少。"]

    row["quality"]["suspicious_tables"][0]["matched_financial_names"] = [
        "operating_revenue"
    ]
    financial_report = _quality_report(
        "HK",
        row,
        {"status": "ready"},
        {"metrics": [{"statement_type": "income_statement"}]},
        [],
    )

    assert financial_report["rule_advisories"] == []
    assert message in financial_report["rule_warnings"]


def test_hk_package_financial_data_groups_periods_and_binds_portable_evidence():
    row = _row()
    row["filing_id"] = "HK:00700:12100024"
    row["parse_run_id"] = "HK:00700:12100024:aaaaaaaaaaaaaaaa"

    statements = build_three_statements(row)
    package = build_package_financial_data(row, statements, build_evidence_index(row, statements))

    item = package["statements"][1]["items"][0]
    assert item["values"] == {"2025-12-31": 751766.0}
    assert item["currency"] == "CNY"
    assert item["sources"]["2025-12-31"]["evidence_id"] == "00700-2025-annual-metric-00001"
    assert item["sources"]["2025-12-31"]["artifact_path"] == "parser/result_complete.md"


def test_hk_apply_is_staging_only_and_requires_canonical_identity(tmp_path):
    base = {
        "results_dir": tmp_path / "results",
        "downloads_root": DEFAULT_DOWNLOADS_ROOT,
        "limit": 0,
        "apply": True,
        "require_canonical_identity": False,
    }
    unsafe_default = run(argparse.Namespace(**base, output_root=DEFAULT_OUTPUT_ROOT))
    missing_canonical = run(argparse.Namespace(**base, output_root=tmp_path / "staging"))

    assert unsafe_default["blocked"] is True
    assert "apply_requires_independent_staging_output" in unsafe_default["safety_errors"]
    assert missing_canonical["blocked"] is True
    assert missing_canonical["safety_errors"] == ["apply_requires_canonical_identity"]


def test_hk_apply_rejects_nonempty_staging_output(tmp_path):
    output_root = tmp_path / "staging"
    output_root.mkdir()
    (output_root / "stale.json").write_text("{}", encoding="utf-8")

    payload = run(
        argparse.Namespace(
            results_dir=tmp_path / "results",
            downloads_root=DEFAULT_DOWNLOADS_ROOT,
            output_root=output_root,
            limit=0,
            apply=True,
            require_canonical_identity=True,
        )
    )

    assert payload["blocked"] is True
    assert payload["safety_errors"] == ["staging_output_must_be_new_or_empty"]


def test_hk_build_plan_can_scope_to_one_task(monkeypatch, tmp_path):
    for task_id in ("task-a", "task-b"):
        (tmp_path / task_id).mkdir()

    monkeypatch.setattr(
        hk_ingest,
        "inspect_hk_result",
        lambda result_dir, downloads_root: {"task_id": result_dir.name},
    )
    monkeypatch.setattr(hk_ingest, "select_active", lambda rows: (rows, {"selected": len(rows)}))

    rows, selection = hk_ingest.build_plan(tmp_path, task_id="task-b")

    assert rows == [{"task_id": "task-b"}]
    assert selection == {"selected": 1}


def test_hk_operational_update_keeps_canonical_identity_gate(tmp_path):
    base = {
        "results_dir": tmp_path / "results",
        "downloads_root": DEFAULT_DOWNLOADS_ROOT,
        "output_root": DEFAULT_OUTPUT_ROOT,
        "limit": 0,
        "apply": True,
        "operational_update": True,
    }

    missing_canonical = run(argparse.Namespace(**base, require_canonical_identity=False))

    assert missing_canonical["blocked"] is True
    assert missing_canonical["safety_errors"] == ["apply_requires_canonical_identity"]


def test_audit_existing_staging_is_read_only_and_reports_evidence_contract(tmp_path, monkeypatch):
    output_root = tmp_path / "staging"
    package_dir = output_root / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    package_dir.mkdir(parents=True)
    hk_ingest.write_json(
        package_dir / "manifest.json",
        {
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:12100024",
            "parse_run_id": "HK:00700:12100024:abc",
            "accession_number": "12100024",
            "source_url": "https://www1.hkexnews.hk/report.pdf",
            "source_manifest": {"content_sha256": "a" * 64},
        },
    )
    hk_ingest.write_json(
        package_dir / "metrics" / "financial_checks.json",
        {"profile_rule_version": hk_ingest.HK_FINANCIAL_PROFILE_VERSION},
    )
    monkeypatch.setattr(
        hk_ingest,
        "validate_staging_packages",
        lambda _root: {
            "package_count": 1,
            "passed_count": 1,
            "failed_count": 0,
            "unit_currency_mismatch_count": 0,
            "quality_gate_decisions": {"allow": 1},
            "quality_gate_block_count": 0,
            "passed": True,
        },
    )

    payload = hk_ingest.audit_existing_staging(output_root)

    assert payload["read_only"] is True
    assert payload["blocked"] is False
    assert payload["candidate_report_count"] == 1
    assert payload["canonical_identity"]["resolved_reports"] == 1
    assert payload["financial_artifacts"]["profile_versions"] == {
        hk_ingest.HK_FINANCIAL_PROFILE_VERSION: 1
    }
