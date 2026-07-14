import json

from services import market_report_status_service as service


def _read_json_file(path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def test_market_package_quality_response_reads_package_inputs(tmp_path):
    package_dir = tmp_path / "wiki" / "AAPL" / "package"
    read_calls = []

    def read_json_file(path, default):
        read_calls.append(path.relative_to(package_dir).as_posix())
        fixtures = {
            "manifest.json": {"filing_id": "AAPL-10K"},
            "qa/quality_report.json": {"overall_status": "pass"},
            "metrics/financial_checks.json": {"status": "warning"},
            "qa/source_map.json": {"entries": [{"evidence_id": "e1"}]},
        }
        return fixtures.get(path.relative_to(package_dir).as_posix(), default)

    payload = service.market_package_quality_response(
        package_dir,
        rel_or_abs=lambda path: f"rel::{path.name}",
        read_json_file=read_json_file,
        load_plan_for_package=lambda _path: {"can_import": True, "rows": [{"row": 1}]},
        quality_gates_with_load_plan=lambda _path: {"overall_status": "pass"},
        include_source_map_summary=True,
    )

    assert payload["package_path"] == "rel::package"
    assert payload["manifest"] == {"filing_id": "AAPL-10K"}
    assert payload["quality"] == {"overall_status": "pass"}
    assert payload["financial_checks"] == {"status": "warning"}
    assert payload["load_plan"]["row_count"] == 1
    assert payload["quality_gates"] == {"overall_status": "pass"}
    assert payload["source_map_summary"] == {"evidence": 1}
    assert read_calls == [
        "qa/source_map.json",
        "manifest.json",
        "qa/quality_report.json",
        "metrics/financial_checks.json",
    ]


def test_market_package_quality_response_omits_source_map_by_default(tmp_path):
    package_dir = tmp_path / "wiki" / "AAPL" / "package"
    read_calls = []

    def read_json_file(path, default):
        read_calls.append(path.relative_to(package_dir).as_posix())
        return default

    payload = service.market_package_quality_response(
        package_dir,
        rel_or_abs=lambda path: str(path),
        read_json_file=read_json_file,
        load_plan_for_package=lambda _path: {},
        quality_gates_with_load_plan=lambda _path: {},
    )

    assert "source_map_summary" not in payload
    assert read_calls == [
        "manifest.json",
        "qa/quality_report.json",
        "metrics/financial_checks.json",
    ]


def test_market_package_list_payload_filters_sorts_and_limits_packages():
    payload = service.market_package_list_payload(
        market_codes=["HK"],
        roots={"HK": "data/wiki/hk"},
        query="tencent",
        limit=1,
        package_summaries=[
            {
                "package_path": "data/wiki/hk/companies/00005-HSBC/reports/2024-annual",
                "market": "HK",
                "filing_id": "HK:00005:2024",
                "ticker": "00005",
                "company_name": "HSBC",
                "period_end": "2024-12-31",
            },
            {
                "package_path": "data/wiki/hk/companies/00700-TENCENT/reports/2024-annual",
                "market": "HK",
                "filing_id": "HK:00700:2024",
                "ticker": "00700",
                "company_name": "Tencent Holdings",
                "published_at": "2025-03-19",
            },
            {
                "package_path": "data/wiki/hk/companies/00700-TENCENT/reports/2025-annual",
                "market": "HK",
                "filing_id": "HK:00700:2025",
                "ticker": "00700",
                "company_name": "Tencent Holdings",
                "published_at": "2026-03-19",
            },
            "ignored",
        ],
    )

    assert payload == {
        "ok": True,
        "market": "HK",
        "markets": ["HK"],
        "roots": {"HK": "data/wiki/hk"},
        "count": 1,
        "packages": [
            {
                "package_path": "data/wiki/hk/companies/00700-TENCENT/reports/2025-annual",
                "market": "HK",
                "filing_id": "HK:00700:2025",
                "ticker": "00700",
                "company_name": "Tencent Holdings",
                "published_at": "2026-03-19",
            }
        ],
    }


def test_market_package_list_payload_keeps_multi_market_contract_and_clamps_limit():
    summaries = [
        {
            "package_path": f"data/wiki/hk/package-{index}",
            "market": "HK",
            "filing_id": f"HK:{index}",
            "period_end": f"2025-12-{index:04d}",
        }
        for index in range(1, 505)
    ]

    payload = service.market_package_list_payload(
        market_codes=["HK", "US"],
        package_summaries=summaries,
        roots={"HK": "data/wiki/hk", "US": "data/wiki/us_sec"},
        query="",
        limit=999,
    )

    assert payload["market"] is None
    assert payload["markets"] == ["HK", "US"]
    assert payload["roots"] == {"HK": "data/wiki/hk", "US": "data/wiki/us_sec"}
    assert payload["count"] == 500
    assert len(payload["packages"]) == 500
    assert payload["packages"][0]["filing_id"] == "HK:504"
    assert payload["packages"][-1]["filing_id"] == "HK:5"


def test_us_sec_package_detail_response_summarizes_metrics_evidence_and_preview(tmp_path):
    package_dir = tmp_path / "wiki" / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K"
    for relative in ("metrics", "qa", "tables", "sections", "raw", "xbrl"):
        (package_dir / relative).mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "filing_id": "US:AAPL:2025:10-K",
                "parser_result_dir": "parser-results/AAPL",
                "parser_result_task_id": "AAPL-10-K-demo",
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "qa" / "quality_report.json").write_text(
        json.dumps({"overall_status": "pass"}),
        encoding="utf-8",
    )
    (package_dir / "metrics" / "financial_data.json").write_text(
        json.dumps({"revenue": 391000}),
        encoding="utf-8",
    )
    (package_dir / "metrics" / "financial_checks.json").write_text(
        json.dumps(
            {
                "overall_status": "warning",
                "checks": [
                    {"rule_id": "bs.assets_equals_liabilities_plus_equity", "status": "pass"},
                    {"rule_id": "note.other", "rule_name": "cash bridge", "status": "warning"},
                    {"rule_id": "note.ignored", "rule_name": "shares", "status": "fail"},
                    "ignored",
                ],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "sections.json").write_text(
        json.dumps({"sections": [{"file": "financials.md"}, {"file": "risk.md"}]}),
        encoding="utf-8",
    )
    (package_dir / "tables" / "table_index.json").write_text(
        json.dumps({"tables": [{"table_index": 1}, {"table_index": 2}]}),
        encoding="utf-8",
    )
    (package_dir / "metrics" / "normalized_metrics.json").write_text(
        json.dumps(
            {
                "metrics": [
                    {"metric_id": "m1", "canonical_name": "revenue"},
                    {"metric_id": "m2", "canonical_name": "revenue_by_region", "dimensions": {"region": "US"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "xbrl" / "facts_raw.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "fact-segment",
                        "concept": "us-gaap:Revenue",
                        "label": "Revenue",
                        "value_numeric": "60",
                        "unit": "USD",
                        "period_start": "2024-09-29",
                        "period_end": "2025-09-27",
                        "context_ref": "c-segment",
                        "dimensions": {"srt:ProductOrServiceAxis": "aapl:IPhoneMember"},
                        "html_anchor": "f-segment",
                    },
                    {"fact_id": "fact-consolidated", "dimensions": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "qa" / "source_map.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"evidence_id": "e1"},
                    {
                        "evidence_id": "e2",
                        "source_type": "sec_xbrl_fact",
                        "html_anchor": "f-segment",
                        "target": "https://www.sec.gov/filing.htm#f-segment",
                        "raw": {"fact_id": "fact-segment"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "raw" / "filing.htm").write_text("<html>10-K</html>", encoding="utf-8")
    (package_dir / "sections" / "report_complete.md").write_text("# Annual report", encoding="utf-8")

    payload = service.us_sec_package_detail_response(
        package_dir,
        rel_or_abs=lambda path: f"rel::{path.name}",
        read_json_file=_read_json_file,
        quality_gates_for_package=lambda _path: {"overall_status": "pass", "import_blocked": False},
    )

    assert payload["package_path"] == "rel::2025-10-K"
    assert payload["parser_result_dir"] == "parser-results/AAPL"
    assert payload["parser_result_task_id"] == "AAPL-10-K-demo"
    assert payload["quality_gates"] == {"overall_status": "pass", "import_blocked": False}
    assert payload["financial_data"] == {"revenue": 391000}
    assert payload["bridge_checks"]["overall_status"] == "warning"
    assert payload["bridge_checks"]["summary"] == {"pass": 1, "warning": 1}
    assert [item["rule_id"] for item in payload["bridge_checks"]["checks"]] == [
        "bs.assets_equals_liabilities_plus_equity",
        "note.other",
    ]
    assert payload["counts"] == {
        "sections": 2,
        "tables": 2,
        "metrics": 2,
        "evidence": 2,
        "dimension_facts": 1,
        "dimension_metrics": 1,
    }
    assert payload["dimension_facts"] == [
        {
            "fact_id": "fact-segment",
            "concept": "us-gaap:Revenue",
            "label": "Revenue",
            "value": "60",
            "unit": "USD",
            "period": {"start": "2024-09-29", "end": "2025-09-27"},
            "context": "c-segment",
            "dimensions": {"srt:ProductOrServiceAxis": "aapl:IPhoneMember"},
            "anchor": "f-segment",
            "evidence": {
                "evidence_id": "e2",
                "source_type": "sec_xbrl_fact",
                "html_anchor": "f-segment",
                "target": "https://www.sec.gov/filing.htm#f-segment",
            },
        }
    ]
    assert payload["dimension_metrics"] == [
        {"metric_id": "m2", "canonical_name": "revenue_by_region", "dimensions": {"region": "US"}}
    ]
    assert payload["preview"] == {
        "raw_html": "raw/filing.htm",
        "default_markdown": "sections/report_complete.md",
    }
    assert payload["semantic_status"]["status"] == "missing"


def test_dimension_fact_samples_reports_full_count_and_caps_payload():
    facts = [
        {
            "fact_id": f"fact-{index}",
            "value_numeric": str(index),
            "dimensions": {"axis": f"member-{index}"},
        }
        for index in range(82)
    ]

    count, samples = service._dimension_fact_samples(facts, [], limit=80)

    assert count == 82
    assert len(samples) == 80
    assert samples[-1]["fact_id"] == "fact-79"


def test_us_sec_semantic_status_for_package_requires_real_rule_log(tmp_path):
    package_dir = tmp_path / "wiki" / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K"
    semantic_dir = package_dir.parent.parent / "semantic"
    semantic_dir.mkdir(parents=True)
    (package_dir.parent.parent / "company.json").write_text(
        json.dumps({"primary_report_id": "2025-10-K"}),
        encoding="utf-8",
    )
    for name in (
        "subject_profile.json",
        "segments.json",
        "facts.json",
        "relations.json",
        "claims.json",
        "retrieval_index.json",
        "note_links.json",
        "evidence_semantic.json",
    ):
        (semantic_dir / name).write_text(json.dumps({}), encoding="utf-8")
    (semantic_dir / "extraction_log.json").write_text(
        json.dumps({"schema_version": "us_semantic_extraction_log_v1", "steps": []}),
        encoding="utf-8",
    )

    placeholder = service.us_sec_semantic_status_for_package(package_dir, read_json_file=_read_json_file)
    assert placeholder["status"] == "missing"
    assert "占位" in placeholder["message"]

    (semantic_dir / "extraction_log.json").write_text(
        json.dumps({
            "inputs": {"company_json_sha256": "sha-company"},
            "counts": {"segments": 3, "facts": 1, "evidence": 4},
        }),
        encoding="utf-8",
    )
    ready = service.us_sec_semantic_status_for_package(package_dir, read_json_file=_read_json_file)
    assert ready["status"] == "ready"
    assert ready["counts"] == {"segments": 3, "facts": 1, "evidence": 4}


def test_us_sec_package_detail_response_tolerates_malformed_optional_files(tmp_path):
    package_dir = tmp_path / "wiki" / "companies" / "AAPL" / "reports" / "bad"
    (package_dir / "metrics").mkdir(parents=True)
    (package_dir / "qa").mkdir()
    (package_dir / "manifest.json").write_text(json.dumps(["bad"]), encoding="utf-8")
    (package_dir / "metrics" / "financial_checks.json").write_text(json.dumps({"checks": {"bad": "shape"}}), encoding="utf-8")
    (package_dir / "sections.json").write_text(json.dumps({"sections": {"bad": "shape"}}), encoding="utf-8")
    (package_dir / "metrics" / "normalized_metrics.json").write_text(json.dumps([]), encoding="utf-8")
    (package_dir / "qa" / "source_map.json").write_text(json.dumps({"entries": {"bad": "shape"}}), encoding="utf-8")

    payload = service.us_sec_package_detail_response(
        package_dir,
        rel_or_abs=lambda path: str(path),
        read_json_file=_read_json_file,
        quality_gates_for_package=lambda _path: {},
    )

    assert payload["parser_result_dir"] == ""
    assert payload["parser_result_task_id"] == ""
    assert payload["manifest"] == ["bad"]
    assert payload["bridge_checks"] == {"overall_status": None, "summary": {}, "checks": []}
    assert payload["counts"] == {
        "sections": 0,
        "tables": 0,
        "metrics": 0,
        "evidence": 0,
        "dimension_facts": 0,
        "dimension_metrics": 0,
    }
    assert payload["dimension_facts"] == []
    assert payload["preview"] == {"raw_html": "", "default_markdown": ""}


def test_market_document_full_status_payload_reports_paths_and_records_postgres_counts(tmp_path):
    document_root = tmp_path / "parser-results" / "hk"
    script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    document_root.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    script.write_text("# import", encoding="utf-8")
    recorded = []

    payload = service.market_document_full_status_payload(
        market_codes=["HK"],
        document_full_roots={"HK": document_root},
        import_scripts={"HK": script},
        market_databases={"HK": "siq_hk"},
        schemas={"HK": "pdf2md_hk"},
        rel_or_abs=lambda path: f"rel::{path.name}",
        db_status_for_market=lambda _code: {"status": "postgres_ready", "facts": 2},
        record_fact_counts=lambda code, status: recorded.append((code, status)),
    )

    assert payload == {
        "ok": True,
        "markets": {
            "HK": {
                "document_full_root": "rel::hk",
                "document_full_root_exists": True,
                "script": "rel::import_hk_document_full_to_postgres.py",
                "script_exists": True,
                "database": "siq_hk",
                "schema": "pdf2md_hk",
                "postgres": {"status": "postgres_ready", "facts": 2},
            }
        },
    }
    assert recorded == [("HK", {"status": "postgres_ready", "facts": 2})]


def test_market_document_full_status_payload_omits_empty_postgres_status(tmp_path):
    document_root = tmp_path / "parser-results" / "kr"
    script = tmp_path / "missing" / "import_kr.py"
    recorded = []

    payload = service.market_document_full_status_payload(
        market_codes=["KR"],
        document_full_roots={"KR": document_root},
        import_scripts={"KR": script},
        market_databases={},
        schemas={},
        rel_or_abs=lambda path: str(path),
        db_status_for_market=lambda _code: {},
        record_fact_counts=lambda code, status: recorded.append((code, status)),
    )

    assert payload["markets"]["KR"] == {
        "document_full_root": str(document_root),
        "document_full_root_exists": False,
        "script": str(script),
        "script_exists": False,
        "database": None,
        "schema": None,
    }
    assert recorded == []


def test_market_ingestion_eval_report_payload_includes_markdown_only_when_present():
    report = {"summary": {"passed": 2}}

    without_markdown = service.market_ingestion_eval_report_payload(
        report=report,
        report_path="eval/report.json",
        markdown_path="eval/report.md",
    )
    assert without_markdown == {
        "ok": True,
        "report_path": "eval/report.json",
        "markdown_path": "eval/report.md",
        "report": report,
    }

    with_markdown = service.market_ingestion_eval_report_payload(
        report={},
        report_path="eval/report.json",
        markdown_path="eval/report.md",
        markdown="# Eval",
    )
    assert with_markdown["ok"] is False
    assert with_markdown["markdown"] == "# Eval"


def test_market_package_quality_payload_keeps_optional_source_map_summary():
    base = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={"filing_id": "AAPL-10K"},
        quality={"overall_status": "pass"},
        financial_checks={"overall_status": "warning"},
    )
    with_source_map = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={"filing_id": "AAPL-10K"},
        quality={"overall_status": "pass"},
        financial_checks={"overall_status": "warning"},
        source_map={"entries": [{"evidence_id": "e1"}, {"evidence_id": "e2"}]},
        include_source_map_summary=True,
    )
    malformed_source_map = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={},
        quality={},
        financial_checks={},
        source_map={"entries": {"bad": "shape"}},
        include_source_map_summary=True,
    )

    assert base == {
        "ok": True,
        "package_path": "data/wiki/us_sec/AAPL/package",
        "manifest": {"filing_id": "AAPL-10K"},
        "quality": {"overall_status": "pass"},
        "financial_checks": {"overall_status": "warning"},
    }
    assert with_source_map["source_map_summary"] == {"evidence": 2}
    assert malformed_source_map["source_map_summary"] == {"evidence": 0}


def test_load_plan_summary_tolerates_malformed_shapes():
    assert service.load_plan_summary(None) == {}
    assert service.load_plan_summary({"rows": {"bad": "shape"}, "quarantine_rows": [], "blocked_reasons": "bad"}) == {
        "can_import": None,
        "can_vector_ingest": None,
        "blocked_reasons": [],
        "promotion_decisions": {},
        "row_count": 0,
        "quarantine_row_count": 0,
    }


def test_merge_load_plan_decision_into_gates_adds_hard_and_soft_rules():
    gates = {
        "overall_status": "warning",
        "import_blocked": False,
        "vector_ingest_blocked": False,
        "hard_gate_rule_ids": ["package.existing.hard"],
        "soft_gate_rule_ids": [],
        "force_allowed": False,
    }
    load_plan = {
        "can_import": False,
        "can_vector_ingest": False,
        "blocked_reasons": ["canonical blocked", "retrieval review"],
        "rows": [{"kind": "canonical"}],
        "quarantine_rows": [{"kind": "retrieval"}],
        "promotion_decisions": {
            "canonical": {"decision": "block"},
            "retrieval": {"decision": "review"},
        },
    }

    merged = service.merge_load_plan_decision_into_gates(gates, load_plan)

    assert gates["import_blocked"] is False
    assert merged["import_blocked"] is True
    assert merged["vector_ingest_blocked"] is True
    assert merged["load_plan"]["row_count"] == 1
    assert merged["load_plan"]["quarantine_row_count"] == 1
    assert "package.existing.hard" in merged["hard_gate_rule_ids"]
    assert "load_plan.canonical.block" in merged["hard_gate_rule_ids"]
    assert "load_plan.retrieval.review" in merged["soft_gate_rule_ids"]
    assert merged["force_allowed"] is False


def test_merge_load_plan_decision_allows_force_for_soft_only_review():
    merged = service.merge_load_plan_decision_into_gates(
        {
            "import_blocked": False,
            "vector_ingest_blocked": False,
            "hard_gate_rule_ids": [],
            "soft_gate_rule_ids": [],
            "force_allowed": False,
        },
        {
            "can_import": False,
            "can_vector_ingest": True,
            "promotion_decisions": {"canonical": {"decision": "review"}},
        },
    )

    assert merged["import_blocked"] is True
    assert merged["vector_ingest_blocked"] is False
    assert merged["hard_gate_rule_ids"] == []
    assert merged["soft_gate_rule_ids"] == ["load_plan.canonical.review"]
    assert merged["force_allowed"] is True


def test_latest_case_item_for_ticker_selects_latest_case_and_tolerates_malformed_inputs():
    case_set = {
        "items": [
            {
                "ticker": "AAPL",
                "filing_date": "2025-10-31",
                "period_end": "2025-09-27",
                "package_path": "old",
            },
            {
                "ticker": "msft",
                "filing_date": "2025-10-30",
                "period_end": "2025-06-30",
                "package_path": "ignored",
            },
            {
                "ticker": " aapl ",
                "filing_date": "2025-10-31",
                "period_end": "2025-12-31",
                "package_path": "latest-period",
            },
            {
                "ticker": "AAPL",
                "filing_date": "2026-01-15",
                "period_end": "",
                "package_path": "latest-filing",
            },
            "ignored",
        ],
    }

    assert service.latest_case_item_for_ticker(case_set, "aapl")["package_path"] == "latest-filing"
    assert service.latest_case_item_for_ticker(case_set, "MSFT")["package_path"] == "ignored"
    assert service.latest_case_item_for_ticker({"items": [{"ticker": " tsla ", "package_path": "padded"}]}, "TSLA")[
        "package_path"
    ] == "padded"
    assert service.latest_case_item_for_ticker(case_set, "TSLA") is None
    assert service.latest_case_item_for_ticker({"items": {"bad": "shape"}}, "AAPL") is None
    assert service.latest_case_item_for_ticker(["not-a-dict"], "AAPL") is None
    assert service.latest_case_item_for_ticker(case_set, " ") is None


def test_us_sec_case_set_status_payload_summarizes_quality_counts_and_ingest_report():
    payload = service.us_sec_case_set_status_payload(
        case_set={
            "items": [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "fiscal_year": 2025,
                    "period_end": "2025-09-27",
                    "filing_date": "2025-10-31",
                    "quality_status": "pass",
                    "retrieval_status": "ready",
                    "wiki_ready": True,
                    "retrieval_issues": [],
                    "quality_summary": {
                        "xbrl_fact_count": 10,
                        "normalized_metric_count": 4,
                        "section_count": 2,
                        "table_count": 3,
                    },
                    "package_path": "data/wiki/us_sec/AAPL/package",
                    "full_document_paths": {"document_full_path": "data/parser-results/us-sec/AAPL/document_full.json"},
                    "parser_result_dir": "data/parser-results/us-sec/AAPL",
                    "parser_result_task_id": "AAPL-10-K-demo",
                },
                {
                    "ticker": "MSFT",
                    "quality_status": "",
                    "quality_summary": {"xbrl_fact_count": 5},
                    "package_path": "data/wiki/us_sec/MSFT/package",
                },
                "ignored",
            ],
        },
        ingest_report={
            "generated_at": "2026-07-03T00:00:00Z",
            "summary": {"inserted": 7},
            "package_count": 2,
            "collection": "siq_documents",
            "batch_tag": "market-evidence",
            "extra": "ignored",
        },
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["case_set_path"] == "/tmp/case_set.json"
    assert payload["ingest_report_path"] == "/tmp/ingest_report.json"
    assert payload["company_count"] == 2
    assert payload["quality"] == {"pass": 1, "unknown": 1}
    assert payload["counts"] == {
        "xbrl_fact_count": 15,
        "normalized_metric_count": 4,
        "section_count": 2,
        "table_count": 3,
    }
    assert payload["items"] == [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "fiscal_year": 2025,
            "period_end": "2025-09-27",
            "filing_date": "2025-10-31",
            "quality_status": "pass",
            "retrieval_status": "ready",
            "wiki_ready": True,
            "retrieval_issues": [],
            "quality_summary": {
                "xbrl_fact_count": 10,
                "normalized_metric_count": 4,
                "section_count": 2,
                "table_count": 3,
            },
            "package_path": "data/wiki/us_sec/AAPL/package",
            "full_document_paths": {"document_full_path": "data/parser-results/us-sec/AAPL/document_full.json"},
            "parser_result_dir": "data/parser-results/us-sec/AAPL",
            "parser_result_task_id": "AAPL-10-K-demo",
        },
        {
            "ticker": "MSFT",
            "company_name": None,
            "fiscal_year": None,
            "period_end": None,
            "filing_date": None,
            "quality_status": "unknown",
            "retrieval_status": None,
            "wiki_ready": None,
            "retrieval_issues": [],
            "quality_summary": {"xbrl_fact_count": 5},
            "package_path": "data/wiki/us_sec/MSFT/package",
            "full_document_paths": {},
            "parser_result_dir": None,
            "parser_result_task_id": None,
        },
    ]
    assert payload["ingest_report"] == {
        "generated_at": "2026-07-03T00:00:00Z",
        "summary": {"inserted": 7},
        "package_count": 2,
        "collection": "siq_documents",
        "batch_tag": "market-evidence",
    }


def test_us_sec_case_set_status_payload_attaches_semantic_status():
    payload = service.us_sec_case_set_status_payload(
        case_set={
            "items": [
                {
                    "ticker": "AAPL",
                    "package_path": "data/wiki/us/companies/AAPL/reports/2025-10-K",
                    "quality_status": "pass",
                }
            ],
        },
        ingest_report={},
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
        semantic_status_for_item=lambda item: {
            "status": "ready",
            "counts": {"segments": 2, "evidence": 3},
            "package": item["package_path"],
        },
    )

    assert payload["items"][0]["semantic_status"] == {
        "status": "ready",
        "counts": {"segments": 2, "evidence": 3},
        "package": "data/wiki/us/companies/AAPL/reports/2025-10-K",
    }


def test_us_sec_case_set_status_payload_tolerates_malformed_inputs():
    payload = service.us_sec_case_set_status_payload(
        case_set={"items": {"not": "a-list"}},
        ingest_report=[],
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["company_count"] == 0
    assert payload["quality"] == {}
    assert payload["counts"] == {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 0,
        "table_count": 0,
    }
    assert payload["items"] == []
    assert payload["ingest_report"] == {}


def test_us_sec_case_set_status_payload_tolerates_bad_count_values():
    payload = service.us_sec_case_set_status_payload(
        case_set={
            "items": [
                {
                    "ticker": "AAPL",
                    "quality_status": "warning",
                    "quality_summary": {
                        "xbrl_fact_count": "n/a",
                        "normalized_metric_count": {"bad": "value"},
                        "section_count": "2",
                        "table_count": -1,
                    },
                },
            ],
        },
        ingest_report={},
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["company_count"] == 1
    assert payload["quality"] == {"warning": 1}
    assert payload["counts"] == {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 2,
        "table_count": 0,
    }
