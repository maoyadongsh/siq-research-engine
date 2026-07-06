import zipfile
from pathlib import Path

from market_report_rules_service.evidence_package import read_json, validate_evidence_package, write_json


EU_IXBRL = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024/ifrs-full"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <head><title>ASML 2025 Annual Report</title></head>
  <body>
    <ix:header>
      <ix:resources>
        <xbrli:context id="fy_duration"><xbrli:entity><xbrli:identifier>ASML</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:context id="fy_instant"><xbrli:entity><xbrli:identifier>ASML</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
        <xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>
      </ix:resources>
    </ix:header>
    <h1>Consolidated financial statements</h1>
    <ix:nonFraction id="f-assets" name="ifrs-full:Assets" contextRef="fy_instant" unitRef="EUR" decimals="-6">50000</ix:nonFraction>
    <ix:nonFraction id="f-liabilities" name="ifrs-full:Liabilities" contextRef="fy_instant" unitRef="EUR" decimals="-6">20000</ix:nonFraction>
    <ix:nonFraction id="f-equity" name="ifrs-full:Equity" contextRef="fy_instant" unitRef="EUR" decimals="-6">30000</ix:nonFraction>
    <ix:nonFraction id="f-revenue" name="ifrs-full:Revenue" contextRef="fy_duration" unitRef="EUR" decimals="-6">30000</ix:nonFraction>
    <ix:nonFraction id="f-profit" name="ifrs-full:ProfitLoss" contextRef="fy_duration" unitRef="EUR" decimals="-6">6000</ix:nonFraction>
    <ix:nonFraction id="f-ocf" name="ifrs-full:CashFlowsFromUsedInOperatingActivities" contextRef="fy_duration" unitRef="EUR" decimals="-6">7200</ix:nonFraction>
  </body>
</html>
"""


EU_HTML = """<!doctype html>
<html>
  <head><title>ASML 2025 Annual Report</title></head>
  <body>
    <h1>ASML 2025 Annual Report</h1>
    <h2>Consolidated income statement</h2>
    <table id="income">
      <caption>Consolidated income statement EUR million</caption>
      <tr><th>Item</th><th>2025</th><th>2024</th></tr>
      <tr><td>Revenue</td><td>30,000</td><td>28,000</td></tr>
      <tr><td>Profit for the year</td><td>6,000</td><td>5,500</td></tr>
    </table>
    <h2>Consolidated statement of financial position</h2>
    <table id="balance">
      <caption>Consolidated statement of financial position EUR million</caption>
      <tr><th>Item</th><th>2025</th><th>2024</th></tr>
      <tr><td>Total assets</td><td>50,000</td><td>45,000</td></tr>
      <tr><td>Total liabilities</td><td>20,000</td><td>18,000</td></tr>
      <tr><td>Total equity</td><td>30,000</td><td>27,000</td></tr>
    </table>
  </body>
</html>
"""


def test_build_eu_esef_evidence_package_from_xhtml(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "eu"))
    from eu_esef_evidence_lib import write_eu_esef_evidence_package

    source = tmp_path / "asml.xhtml"
    source.write_text(EU_IXBRL, encoding="utf-8")
    metadata = source.with_suffix(".xhtml.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "eu_direct",
                "source_tier": "local_uploaded",
                "country": "NL",
                "ticker": "ASML",
                "company_name": "ASML Holding N.V.",
                "form": "ESEF",
                "report_type": "annual",
                "report_end": "2025-12-31",
                "published_at": "2026-02-11",
                "document_url": "https://example.test/asml.xhtml",
                "source_tier": "local_uploaded",
            }
        },
    )

    package_dir = write_eu_esef_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    validation = validate_evidence_package(package_dir)
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []
    facts = read_json(package_dir / "xbrl" / "facts_raw.json").get("facts") or []
    contexts = read_json(package_dir / "xbrl" / "contexts.json").get("contexts") or {}
    units = read_json(package_dir / "xbrl" / "units.json").get("units") or {}

    assert validation.ok, validation.errors
    assert validation.manifest["market"] == "EU"
    assert validation.manifest["country"] == "NL"
    assert validation.manifest["document_format"] == "ixbrl_xhtml"
    assert len(facts) == 6
    assert contexts
    assert units
    assert any(item["canonical_name"] == "operating_revenue" for item in metrics)
    assert any(item["xbrl_tag"] == "ifrs-full:Assets" and item["evidence_id"] for item in metrics)


def test_build_eu_html_evidence_package_from_plain_html(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "eu"))
    from eu_esef_evidence_lib import write_eu_esef_evidence_package

    source = tmp_path / "asml.html"
    source.write_text(EU_HTML, encoding="utf-8")
    metadata = source.with_suffix(".html.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "country": "NL",
                "ticker": "ASML",
                "company_name": "ASML Holding N.V.",
                "form": "annual",
                "report_type": "annual",
                "report_end": "2025-12-31",
                "published_at": "2026-02-11",
                "document_url": "https://example.test/asml.html",
                "source_tier": "local_uploaded",
            }
        },
    )

    package_dir = write_eu_esef_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    validation = validate_evidence_package(package_dir)
    manifest = validation.manifest
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []
    facts = read_json(package_dir / "xbrl" / "facts_raw.json").get("facts") or []
    source_map = read_json(package_dir / "qa" / "source_map.json").get("entries") or []
    tables = read_json(package_dir / "tables" / "table_index.json").get("tables") or []

    assert validation.ok, validation.errors
    assert manifest["document_format"] == "html"
    assert manifest["inline_xbrl"] is False
    assert facts == []
    assert len(tables) == 2
    assert any(item["canonical_name"] == "operating_revenue" and item["source_type"] == "html_table" for item in metrics)
    assert any(entry["source_type"] == "html_table" and entry["html_anchor"] == "income" for entry in source_map)


def test_build_eu_esef_evidence_package_from_zip(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "eu"))
    from eu_esef_evidence_lib import write_eu_esef_evidence_package

    source = tmp_path / "asml-esef.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("reports/asml.xhtml", EU_IXBRL)
        zf.writestr("taxonomy/asml.xsd", "<schema/>")
    metadata = source.with_suffix(".zip.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "country": "NL",
                "ticker": "ASML",
                "company_name": "ASML Holding N.V.",
                "form": "ESEF",
                "report_end": "2025-12-31",
                "published_at": "2026-02-11",
                "document_url": "https://example.test/asml-esef.zip",
                "source_tier": "local_uploaded",
            }
        },
    )

    package_dir = write_eu_esef_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    validation = validate_evidence_package(package_dir)
    manifest = validation.manifest
    entrypoints = read_json(package_dir / "xbrl" / "entrypoints.json")

    assert validation.ok, validation.errors
    assert manifest["document_format"] == "esef_zip"
    assert manifest["local_source_path"] == "raw/esef.zip"
    assert entrypoints["primary"] == "raw/extracted/reports/asml.xhtml"
