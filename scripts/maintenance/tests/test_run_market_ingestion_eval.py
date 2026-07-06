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
    assert result["evidence_coverage_ratio"] == 0
    assert result["counts"]["unresolvable_evidence"] == 1
    assert "unresolvable_evidence_present" in result["gate_failures"]


def test_summarize_items_calculates_mvp_quality_metrics():
    module = _load_eval_module()

    summary = module.summarize_items(
        [
            {
                "status": "pass",
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
    assert metrics["official_source_hit_rate"] == 0.5
    assert metrics["parser_success_rate"] == 0.5
    assert round(metrics["evidence_coverage_ratio"], 2) == 0.6
    assert metrics["statement_coverage"] == 0.75
    assert metrics["bridge_check_pass_rate"] == 0.5
    assert metrics["answer_citation_rate"] == 0.5
    assert metrics["numeric_accuracy"] == 1.0
    assert metrics["hallucination_block_rate"] == 0.5
