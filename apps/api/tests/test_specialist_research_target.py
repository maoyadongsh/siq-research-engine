from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from services.research_report_package import (
    enumerate_companies,
    iter_exact_artifact_sidecars,
    resolve_report_package,
)
from services.research_universe_contracts import ResearchUniverseError
from services.specialist_research_target import (
    materialized_target_bundle,
    publish_agent_artifact_v2,
)
from tests.research_universe_fixture import build_six_market_wiki
from tests.specialist_workflow_fixture import write_analysis_target

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script_module(name: str, path: Path):
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _us_package(wiki_root: Path):
    company = next(item for item in enumerate_companies(wiki_root=wiki_root, markets=("US",)))
    return resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="tracking",
        wiki_root=wiki_root,
    )


def test_target_bundle_binds_analysis_id_hash_and_server_paths(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_us_package(wiki_root))

    payload = target.to_bundle()

    assert payload["baseline_analysis_artifact_id"] == target.analysis_artifact.artifact.artifact_id
    assert payload["baseline_analysis_content_hash"] == target.analysis_artifact.artifact.content_hash
    assert payload["research_target"]["research_identity"] == target.package.research_identity.to_dict()
    assert payload["resolved_paths"]["analysis_sidecar"] == str(target.analysis_artifact.sidecar_path)
    with materialized_target_bundle(target, prefix="pytest") as bundle_path:
        assert bundle_path.is_file()
        assert json.loads(bundle_path.read_text(encoding="utf-8"))["baseline_analysis_content_hash"]
    assert not bundle_path.exists()


def test_v2_publisher_creates_canonical_html_and_exact_sidecar(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_us_package(wiki_root))
    output_dir = target.package.output_dir_for("tracking")
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = output_dir / "human-readable-report.html"
    generated.write_text("<html>tracking</html>", encoding="utf-8")

    artifact, sidecar, canonical = publish_agent_artifact_v2(
        target,
        artifact_type="tracking",
        html_path=generated,
        status="degraded",
        adapter_version="market_tracking_v1",
        citation_count=2,
        unresolved_count=0,
        warnings=["sentiment_source_unavailable"],
    )

    assert canonical.name == f"{artifact.artifact_id}.html"
    assert sidecar.name == f"{artifact.artifact_id}.artifact.json"
    assert canonical.read_text(encoding="utf-8") == generated.read_text(encoding="utf-8")
    assert generated.is_symlink()
    assert generated.resolve() == canonical
    assert artifact.metadata["legacy_aliases"] == [generated.name]
    exact = iter_exact_artifact_sidecars(target.package, "tracking")
    assert [item[0].artifact_id for item in exact] == [artifact.artifact_id]
    assert exact[0][0].upstream_artifact_ids == (target.analysis_artifact.artifact.artifact_id,)


def test_v2_publisher_rejects_output_outside_resolved_company(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_us_package(wiki_root))
    outside = tmp_path / "outside.html"
    outside.write_text("<html>outside</html>", encoding="utf-8")

    with pytest.raises(ResearchUniverseError) as rejected:
        publish_agent_artifact_v2(
            target,
            artifact_type="tracking",
            html_path=outside,
            status="completed",
            adapter_version="market_tracking_v1",
            citation_count=1,
            unresolved_count=0,
        )

    assert rejected.value.code == "unsafe_path_rejected"


def test_v2_publisher_rolls_back_sidecar_when_html_commit_fails(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_us_package(wiki_root))
    output_dir = target.package.output_dir_for("tracking")
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = output_dir / "generated-before-publication.html"
    generated.write_text("<html>tracking</html>", encoding="utf-8")
    original_replace = Path.replace

    def fail_html_commit(path: Path, target_path: Path):
        if path.name.endswith(".html.tmp"):
            raise OSError("injected HTML commit failure")
        return original_replace(path, target_path)

    monkeypatch.setattr(Path, "replace", fail_html_commit)
    with pytest.raises(OSError, match="injected HTML commit failure"):
        publish_agent_artifact_v2(
            target,
            artifact_type="tracking",
            html_path=generated,
            status="completed",
            adapter_version="market_tracking_v1",
            citation_count=1,
            unresolved_count=0,
        )

    assert generated.is_file()
    assert not list(output_dir.glob("tracking_*.artifact.json"))
    assert not list(output_dir.glob("tracking_*.html"))
    assert not list(output_dir.glob("*.tmp"))


def test_factcheck_and_tracking_script_loaders_recheck_analysis_hash(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_us_package(wiki_root))
    bundle_path = tmp_path / "target.json"
    bundle_path.write_text(json.dumps(target.to_bundle()), encoding="utf-8")
    factcheck_module = _load_script_module(
        "market_factcheck_target_loader_test",
        REPO_ROOT
        / "agents"
        / "hermes"
        / "profiles"
        / "siq_factchecker_multi_market"
        / "scripts"
        / "market_factcheck_engine.py",
    )
    tracking_module = _load_script_module(
        "tracking_target_loader_test",
        REPO_ROOT / "data" / "wiki" / "tracking" / "scripts_multi_market" / "run_all.py",
    )

    assert factcheck_module.load_resolved_target(bundle_path, wiki_root).analysis_artifact.is_file()
    assert tracking_module._read_target_bundle(str(bundle_path), str(wiki_root))["research_target"]

    cn_bundle = target.to_bundle()
    cn_bundle["research_target"]["research_identity"]["market"] = "CN"
    cn_bundle_path = tmp_path / "cn-target.json"
    cn_bundle_path.write_text(json.dumps(cn_bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="cn_legacy_pipeline_required"):
        factcheck_module.load_resolved_target(cn_bundle_path, wiki_root)
    with pytest.raises(ValueError, match="cn_legacy_pipeline_required"):
        tracking_module._read_target_bundle(str(cn_bundle_path), str(wiki_root))

    target.analysis_artifact.html_path.write_text("<html>tampered</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="content hash"):
        factcheck_module.load_resolved_target(bundle_path, wiki_root)
    with pytest.raises(ValueError, match="content hash"):
        tracking_module._read_target_bundle(str(bundle_path), str(wiki_root))
