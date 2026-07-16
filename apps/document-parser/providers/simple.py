"""Local parsing providers that do not require an external service."""

from __future__ import annotations

import html
import http.client
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping

from contracts import ParseConfig, ParseOutput, SourceFile
from file_utils import guess_mime_type, safe_client_filename, sha256_file
from page_ranges import parse_page_ranges
from pdf_parser_artifact_transport import (
    ArtifactTransportError,
    artifact_transport_mode,
    cleanup_staged_pdf_parser_artifacts,
    stage_pdf_parser_artifacts,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_BLOCK_BREAK_RE = re.compile(r"</(?:p|div|section|article|h[1-6]|li|tr|table)>", re.I)
PDF_BRIDGE_SUPPORTED_MARKETS = {"CN", "HK", "US", "JP", "KR", "EU", "DOC"}
PDF_PARSER_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
IDENTITY_SCOPE_VALUE_RE = re.compile(r"[^A-Za-z0-9_.@:-]+")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    parts = []
    for block in blocks:
        block_id = block.get("block_id", "")
        evidence_id = (block.get("source_ref") or {}).get("evidence_id", "")
        page = block.get("page_number") or 1
        marker = f"<!-- DOC_BLOCK: {block_id} page={page} evidence={evidence_id} -->"
        markdown = str(block.get("markdown") or block.get("text") or "").strip()
        if markdown:
            parts.append(f"{marker}\n{markdown}")
    return "\n\n".join(parts).strip() + ("\n" if parts else "")


def _pdf_parser_api_base() -> str:
    return (
        os.environ.get("SIQ_PDF2MD_API_BASE")
        or os.environ.get("PDF2MD_API_BASE")
        or "http://127.0.0.1:15000"
    ).rstrip("/")


def _pdf_parser_access_token() -> str:
    return (
        os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN")
        or os.environ.get("PDF2MD_ACCESS_TOKEN")
        or ""
    ).strip()


def _identity_scope_header_value(value: Any) -> str:
    text = IDENTITY_SCOPE_VALUE_RE.sub("_", str(value or "").strip())[:120]
    return text.strip("._:-")


def _pdf_parser_headers(
    extra: Mapping[str, str] | None = None,
    *,
    identity_scope: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    headers = dict(extra or {})
    if _pdf_parser_access_token():
        headers.setdefault("X-PDF2MD-Token", _pdf_parser_access_token())
    scope = identity_scope if isinstance(identity_scope, Mapping) else {}
    for key, header_name in (
        ("owner_id", "X-SIQ-User-Id"),
        ("tenant_id", "X-SIQ-Tenant-Id"),
        ("user_role", "X-SIQ-User-Role"),
    ):
        value = _identity_scope_header_value(scope.get(key))
        if value:
            headers[header_name] = value
    market_scope = str(scope.get("market_scope") or "").strip().upper()
    if market_scope in PDF_BRIDGE_SUPPORTED_MARKETS:
        headers["X-SIQ-Market-Scope"] = market_scope
    return headers


def _json_request(url: str, method: str = "GET", data: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    payload = data
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        return {"_error": True, "status": exc.code, "detail": detail}
    except Exception as exc:
        return {"_error": True, "detail": str(exc)}


def _stream_multipart_post(
    url: str,
    fields: dict[str, Any],
    file_field_name: str,
    filename: str,
    file_path: Path,
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 300,
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    boundary = "----CodexBoundary" + uuid.uuid4().hex
    file_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    preamble_parts = []
    for name, value in fields.items():
        escaped_name = str(name).replace('"', '\\"')
        preamble_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{escaped_name}"\r\n\r\n'
                f"{value if value is not None else ''}\r\n"
            ).encode("utf-8")
        )
    escaped_file_field_name = str(file_field_name).replace('"', '\\"')
    escaped_filename = str(filename).replace('"', '\\"')
    preamble_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{escaped_file_field_name}"; filename="{escaped_filename}"\r\n'
            f"Content-Type: {file_content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    preamble = b"".join(preamble_parts)
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")

    content_length = len(preamble) + file_path.stat().st_size + len(epilogue)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query

    try:
        connection.putrequest("POST", target)
        header_map = {"Accept": "application/json"}
        if headers:
            header_map.update(headers)
        for name, value in header_map.items():
            connection.putheader(name, value)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        connection.send(preamble)
        with file_path.open("rb") as infile:
            while True:
                chunk = infile.read(chunk_size)
                if not chunk:
                    break
                connection.send(chunk)
        connection.send(epilogue)

        response = connection.getresponse()
        body = response.read().decode("utf-8")
        if 200 <= response.status < 300:
            return json.loads(body) if body else {}
        return {"_error": True, "status": response.status, "detail": body or response.reason}
    except Exception as exc:
        return {"_error": True, "detail": str(exc)}
    finally:
        connection.close()


def _pdf_parser_project_root() -> Path:
    return Path(
        os.environ.get("SIQ_PROJECT_ROOT")
        or Path(__file__).resolve().parents[3]
    ).expanduser().resolve()


def _pdf_parser_data_dir() -> Path:
    return Path(
        os.environ.get("SIQ_PDF2MD_DATA_DIR")
        or os.environ.get("PDF2MD_DATA_DIR")
        or (_pdf_parser_project_root() / "data" / "pdf-parser")
    ).expanduser().resolve()


def _pdf_parser_results_roots() -> tuple[Path, ...]:
    project_root = _pdf_parser_project_root()
    configured_results = os.environ.get("SIQ_PDF_RESULTS_ROOT") or os.environ.get("RESULTS_FOLDER")
    artifacts_root = os.environ.get("SIQ_ARTIFACTS_ROOT")
    candidates = [
        Path(configured_results).expanduser() if configured_results else None,
        Path(artifacts_root).expanduser() / "pdf-parser" / "results" if artifacts_root else None,
        _pdf_parser_data_dir() / "results",
        project_root / "data" / "pdf-parser" / "results",
        project_root / "apps" / "pdf-parser" / "results",
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.resolve()
        key = str(resolved)
        if key not in seen:
            roots.append(resolved)
            seen.add(key)
    return tuple(roots)


def _pdf_parser_task_dir(root: Path, task_id: str) -> Path | None:
    task_id = str(task_id or "")
    if task_id in {".", ".."} or not PDF_PARSER_TASK_ID_RE.fullmatch(task_id):
        return None
    candidate = (root / task_id).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _pdf_parser_results_root() -> Path:
    return _pdf_parser_results_roots()[0]


def _pdf_parser_result_dir(task_id: str) -> Path:
    candidates = [
        candidate
        for root in _pdf_parser_results_roots()
        if (candidate := _pdf_parser_task_dir(root, task_id)) is not None
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if not candidate.is_dir() or not _pdf_parser_result_identity_matches(candidate, task_id):
            raise RuntimeError(f"PDF parser artifact manifest task identity mismatch: {task_id}")
        return candidate
    if not candidates:
        raise ValueError(f"Invalid PDF parser task id or no safe result root is configured: {task_id}")
    return candidates[0]


def _pdf_parser_result_identity_matches(result_dir: Path, task_id: str) -> bool:
    manifest_path = result_dir / "artifact_manifest.json"
    if not manifest_path.exists():
        return True
    if manifest_path.resolve().parent != result_dir.resolve():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and str(manifest.get("task_id") or "") == str(task_id)


def _pdf_parser_result_dir_from_payload(task_id: str, payload: dict[str, Any]) -> Path | None:
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, dict):
        return None
    required_names = ["document_full.json"]
    markdown_name = next(
        (
            name
            for name in ("result_complete.md", "result.md")
            if isinstance(artifacts.get(name), dict) and artifacts[name].get("exists")
        ),
        None,
    )
    if markdown_name is None:
        raise RuntimeError("PDF parser result payload is missing a completed Markdown artifact")
    required_names.append(markdown_name)

    parents: set[Path] = set()
    allowed_task_dirs = {
        candidate
        for root in _pdf_parser_results_roots()
        if (candidate := _pdf_parser_task_dir(root, task_id)) is not None
    }
    for name in required_names:
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or not artifact.get("exists"):
            raise RuntimeError(f"PDF parser result payload is missing required artifact: {name}")
        raw_path = str(artifact.get("path") or "").strip()
        if not raw_path:
            raise RuntimeError(f"PDF parser result payload omitted artifact path: {name}")
        artifact_path = Path(raw_path).expanduser().resolve()
        if artifact_path.name != name or artifact_path.parent not in allowed_task_dirs:
            raise RuntimeError(f"PDF parser result artifact path is outside allowlisted roots: {name}")
        parents.add(artifact_path.parent)
    if len(parents) != 1:
        raise RuntimeError("PDF parser result artifacts do not share one task directory")
    result_dir = parents.pop()
    if not _pdf_parser_result_identity_matches(result_dir, task_id):
        raise RuntimeError(f"PDF parser artifact manifest task identity mismatch: {task_id}")
    return result_dir if _result_dir_looks_ready(result_dir) else None


def _bridge_task_id(task_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]", "-", str(task_id or "").strip())
    if clean and len(clean) <= 116:
        return f"doc-{clean}"
    return f"doc-{uuid.uuid5(uuid.NAMESPACE_URL, str(task_id or uuid.uuid4()))}"


def _result_dir_looks_ready(path: Path) -> bool:
    return path.exists() and path.is_dir() and (path / "document_full.json").exists() and (
        (path / "result_complete.md").exists() or (path / "result.md").exists()
    )


def _model_version_to_pdf_backend(model_version: str | None) -> str:
    value = str(model_version or "").strip().lower()
    if value in {"pipeline"}:
        return "pipeline"
    if value in {"vlm", "vlm-http-client"}:
        return "vlm-http-client"
    return "hybrid-http-client"


def _ocr_to_pdf_parse_method(ocr: str | None) -> str:
    value = str(ocr or "").strip().lower()
    if value in {"force", "ocr", "true", "1", "yes", "on"}:
        return "ocr"
    if value in {"off", "txt", "text", "false", "0", "no"}:
        return "txt"
    return "auto"


def _one_based_page_to_pdf_page_id(page_number: int) -> int:
    return max(0, int(page_number) - 1)


def _converted_dir(source: SourceFile) -> Path:
    path = source.path.parent / ".converted"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pdf_source_from_path(path: Path, filename: str, source: SourceFile) -> SourceFile:
    return SourceFile(
        path=path,
        filename=safe_client_filename(filename),
        mime_type=guess_mime_type(filename, "application/pdf"),
        extension=".pdf",
        file_size=path.stat().st_size,
        sha256=sha256_file(path),
        source_type=f"{source.source_type}_converted_pdf",
        source_url=source.source_url,
    )


def _convert_image_to_pdf_source(source: SourceFile) -> SourceFile:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
        raise RuntimeError("图片转 PDF 需要 Pillow 依赖") from exc

    pdf_path = _converted_dir(source) / f"{Path(source.filename).stem or source.path.stem}.pdf"
    with Image.open(source.path) as image:
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.convert("RGBA").getchannel("A")
            background.paste(image.convert("RGBA"), mask=alpha)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.save(pdf_path, "PDF", resolution=150.0)
    return _pdf_source_from_path(pdf_path, f"{Path(source.filename).stem or source.path.stem}.pdf", source)


def _libreoffice_binary() -> str:
    binary = shutil.which(os.environ.get("SIQ_DOCUMENT_PARSE_OFFICE_CONVERTER", ""))
    if binary:
        return binary
    for candidate in ("libreoffice", "soffice"):
        binary = shutil.which(candidate)
        if binary:
            return binary
    raise RuntimeError("未找到 LibreOffice/soffice，无法把 Office 文档转换为 PDF")


def _convert_office_to_pdf_source(source: SourceFile) -> SourceFile:
    output_dir = _converted_dir(source)
    for stale in output_dir.glob("*.pdf"):
        stale.unlink(missing_ok=True)
    timeout = int(os.environ.get("SIQ_DOCUMENT_PARSE_OFFICE_CONVERT_TIMEOUT", "120"))
    command = [
        _libreoffice_binary(),
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source.path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown LibreOffice error").strip()
        raise RuntimeError(f"Office 转 PDF 失败: {detail}")
    pdf_path = output_dir / f"{source.path.stem}.pdf"
    if not pdf_path.exists():
        candidates = sorted(output_dir.glob("*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True)
        pdf_path = candidates[0] if candidates else pdf_path
    if not pdf_path.exists():
        raise RuntimeError("Office 转 PDF 未生成输出文件")
    return _pdf_source_from_path(pdf_path, f"{Path(source.filename).stem or source.path.stem}.pdf", source)


def _config_to_pdf_parser_submit_fields(upstream_task_id: str, config: ParseConfig, source: SourceFile) -> dict[str, str]:
    bridge_market = os.environ.get("SIQ_DOCUMENT_PARSE_PDF_BRIDGE_MARKET", "DOC").strip().upper() or "DOC"
    if bridge_market not in PDF_BRIDGE_SUPPORTED_MARKETS:
        bridge_market = "DOC"
    fields = {
        "task_id": upstream_task_id,
        "backend": _model_version_to_pdf_backend(config.model_version),
        "parse_method": _ocr_to_pdf_parse_method(config.ocr),
        "formula_enable": "true" if config.enable_formula else "false",
        "table_enable": "true" if config.enable_table else "false",
        "server_url": os.environ.get("VLM_API_URL", "http://127.0.0.1:8002"),
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "true",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "false",
        "return_original_file": "false",
        "lang_list": "ch" if str(config.language or "").lower() in {"", "auto", "zh", "zh-cn", "cn"} else str(config.language),
        "market": bridge_market,
    }
    page_numbers = parse_page_ranges(config.page_ranges or "", page_count=None) if config.page_ranges else []
    if page_numbers:
        fields["start_page_id"] = str(_one_based_page_to_pdf_page_id(page_numbers[0]))
        fields["end_page_id"] = str(_one_based_page_to_pdf_page_id(page_numbers[-1]))
        if page_numbers[-1] - page_numbers[0] + 1 != len(page_numbers):
            fields["page_ranges_warning"] = "non_contiguous"
    return fields


def _filter_parse_output_pages(output: ParseOutput, page_numbers: list[int]) -> ParseOutput:
    if not page_numbers:
        return output
    allowed = set(page_numbers)
    filtered_blocks = [block for block in output.blocks if int(block.get("page_number") or 0) in allowed]
    filtered_tables = [table for table in output.tables if int(table.get("page_number") or 0) in allowed]
    filtered_figures = [figure for figure in output.figures if int(figure.get("page_number") or 0) in allowed]
    filtered_warnings = list(output.warnings)
    if len(filtered_blocks) != len(output.blocks):
        filtered_warnings.append("已按 page_ranges 过滤页面。")
    markdown = _blocks_to_markdown(filtered_blocks) if filtered_blocks else ""
    if not markdown:
        markdown = output.markdown
    return ParseOutput(
        markdown=markdown,
        blocks=filtered_blocks,
        tables=filtered_tables,
        figures=filtered_figures,
        warnings=filtered_warnings,
        page_count=len(allowed),
        provider_name=output.provider_name,
        upstream_parser_version=output.upstream_parser_version,
        document_kind=output.document_kind,
        language_detected=output.language_detected,
        page_metadata=output.page_metadata,
        raw_artifacts_dir=output.raw_artifacts_dir,
    )


def _pdf_parser_staging_root(source: SourceFile) -> Path:
    source_parent = source.path.resolve().parent
    if source_parent.name == ".converted":
        source_parent = source_parent.parent
    return source_parent / ".pdf-parser-staging"


def _materialize_pdf_parser_result(
    task_id: str,
    upstream_task_id: str,
    source: SourceFile,
    *,
    result_payload: Mapping[str, Any] | None = None,
    identity_scope: Mapping[str, Any] | None = None,
) -> Path:
    mode = artifact_transport_mode()
    bridge_headers = _pdf_parser_headers(identity_scope=identity_scope)
    encoded_task_id = urllib.parse.quote(upstream_task_id, safe="")
    if result_payload is None:
        result_payload = _json_request(
            f"{_pdf_parser_api_base()}/api/result/{encoded_task_id}",
            headers=bridge_headers,
            timeout=int(os.environ.get("SIQ_DOCUMENT_PARSE_PDF_STATUS_TIMEOUT", "120")),
        )
    if not isinstance(result_payload, Mapping) or result_payload.get("_error"):
        detail = result_payload.get("detail") if isinstance(result_payload, Mapping) else None
        raise ArtifactTransportError(
            str(detail or "PDF parser result API did not return an artifact contract")
        )

    shared_error: Exception | None = None
    if mode != "api":
        try:
            shared_dir = _pdf_parser_result_dir_from_payload(
                upstream_task_id,
                dict(result_payload),
            )
            if shared_dir is None:
                shared_dir = _pdf_parser_result_dir(upstream_task_id)
            if _result_dir_looks_ready(shared_dir):
                return shared_dir
            shared_error = RuntimeError(
                f"Upstream parser result directory is not ready: {shared_dir}"
            )
        except Exception as exc:
            shared_error = exc
        if mode == "shared_fs":
            raise ArtifactTransportError(
                f"PDF parser shared filesystem transport is unavailable: {shared_error}"
            ) from shared_error

    staged = stage_pdf_parser_artifacts(
        task_id=upstream_task_id,
        result_payload=result_payload,
        api_base=_pdf_parser_api_base(),
        headers=bridge_headers,
        staging_root=_pdf_parser_staging_root(source),
    )
    return staged.result_dir


def _parse_pdf_via_pdf_parser(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    submit_url = f"{_pdf_parser_api_base()}/api/upload"
    upstream_requested_task_id = _bridge_task_id(task_id)
    fields = _config_to_pdf_parser_submit_fields(upstream_requested_task_id, config, source)
    bridge_headers = _pdf_parser_headers(identity_scope=identity_scope)
    result = _stream_multipart_post(
        submit_url,
        fields=fields,
        file_field_name="files",
        filename=source.filename,
        file_path=source.path,
        content_type=source.mime_type or "application/pdf",
        headers=bridge_headers,
        timeout=600,
    )
    if result.get("_error"):
        raise RuntimeError(result.get("detail", "Failed to submit PDF to upstream parser"))
    upstream_task_id = str(result.get("task_id") or upstream_requested_task_id)
    if on_status:
        on_status({"status": "submitted", "stage": "submitted", "task_id": upstream_task_id})
    max_wait_seconds = int(os.environ.get("SIQ_DOCUMENT_PARSE_PDF_BRIDGE_TIMEOUT", str(6 * 60 * 60)))
    status_timeout = int(os.environ.get("SIQ_DOCUMENT_PARSE_PDF_STATUS_TIMEOUT", "120"))
    deadline = time.time() + max(600, max_wait_seconds)
    while time.time() < deadline:
        encoded_task_id = urllib.parse.quote(upstream_task_id, safe="")
        status = _json_request(
            f"{_pdf_parser_api_base()}/api/status/{encoded_task_id}",
            headers=bridge_headers,
            timeout=status_timeout,
        )
        if status.get("_error"):
            detail = str(status.get("detail") or "")
            code = int(status.get("status") or 0)
            if code in {408, 502, 503, 504} or "timed out" in detail.lower() or "timeout" in detail.lower():
                if on_status:
                    on_status({"status": "processing", "stage": "processing", "task_id": upstream_task_id})
                time.sleep(1.0)
                continue
            raise RuntimeError(status.get("detail", "Failed to poll upstream parser"))
        if on_status:
            on_status({**status, "task_id": upstream_task_id})
        if status.get("status") in {"completed", "completed_with_warnings"}:
            break
        if status.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(status.get("error") or status.get("message") or f"Upstream parser ended with {status.get('status')}")
        time.sleep(1.0)
    else:
        raise TimeoutError(f"Upstream parser did not finish within {max_wait_seconds} seconds: {upstream_task_id}")
    result_payload = _json_request(
        f"{_pdf_parser_api_base()}/api/result/{encoded_task_id}",
        headers=bridge_headers,
        timeout=status_timeout,
    )
    result_dir = _materialize_pdf_parser_result(
        task_id,
        upstream_task_id,
        source,
        result_payload=result_payload,
        identity_scope=identity_scope,
    )
    from mineru_import import parse_mineru_output_dir, rewrite_image_paths_to_result

    try:
        source_file, output = parse_mineru_output_dir(task_id, result_dir, config)
        rewrite_image_paths_to_result(output)
        if source_file.path.exists():
            source_file.source_type = "pdf_parser_import"
        page_numbers = (
            parse_page_ranges(config.page_ranges or "", page_count=output.page_count)
            if config.page_ranges
            else []
        )
        if page_numbers:
            output = _filter_parse_output_pages(output, page_numbers)
        if fields.get("page_ranges_warning") == "non_contiguous":
            output.warnings.append("非连续 page_ranges 已由上游整页解析后在本地过滤。")
        output.upstream_task_id = upstream_task_id
        return output
    except Exception:
        cleanup_staged_pdf_parser_artifacts(
            result_dir,
            task_id=upstream_task_id,
            staging_root=_pdf_parser_staging_root(source),
        )
        raise


def _call_pdf_parser_bridge(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    try:
        return _parse_pdf_via_pdf_parser(
            task_id,
            source,
            config,
            on_status=on_status,
            identity_scope=identity_scope,
        )
    except TypeError:
        if on_status is None and identity_scope is None:
            return _parse_pdf_via_pdf_parser(task_id, source, config)
        raise


def cleanup_pdf_parser_bridge_resources(
    upstream_task_id: str,
    *,
    raw_artifacts_dir: Path | str | None,
    identity_scope: Mapping[str, Any] | None = None,
    staging_root: Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("SIQ_DOCUMENT_PARSE_KEEP_PDF_BRIDGE_OUTPUT", "").lower() in {"1", "true", "yes", "on"}:
        return {"state": "retained", "cleaned": True, "staged_cleaned": False}
    upstream_task_id = str(upstream_task_id or "")
    if upstream_task_id in {".", ".."} or not PDF_PARSER_TASK_ID_RE.fullmatch(upstream_task_id):
        return {"state": "invalid", "cleaned": False, "staged_cleaned": False}
    raw_dir = Path(str(raw_artifacts_dir or ""))
    encoded_task_id = urllib.parse.quote(upstream_task_id, safe="")
    response = _json_request(
        f"{_pdf_parser_api_base()}/api/tasks/{encoded_task_id}",
        method="DELETE",
        headers=_pdf_parser_headers(identity_scope=identity_scope),
        timeout=30,
    )
    staged_cleaned = cleanup_staged_pdf_parser_artifacts(
        raw_dir,
        task_id=upstream_task_id,
        staging_root=staging_root,
    )
    if response.get("_error"):
        try:
            status_code = int(response.get("status") or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code == 404:
            return {"state": "not_found", "cleaned": True, "staged_cleaned": staged_cleaned}
        return {"state": "deferred", "cleaned": False, "staged_cleaned": staged_cleaned}
    return {"state": "deleted", "cleaned": True, "staged_cleaned": staged_cleaned}


def cleanup_pdf_parser_bridge_output(
    output: ParseOutput,
    identity_scope: Mapping[str, Any] | None = None,
    *,
    staging_root: Path | None = None,
) -> str | None:
    if os.environ.get("SIQ_DOCUMENT_PARSE_KEEP_PDF_BRIDGE_OUTPUT", "").lower() in {"1", "true", "yes", "on"}:
        return None
    upstream_task_id = str(output.upstream_task_id or "")
    if upstream_task_id in {"", ".", ".."} or not PDF_PARSER_TASK_ID_RE.fullmatch(upstream_task_id):
        return None
    result = cleanup_pdf_parser_bridge_resources(
        upstream_task_id,
        raw_artifacts_dir=output.raw_artifacts_dir,
        identity_scope=identity_scope,
        staging_root=staging_root,
    )
    staged_cleaned = bool(result.get("staged_cleaned"))
    suffix = "；本地 staging 已清理" if staged_cleaned else ""
    if result.get("state") == "not_found":
        return f"pdf-parser 临时任务已不存在: {upstream_task_id}{suffix}"
    if not result.get("cleaned"):
        return f"pdf-parser 临时任务清理延期，等待上游 API 重试: {upstream_task_id}{suffix}"
    return f"已删除临时 pdf-parser 文档解析任务: {upstream_task_id}{suffix}"


def cancel_pdf_parser_bridge_task(
    upstream_task_id: str,
    *,
    raw_artifacts_dir: Path | str | None = None,
    identity_scope: Mapping[str, Any] | None = None,
    staging_root: Path | None = None,
) -> dict[str, Any]:
    """Stop and remove one temporary PDF bridge task without exposing upstream details."""
    upstream_task_id = str(upstream_task_id or "")
    if upstream_task_id in {"", ".", ".."} or not PDF_PARSER_TASK_ID_RE.fullmatch(upstream_task_id):
        return {"state": "invalid", "cancelled": False, "staged_cleaned": False}

    raw_dir = Path(str(raw_artifacts_dir or ""))
    encoded_task_id = urllib.parse.quote(upstream_task_id, safe="")
    headers = _pdf_parser_headers(identity_scope=identity_scope)
    cancel_response = _json_request(
        f"{_pdf_parser_api_base()}/api/cancel/{encoded_task_id}",
        method="POST",
        headers=headers,
        timeout=30,
    )
    try:
        cancel_status = int(cancel_response.get("status") or 0)
    except (TypeError, ValueError):
        cancel_status = 0

    if cancel_response.get("_error") and cancel_status == 404:
        staged_cleaned = cleanup_staged_pdf_parser_artifacts(
            raw_dir,
            task_id=upstream_task_id,
            staging_root=staging_root,
        )
        return {"state": "not_found", "cancelled": True, "staged_cleaned": staged_cleaned}
    if cancel_response.get("_error") and cancel_status not in {400, 409}:
        staged_cleaned = cleanup_staged_pdf_parser_artifacts(
            raw_dir,
            task_id=upstream_task_id,
            staging_root=staging_root,
        )
        return {"state": "deferred", "cancelled": False, "staged_cleaned": staged_cleaned}

    delete_response = _json_request(
        f"{_pdf_parser_api_base()}/api/tasks/{encoded_task_id}",
        method="DELETE",
        headers=headers,
        timeout=30,
    )
    staged_cleaned = cleanup_staged_pdf_parser_artifacts(
        raw_dir,
        task_id=upstream_task_id,
        staging_root=staging_root,
    )
    try:
        delete_status = int(delete_response.get("status") or 0)
    except (TypeError, ValueError):
        delete_status = 0
    if delete_response.get("_error") and delete_status != 404:
        return {"state": "deferred", "cancelled": False, "staged_cleaned": staged_cleaned}
    return {
        "state": "not_found" if delete_status == 404 else "deleted",
        "cancelled": True,
        "staged_cleaned": staged_cleaned,
    }


def _parse_via_converted_pdf(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    document_kind: str,
    converter,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    pdf_source = converter(source)
    output = _call_pdf_parser_bridge(
        task_id,
        pdf_source,
        config,
        on_status=on_status,
        identity_scope=identity_scope,
    )
    output.provider_name = f"pdf_parser_bridge:{document_kind}_to_pdf"
    output.document_kind = document_kind
    return output


def _decode_bytes(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _text_to_blocks(task_id: str, text: str, kind: str = "text") -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraphs: list[str] = []
    order = 1

    def flush_paragraph() -> None:
        nonlocal order
        if not paragraphs:
            return
        paragraph = "\n".join(paragraphs).strip()
        paragraphs.clear()
        if not paragraph:
            return
        block_id = f"b{order:06d}"
        blocks.append(
            {
                "block_id": block_id,
                "type": "paragraph",
                "sub_type": kind,
                "text": paragraph,
                "markdown": paragraph,
                "html": "",
                "page_number": 1,
                "page_index": 0,
                "sheet_name": "",
                "slide_number": None,
                "bbox": [],
                "bbox_unit": "none",
                "reading_order": order,
                "parent_block_id": "",
                "source_ref": {
                    "evidence_id": f"doc:{task_id}:p1:{block_id}",
                    "source_type": f"{kind}_block",
                    "path": "",
                },
                "confidence": 1.0,
                "warnings": [],
            }
        )
        order += 1

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        heading = HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            block_id = f"b{order:06d}"
            level = len(heading.group(1))
            title = heading.group(2).strip()
            blocks.append(
                {
                    "block_id": block_id,
                    "type": "title" if level == 1 else "heading",
                    "sub_type": f"h{level}",
                    "text": title,
                    "markdown": line,
                    "html": "",
                    "page_number": 1,
                    "page_index": 0,
                    "sheet_name": "",
                    "slide_number": None,
                    "bbox": [],
                    "bbox_unit": "none",
                    "reading_order": order,
                    "parent_block_id": "",
                    "source_ref": {
                        "evidence_id": f"doc:{task_id}:p1:{block_id}",
                        "source_type": f"{kind}_heading",
                        "path": "",
                    },
                    "confidence": 1.0,
                    "warnings": [],
                }
            )
            order += 1
        else:
            paragraphs.append(line)
    flush_paragraph()
    return blocks


def parse_text_document(task_id: str, source: SourceFile, config: ParseConfig) -> ParseOutput:
    text = _normalize_text(_decode_bytes(source.path))
    blocks = _text_to_blocks(task_id, text, "text")
    warnings = []
    if not blocks:
        warnings.append(
            {
                "code": "empty_text",
                "severity": "warning",
                "message": "文档没有可解析文本内容。",
            }
        )
    return ParseOutput(
        markdown=_blocks_to_markdown(blocks),
        blocks=blocks,
        warnings=warnings,
        page_count=1,
        provider_name="simple_text_parser",
        document_kind="text",
    )


def parse_html_document(task_id: str, source: SourceFile, config: ParseConfig) -> ParseOutput:
    raw = _decode_bytes(source.path)
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = HTML_BLOCK_BREAK_RE.sub("\n", raw)
    text = HTML_TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = _normalize_text(text)
    blocks = _text_to_blocks(task_id, text, "html")
    return ParseOutput(
        markdown=_blocks_to_markdown(blocks),
        blocks=blocks,
        warnings=[] if blocks else [{"code": "empty_html", "severity": "warning", "message": "HTML 正文提取为空。"}],
        page_count=1,
        provider_name="html_reader",
        document_kind="html",
    )


def parse_pdf_document(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    try:
        output = _call_pdf_parser_bridge(
            task_id,
            source,
            config,
            on_status=on_status,
            identity_scope=identity_scope,
        )
        output.provider_name = output.provider_name or "pdf_parser_bridge"
        return output
    except Exception as exc:
        raise RuntimeError(f"MinerU PDF 解析失败，已停止而不是回退到简易文本解析: {exc}") from exc


def _raise_mineru_only_unsupported(source: SourceFile, document_kind: str) -> None:
    raise RuntimeError(
        f"MinerU-only 模式不再为 {document_kind} 文件生成本地简易或占位产物: "
        f"{source.filename}。请先转换为 PDF 后使用本机 MinerU 解析，或导入已有 MinerU 输出目录。"
    )


def parse_docx_document(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    return _parse_via_converted_pdf(
        task_id,
        source,
        config,
        "word",
        _convert_office_to_pdf_source,
        on_status=on_status,
        identity_scope=identity_scope,
    )


def parse_spreadsheet_document(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    return _parse_via_converted_pdf(
        task_id,
        source,
        config,
        "excel",
        _convert_office_to_pdf_source,
        on_status=on_status,
        identity_scope=identity_scope,
    )


def parse_image_document(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    return _parse_via_converted_pdf(
        task_id,
        source,
        config,
        "image",
        _convert_image_to_pdf_source,
        on_status=on_status,
        identity_scope=identity_scope,
    )


def parse_office_placeholder(
    task_id: str,
    source: SourceFile,
    config: ParseConfig,
    kind: str,
    on_status=None,
    identity_scope: Mapping[str, Any] | None = None,
) -> ParseOutput:
    return _parse_via_converted_pdf(
        task_id,
        source,
        config,
        kind,
        _convert_office_to_pdf_source,
        on_status=on_status,
        identity_scope=identity_scope,
    )


def parse_json_schema_excerpt(schema: dict[str, Any], markdown: str) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        properties = {}
    result: dict[str, Any] = {}
    text = markdown or ""
    for key in properties:
        pattern = re.compile(rf"(?im)^\s*{re.escape(str(key))}\s*[:：]\s*(.+?)\s*$")
        match = pattern.search(text)
        result[key] = match.group(1).strip() if match else None
    return result


def _markdown_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    width = max((len(row) for row in rows), default=0)
    normalized = [[str(cell or "").replace("\n", " ").strip() for cell in row] + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:] or [[""] * width]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)
