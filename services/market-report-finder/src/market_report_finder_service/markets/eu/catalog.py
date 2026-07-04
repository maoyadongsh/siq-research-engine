from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from urllib.parse import urlparse

from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportType


@dataclass(frozen=True)
class EuAnnualReportCatalogEntry:
    country: str
    company_id: str
    ticker: str
    company_name: str
    document_url: str
    landing_url: str
    report_end: date
    published_at: date
    title: str
    source_id: str = "issuer_annual_report"
    source_name: str = "Issuer annual report download"
    source_tier: str = "official_direct"
    file_format: str = "pdf"
    language: str = "en"
    aliases: tuple[str, ...] = ()


EU_ANNUAL_REPORT_CATALOG: tuple[EuAnnualReportCatalogEntry, ...] = (
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:AZN",
        ticker="AZN",
        company_name="AstraZeneca PLC",
        document_url="https://www.astrazeneca.com/content/dam/az/Investor_Relations/annual-report-2025/pdf/AstraZeneca_AR_2025.pdf",
        landing_url="https://www.astrazeneca.com/investor-relations/annual-reports/annual-report-2025.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="AstraZeneca Annual Report 2025",
        aliases=("AstraZeneca", "AstraZeneca PLC", "AZN"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:BP",
        ticker="BP",
        company_name="BP p.l.c.",
        document_url="https://www.bp.com/api/files/6cqieuqhq4no/master/33M2iHp8A6d07McKzKqLNP/12b5d4eccb4e02093d1ad9efc0d6a746/bp-annual-report-and-form-20f-2025.pdf",
        landing_url="https://www.bp.com/investors/results-reporting-and-presentations/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 7),
        title="BP Annual Report and Form 20-F 2025",
        aliases=("BP", "BP PLC", "BP p.l.c."),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:BARC",
        ticker="BARC",
        company_name="Barclays PLC",
        document_url="https://home.barclays/content/dam/home-barclays/documents/investor-relations/reports-and-events/annual-reports/2025/Barclays-PLC-Annual-Report-2025.pdf",
        landing_url="https://home.barclays/investor-relations/reports-and-events/annual-reports/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 17),
        title="Barclays PLC Annual Report 2025",
        aliases=("Barclays", "Barclays PLC", "BARC"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:HSBA",
        ticker="HSBA",
        company_name="HSBC Holdings plc",
        document_url="https://www.hsbc.com/-/files/hsbc/investors/hsbc-results/2025/annual/pdfs/hsbc-holdings-plc/260225-annual-report-and-accounts-2025.pdf?download=1",
        landing_url="https://www.hsbc.com/investors/results-and-announcements/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 25),
        title="HSBC Holdings Annual Report and Accounts 2025",
        aliases=("HSBC", "HSBC Holdings", "HSBC Holdings plc", "HSBA", "HSBA.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:SHEL",
        ticker="SHEL",
        company_name="Shell plc",
        document_url="https://www.shell.com/investors/results-and-reporting/annual-report/_jcr_content/root/main/section/promo.multi.stream/1779352356739/36306d968747b6079d3f800b2b1552a033856b5d/shell-integrated-annual-and-sustainability-report.pdf",
        landing_url="https://www.shell.com/investors/results-and-reporting/annual-report.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 12),
        title="Shell Integrated Annual and Sustainability Report 2025",
        aliases=("Shell", "Shell plc", "SHEL", "SHEL.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:ULVR",
        ticker="ULVR",
        company_name="Unilever PLC",
        document_url="https://www.londonstockexchange.com/news-article/ULVR/annual-financial-report/17501023",
        landing_url="https://www.unilever.com/investors/annual-report-and-accounts/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 12),
        title="Unilever Annual Financial Report 2025",
        source_id="exchange_regulatory_news",
        source_name="Exchange regulatory news annual report",
        source_tier="official_direct",
        file_format="html",
        aliases=("Unilever", "Unilever PLC", "ULVR", "ULVR.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:DGE",
        ticker="DGE",
        company_name="Diageo plc",
        document_url="https://www.diageo.com/~/media/Files/D/Diageo-V2/Diageo-Corp/investors/results-reports-and-events/annual-reports/2025/annual-report-2025.pdf",
        landing_url="https://www.diageo.com/en/investors/results-reports-and-events/annual-report-2025",
        report_end=date(2025, 6, 30),
        published_at=date(2025, 9, 5),
        title="Diageo Annual Report 2025",
        aliases=("Diageo", "Diageo plc", "DGE", "DGE.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:RIO",
        ticker="RIO",
        company_name="Rio Tinto plc",
        document_url="https://cdn-rio.dataweavers.io/-/media/content/documents/invest/reports/annual-reports/2025-annual-report.pdf?rev=928756ce35df4757be31105d2665bd55",
        landing_url="https://www.riotinto.com/en/invest/reports/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 19),
        title="Rio Tinto Annual Report 2025",
        aliases=("Rio Tinto", "Rio Tinto plc", "RIO", "RIO.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:GLEN",
        ticker="GLEN",
        company_name="Glencore plc",
        document_url="https://www.glencore.com/.rest/api/v1/documents/static/9b103e11-72e7-40bf-ae7c-eabe57361522/GLEN-2025-Annual-Report.pdf",
        landing_url="https://www.glencore.com/publications",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 10),
        title="Glencore Annual Report 2025",
        aliases=("Glencore", "Glencore plc", "GLEN", "GLEN.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="GB",
        company_id="GB:LSEG",
        ticker="LSEG",
        company_name="London Stock Exchange Group plc",
        document_url="https://www.lseg.com/content/dam/lseg/en_us/documents/investor-relations/annual-reports/lseg-annual-report-2025.pdf",
        landing_url="https://www.lseg.com/en/investor-relations/annual-reports/2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 11),
        title="London Stock Exchange Group Annual Report 2025",
        aliases=("London Stock Exchange Group", "London Stock Exchange Group plc", "LSEG", "LSEG.L"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:TTE",
        ticker="TTE",
        company_name="TotalEnergies SE",
        document_url="https://totalenergies.com/system/files/documents/totalenergies_universal-registration-document-2025_2026_en.pdf",
        landing_url="https://totalenergies.com/investors/publications-and-regulated-information/regulated-information/universal-registration-document",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 27),
        title="TotalEnergies Universal Registration Document 2025",
        aliases=("TotalEnergies", "TotalEnergies SE", "TTE"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:SAN",
        ticker="SAN",
        company_name="Sanofi",
        document_url="https://www.sanofi.com/assets/dotcom/content-app/publications/annual-report-on-form-20-f/2025-01-01-form-20-f-2025-en.pdf",
        landing_url="https://www.sanofi.com/en/investors/financial-reports-and-regulated-information",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 20),
        title="Sanofi Annual Report on Form 20-F 2025",
        aliases=("Sanofi", "Sanofi SA", "SAN"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:AI",
        ticker="AI",
        company_name="Air Liquide S.A.",
        document_url="https://www.airliquide.com/sites/airliquide.com/files/2026-03/air-liquide-2025-universal-registration-document-interactive.pdf",
        landing_url="https://www.airliquide.com/group/publications",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 6),
        title="Air Liquide Universal Registration Document 2025",
        aliases=("Air Liquide", "Air Liquide S.A.", "AI"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:MC",
        ticker="MC",
        company_name="LVMH Moet Hennessy Louis Vuitton SE",
        document_url="https://lvmh-com.cdn.prismic.io/lvmh-com/aczo-pGXnQHGZKQ5_UniversalRegistrationDocument2025.pdf",
        landing_url="https://www.lvmh.com/en/publications",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 4, 1),
        title="LVMH Universal Registration Document 2025",
        aliases=("LVMH", "LVMH Moet Hennessy Louis Vuitton", "LVMH Moet Hennessy Louis Vuitton SE", "MC", "MC.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:OR",
        ticker="OR",
        company_name="L'Oreal S.A.",
        document_url="https://www.loreal-finance.com/system/files/2026-03/LOREAL_DEU_2025_EN.pdf",
        landing_url="https://www.loreal-finance.com/eng/registration-document",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 18),
        title="L'Oreal Universal Registration Document 2025",
        aliases=("L'Oreal", "Loreal", "L'Oreal S.A.", "OR", "OR.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:SU",
        ticker="SU",
        company_name="Schneider Electric SE",
        document_url="https://download.schneider-electric.com/files?p_Doc_Ref=2025-URD&p_enDocType=Institutional+Document&p_File_Name=2025-universal-registration-document.pdf",
        landing_url="https://www.se.com/ww/en/download/document/2025-URD/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 27),
        title="Schneider Electric Universal Registration Document 2025",
        aliases=("Schneider Electric", "Schneider Electric SE", "SU", "SU.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:BNP",
        ticker="BNP",
        company_name="BNP Paribas S.A.",
        document_url="https://invest.bnpparibas/en/document/universal-registration-document-annual-financial-report-2025-pdf",
        landing_url="https://invest.bnpparibas/en/search/reports/documents/financial-reports",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 24),
        title="BNP Paribas Universal Registration Document and Annual Financial Report 2025",
        aliases=("BNP Paribas", "BNP Paribas S.A.", "BNP", "BNP.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:CS",
        ticker="CS",
        company_name="AXA SA",
        document_url="https://www-axa-com.cdn.prismic.io/www-axa-com/aeigScBOoF08xM_E_axa_urd2025_accessibleb_va.pdf",
        landing_url="https://www.axa.com/en/press/publications/2025-annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 24),
        title="AXA Universal Registration Document 2025",
        aliases=("AXA", "AXA SA", "CS", "CS.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:AIR",
        ticker="AIR",
        company_name="Airbus SE",
        document_url="https://www.airbus.com/sites/g/files/jlcbta136/files/2026-02/airbus_se_report_of_the_board_of_directors_fy_2025_1.pdf",
        landing_url="https://www.airbus.com/en/investors/annual-reports",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 19),
        title="Airbus Report of the Board of Directors 2025",
        aliases=("Airbus", "Airbus SE", "AIR", "AIR.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="FR",
        company_id="FR:DG",
        ticker="DG",
        company_name="VINCI SA",
        document_url="https://www.vinci.com/publi/vinci/vinci-2025-universal-registration-document.pdf",
        landing_url="https://www.vinci.com/en/newsroom/press-releases/publication-english-version-2025-universal-registration-document",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 4, 8),
        title="VINCI Universal Registration Document 2025",
        aliases=("VINCI", "VINCI SA", "DG", "DG.PA"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:SIE",
        ticker="SIE",
        company_name="Siemens AG",
        document_url="https://assets.new.siemens.com/siemens/assets/api/uuid:428ea18a-e7ab-4f93-a160-33908f1c3540/Siemens-Annual-Report-2025.pdf",
        landing_url="https://www.siemens.com/global/en/company/investor-relations/annual-reports.html",
        report_end=date(2025, 9, 30),
        published_at=date(2025, 12, 11),
        title="Siemens Annual Report 2025",
        aliases=("Siemens", "Siemens AG", "SIE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:SAP",
        ticker="SAP",
        company_name="SAP SE",
        document_url="https://www.sap.com/docs/download/investors/2025/sap-2025-annual-report-form-20f.pdf",
        landing_url="https://www.sap.com/investors/en/financial-documents-and-events.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="SAP Annual Report on Form 20-F 2025",
        aliases=("SAP", "SAP SE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:DTE",
        ticker="DTE",
        company_name="Deutsche Telekom AG",
        document_url="https://report.telekom.com/annual-report-2025/_assets/downloads/entire-dtag-ar25.pdf",
        landing_url="https://report.telekom.com/annual-report-2025/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="Deutsche Telekom Annual Report 2025",
        aliases=("Deutsche Telekom", "Deutsche Telekom AG", "DTE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:ALV",
        ticker="ALV",
        company_name="Allianz SE",
        document_url="https://www.allianz.com/content/dam/onemarketing/azcom/Allianz_com/investor-relations/en/results-reports/annual-report/ar-2025/en-allianz-group-annual-report-2025.pdf",
        landing_url="https://www.allianz.com/en/investor_relations/results-reports/annual-report.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 6),
        title="Allianz Group Annual Report 2025",
        aliases=("Allianz", "Allianz SE", "ALV", "ALV.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:MBG",
        ticker="MBG",
        company_name="Mercedes-Benz Group AG",
        document_url="https://www.eqs-news.com/media/document/3f8ee8ed-7b5b-441c-bf32-c9e9b94a004a/assets/DE0007100000-JA-2025-EQ-E-00.pdf",
        landing_url="https://www.eqs-news.com/company/mercedes-benz-group-ag/reports/5b70c216-ea7c-11e8-902f-2c44fd856d8c",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 12),
        title="Mercedes-Benz Group Annual Financial Report 2025",
        source_id="exchange_regulatory_news",
        source_name="Exchange regulatory news annual report",
        source_tier="official_direct",
        aliases=("Mercedes-Benz", "Mercedes-Benz Group", "Mercedes-Benz Group AG", "MBG", "MBG.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:BMW",
        ticker="BMW",
        company_name="Bayerische Motoren Werke Aktiengesellschaft",
        document_url="https://www.bmwgroup.com/en/report/2025/downloads/BMW-Group-Financial-Statements-2025-en.pdf",
        landing_url="https://www.bmwgroup.com/en/report/2025/downloads/index.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 12),
        title="BMW Group Financial Statements 2025",
        aliases=("BMW", "BMW Group", "Bayerische Motoren Werke", "Bayerische Motoren Werke Aktiengesellschaft", "BMW.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:VOW3",
        ticker="VOW3",
        company_name="Volkswagen AG",
        document_url="https://uploads.vw-mms.de/system/production/documents/cws/003/212/file_en/41966a4270c8a0a4185b3ab69eaa433cf6892168/annual-report-2025-volkswagen-group.pdf?1773224256=",
        landing_url="https://www.volkswagen-group.com/en/publications/more/annual-report-2025-3212",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 11),
        title="Volkswagen Group Annual Report 2025",
        aliases=("Volkswagen", "Volkswagen AG", "VOW3", "VOW3.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:BAS",
        ticker="BAS",
        company_name="BASF SE",
        document_url="https://www.basf.com/dam/jcr%3Ad54ddca4-f9c7-4b4f-b65e-d6ae3188ee24/basf/www/global/documents/en/investor-relations/calendar-and-publications/reports/2026/BASF_Report_2025.pdf?vid=yj4yetYQHDZenmYJh_de7TrtePiXkswC",
        landing_url="https://report.basf.com/2025/en/services/downloads.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 19),
        title="BASF Report 2025",
        aliases=("BASF", "BASF SE", "BAS", "BAS.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:IFX",
        ticker="IFX",
        company_name="Infineon Technologies AG",
        document_url="https://www.infineon.com/assets/row/public/documents/corporate/investors/annual-reports/2025/2025-annual-report-v01-00-en.pdf",
        landing_url="https://www.infineon.com/about/investor/reports-presentations/annual-reports",
        report_end=date(2025, 9, 30),
        published_at=date(2025, 11, 13),
        title="Infineon Technologies Annual Report 2025",
        aliases=("Infineon", "Infineon Technologies", "Infineon Technologies AG", "IFX", "IFX.DE"),
    ),
    EuAnnualReportCatalogEntry(
        country="DE",
        company_id="DE:MUV2",
        ticker="MUV2",
        company_name="Muenchener Rueckversicherungs-Gesellschaft Aktiengesellschaft in Muenchen",
        document_url="https://www.munichre.com/content/dam/munichre/mrwebsiteslaunches/2025-annual-report/MunichRe-Group-Annual-Report-2025-en.pdf/_jcr_content/renditions/original./MunichRe-Group-Annual-Report-2025-en.pdf",
        landing_url="https://www.munichre.com/en/company/investors/reports-and-presentations/annual-report-2025.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 18),
        title="Munich Re Group Annual Report 2025",
        aliases=(
            "Munich Re",
            "Muenchener Rueck",
            "Muenchener Rueckversicherungs-Gesellschaft Aktiengesellschaft in Muenchen",
            "MUV2",
            "MUV2.DE",
        ),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:ASML",
        ticker="ASML",
        company_name="ASML Holding N.V.",
        document_url="https://ourbrand.asml.com/m/71076aaad607de4d/original/asml-2025-annual-report-based-on-us-gaap.pdf",
        landing_url="https://www.asml.com/en/investors/annual-report/2025/downloads",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 25),
        title="ASML Annual Report 2025 based on US GAAP",
        aliases=("ASML", "ASML Holding", "ASML Holding N.V."),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:PHIA",
        ticker="PHIA",
        company_name="Koninklijke Philips N.V.",
        document_url="https://www.results.philips.com/app/uploads/2026/04/PhilipsFullAnnualReport2025-English.pdf",
        landing_url="https://www.results.philips.com/ar25",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 19),
        title="Philips Annual Report 2025",
        aliases=("Philips", "Koninklijke Philips", "Koninklijke Philips N.V.", "PHIA"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:HEIA",
        ticker="HEIA",
        company_name="Heineken N.V.",
        document_url="https://www.theheinekencompany.com/sites/heineken-corp/files/2026-02/2025_Heineken_NV_Annual_Report_Interactive_100226_FINAL.pdf",
        landing_url="https://www.theheinekencompany.com/investors/results-reports-webcasts-presentations",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 12),
        title="Heineken N.V. Annual Report 2025",
        aliases=("Heineken", "Heineken N.V.", "HEIA"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:SHELL",
        ticker="SHELL",
        company_name="Shell plc",
        document_url="https://www.shell.com/investors/results-and-reporting/annual-report/_jcr_content/root/main/section/promo/links/item0.stream/1774544186011/5727c329a58b5eb7a54442c0a03f562a5aef1159/shell-annual-report-2025-interactive.pdf",
        landing_url="https://www.shell.com/investors/results-and-reporting/annual-report.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 26),
        title="Shell Annual Report and Accounts 2025",
        aliases=("Shell", "Shell plc", "SHELL", "SHELL.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:UNA",
        ticker="UNA",
        company_name="Unilever PLC",
        document_url="https://www.investegate.co.uk/announcement/rns/unilever--ulvr/annual-financial-report/9472197",
        landing_url="https://www.unilever.com/investors/annual-report-and-accounts/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 12),
        title="Unilever Annual Financial Report 2025",
        source_id="exchange_regulatory_news",
        source_name="Exchange regulatory news annual report",
        source_tier="official_direct",
        file_format="html",
        aliases=("Unilever", "Unilever PLC", "UNA", "UNA.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:INGA",
        ticker="INGA",
        company_name="ING Groep N.V.",
        document_url="https://www.ing.com/binaries/content/assets/documents/annual-reports/2025-ing-groep-nv-annual-report.pdf",
        landing_url="https://www.ing.com/investors/financial-performance/annual-reports",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 26),
        title="ING Groep Annual Report 2025",
        aliases=("ING", "ING Groep", "ING Groep N.V.", "INGA", "INGA.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:PRX",
        ticker="PRX",
        company_name="Prosus N.V.",
        document_url="https://www.prosus.com/~/media/Files/P/prosus-corp-v2/results-reports-and-events-archive/annual-report/2025/fy2025-annual-report.pdf",
        landing_url="https://www.prosus.com/investors/financial-information/annual-reports",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 6, 23),
        title="Prosus Annual Report 2025",
        aliases=("Prosus", "Prosus N.V.", "PRX", "PRX.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:ADYEN",
        ticker="ADYEN",
        company_name="Adyen N.V.",
        document_url="https://brand.adyen.com/api/asset/eyJjbGllbnRJZCI6bnVsbCwiaWQiOjEyMjMwOCwidGltZXN0YW1wIjoxNzc1NTY0MjQ0LCJ2ZXJzaW9uIjoxNzcyNjkwMjA3fQ:adyen:DEcCEo4XPo3eDfzj-fjMZ366g2pQkuoZMPARCHC5BoE/download",
        landing_url="https://investors.adyen.com/financials/2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 5),
        title="Adyen Annual Report and Consolidated Financial Statements 2025",
        aliases=("Adyen", "Adyen N.V.", "ADYEN", "ADYEN.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:AD",
        ticker="AD",
        company_name="Koninklijke Ahold Delhaize N.V.",
        document_url="https://media.aholddelhaize.com/media/k0pnhvk5/ad-annual-report-2025-interactive.pdf",
        landing_url="https://www.aholddelhaize.com/investors/annual-reports/2025/",
        report_end=date(2025, 12, 28),
        published_at=date(2026, 2, 25),
        title="Ahold Delhaize Annual Report 2025",
        aliases=("Ahold Delhaize", "Koninklijke Ahold Delhaize", "Koninklijke Ahold Delhaize N.V.", "AD", "AD.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="NL",
        company_id="NL:DSFIR",
        ticker="DSFIR",
        company_name="DSM-Firmenich AG",
        document_url="https://annualreport.dsm-firmenich.com/2025/_assets/downloads/entire-dsmfirmenich-iar25.pdf",
        landing_url="https://annualreport.dsm-firmenich.com/2025/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 20),
        title="DSM-Firmenich Integrated Annual Report 2025",
        aliases=("DSM-Firmenich", "DSM Firmenich", "DSM-Firmenich AG", "DSFIR", "DSFIR.AS"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:NESN",
        ticker="NESN",
        company_name="Nestle S.A.",
        document_url="https://www.nestle.com/sites/default/files/2026-02/annual-review-2025-en.pdf",
        landing_url="https://www.nestle.com/investors/annual-report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 13),
        title="Nestle Annual Review 2025",
        aliases=("Nestle", "Nestle S.A.", "NESN"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:NOVN",
        ticker="NOVN",
        company_name="Novartis AG",
        document_url="https://www.novartis.com/sites/novartis_com/files/novartis-annual-report-2025.pdf",
        landing_url="https://www.novartis.com/news/media-library/novartis-annual-report-2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 1, 30),
        title="Novartis Annual Report 2025",
        aliases=("Novartis", "Novartis AG", "NOVN"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:ROG",
        ticker="ROG",
        company_name="Roche Holding AG",
        document_url="https://assets.roche.com/f/176343/x/fa3c863601/ar25e.pdf",
        landing_url="https://www.roche.com/investors/annualreport25",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 1, 29),
        title="Roche Annual Report 2025",
        aliases=("Roche", "Roche Holding", "Roche Holding AG", "ROG"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:UBSG",
        ticker="UBSG",
        company_name="UBS Group AG",
        document_url="https://www.sec.gov/Archives/edgar/data/1610520/000161052026000023/ubs-20251231.htm",
        landing_url="https://www.sec.gov/Archives/edgar/data/1610520/000161052026000023/",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 9),
        title="UBS Group Annual Report on Form 20-F 2025",
        source_id="sec",
        source_name="SEC EDGAR annual report",
        source_tier="official_mirror",
        file_format="html",
        aliases=("UBS", "UBS Group", "UBS Group AG", "UBSG", "UBSG.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:ZURN",
        ticker="ZURN",
        company_name="Zurich Insurance Group AG",
        document_url="https://edge.sitecorecloud.io/zurichinsur6934-zwpcorp-prod-ae5e/media/project/zurich/dotcom/investor-relations/docs/financial-reports/2025/annual-report-2025-en.pdf",
        landing_url="https://www.zurich.com/annual-report-2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 5),
        title="Zurich Insurance Group Annual Report 2025",
        aliases=("Zurich Insurance", "Zurich Insurance Group", "Zurich Insurance Group AG", "ZURN", "ZURN.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:ABBN",
        ticker="ABBN",
        company_name="ABB Ltd",
        document_url="https://library.e.abb.com/public/c81058c6d8cc4437bba6acf6a43a21d2/ABB%20Integrated%20Report%202025.pdf?x-sign=xTufnLzEP0cwSMWaeZwwfdG%2bl%2fI8oPZgqxHsmIpCBLxfYgnbqr11gvHuOSutYVau",
        landing_url="https://www.abb.com/global/en/company/annual-reporting-suite",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 3),
        title="ABB Integrated Report 2025",
        aliases=("ABB", "ABB Ltd", "ABBN", "ABBN.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:CFR",
        ticker="CFR",
        company_name="Compagnie Financiere Richemont SA",
        document_url="https://www.richemont.com/media/ue1bjrjv/richemont-fy25-annual-report-en.pdf",
        landing_url="https://www.richemont.com/news-media/press-releases-news/fy25-annual-report-and-accounts/",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 5, 16),
        title="Richemont Annual Report and Accounts 2025",
        aliases=("Richemont", "Compagnie Financiere Richemont", "Compagnie Financiere Richemont SA", "CFR", "CFR.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:SREN",
        ticker="SREN",
        company_name="Swiss Re Ltd",
        document_url="https://www.swissre.com/dam/jcr%3Aa7e9dca5-1911-404d-bc9d-a8875ca7cab9/2025-annual-report.pdf",
        landing_url="https://www.swissre.com/investors/financial-calendar.html",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 12),
        title="Swiss Re Annual Report 2025",
        aliases=("Swiss Re", "Swiss Re Ltd", "SREN", "SREN.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:SIKA",
        ticker="SIKA",
        company_name="Sika AG",
        document_url="https://www.sika.com/dms/getdocument.get/25912ef3-3470-49d7-b658-f41ccfb7a317/glo-ar-25-annual-report.pdf",
        landing_url="https://reports.sika.com/en/annual-report-2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 20),
        title="Sika Annual Report 2025",
        aliases=("Sika", "Sika AG", "SIKA", "SIKA.SW"),
    ),
    EuAnnualReportCatalogEntry(
        country="CH",
        company_id="CH:HOLN",
        ticker="HOLN",
        company_name="Holcim Ltd",
        document_url="https://www.holcim.com/sites/holcim/files/docs/27022026-finance-holcim-fy-2025-report-full-en.pdf",
        landing_url="https://www.holcim.com/investors/publications/annual-report-2025",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 27),
        title="Holcim Integrated Annual Report 2025",
        aliases=("Holcim", "Holcim Ltd", "HOLN", "HOLN.SW"),
    ),
)


class EuAnnualReportCatalog:
    SAMPLE_COUNTRY_ORDER = ("GB", "FR", "DE", "NL", "CH")

    COUNTRY_ALIASES = {
        "UK": "GB",
        "GB": "GB",
        "UNITED KINGDOM": "GB",
        "FR": "FR",
        "FRANCE": "FR",
        "DE": "DE",
        "GERMANY": "DE",
        "NL": "NL",
        "NETHERLANDS": "NL",
        "CH": "CH",
        "SWITZERLAND": "CH",
    }

    @classmethod
    def normalize_country(cls, value: object) -> str | None:
        text = str(value or "").strip().upper()
        if not text:
            return None
        return cls.COUNTRY_ALIASES.get(text, text if text in {"GB", "FR", "DE", "NL", "CH"} else None)

    @classmethod
    def sample_filings(
        cls,
        *,
        limit: int = 10,
        report_year: int | None = None,
        country: str | None = None,
    ) -> list[FilingCandidate]:
        if limit <= 0:
            return []
        country_provided = country is not None and bool(str(country).strip())
        target_country = cls.normalize_country(country)
        if country_provided and target_country is None:
            return []
        entries = [
            entry
            for entry in EU_ANNUAL_REPORT_CATALOG
            if report_year is None or entry.report_end.year == report_year
        ]
        if target_country:
            return [cls.filing_candidate(entry) for entry in entries if entry.country == target_country][:limit]

        by_country: dict[str, list[EuAnnualReportCatalogEntry]] = {code: [] for code in cls.SAMPLE_COUNTRY_ORDER}
        for entry in entries:
            if entry.country in by_country:
                by_country[entry.country].append(entry)

        per_country, remainder = divmod(limit, len(cls.SAMPLE_COUNTRY_ORDER))
        target_counts = {
            code: per_country + (1 if index < remainder else 0)
            for index, code in enumerate(cls.SAMPLE_COUNTRY_ORDER)
        }
        selected: list[EuAnnualReportCatalogEntry] = []
        seen: set[str] = set()
        for code in cls.SAMPLE_COUNTRY_ORDER:
            for entry in by_country[code][:target_counts[code]]:
                selected.append(entry)
                seen.add(entry.document_url)

        if len(selected) < limit:
            made_progress = True
            while len(selected) < limit and made_progress:
                made_progress = False
                for code in cls.SAMPLE_COUNTRY_ORDER:
                    for entry in by_country[code]:
                        if entry.document_url in seen:
                            continue
                        selected.append(entry)
                        seen.add(entry.document_url)
                        made_progress = True
                        break
                    if len(selected) >= limit:
                        break

        return [cls.filing_candidate(entry) for entry in selected[:limit]]

    @classmethod
    def resolve_company(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        country: str | None = None,
    ) -> tuple[CompanyEntity, list[CompanyEntity]]:
        matches = cls.match_entries(company_name=company_name, ticker=ticker, company_id=company_id, country=country)
        if not matches:
            raise ValueError(f"EU annual report catalog did not match: {company_id or ticker or company_name or ''}")
        candidates = [cls.company_entity(entry, score=score, reason=reason) for entry, score, reason in matches]
        return candidates[0], candidates

    @classmethod
    def match_entries(
        cls,
        *,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        country: str | None = None,
    ) -> list[tuple[EuAnnualReportCatalogEntry, float, str]]:
        target_country = cls.normalize_country(country)
        raw_identifier = str(company_id or ticker or "").strip()
        if ":" in raw_identifier:
            prefix, suffix = raw_identifier.split(":", 1)
            target_country = target_country or cls.normalize_country(prefix)
            raw_identifier = suffix.strip()
        normalized_identifier = cls._normalize(raw_identifier)
        normalized_query = cls._normalize(company_name or raw_identifier)
        matches: list[tuple[EuAnnualReportCatalogEntry, float, str]] = []
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if target_country and entry.country != target_country:
                continue
            score, reason = cls._score(entry, normalized_identifier=normalized_identifier, normalized_query=normalized_query)
            if score >= 0.55:
                matches.append((entry, score, reason))
        return sorted(matches, key=lambda item: (item[1], item[0].published_at, item[0].company_name), reverse=True)

    @classmethod
    def company_entity(cls, entry: EuAnnualReportCatalogEntry, *, score: float = 0.99, reason: str = "catalog_match") -> CompanyEntity:
        aliases = list(dict.fromkeys([entry.company_name, entry.ticker, entry.company_id, *entry.aliases]))
        return CompanyEntity(
            market=Market.eu,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            exchange=entry.country,
            aliases=aliases,
            confidence=score,
            match_reason=reason,
            metadata={
                "country": entry.country,
                "country_label": cls.country_label(entry.country),
                "source_id": entry.source_id,
                "source_tier": entry.source_tier,
            },
        )

    @classmethod
    def filings_for_company(cls, company: CompanyEntity, report_year: int | None = None) -> list[FilingCandidate]:
        country = cls.normalize_country(company.metadata.get("country") or company.exchange)
        query_keys = {
            cls._normalize(company.company_id),
            cls._normalize(company.ticker),
            cls._normalize(company.company_name),
        }
        candidates = []
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if country and entry.country != country:
                continue
            entry_keys = {
                cls._normalize(entry.company_id),
                cls._normalize(entry.ticker),
                cls._normalize(entry.company_name),
                *(cls._normalize(alias) for alias in entry.aliases),
            }
            if not (query_keys & entry_keys):
                continue
            if report_year is not None and entry.report_end.year != report_year:
                continue
            candidates.append(cls.filing_candidate(entry))
        return sorted(candidates, key=lambda item: (item.report_end, item.published_at), reverse=True)

    @classmethod
    def filing_candidate(cls, entry: EuAnnualReportCatalogEntry) -> FilingCandidate:
        host = urlparse(entry.document_url).netloc
        return FilingCandidate(
            source_id=entry.source_id,
            source_name=entry.source_name,
            source_domain=host,
            market=Market.eu,
            company_id=entry.company_id,
            ticker=entry.ticker,
            company_name=entry.company_name,
            report_type=ReportType.annual,
            report_family=ReportFamily.annual,
            form="annual",
            title=entry.title,
            accession_number=entry.company_id,
            primary_document=urlparse(entry.document_url).path.rsplit("/", 1)[-1] or "annual-report.pdf",
            report_end=entry.report_end,
            published_at=entry.published_at,
            document_url=entry.document_url,
            landing_url=entry.landing_url,
            file_format=entry.file_format,
            language=entry.language,
            metadata={
                "country": entry.country,
                "country_label": cls.country_label(entry.country),
                "source_tier": entry.source_tier,
                "source_note": "Curated issuer/mainstream annual-report download used to provide current-year European annual reports.",
            },
        )

    @staticmethod
    def entry_for_url(document_url: str) -> EuAnnualReportCatalogEntry | None:
        normalized = str(document_url or "").strip()
        for entry in EU_ANNUAL_REPORT_CATALOG:
            if entry.document_url == normalized or entry.landing_url == normalized:
                return entry
        return None

    @staticmethod
    def country_label(country: str | None) -> str:
        return {
            "GB": "UK",
            "FR": "France",
            "DE": "Germany",
            "NL": "Netherlands",
            "CH": "Switzerland",
        }.get(country or "", country or "")

    @classmethod
    def _score(
        cls,
        entry: EuAnnualReportCatalogEntry,
        *,
        normalized_identifier: str,
        normalized_query: str,
    ) -> tuple[float, str]:
        aliases = [entry.company_id, entry.ticker, entry.company_name, *entry.aliases]
        alias_keys = [cls._normalize(alias) for alias in aliases if cls._normalize(alias)]
        if normalized_identifier:
            if normalized_identifier in {cls._normalize(entry.company_id), cls._normalize(entry.ticker)}:
                return 0.99, "identifier_exact"
            for key in alias_keys:
                if normalized_identifier == key:
                    return 0.95, "alias_exact"
        if not normalized_query:
            return -1.0, "empty_query"
        best = 0.0
        reason = "query_mismatch"
        for key in alias_keys:
            if normalized_query == key:
                return 0.96, "company_exact"
            if normalized_query in key:
                best = max(best, 0.88)
                reason = "company_contains_query"
            elif key in normalized_query:
                best = max(best, 0.82)
                reason = "query_contains_company"
            else:
                ratio = SequenceMatcher(None, normalized_query, key).ratio()
                if ratio >= 0.72 and ratio > best:
                    best = 0.70 + (ratio - 0.72) * 0.3
                    reason = "company_fuzzy"
        return best, reason

    @staticmethod
    def _normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
