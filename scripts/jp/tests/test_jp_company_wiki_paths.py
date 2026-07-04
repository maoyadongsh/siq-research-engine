import jp_evidence_lib as jp


def test_company_wiki_report_paths_follow_market_company_report_contract(tmp_path):
    metadata = {
        "ticker": "7203",
        "company_name": "Toyota Motor Corporation",
        "company_name_ja": "トヨタ自動車株式会社",
        "edinet_code": "E02144",
        "doc_id": "S100TEST",
        "form": "Annual Securities Report",
        "report_type": "annual_securities_report",
        "fiscal_year": 2025,
    }

    paths = jp.company_wiki_report_paths(tmp_path / "wiki", metadata)

    assert paths.company_id == "7203-Toyota-Motor-Corporation"
    assert paths.report_id == "2025-annual-securities-report-S100TEST"
    assert paths.company_dir == tmp_path / "wiki" / "jp" / "companies" / "7203-Toyota-Motor-Corporation"
    assert paths.report_dir == paths.company_dir / "reports" / "2025-annual-securities-report-S100TEST"
    assert paths.company_wiki_path == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation"
    assert paths.wiki_report_path == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation/reports/2025-annual-securities-report-S100TEST"


def test_jp_report_type_distinguishes_annual_securities_report():
    assert jp._report_type("Annual Securities Report") == "annual_securities_report"
    assert jp._report_type("有価証券報告書") == "annual_securities_report"
    assert jp._report_type("Integrated Report") == "integrated_report"
    assert jp._report_type("integrated_report") == "integrated_report"
