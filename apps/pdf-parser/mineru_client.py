"""Small MinerU HTTP client helpers."""

from __future__ import annotations

import http.client
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


def safe_header_value(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", "_").replace("\n", "_")


def json_request(url, method="GET", data=None, headers=None, timeout=30):
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    payload = data
    if data is not None and isinstance(data, dict):
        payload = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def stream_multipart_post(url, fields, file_field_name, filename, file_path, content_type=None, timeout=300, chunk_size=1024 * 1024):
    parsed = urllib.parse.urlsplit(url)
    boundary = "----CodexBoundary" + uuid.uuid4().hex
    file_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    preamble_parts = []
    for name, value in fields.items():
        preamble_parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{safe_header_value(name)}"\r\n\r\n'
                f"{value if value is not None else ''}\r\n"
            ).encode("utf-8")
        )
    preamble_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{safe_header_value(file_field_name)}"; filename="{safe_header_value(filename)}"\r\n'
            f"Content-Type: {file_content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    preamble = b"".join(preamble_parts)
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")

    content_length = len(preamble) + os.path.getsize(file_path) + len(epilogue)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query

    try:
        connection.putrequest("POST", target)
        connection.putheader("Accept", "application/json")
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        connection.send(preamble)
        with open(file_path, "rb") as infile:
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


def friendly_submit_error(detail):
    message = detail or "Unknown error"
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return (
            "提交到 MinerU 超时。MinerU 可能仍在预热、下载依赖或刚刚重启，"
            "不是 PDF 本身有问题。请稍等 1-3 分钟后重试。"
        )
    if "connection refused" in lowered:
        return "MinerU 服务当前不可用，请确认 8003 服务已启动后再试。"
    return message


def check_service_health(url, timeout=5):
    ok = False
    detail = ""
    payload = None
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            ok = response.status == 200
            if body:
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"raw": body}
    except Exception as exc:
        detail = str(exc)
    return ok, detail, payload


def submit_readiness(mineru_api_base, vlm_api_base, timeout=5):
    mineru_ok, mineru_detail, mineru_payload = check_service_health(f"{mineru_api_base}/health", timeout=timeout)
    vlm_ok, vlm_detail, _ = check_service_health(f"{vlm_api_base}/health", timeout=timeout)

    submit_ready = mineru_ok and vlm_ok
    warning = ""
    if not mineru_ok:
        if "timed out" in mineru_detail.lower():
            warning = "MinerU 正在预热或被长任务阻塞，暂时不建议上传。"
        else:
            warning = "MinerU 当前不可用，暂时无法安全提交任务。"
    elif not vlm_ok:
        warning = "VLM 服务当前不可用，暂时不建议上传。"

    return {
        "mineru": mineru_ok,
        "mineru_detail": mineru_detail,
        "mineru_payload": mineru_payload,
        "vlm": vlm_ok,
        "vlm_detail": vlm_detail,
        "submit_ready": submit_ready,
        "warning": warning,
    }
