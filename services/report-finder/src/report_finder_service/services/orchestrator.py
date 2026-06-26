from datetime import date, datetime, timezone
import zipfile

from fastapi import HTTPException

from report_finder_service.adapters.cninfo import CninfoAdapter
from report_finder_service.core.config import settings
from report_finder_service.models.schemas import (
    BatchDownloadResponse,
    BatchDownloadResultItem,
    DirectReportDownloadResponse,
    DownloadedReportFile,
    LatestReportDownloadResponse,
    LatestReportResponse,
    Market,
    RecentReportsResponse,
    ReportCandidate,
    ReportTarget,
    ReportType,
    ResolveCompanyResponse,
    SourceCatalogResponse,
    SourceDescriptor,
)
from report_finder_service.services.company_resolver import CompanyResolver
from report_finder_service.services.latest_selector import LatestReportSelector
from report_finder_service.services.report_downloader import ReportDownloader
from report_finder_service.services.source_router import SourceRouter


class ReportFinderOrchestrator:
    def __init__(self) -> None:
        self.resolver = CompanyResolver()
        self.router = SourceRouter()
        self.selector = LatestReportSelector()
        self.downloader = ReportDownloader()
        self.adapters = {
            "cninfo": CninfoAdapter(),
        }

    def describe_sources(self) -> SourceCatalogResponse:
        return SourceCatalogResponse(
            sources=[adapter.describe() for adapter in self.adapters.values()]
        )

    def resolve_company(
        self,
        company_name: str,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> ResolveCompanyResponse:
        try:
            resolved, candidates = self.resolver.resolve_with_candidates(
                company_name=company_name,
                ticker=ticker,
                exchange_hint=exchange_hint,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ResolveCompanyResponse(
            query=company_name,
            resolved=resolved,
            candidates=candidates,
            candidate_count=len(candidates),
        )

    def find_latest_report(
        self,
        company_name: str,
        target: ReportTarget,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ) -> LatestReportResponse:
        resolved, candidates, selected, selection_evidence = self._resolve_and_select(
            company_name=company_name,
            target=target,
            ticker=ticker,
            exchange_hint=exchange_hint,
        )

        return LatestReportResponse(
            query=company_name,
            target=target,
            resolved=resolved,
            selected=selected,
            candidates_considered=len(candidates),
            selection_evidence=selection_evidence,
        )

    def list_recent_reports(
        self,
        company_name: str | None,
        target: ReportTarget,
        ticker: str | None = None,
        exchange_hint: str | None = None,
        report_year: int | None = None,
        include_earnings: bool = False,
        limit: int = 20,
    ) -> RecentReportsResponse:
        query = company_name or ticker or ""
        try:
            resolved, _ = self.resolver.resolve_with_candidates(
                company_name=query,
                ticker=ticker,
                exchange_hint=exchange_hint,
            )
            source_id = self.router.route(resolved)
            adapter = self.adapters[source_id]
            candidates = adapter.search(resolved, target=target)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        filtered = self._filter_recent_report_candidates(
            candidates=candidates,
            target=target,
            report_year=report_year,
            include_earnings=include_earnings,
        )
        ranked = sorted(
            filtered,
            key=lambda item: (
                item.report_end,
                item.published_at,
                self.selector.TYPE_PRIORITY.get(item.report_type, 0),
            ),
            reverse=True,
        )[:limit]
        effective_year = report_year
        if effective_year is None and ranked:
            effective_year = max(item.report_end.year for item in ranked)

        return RecentReportsResponse(
            query=query,
            target=target,
            resolved=resolved,
            report_year=effective_year,
            candidates_total=len(filtered),
            reports=ranked,
            checked_at=datetime.now(timezone.utc),
            ranking_rule=(
                "返回指定报告年度或候选中最近报告年度的正式定期报告；"
                "按 report_end、published_at、report_type_priority 倒序排序。"
            ),
        )

    def find_latest_report_and_download(
        self,
        company_name: str,
        target: ReportTarget,
        ticker: str | None = None,
        exchange_hint: str | None = None,
        sub_dir: str | None = None,
    ) -> LatestReportDownloadResponse:
        resolved, candidates, selected, selection_evidence = self._resolve_and_select(
            company_name=company_name,
            target=target,
            ticker=ticker,
            exchange_hint=exchange_hint,
        )
        effective_sub_dir = sub_dir if sub_dir is not None else resolved.canonical_name
        downloaded_file = self.downloader.download(selected, sub_dir=effective_sub_dir)

        return LatestReportDownloadResponse(
            query=company_name,
            target=target,
            resolved=resolved,
            selected=selected,
            candidates_considered=len(candidates),
            selection_evidence=selection_evidence,
            downloaded_file=downloaded_file,
        )

    def download_single(
        self,
        *,
        company_name: str,
        document_url: str,
        title: str | None = None,
        sub_dir: str | None = None,
        ticker: str | None = None,
    ) -> DownloadedReportFile:
        today = datetime.now(timezone.utc).date()
        from urllib.parse import urlparse
        source_domain = urlparse(document_url).netloc or "manual"
        candidate = ReportCandidate(
            source_id="manual",
            source_name="manual",
            source_domain=source_domain,
            company_name=company_name,
            ticker=self._resolve_ticker(company_name, ticker),
            market=Market.cn,
            report_type=ReportType.annual,
            title=title or "unknown",
            report_end=today,
            published_at=today,
            document_url=document_url,
            landing_url=document_url,
            file_format="pdf",
        )
        try:
            return self.downloader.download(candidate, sub_dir=sub_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"下载失败: {exc}") from exc

    def download_batch(
        self,
        *,
        items: list,
        default_company_name: str,
    ) -> BatchDownloadResponse:
        """按公司名分子目录，逐个下载，不打包zip"""
        results: list[BatchDownloadResultItem] = []

        for idx, item in enumerate(items):
            company_name = item.company_name or default_company_name
            title = item.title or f"report_{idx+1}"
            try:
                if item.report_type and item.report_end and item.published_at:
                    from urllib.parse import urlparse

                    source_domain = urlparse(item.document_url).netloc or "manual"
                    candidate = ReportCandidate(
                        source_id="manual",
                        source_name="manual",
                        source_domain=source_domain,
                        company_name=company_name,
                        ticker=self._resolve_ticker(company_name, item.ticker),
                        market=Market.cn,
                        report_type=item.report_type,
                        title=title,
                        report_end=item.report_end,
                        published_at=item.published_at,
                        document_url=item.document_url,
                        landing_url=item.document_url,
                        file_format="pdf",
                    )
                    downloaded = self.downloader.download(candidate, sub_dir=company_name)
                else:
                    downloaded = self.download_single(
                        company_name=company_name,
                        document_url=item.document_url,
                        title=title,
                        sub_dir=company_name,
                        ticker=item.ticker,
                    )
                results.append(BatchDownloadResultItem(
                    document_url=item.document_url,
                    company_name=company_name,
                    file_name=downloaded.file_name,
                    size_bytes=downloaded.size_bytes,
                    success=True,
                    error=None,
                ))
            except HTTPException as exc:
                results.append(BatchDownloadResultItem(
                    document_url=item.document_url,
                    company_name=company_name,
                    file_name="",
                    size_bytes=0,
                    success=False,
                    error=exc.detail,
                ))
            except Exception as exc:
                results.append(BatchDownloadResultItem(
                    document_url=item.document_url,
                    company_name=company_name,
                    file_name="",
                    size_bytes=0,
                    success=False,
                    error=str(exc),
                ))

        succeeded = sum(1 for r in results if r.success)
        return BatchDownloadResponse(
            total=len(items),
            succeeded=succeeded,
            failed=len(items) - succeeded,
            results=results,
            zip_file_name="",
            checked_at=datetime.now(timezone.utc),
        )

    def _resolve_ticker(self, company_name: str, ticker: str | None = None) -> str:
        if ticker:
            return ticker
        try:
            resolved, _ = self.resolver.resolve_with_candidates(company_name=company_name)
            return resolved.ticker
        except Exception:
            return "unknown"

    def download_selected(
        self,
        *,
        company_name: str | None,
        ticker: str | None,
        exchange_hint: str | None,
        report_types: list[str],
        reports: list[ReportCandidate] | None = None,
        report_year: int | None = None,
    ):
        """
        一站式：查询列表 → 按类型筛选 → 逐个下载到 downloads/ → 返回结果JSON
        """
        from report_finder_service.models.schemas import SelectiveDownloadResultItem
        from pathlib import Path

        # 1. 解析公司（company_name 和 ticker 至少一个）
        query_name = company_name or ticker or ""
        resolved, _ = self.resolver.resolve_with_candidates(
            company_name=query_name,
            ticker=ticker,
            exchange_hint=exchange_hint,
        )
        if reports:
            selected = reports
        else:
            source_id = self.router.route(resolved)
            adapter = self.adapters[source_id]

            # 2. 查询候选列表
            candidates = adapter.search(resolved, target=ReportTarget.financial_report)

            # 3. 按类型和年份筛选
            type_map = {
                "annual": ReportType.annual,
                "semiannual": ReportType.semiannual,
                "q1": ReportType.q1,
                "q3": ReportType.q3,
            }
            allowed_types = {type_map[rt] for rt in report_types if rt in type_map}

            filtered = [c for c in candidates if c.report_type in allowed_types]
            if report_year is not None:
                filtered = [c for c in filtered if c.report_end.year == report_year]

            # 去重：同类型只保留最新的。旧客户端只传 report_types 时保留兼容行为。
            seen_types: dict[ReportType, ReportCandidate] = {}
            for c in sorted(filtered, key=lambda x: (x.report_end, x.published_at), reverse=True):
                if c.report_type not in seen_types:
                    seen_types[c.report_type] = c
            selected = list(seen_types.values())

        if not selected:
            raise HTTPException(
                status_code=404,
                detail=f"未找到符合条件的报告: types={report_types}, year={report_year}"
            )

        # 4. 逐个下载到本地 downloads/
        files: list[SelectiveDownloadResultItem] = []
        succeeded = 0
        for c in selected:
            try:
                # 直接用真实候选信息下载，按公司名+报告类型分目录
                downloaded = self.downloader.download(c, sub_dir=resolved.canonical_name)
                files.append(SelectiveDownloadResultItem(
                    title=c.title,
                    report_type=c.report_type.value,
                    report_end=c.report_end,
                    published_at=c.published_at,
                    document_url=c.document_url,
                    file_name=downloaded.file_name,
                    saved_path=downloaded.saved_path,
                    size_bytes=downloaded.size_bytes,
                    cache_hit=downloaded.cache_hit,
                ))
                succeeded += 1
            except Exception as exc:
                files.append(SelectiveDownloadResultItem(
                    title=c.title,
                    report_type=c.report_type.value,
                    report_end=c.report_end,
                    published_at=c.published_at,
                    document_url=c.document_url,
                    file_name="",
                    saved_path="",
                    size_bytes=0,
                    cache_hit=False,
                ))

        download_dir = Path(settings.download_dir).expanduser().resolve()
        return {
            "company_name": resolved.canonical_name,
            "ticker": resolved.ticker,
            "total": len(selected),
            "succeeded": succeeded,
            "failed": len(selected) - succeeded,
            "files": files,
            "download_dir": str(download_dir),
            "checked_at": datetime.now(timezone.utc),
        }

    def download_direct_official_report(
        self,
        *,
        company_name: str,
        document_url: str,
        landing_url: str | None,
        source_name: str,
        report_type,
        report_end,
        published_at,
    ) -> DirectReportDownloadResponse:
        effective_report_end = report_end or datetime.now(timezone.utc).date()
        effective_published_at = published_at or effective_report_end
        try:
            return self.downloader.download_direct(
                company_name=company_name,
                document_url=document_url,
                landing_url=landing_url,
                source_name=source_name,
                report_type=report_type,
                report_end=effective_report_end,
                published_at=effective_published_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _resolve_and_select(
        self,
        company_name: str,
        target: ReportTarget,
        ticker: str | None = None,
        exchange_hint: str | None = None,
    ):
        try:
            resolved, _ = self.resolver.resolve_with_candidates(
                company_name=company_name,
                ticker=ticker,
                exchange_hint=exchange_hint,
            )
            source_id = self.router.route(resolved)
            adapter = self.adapters[source_id]
            candidates = adapter.search(resolved, target=target)
            selected, selection_evidence = self.selector.select_with_evidence(candidates, target)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return resolved, candidates, selected, selection_evidence

    def _filter_recent_report_candidates(
        self,
        *,
        candidates,
        target: ReportTarget,
        report_year: int | None,
        include_earnings: bool,
    ):
        filtered = self.selector._filter_candidates(candidates, target)
        if not include_earnings:
            filtered = [
                candidate
                for candidate in filtered
                if candidate.report_type != ReportType.earnings
            ]
        if report_year is not None:
            return [candidate for candidate in filtered if candidate.report_end.year == report_year]
        if not filtered:
            return []
        latest_year = max(candidate.report_end.year for candidate in filtered)
        return [candidate for candidate in filtered if candidate.report_end.year == latest_year]
