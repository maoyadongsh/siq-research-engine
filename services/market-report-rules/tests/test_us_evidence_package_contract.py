from pathlib import Path

from market_report_rules_service.evidence_package import validate_evidence_package, write_json


def test_us_sec_builder_writes_market_contract_package(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "us-sec"))
    from sec_evidence_lib import write_evidence_package

    html = tmp_path / "demo.htm"
    html.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <xbrli:context id="c1"><xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
          <xbrli:context id="d1"><xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          Item 1. Business """ + ("business text " * 80) + """
          Item 7. Management's Discussion and Analysis """ + ("mda text " * 80) + """
          Item 8. Financial Statements """ + ("financial text " * 80) + """
          <ix:nonFraction name="us-gaap:Assets" contextRef="c1" unitRef="usd">1000</ix:nonFraction>
          <ix:nonFraction name="us-gaap:Liabilities" contextRef="c1" unitRef="usd">600</ix:nonFraction>
          <ix:nonFraction name="us-gaap:StockholdersEquity" contextRef="c1" unitRef="usd">400</ix:nonFraction>
          <ix:nonFraction name="us-gaap:Revenues" contextRef="d1" unitRef="usd">3000</ix:nonFraction>
          <ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="d1" unitRef="usd">100</ix:nonFraction>
          <ix:nonFraction name="us-gaap:NetCashProvidedByUsedInOperatingActivities" contextRef="d1" unitRef="usd">120</ix:nonFraction>
        </html>
        """,
        encoding="utf-8",
    )
    metadata = html.with_suffix(".htm.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "sec",
                "ticker": "DEMO",
                "company_name": "Demo Corp",
                "form": "10-K",
                "accession_number": "0000000001-26-000001",
                "report_end": "2025-12-31",
                "published_at": "2026-02-01",
                "document_url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo.htm",
            }
        },
    )

    package_dir = write_evidence_package(html, tmp_path / "wiki", metadata, force=True)
    result = validate_evidence_package(package_dir)

    assert result.ok, result.errors
    assert result.manifest["schema_version"] == "market_evidence_package_v1"
    assert result.manifest["company_id"] == "US:0000000001"
