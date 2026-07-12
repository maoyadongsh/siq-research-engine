import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "ingest_agent_memory_to_milvus.py"
    spec = importlib.util.spec_from_file_location("ingest_agent_memory_to_milvus_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_profile_fixture(tmp_path: Path) -> tuple[Path, Path]:
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / "siq_assistant"
    profile_dir.mkdir(parents=True)
    (profile_dir / "README.md").write_text("SIQ assistant evidence retrieval policy.\n", encoding="utf-8")
    manifest = profiles_root / "manifest.json"
    manifest.write_text(json.dumps({"profiles": ["siq_assistant"]}), encoding="utf-8")
    return profiles_root, manifest


def test_agent_memory_milvus_seed_summary_redacts_embedding_endpoint(monkeypatch, tmp_path, capsys):
    module = _load_module()
    profiles_root, manifest = _write_profile_fixture(tmp_path)
    output = tmp_path / "seed.json"
    markdown = tmp_path / "seed.md"

    monkeypatch.setattr(module, "embed_batch", lambda texts, **_kwargs: [[0.1, 0.2, 0.3] for _text in texts])
    monkeypatch.setattr(module.agent_memory_milvus, "upsert_records", lambda records, *, flush=False: len(records))
    monkeypatch.setattr(module.agent_memory_milvus, "flush_collection", lambda: None)

    exit_code = module.main(
        [
            "--profiles-root",
            str(profiles_root),
            "--manifest",
            str(manifest),
            "--collection",
            "siq_agent_memory_perf",
            "--embed-url",
            "http://embedding.internal/v1?api_key=secret",
            "--embed-model",
            "fake-embedding-model",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--flush",
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    combined_output = capsys.readouterr().out + output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["collection"] == "siq_agent_memory_perf"
    assert payload["chunk_count"] == 1
    assert payload["inserted"] == 1
    assert payload["embedding_endpoint_configured"] is True
    assert payload["embed_model"] == "fake-embedding-model"
    assert "embedding.internal" not in combined_output
    assert "api_key" not in combined_output
    assert "secret" not in combined_output
    assert "embed_url" not in combined_output


def test_agent_memory_milvus_seed_failure_redacts_external_error(monkeypatch, tmp_path, capsys):
    module = _load_module()
    profiles_root, manifest = _write_profile_fixture(tmp_path)
    output = tmp_path / "seed.json"

    def fake_embed_batch(_texts, **_kwargs):
        raise RuntimeError("failed calling http://embedding.internal/v1?api_key=secret")

    monkeypatch.setattr(module, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(module.agent_memory_milvus, "upsert_records", lambda records, *, flush=False: len(records))

    exit_code = module.main(
        [
            "--profiles-root",
            str(profiles_root),
            "--manifest",
            str(manifest),
            "--embed-url",
            "http://embedding.internal/v1?api_key=secret",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    combined_output = capsys.readouterr().out + output.read_text(encoding="utf-8")
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["inserted"] == 0
    assert payload["error_type"] == "RuntimeError"
    assert "embedding.internal" not in combined_output
    assert "api_key" not in combined_output
    assert "secret" not in combined_output


def test_agent_memory_milvus_seed_requires_explicit_embedding_endpoint(monkeypatch, tmp_path, capsys):
    module = _load_module()
    profiles_root, manifest = _write_profile_fixture(tmp_path)
    output = tmp_path / "seed.json"

    for name in ("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL", "SIQ_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "embed_batch", lambda _texts, **_kwargs: (_ for _ in ()).throw(AssertionError("must not embed")))

    exit_code = module.main(
        [
            "--profiles-root",
            str(profiles_root),
            "--manifest",
            str(manifest),
            "--require-configured-embed-url",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    combined_output = capsys.readouterr().out + output.read_text(encoding="utf-8")
    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["inserted"] == 0
    assert payload["embedding_endpoint_configured"] is False
    assert payload["requires_configured_embedding_endpoint"] is True
    assert payload["error_type"] == "embedding_endpoint_not_configured"
    assert "127.0.0.1" not in combined_output
