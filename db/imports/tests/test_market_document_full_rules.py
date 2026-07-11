import importlib.util
import sys
from pathlib import Path


def _load_module(name: str, rel: str):
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    spec = importlib.util.find_spec(rel.removesuffix(".py").replace("/", "."))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_rows(market: str, document_full: dict, tmp_path: Path):
    common = _load_module("market_document_full_common", "market_document_full_rules/common.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    registry = _load_module("market_document_full_registry", "market_document_full_rules/registry.py")
    path = tmp_path / "document_full.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    context = base.MarketDocumentFullContext(
        market=market,
        document_full_path=path,
        document_full_sha256="a" * 64,
        source_root=tmp_path,
    )
    return registry.rule_for_market(market).build_rows(document_full, context)


def test_market_document_full_rules_have_independent_market_modules(tmp_path):
    registry = _load_module("market_document_full_registry", "market_document_full_rules/registry.py")

    cases = {
        "HK": "HKDocumentFullRule",
        "JP": "JPDocumentFullRule",
        "KR": "KRDocumentFullRule",
        "EU": "EUDocumentFullRule",
        "US": "USSecDocumentFullRule",
    }
    for market, class_name in cases.items():
        rule = registry.rule_for_market(market)
        assert rule.__class__.__name__ == class_name
        assert rule.market == market


def test_market_document_full_wrappers_pin_default_market():
    imports_dir = Path(__file__).resolve().parents[1]
    cases = {
        "import_hk_document_full_to_postgres.py": "main(\"HK\")",
        "import_jp_document_full_to_postgres.py": "main(\"JP\")",
        "import_kr_document_full_to_postgres.py": "main(\"KR\")",
        "import_eu_document_full_to_postgres.py": "main(\"EU\")",
        "import_us_sec_document_full_to_postgres.py": "main(\"US\")",
    }

    for filename, expected in cases.items():
        assert expected in (imports_dir / filename).read_text(encoding="utf-8")


def test_market_document_full_importer_rejects_single_file_market_mismatch(tmp_path):
    importer = _load_module("market_document_full_importer", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "document_full.json"
    document_full.write_text('{"metadata":{"market":"JP"}}', encoding="utf-8")

    try:
        importer.import_document_full(document_full, market="HK")
    except importer.MarketMismatchError as exc:
        assert "Requested market HK does not match document_full market JP" in str(exc)
    else:
        raise AssertionError("expected market mismatch")


def test_market_document_full_importer_rejects_package_directory_without_recursive_scan(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_package_guard", "import_market_document_full_to_postgres.py")
    package_dir = tmp_path / "wiki" / "hk" / "companies" / "00700-Tencent" / "reports" / "2025-annual"
    (package_dir / "metrics").mkdir(parents=True)
    (package_dir / "qa").mkdir()
    (package_dir / "metrics" / "financial_data.json").write_text('{"statements":[{"items":[{"value":1}]}]}', encoding="utf-8")
    (package_dir / "qa" / "source_map.json").write_text('{"entries":[{"evidence_id":"e1"}]}', encoding="utf-8")

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("package directory should fail before connecting")

    monkeypatch.setattr(importer, "connect", fail_connect)

    try:
        importer.import_document_full(package_dir, market="HK")
    except SystemExit as exc:
        assert "pass a document_full.json file" in str(exc)
    else:
        raise AssertionError("expected directory input to be rejected unless it contains/points to document_full.json")


def test_market_document_full_importer_reads_only_document_full_from_package_dir(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_document_full_only", "import_market_document_full_to_postgres.py")
    base = _load_module("market_document_full_base_document_full_only", "market_document_full_rules/base.py")
    package_dir = tmp_path / "wiki" / "hk" / "companies" / "00700-Tencent" / "reports" / "2025-annual"
    document_full = package_dir / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"HK"}}', encoding="utf-8")
    for rel_path in (
        "metrics/financial_data.json",
        "metrics/financial_checks.json",
        "qa/source_map.json",
        "qa/quality_report.json",
        "tables/table_index.json",
        "xbrl/facts_raw.json",
    ):
        path = package_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")

    original_read_json = importer.read_json
    read_paths = []

    def read_json_guard(path):
        resolved = Path(path).resolve()
        read_paths.append(resolved)
        assert resolved == document_full.resolve()
        return original_read_json(path)

    class FakeRule:
        def build_rows(self, _document_full, context):
            assert context.document_full_path == document_full.resolve()
            assert context.source_root == package_dir.resolve()
            return base.MarketDocumentFullRows(
                company={"company_id": "HK:00700", "ticker": "00700"},
                filing={"filing_id": "HK:f1", "company_id": "HK:00700"},
                parse_run={"parse_run_id": "parse-document-full-only"},
                statement_items=[
                    {
                        "item_uid": "item-1",
                        "statement_type": "income_statement",
                        "canonical_name": "revenue",
                        "value": "100",
                    }
                ],
                chunks=[{"chunk_uid": "chunk-1", "text": "Revenue 100"}],
                citations=[
                    {
                        "evidence_id": "ev-1",
                        "source_type": "table_cell",
                        "table_index": 1,
                        "quote_text": "Revenue 100",
                    }
                ],
            )

    class FakeWriter:
        def __init__(self, _conn, *, market, schema):
            assert market == "HK"
            assert schema == "pdf2md_hk"

        def import_rows(self, rows):
            return rows.parse_run["parse_run_id"]

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

    monkeypatch.setattr(importer, "read_json", read_json_guard)
    monkeypatch.setattr(importer, "rule_for_market", lambda _market: FakeRule())
    monkeypatch.setattr(importer, "connect", lambda _url: FakeConn())
    monkeypatch.setattr(importer, "MarketDocumentFullWriter", FakeWriter)

    assert importer.import_document_full(package_dir, market="HK") == "parse-document-full-only"
    assert read_paths == [document_full.resolve()]


def test_market_document_full_importer_allows_explicit_market_when_json_has_no_market(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_explicit", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "task-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"financial_data":{"statements":[]}}', encoding="utf-8")

    seen = {}

    class FakeWriter:
        def __init__(self, _conn, *, market, schema):
            seen["market"] = market
            seen["schema"] = schema

        def import_rows(self, rows):
            seen["rows_market"] = rows.parse_run["raw"]["document_full_path"]
            return "parse-explicit-market"

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

    monkeypatch.setattr(importer, "connect", lambda _url: FakeConn())
    monkeypatch.setattr(importer, "MarketDocumentFullWriter", FakeWriter)

    parse_run_id = importer.import_document_full(document_full, market="HK", allow_empty=True)

    assert parse_run_id == "parse-explicit-market"
    assert seen["market"] == "HK"
    assert seen["schema"] == "pdf2md_hk"


def test_market_document_full_importer_rejects_zero_fact_imports_by_default(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_empty_guard", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "task-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"HK"},"financial_data":{"statements":[]}}', encoding="utf-8")

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("empty document_full should fail before connecting")

    monkeypatch.setattr(importer, "connect", fail_connect)

    try:
        importer.import_document_full(document_full, market="HK")
    except SystemExit as exc:
        assert "produced zero numeric financial facts" in str(exc)
    else:
        raise AssertionError("expected empty import guard")


def test_market_document_full_importer_rejects_metadata_only_xbrl_facts_by_default(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_metadata_guard", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "filing-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text(
        '{"filing":{"market":"US","ticker":"AAPL","cik":"320193"},"facts":[{"concept":"dei:EntityRegistrantName","value_text":"Apple Inc."}]}',
        encoding="utf-8",
    )

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("metadata-only document_full should fail before connecting")

    monkeypatch.setattr(importer, "connect", fail_connect)

    try:
        importer.import_document_full(document_full, market="US")
    except SystemExit as exc:
        assert "produced zero numeric financial facts" in str(exc)
    else:
        raise AssertionError("expected metadata-only import guard")


def test_us_sec_rule_backfills_cik_from_filing_id(tmp_path):
    rows = _build_rows(
        "US",
        {
            "filing": {
                "market": "US",
                "ticker": "META",
                "company_name": "Meta Platforms, Inc.",
                "filing_id": "US:0001326801:0001628280-26-003942",
                "accession_number": "0001628280-26-003942",
                "form": "10-K",
                "fiscal_year": 2025,
            },
            "facts": [
                {
                    "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "label": "Revenue",
                    "value_numeric": "100",
                    "value_text": "100",
                    "unit": "iso4217:USD",
                    "period_end": "2025-12-31",
                    "html_anchor": "f-revenue-2025",
                }
            ],
        },
        tmp_path,
    )

    assert rows.company["cik"] == "0001326801"
    assert rows.company["company_id"] == "US:CIK0001326801"
    assert rows.filing["company_id"] == "US:CIK0001326801"


def test_market_document_full_importer_rejects_zero_citations_when_facts_and_chunks_exist(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_zero_citations", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "document_full.json"
    document_full.write_text('{"metadata":{"market":"HK"}}', encoding="utf-8")

    base = _load_module("market_document_full_base_zero_citations", "market_document_full_rules/base.py")

    class FakeRule:
        def build_rows(self, _document_full, _context):
            return base.MarketDocumentFullRows(
                company={"company_id": "HK:00700", "ticker": "00700"},
                filing={"filing_id": "HK:f1", "company_id": "HK:00700"},
                parse_run={"parse_run_id": "parse-hk-zero-citations"},
                statement_items=[
                    {
                        "item_uid": "item-1",
                        "statement_type": "income_statement",
                        "canonical_name": "revenue",
                        "value": "100",
                    }
                ],
                chunks=[{"chunk_uid": "chunk-1", "text": "Revenue 100"}],
                citations=[],
            )

    monkeypatch.setattr(importer, "rule_for_market", lambda _market: FakeRule())
    monkeypatch.setattr(
        importer,
        "connect",
        lambda _url: (_ for _ in ()).throw(AssertionError("zero-citation import should fail before connecting")),
    )

    try:
        importer.import_document_full(document_full, market="HK")
    except SystemExit as exc:
        assert "produced zero evidence citations" in str(exc)
    else:
        raise AssertionError("expected zero-citation import guard")


def test_market_document_full_importer_accepts_us_sec_market_alias(tmp_path, monkeypatch):
    importer = _load_module("market_document_full_importer_us_sec_alias", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "us-sec" / "filing-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"filing":{"market":"US_SEC","ticker":"AAPL","cik":"320193"},"facts":[]}', encoding="utf-8")

    class FakeWriter:
        def __init__(self, _conn, *, market, schema):
            assert market == "US"
            assert schema == "sec_us"

        def import_rows(self, rows):
            assert rows.company["company_id"] == "US:CIK0000320193"
            return "parse-us-sec-alias"

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

    monkeypatch.setattr(importer, "connect", lambda _url: FakeConn())
    monkeypatch.setattr(importer, "MarketDocumentFullWriter", FakeWriter)

    assert importer.import_document_full(document_full, market="US_SEC", allow_empty=True) == "parse-us-sec-alias"


def test_market_ingestion_contract_accepts_us_sec_aliases():
    contract = _load_module("market_ingestion_contract_aliases", "market_ingestion_contract.py")

    for alias in ("US_SEC", "US-SEC", "US SEC"):
        target = contract.target_for_market(alias)
        assert target.market == "US"
        assert target.schema == "sec_us"


def test_market_document_full_importer_keeps_cn_on_legacy_pipeline(tmp_path):
    importer = _load_module("market_document_full_importer_cn", "import_market_document_full_to_postgres.py")
    document_full = tmp_path / "document_full.json"
    document_full.write_text('{"metadata":{"market":"CN"}}', encoding="utf-8")

    try:
        importer.import_document_full(document_full)
    except SystemExit as exc:
        assert "CN/A-share document_full imports must use" in str(exc)
    else:
        raise AssertionError("expected CN legacy pipeline guard")


def test_hk_document_full_rules_map_local_metrics_and_preserve_currency(tmp_path):
    document_full = {
        "metadata": {"market": "HK", "ticker": "700", "company_name": "Tencent", "accounting_standard": "HKFRS"},
        "financial_data": {
            "report_id": "HK:00700:2025-annual",
            "period_end": "2025-12-31",
            "statements": [
                {
                    "statement_id": "is",
                    "statement_type": "income_statement",
                    "unit": "RMB million",
                    "items": [
                        {
                            "local_name": "收益",
                            "values": {"2025": {"value": "100", "raw_value": "100", "currency": "CNY", "evidence": {"page_number": 10, "table_index": 1}}},
                        }
                    ],
                }
            ]
        },
        "quality_report": {"overall_status": "pass"},
    }

    rows = _build_rows("HK", document_full, tmp_path)

    assert rows.company["company_id"] == "HK:00700"
    assert rows.filing["filing_id"] == "HK:00700:2025-annual"
    assert rows.statement_items[0]["canonical_name"] == "revenue"
    assert rows.statement_items[0]["currency"] == "CNY"
    assert rows.statement_items[0]["source_page_number"] == 10
    assert rows.enriched_items[0]["unit_standardized"] == "CNY"
    assert rows.statement_items[0]["fact_currency"] == "CNY"
    assert rows.statement_items[0]["reporting_currency"] == "CNY"


def test_hk_rules_normalize_common_ticker_shapes(tmp_path):
    document_full = {
        "metadata": {"market": "HK", "ticker": "700.HK", "company_name": "Tencent"},
        "financial_data": {"statements": []},
    }

    rows = _build_rows("HK", document_full, tmp_path)

    assert rows.company["company_id"] == "HK:00700"
    assert rows.company["ticker"] == "00700"
    assert rows.company["hkex_stock_code"] == "00700"


def test_jp_kr_rules_map_market_specific_names(tmp_path):
    jp = {
        "metadata": {"market": "JP", "ticker": "7203", "company_name": "Toyota", "edinet_code": "E02144"},
        "financial_data": {"statements": [{"statement_type": "income_statement", "items": [{"local_name": "営業利益", "period_key": "2025", "value": "25"}]}]},
    }
    kr = {
        "metadata": {"market": "KR", "ticker": "005930", "company_name": "Samsung", "corp_code": "00126380"},
        "financial_data": {"statements": [{"statement_type": "income_statement", "items": [{"local_name": "영업이익", "period_key": "2025", "value": "30"}]}]},
    }

    jp_rows = _build_rows("JP", jp, tmp_path / "jp")
    kr_rows = _build_rows("KR", kr, tmp_path / "kr")

    assert jp_rows.statement_items[0]["canonical_name"] == "operating_profit"
    assert kr_rows.statement_items[0]["canonical_name"] == "operating_profit"


def test_kr_rules_map_current_assets_to_common_core(tmp_path):
    document_full = {
        "metadata": {"market": "KR", "ticker": "005930", "company_name": "Samsung", "corp_code": "00126380"},
        "financial_data": {
            "statements": [
                {
                    "statement_type": "balance_sheet",
                    "items": [{"local_name": "유동자산", "period_key": "2025-12-31", "value": "247684612"}],
                }
            ]
        },
    }

    rows = _build_rows("KR", document_full, tmp_path)

    assert rows.statement_items[0]["canonical_name"] == "current_assets"
    assert rows.statement_items[0]["canonical_scope"] == "common_core"


def test_eu_rules_preserve_country_currency_and_ifrs_tags(tmp_path):
    document_full = {
        "metadata": {"market": "EU", "country": "GB", "ticker": "VOD", "company_name": "Vodafone", "isin": "GB00BH4HKS39"},
        "financial_data": {
            "statements": [
                {
                    "statement_type": "income_statement",
                    "unit": "GBP million",
                    "items": [{"concept": "ifrs-full:Revenue", "period_key": "2025", "value": "1000", "currency": "GBP"}],
                }
            ]
        },
    }

    rows = _build_rows("EU", document_full, tmp_path)

    assert rows.company["country"] == "GB"
    assert rows.statement_items[0]["canonical_name"] == "revenue"
    assert rows.statement_items[0]["currency"] == "GBP"


def test_us_sec_rules_use_top_level_facts_without_financial_data(tmp_path):
    document_full = {
        "schema_version": "sec_html_document_full_v1",
        "filing": {"ticker": "AAPL", "cik": "320193", "company_name": "Apple Inc.", "form": "10-K", "period_end": "2025-09-27"},
        "source": {"source_url": "https://www.sec.gov/demo.htm"},
        "tables": [{"table_index": 1, "html_anchor": "#t1", "title": "Statements"}],
        "facts": [
            {
                "concept": "us-gaap:Revenues",
                "label": "Net sales",
                "value_numeric": "391035",
                "value_text": "391,035",
                "unit": "USD million",
                "unit_ref": "usd",
                "context_ref": "fy2025",
                "period_key": "FY2025",
                "html_anchor": "#fact-revenue",
                "table_index": 1,
            }
        ],
    }

    rows = _build_rows("US", document_full, tmp_path)

    assert rows.company["company_id"] == "US:CIK0000320193"
    assert rows.filing["accession_number"]
    assert rows.filing["form"] == "10-K"
    # US facts are normalized into statement-like rows by the generic rule.
    assert rows.statement_items or rows.key_metrics
    assert rows.statement_items[0]["canonical_name"] == "revenue"
    assert rows.statement_items[0]["canonical_scope"] == "common_core"
    assert rows.statement_items[0]["xbrl_tag"] == "us-gaap:Revenues"
    assert rows.xbrl_facts_raw[0]["concept"] == "us-gaap:Revenues"
    assert rows.xbrl_facts_raw[0]["html_anchor"] == "#fact-revenue"


def test_eu_rules_normalize_top_level_esef_facts(tmp_path):
    document_full = {
        "metadata": {"market": "EU", "country": "CH", "ticker": "NESN", "company_name": "Nestle", "isin": "CH0038863350"},
        "facts": [
            {
                "concept": "ifrs-full:Revenue",
                "label": "Revenue",
                "value_numeric": "93000",
                "value_text": "93,000",
                "unit": "CHF million",
                "context_ref": "fy2025",
                "period_key": "FY2025",
            }
        ],
    }

    rows = _build_rows("EU", document_full, tmp_path)

    assert rows.company["country"] == "CH"
    assert rows.statement_items[0]["statement_type"] == "income_statement"
    assert rows.statement_items[0]["canonical_name"] == "revenue"
    assert rows.statement_items[0]["currency"] == "CHF"
    assert rows.wide_rows[0]["all_metrics"]["revenue"]["currency"] == "CHF"


def test_eu_rules_classify_common_ifrs_statement_types(tmp_path):
    document_full = {
        "metadata": {"market": "EU", "country": "NL", "ticker": "ASML", "company_name": "ASML", "isin": "NL0010273215"},
        "facts": [
            {"concept": "ifrs-full:Assets", "label": "Assets", "value_numeric": "100", "unit": "EUR", "context_ref": "fy2025", "period_key": "2025"},
            {"concept": "ifrs-full:ProfitLoss", "label": "Profit", "value_numeric": "20", "unit": "EUR", "context_ref": "fy2025", "period_key": "2025"},
            {"concept": "ifrs-full:CashFlowsFromUsedInOperatingActivities", "label": "Operating cash flow", "value_numeric": "15", "unit": "EUR", "context_ref": "fy2025", "period_key": "2025"},
        ],
    }

    rows = _build_rows("EU", document_full, tmp_path)

    assert [item["statement_type"] for item in rows.statement_items] == [
        "balance_sheet",
        "income_statement",
        "cash_flow_statement",
    ]
    assert rows.wide_rows[0]["balance_sheet"]["total_assets"]["value"] == "100"
    assert rows.wide_rows[0]["income_statement"]["net_profit"]["value"] == "20"
    assert rows.wide_rows[0]["cash_flow_statement"]["operating_cash_flow"]["value"] == "15"


def test_eu_rules_flag_multi_currency_documents(tmp_path):
    document_full = {
        "metadata": {"market": "EU", "country": "GB", "ticker": "VOD", "company_name": "Vodafone", "isin": "GB00BH4HKS39"},
        "financial_data": {
            "statements": [
                {
                    "statement_type": "income_statement",
                    "items": [
                        {"item_name": "Revenue", "period_key": "2025", "value": "1", "unit": "GBP million"},
                        {"item_name": "Revenue", "period_key": "2024", "value": "1", "unit": "EUR million"},
                    ],
                }
            ]
        },
    }

    rows = _build_rows("EU", document_full, tmp_path)

    assert "eu_multi_currency_document" in rows.parse_run["warnings"]
    assert rows.statement_items[0]["fact_currency"] == "GBP"
    assert rows.statement_items[1]["fact_currency"] == "EUR"
    assert any("multi_currency_document" in item["quality_flags"] for item in rows.enriched_items)


def test_unmapped_metrics_are_preserved_without_fabricated_canonical(tmp_path):
    document_full = {
        "metadata": {"market": "HK", "ticker": "700", "company_name": "Tencent"},
        "financial_data": {
            "statements": [{"statement_type": "income_statement", "items": [{"local_name": "自定义云业务客户数", "period_key": "2025", "value": "12"}]}]
        },
    }

    rows = _build_rows("HK", document_full, tmp_path)

    assert rows.statement_items[0]["canonical_name"] is None
    assert rows.statement_items[0]["canonical_scope"] == "unmapped"
    assert "canonical_unmapped" in rows.enriched_items[0]["quality_flags"]


def test_jp_kr_top_level_xbrl_facts_promote_to_statement_items(tmp_path):
    jp = {
        "metadata": {"market": "JP", "ticker": "7203", "company_name": "Toyota"},
        "facts": [
            {
                "concept": "ifrs-full:Revenue",
                "label": "売上収益",
                "numeric_value": "48036704",
                "raw_value": "48,036,704",
                "unit": "JPY million",
                "context_ref": "fy2025",
                "period_key": "FY2025",
            }
        ],
    }
    kr = {
        "metadata": {"market": "KR", "ticker": "005930", "company_name": "Samsung"},
        "facts": [
            {
                "concept": "ifrs-full:Revenue",
                "label": "매출액",
                "value": "300000",
                "unit": "KRW million",
                "context_ref": "fy2025",
                "period_key": "FY2025",
            }
        ],
    }

    jp_rows = _build_rows("JP", jp, tmp_path / "jp")
    kr_rows = _build_rows("KR", kr, tmp_path / "kr")

    assert jp_rows.statement_items[0]["canonical_name"] == "revenue"
    assert jp_rows.statement_items[0]["raw_value"] == "48,036,704"
    assert jp_rows.xbrl_facts_raw[0]["value_numeric"].to_eng_string() == "48036704"
    assert kr_rows.statement_items[0]["canonical_name"] == "revenue"
    assert kr_rows.statement_items[0]["value"].to_eng_string() == "300000"


def test_us_sec_rules_preserve_sections_contexts_and_units(tmp_path):
    document_full = {
        "schema_version": "sec_html_document_full_v1",
        "filing": {"market": "US", "ticker": "AAPL", "cik": "320193", "company_name": "Apple Inc.", "form": "10-K"},
        "sections": [{"section_id": "fs", "title": "Financial Statements", "html_anchor": "#fs"}],
        "contexts": {"c-2025": {"period_start": "2024-09-29", "period_end": "2025-09-27", "dimensions": {"dei:LegalEntityAxis": "AAPL"}}},
        "units": {"usd": {"unit": "iso4217:USD"}},
        "facts": [
            {
                "fact_id": "f1",
                "concept": "us-gaap:Revenues",
                "label": "Net sales",
                "value_numeric": "416161",
                "value_text": "416,161",
                "unit_ref": "usd",
                "unit": "iso4217:USD",
                "context_ref": "c-2025",
                "html_anchor": "#f1",
                "dimensions": {"ProductAxis": "Total"},
            }
        ],
    }

    rows = _build_rows("US", document_full, tmp_path)

    assert rows.sections[0]["section_id"] == "fs"
    assert rows.xbrl_contexts[0]["context_ref"] == "c-2025"
    assert rows.xbrl_contexts[0]["dimensions"] == {"dei:LegalEntityAxis": "AAPL"}
    assert rows.xbrl_units[0]["unit_ref"] == "usd"
    assert rows.xbrl_facts_raw[0]["dimensions"] == {"ProductAxis": "Total"}
    assert rows.xbrl_facts_raw[0]["fact_id"].startswith("fact_")
    assert rows.xbrl_facts_raw[0]["raw_fact_id"] == "f1"
    assert rows.statement_items[0]["period_end"] == "2025-09-27"
    assert rows.statement_items[0]["dimensions"] == {"ProductAxis": "Total"}
    assert rows.statement_items[0]["raw_fact_id"] == rows.xbrl_facts_raw[0]["fact_id"]


def test_us_sec_rules_classify_common_concepts_into_statement_types(tmp_path):
    document_full = {
        "filing": {"market": "US", "ticker": "AAPL", "cik": "320193", "company_name": "Apple Inc.", "form": "10-K"},
        "facts": [
            {
                "fact_id": "assets",
                "concept": "us-gaap:Assets",
                "label": "Total assets",
                "value_numeric": "100",
                "value_text": "100",
                "unit": "USD",
                "context_ref": "fy2025",
                "period_key": "FY2025",
            },
            {
                "fact_id": "revenue",
                "concept": "us-gaap:Revenues",
                "label": "Revenue",
                "value_numeric": "200",
                "value_text": "200",
                "unit": "USD",
                "context_ref": "fy2025",
                "period_key": "FY2025",
            },
        ],
    }

    rows = _build_rows("US", document_full, tmp_path)
    by_concept = {item["concept"]: item for item in rows.statement_items}

    assert by_concept["us-gaap:Assets"]["statement_type"] == "balance_sheet"
    assert by_concept["us-gaap:Revenues"]["statement_type"] == "income_statement"


def test_hk_rules_extract_quality_and_content_enhancements(tmp_path):
    document_full = {
        "metadata": {"market": "HK", "ticker": "700", "company_name": "Tencent"},
        "financial_data": {"statements": []},
        "quality_report": {
            "overall_status": "warning",
            "table_count": 3,
            "critical_warnings": ["missing-cash-flow"],
            "evidence_coverage_ratio": "0.91",
        },
        "content_list_enhanced": {
            "footnotes": {"references": [{"id": "fn1", "page_number": 8, "table_index": 2, "text": "Including subsidiaries"}]},
            "toc": {"headings": [{"title": "Financial statements", "level": 1, "page_number": 7}]},
            "financial_note_links": {"links": [{"note": "1", "target": "revenue", "table_index": 2}]},
            "tables": [{"table_index": 2, "relations": [{"type": "footnote", "target": "revenue", "target_table_id": "table-3"}]}],
            "quality_signals": {"tables": [{"table_index": 2, "score": 0.95}]},
        },
    }

    rows = _build_rows("HK", document_full, tmp_path)

    assert rows.quality_reports[0]["overall_status"] == "warning"
    assert rows.quality_reports[0]["table_count"] == 3
    assert rows.footnotes[0]["footnote_key"] == "fn1"
    assert rows.toc_entries[0]["title"] == "Financial statements"
    assert rows.financial_note_links[0]["target"] == "revenue"
    assert rows.table_relations[0]["relation_type"] == "footnote"
    assert rows.table_quality_signals[0]["signal_value"] == "0.95"
    assert rows.raw_payload_refs[0]["payload_name"] == "document_full"
