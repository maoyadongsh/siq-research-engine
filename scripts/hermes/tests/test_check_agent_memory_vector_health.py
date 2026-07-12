import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_agent_memory_vector_health.py"
    spec = importlib.util.spec_from_file_location("check_agent_memory_vector_health_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_vector_health_optional_passes_without_pymilvus(monkeypatch, tmp_path, capsys):
    module = _load_module()
    output = tmp_path / "health.json"
    markdown = tmp_path / "health.md"
    monkeypatch.setattr(module, "_module_available", lambda _name: False)
    monkeypatch.setenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL", "http://embedding.internal/v1?api_key=secret")

    exit_code = module.main(["--output", str(output), "--markdown", str(markdown)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    combined = capsys.readouterr().out + output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["embedding"]["endpoint_configured"] is True
    assert payload["milvus"]["pymilvus_available"] is False
    assert "embedding.internal" not in combined
    assert "api_key" not in combined
    assert "secret" not in combined


def test_vector_health_required_fails_without_pymilvus(monkeypatch, tmp_path):
    module = _load_module()
    output = tmp_path / "health.json"
    markdown = tmp_path / "health.md"
    monkeypatch.setattr(module, "_module_available", lambda _name: False)

    exit_code = module.main(["--require-milvus", "--output", str(output), "--markdown", str(markdown)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["failures"] == ["pymilvus is not installed"]


def test_vector_health_checks_collection_schema_without_leaking_connection(monkeypatch, tmp_path, capsys):
    module = _load_module()
    output = tmp_path / "health.json"
    markdown = tmp_path / "health.md"

    class FakeClient:
        @staticmethod
        def has_collection(name):
            return name == "siq_agent_memory_perf"

    monkeypatch.setattr(module, "_module_available", lambda _name: True)
    monkeypatch.setattr(module.agent_memory_milvus, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        module.agent_memory_milvus,
        "_schema_field_names",
        lambda _client, _name: set(module.agent_memory_milvus.REQUIRED_FIELDS),
    )
    monkeypatch.setenv("SIQ_MILVUS_HOST", "milvus.internal")
    monkeypatch.setenv("SIQ_MILVUS_TOKEN", "secret-token")

    exit_code = module.main(
        [
            "--collection",
            "siq_agent_memory_perf",
            "--require-milvus",
            "--require-collection",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    combined = capsys.readouterr().out + output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["milvus"]["connectivity"]["passed"] is True
    assert payload["milvus"]["collection"]["exists"] is True
    assert payload["milvus"]["collection"]["name"] == "siq_agent_memory_perf"
    assert payload["milvus"]["collection"]["required_fields_present"] is True
    assert payload["milvus"]["collection"]["missing_required_fields"] == []
    assert "milvus.internal" not in combined
    assert "secret-token" not in combined
