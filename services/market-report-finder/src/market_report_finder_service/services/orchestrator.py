from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

from market_report_finder_service.core.config import settings
from market_report_finder_service.markets.base import MarketReportFinder
from market_report_finder_service.markets.cn import CnReportFinder
from market_report_finder_service.markets.eu import EuReportFinder
from market_report_finder_service.markets.hk import HkReportFinder
from market_report_finder_service.markets.jp import JpReportFinder
from market_report_finder_service.markets.kr import KrReportFinder
from market_report_finder_service.markets.us import UsReportFinder
from market_report_finder_service.models.schemas import (
    BatchDownloadResponse,
    BatchDownloadResultItem,
    CompanyEntity,
    DirectReportDownloadRequest,
    DirectReportDownloadResponse,
    DownloadedReportFile,
    FilingCandidate,
    LatestReportResponse,
    Market,
    RecentReportsResponse,
    ReportAssistRequest,
    ReportAssistResponse,
    ReportTarget,
    ReportType,
    ResolveCompanyResponse,
    SingleDownloadRequest,
    SelectiveDownloadResponse,
    SelectiveDownloadResultItem,
    SourceCatalogResponse,
)
from market_report_finder_service.services.assist import ReportAssistService
from market_report_finder_service.services.downloader import ReportDownloader


class ReportFinderOrchestrator:
    def __init__(self) -> None:
        self.markets: dict[Market, MarketReportFinder] = {
            Market.cn: CnReportFinder(),
            Market.us: UsReportFinder(),
            Market.hk: HkReportFinder(),
            Market.eu: EuReportFinder(),
            Market.kr: KrReportFinder(),
            Market.jp: JpReportFinder(),
        }
        self.downloader = ReportDownloader()
        self.assistant = ReportAssistService()

    def describe_sources(self) -> SourceCatalogResponse:
        return SourceCatalogResponse(sources=[finder.source_descriptor() for finder in self.markets.values()])

    def assist_reports(self, request: ReportAssistRequest) -> ReportAssistResponse:
        return self.assistant.assist(request)

    def curated_annual_reports(
        self,
        *,
        market: Market,
        report_year: int | None = None,
        limit: int = 10,
    ) -> dict:
        finder = self._market(market)
        if not hasattr(finder, "curated_annual_reports"):
            raise HTTPException(status_code=400, detail=f"{market.value} does not provide curated annual-report samples")
        reports = finder.curated_annual_reports(report_year=report_year, limit=limit)  # type: ignore[attr-defined]
        return {
            "market": market,
            "report_year": report_year,
            "limit": limit,
            "candidates_total": len(reports),
            "reports": reports,
            "ranking_rule": "Curated mainstream companies by market, each resolved to the latest matching annual-report candidate.",
            "checked_at": datetime.now(timezone.utc),
        }

    def resolve_company(
        self,
        *,
        market: Market | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
    ) -> ResolveCompanyResponse:
        effective_market = self._infer_market(
            market=market,
            ticker=ticker,
            company_id=company_id,
            cik=cik,
            company_name=company_name,
        )
        try:
            resolved, candidates = self._market(effective_market).resolve_company(
                company_name=company_name,
                ticker=ticker,
                company_id=company_id,
                cik=cik,
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ResolveCompanyResponse(
            query=company_name or ticker or company_id or cik or "",
            market=effective_market,
            resolved=resolved,
            candidates=candidates,
            candidate_count=len(candidates),
        )

    def list_recent_reports(
        self,
        *,
        market: Market | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
        target: ReportTarget,
        report_year: int | None = None,
        forms: list[str] | None = None,
        include_amendments: bool = False,
        include_earnings: bool = False,
        limit: int = 20,
    ) -> RecentReportsResponse:
        resolved = self._resolve(
            market=market,
            company_name=company_name,
            ticker=ticker,
            company_id=company_id,
            cik=cik,
        )
        finder = self._market(resolved.market)
        try:
            candidates = finder.list_filings(
                resolved,
                target=target,
                forms=forms or [],
                include_amendments=include_amendments,
                include_earnings=include_earnings,
                report_year=report_year,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{resolved.market.value} query failed: {exc}") from exc

        effective_year = report_year
        if effective_year is None and candidates:
            effective_year = max(item.report_end.year for item in candidates)
        filtered = self._filter_by_year(candidates, effective_year)
        ranked = self._rank(filtered)[:limit]
        return RecentReportsResponse(
            query=company_name or ticker or company_id or cik or "",
            market=resolved.market,
            target=target,
            resolved=resolved,
            report_year=effective_year,
            candidates_total=len(filtered),
            reports=ranked,
            ranking_rule="Sorted by report_end, published_at, and market-specific report priority descending.",
        )

    def find_latest_report(
        self,
        *,
        market: Market | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
        target: ReportTarget,
        forms: list[str] | None = None,
        include_amendments: bool = False,
        include_earnings: bool = False,
    ) -> LatestReportResponse:
        response = self.list_recent_reports(
            market=market,
            company_name=company_name,
            ticker=ticker,
            company_id=company_id,
            cik=cik,
            target=target,
            forms=forms,
            include_amendments=include_amendments,
            include_earnings=include_earnings,
            limit=100,
        )
        if not response.reports:
            raise HTTPException(status_code=404, detail="No matching official filing found")
        return LatestReportResponse(
            query=response.query,
            market=response.market,
            target=target,
            resolved=response.resolved,
            selected=response.reports[0],
            candidates_considered=response.candidates_total,
        )

    def download_selected(
        self,
        *,
        market: Market | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
        report_types: list[str] | None = None,
        forms: list[str] | None = None,
        reports: list[FilingCandidate] | None = None,
        report_year: int | None = None,
        include_amendments: bool = False,
        include_earnings: bool = False,
    ) -> SelectiveDownloadResponse:
        resolved = self._resolve(
            market=market,
            company_name=company_name,
            ticker=ticker,
            company_id=company_id,
            cik=cik,
        )
        selected = reports or self._select_for_download(
            resolved,
            report_types=report_types or [],
            forms=forms or [],
            report_year=report_year,
            include_amendments=include_amendments,
            include_earnings=include_earnings,
        )
        if not selected:
            raise HTTPException(status_code=404, detail="No matching official filings selected for download")

        files: list[SelectiveDownloadResultItem] = []
        succeeded = 0
        for candidate in selected:
            try:
                downloaded = self.downloader.download(candidate)
                succeeded += 1
                files.append(self._download_result(candidate, downloaded))
            except Exception as exc:
                files.append(self._download_error(candidate, exc))

        return SelectiveDownloadResponse(
            market=resolved.market,
            company_name=resolved.company_name,
            ticker=resolved.ticker,
            company_id=resolved.company_id,
            total=len(selected),
            succeeded=succeeded,
            failed=len(selected) - succeeded,
            files=files,
            download_dir=str(Path(settings.download_dir).expanduser().resolve()),
            checked_at=datetime.now(timezone.utc),
        )

    def download_direct(self, request: DirectReportDownloadRequest) -> DirectReportDownloadResponse:
        candidate = self._market(request.market).direct_candidate(request)
        downloaded = self.downloader.download(candidate)
        return DirectReportDownloadResponse(
            market=request.market,
            company_name=request.company_name,
            ticker=request.ticker,
            company_id=candidate.company_id,
            document_url=request.document_url,
            landing_url=candidate.landing_url,
            form=request.form,
            downloaded_file=downloaded,
        )

    def download_batch(
        self,
        *,
        items: list,
        default_company_name: str,
        market: Market | None = None,
    ) -> BatchDownloadResponse:
        results: list[BatchDownloadResultItem] = []
        for item in items:
            effective_market = item.market or market or self._infer_market_from_url_or_identifier(
                document_url=item.document_url,
                ticker=item.ticker,
                company_id=item.company_id,
            )
            candidate = self._market(effective_market).batch_candidate(item, default_company_name=default_company_name)
            company_name = item.company_name or default_company_name
            try:
                downloaded = self.downloader.download(candidate)
                results.append(
                    BatchDownloadResultItem(
                        document_url=item.document_url,
                        company_name=company_name,
                        file_name=downloaded.file_name,
                        saved_path=downloaded.saved_path,
                        size_bytes=downloaded.size_bytes,
                        success=True,
                    )
                )
            except Exception as exc:
                results.append(
                    BatchDownloadResultItem(
                        document_url=item.document_url,
                        company_name=company_name,
                        file_name="",
                        size_bytes=0,
                        success=False,
                        error=str(exc),
                    )
                )
        succeeded = sum(1 for result in results if result.success)
        return BatchDownloadResponse(
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=results,
            checked_at=datetime.now(timezone.utc),
        )

    def download_single(
        self,
        *,
        market: Market | None = None,
        company_name: str,
        document_url: str,
        title: str | None = None,
        ticker: str | None = None,
    ) -> DownloadedReportFile:
        item = SingleDownloadRequest(
            market=market,
            company_name=company_name,
            document_url=document_url,
            title=title,
            ticker=ticker,
        )
        effective_market = market or self._infer_market_from_url_or_identifier(
            document_url=document_url,
            ticker=ticker,
            company_id=None,
        )
        candidate = self._market(effective_market).batch_candidate(item, default_company_name=company_name)
        return self.downloader.download(candidate)

    def _resolve(
        self,
        *,
        market: Market | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        company_id: str | None = None,
        cik: str | None = None,
    ) -> CompanyEntity:
        effective_market = self._infer_market(
            market=market,
            ticker=ticker,
            company_id=company_id,
            cik=cik,
            company_name=company_name,
        )
        try:
            resolved, _ = self._market(effective_market).resolve_company(
                company_name=company_name,
                ticker=ticker,
                company_id=company_id,
                cik=cik,
            )
            return resolved
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _select_for_download(
        self,
        resolved: CompanyEntity,
        *,
        report_types: list[str],
        forms: list[str],
        report_year: int | None,
        include_amendments: bool,
        include_earnings: bool,
    ) -> list[FilingCandidate]:
        finder = self._market(resolved.market)
        effective_forms = forms or finder.forms_for_report_types(report_types)
        target = finder.target_for_forms_or_types(effective_forms, report_types)
        candidates = finder.list_filings(
            resolved,
            target=target,
            forms=effective_forms,
            include_amendments=include_amendments,
            include_earnings=include_earnings,
            report_year=report_year,
        )
        filtered = self._filter_by_year(candidates, report_year)
        quarter_months = finder.quarter_months_from_report_types(report_types) if resolved.market in {Market.hk, Market.kr, Market.jp} else set()
        if quarter_months:
            filtered = [candidate for candidate in filtered if candidate.report_end.month in quarter_months]
        by_key: dict[str, FilingCandidate] = {}
        for candidate in self._rank(filtered):
            if quarter_months:
                key = str(candidate.report_end.month)
            else:
                key = candidate.report_type.value if effective_forms else candidate.report_family.value
            by_key.setdefault(key, candidate)
        return list(by_key.values())

    def _market(self, market: Market) -> MarketReportFinder:
        try:
            return self.markets[market]
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Unsupported market: {market}") from exc

    @staticmethod
    def _infer_market(
        *,
        market: Market | None,
        ticker: str | None,
        company_id: str | None,
        cik: str | None,
        company_name: str | None = None,
    ) -> Market:
        if market is not None:
            return market
        if cik:
            return Market.us
        identifier = (ticker or company_id or "").strip().upper()
        if re.match(r"^(SH|SSE|SZ|SZSE|BJ|BSE)[:.\s-]*\d{6}$", identifier):
            return Market.cn
        if re.match(r"^\d{6}$", identifier):
            return Market.cn
        if re.match(r"^(HK|HKG|HKEX)[:.\s-]*\d{1,5}$", identifier) or identifier.endswith(".HK"):
            return Market.hk
        if re.match(r"^(EU|EUR|GB|UK|FR|DE|NL|CH)[:.\s-]*[A-Z0-9]{2,}$", identifier):
            return Market.eu
        if re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", identifier) or re.match(r"^[A-Z0-9]{20}$", identifier):
            return Market.eu
        if re.match(r"^(KR|KOR|KRX)[:.\s-]*\d{6}$", identifier) or identifier.endswith(".KS") or identifier.endswith(".KQ"):
            return Market.kr
        if re.match(r"^(JP|JPN|TSE|TYO|JPX)[:.\s-]*\d{4,5}$", identifier) or identifier.endswith(".T"):
            return Market.jp
        digits = re.sub(r"\D+", "", identifier)
        if digits and len(digits) in {4, 5}:
            return Market.jp if len(digits) == 4 else Market.hk
        if digits and len(digits) == 6:
            return Market.kr
        normalized_name = (company_name or "").strip().lower()
        if normalized_name and any(token in normalized_name for token in ("三星", "现代汽车", "sk海力士", "samsung", "hyundai", "hynix")):
            return Market.kr
        if normalized_name and any(token in normalized_name for token in ("丰田", "索尼", "任天堂", "铠侠", "鎧俠", "toyota", "sony", "nintendo", "kioxia")):
            return Market.jp
        if normalized_name and any(token in normalized_name for token in ("asml", "siemens", "lvmh", "airbus", "astrazeneca", "unilever", "shell")):
            return Market.eu
        if company_name and re.search(r"[\u4e00-\u9fff]", company_name):
            return Market.cn
        return Market.us

    @staticmethod
    def _filter_by_year(candidates: list[FilingCandidate], report_year: int | None) -> list[FilingCandidate]:
        if report_year is not None:
            return [candidate for candidate in candidates if candidate.report_end.year == report_year]
        return candidates

    @staticmethod
    def _rank(candidates: list[FilingCandidate]) -> list[FilingCandidate]:
        priority = {
            ReportType.form_10k: 7,
            ReportType.form_20f: 6,
            ReportType.annual: 5,
            ReportType.semiannual: 4,
            ReportType.form_10q: 3,
            ReportType.quarterly: 2,
            ReportType.q3: 2,
            ReportType.q1: 2,
            ReportType.form_6k: 1,
            ReportType.earnings: 0,
        }
        return sorted(
            candidates,
            key=lambda item: (item.report_end, item.published_at, priority.get(item.report_type, 0)),
            reverse=True,
        )

    @staticmethod
    def _infer_market_from_url_or_identifier(
        *,
        document_url: str,
        ticker: str | None,
        company_id: str | None,
    ) -> Market:
        host = urlparse(document_url).netloc.lower()
        if "cninfo.com.cn" in host:
            return Market.cn
        if "hkexnews.hk" in host or "hkex.com.hk" in host:
            return Market.hk
        if (
            "filings.xbrl.org" in host
            or "annualreports.ai" in host
            or "financialreports.eu" in host
            or "financialfilings.com" in host
            or "fca.org.uk" in host
            or "info-financiere.fr" in host
            or "amf-france.org" in host
            or "unternehmensregister.de" in host
            or "bundesanzeiger.de" in host
            or "afm.nl" in host
            or "six-group.com" in host
            or "ser-ag.com" in host
            or "astrazeneca.com" in host
            or "bp.com" in host
            or "barclays" in host
            or "totalenergies.com" in host
            or "sanofi.com" in host
            or "airliquide.com" in host
            or "siemens.com" in host
            or "sap.com" in host
            or "telekom.com" in host
            or "asml.com" in host
            or "philips.com" in host
            or "heinekencompany.com" in host
            or "nestle.com" in host
            or "novartis.com" in host
            or "roche.com" in host
        ):
            return Market.eu
        if "dart.fss.or.kr" in host or "opendart.fss.or.kr" in host or "kind.krx.co.kr" in host:
            return Market.kr
        if (
            "edinet-fsa.go.jp" in host
            or "toyota" in host
            or "mufg.jp" in host
            or "nintendo.co.jp" in host
            or "fastretailing.com" in host
            or "hitachi.com" in host
            or "group.ntt" in host
            or "daikin.com" in host
            or "fujitsu" in host
            or "itochu.co.jp" in host
            or "shiseido.com" in host
        ):
            return Market.jp
        if "sec.gov" in host:
            return Market.us
        return ReportFinderOrchestrator._infer_market(market=None, ticker=ticker, company_id=company_id, cik=None)

    @staticmethod
    def _download_result(candidate: FilingCandidate, downloaded: DownloadedReportFile) -> SelectiveDownloadResultItem:
        return SelectiveDownloadResultItem(
            title=candidate.title,
            form=candidate.form,
            report_type=candidate.report_type,
            report_family=candidate.report_family,
            report_end=candidate.report_end,
            published_at=candidate.published_at,
            accession_number=candidate.accession_number,
            document_url=candidate.document_url,
            file_name=downloaded.file_name,
            saved_path=downloaded.saved_path,
            size_bytes=downloaded.size_bytes,
            cache_hit=downloaded.cache_hit,
        )

    @staticmethod
    def _download_error(candidate: FilingCandidate, exc: Exception) -> SelectiveDownloadResultItem:
        return SelectiveDownloadResultItem(
            title=candidate.title,
            form=candidate.form,
            report_type=candidate.report_type,
            report_family=candidate.report_family,
            report_end=candidate.report_end,
            published_at=candidate.published_at,
            accession_number=candidate.accession_number,
            document_url=candidate.document_url,
            file_name="",
            saved_path="",
            size_bytes=0,
            cache_hit=False,
            error=str(exc),
        )
