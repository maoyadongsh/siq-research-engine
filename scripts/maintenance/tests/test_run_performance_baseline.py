import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest


def _load_module():
    source = Path(__file__).resolve().parents[1] / "run_performance_baseline.py"
    spec = importlib.util.spec_from_file_location("run_performance_baseline_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _clear_agent_memory_vector_env(monkeypatch):
    for name in (
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
        "SIQ_EMBEDDING_BASE_URL",
        "EMBEDDING_BASE_URL",
        "SIQ_AGENT_MEMORY_EMBEDDING_MODEL",
        "SIQ_EMBEDDING_MODEL",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_nightly_manifest(tmp_path: Path) -> tuple[Path, Path]:
    sample_root = tmp_path / "external-samples"
    markets = {}
    for market in ("HK", "JP", "KR", "EU", "US"):
        rel = f"data/pdf-parser/results/{market.lower()}/document_full.json"
        resolved = sample_root / Path(*Path(rel).parts[1:])
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(
                {
                    "market": market,
                    "company": f"{market} Sample",
                    "statements": [],
                }
            ),
            encoding="utf-8",
        )
        markets[market] = [rel]
    manifest = tmp_path / "production_sample_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_document_full_production_sample_manifest_v1",
                "sample_goal_per_market": 1,
                "markets": markets,
            }
        ),
        encoding="utf-8",
    )
    return manifest, sample_root


def test_contract_performance_baseline_writes_machine_report(tmp_path, capsys, monkeypatch):
    module = _load_module()
    output = tmp_path / "performance_baseline.json"
    markdown = tmp_path / "performance_baseline.md"
    secret_url = "https://user:perf-secret@private-embedding.invalid/v1"
    monkeypatch.setenv("SIQ_PGPASSWORD", "database-secret")

    exit_code = module.main(
        [
            "--repeat",
            "1",
            "--agent-memory-embedding-base-url",
            secret_url,
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--json",
            "--cpu-count-override",
            "8",
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    stdout_payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == "siq_performance_baseline_v1"
    assert payload["mode"] == "contract"
    assert payload["passed"] is True
    assert payload["environment"]["cpu_count"] == 8
    required_evidence_fields = {
        "generated_at",
        "base_commit",
        "worktree_dirty",
        "task_id",
        "environment_profile",
        "command",
        "result",
        "duration_seconds",
        "failures",
        "artifact_checksums",
    }
    assert required_evidence_fields <= payload.keys()
    assert payload["task_id"] == "T10"
    assert payload["result"] == "pass"
    assert payload["duration_seconds"] >= 0
    assert payload["failures"] == []
    assert payload["artifact_checksums"]
    assert all(not key.startswith("/") for key in payload["artifact_checksums"])
    assert all("#" not in key for key in payload["artifact_checksums"])
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in payload["artifact_checksums"].values())
    serialized = json.dumps(payload)
    markdown_text = markdown.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "perf-secret" not in serialized
    assert "database-secret" not in serialized
    assert "private-embedding.invalid" not in serialized
    assert "<configured-url>" in payload["command"]
    assert "## Failures" in markdown_text
    assert "## Artifact Checksums" in markdown_text
    assert {item["name"] for item in payload["benchmarks"]} == {
        "market_document_full_contract",
        "market_evidence_chunk_builder",
        "market_ingestion_contract",
    }
    assert all(item["iterations"] == 1 for item in payload["benchmarks"])
    assert stdout_payload["passed"] is True
    assert "SIQ Performance Baseline" in markdown.read_text(encoding="utf-8")


def test_contract_performance_baseline_uses_portable_fixtures_by_default():
    module = _load_module()
    args = module.build_parser().parse_args([])

    default_paths = [
        args.market_ingestion_case_root,
        args.market_ingestion_wiki_root,
        args.document_full_cases,
        args.production_sample_manifest,
        args.market_package,
    ]

    assert all(str(path).startswith(str(module.REPO_ROOT / "eval_datasets")) for path in default_paths)
    assert not any("data/wiki" in str(path) for path in default_paths)


def test_vector_performance_baseline_defaults_to_production_collection_alias():
    module = _load_module()

    args = module.build_parser().parse_args([])

    assert args.agent_memory_vector_collection == "siq_agent_memory_active"


def test_performance_baseline_rejects_database_url_argv_credentials():
    module = _load_module()

    with pytest.raises(SystemExit):
        module.build_parser().parse_args(
            ["--database-url", "postgresql://user:secret@db.internal/siq_hk"]
        )


def test_postgres_performance_probe_uses_env_routing_and_redacts_connection_errors(monkeypatch):
    module = _load_module()
    seen_explicit_urls = []

    class FakeBacktest:
        MARKET_SCHEMAS = {"HK": "hk"}

        @staticmethod
        def database_url_for_market(market, explicit_url):
            assert market == "HK"
            seen_explicit_urls.append(explicit_url)
            return "postgresql://user:secret@db.internal/siq_hk"

        @staticmethod
        def safe_sql_ident(value):
            return value

    class FakePsycopg:
        @staticmethod
        def connect(_url):
            raise RuntimeError("failed postgresql://user:secret@db.internal/siq_hk")

    monkeypatch.setattr(module, "_load_module", lambda *_args, **_kwargs: FakeBacktest)
    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg)

    result = module._postgres_agent_view_query_benchmark()

    assert seen_explicit_urls == [None]
    assert result["passed"] is False
    assert result["skipped"] is True
    assert result["errors"] == ["HK: RuntimeError"]
    assert "secret" not in json.dumps(result)
    assert "db.internal" not in json.dumps(result)


def test_nightly_performance_baseline_reports_real_sample_and_optional_postgres_skip(tmp_path, monkeypatch):
    module = _load_module()
    _clear_agent_memory_vector_env(monkeypatch)
    manifest, sample_root = _write_nightly_manifest(tmp_path)
    output = tmp_path / "performance_baseline_nightly.json"
    markdown = tmp_path / "performance_baseline_nightly.md"

    exit_code = module.main(
        [
            "--mode",
            "nightly",
            "--repeat",
            "1",
            "--production-sample-manifest",
            str(manifest),
            "--production-sample-root",
            str(sample_root),
            "--skip-postgres-nightly",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in payload["benchmarks"]}
    assert exit_code == 0
    assert payload["mode"] == "nightly"
    assert by_name["production_sample_manifest_files"]["passed"] is True
    assert by_name["production_sample_manifest_files"]["domain"]["sample_root"] == "[configured]"
    assert str(sample_root) not in output.read_text(encoding="utf-8")
    assert by_name["production_sample_manifest_files"]["domain"]["existing_samples"] == 5
    assert by_name["parser_document_full_load"]["passed"] is True
    assert by_name["parser_document_full_load"]["domain"]["loaded_documents"] == 5
    assert "postgres_agent_view_query_latency" not in by_name
    assert by_name["agent_memory_embedding_throughput"]["passed"] is True
    assert by_name["agent_memory_embedding_throughput"]["skipped"] is True
    assert by_name["agent_memory_milvus_retrieval_latency"]["passed"] is True
    assert by_name["agent_memory_milvus_retrieval_latency"]["skipped"] is True
    assert "SIQ Performance Baseline" in markdown.read_text(encoding="utf-8")


def test_required_nightly_inputs_fail_when_real_sample_root_is_missing(tmp_path, monkeypatch):
    module = _load_module()
    _clear_agent_memory_vector_env(monkeypatch)
    output = tmp_path / "performance_baseline_nightly.json"
    markdown = tmp_path / "performance_baseline_nightly.md"

    exit_code = module.main(
        [
            "--mode",
            "nightly",
            "--repeat",
            "1",
            "--require-nightly-inputs",
            "--production-sample-root",
            str(tmp_path / "missing-samples"),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    failed = [item["name"] for item in payload["benchmarks"] if not item["passed"]]
    assert exit_code == 1
    assert "production_sample_manifest_files" in failed
    assert "parser_document_full_load" in failed


def test_nightly_agent_memory_vector_probes_skip_without_external_services(tmp_path, monkeypatch):
    module = _load_module()
    _clear_agent_memory_vector_env(monkeypatch)
    output = tmp_path / "performance_baseline_nightly.json"
    markdown = tmp_path / "performance_baseline_nightly.md"

    exit_code = module.main(
        [
            "--mode",
            "nightly",
            "--repeat",
            "1",
            "--skip-postgres-nightly",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in payload["benchmarks"]}
    assert exit_code == 0
    assert payload["passed"] is True
    assert by_name["agent_memory_embedding_throughput"]["required"] is False
    assert by_name["agent_memory_embedding_throughput"]["skipped"] is True
    assert "embedding base URL is not configured" in by_name["agent_memory_embedding_throughput"]["domain"]["reason"]
    assert by_name["agent_memory_milvus_retrieval_latency"]["required"] is False
    assert by_name["agent_memory_milvus_retrieval_latency"]["skipped"] is True
    assert "embedding base URL is not configured" in by_name["agent_memory_milvus_retrieval_latency"]["domain"]["reason"]


def test_agent_memory_embedding_throughput_probe_reports_vector_dimensions(monkeypatch):
    module = _load_module()

    class FakeIngest:
        @staticmethod
        def embedding_endpoint(args):
            assert args.embed_url == "http://embedding.internal"
            return "http://embedding.internal/v1/embeddings"

        @staticmethod
        def embed_batch(texts, *, endpoint, model, timeout):
            assert endpoint == "http://embedding.internal/v1/embeddings"
            assert model == "fake-embedding-model"
            assert timeout == 3.0
            return [[0.1, 0.2, 0.3] for _text in texts]

    class FakeEval:
        @staticmethod
        def load_cases(path):
            assert path == ""
            return [{"query": "alpha"}, {"query": "beta"}]

    monkeypatch.setattr(module, "_agent_memory_ingest_module", lambda: FakeIngest)
    monkeypatch.setattr(module, "_agent_memory_eval_module", lambda: FakeEval)

    result = module._agent_memory_embedding_throughput_benchmark(
        embedding_base_url="http://embedding.internal",
        embedding_model="fake-embedding-model",
        cases_path=None,
        timeout_seconds=3.0,
        max_texts=2,
        required=True,
    )

    assert result["passed"] is True
    assert result["input_count"] == 2
    assert result["vector_count"] == 2
    assert result["vector_dim"] == 3
    assert result["texts_per_second"] > 0


def test_agent_memory_embedding_probe_redacts_external_endpoint_from_errors(monkeypatch):
    module = _load_module()

    class FakeIngest:
        @staticmethod
        def embedding_endpoint(args):
            return f"{args.embed_url}/v1/embeddings"

        @staticmethod
        def embed_batch(texts, *, endpoint, model, timeout):
            raise RuntimeError(f"failed calling {endpoint}?api_key=secret")

    class FakeEval:
        @staticmethod
        def load_cases(path):
            return [{"query": "alpha"}]

    monkeypatch.setattr(module, "_agent_memory_ingest_module", lambda: FakeIngest)
    monkeypatch.setattr(module, "_agent_memory_eval_module", lambda: FakeEval)

    result = module._agent_memory_embedding_throughput_benchmark(
        embedding_base_url="http://embedding.internal",
        embedding_model="fake-embedding-model",
        cases_path=None,
        timeout_seconds=3.0,
        max_texts=1,
        required=False,
    )

    assert result["skipped"] is True
    assert result["reason"] == "embedding throughput probe failed: RuntimeError"
    assert "embedding.internal" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_agent_memory_milvus_retrieval_probe_reports_hit_rate_latency_and_restores_env(monkeypatch):
    module = _load_module()

    class FakeEval:
        @staticmethod
        def load_cases(path):
            assert path == ""
            return [
                {"query": "alpha", "profile": "siq_assistant"},
                {"query": "beta", "profile": "siq_ic_chairman"},
            ]

        @staticmethod
        async def run_case(case, *, top_k):
            assert top_k == 4
            return {
                "query": case["query"],
                "profile": case["profile"],
                "status": "passed",
                "rank": 1,
                "latency_ms": 7.0 if case["query"] == "alpha" else 9.0,
            }

    monkeypatch.setattr(module, "_agent_memory_eval_module", lambda: FakeEval)
    monkeypatch.setattr(module, "_module_available", lambda name: name == "pymilvus")
    monkeypatch.setenv("SIQ_AGENT_MEMORY_VECTOR_BACKEND", "pgvector")

    result = module._agent_memory_milvus_retrieval_benchmark(
        embedding_base_url="http://embedding.internal",
        embedding_model="fake-embedding-model",
        collection="siq_agent_memory_perf",
        cases_path=None,
        top_k=4,
        max_cases=2,
        required=True,
    )

    assert result["passed"] is True
    assert result["cases"] == 2
    assert result["passed_cases"] == 2
    assert result["hit_rate"] == 1.0
    assert result["mrr"] == 1.0
    assert result["latency_ms"]["p95"] == 9.0
    assert result["collection"] == "siq_agent_memory_perf"
    assert os.environ["SIQ_AGENT_MEMORY_VECTOR_BACKEND"] == "pgvector"


def test_required_agent_memory_vector_probes_fail_without_embedding_endpoint(tmp_path, monkeypatch):
    module = _load_module()
    _clear_agent_memory_vector_env(monkeypatch)
    output = tmp_path / "performance_baseline_nightly.json"
    markdown = tmp_path / "performance_baseline_nightly.md"

    exit_code = module.main(
        [
            "--mode",
            "nightly",
            "--repeat",
            "1",
            "--skip-postgres-nightly",
            "--require-agent-memory-vector-probes",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    failed = {item["name"]: item for item in payload["benchmarks"] if not item["passed"]}
    assert exit_code == 1
    assert failed["agent_memory_embedding_throughput"]["required"] is True
    assert failed["agent_memory_embedding_throughput"]["skipped"] is False
    assert failed["agent_memory_milvus_retrieval_latency"]["required"] is True
    assert failed["agent_memory_milvus_retrieval_latency"]["skipped"] is False


def test_benchmark_redacts_exception_details():
    module = _load_module()

    def explode():
        raise RuntimeError("failed postgresql://user:secret@private-db.invalid/siq")

    result = module._benchmark(
        module.BenchmarkSpec(name="redaction_probe", fn=explode),
        repeat=1,
        max_benchmark_seconds=30,
    )

    assert result["errors"] == ["RuntimeError"]
    assert "secret" not in json.dumps(result)
    assert "private-db.invalid" not in json.dumps(result)


def test_benchmark_fails_on_domain_failure():
    module = _load_module()
    result = module._benchmark(
        module.BenchmarkSpec(name="empty", fn=lambda: {"passed": False}),
        repeat=3,
        max_benchmark_seconds=30,
    )

    assert result["passed"] is False
    assert result["iterations"] == 3


def test_benchmark_fails_on_antihang_ceiling():
    module = _load_module()
    result = module._benchmark(
        module.BenchmarkSpec(name="fast", fn=lambda: {"passed": True}),
        repeat=1,
        max_benchmark_seconds=-1,
    )

    assert result["passed"] is False
    assert "iteration exceeded" in result["errors"][0]


def test_main_returns_nonzero_when_any_contract_benchmark_fails(tmp_path):
    module = _load_module()
    output = tmp_path / "performance_baseline.json"
    markdown = tmp_path / "performance_baseline.md"

    exit_code = module.main(
        [
            "--repeat",
            "1",
            "--market-package",
            str(tmp_path / "missing-package"),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    failed = [item for item in payload["benchmarks"] if not item["passed"]]
    assert exit_code == 1
    assert payload["passed"] is False
    assert [item["name"] for item in failed] == ["market_evidence_chunk_builder"]
    assert failed[0]["errors"]
