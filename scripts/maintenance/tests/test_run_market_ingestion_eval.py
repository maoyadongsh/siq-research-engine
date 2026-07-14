import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_eval_module():
    source = Path(__file__).resolve().parents[1] / "run_market_ingestion_eval.py"
    spec = importlib.util.spec_from_file_location("run_market_ingestion_eval_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(package_dir: Path, payload: dict) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _artifact_hashes(package_dir: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        hashes[str(path.relative_to(package_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _write_complete_eval_package(package_dir: Path, *, market: str = "HK", ticker: str = "00700", filing_id: str = "HK:00700:12100024") -> None:
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text("# Eval fixture\n", encoding="utf-8")
    (package_dir / "raw" / "report.pdf").write_bytes(b"%PDF-1.4 test")
    raw_hash = hashlib.sha256((package_dir / "raw" / "report.pdf").read_bytes()).hexdigest()
    _write_json(package_dir / "tables" / "table_index.json", {"tables": [{"table_index": 1}]})
    _write_json(package_dir / "xbrl" / "facts_raw.json", {"facts": []})
    financial_data = {
        "statements": [
            {
                "statement_type": "income_statement",
                "items": [
                    {
                        "name": "Revenue",
                        "canonical_name": "operating_revenue",
                        "statement_type": "income_statement",
                        "values": {"2025-12-31": 100},
                        "raw_values": {"2025-12-31": "100"},
                        "sources": {
                            "2025-12-31": {
                                "source_type": "pdf_statement_table",
                                "source_id": "table_1",
                                "page_number": 12,
                                "table_index": 1,
                                "row_index": 2,
                                "column_index": 1,
                            }
                        },
                    }
                ],
            }
        ],
        "key_metrics": [],
        "operating_metrics": [],
    }
    _write_json(package_dir / "metrics" / "financial_data.json", financial_data)
    _write_json(package_dir / "metrics" / "financial_checks.json", {"overall_status": "pass", "summary": {"pass": 4}})
    _write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": [{"canonical_name": "operating_revenue"}]})
    _write_json(
        package_dir / "qa" / "source_map.json",
        {
            "entries": [
                {
                    "evidence_id": "e1",
                    "page_number": 12,
                    "table_index": 1,
                    "row_index": 2,
                    "column_index": 1,
                    "target": "page=12;table=1;row=2;column=1",
                }
            ]
        },
    )
    _write_json(
        package_dir / "qa" / "quality_report.json",
        {
            "overall_status": "pass",
            "table_count": 6,
            "raw_fact_count": 1,
            "normalized_metric_count": 1,
            "evidence_coverage_ratio": 1.0,
            "required_statement_status": {
                "income_statement": "present",
                "balance_sheet": "present",
                "cash_flow_statement": "present",
            },
        },
    )
    manifest = {
        "schema_version": "market_evidence_package_v1",
        "market": market,
        "filing_id": filing_id,
        "ticker": ticker,
        "report_type": "annual",
        "fiscal_year": 2025,
        "quality_status": "pass",
        "source_id": "hkex",
        "source_tier": "official_regulator",
        "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900000.pdf",
        "source_manifest": {
            "schema_version": "siq_source_manifest_v1",
            "source_tier": "official_regulator",
            "source_verification_status": "official_verified",
            "initial_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900000.pdf",
            "final_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900000.pdf",
            "redirect_chain": [],
            "content_sha256": raw_hash,
            "retrieved_at": "2026-04-09T00:00:00Z",
        },
        "artifact_hashes": {},
    }
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)


def test_find_package_uses_hk_company_wiki_layout(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_manifest(
        package_dir,
        {"schema_version": "market_evidence_package_v1", "market": "HK", "ticker": "00700", "fiscal_year": 2025, "report_type": "annual"},
    )
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    found = module.find_package({"market": "HK", "ticker": "00700", "fiscal_year": 2025, "report_type": "annual"})

    assert found == package_dir


def test_find_package_uses_jp_company_wiki_layout(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "jp"
    package_dir = root / "companies" / "7203-Toyota-Motor-Corporation" / "reports" / "2025-annual-securities-report"
    _write_manifest(
        package_dir,
        {"schema_version": "market_evidence_package_v1", "market": "JP", "ticker": "7203", "fiscal_year": 2025, "report_type": "annual_securities_report"},
    )
    monkeypatch.setitem(module.WIKI_ROOTS, "JP", root)

    found = module.find_package({"market": "JP", "ticker": "7203", "fiscal_year": 2025, "report_type": "annual_securities_report"})

    assert found == package_dir


def test_find_package_accepts_current_market_annual_report_aliases(tmp_path, monkeypatch):
    module = _load_eval_module()
    fixtures = [
        ("HK", "hk", "00700", "年报", "annual_report"),
        ("EU", "eu", "SAP", "年报", "eu_esef_annual_report"),
        ("JP", "jp", "7203", "年报", "jp_annual_securities_report"),
        ("KR", "kr", "005930", "年报", "kr_business_report"),
    ]

    for market, root_key, ticker, report_type, form in fixtures:
        root = tmp_path / "data" / "wiki" / root_key
        package_dir = root / "companies" / f"{ticker}-Company" / "reports" / "2025-annual"
        _write_manifest(
            package_dir,
            {
                "schema_version": "market_evidence_package_v1",
                "market": market,
                "ticker": ticker,
                "fiscal_year": 2025,
                "report_type": report_type,
                "form": form,
            },
        )
        monkeypatch.setitem(module.WIKI_ROOTS, market, root)

        found = module.find_package({"market": market, "ticker": ticker, "fiscal_year": 2025, "report_type": "annual"})

        assert found == package_dir


def test_find_package_accepts_kr_pdf_wiki_report_year(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "kr"
    package_dir = root / "companies" / "005930-SamsungElectronics" / "reports" / "2025-annual-task-kr"
    _write_manifest(
        package_dir,
        {"package_schema": "market_evidence_package_v1", "market": "KR", "ticker": "005930", "report_year": 2025, "report_type": "annual"},
    )
    monkeypatch.setitem(module.WIKI_ROOTS, "KR", root)

    found = module.find_package({"market": "KR", "ticker": "005930", "fiscal_year": 2025, "report_type": "annual"})

    assert found == package_dir


def test_find_package_requires_requested_document_format(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "jp"
    package_dir = root / "companies" / "7203-Toyota" / "reports" / "2025-annual"
    _write_manifest(
        package_dir,
        {
            "schema_version": "market_evidence_package_v1",
            "market": "JP",
            "ticker": "7203",
            "fiscal_year": 2025,
            "report_type": "annual",
            "document_format": "pdf",
        },
    )
    monkeypatch.setitem(module.WIKI_ROOTS, "JP", root)

    case = {
        "market": "JP",
        "ticker": "7203",
        "fiscal_year": 2025,
        "report_type": "annual",
        "document_format": "xbrl",
    }

    assert module.find_package(case) is None
    result = module.evaluate_case(case)
    assert result["package_resolution"] == "wrong_document_format"
    assert result["package_candidates"] == []
    assert result["package_format_mismatches"] == [
        {
            "package_path": str(package_dir),
            "expected_document_format": "xbrl",
            "observed_document_formats": ["pdf"],
        }
    ]


def test_find_package_derives_pdf_format_from_raw_artifact(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    _write_manifest(
        package_dir,
        {
            "schema_version": "market_evidence_package_v1",
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "artifact_hashes": {"raw/report.pdf": "a" * 64},
        },
    )
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    found = module.find_package(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "document_format": "pdf",
        }
    )

    assert found == package_dir


def test_find_package_fails_closed_when_multiple_packages_match(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    case = {"market": "HK", "ticker": "00700", "fiscal_year": 2025, "report_type": "annual"}
    for suffix in ("canonical", "legacy"):
        package_dir = root / "companies" / "00700-TENCENT" / "reports" / f"2025-annual-{suffix}"
        _write_manifest(
            package_dir,
            {
                "schema_version": "market_evidence_package_v1",
                "market": "HK",
                "ticker": "00700",
                "fiscal_year": 2025,
                "report_type": "annual",
            },
        )
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    assert module.find_package(case) is None
    assert len(module.find_package_candidates(case)) == 2

    result = module.evaluate_case(case)
    assert result["status"] == "missing_package"
    assert result["package_resolution"] == "ambiguous"
    assert len(result["package_candidates"]) == 2


def test_evaluate_case_treats_null_metrics_as_empty_list(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "jp"
    package_dir = root / "companies" / "7203-Toyota-Motor-Corporation" / "reports" / "2025-annual-securities-report"
    _write_manifest(
        package_dir,
        {"schema_version": "market_evidence_package_v1", "market": "JP", "ticker": "7203", "fiscal_year": 2025, "report_type": "annual_securities_report"},
    )
    metrics_dir = package_dir / "metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "normalized_metrics.json").write_text(json.dumps({"metrics": None}), encoding="utf-8")
    monkeypatch.setitem(module.WIKI_ROOTS, "JP", root)

    result = module.evaluate_case(
        {
            "market": "JP",
            "ticker": "7203",
            "fiscal_year": 2025,
            "report_type": "annual_securities_report",
            "expected_metrics": ["operating_revenue"],
        }
    )

    assert result["status"] == "fail"
    assert result["counts"]["metrics"] == 0
    assert result["missing_metrics"] == ["operating_revenue"]


def test_expected_metric_aliases_are_explicit_and_directional():
    module = _load_eval_module()

    assert module._missing_expected_metrics(
        ["operating_cash_flow"],
        {"operating_cash_flow_net"},
    ) == []
    assert module._missing_expected_metrics(
        ["operating_cash_flow"],
        {"free_cash_flow"},
    ) == ["operating_cash_flow"]
    assert module._missing_expected_metrics(
        ["operating_cash_flow_net"],
        {"operating_cash_flow"},
    ) == ["operating_cash_flow_net"]


def test_evaluate_case_accepts_tencent_operating_cash_flow_alias(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    _write_json(
        package_dir / "metrics" / "normalized_metrics.json",
        {"metrics": [{"canonical_name": "operating_cash_flow_net"}]},
    )
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_cash_flow"],
            "expected_evidence": True,
        }
    )

    assert result["status"] == "pass"
    assert result["missing_metrics"] == []


def test_evaluate_case_emits_mvp_quality_metrics(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_evidence": True,
            "quality_thresholds": {
                "evidence_coverage_ratio": 1.0,
                "evidence_resolvability_ratio": 1.0,
                "statement_coverage": 1.0,
                "bridge_check_pass_rate": 1.0,
            },
        }
    )

    assert result["status"] == "pass"
    assert result["eval_gate_status"] == "pass"
    assert result["artifact_hash_status"] == "ok"
    assert result["counts"]["resolvable_evidence"] == 1
    assert result["evidence_coverage_ratio"] == 1.0
    assert result["evidence_resolvability_ratio"] == 1.0
    assert result["statement_coverage"] == 1.0
    assert result["bridge_check_pass_rate"] == 1.0


def test_default_mvp_dataset_covers_all_secondary_markets():
    module = _load_eval_module()

    cases = module.load_cases(module.CASE_ROOT, legacy_case_root=None)
    markets = {case["market"] for case in cases}

    assert {"HK", "EU", "JP", "KR", "US"}.issubset(markets)


def test_default_mvp_dataset_covers_negative_expectations_and_gate_statuses():
    module = _load_eval_module()

    cases = module.load_cases(module.CASE_ROOT, legacy_case_root=None)
    gate_statuses = {case.get("expected_gate_status") for case in cases}
    negative_types = {
        expectation.get("type")
        for case in cases
        for expectation in case.get("negative_expectations") or []
        if isinstance(expectation, dict)
    }
    features_by_market = {}
    for case in cases:
        features_by_market.setdefault(case["market"], set()).update(case.get("representative_features") or [])

    assert {"pass", "review", "block"}.issubset(gate_statuses)
    assert {
        "missing_required_statements",
        "currency_mismatch",
        "period_mismatch",
        "hash_mismatch",
        "unverified_official_source",
    }.issubset(negative_types)
    assert "hk_bank_annual_report" in features_by_market["HK"]
    assert "esef_ixbrl" in features_by_market["EU"]
    assert {"10-k", "10-q", "20-f", "dimension_fact", "segment_fact"}.issubset(features_by_market["US"])
    assert {"edinet_ifrs", "edinet_jgaap"}.issubset(features_by_market["JP"])
    assert "dart_k_ifrs" in features_by_market["KR"]


def test_eval_gate_fails_unresolvable_evidence(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    financial_data = json.loads(data_path.read_text(encoding="utf-8"))
    financial_data["statements"][0]["items"][0]["sources"]["2025-12-31"] = {
        "source_type": "pdf_statement_table",
        "source_id": "table_1",
        "table_index": 1,
    }
    _write_json(data_path, financial_data)
    _write_json(package_dir / "qa" / "source_map.json", {"entries": [{"evidence_id": "e1", "table_index": 1}]})
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_evidence": True,
        }
    )

    assert result["status"] == "fail"
    assert result["eval_gate_status"] == "block"
    assert result["evidence_coverage_ratio"] == 0
    assert result["counts"]["unresolvable_evidence"] == 1
    assert "unresolvable_evidence_present" in result["gate_failures"]


def test_eval_gate_blocks_missing_required_statements(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    quality_path = package_dir / "qa" / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["required_statement_status"] = {
        "income_statement": "missing",
        "balance_sheet": "missing",
        "cash_flow_statement": "missing",
    }
    _write_json(quality_path, quality)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_statements": ["income_statement", "balance_sheet", "cash_flow_statement"],
            "quality_thresholds": {"statement_coverage": 1.0},
        }
    )

    assert result["status"] == "fail"
    assert result["eval_gate_status"] == "block"
    assert result["statement_coverage"] == 0
    assert "statement_coverage_lt_1_0" in result["gate_failures"]


def test_eval_gate_blocks_currency_and_period_mismatch(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["reporting_currency"] = "USD"
    manifest["period_end"] = "2024-12-31"
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_currency": "HKD",
            "period_end": "2025-12-31",
        }
    )

    assert result["status"] == "fail"
    assert result["eval_gate_status"] == "block"
    assert "currency_mismatch_expected_hkd" in result["gate_failures"]
    assert "period_end_mismatch_expected_2025-12-31" in result["gate_failures"]


def test_eval_gate_blocks_explicit_unit_currency_conflict(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    financial_data = json.loads(data_path.read_text(encoding="utf-8"))
    statement = financial_data["statements"][0]
    statement.update({"unit": "人民币百万元", "currency": "HKD"})
    statement["items"][0].update({"unit": "RMB million", "currency": "HKD"})
    _write_json(data_path, financial_data)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
        }
    )

    assert result["status"] == "fail"
    assert result["eval_gate_status"] == "block"
    assert "fact_currency_unit_mismatch" in result["gate_failures"]
    assert result["currency_unit_consistency"]["mismatch_count"] == 2
    assert {item["unit_currency"] for item in result["currency_unit_consistency"]["mismatches"]} == {"CNY"}


def test_eval_gate_accepts_hk_company_reporting_in_cny(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    financial_data = json.loads(data_path.read_text(encoding="utf-8"))
    statement = financial_data["statements"][0]
    statement.update({"unit": "人民币百万元", "currency": "CNY"})
    statement["items"][0].update({"unit": "RMB million", "currency": "CNY"})
    _write_json(data_path, financial_data)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["reporting_currency"] = "CNY"
    manifest["artifact_hashes"] = _artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_currency": "CNY",
            "expected_metrics": ["operating_revenue"],
        }
    )

    assert result["status"] == "pass"
    assert result["currency_unit_consistency"] == {"passed": True, "mismatch_count": 0, "mismatches": []}


def test_eval_gate_blocks_artifact_hash_mismatch(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    (package_dir / "README.md").write_text("# Tampered fixture\n", encoding="utf-8")
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
        }
    )

    assert result["status"] == "fail"
    assert result["eval_gate_status"] == "block"
    assert result["artifact_hash_status"] == "mismatch"
    assert "artifact_hash_mismatch" in result["gate_failures"]


def test_bridge_check_pass_rate_ignores_skipped_and_warning_checks():
    module = _load_eval_module()

    assert module._bridge_check_pass_rate({"summary": {"pass": 8, "warning": 2, "skipped": 10, "fail": 0}}) == 1.0
    assert module._bridge_check_pass_rate({"summary": {"pass": 8, "warning": 2, "skipped": 10, "fail": 2}}) == 0.8


def test_eval_gate_reviews_unverified_official_source(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "source_id": "hkex",
            "source_tier": "official",
            "source_url": "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0410/report.pdf",
            "source_verification_status": "unverified",
        }
    )
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_official_source": True,
            "expected_gate_status": "review",
            "negative_expectations": [
                {
                    "type": "unverified_official_source",
                    "expected_failures": ["official_source_unverified"],
                }
            ],
        }
    )

    assert result["eval_gate_status"] == "review"
    assert result["gate_status_matches_expected"] is True
    assert result["status"] == "pass"
    assert result["expectation_passed"] is True
    assert result["negative_expectations_matched"] is True
    assert result["missing_expected_failures"] == []
    assert "official_source_unverified" in result["gate_failures"]


def test_negative_eval_case_requires_declared_failure_to_pass(tmp_path, monkeypatch):
    module = _load_eval_module()
    root = tmp_path / "data" / "wiki" / "hk"
    package_dir = root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_complete_eval_package(package_dir)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "source_id": "hkex",
            "source_tier": "official",
            "source_url": "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0410/report.pdf",
            "source_verification_status": "unverified",
        }
    )
    _write_json(package_dir / "manifest.json", manifest)
    monkeypatch.setitem(module.WIKI_ROOTS, "HK", root)

    result = module.evaluate_case(
        {
            "market": "HK",
            "ticker": "00700",
            "fiscal_year": 2025,
            "report_type": "annual",
            "expected_metrics": ["operating_revenue"],
            "expected_official_source": True,
            "expected_gate_status": "review",
            "negative_expectations": [
                {
                    "type": "period_mismatch",
                    "expected_failures": ["period_end_mismatch_expected_2025-12-31"],
                }
            ],
        }
    )

    assert result["eval_gate_status"] == "review"
    assert result["gate_status_matches_expected"] is True
    assert result["status"] == "fail"
    assert result["expectation_passed"] is False
    assert result["negative_expectations_matched"] is False
    assert result["missing_expected_failures"] == ["period_end_mismatch_expected_2025-12-31"]


def test_summarize_items_calculates_mvp_quality_metrics():
    module = _load_eval_module()

    summary = module.summarize_items(
        [
            {
                "status": "pass",
                "eval_gate_status": "pass",
                "market": "HK",
                "quality_gates": {"official_evidence_allowed": True},
                "expected_official_source": True,
                "evidence_coverage_ratio": 0.8,
                "statement_coverage": 1.0,
                "bridge_check_pass_rate": 1.0,
                "answer_evals": [
                    {"has_valid_citation": True, "numeric_correct": True, "hallucination_blocked": True},
                    {"has_valid_citation": False, "numeric_correct": True, "hallucination_blocked": False},
                ],
            },
            {
                "status": "missing_package",
                "eval_gate_status": "block",
                "market": "HK",
                "source_tier": "unknown",
                "evidence_coverage_ratio": 0.4,
                "statement_coverage": 0.5,
                "bridge_check_pass_rate": 0.0,
            },
        ]
    )

    metrics = summary["quality_metrics"]
    assert summary["cases"] == 2
    assert summary["eval_gate_status"] == {"pass": 1, "review": 0, "block": 1}
    assert metrics["official_source_hit_rate"] == 0.5
    assert metrics["expected_official_source_rate"] == 0.5
    assert metrics["parser_success_rate"] == 0.5
    assert round(metrics["evidence_coverage_ratio"], 2) == 0.6
    assert metrics["statement_coverage"] == 0.75
    assert metrics["bridge_check_pass_rate"] == 0.5
    assert metrics["answer_citation_rate"] == 0.5
    assert metrics["numeric_accuracy"] == 1.0
    assert metrics["hallucination_block_rate"] == 0.5


def test_strict_failure_reasons_use_expectation_status_not_raw_block_count():
    module = _load_eval_module()

    reasons = module.strict_failure_reasons(
        {
            "cases": 7,
            "fail": 2,
            "missing_package": 1,
            "eval_gate_status": {"pass": 3, "review": 1, "block": 4},
        }
    )

    assert reasons == [
        "summary.fail=2",
        "summary.missing_package=1",
    ]


def test_strict_failure_reasons_allow_expected_negative_block_cases():
    module = _load_eval_module()

    assert module.strict_failure_reasons(
        {
            "cases": 1,
            "pass": 1,
            "fail": 0,
            "missing_package": 0,
            "eval_gate_status": {"pass": 0, "review": 0, "block": 1},
        }
    ) == []


def test_summary_uses_expectation_passed_without_lowering_raw_gate_status():
    module = _load_eval_module()

    summary = module.summarize_items(
        [
            {
                "status": "pass",
                "expectation_passed": False,
                "eval_gate_status": "block",
                "market": "HK",
            },
            {
                "status": "pass",
                "expectation_passed": True,
                "eval_gate_status": "review",
                "market": "KR",
            },
        ]
    )

    assert summary["pass"] == 1
    assert summary["fail"] == 1
    assert summary["eval_gate_status"] == {"pass": 0, "review": 1, "block": 1}


def test_summary_distinguishes_missing_wrong_format_and_ambiguous_packages():
    module = _load_eval_module()

    summary = module.summarize_items(
        [
            {"status": "missing_package", "package_resolution": "missing", "market": "EU"},
            {"status": "missing_package", "package_resolution": "wrong_document_format", "market": "JP"},
            {"status": "missing_package", "package_resolution": "ambiguous", "market": "HK"},
        ]
    )

    assert summary["missing_package"] == 3
    assert summary["package_resolution"] == {
        "missing": 1,
        "wrong_document_format": 1,
        "ambiguous": 1,
    }


def test_strict_failure_reasons_reject_empty_case_set():
    module = _load_eval_module()

    assert module.strict_failure_reasons(
        {"cases": 0, "fail": 0, "missing_package": 0, "eval_gate_status": {"block": 0}}
    ) == ["summary.cases=0"]


def test_named_final_v5_profile_resolves_staging_root_without_weakening_source_contract():
    module = _load_eval_module()

    name, root, roots = module.resolve_evidence_profile("final-v5-staging", wiki_root=None)

    assert name == "final-v5-staging"
    assert root == module.FINAL_V5_STAGING_WIKI_ROOT
    assert roots["HK"] == module.FINAL_V5_STAGING_WIKI_ROOT / "hk"
    assert roots["US"] == module.FINAL_V5_STAGING_WIKI_ROOT / "us"
    assert module.OFFICIAL_SOURCE_CONTRACT["required_for_expected_official_source"] is True
    assert module.OFFICIAL_SOURCE_CONTRACT["fail_closed"] is True
    assert "retrieved_at" in module.OFFICIAL_SOURCE_CONTRACT["required_fields"]


def test_named_profile_rejects_ambiguous_custom_wiki_root(tmp_path):
    module = _load_eval_module()

    with pytest.raises(SystemExit, match="cannot be combined"):
        module.resolve_evidence_profile("final-v5-staging", wiki_root=tmp_path)


def test_strict_main_uses_portable_wiki_root(tmp_path, capsys):
    module = _load_eval_module()
    case_root = tmp_path / "cases"
    wiki_root = tmp_path / "wiki"
    package_dir = wiki_root / "hk" / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    _write_complete_eval_package(package_dir)
    _write_json(
        case_root / "contract_cases.json",
        [
            {
                "market": "HK",
                "ticker": "00700",
                "fiscal_year": 2025,
                "report_type": "annual",
                "expected_metrics": ["operating_revenue"],
                "expected_evidence": True,
                "expected_gate_status": "pass",
            }
        ],
    )
    output = tmp_path / "report.json"

    exit_code = module.main(
        [
            "--case-root",
            str(case_root),
            "--legacy-case-root",
            str(case_root),
            "--wiki-root",
            str(wiki_root),
            "--output",
            str(output),
            "--markdown",
            str(tmp_path / "report.md"),
            "--strict",
        ]
    )

    assert exit_code == 0
    assert "FAIL market ingestion eval strict gate" not in capsys.readouterr().err
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["wiki_root"] == str(wiki_root)
    assert report["evidence_profile"] == "custom-wiki-root"
    assert report["official_source_contract"]["fail_closed"] is True
    assert report["passed"] is True
    assert report["failure_reasons"] == []
    assert report["base_commit"]
    assert isinstance(report["worktree_dirty"], bool)
    assert report["worktree_summary"]["available"] is True
    assert report["task_id"] == "T10"
    assert report["environment_profile"] == "local-evaluation"
    assert report["result"] == "pass"
    assert report["duration_seconds"] >= 0
    assert report["failures"] == []
    assert report["artifact_checksums"]
    assert all(len(value) == 64 for value in report["artifact_checksums"].values())
    assert str(tmp_path) not in report["command"]
    assert report["summary"]["pass"] == 1
    assert report["items"][0]["package_path"] == str(package_dir)


def test_market_wiki_root_override_replaces_only_requested_market(tmp_path, capsys):
    module = _load_eval_module()
    case_root = tmp_path / "cases"
    hk_root = tmp_path / "staging-hk"
    package_dir = hk_root / "companies" / "00700-TENCENT" / "reports" / "2025-annual"
    _write_complete_eval_package(package_dir)
    _write_json(
        case_root / "contract_cases.json",
        [
            {
                "market": "HK",
                "ticker": "00700",
                "fiscal_year": 2025,
                "report_type": "annual",
                "expected_metrics": ["operating_revenue"],
                "expected_evidence": True,
                "expected_gate_status": "pass",
            }
        ],
    )
    output = tmp_path / "report.json"

    exit_code = module.main(
        [
            "--case-root",
            str(case_root),
            "--legacy-case-root",
            str(case_root),
            "--market-wiki-root",
            f"HK={hk_root}",
            "--output",
            str(output),
            "--markdown",
            str(tmp_path / "report.md"),
            "--strict",
        ]
    )

    assert exit_code == 0
    assert "FAIL market ingestion eval strict gate" not in capsys.readouterr().err
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["wiki_roots"]["HK"] == str(hk_root)
    assert report["wiki_roots"]["US"] == str(module.WIKI_ROOTS["US"])
    assert report["items"][0]["package_path"] == str(package_dir)


def test_portable_report_redacts_paths_outside_repo(tmp_path):
    module = _load_eval_module()
    payload = {
        "inside": str(module.REPO_ROOT / "data" / "wiki" / "hk"),
        "outside": str(tmp_path / "wiki"),
        "source_url": "https://example.com/report.pdf",
    }

    observed = module._portable_report_value(payload)

    assert observed == {
        "inside": "data/wiki/hk",
        "outside": "<external>",
        "source_url": "https://example.com/report.pdf",
    }


def test_strict_main_returns_nonzero_for_missing_package(tmp_path, capsys):
    module = _load_eval_module()
    case_root = tmp_path / "cases"
    _write_json(
        case_root / "hk_cases.json",
        [
            {
                "market": "HK",
                "ticker": "99999",
                "fiscal_year": 2099,
                "report_type": "annual",
            }
        ],
    )
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"

    exit_code = module.main(
        [
            "--case-root",
            str(case_root),
            "--legacy-case-root",
            str(case_root),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--strict",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "summary.missing_package=1" in captured.err
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["missing_package"] == 1
    assert report["items"][0]["expectation_passed"] is False
    assert report["items"][0]["gate_status_matches_expected"] is None
    assert markdown.exists()


def test_non_strict_main_keeps_report_generation_advisory(tmp_path, capsys):
    module = _load_eval_module()
    case_root = tmp_path / "cases"
    _write_json(
        case_root / "hk_cases.json",
        [
            {
                "market": "HK",
                "ticker": "99998",
                "fiscal_year": 2099,
                "report_type": "annual",
            }
        ],
    )

    exit_code = module.main(
        [
            "--case-root",
            str(case_root),
            "--legacy-case-root",
            str(case_root),
            "--output",
            str(tmp_path / "report.json"),
            "--markdown",
            str(tmp_path / "report.md"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "FAIL market ingestion eval strict gate" not in captured.err
