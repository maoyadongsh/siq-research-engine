import zipfile
from pathlib import Path

from market_report_rules_service.evidence_package import read_json, validate_evidence_package, write_json


JP_XBRL = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024/ifrs-full" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <xbrli:context id="fy_duration"><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="fy_instant"><xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
  <ifrs-full:Assets contextRef="fy_instant" unitRef="JPY">1000</ifrs-full:Assets>
  <ifrs-full:Liabilities contextRef="fy_instant" unitRef="JPY">600</ifrs-full:Liabilities>
  <ifrs-full:Equity contextRef="fy_instant" unitRef="JPY">400</ifrs-full:Equity>
  <ifrs-full:Revenue contextRef="fy_duration" unitRef="JPY">3000</ifrs-full:Revenue>
  <ifrs-full:ProfitLoss contextRef="fy_duration" unitRef="JPY">100</ifrs-full:ProfitLoss>
  <ifrs-full:CashFlowsFromUsedInOperatingActivities contextRef="fy_duration" unitRef="JPY">150</ifrs-full:CashFlowsFromUsedInOperatingActivities>
</xbrli:xbrl>
"""


KR_XBRL = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024/ifrs-full" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <xbrli:context id="fy_duration"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="fy_instant"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="KRW"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>
  <ifrs-full:Assets contextRef="fy_instant" unitRef="KRW">1000</ifrs-full:Assets>
  <ifrs-full:Liabilities contextRef="fy_instant" unitRef="KRW">450</ifrs-full:Liabilities>
  <ifrs-full:Equity contextRef="fy_instant" unitRef="KRW">550</ifrs-full:Equity>
  <ifrs-full:Revenue contextRef="fy_duration" unitRef="KRW">3000</ifrs-full:Revenue>
  <ifrs-full:ProfitLoss contextRef="fy_duration" unitRef="KRW">180</ifrs-full:ProfitLoss>
  <ifrs-full:CashFlowsFromUsedInOperatingActivities contextRef="fy_duration" unitRef="KRW">300</ifrs-full:CashFlowsFromUsedInOperatingActivities>
</xbrli:xbrl>
"""


def test_build_jp_evidence_package_from_xbrl_zip(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "jp"))
    from jp_evidence_lib import write_jp_evidence_package

    source = tmp_path / "toyota.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("XBRL/PublicDoc/toyota.xbrl", JP_XBRL)
    metadata = source.with_suffix(".zip.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "edinet",
                "company_id": "E02144",
                "edinet_code": "E02144",
                "ticker": "7203",
                "company_name": "Toyota Motor Corporation",
                "form": "有価証券報告書",
                "doc_id": "S100TEST",
                "report_end": "2025-03-31",
                "published_at": "2025-06-30",
                "document_url": "https://disclosure.edinet-fsa.go.jp/test",
            }
        },
    )

    package_dir = write_jp_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    validation = validate_evidence_package(package_dir)
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []

    assert validation.ok, validation.errors
    assert len(metrics) >= 6
    assert any(item["canonical_name"] == "operating_revenue" for item in metrics)


def test_build_kr_evidence_package_from_xbrl_xml(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "scripts" / "kr"))
    from kr_evidence_lib import write_kr_evidence_package

    source = tmp_path / "samsung.xml"
    source.write_text(KR_XBRL, encoding="utf-8")
    metadata = source.with_suffix(".xml.metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "dart",
                "company_id": "00126380",
                "corp_code": "00126380",
                "ticker": "005930",
                "company_name": "Samsung Electronics",
                "form": "business_report",
                "rcp_no": "20260315000001",
                "report_end": "2025-12-31",
                "published_at": "2026-03-15",
                "document_url": "https://dart.fss.or.kr/test",
            }
        },
    )

    package_dir = write_kr_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    validation = validate_evidence_package(package_dir)
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []

    assert validation.ok, validation.errors
    assert len(metrics) >= 6
    assert any(item["canonical_name"] == "total_assets" for item in metrics)
