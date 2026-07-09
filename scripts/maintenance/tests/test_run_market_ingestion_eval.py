import importlib.util
import hashlib
import json
from pathlib import Path


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
        }
    )

    assert result["eval_gate_status"] == "review"
    assert result["gate_status_matches_expected"] is True
    assert "official_source_unverified" in result["gate_failures"]


def test_summarize_items_calculates_mvp_quality_metrics():
    module = _load_eval_module()

    summary = module.summarize_items(
        [
            {
                "status": "pass",
                "eval_gate_status": "pass",
                "market": "HK",
                "source_tier": "official",
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
    assert metrics["parser_success_rate"] == 0.5
    assert round(metrics["evidence_coverage_ratio"], 2) == 0.6
    assert metrics["statement_coverage"] == 0.75
    assert metrics["bridge_check_pass_rate"] == 0.5
    assert metrics["answer_citation_rate"] == 0.5
    assert metrics["numeric_accuracy"] == 1.0
    assert metrics["hallucination_block_rate"] == 0.5
