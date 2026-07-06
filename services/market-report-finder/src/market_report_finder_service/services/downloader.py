from __future__ import annotations

from dataclasses import dataclass
import hashlib
from ipaddress import ip_address
import json
import re
import socket
import threading
import time
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from uuid import uuid4

import httpx

from market_report_finder_service.core.config import settings
from market_report_finder_service.markets.url_ownership import (
    MANUAL_UNVERIFIED_SOURCE_ID,
    MANUAL_UNVERIFIED_STATUS,
    OFFICIAL_VERIFIED_STATUS,
    is_forbidden_report_ip,
    market_owns_url,
    validate_http_url,
)
from market_report_finder_service.models.schemas import DownloadedReportFile, FilingCandidate, Market, ReportFamily, ReportType


@dataclass(frozen=True)
class _FetchedReport:
    content_sha256: str
    content_type: str | None
    effective_url: str
    size_bytes: int


class ReportDownloader:
    _index_lock = threading.Lock()
    MAX_REDIRECTS = 10
    REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
    SENSITIVE_QUERY_PARAM_NAMES = {
        "access_token",
        "api_key",
        "apikey",
        "crtfc_key",
        "key",
        "source_token",
        "subscription-key",
        "token",
    }
    MAX_DOWNLOAD_BYTES_BY_MARKET = {
        Market.cn: 96 * 1024 * 1024,
        Market.hk: 96 * 1024 * 1024,
        Market.us: 128 * 1024 * 1024,
        Market.eu: 512 * 1024 * 1024,
        Market.kr: 256 * 1024 * 1024,
        Market.jp: 256 * 1024 * 1024,
    }
    ALLOWED_CONTENT_TYPES = {
        "application/download",
        "application/force-download",
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/x-download",
        "application/x-pdf",
        "application/x-zip",
        "application/x-zip-compressed",
        "application/xhtml+xml",
        "application/xml",
        "application/zip",
        "binary/octet-stream",
        "text/html",
        "text/plain",
        "text/xml",
    }
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
        candidate = self._candidate_with_original_url(candidate)
        self._validate_original_url(candidate)
        download_dir = self._download_dir(candidate)
        download_dir.mkdir(parents=True, exist_ok=True)
        index_path = download_dir / settings.download_index_file
        with self._index_lock:
            index = self._load_index(index_path)
            cached = self._lookup_cached(index, candidate)
            if cached is not None and not settings.download_overwrite:
                return cached

        temp_path = self._temp_download_path(download_dir)
        try:
            fetched = self._fetch_to_path(candidate, temp_path)
            candidate = self._candidate_with_effective_url(candidate, fetched.effective_url)
            digest = fetched.content_sha256
            effective_content_type = fetched.content_type
            file_name = self._build_file_name(candidate, effective_content_type)
            file_path = download_dir / file_name

            with self._index_lock:
                index = self._load_index(index_path)
                cached = self._lookup_cached(index, candidate)
                if cached is not None and not settings.download_overwrite:
                    return cached

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

                file_existed = file_path.exists()
                temp_path.replace(file_path)
                try:
                    metadata_path = self._write_metadata(file_path, candidate, digest, effective_content_type)
                    self._register(index, candidate, str(file_path.resolve()), effective_content_type, digest)
                    self._save_index(index_path, index)
                except Exception:
                    if not file_existed:
                        file_path.unlink(missing_ok=True)
                    self._metadata_path(file_path).unlink(missing_ok=True)
                    raise

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
        finally:
            temp_path.unlink(missing_ok=True)

    def _fetch_content(self, candidate: FilingCandidate) -> tuple[bytes, str | None]:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "report-download.tmp"
            fetched = self._fetch_to_path(candidate, temp_path)
            return temp_path.read_bytes(), fetched.content_type

    def _fetch_to_path(self, candidate: FilingCandidate, temp_path: Path) -> _FetchedReport:
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
        with httpx.Client(timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=False) as client:
            effective_url = self._effective_document_url(candidate)
            if candidate.source_id == "dart_public":
                return self._fetch_dart_public_to_path(client, candidate, effective_url, temp_path)
            if candidate.source_id == "edinet":
                return self._fetch_edinet_to_path(client, effective_url, temp_path, candidate)
            return self._stream_get_to_path(client, effective_url, temp_path, candidate)

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

    def _fetch_edinet_to_path(
        self,
        client: httpx.Client,
        effective_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
    ) -> _FetchedReport:
        last_rate_limited = False
        for attempt in range(4):
            try:
                return self._stream_get_to_path(client, effective_url, temp_path, candidate)
            except httpx.HTTPStatusError as exc:
                response = exc.response
                if response.status_code != 429:
                    raise
                last_rate_limited = True
                time.sleep(self._retry_delay_seconds(response, attempt))
                continue
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

    def _fetch_dart_public_to_path(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
        effective_url: str,
        temp_path: Path,
    ) -> _FetchedReport:
        parsed = urlparse(effective_url)
        path = parsed.path.lower()
        if "/pdf/download/pdf.do" in path:
            return self._fetch_dart_public_pdf_to_path(client, candidate, effective_url, temp_path)
        if "/report/combined.do" in path or "/dsaf001/" in path:
            return self._fetch_dart_public_combined_html_to_path(client, candidate, temp_path)
        return self._stream_get_to_path(client, effective_url, temp_path, candidate)

    def _fetch_dart_public_pdf_to_path(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
        pdf_url: str,
        temp_path: Path,
    ) -> _FetchedReport:
        viewer_url = str(candidate.metadata.get("dart_viewer_url") or candidate.landing_url or "").strip()
        if viewer_url:
            self._validate_effective_url(candidate, viewer_url)
            self._get_with_redirects(client, viewer_url, candidate)
        landing_url = str(candidate.metadata.get("dart_pdf_landing_url") or "").strip() or self._dart_pdf_landing_url(pdf_url)
        self._validate_effective_url(candidate, landing_url)
        landing_response = self._get_with_redirects(
            client,
            landing_url,
            candidate,
            headers={"Referer": viewer_url or "https://dart.fss.or.kr/"},
        )
        landing_response.raise_for_status()
        fetched = self._stream_get_to_path(
            client,
            pdf_url,
            temp_path,
            candidate,
            headers={
                "Referer": landing_url,
                "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            },
        )
        if fetched.content_type != "application/pdf":
            temp_path.unlink(missing_ok=True)
            return self._fetch_dart_public_combined_html_to_path(client, candidate, temp_path)
        return fetched

    def _fetch_dart_public_combined_html_to_path(
        self,
        client: httpx.Client,
        candidate: FilingCandidate,
        temp_path: Path,
    ) -> _FetchedReport:
        viewer_url = str(candidate.metadata.get("dart_viewer_url") or candidate.landing_url or candidate.document_url).strip()
        if "/report/combined.do" in viewer_url:
            receipt_no = self._query_value(viewer_url, "rcpNo") or self._query_value(viewer_url, "rcp_no")
            viewer_url = f"https://dart.fss.or.kr/dsaf001/main.do?{urlencode({'rcpNo': receipt_no or candidate.accession_number or ''})}"
        self._validate_effective_url(candidate, viewer_url)
        viewer_response = self._get_with_redirects(client, viewer_url, candidate)
        viewer_response.raise_for_status()
        sections = self._dart_viewer_sections(viewer_response.text)
        if not sections:
            return self._response_body_to_path(viewer_response, requested_url=viewer_url, temp_path=temp_path, candidate=candidate)

        body_parts: list[str] = []
        for section in sections:
            self._validate_effective_url(candidate, section["url"])
            section_response = self._get_with_redirects(client, section["url"], candidate, headers={"Referer": viewer_url})
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
        return self._bytes_to_path(
            html.encode("utf-8"),
            content_type="text/html",
            effective_url=viewer_url,
            temp_path=temp_path,
            candidate=candidate,
        )

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

    def _stream_get_to_path(
        self,
        client: httpx.Client,
        requested_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
        *,
        headers: dict[str, str] | None = None,
    ) -> _FetchedReport:
        if hasattr(client, "stream"):
            return self._stream_get_with_redirects(client, requested_url, temp_path, candidate, headers=headers)
        response = self._get_with_redirects(client, requested_url, candidate, headers=headers)
        return self._response_body_to_path(response, requested_url=requested_url, temp_path=temp_path, candidate=candidate)

    def _stream_get_with_redirects(
        self,
        client: httpx.Client,
        requested_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
        *,
        headers: dict[str, str] | None = None,
    ) -> _FetchedReport:
        current_url = requested_url
        for _redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_fetch_url(candidate, current_url)
            with client.stream("GET", current_url, headers=headers) as response:
                if self._is_redirect_response(response):
                    current_url = self._redirect_target_url(response, current_url, candidate)
                    continue
                return self._stream_response_to_path(
                    response,
                    requested_url=current_url,
                    temp_path=temp_path,
                    candidate=candidate,
                )
        raise ValueError(f"Report download exceeded redirect limit: {self._redact_url_for_storage(current_url)}")

    def _get_with_redirects(
        self,
        client: httpx.Client,
        requested_url: str,
        candidate: FilingCandidate,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        current_url = requested_url
        for _redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_fetch_url(candidate, current_url)
            response = client.get(current_url, headers=headers) if headers else client.get(current_url)
            if self._is_redirect_response(response):
                current_url = self._redirect_target_url(response, current_url, candidate)
                continue
            effective_url = str(getattr(response, "url", None) or current_url)
            self._validate_fetch_url(candidate, effective_url)
            return response
        raise ValueError(f"Report download exceeded redirect limit: {self._redact_url_for_storage(current_url)}")

    @classmethod
    def _is_redirect_response(cls, response: httpx.Response) -> bool:
        return int(getattr(response, "status_code", 200) or 200) in cls.REDIRECT_STATUS_CODES

    def _redirect_target_url(self, response: httpx.Response, current_url: str, candidate: FilingCandidate) -> str:
        location = str((getattr(response, "headers", {}) or {}).get("location") or "").strip()
        if not location:
            raise ValueError(f"Report redirect missing Location header: {self._redact_url_for_storage(current_url)}")
        target_url = urljoin(current_url, location)
        self._validate_fetch_url(candidate, target_url)
        return target_url

    def _stream_response_to_path(
        self,
        response: httpx.Response,
        *,
        requested_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
    ) -> _FetchedReport:
        response.raise_for_status()
        effective_url = str(getattr(response, "url", None) or requested_url)
        self._validate_effective_url(candidate, effective_url)
        content_type = response.headers.get("content-type")
        self._validate_declared_content_type(content_type)
        self._validate_content_length(candidate, response.headers.get("content-length"))

        digest = hashlib.sha256()
        size_bytes = 0
        head = bytearray()
        max_bytes = self._max_download_bytes(candidate)
        with temp_path.open("wb") as output:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ValueError(f"Downloaded report exceeds {max_bytes} byte limit for {candidate.market.value}")
                if len(head) < 4096:
                    head.extend(chunk[: 4096 - len(head)])
                digest.update(chunk)
                output.write(chunk)

        effective_content_type = self._effective_content_type(bytes(head), content_type)
        self._validate_effective_content_type(effective_content_type)
        return _FetchedReport(
            content_sha256=digest.hexdigest(),
            content_type=effective_content_type,
            effective_url=effective_url,
            size_bytes=size_bytes,
        )

    def _response_body_to_path(
        self,
        response: httpx.Response,
        *,
        requested_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
    ) -> _FetchedReport:
        response.raise_for_status()
        content = self._response_body_bytes(response)
        effective_url = str(getattr(response, "url", None) or requested_url)
        return self._bytes_to_path(
            content,
            content_type=response.headers.get("content-type"),
            effective_url=effective_url,
            temp_path=temp_path,
            candidate=candidate,
        )

    def _bytes_to_path(
        self,
        content: bytes,
        *,
        content_type: str | None,
        effective_url: str,
        temp_path: Path,
        candidate: FilingCandidate,
    ) -> _FetchedReport:
        self._validate_effective_url(candidate, effective_url)
        self._validate_declared_content_type(content_type)
        max_bytes = self._max_download_bytes(candidate)
        size_bytes = len(content)
        if size_bytes > max_bytes:
            raise ValueError(f"Downloaded report exceeds {max_bytes} byte limit for {candidate.market.value}")
        effective_content_type = self._effective_content_type(content[:4096], content_type)
        self._validate_effective_content_type(effective_content_type)
        temp_path.write_bytes(content)
        return _FetchedReport(
            content_sha256=hashlib.sha256(content).hexdigest(),
            content_type=effective_content_type,
            effective_url=effective_url,
            size_bytes=size_bytes,
        )

    @staticmethod
    def _response_body_bytes(response: httpx.Response) -> bytes:
        iter_bytes = getattr(response, "iter_bytes", None)
        if callable(iter_bytes):
            return b"".join(chunk for chunk in iter_bytes() if chunk)
        return bytes(getattr(response, "content", b""))

    def _validate_original_url(self, candidate: FilingCandidate) -> None:
        host = validate_http_url(candidate.document_url)
        if not self._is_manual_unverified(candidate) and not market_owns_url(candidate.market, candidate.document_url):
            raise ValueError(
                f"{candidate.source_id} URL is outside the {candidate.market.value} official source allowlist: "
                f"{self._redact_url_for_storage(candidate.document_url)}"
            )
        self._validate_resolved_host(host, candidate.document_url)

    def _validate_effective_url(self, candidate: FilingCandidate, effective_url: str) -> None:
        host = validate_http_url(effective_url)
        if not self._is_manual_unverified(candidate) and not market_owns_url(candidate.market, effective_url):
            raise ValueError(
                f"{candidate.source_id} redirect escaped the {candidate.market.value} official source allowlist: "
                f"{self._redact_url_for_storage(effective_url)}"
            )
        self._validate_resolved_host(host, effective_url)

    def _validate_fetch_url(self, candidate: FilingCandidate, report_url: str) -> None:
        host = validate_http_url(report_url)
        if not self._is_manual_unverified(candidate) and not market_owns_url(candidate.market, report_url):
            raise ValueError(
                f"{candidate.source_id} URL is outside the {candidate.market.value} official source allowlist: "
                f"{self._redact_url_for_storage(report_url)}"
            )
        self._validate_resolved_host(host, report_url)

    @classmethod
    def _validate_resolved_host(cls, host: str, report_url: str) -> None:
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if is_forbidden_report_ip(address):
                raise ValueError("Report URL resolves to a private, link-local, loopback, or cloud metadata IP address")
            return

        parsed = urlparse(report_url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Report URL host could not be resolved: {host}") from exc

        addresses: set[str] = set()
        for record in records:
            sockaddr = record[4]
            if sockaddr:
                addresses.add(str(sockaddr[0]).split("%", 1)[0])
        if not addresses:
            raise ValueError(f"Report URL host could not be resolved: {host}")
        for value in addresses:
            if is_forbidden_report_ip(value):
                raise ValueError(
                    "Report URL resolves to a private, link-local, loopback, or cloud metadata IP address: "
                    f"{host}"
                )

    @classmethod
    def _redact_url_for_storage(cls, report_url: str | None) -> str | None:
        if not report_url:
            return report_url
        parsed = urlparse(str(report_url))
        query = [
            (key, "[redacted]" if key.lower() in cls.SENSITIVE_QUERY_PARAM_NAMES else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunparse(parsed._replace(query=urlencode(query)))

    @classmethod
    def _candidate_payload_for_storage(cls, candidate: FilingCandidate) -> dict:
        payload = candidate.model_dump(mode="json")
        for key in ("document_url", "landing_url"):
            payload[key] = cls._redact_url_for_storage(payload.get(key))
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            for key in ("original_url", "effective_url", "dart_viewer_url", "dart_pdf_landing_url"):
                if key in metadata:
                    metadata[key] = cls._redact_url_for_storage(metadata.get(key))
        return payload

    @classmethod
    def _is_manual_unverified(cls, candidate: FilingCandidate) -> bool:
        return (
            candidate.source_id == MANUAL_UNVERIFIED_SOURCE_ID
            or candidate.metadata.get("source_verification_status") == MANUAL_UNVERIFIED_STATUS
        )

    @classmethod
    def _candidate_with_original_url(cls, candidate: FilingCandidate) -> FilingCandidate:
        metadata = dict(candidate.metadata)
        metadata.setdefault("original_url", cls._redact_url_for_storage(candidate.document_url))
        if "source_verification_status" not in metadata:
            metadata["source_verification_status"] = (
                MANUAL_UNVERIFIED_STATUS if cls._is_manual_unverified(candidate) else OFFICIAL_VERIFIED_STATUS
            )
        return candidate.model_copy(update={"metadata": metadata})

    @classmethod
    def _candidate_with_effective_url(cls, candidate: FilingCandidate, effective_url: str) -> FilingCandidate:
        metadata = dict(candidate.metadata)
        metadata.setdefault("original_url", cls._redact_url_for_storage(candidate.document_url))
        metadata["effective_url"] = cls._redact_url_for_storage(effective_url)
        metadata["source_verification_status"] = (
            MANUAL_UNVERIFIED_STATUS if cls._is_manual_unverified(candidate) else OFFICIAL_VERIFIED_STATUS
        )
        return candidate.model_copy(update={"metadata": metadata})

    @classmethod
    def _max_download_bytes(cls, candidate: FilingCandidate) -> int:
        return cls.MAX_DOWNLOAD_BYTES_BY_MARKET.get(candidate.market, 256 * 1024 * 1024)

    def _validate_content_length(self, candidate: FilingCandidate, content_length: str | None) -> None:
        if not content_length:
            return
        try:
            length = int(content_length)
        except ValueError:
            return
        max_bytes = self._max_download_bytes(candidate)
        if length > max_bytes:
            raise ValueError(f"Downloaded report exceeds {max_bytes} byte limit for {candidate.market.value}")

    @classmethod
    def _validate_declared_content_type(cls, content_type: str | None) -> None:
        normalized = cls._normalize_content_type(content_type)
        if not normalized:
            return
        if normalized in cls.ALLOWED_CONTENT_TYPES or normalized.endswith("+xml"):
            return
        raise ValueError(f"Unsupported report content type: {normalized}")

    @classmethod
    def _validate_effective_content_type(cls, content_type: str | None) -> None:
        if not content_type:
            return
        cls._validate_declared_content_type(content_type)

    @staticmethod
    def _temp_download_path(download_dir: Path) -> Path:
        return download_dir / f".report-download-{uuid4().hex}.tmp"

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
            "source_verification_status": candidate.metadata.get("source_verification_status"),
            "original_url": self._redact_url_for_storage(candidate.metadata.get("original_url") or candidate.document_url),
            "effective_url": self._redact_url_for_storage(candidate.metadata.get("effective_url")),
        }
        index.setdefault("by_url", {})[self._redact_url_for_storage(candidate.document_url)] = entry
        if self._should_cache_landing_url(candidate):
            index.setdefault("by_url", {})[self._redact_url_for_storage(candidate.landing_url)] = entry
        index.setdefault("by_content_sha256", {})[digest] = entry

    @classmethod
    def _cache_lookup_urls(cls, candidate: FilingCandidate) -> tuple[str, ...]:
        urls = [candidate.document_url]
        if cls._should_cache_landing_url(candidate):
            urls.append(candidate.landing_url)
        keys: list[str] = []
        for url in urls:
            redacted = cls._redact_url_for_storage(url)
            if redacted and redacted not in keys:
                keys.append(redacted)
            if url and url not in keys:
                keys.append(url)
        return tuple(keys)

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
        source_verification = {
            "original_url": self._redact_url_for_storage(candidate.metadata.get("original_url") or candidate.document_url),
            "effective_url": self._redact_url_for_storage(candidate.metadata.get("effective_url") or candidate.document_url),
            "source_verification_status": candidate.metadata.get("source_verification_status"),
        }
        payload = {
            "candidate": self._candidate_payload_for_storage(candidate),
            "downloaded_file": {
                "file_name": file_path.name,
                "saved_path": str(file_path.resolve()),
                "size_bytes": file_path.stat().st_size,
                "content_type": content_type,
                "content_sha256": digest,
            },
            "source_verification": source_verification,
        }
        self._atomic_write_json(metadata_path, payload)
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
        ReportDownloader._atomic_write_json(index_path, index)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

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
        return ReportDownloader._normalize_content_type(content_type)

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
        normalized = ReportDownloader._normalize_content_type(content_type)
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

    @staticmethod
    def _normalize_content_type(content_type: str | None) -> str | None:
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        return normalized or None
