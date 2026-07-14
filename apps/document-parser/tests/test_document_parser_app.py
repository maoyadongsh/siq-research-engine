from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import time
import types
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    class _DummyResponse:
        def __init__(self, data=b"", status_code=200, json_payload=None, mimetype="application/json", headers=None):
            self.status_code = status_code
            self._json = json_payload
            self.mimetype = mimetype
            self.headers = dict(headers or {})
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.data = data if isinstance(data, bytes) else bytes(data or b"")

        @property
        def json(self):
            return self._json

        def get_json(self):
            return self._json


    class _DummyUploadFile:
        def __init__(self, fileobj, filename, mimetype="application/octet-stream"):
            self._fileobj = fileobj
            self.filename = filename
            self.mimetype = mimetype

        def save(self, path):
            if hasattr(self._fileobj, "seek"):
                self._fileobj.seek(0)
            payload = self._fileobj.read()
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            Path(path).write_bytes(payload)


    class _DummyFiles:
        def __init__(self, items=None):
            self._items = items or {}

        def getlist(self, key):
            value = self._items.get(key, [])
            return list(value) if isinstance(value, list) else [value]


    class _DummyRequest:
        def __init__(self):
            self.args = {}
            self.files = _DummyFiles()
            self.form = {}
            self.headers = {}
            self.cookies = {}
            self.method = "GET"
            self.path = ""
            self._json = None

        def get_json(self, silent=True):
            return self._json


    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            self.config = {}
            self._routes = []
            self._before_request = []

        def route(self, *args, **kwargs):
            rule = args[0] if args else kwargs.get("rule", "")
            methods = [str(method).upper() for method in kwargs.get("methods") or ["GET"]]

            def _compile(pattern):
                token_re = re.compile(r"<(?:(int|path):)?([a-zA-Z_][a-zA-Z0-9_]*)>")
                cursor = 0
                pieces = ["^"]
                for match in token_re.finditer(pattern):
                    pieces.append(re.escape(pattern[cursor:match.start()]))
                    converter = match.group(1) or "string"
                    name = match.group(2)
                    if converter == "int":
                        pieces.append(f"(?P<{name}>\\d+)")
                    elif converter == "path":
                        pieces.append(f"(?P<{name}>.+)")
                    else:
                        pieces.append(f"(?P<{name}>[^/]+)")
                    cursor = match.end()
                pieces.append(re.escape(pattern[cursor:]))
                pieces.append("$")
                return re.compile("".join(pieces))

            def decorator(func):
                self._routes.append({"rule": rule, "methods": methods, "regex": _compile(rule), "func": func})
                return func

            return decorator

        def get(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["methods"] = ["GET"]
            return self.route(*args, **kwargs)

        def post(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["methods"] = ["POST"]
            return self.route(*args, **kwargs)

        def delete(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["methods"] = ["DELETE"]
            return self.route(*args, **kwargs)

        def put(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["methods"] = ["PUT"]
            return self.route(*args, **kwargs)

        def patch(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["methods"] = ["PATCH"]
            return self.route(*args, **kwargs)

        def before_request(self, func=None):
            if func is not None:
                self._before_request.append(func)
                return func

            def decorator(func):
                self._before_request.append(func)
                return func

            return decorator

        def errorhandler(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def test_client(self):
            return _DummyTestClient(self)

        @staticmethod
        def _normalize_response(result, status_code=200, headers=None):
            headers = dict(headers or {})
            if isinstance(result, tuple):
                if len(result) == 2:
                    result, status_code = result
                elif len(result) == 3:
                    result, status_code, headers = result
            if isinstance(result, _DummyResponse):
                result.status_code = status_code
                result.headers.update(headers)
                return result
            if hasattr(result, "data") and hasattr(result, "status_code"):
                result.status_code = status_code
                if hasattr(result, "headers"):
                    result.headers.update(headers)
                return result
            if isinstance(result, (dict, list)):
                return _DummyResponse(
                    data=json.dumps(result, ensure_ascii=False).encode("utf-8"),
                    status_code=status_code,
                    json_payload=result,
                    headers=headers,
                )
            if isinstance(result, str):
                return _DummyResponse(
                    data=result.encode("utf-8"),
                    status_code=status_code,
                    json_payload=result,
                    headers=headers,
                )
            if isinstance(result, bytes):
                return _DummyResponse(data=result, status_code=status_code, headers=headers)
            return _DummyResponse(status_code=status_code, headers=headers)

        @staticmethod
        def _coerce_json_payload(response):
            if not isinstance(response, _DummyResponse):
                return response
            if response._json is not None:
                return response
            if response.data:
                try:
                    response._json = json.loads(response.data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            return response

        def _dispatch(self, method, path, *, data=None, json_payload=None, headers=None, content_type=None):
            parsed = urlsplit(path)
            request = sys.modules["flask"].request
            request.method = method.upper()
            request.path = parsed.path
            request.headers = dict(headers or {})
            request.cookies = {}
            request.args = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
            request._json = json_payload
            request.form = {}
            request.files = _DummyFiles()
            if data is not None and content_type and "multipart/form-data" in content_type.lower():
                form = {}
                files = {}
                for key, value in data.items():
                    if key == "files":
                        items = value if isinstance(value, list) else [value]
                        uploads = []
                        for item in items:
                            if isinstance(item, tuple):
                                fileobj = item[0]
                                filename = item[1]
                                mimetype = item[2] if len(item) > 2 else "application/octet-stream"
                            else:
                                fileobj = item
                                filename = "upload.bin"
                                mimetype = "application/octet-stream"
                            uploads.append(_DummyUploadFile(fileobj, filename, mimetype=mimetype))
                        files[key] = uploads
                    else:
                        form[key] = str(value)
                request.form = form
                request.files = _DummyFiles(files)
            elif isinstance(data, dict):
                request.form = {key: str(value) for key, value in data.items()}

            for hook in self._before_request:
                result = hook()
                if result is not None:
                    return self._normalize_response(result)

            for route in self._routes:
                if method.upper() not in route["methods"]:
                    continue
                match = route["regex"].match(parsed.path)
                if not match:
                    continue
                kwargs = {}
                for key, value in match.groupdict().items():
                    kwargs[key] = int(value) if isinstance(value, str) and value.isdigit() else value
                return self._coerce_json_payload(self._normalize_response(route["func"](**kwargs)))

            return self._coerce_json_payload(_DummyResponse(
                data=json.dumps({"error": "not_found"}, ensure_ascii=False).encode("utf-8"),
                status_code=404,
                json_payload={"error": "not_found"},
            ))


    class _DummyTestClient:
        def __init__(self, app):
            self.app = app

        def open(self, path, method="GET", data=None, json=None, headers=None, content_type=None):
            return self.app._dispatch(method, path, data=data, json_payload=json, headers=headers, content_type=content_type)

        def get(self, path, **kwargs):
            return self.open(path, method="GET", **kwargs)

        def post(self, path, **kwargs):
            return self.open(path, method="POST", **kwargs)

        def delete(self, path, **kwargs):
            return self.open(path, method="DELETE", **kwargs)

        def put(self, path, **kwargs):
            return self.open(path, method="PUT", **kwargs)

        def patch(self, path, **kwargs):
            return self.open(path, method="PATCH", **kwargs)


    flask_stub = types.ModuleType("flask")
    flask_stub.Flask = _DummyFlask
    flask_stub.jsonify = lambda *args, **kwargs: args[0] if args else kwargs
    flask_stub.make_response = lambda value: types.SimpleNamespace(value=value, set_cookie=lambda *args, **kwargs: None)
    flask_stub.render_template = lambda *args, **kwargs: ""
    flask_stub.request = _DummyRequest()
    flask_stub.send_file = lambda path, *args, **kwargs: _DummyResponse(
        data=Path(path).read_bytes() if Path(path).exists() else b"",
        status_code=200,
        mimetype=kwargs.get("mimetype") or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{kwargs.get("download_name") or Path(path).name}"'
        }
        if kwargs.get("as_attachment")
        else {},
    )
    original_send_file = flask_stub.send_file

    def _send_file(path, *args, **kwargs):
        response = original_send_file(path, *args, **kwargs)
        if Path(path).suffix.lower() == ".json" and response.data:
            try:
                response._json = json.loads(response.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return response

    flask_stub.send_file = _send_file
    sys.modules.setdefault("flask", flask_stub)


def load_app_module(tmp_path):
    base = Path(__file__).resolve().parents[1]
    os.environ["SIQ_DOCUMENT_PARSE_DATA_DIR"] = str(tmp_path / "data")
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("document_parser_app_test", base / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_app(tmp_path):
    return load_app_module(tmp_path).app.test_client()


def test_url_validation_rejects_private_and_non_http_destinations(monkeypatch, tmp_path):
    module = load_app_module(tmp_path)
    monkeypatch.setattr(module, "_is_public_hostname", lambda hostname: hostname == "public.example")

    module._validate_public_url("https://public.example/report.pdf")
    with pytest.raises(ValueError, match="host is not allowed"):
        module._validate_public_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="Only http/https"):
        module._validate_public_url("file:///etc/passwd")


def test_url_redirect_handler_revalidates_redirect_destination(monkeypatch, tmp_path):
    module = load_app_module(tmp_path)
    monkeypatch.setattr(module, "_is_public_hostname", lambda hostname: hostname == "public.example")
    handler = module._PublicOnlyRedirectHandler()

    with pytest.raises(ValueError, match="host is not allowed"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data",
        )


def test_create_url_task_rejects_private_destination_at_http_boundary(monkeypatch, tmp_path):
    module = load_app_module(tmp_path)
    monkeypatch.setattr(module, "_is_public_hostname", lambda _hostname: False)
    monkeypatch.setattr(module, "build_opener", lambda *_args: pytest.fail("blocked URL must not be opened"))

    response = module.app.test_client().post(
        "/api/tasks",
        json={"source_type": "url", "url": "http://169.254.169.254/latest/meta-data"},
    )

    assert response.status_code == 400
    assert response.json == {"error": "invalid_url", "message": "URL host is not allowed"}


def wait_for_terminal(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    last_payload = {}
    while time.time() < deadline:
        response = client.get(f"/api/status/{task_id}")
        assert response.status_code == 200
        last_payload = response.json
        if last_payload["status"] in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {last_payload}")


def test_owner_scope_filters_document_task_routes(tmp_path):
    document_app = load_app_module(tmp_path)
    document_app.APP_ACCESS_TOKEN = "owner-token"
    client = document_app.app.test_client()
    document_app.store.create_task(
        {
            "task_id": "owned-doc",
            "filename": "owned.md",
            "owner_id": "alice",
            "tenant_id": "unknown",
            "market_scope": "EU",
            "parse_config_hash": "hash-eu",
            "document_kind": "text",
            "source_type": "upload",
            "source_url": "",
            "status": "completed",
            "stage": "completed",
            "progress_percent": 100,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "text/markdown",
            "config": {},
        }
    )

    blocked = client.get(
        "/api/tasks/owned-doc",
        headers={"X-Document-Parser-Token": "owner-token", "X-SIQ-User-Id": "bob"},
    )
    allowed = client.get(
        "/api/tasks/owned-doc",
        headers={"X-Document-Parser-Token": "owner-token", "X-SIQ-User-Id": "alice"},
    )
    listing = client.get(
        "/api/tasks",
        headers={"X-Document-Parser-Token": "owner-token", "X-SIQ-User-Id": "alice"},
    )

    assert blocked.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json["task_id"] == "owned-doc"
    assert allowed.json["owner_id"] == "alice"
    assert allowed.json["market_scope"] == "EU"
    assert [task["task_id"] for task in listing.json["tasks"]] == ["owned-doc"]


def test_legacy_document_task_requires_legacy_scope_marker_for_user_headers(tmp_path):
    document_app = load_app_module(tmp_path)
    document_app.APP_ACCESS_TOKEN = "legacy-token"
    client = document_app.app.test_client()
    document_app.store.create_task(
        {
            "task_id": "legacy-doc",
            "filename": "legacy.md",
            "document_kind": "text",
            "source_type": "upload",
            "source_url": "",
            "status": "completed",
            "stage": "completed",
            "progress_percent": 100,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "text/markdown",
            "config": {},
        }
    )

    blocked = client.get(
        "/api/tasks/legacy-doc",
        headers={"X-Document-Parser-Token": "legacy-token", "X-SIQ-User-Id": "alice"},
    )
    allowed = client.get(
        "/api/tasks/legacy-doc",
        headers={
            "X-Document-Parser-Token": "legacy-token",
            "X-SIQ-User-Id": "alice",
            "X-SIQ-Allow-Legacy-Task": "1",
        },
    )

    assert blocked.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json["owner_id"] == "system"
    assert allowed.json["tenant_id"] == "unknown"
    assert allowed.json["legacy_owner"] is True


def test_create_task_requires_token_in_docker_profile(tmp_path, monkeypatch):
    document_app = load_app_module(tmp_path)
    document_app.APP_ACCESS_TOKEN = ""
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "docker")

    response = document_app.app.test_client().post("/api/tasks", data={})

    assert response.status_code == 401


def test_unvalidated_admin_header_does_not_bypass_document_owner_scope(tmp_path, monkeypatch):
    document_app = load_app_module(tmp_path)
    document_app.APP_ACCESS_TOKEN = ""
    monkeypatch.setenv("SIQ_ENV", "local")
    client = document_app.app.test_client()
    document_app.store.create_task(
        {
            "task_id": "admin-forged-doc",
            "filename": "owned.md",
            "owner_id": "alice",
            "tenant_id": "unknown",
            "market_scope": "DOC",
            "parse_config_hash": "hash-doc",
            "document_kind": "text",
            "source_type": "upload",
            "source_url": "",
            "status": "completed",
            "stage": "completed",
            "progress_percent": 100,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "text/markdown",
            "config": {},
        }
    )

    response = client.get(
        "/api/tasks/admin-forged-doc",
        headers={"X-SIQ-User-Role": "admin", "X-SIQ-User-Id": "mallory"},
    )

    assert response.status_code == 404


def test_valid_token_preserves_document_admin_scope(tmp_path):
    document_app = load_app_module(tmp_path)
    document_app.APP_ACCESS_TOKEN = "admin-token"
    client = document_app.app.test_client()
    document_app.store.create_task(
        {
            "task_id": "admin-token-doc",
            "filename": "owned.md",
            "owner_id": "alice",
            "tenant_id": "unknown",
            "market_scope": "DOC",
            "parse_config_hash": "hash-doc",
            "document_kind": "text",
            "source_type": "upload",
            "source_url": "",
            "status": "completed",
            "stage": "completed",
            "progress_percent": 100,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "text/markdown",
            "config": {},
        }
    )

    response = client.get(
        "/api/tasks/admin-token-doc",
        headers={
            "X-Document-Parser-Token": "admin-token",
            "X-SIQ-User-Role": "admin",
            "X-SIQ-User-Id": "mallory",
        },
    )

    assert response.status_code == 200
    assert response.json["task_id"] == "admin-token-doc"


def test_markdown_upload_generates_normalized_artifacts(tmp_path):
    client = load_app(tmp_path)
    payload = b"# Contract\n\nparty_a: Alice\n\nparty_b: Bob\n"

    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(payload), "sample.md")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    task = response.json["tasks"][0]
    assert task["status"] == "queued"
    task_id = task["task_id"]
    task = wait_for_terminal(client, task_id)
    assert task["status"] == "completed"

    result = client.get(f"/api/result/{task_id}")
    assert result.status_code == 200
    assert "DOC_BLOCK" in result.json["markdown"]
    assert result.json["manifest"]["document_kind"] == "text"
    assert result.json["artifacts"]["blocks.json"]["exists"] is True

    blocks = client.get(f"/api/artifact/{task_id}/blocks.json")
    assert blocks.status_code == 200
    assert blocks.json["schema_version"] == "document_blocks_v1"
    assert blocks.json["blocks"]


def test_health_describes_pdf_bridge_and_document_artifact_archive(tmp_path):
    client = load_app(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    providers = response.json["providers"]
    assert providers["pdf_parser_bridge"] is True
    assert providers["image_to_pdf_bridge"] is True
    assert providers["office_to_pdf_bridge"] is True
    assert providers["spreadsheet_to_pdf_bridge"] is True
    assert providers["office_local"] is False
    assert providers["image_local"] is False
    assert response.json["parser_engine"]["service"] == "apps/pdf-parser"
    assert response.json["parser_engine"]["final_artifact_root"] == str(tmp_path / "data" / "results")
    assert response.json["conversion_pipeline"]["image"] == "image_to_pdf -> pdf_parser_bridge"


def test_readiness_requires_worker_and_pdf_bridge(tmp_path, monkeypatch):
    module = load_app_module(tmp_path)

    class AliveThread:
        @staticmethod
        def is_alive():
            return True

    module.worker_thread = AliveThread()
    module.WORKER_AUTOSTART = True
    monkeypatch.setattr(module, "pdf_parser_json_request", lambda *args, **kwargs: {"ready": True, "status": "ready"})

    payload = module._readiness_payload()

    assert payload == {
        "status": "ready",
        "ready": True,
        "worker_ready": True,
        "pdf_parser_ready": True,
        "pdf_parser_status": "ready",
    }


def test_ready_endpoint_returns_503_without_auth_challenge_when_dependency_is_down(tmp_path, monkeypatch):
    module = load_app_module(tmp_path)
    module.APP_ACCESS_TOKEN = "internal-token"
    monkeypatch.setattr(module, "ensure_worker_started", lambda: None)
    monkeypatch.setattr(
        module,
        "_readiness_payload",
        lambda: {
            "status": "unavailable",
            "ready": False,
            "worker_ready": True,
            "pdf_parser_ready": False,
            "pdf_parser_status": "unavailable",
        },
    )

    response = module.app.test_client().get("/api/ready")

    assert response.status_code == 503
    assert response.json["ready"] is False
    assert response.json["pdf_parser_ready"] is False


def test_pdf_provider_prefers_upstream_bridge(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    from contracts import ParseConfig, ParseOutput, SourceFile

    pdf_path = tmp_path / "bridge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    source = SourceFile(
        path=pdf_path,
        filename="bridge.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )

    def fake_pdf_bridge(task_id, source_file, config):
        return ParseOutput(
            markdown="# Bridge PDF\n\nHello from MinerU\n",
            blocks=[
                {
                    "block_id": "b000001",
                    "type": "title",
                    "text": "Bridge PDF",
                    "markdown": "# Bridge PDF",
                    "page_number": 1,
                    "page_index": 0,
                    "reading_order": 1,
                    "source_ref": {
                        "evidence_id": f"doc:{task_id}:p1:b000001",
                        "source_type": "pdf_parser_bridge",
                        "path": "raw/mineru/content_list.json",
                    },
                }
            ],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            page_count=1,
        )

    monkeypatch.setattr(simple_provider, "_parse_pdf_via_pdf_parser", fake_pdf_bridge)
    output = simple_provider.parse_pdf_document("bridge-task", source, ParseConfig())

    assert output.provider_name == "pdf_parser_bridge"
    assert output.markdown.startswith("# Bridge PDF")
    assert output.blocks[0]["source_ref"]["source_type"] == "pdf_parser_bridge"


def test_pdf_provider_fails_instead_of_falling_back_when_bridge_fails(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    import pytest
    from contracts import ParseConfig, SourceFile

    pdf_path = tmp_path / "bridge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    source = SourceFile(
        path=pdf_path,
        filename="bridge.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )

    def failing_pdf_bridge(task_id, source_file, config):
        raise RuntimeError("MinerU unavailable")

    monkeypatch.setattr(simple_provider, "_parse_pdf_via_pdf_parser", failing_pdf_bridge)

    with pytest.raises(RuntimeError, match="回退到简易文本解析"):
        simple_provider.parse_pdf_document("bridge-task", source, ParseConfig())


def test_pdf_bridge_submit_fields_use_document_market(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    from contracts import ParseConfig, SourceFile

    pdf_path = tmp_path / "bridge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    source = SourceFile(
        path=pdf_path,
        filename="bridge.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )

    monkeypatch.delenv("SIQ_DOCUMENT_PARSE_PDF_BRIDGE_MARKET", raising=False)

    fields = simple_provider._config_to_pdf_parser_submit_fields("doc-bridge-task", ParseConfig(), source)

    assert fields["market"] == "DOC"


def test_pdf_bridge_submit_fields_fall_back_from_invalid_market(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    from contracts import ParseConfig, SourceFile

    pdf_path = tmp_path / "bridge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    source = SourceFile(
        path=pdf_path,
        filename="bridge.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )

    monkeypatch.setenv("SIQ_DOCUMENT_PARSE_PDF_BRIDGE_MARKET", "SG")

    fields = simple_provider._config_to_pdf_parser_submit_fields("doc-bridge-task", ParseConfig(), source)

    assert fields["market"] == "DOC"


def test_pdf_provider_ignores_transient_poll_timeouts(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import mineru_import as mineru_import_module
    import providers.simple as simple_provider
    from contracts import ParseConfig, ParseOutput, SourceFile

    pdf_path = tmp_path / "bridge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    source = SourceFile(
        path=pdf_path,
        filename="bridge.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        file_size=pdf_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    seen_statuses: list[dict[str, object]] = []
    poll_responses = iter(
        [
            {"_error": True, "status": 504, "detail": "timed out"},
            {"status": "completed", "stage": "completed", "task_id": "upstream-1"},
        ]
    )

    monkeypatch.setattr(simple_provider, "_stream_multipart_post", lambda *args, **kwargs: {"task_id": "upstream-1"})
    monkeypatch.setattr(simple_provider, "_json_request", lambda *args, **kwargs: next(poll_responses))
    monkeypatch.setattr(simple_provider, "_result_dir_looks_ready", lambda path: True)
    monkeypatch.setattr(simple_provider.time, "sleep", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(
        mineru_import_module,
        "parse_mineru_output_dir",
        lambda task_id, source_dir, config: (
            source,
            ParseOutput(
                markdown="# Bridge PDF\n",
                blocks=[],
                provider_name="mineru_import",
                document_kind="pdf",
                page_count=1,
                raw_artifacts_dir=str(result_dir),
            ),
        ),
    )
    monkeypatch.setattr(mineru_import_module, "rewrite_image_paths_to_result", lambda output: None)

    output = simple_provider._parse_pdf_via_pdf_parser("bridge-task", source, ParseConfig(), on_status=seen_statuses.append)

    assert output.provider_name == "mineru_import"
    assert output.markdown.startswith("# Bridge PDF")
    assert seen_statuses[0]["status"] == "submitted"
    assert any(status.get("status") == "processing" for status in seen_statuses)
    assert seen_statuses[-1]["status"] == "completed"


def test_pdf_bridge_status_sync_keeps_document_task_running(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    os.environ["SIQ_DOCUMENT_PARSE_DATA_DIR"] = str(tmp_path / "data")
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("document_parser_app_status_test", base / "app.py")
    document_app = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(document_app)

    task_id = "task-a"
    document_app.store.create_task(
        {
            "task_id": task_id,
            "filename": "annual.pdf",
            "document_kind": "pdf",
            "source_type": "upload",
            "source_url": "",
            "status": "failed",
            "stage": "failed",
            "progress_percent": 0,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "application/pdf",
            "error": "old timeout",
            "config": {},
        }
    )

    document_app._sync_pdf_bridge_status(
        task_id,
        {
            "status": "processing",
            "stage": "processing",
            "task_id": f"doc-{task_id}",
            "elapsed_seconds": 270,
            "total_pages": 250,
            "processed_pages": 15,
            "progress_percent": 6,
        },
    )

    status = document_app.store.get_task(task_id)

    assert status["status"] == "running"
    assert status["stage"] == "processing"
    assert status["upstream_task_id"] == f"doc-{task_id}"
    assert status["total_pages"] == 250
    assert status["processed_pages"] == 15
    assert status["progress_percent"] == 6
    assert not status["error"]


def test_failed_pdf_bridge_task_recovers_when_upstream_artifacts_are_ready(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    os.environ["SIQ_DOCUMENT_PARSE_DATA_DIR"] = str(tmp_path / "data")
    os.environ["SIQ_PDF2MD_DATA_DIR"] = str(tmp_path / "pdf-parser")
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("document_parser_app_recovery_test", base / "app.py")
    document_app = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(document_app)

    task_id = "bridge-timeout-task"
    upload_dir = document_app._task_upload_dir(task_id)
    upload_dir.mkdir(parents=True)
    source_pdf = upload_dir / "annual.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
    upstream_dir = document_app._pdf_parser_result_dir(f"doc-{task_id}")
    upstream_dir.mkdir(parents=True)
    (upstream_dir / "annual.pdf").write_bytes(source_pdf.read_bytes())
    (upstream_dir / "result.md").write_text("# Recovered Bridge\n\nRecovered from upstream.\n", encoding="utf-8")
    (upstream_dir / "content_list.json").write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "Recovered Bridge",
                    "text_level": 1,
                    "page_idx": 0,
                    "bbox": [1, 2, 100, 30],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (upstream_dir / "middle.json").write_text(json.dumps({"pdf_info": [{}]}, ensure_ascii=False), encoding="utf-8")
    (upstream_dir / "document_full.json").write_text(
        json.dumps({"task": {"filename": "annual.pdf"}, "source_files": {"pdf": {"path": str(source_pdf)}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    document_app.store.create_task(
        {
            "task_id": task_id,
            "filename": "annual.pdf",
            "document_kind": "pdf",
            "source_type": "upload",
            "source_url": "",
            "status": "failed",
            "stage": "failed",
            "progress_percent": 0,
            "file_size": source_pdf.stat().st_size,
            "file_sha256": "sha",
            "mime_type": "application/pdf",
            "error": "MinerU PDF 解析失败: timed out",
            "config": {},
        }
    )
    document_app.store.add_log(task_id, "解析失败: MinerU PDF 解析失败: timed out", level="error")

    status = document_app.app.test_client().get(f"/api/status/{task_id}")

    assert status.status_code == 200
    assert status.json["status"] == "completed"
    assert status.json["upstream_task_id"] == f"doc-{task_id}"
    assert status.json["parser_provider"] == "mineru_import"
    assert status.json["artifacts_ready"] is True

    result = document_app.app.test_client().get(f"/api/result/{task_id}")
    assert result.status_code == 200
    assert "Recovered Bridge" in result.json["markdown"]
    assert result.json["manifest"]["raw_artifacts"] == "raw/mineru"


def test_image_provider_converts_to_pdf_before_using_pdf_parser_bridge(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    from contracts import ParseConfig, ParseOutput, SourceFile

    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"fake image bytes")
    image_source = SourceFile(
        path=image_path,
        filename="diagram.png",
        mime_type="image/png",
        extension=".png",
        file_size=image_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )
    converted_pdf = tmp_path / "diagram.pdf"
    converted_pdf.write_bytes(b"%PDF-1.4\n%converted\n%%EOF\n")
    seen: dict[str, SourceFile] = {}

    def fake_converter(source_file):
        seen["original"] = source_file
        return SourceFile(
            path=converted_pdf,
            filename="diagram.pdf",
            mime_type="application/pdf",
            extension=".pdf",
            file_size=converted_pdf.stat().st_size,
            sha256="converted",
            source_type="upload_converted_pdf",
        )

    def fake_pdf_bridge(task_id, source_file, config):
        seen["pdf"] = source_file
        return ParseOutput(
            markdown="# Converted Image\n",
            blocks=[
                {
                    "block_id": "b000001",
                    "type": "title",
                    "text": "Converted Image",
                    "markdown": "# Converted Image",
                    "page_number": 1,
                    "page_index": 0,
                    "reading_order": 1,
                    "source_ref": {"evidence_id": f"doc:{task_id}:p1:b000001", "source_type": "pdf_parser_bridge"},
                }
            ],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            page_count=1,
        )

    monkeypatch.setattr(simple_provider, "_convert_image_to_pdf_source", fake_converter)
    monkeypatch.setattr(simple_provider, "_parse_pdf_via_pdf_parser", fake_pdf_bridge)

    output = simple_provider.parse_image_document("image-task", image_source, ParseConfig())

    assert seen["original"].extension == ".png"
    assert seen["pdf"].extension == ".pdf"
    assert output.provider_name == "pdf_parser_bridge:image_to_pdf"
    assert output.document_kind == "image"
    assert output.markdown.startswith("# Converted Image")


def test_spreadsheet_provider_converts_to_pdf_before_using_pdf_parser_bridge(tmp_path, monkeypatch):
    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))

    import providers.simple as simple_provider
    from contracts import ParseConfig, ParseOutput, SourceFile

    xlsx_path = tmp_path / "table.xlsx"
    xlsx_path.write_bytes(b"fake spreadsheet bytes")
    spreadsheet_source = SourceFile(
        path=xlsx_path,
        filename="table.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension=".xlsx",
        file_size=xlsx_path.stat().st_size,
        sha256="stub",
        source_type="upload",
    )
    converted_pdf = tmp_path / "table.pdf"
    converted_pdf.write_bytes(b"%PDF-1.4\n%converted\n%%EOF\n")
    seen: dict[str, SourceFile] = {}

    def fake_converter(source_file):
        seen["original"] = source_file
        return SourceFile(
            path=converted_pdf,
            filename="table.pdf",
            mime_type="application/pdf",
            extension=".pdf",
            file_size=converted_pdf.stat().st_size,
            sha256="converted",
            source_type="upload_converted_pdf",
        )

    def fake_pdf_bridge(task_id, source_file, config):
        seen["pdf"] = source_file
        return ParseOutput(
            markdown="# Converted Spreadsheet\n",
            blocks=[
                {
                    "block_id": "b000001",
                    "type": "title",
                    "text": "Converted Spreadsheet",
                    "markdown": "# Converted Spreadsheet",
                    "page_number": 1,
                    "page_index": 0,
                    "reading_order": 1,
                    "source_ref": {"evidence_id": f"doc:{task_id}:p1:b000001", "source_type": "pdf_parser_bridge"},
                }
            ],
            provider_name="pdf_parser_bridge",
            document_kind="pdf",
            page_count=1,
        )

    monkeypatch.setattr(simple_provider, "_convert_office_to_pdf_source", fake_converter)
    monkeypatch.setattr(simple_provider, "_parse_pdf_via_pdf_parser", fake_pdf_bridge)

    output = simple_provider.parse_spreadsheet_document("spreadsheet-task", spreadsheet_source, ParseConfig())

    assert seen["original"].extension == ".xlsx"
    assert seen["pdf"].extension == ".pdf"
    assert output.provider_name == "pdf_parser_bridge:excel_to_pdf"
    assert output.document_kind == "excel"
    assert output.markdown.startswith("# Converted Spreadsheet")


def test_batch_download_includes_completed_task_packages(tmp_path):
    client = load_app(tmp_path)
    first = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"# A\n\nhello\n"), "a.md")},
        content_type="multipart/form-data",
    ).json["tasks"][0]["task_id"]
    second = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"# B\n\nworld\n"), "b.md")},
        content_type="multipart/form-data",
    ).json["tasks"][0]["task_id"]
    wait_for_terminal(client, first)
    wait_for_terminal(client, second)

    response = client.post("/api/download/batch", json={"task_ids": [first, second, "missing-task"]})

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert "batch_manifest.json" in names
        manifest = json.loads(archive.read("batch_manifest.json").decode("utf-8"))
        assert manifest["task_count"] == 2
        assert "missing-task" in manifest["missing"]
        assert any(name.startswith(f"{first}/") and name.endswith(".zip") for name in names)
        assert any(name.startswith(f"{second}/") and name.endswith(".zip") for name in names)


def test_import_mineru_output_dir_normalizes_artifacts_and_raw_archive(tmp_path):
    client = load_app(tmp_path)
    source_dir = tmp_path / "data" / "legacy-mineru" / "case-a"
    images_dir = source_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (source_dir / "result.md").write_text("# Imported Case\n\n![Chart](images/chart.png)\n", encoding="utf-8")
    content_items = [
        {"type": "text", "text": "Imported title", "text_level": 1, "page_idx": 0, "bbox": [1, 2, 100, 30]},
        {
            "type": "table",
            "table_caption": ["Key metrics"],
            "table_body": "<table><tr><th>Metric</th><th>Value</th></tr><tr><td>Revenue</td><td>42</td></tr></table>",
            "page_idx": 0,
            "bbox": [4, 40, 200, 90],
        },
        {
            "type": "image",
            "img_path": "images/chart.png",
            "image_caption": ["Revenue chart"],
            "page_idx": 1,
            "bbox": [10, 20, 300, 240],
        },
    ]
    (source_dir / "content_list.json").write_text(json.dumps(json.dumps(content_items), ensure_ascii=False), encoding="utf-8")
    (source_dir / "middle.json").write_text(
        json.dumps(json.dumps({"pdf_info": [{"page_size": [842, 595]}, {"page_size": [595, 842]}]}), ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.post(
        "/api/import/mineru",
        json={"source_dir": str(source_dir), "task_id": "import-case-a", "language": "zh"},
    )

    assert response.status_code == 200
    task = response.json["task"]
    assert task["task_id"] == "import-case-a"
    assert task["status"] == "completed"
    assert task["parser_provider"] == "mineru_import"

    result = client.get("/api/result/import-case-a")
    assert result.status_code == 200
    assert result.json["manifest"]["raw_artifacts"] == "raw/mineru"
    assert "images/original/chart.png" in result.json["markdown"]

    blocks = client.get("/api/artifact/import-case-a/blocks.json")
    assert blocks.status_code == 200
    assert len(blocks.json["blocks"]) == 3
    assert blocks.json["blocks"][0]["source_ref"]["path"] == "raw/mineru/content_list.json"
    assert blocks.json["blocks"][0]["bbox_unit"] == "normalized_1000"

    layout = client.get("/api/artifact/import-case-a/layout_blocks.json")
    assert layout.status_code == 200
    assert layout.json["pages"][0]["width"] == 842
    assert layout.json["pages"][0]["height"] == 595
    assert layout.json["pages"][1]["width"] == 595
    assert layout.json["pages"][1]["height"] == 842

    tables = client.get("/api/artifact/import-case-a/tables.json")
    assert tables.status_code == 200
    table = tables.json["physical_tables"][0]
    assert table["caption"] == "Key metrics"
    assert table["bbox_unit"] == "normalized_1000"
    assert table["quality"]["row_count"] == 2
    assert table["cells"][2]["text"] == "Revenue"

    figures = client.get("/api/figures/import-case-a")
    assert figures.status_code == 200
    assert set(figures.json) == {"schema_version", "task_id", "figures"}
    assert figures.json["schema_version"] == "document_figures_v1"
    assert figures.json["task_id"] == "import-case-a"
    figure = figures.json["figures"][0]
    assert figure["image_id"] == "img-000001"
    assert figure["image_path"] == "images/original/chart.png"
    assert figure["bbox"] == [10.0, 20.0, 300.0, 240.0]

    missing_figures = client.get("/api/figures/missing-task")
    assert missing_figures.status_code == 404
    assert missing_figures.json == {"error": "not_found"}

    image = client.get("/api/artifact/import-case-a/images/original/chart.png")
    assert image.status_code == 200
    raw_content = client.get("/api/artifact/import-case-a/raw/mineru/content_list.json")
    assert raw_content.status_code == 200

    source_image = client.get("/api/source/import-case-a/image/img-000001")
    assert source_image.status_code == 200
    assert source_image.json["crop_url"] == "/api/artifact/import-case-a/images/original/chart.png"

    source_page = client.get("/api/source/import-case-a/page/2")
    assert source_page.status_code == 200
    assert source_page.json["page"]["page_size"] == [595, 842]
    assert source_page.json["page"]["bbox_unit"] == "pdf_point"

    source_block = client.get("/api/source/import-case-a/block/b000001")
    assert source_block.status_code == 200
    assert source_block.json["task_id"] == "import-case-a"
    assert source_block.json["block"]["block_id"] == "b000001"
    assert source_block.json["block"]["source_ref"]["path"] == "raw/mineru/content_list.json"

    missing_block = client.get("/api/source/import-case-a/block/missing-block")
    assert missing_block.status_code == 404
    assert missing_block.json == {"error": "not_found"}

    source_table = client.get(f"/api/source/import-case-a/table/{table['table_id']}")
    assert source_table.status_code == 200
    assert source_table.json["task_id"] == "import-case-a"
    assert source_table.json["table"]["table_id"] == table["table_id"]
    assert source_table.json["table"]["caption"] == "Key metrics"

    missing_table = client.get("/api/source/import-case-a/table/missing-table")
    assert missing_table.status_code == 404
    assert missing_table.json == {"error": "not_found"}

    candidates = client.get("/api/import/mineru/candidates?limit=5")
    assert candidates.status_code == 200
    assert any(item["source_dir"] == str(source_dir) for item in candidates.json["candidates"])

    package = client.get("/api/download/import-case-a")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        names = set(archive.namelist())
        assert "raw/mineru/content_list.json" in names
        assert "images/original/chart.png" in names


def test_table_relations_endpoint_refreshes_stale_ruleset(tmp_path):
    base = Path(__file__).resolve().parents[1]
    os.environ["SIQ_DOCUMENT_PARSE_DATA_DIR"] = str(tmp_path / "data")
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("document_parser_app_table_relations_test", base / "app.py")
    document_app = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(document_app)

    task_id = "stale-relations-task"
    document_app.store.create_task(
        {
            "task_id": task_id,
            "filename": "stale-relations.md",
            "document_kind": "text",
            "source_type": "upload",
            "source_url": "",
            "status": "completed",
            "stage": "completed",
            "progress_percent": 100,
            "file_size": 1,
            "file_sha256": "sha",
            "mime_type": "text/markdown",
            "config": {},
        }
    )
    result_dir = document_app._task_result_dir(task_id)
    result_dir.mkdir(parents=True)
    (result_dir / "exports").mkdir(parents=True)
    tables = [
        {
            "table_id": "pt-1",
            "page_number": 1,
            "bbox": [100, 720, 900, 920],
            "markdown": "| A | B | C |\n| --- | --- | --- |\n| row 1 | x | y |",
            "quality": {"row_count": 3, "column_count": 3},
            "cells": [],
        },
        {
            "table_id": "pt-2",
            "page_number": 2,
            "bbox": [100, 110, 900, 340],
            "markdown": "| A | B | C |\n| --- | --- | --- |\n| row 2 | x | y |",
            "quality": {"row_count": 3, "column_count": 3},
            "cells": [],
        },
    ]
    document_app.write_json(result_dir / "tables.json", {"schema_version": "document_tables_v1", "task_id": task_id, "physical_tables": tables})
    document_app.write_json(
        result_dir / "blocks.json",
        {
            "schema_version": "document_blocks_v1",
            "task_id": task_id,
            "blocks": [
                {"block_id": "t1", "type": "table", "page_number": 1, "bbox": [100, 720, 900, 920], "text": ""},
                {"block_id": "t2", "type": "table", "page_number": 2, "bbox": [100, 110, 900, 340], "text": ""},
            ],
        },
    )
    (result_dir / "document.md").write_text(
        "<table><tr><td>A</td><td>B</td><td>C</td></tr><tr><td>row 1</td><td>x</td><td>y</td></tr><tr><td>row 2</td><td>x</td><td>y</td></tr></table>\n",
        encoding="utf-8",
    )
    document_app.write_json(
        result_dir / "table_relations.json",
        {
            "schema_version": "document_table_relations_v1",
            "task_id": task_id,
            "relations": [
                {
                    "relation_id": "rel-old",
                    "from_table_id": "old-a",
                    "to_table_id": "old-b",
                    "relation_type": "continuation",
                    "merge_status": "auto_merged",
                }
            ],
        },
    )
    document_app.write_json(result_dir / "logical_tables.json", {"schema_version": "document_logical_tables_v1", "task_id": task_id, "logical_tables": []})

    response = document_app.app.test_client().get(f"/api/table-relations/{task_id}")

    assert response.status_code == 200
    assert response.json["ruleset_version"] == document_app.TABLE_RELATION_RULESET_VERSION
    assert [(item["from_table_id"], item["to_table_id"]) for item in response.json["relations"]] == [("pt-1", "pt-2")]
    stored = document_app.read_json(result_dir / "table_relations.json")
    assert stored["ruleset_version"] == document_app.TABLE_RELATION_RULESET_VERSION
    assert (result_dir / "exports" / "full.zip").exists()


def test_rule_based_schema_extraction_with_evidence_and_cache(tmp_path):
    client = load_app(tmp_path)
    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"party_a: Alice\nparty_b: Bob\namount: 100 USD\n"), "contract.md")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    wait_for_terminal(client, task_id)

    extract = client.post(
        f"/api/extract/{task_id}",
        json={
            "schema": {
                "type": "object",
                "properties": {
                    "party_a": {"type": "string"},
                    "party_b": {"type": "string"},
                },
            }
        },
    )

    assert extract.status_code == 200
    assert extract.json["result"] == {"party_a": "Alice", "party_b": "Bob"}
    assert extract.json["evidence_map"]["party_a"][0]["block_id"]
    assert extract.json["validation_report"]["evidence_coverage_ratio"] == 1.0

    cached = client.post(
        f"/api/extract/{task_id}",
        json={
            "schema": {
                "type": "object",
                "properties": {
                    "party_a": {"type": "string"},
                    "party_b": {"type": "string"},
                },
            }
        },
    )
    assert cached.status_code == 200
    assert cached.json["extract_id"] == extract.json["extract_id"]
    assert cached.json["cached"] is True

    result_artifact = client.get(f"/api/artifact/{task_id}/extraction/result.json")
    assert result_artifact.status_code == 200
    assert result_artifact.json["result"]["party_a"] == "Alice"


def test_template_extraction_lists_templates_and_keeps_missing_fields_null(tmp_path):
    client = load_app(tmp_path)
    templates = client.get("/api/extraction/templates")
    assert templates.status_code == 200
    template_ids = {item["template_id"] for item in templates.json["templates"]}
    assert "contract_terms_v1" in template_ids

    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO("甲方: 上海甲公司\n乙方: 北京乙公司\n合同金额: 42万元\n".encode("utf-8")), "contract.md")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    wait_for_terminal(client, task_id)

    extract = client.post(f"/api/extract/{task_id}", json={"template_id": "contract_terms_v1"})
    assert extract.status_code == 200
    assert extract.json["template_id"] == "contract_terms_v1"
    assert extract.json["result"]["party_a"] == "上海甲公司"
    assert extract.json["result"]["party_b"] == "北京乙公司"
    assert extract.json["result"]["amount"] == "42万元"
    assert extract.json["result"]["term"] is None
    assert extract.json["validation_report"]["missing_fields"]
