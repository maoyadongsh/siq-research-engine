from __future__ import annotations

import re
import time
from datetime import date, datetime
from html import unescape
from urllib.parse import urlencode

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import CompanyEntity, FilingCandidate, Market, ReportFamily, ReportTarget, ReportType


class DartPublicClient:
    MAIN_URL = "https://dart.fss.or.kr/dsab007/main.do"
    SEARCH_URL = "https://dart.fss.or.kr/dsab007/detailSearch.ax"
    VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"
    PDF_DOWNLOAD_URL = "https://dart.fss.or.kr/pdf/download/pdf.do"
    PDF_LANDING_URL = "https://dart.fss.or.kr/pdf/download/main.do"
    COMBINED_HTML_URL = "https://dart.fss.or.kr/report/combined.do"

    REPORT_NAMES = {
        ReportType.annual: "사업보고서",
        ReportType.semiannual: "반기보고서",
        ReportType.quarterly: "분기보고서",
        ReportType.q1: "분기보고서",
        ReportType.q3: "분기보고서",
    }

    def list_filings(
        self,
        company: CompanyEntity,
        *,
        target: ReportTarget = ReportTarget.financial_report,
        forms: list[str] | None = None,
        report_year: int | None = None,
    ) -> list[FilingCandidate]:
        allowed = self._allowed_types(target=target, forms=forms or [])
        candidates: list[FilingCandidate] = []
        with self._client() as client:
            client.get(self.MAIN_URL)
            for report_type in sorted(allowed, key=lambda item: item.value):
                response = client.post(self.SEARCH_URL, data=self._search_payload(company, report_type, report_year=report_year))
                response.raise_for_status()
                parsed_candidates = self._parse_search_html(company, response.text, expected_report_type=report_type)
                candidates.extend(self._with_download_url(client, candidate) for candidate in parsed_candidates)
                time.sleep(0.05)
        return self._dedupe_candidates(candidates)

    @classmethod
    def _search_payload(cls, company: CompanyEntity, report_type: ReportType, *, report_year: int | None) -> dict[str, str]:
        report_name = cls.REPORT_NAMES.get(report_type, "사업보고서")
        start_date, end_date = cls._date_range(report_type, report_year=report_year)
        query = str(company.ticker or company.company_name).strip()
        return {
            "currentPage": "1",
            "maxResults": "15",
            "maxLinks": "10",
            "sort": "date",
            "series": "desc",
            "textCrpNm": query,
            "textCrpNm2": query,
            "textCrpCik": "",
            "textPresenterNm": "",
            "reportName": report_name,
            "reportName2": report_name,
            "tocSrch": "",
            "tocSrch2": "",
            "startDate": start_date,
            "endDate": end_date,
            "finalReport": "recent",
            "businessCode": "all",
            "businessNm": "전체",
            "corporationType": "all",
            "closingAccountsMonth": "all",
            "option": "corp",
            "autoSearch": "N",
            "autoSearchCorp": "Y",
        }

    @classmethod
    def _parse_search_html(
        cls,
        company: CompanyEntity,
        html: str,
        *,
        expected_report_type: ReportType,
    ) -> list[FilingCandidate]:
        candidates: list[FilingCandidate] = []
        for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S):
            row = row_match.group(1)
            receipt_match = re.search(r"rcpNo=(\d+)", row)
            if not receipt_match:
                continue
            receipt_no = receipt_match.group(1)
            link_match = re.search(r"<a[^>]+rcpNo=\d+[^>]*>(.*?)</a>", row, re.I | re.S)
            title_text = cls._clean_html(link_match.group(1) if link_match else "")
            if not title_text:
                continue
            published_match = re.search(r"<td[^>]*>\s*(20\d{2})[.](\d{2})[.](\d{2})\s*</td>", row, re.I)
            published_at = (
                date(int(published_match.group(1)), int(published_match.group(2)), int(published_match.group(3)))
                if published_match
                else date.today()
            )
            report_type, family = cls._infer_report_type(title_text, expected_report_type)
            report_end = cls._infer_report_end(title_text, report_type, published_at)
            viewer_url = cls.viewer_url(receipt_no)
            document_url = cls.combined_html_url(receipt_no)
            candidates.append(
                FilingCandidate(
                    source_id="dart_public",
                    source_name="DART public disclosure viewer",
                    source_domain="dart.fss.or.kr",
                    market=Market.kr,
                    company_id=company.company_id,
                    ticker=company.ticker,
                    company_name=company.company_name,
                    report_type=report_type,
                    report_family=family,
                    form=report_type.value,
                    title=title_text,
                    accession_number=receipt_no,
                    primary_document=f"{receipt_no}.html",
                    report_end=report_end,
                    published_at=published_at,
                    document_url=document_url,
                    landing_url=viewer_url,
                    file_format="html",
                    language="ko",
                    metadata={
                        "corp_code": company.company_id,
                        "stock_code": company.ticker,
                        "dart_viewer_url": viewer_url,
                        "source_tier": "statutory_public_html",
                        "document_format": "dart_public_combined_html",
                    },
                )
            )
        return candidates

    @classmethod
    def _with_download_url(cls, client: httpx.Client, candidate: FilingCandidate) -> FilingCandidate:
        receipt_no = candidate.accession_number
        if not receipt_no:
            return candidate
        try:
            response = client.get(candidate.landing_url)
            response.raise_for_status()
        except Exception:
            return candidate
        dcm_no = cls._parse_viewer_dcm_no(response.text, receipt_no=receipt_no)
        if not dcm_no:
            return candidate
        metadata = dict(candidate.metadata)
        metadata.update(
            {
                "dcm_no": dcm_no,
                "dart_pdf_landing_url": cls.pdf_landing_url(receipt_no, dcm_no),
                "source_tier": "statutory_public_pdf",
                "document_format": "dart_public_pdf",
            }
        )
        return candidate.model_copy(
            update={
                "source_name": "DART public disclosure PDF",
                "document_url": cls.pdf_document_url(receipt_no, dcm_no),
                "primary_document": f"{receipt_no}.pdf",
                "file_format": "pdf",
                "metadata": metadata,
            }
        )

    @classmethod
    def _parse_viewer_dcm_no(cls, html: str, *, receipt_no: str) -> str | None:
        escaped_receipt = re.escape(receipt_no)
        view_doc_pattern = re.compile(
            rf"viewDoc\(\s*['\"]{escaped_receipt}['\"]\s*,\s*['\"](?P<dcm_no>\d+)['\"]",
            re.I,
        )
        match = view_doc_pattern.search(html)
        if match:
            return match.group("dcm_no")

        node_pattern = re.compile(
            rf"node1\[['\"]rcpNo['\"]\]\s*=\s*['\"]{escaped_receipt}['\"].*?"
            r"node1\[['\"]dcmNo['\"]\]\s*=\s*['\"](?P<dcm_no>\d+)['\"]",
            re.I | re.S,
        )
        match = node_pattern.search(html)
        return match.group("dcm_no") if match else None

    @classmethod
    def viewer_url(cls, receipt_no: str) -> str:
        return f"{cls.VIEWER_URL}?{urlencode({'rcpNo': receipt_no})}"

    @classmethod
    def combined_html_url(cls, receipt_no: str) -> str:
        return f"{cls.COMBINED_HTML_URL}?{urlencode({'rcpNo': receipt_no})}"

    @classmethod
    def pdf_landing_url(cls, receipt_no: str, dcm_no: str) -> str:
        return f"{cls.PDF_LANDING_URL}?{urlencode({'rcp_no': receipt_no, 'dcm_no': dcm_no})}"

    @classmethod
    def pdf_document_url(cls, receipt_no: str, dcm_no: str) -> str:
        return f"{cls.PDF_DOWNLOAD_URL}?{urlencode({'rcp_no': receipt_no, 'dcm_no': dcm_no})}"

    @staticmethod
    def _clean_html(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", unescape(text)).strip()

    @staticmethod
    def _date_range(report_type: ReportType, *, report_year: int | None) -> tuple[str, str]:
        if report_year is None:
            current = date.today().year
            return f"{current - 2}0101", f"{current + 1}1231"
        if report_type == ReportType.annual:
            return f"{report_year + 1}0101", f"{report_year + 1}1231"
        return f"{report_year}0101", f"{report_year + 1}1231"

    @classmethod
    def _infer_report_type(cls, title: str, fallback: ReportType) -> tuple[ReportType, ReportFamily]:
        normalized = title.lower()
        if "사업보고서" in normalized or "annual report" in normalized:
            return ReportType.annual, ReportFamily.annual
        if "반기보고서" in normalized or "half-year" in normalized or "semiannual" in normalized or "semi-annual" in normalized:
            return ReportType.semiannual, ReportFamily.semiannual
        if "분기보고서" in normalized or "quarterly report" in normalized:
            return ReportType.quarterly, ReportFamily.quarterly
        if fallback == ReportType.semiannual:
            return fallback, ReportFamily.semiannual
        if fallback in {ReportType.quarterly, ReportType.q1, ReportType.q3}:
            return ReportType.quarterly, ReportFamily.quarterly
        return ReportType.annual, ReportFamily.annual

    @staticmethod
    def _infer_report_end(title: str, report_type: ReportType, published_at: date) -> date:
        year_month_match = re.search(r"(20\d{2})[.\-/年]\s*(0?[1-9]|1[0-2])", title)
        if year_month_match:
            year = int(year_month_match.group(1))
            month = int(year_month_match.group(2))
            if report_type == ReportType.annual:
                return date(year, 12, 31)
            if report_type == ReportType.semiannual:
                return date(year, 6, 30)
            if month <= 3:
                return date(year, 3, 31)
            if month <= 6:
                return date(year, 6, 30)
            if month <= 9:
                return date(year, 9, 30)
            return date(year, 12, 31)
        year_match = re.search(r"20\d{2}", title)
        year = int(year_match.group(0)) if year_match else published_at.year
        if report_type == ReportType.annual:
            return date(year, 12, 31)
        if report_type == ReportType.semiannual:
            return date(year, 6, 30)
        if "3분기" in title or "third" in title.lower() or "q3" in title.lower():
            return date(year, 9, 30)
        if "1분기" in title or "first" in title.lower() or "q1" in title.lower():
            return date(year, 3, 31)
        return published_at

    @classmethod
    def _allowed_types(cls, *, target: ReportTarget, forms: list[str]) -> set[ReportType]:
        explicit = {cls._form_to_report_type(form) for form in forms if cls._form_to_report_type(form)}
        if explicit:
            return explicit
        if target == ReportTarget.annual_report:
            return {ReportType.annual}
        if target == ReportTarget.semiannual_report:
            return {ReportType.semiannual}
        if target == ReportTarget.quarterly_report:
            return {ReportType.quarterly}
        return {ReportType.annual, ReportType.semiannual, ReportType.quarterly}

    @staticmethod
    def _form_to_report_type(form: str) -> ReportType | None:
        normalized = form.strip().lower().replace("_", "-")
        mapping = {
            "annual": ReportType.annual,
            "annual-report": ReportType.annual,
            "business-report": ReportType.annual,
            "semiannual": ReportType.semiannual,
            "semi-annual": ReportType.semiannual,
            "half-year": ReportType.semiannual,
            "interim": ReportType.semiannual,
            "quarterly": ReportType.quarterly,
            "quarterly-report": ReportType.quarterly,
            "q1": ReportType.quarterly,
            "q3": ReportType.quarterly,
        }
        return mapping.get(normalized)

    @staticmethod
    def _dedupe_candidates(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        by_receipt: dict[str, FilingCandidate] = {}
        for candidate in candidates:
            key = candidate.accession_number or candidate.document_url
            by_receipt.setdefault(key, candidate)
        return list(by_receipt.values())

    @staticmethod
    def _client() -> httpx.Client:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": DartPublicClient.MAIN_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
        return httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True)
