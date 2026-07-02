import json
from pathlib import Path
import sys
import types

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            self.config = {}

        def route(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def before_request(self, func=None):
            def decorator(func):
                return func

            return decorator if func is None else func

        def errorhandler(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator


    sys.modules.setdefault(
        "flask",
        types.SimpleNamespace(
            Flask=_DummyFlask,
            jsonify=lambda *args, **kwargs: None,
            make_response=lambda value: types.SimpleNamespace(
                value=value,
                headers={},
                set_cookie=lambda *args, **kwargs: None,
            ),
            render_template=lambda *args, **kwargs: "",
            request=types.SimpleNamespace(
                args={},
                files={},
                form={},
                headers={},
                cookies={},
                get_json=lambda silent=True: {},
            ),
            send_file=lambda *args, **kwargs: None,
        ),
    )

import app


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_ensure_document_full_existing_file_only_backfills_standalone_enhanced(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    task = {"task_id": "existing-doc-full", "filename": "report.pdf"}
    result_dir = tmp_path / task["task_id"]
    document_full_path = result_dir / "document_full.json"
    _write_json(document_full_path, {"schema_version": "document_full_v1", "sentinel": "keep"})
    calls = []

    def fake_ensure_content_list_enhanced(task_arg, markdown):
        calls.append(("enhanced", task_arg["task_id"], markdown))
        return {"schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION}

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("existing document_full.json must not trigger full rebuild")

    monkeypatch.setattr(app, "_ensure_content_list_enhanced_artifact", fake_ensure_content_list_enhanced)
    monkeypatch.setattr(app, "_ensure_financial_artifacts", fail_if_called)
    monkeypatch.setattr(app, "_write_document_full_artifact", fail_if_called)
    monkeypatch.setattr(app, "_build_quality_report", fail_if_called)

    returned = app._ensure_document_full_artifact(task, "markdown")

    assert returned == str(document_full_path)
    assert calls == [("enhanced", "existing-doc-full", "markdown")]
    assert json.loads(document_full_path.read_text(encoding="utf-8"))["sentinel"] == "keep"


def test_ensure_content_list_enhanced_current_artifact_is_reused_without_side_effects(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    task = {"task_id": "fresh-enhanced", "filename": "report.pdf"}
    result_dir = tmp_path / task["task_id"]
    enhanced_path = result_dir / "content_list_enhanced.json"
    existing = {
        "schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
        "sentinel": "fresh",
    }
    _write_json(enhanced_path, existing)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("fresh content_list_enhanced.json must be reused without writes")

    monkeypatch.setattr(app, "_build_content_list_enhanced", fail_if_called)
    monkeypatch.setattr(app, "_write_json", fail_if_called)
    monkeypatch.setattr(app, "_write_complete_markdown_artifact", fail_if_called)
    monkeypatch.setattr(app, "_ensure_table_relations_artifact", fail_if_called)
    monkeypatch.setattr(app, "_ensure_financial_artifacts", fail_if_called)

    returned = app._ensure_content_list_enhanced_artifact(task, "markdown")

    assert returned == existing


def test_ensure_content_list_enhanced_reuses_embedded_document_full_payload(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    monkeypatch.setattr(app, "_now_iso", lambda: "2026-07-02T00:00:00Z")
    task = {"task_id": "embedded-enhanced", "filename": "report.pdf"}
    result_dir = tmp_path / task["task_id"]
    embedded = {
        "schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
        "tables": [],
        "sentinel": "embedded",
    }
    document_full = {"content_list_enhanced": embedded, "artifacts": {}}
    _write_json(result_dir / "document_full.json", document_full)
    writes = []
    complete_markdown_calls = []
    table_relations = {"schema_version": "document_table_relations_v1", "relations": []}

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("embedded current content_list_enhanced should be reused")

    def record_write_json(path, payload):
        writes.append((Path(path).name, payload.copy()))
        _write_json(Path(path), payload)

    def fake_apply_update_to_document_full(
        document_full_arg,
        *,
        task_id,
        enhanced,
        table_relations,
        content_list_enhanced_path,
        table_relations_path,
        complete_markdown_path,
        complete_markdown_exists,
    ):
        updated = document_full_arg.copy()
        updated["updated_with"] = {
            "task_id": task_id,
            "enhanced_sentinel": enhanced["sentinel"],
            "table_relations": table_relations,
            "content_list_enhanced_path": Path(content_list_enhanced_path).name,
            "table_relations_path": Path(table_relations_path).name,
            "complete_markdown_path": Path(complete_markdown_path).name,
            "complete_markdown_exists": complete_markdown_exists,
        }
        return updated

    monkeypatch.setattr(app, "_build_content_list_enhanced", fail_if_called)
    monkeypatch.setattr(app, "_write_json", record_write_json)
    monkeypatch.setattr(app, "_load_corrections", lambda _task: {})
    monkeypatch.setattr(
        app,
        "_write_complete_markdown_artifact",
        lambda task_arg, markdown, enhanced, corrections=None: complete_markdown_calls.append(
            (task_arg["task_id"], enhanced["sentinel"], corrections)
        ),
    )
    monkeypatch.setattr(
        app,
        "_ensure_table_relations_artifact",
        lambda task_arg, markdown, enhanced=None, content_list=None: table_relations,
    )
    monkeypatch.setattr(
        app.document_full_service,
        "apply_content_list_enhanced_update_to_document_full",
        fake_apply_update_to_document_full,
    )

    enhanced = app._ensure_content_list_enhanced_artifact(task, "markdown")

    assert enhanced["sentinel"] == "embedded"
    assert enhanced["task_id"] == "embedded-enhanced"
    assert enhanced["filename"] == "report.pdf"
    assert enhanced["generated_at"] == "2026-07-02T00:00:00Z"
    assert complete_markdown_calls == [("embedded-enhanced", "embedded", {})]
    assert [name for name, _payload in writes] == [
        "content_list_enhanced.json",
        "document_full.json",
    ]
    updated_document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    assert updated_document_full["updated_with"] == {
        "task_id": "embedded-enhanced",
        "enhanced_sentinel": "embedded",
        "table_relations": table_relations,
        "content_list_enhanced_path": "content_list_enhanced.json",
        "table_relations_path": "table_relations.json",
        "complete_markdown_path": "result_complete.md",
        "complete_markdown_exists": False,
    }


def test_ensure_content_list_enhanced_stale_standalone_bootstraps_document_full(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    monkeypatch.setattr(app, "_now_iso", lambda: "2026-07-02T00:00:00Z")
    task = {"task_id": "stale-enhanced", "filename": "report.pdf"}
    result_dir = tmp_path / task["task_id"]
    _write_json(result_dir / "content_list_enhanced.json", {"schema_version": 1})
    _write_json(result_dir / "content_list.json", [])
    writes = []
    complete_markdown_calls = []
    table_relation_calls = []
    document_full_calls = []

    monkeypatch.setattr(
        app,
        "_build_content_list_enhanced",
        lambda markdown, content_list=None, report_year=None: {
            "schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
            "tables": [],
        },
    )
    monkeypatch.setattr(app, "_detect_report_year", lambda *_args, **_kwargs: 2026)
    monkeypatch.setattr(app, "_load_corrections", lambda _task: {"tables": {}})
    monkeypatch.setattr(
        app,
        "_write_complete_markdown_artifact",
        lambda task_arg, markdown, enhanced, corrections=None: complete_markdown_calls.append(
            (task_arg["task_id"], corrections)
        ),
    )
    monkeypatch.setattr(
        app,
        "_ensure_table_relations_artifact",
        lambda task_arg, markdown, enhanced=None, content_list=None: table_relation_calls.append(
            (task_arg["task_id"], enhanced.get("schema_version"), content_list)
        )
        or {"schema_version": "document_table_relations_v1"},
    )
    monkeypatch.setattr(
        app,
        "_ensure_financial_artifacts",
        lambda task_arg, markdown: (
            {"summary": {"statement_count": 0, "key_metric_count": 0}},
            {"summary": {}, "overall_status": "ok"},
        ),
    )
    monkeypatch.setattr(app, "_read_quality_report", lambda _task: None)
    monkeypatch.setattr(app, "_build_quality_report", lambda *_args, **_kwargs: {"warnings": []})

    def fake_write_document_full_artifact(
        task_arg,
        markdown,
        enhanced,
        report,
        *,
        financial_data=None,
        financial_checks=None,
        table_relations=None,
    ):
        document_full_calls.append(
            {
                "task_id": task_arg["task_id"],
                "enhanced_schema_version": enhanced["schema_version"],
                "financial_data": financial_data,
                "financial_checks": financial_checks,
            }
        )
        return str(result_dir / "document_full.json")

    def record_write_json(path, payload):
        writes.append((Path(path).name, payload.copy()))
        _write_json(Path(path), payload)

    monkeypatch.setattr(app, "_write_document_full_artifact", fake_write_document_full_artifact)
    monkeypatch.setattr(app, "_write_json", record_write_json)

    enhanced = app._ensure_content_list_enhanced_artifact(task, "markdown")

    assert enhanced["schema_version"] == app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION
    assert enhanced["task_id"] == "stale-enhanced"
    assert enhanced["filename"] == "report.pdf"
    assert enhanced["generated_at"] == "2026-07-02T00:00:00Z"
    assert writes[0][0] == "content_list_enhanced.json"
    assert complete_markdown_calls == [("stale-enhanced", {"tables": {}})]
    assert table_relation_calls == [
        ("stale-enhanced", app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION, [])
    ]
    assert document_full_calls == [
        {
            "task_id": "stale-enhanced",
            "enhanced_schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION,
            "financial_data": {"summary": {"statement_count": 0, "key_metric_count": 0}},
            "financial_checks": {"summary": {}, "overall_status": "ok"},
        }
    ]


def test_ensure_document_full_missing_file_builds_prerequisites_in_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    task = {"task_id": "missing-doc-full", "filename": "report.pdf"}
    calls = []
    writer_calls = []
    enhanced = {"schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION}
    financial_data = {"summary": {"statement_count": 1, "key_metric_count": 2}}
    financial_checks = {"summary": {"ok": True}, "overall_status": "ok"}
    quality_report = {"schema_version": app.QUALITY_SCHEMA_VERSION, "warnings": []}

    def fake_ensure_content_list_enhanced(task_arg, markdown):
        calls.append(("enhanced", task_arg["task_id"], markdown))
        return enhanced

    def fake_ensure_financial(task_arg, markdown):
        calls.append(("financial", task_arg["task_id"], markdown))
        return financial_data, financial_checks

    def fake_read_quality(task_arg):
        calls.append(("read_quality", task_arg["task_id"]))
        return None

    def fake_build_quality(markdown, task_arg, **kwargs):
        calls.append(("build_quality", task_arg["task_id"], markdown, kwargs))
        return quality_report

    def fake_write_document_full(
        task_arg,
        markdown,
        enhanced_arg,
        report_arg,
        *,
        financial_data=None,
        financial_checks=None,
        table_relations=None,
    ):
        calls.append(("write_document_full", task_arg["task_id"]))
        writer_calls.append(
            {
                "task_id": task_arg["task_id"],
                "markdown": markdown,
                "enhanced": enhanced_arg,
                "quality_report": report_arg,
                "financial_data": financial_data,
                "financial_checks": financial_checks,
            }
        )
        return str(tmp_path / task_arg["task_id"] / "document_full.json")

    monkeypatch.setattr(app, "_ensure_content_list_enhanced_artifact", fake_ensure_content_list_enhanced)
    monkeypatch.setattr(app, "_ensure_financial_artifacts", fake_ensure_financial)
    monkeypatch.setattr(app, "_read_quality_report", fake_read_quality)
    monkeypatch.setattr(app, "_build_quality_report", fake_build_quality)
    monkeypatch.setattr(app, "_load_json_artifact", lambda _task, _filename: [])
    monkeypatch.setattr(app, "_write_document_full_artifact", fake_write_document_full)

    returned = app._ensure_document_full_artifact(task, "markdown")

    assert returned == str(tmp_path / "missing-doc-full" / "document_full.json")
    assert ("enhanced", "missing-doc-full", "markdown") in calls
    assert ("financial", "missing-doc-full", "markdown") in calls
    assert ("read_quality", "missing-doc-full") in calls
    assert (
        "build_quality",
        "missing-doc-full",
        "markdown",
        {"file_name": "report.pdf", "content_list": []},
    ) in calls
    assert calls.count(("write_document_full", "missing-doc-full")) == 1
    assert writer_calls == [
        {
            "task_id": "missing-doc-full",
            "markdown": "markdown",
            "enhanced": enhanced,
            "quality_report": quality_report,
            "financial_data": financial_data,
            "financial_checks": financial_checks,
        }
    ]


def test_write_document_full_artifact_ensures_table_relations_when_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    monkeypatch.setattr(app, "_now_iso", lambda: "2026-07-02T00:00:00Z")
    task = {"task_id": "write-doc-full", "filename": "report.pdf"}
    enhanced = {"schema_version": app.CONTENT_LIST_ENHANCED_SCHEMA_VERSION, "tables": []}
    content_list = [{"type": "text", "text": "hello"}]
    table_relations = {"schema_version": "document_table_relations_v1", "relations": []}
    calls = []

    def fake_ensure_table_relations(task_arg, markdown, *, enhanced=None, content_list=None):
        calls.append((task_arg["task_id"], markdown, enhanced, content_list))
        return table_relations

    monkeypatch.setattr(app, "_load_json_artifact", lambda _task, filename: content_list if filename == "content_list.json" else None)
    monkeypatch.setattr(app, "_ensure_table_relations_artifact", fake_ensure_table_relations)
    monkeypatch.setattr(
        app,
        "_build_document_full_json",
        lambda task_arg, markdown, enhanced_arg, quality_report, **kwargs: {
            "task_id": task_arg["task_id"],
            "table_relations": kwargs["table_relations"],
            "financial_data": kwargs["financial_data"],
            "financial_checks": kwargs["financial_checks"],
            "artifacts": {},
        },
    )

    path = app._write_document_full_artifact(
        task,
        "markdown",
        enhanced,
        {"warnings": []},
        financial_data={"summary": {}},
        financial_checks={"overall_status": "ok"},
    )

    assert calls == [("write-doc-full", "markdown", enhanced, content_list)]
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["table_relations"] == table_relations
    assert payload["artifacts"]["document_full.json"]["exists"] is True
    assert payload["artifacts"]["table_relations.json"]["exists"] is True


def test_ensure_table_relations_artifact_reuses_current_ruleset(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(tmp_path))
    task = {"task_id": "current-relations", "filename": "report.pdf"}
    current = {
        "schema_version": "document_table_relations_v1",
        "ruleset_version": app.TABLE_RELATION_RULESET_VERSION,
        "relations": [{"from_table_id": "pt-1", "to_table_id": "pt-2"}],
    }
    _write_json(tmp_path / task["task_id"] / "table_relations.json", current)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("current table_relations.json must be reused")

    monkeypatch.setattr(app, "_write_table_relations_artifact", fail_if_called)

    returned = app._ensure_table_relations_artifact(task, "markdown")

    assert returned == current
