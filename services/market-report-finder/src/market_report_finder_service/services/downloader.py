from __future__ import annotations

import hashlib
import json
import re
import time
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import DownloadedReportFile, FilingCandidate, ReportFamily, ReportType


class ReportDownloader:
    REPORT_TYPE_LABELS = {
        ReportType.form_10k: "10-K",
        ReportType.form_20f: "20-F",
        ReportType.form_10q: "10-Q",
        ReportType.form_6k: "6-K",
        ReportType.annual: "年报",
        ReportType.semiannual: "半年报",
        ReportType.quarterly: "季报",
        ReportType.q1: "一季报",
        ReportType.q3: "三季报",
        ReportType.earnings: "业绩公告",
    }

    def download(self, candidate: FilingCandidate) -> DownloadedReportFile:
        download_dir = self._download_dir(candidate)
        download_dir.mkdir(parents=True, exist_ok=True)
        index_path = download_dir / settings.download_index_file
        index = self._load_index(index_path)
        cached = self._lookup_cached(index, candidate)
        if cached is not None and not settings.download_overwrite:
            return cached

        content, content_type = self._fetch_content(candidate)
        digest = hashlib.sha256(content).hexdigest()
        effective_content_type = self._effective_content_type(content, content_type)
        file_name = self._build_file_name(candidate, effective_content_type)
        file_path = download_dir / file_name

        deduped = self._lookup_by_digest(index, digest)
        if deduped is not None and not settings.download_overwrite:
            self._register(index, candidate, deduped["saved_path"], deduped.get("content_type"), digest)
            self._save_index(index_path, index)
            existing_path = Path(deduped["saved_path"])
            return DownloadedReportFile(
                file_name=existing_path.name,
                saved_path=str(existing_path.resolve()),
                size_bytes=existing_path.stat().st_size,
                content_type=deduped.get("content_type") or self._content_type_from_suffix(existing_path.suffix),
                cache_hit=False,
                deduplicated=True,
                content_sha256=digest,
                metadata_path=self._metadata_path(existing_path).as_posix(),
            )

        file_path.write_bytes(content)
        metadata_path = self._write_metadata(file_path, candidate, digest, effective_content_type)
        self._register(index, candidate, str(file_path.resolve()), effective_content_type, digest)
        self._save_index(index_path, index)

        return DownloadedReportFile(
            file_name=file_path.name,
            saved_path=str(file_path.resolve()),
            size_bytes=file_path.stat().st_size,
            content_type=effective_content_type,
            cache_hit=False,
            deduplicated=False,
            content_sha256=digest,
            metadata_path=str(metadata_path.resolve()),
        )

    def _fetch_content(self, candidate: FilingCandidate) -> tuple[bytes, str | None]:
        headers = {
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if candidate.source_id == "sec":
            headers["Host"] = "www.sec.gov"
        if candidate.source_id == "hkex":
            headers["Referer"] = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=EN"
        if candidate.source_id == "cninfo":
            headers["Referer"] = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
            headers["X-Requested-With"] = "XMLHttpRequest"
        if candidate.source_id == "dart":
            headers["Referer"] = "https://dart.fss.or.kr/"
        if candidate.source_id == "dart_public":
            headers["Referer"] = "https://dart.fss.or.kr/dsab007/main.do"
            headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
        if candidate.source_id == "edinet":
            headers["Referer"] = "https://disclosure2.edinet-fsa.go.jp/"
            if settings.edinet_api_key:
                headers["Subscription-Key"] = settings.edinet_api_key
        host = urlparse(candidate.document_url).netloc.lower()
        if candidate.market.value == "EU" and candidate.source_id != "sec" and "bmwgroup.com" not in host:
            headers["User-Agent"] = settings.eu_user_agent
            headers["Accept"] = "application/pdf,application/zip,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers["Accept-Language"] = "en-US,en;q=0.9"
            if candidate.source_id == "xbrl_filings_esef":
                headers["Referer"] = "https://filings.xbrl.org/"
        with httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True) as client:
            effective_url = self._effective_document_url(candidate)
            if candidate.source_id == "dart_public":
                return self._fetch_dart_public_content(client, candidate, effective_url)
            if candidate.source_id == "edinet":
                return self._fetch_edinet_content(client, effective_url)
            response = client.get(effective_url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type")

    @staticmethod
    def _fetch_edinet_content(client: httpx.Client, effective_url: str) -> tuple[bytes, str | None]:
        last_rate_limited = False
        for attempt in range(4):
            response = client.get(effective_url)
            if response.status_code == 429:
                last_rate_limited = True
                time.sleep(ReportDownloader._retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response.content, response.headers.get("content-type")
        if last_rate_limited:
            raise ValueError("EDINET API rate limit reached while downloading a document. Please retry after a longer interval.")
        raise ValueError("EDINET document download failed")

    @staticmethod
    def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 1.0), 120.0)
            except ValueError:
                pass
        return min(60.0, 2.0 * (attempt + 1) ** 2)

    def _fetch_dart_public_content(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
        effective_url: str,
    ) -> tuple[bytes, str | None]:
        parsed = urlparse(effective_url)
        path = parsed.path.lower()
        if "/pdf/download/pdf.do" in path:
            return self._fetch_dart_public_pdf(client, candidate, effective_url)
        if "/report/combined.do" in path or "/dsaf001/" in path:
            return self._fetch_dart_public_combined_html(client, candidate)
        response = client.get(effective_url)
        response.raise_for_status()
        return response.content, response.headers.get("content-type")

    def _fetch_dart_public_pdf(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
        pdf_url: str,
    ) -> tuple[bytes, str | None]:
        viewer_url = str(candidate.metadata.get("dart_viewer_url") or candidate.landing_url or "").strip()
        if viewer_url:
            client.get(viewer_url)
        landing_url = str(candidate.metadata.get("dart_pdf_landing_url") or "").strip() or self._dart_pdf_landing_url(pdf_url)
        landing_response = client.get(landing_url, headers={"Referer": viewer_url or "https://dart.fss.or.kr/"})
        landing_response.raise_for_status()
        response = client.get(
            pdf_url,
            headers={
                "Referer": landing_url,
                "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        if not response.content.startswith(b"%PDF-"):
            return self._fetch_dart_public_combined_html(client, candidate)
        return response.content, response.headers.get("content-type") or "application/pdf"

    def _fetch_dart_public_combined_html(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
    ) -> tuple[bytes, str | None]:
        viewer_url = str(candidate.metadata.get("dart_viewer_url") or candidate.landing_url or candidate.document_url).strip()
        if "/report/combined.do" in viewer_url:
            receipt_no = self._query_value(viewer_url, "rcpNo") or self._query_value(viewer_url, "rcp_no")
            viewer_url = f"https://dart.fss.or.kr/dsaf001/main.do?{urlencode({'rcpNo': receipt_no or candidate.accession_number or ''})}"
        viewer_response = client.get(viewer_url)
        viewer_response.raise_for_status()
        sections = self._dart_viewer_sections(viewer_response.text)
        if not sections:
            return viewer_response.content, viewer_response.headers.get("content-type")

        body_parts: list[str] = []
        for section in sections:
            section_response = client.get(section["url"], headers={"Referer": viewer_url})
            section_response.raise_for_status()
            body_html = self._dart_section_body(section_response.text)
            title = escape(section["title"])
            body_parts.append(f"<section data-ele-id=\"{escape(section['ele_id'])}\"><h1>{title}</h1>{body_html}</section>")

        title = escape(candidate.title or candidate.company_name)
        landing = escape(viewer_url)
        html = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<title>{title}</title><base href=\"https://dart.fss.or.kr/\">"
            "<style>body{font-family:Arial,'Noto Sans KR',sans-serif;margin:24px;line-height:1.5}"
            "section{break-after:auto;margin-bottom:32px}table{max-width:100%;border-collapse:collapse}"
            "td,th{border:1px solid #ccc;padding:4px}h1{font-size:20px;border-bottom:1px solid #ddd;padding-bottom:8px}"
            "</style></head><body>"
            f"<p>Original DART viewer: <a href=\"{landing}\">{landing}</a></p>"
            + "\n".join(body_parts)
            + "</body></html>"
        )
        return html.encode("utf-8"), "text/html"

    @staticmethod
    def _dart_pdf_landing_url(pdf_url: str) -> str:
        parsed = urlparse(pdf_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        receipt_no = query.get("rcp_no") or query.get("rcpNo") or ""
        dcm_no = query.get("dcm_no") or query.get("dcmNo") or ""
        return f"https://dart.fss.or.kr/pdf/download/main.do?{urlencode({'rcp_no': receipt_no, 'dcm_no': dcm_no})}"

    @staticmethod
    def _query_value(url: str, key: str) -> str | None:
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        value = query.get(key)
        return value or None

    @staticmethod
    def _dart_viewer_sections(html: str) -> list[dict[str, str]]:
        node_pattern = re.compile(
            r"node1\[['\"]text['\"]\]\s*=\s*['\"](?P<title>.*?)['\"].*?"
            r"node1\[['\"]rcpNo['\"]\]\s*=\s*['\"](?P<rcp_no>\d+)['\"].*?"
            r"node1\[['\"]dcmNo['\"]\]\s*=\s*['\"](?P<dcm_no>\d+)['\"].*?"
            r"node1\[['\"]eleId['\"]\]\s*=\s*['\"](?P<ele_id>\d+)['\"].*?"
            r"node1\[['\"]offset['\"]\]\s*=\s*['\"](?P<offset>\d+)['\"].*?"
            r"node1\[['\"]length['\"]\]\s*=\s*['\"](?P<length>\d+)['\"].*?"
            r"node1\[['\"]dtd['\"]\]\s*=\s*['\"](?P<dtd>[^'\"]+)['\"]",
            re.I | re.S,
        )
        sections: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for match in node_pattern.finditer(html):
            data = match.groupdict()
            key = (data["rcp_no"], data["dcm_no"], data["ele_id"])
            if key in seen:
                continue
            seen.add(key)
            url = "https://dart.fss.or.kr/report/viewer.do?" + urlencode(
                {
                    "rcpNo": data["rcp_no"],
                    "dcmNo": data["dcm_no"],
                    "eleId": data["ele_id"],
                    "offset": data["offset"],
                    "length": data["length"],
                    "dtd": data["dtd"],
                }
            )
            sections.append({**data, "url": url})
        return sections

    @staticmethod
    def _dart_section_body(html: str) -> str:
        body_match = re.search(r"<body[^>]*>(?P<body>.*?)</body>", html, re.I | re.S)
        return body_match.group("body") if body_match else html

    @staticmethod
    def _effective_document_url(candidate: FilingCandidate) -> str:
        parsed = urlparse(candidate.document_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if candidate.source_id == "dart" and settings.dart_api_key:
            query.setdefault("crtfc_key", settings.dart_api_key)
        if candidate.source_id == "edinet" and settings.edinet_api_key:
            query.setdefault("Subscription-Key", settings.edinet_api_key)
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _download_dir(self, candidate: FilingCandidate) -> Path:
        folder = "年报" if candidate.report_family == ReportFamily.annual else "财报"
        report_year = str(candidate.report_end.year)
        market_parts = [candidate.market.value]
        if candidate.market.value == "EU":
            market_parts.append(self._safe_filename_part(self._eu_country_for_candidate(candidate)))
        return (
            Path(settings.download_dir).expanduser()
            .joinpath(*market_parts)
            / self._safe_filename_part(candidate.company_name)
            / report_year
            / folder
        )

    def _build_file_name(self, candidate: FilingCandidate, content_type: str | None = None) -> str:
        suffix = self._file_suffix(candidate, content_type)
        report_type = self._report_type_label(candidate)
        url_hash = hashlib.sha256(candidate.document_url.encode("utf-8")).hexdigest()[:8]
        parts = [
            candidate.company_name,
            f"{candidate.market.value}_{candidate.ticker or candidate.company_id}",
            candidate.report_end.isoformat(),
            report_type,
            candidate.published_at.isoformat(),
            candidate.source_id,
            url_hash,
        ]
        return "_".join(self._safe_filename_part(part) for part in parts) + suffix

    def _lookup_cached(self, index: dict, candidate: FilingCandidate) -> DownloadedReportFile | None:
        for key in self._cache_lookup_urls(candidate):
            entry = index.get("by_url", {}).get(key)
            if not entry:
                continue
            file_path = Path(entry["saved_path"])
            if not file_path.exists():
                continue
            return DownloadedReportFile(
                file_name=file_path.name,
                saved_path=str(file_path.resolve()),
                size_bytes=file_path.stat().st_size,
                content_type=entry.get("content_type") or self._content_type_from_suffix(file_path.suffix),
                cache_hit=True,
                deduplicated=False,
                content_sha256=entry.get("content_sha256"),
                metadata_path=entry.get("metadata_path"),
            )
        return None

    @staticmethod
    def _lookup_by_digest(index: dict, digest: str) -> dict | None:
        entry = index.get("by_content_sha256", {}).get(digest)
        if not entry:
            return None
        return entry if Path(entry["saved_path"]).exists() else None

    def _register(
        self,
        index: dict,
        candidate: FilingCandidate,
        saved_path: str,
        content_type: str | None,
        digest: str,
    ) -> None:
        entry = {
            "saved_path": saved_path,
            "file_name": Path(saved_path).name,
            "content_type": content_type,
            "content_sha256": digest,
            "metadata_path": str(self._metadata_path(Path(saved_path)).resolve()),
            "market": candidate.market.value,
            "company_id": candidate.company_id,
            "ticker": candidate.ticker,
            "form": candidate.form,
            "report_type": candidate.report_type.value,
            "report_family": candidate.report_family.value,
            "report_end": candidate.report_end.isoformat(),
            "published_at": candidate.published_at.isoformat(),
            "accession_number": candidate.accession_number,
            "country": candidate.metadata.get("country"),
            "source_tier": candidate.metadata.get("source_tier"),
        }
        index.setdefault("by_url", {})[candidate.document_url] = entry
        if self._should_cache_landing_url(candidate):
            index.setdefault("by_url", {})[candidate.landing_url] = entry
        index.setdefault("by_content_sha256", {})[digest] = entry

    @classmethod
    def _cache_lookup_urls(cls, candidate: FilingCandidate) -> tuple[str, ...]:
        if cls._should_cache_landing_url(candidate):
            return (candidate.document_url, candidate.landing_url)
        return (candidate.document_url,)

    @staticmethod
    def _should_cache_landing_url(candidate: FilingCandidate) -> bool:
        if candidate.source_id == "issuer_annual_report":
            return False
        if candidate.source_id == "dart_public" and candidate.document_url != candidate.landing_url:
            return False
        return True

    def _write_metadata(
        self,
        file_path: Path,
        candidate: FilingCandidate,
        digest: str,
        content_type: str | None,
    ) -> Path:
        metadata_path = self._metadata_path(file_path)
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "downloaded_file": {
                "file_name": file_path.name,
                "saved_path": str(file_path.resolve()),
                "size_bytes": file_path.stat().st_size,
                "content_type": content_type,
                "content_sha256": digest,
            },
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata_path

    @staticmethod
    def _metadata_path(file_path: Path) -> Path:
        return file_path.with_suffix(file_path.suffix + ".metadata.json")

    @staticmethod
    def _load_index(index_path: Path) -> dict:
        if not index_path.exists():
            return {"by_url": {}, "by_content_sha256": {}}
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"by_url": {}, "by_content_sha256": {}}

    @staticmethod
    def _save_index(index_path: Path, index: dict) -> None:
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _safe_filename_part(value: object) -> str:
        text = str(value or "").strip()
        text = re.sub(r'[\\/:*?"<>|\s]+', "-", text)
        text = re.sub(r"-{2,}", "-", text).strip(".-_")
        return text or "unknown"

    def _report_type_label(self, candidate: FilingCandidate) -> str:
        if candidate.report_type == ReportType.quarterly:
            quarter_label = self._quarter_label(candidate.report_end)
            if quarter_label:
                return quarter_label
        return self.REPORT_TYPE_LABELS.get(candidate.report_type, candidate.report_type.value)

    @staticmethod
    def _quarter_label(report_end) -> str | None:
        month_day = (report_end.month, report_end.day)
        if month_day == (3, 31):
            return "一季报"
        if month_day == (6, 30):
            return "二季报"
        if month_day == (9, 30):
            return "三季报"
        if month_day == (12, 31):
            return "四季报"
        return None

    @staticmethod
    def _eu_country_for_candidate(candidate: FilingCandidate) -> str:
        country = str(candidate.metadata.get("country") or "").strip().upper()
        aliases = {"GB": "UK", "UNITED KINGDOM": "UK", "UK": "UK", "FR": "FR", "DE": "DE", "NL": "NL", "CH": "CH"}
        if country in aliases:
            return aliases[country]
        return country or "UNKNOWN"

    @staticmethod
    def _file_suffix(candidate: FilingCandidate, content_type: str | None = None) -> str:
        suffix = Path(urlparse(candidate.document_url).path).suffix.lower()
        declared = candidate.file_format.strip().lower().lstrip(".")
        content_suffix = ReportDownloader._suffix_from_content_type(content_type)
        if suffix not in {".pdf", ".html", ".htm", ".xml", ".txt", ".json", ".zip"}:
            suffix = ""
        if content_suffix == ".pdf":
            return ".pdf"
        if suffix and content_suffix != ".pdf":
            return suffix
        if declared and declared != "pdf" and content_suffix != ".pdf":
            return f".{declared}"
        if content_suffix:
            return content_suffix
        if declared:
            return f".{declared}"
        return suffix or ".bin"

    @staticmethod
    def _content_type_from_suffix(suffix: str) -> str | None:
        mapping = {
            ".html": "text/html",
            ".htm": "text/html",
            ".xml": "application/xml",
            ".txt": "text/plain",
            ".pdf": "application/pdf",
            ".json": "application/json",
            ".zip": "application/zip",
        }
        return mapping.get(suffix.lower())

    @staticmethod
    def _effective_content_type(content: bytes, content_type: str | None) -> str | None:
        sniffed = ReportDownloader._sniff_content_type(content)
        if sniffed:
            return sniffed
        if not content_type:
            return None
        return content_type.split(";", 1)[0].strip().lower() or None

    @staticmethod
    def _sniff_content_type(content: bytes) -> str | None:
        head = content[:4096].lstrip().lower()
        if head.startswith(b"%pdf-"):
            return "application/pdf"
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            return "text/html"
        if head.startswith(b"<?xml"):
            if b"<html" in head or b"xmlns:ix" in head or b"ix:header" in head:
                return "text/html"
            return "application/xml"
        if head.startswith(b"pk\x03\x04"):
            return "application/zip"
        return None

    @staticmethod
    def _suffix_from_content_type(content_type: str | None) -> str | None:
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        mapping = {
            "application/pdf": ".pdf",
            "text/html": ".html",
            "application/xhtml+xml": ".html",
            "application/xml": ".xml",
            "text/xml": ".xml",
            "text/plain": ".txt",
            "application/json": ".json",
            "application/zip": ".zip",
            "application/x-zip-compressed": ".zip",
        }
        return mapping.get(normalized)
