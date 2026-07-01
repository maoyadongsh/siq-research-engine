import json

import pytest

import pdf_parser_financial_service as financial


def test_financial_artifact_paths_use_result_dir(tmp_path):
    task = {"task_id": "task-fin"}

    def result_dir(value):
        return str(tmp_path / value["task_id"])

    assert financial.financial_data_path(task, result_dir) == str(tmp_path / "task-fin" / "financial_data.json")
    assert financial.financial_checks_path(task, result_dir) == str(tmp_path / "task-fin" / "financial_checks.json")


def test_read_financial_artifacts_and_current_round_trip(tmp_path):
    task = {"task_id": "task-fin"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"statement_count": 1},
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "summary": {"pass": 1},
    }
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    checks_path.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")

    loaded_data, loaded_checks = financial.read_financial_artifacts(task, result_dir)

    assert loaded_data == data
    assert loaded_checks == checks
    assert financial.financial_artifacts_are_current(loaded_data, loaded_checks)


@pytest.mark.parametrize("existing_artifact", ["financial_data.json", "financial_checks.json"])
def test_read_financial_artifacts_ignores_single_sided_artifact(tmp_path, existing_artifact):
    task = {"task_id": "task-fin"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    artifact_path = tmp_path / "task-fin" / existing_artifact
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(json.dumps({"schema_version": 1}, ensure_ascii=False), encoding="utf-8")

    assert financial.read_financial_artifacts(task, result_dir) == (None, None)


@pytest.mark.parametrize(
    ("data_patch", "checks_patch"),
    [
        ({"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION - 1}, {}),
        ({}, {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION - 1}),
        ({"rule_version": "stale-data-rules"}, {}),
        ({}, {"rule_version": "stale-check-rules"}),
    ],
)
def test_financial_artifacts_are_current_rejects_single_sided_schema_or_rule_mismatch(data_patch, checks_patch):
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
    }
    data.update(data_patch)
    checks.update(checks_patch)

    assert not financial.financial_artifacts_are_current(data, checks)


def test_ensure_financial_artifacts_writes_when_stale(tmp_path):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    writes = {}

    def write_json(path, payload):
        writes[path] = payload

    def build_data(markdown, task_id=None, filename=None, llm_cache_dir=None):
        return {
            "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "markdown": markdown,
            "task_id": task_id,
            "filename": filename,
            "llm_cache_dir": llm_cache_dir,
        }

    def build_checks(data):
        return {
            "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": financial.FINANCIAL_RULE_VERSION,
            "source_task_id": data["task_id"],
        }

    financial_data, financial_checks = financial.write_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
        build_data=build_data,
        build_checks=build_checks,
    )

    assert financial_data["task_id"] == "task-fin"
    assert financial_data["filename"] == "report.pdf"
    assert financial_data["llm_cache_dir"].endswith("cache/task-fin")
    assert financial_checks["source_task_id"] == "task-fin"
    assert any(path.endswith("financial_data.json") for path in writes)
    assert any(path.endswith("financial_checks.json") for path in writes)


def test_ensure_financial_artifacts_rewrites_when_checks_schema_is_stale(tmp_path, monkeypatch):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-data",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    checks_path.write_text(
        json.dumps(
            {
                "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION - 1,
                "rule_version": financial.FINANCIAL_RULE_VERSION,
                "sentinel": "old-checks",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    writes = []

    def write_json(path, payload):
        writes.append((path, payload))

    def fake_write_financial_artifacts(
        task,
        markdown,
        *,
        result_dir,
        write_json,
        financial_llm_cache_folder,
        file_name=None,
    ):
        writes.append(
            {
                "task": task,
                "markdown": markdown,
                "result_dir": result_dir,
                "write_json": write_json,
                "financial_llm_cache_folder": financial_llm_cache_folder,
                "file_name": file_name,
            }
        )
        return (
            {"schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION, "sentinel": "new-data"},
            {"schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION, "sentinel": "new-checks"},
        )

    monkeypatch.setattr(financial, "write_financial_artifacts", fake_write_financial_artifacts)

    financial_data, financial_checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert financial_data["sentinel"] == "new-data"
    assert financial_checks["sentinel"] == "new-checks"
    assert len(writes) == 1
    assert writes[0]["task"] == task
    assert writes[0]["markdown"] == "markdown"


def test_ensure_financial_artifacts_reuses_current_files(tmp_path):
    task = {"task_id": "task-fin", "filename": "report.pdf"}
    result_dir = lambda value: str(tmp_path / value["task_id"])
    data_path = tmp_path / "task-fin" / "financial_data.json"
    checks_path = tmp_path / "task-fin" / "financial_checks.json"
    data_path.parent.mkdir(parents=True)
    data = {
        "schema_version": financial.FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "sentinel": "data",
    }
    checks = {
        "schema_version": financial.FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": financial.FINANCIAL_RULE_VERSION,
        "sentinel": "checks",
    }
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    checks_path.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")

    called = []

    def write_json(path, payload):
        called.append((path, payload))

    loaded_data, loaded_checks = financial.ensure_financial_artifacts(
        task,
        "markdown",
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=str(tmp_path / "cache"),
    )

    assert loaded_data == data
    assert loaded_checks == checks
    assert called == []
