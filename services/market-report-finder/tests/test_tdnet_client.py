from datetime import date

from market_report_finder_service.markets.jp.tdnet import TdnetClient
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportTarget, ReportType


def _jp_company() -> CompanyEntity:
    return CompanyEntity(
        market=Market.jp,
        company_id="285A",
        ticker="285A0",
        company_name="キオクシアホールディングス株式会社",
        exchange="JPX",
    )


TDNET_HTML = """
<html><body>
<div class="pager-M" onClick="pagerLink('I_list_002_20260626.html')">2</div>
<table id="main-list-table">
<tr>
<td class="oddnew-L kjTime" noWrap>15:30</td>
<td class="oddnew-M kjCode" noWrap>285A0</td>
<td class="oddnew-M kjName" noWrap>キオクシア</td>
<td class="oddnew-M kjTitle" align="left"><a href="140120260626581111.pdf" target="_blank">2026年3月期 第3四半期決算短信〔日本基準〕（連結）</a></td>
<td class="oddnew-M kjXbrl" noWrap align="center"><a href="091220260626581111.zip">XBRL</a></td>
<td class="oddnew-M kjPlace" noWrap align="left">東</td>
<td class="oddnew-R kjHistroy" align="left"> </td>
</tr>
</table>
</body></html>
"""


def test_tdnet_parses_public_daily_listing(monkeypatch):
    client = TdnetClient()
    monkeypatch.setattr(client, "_get_text", lambda url: TDNET_HTML if "001" in url else "")

    rows = client._list_rows_for_day(date(2026, 6, 26))

    assert rows[0]["code"] == "285A0"
    assert rows[0]["company_name"] == "キオクシア"
    assert rows[0]["pdf_href"] == "140120260626581111.pdf"
    assert "第3四半期決算短信" in rows[0]["title"]


def test_tdnet_builds_official_pdf_candidate(monkeypatch):
    client = TdnetClient()
    monkeypatch.setattr(client, "_scan_window", lambda report_year=None: [
        {
            "time": "15:30",
            "code": "285A0",
            "company_name": "キオクシア",
            "title": "2026年3月期 第3四半期決算短信〔日本基準〕（連結）",
            "pdf_href": "140120260626581111.pdf",
            "published_at": "2026-06-26",
            "list_page": "I_list_001_20260626.html",
        }
    ])

    candidates = client.list_filings(_jp_company(), target=ReportTarget.quarterly_report, forms=["quarterly"], report_year=2026)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_id == "tdnet"
    assert candidate.source_name == "TDnet"
    assert candidate.report_type == ReportType.quarterly
    assert candidate.report_family == ReportFamily.quarterly
    assert candidate.document_url == "https://www.release.tdnet.info/inbs/140120260626581111.pdf"
    assert candidate.landing_url == "https://www.release.tdnet.info/inbs/I_list_001_20260626.html"
