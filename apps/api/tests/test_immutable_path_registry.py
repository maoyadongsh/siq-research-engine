from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from services.immutable_path_registry import (
    ImmutableRegistrySecurityError,
    build_immutable_registry,
    write_registry,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    wiki = project / "data" / "wiki"
    wiki.mkdir(parents=True)
    return project, wiki


def _company(path: Path, *, company_id: str, reports: list[dict[str, object]]) -> None:
    _write_json(path / "company.json", {"company_id": company_id, "reports": reports})


def _cn_report(company: Path, report_id: str, *, company_status: str, core_status: str = "ready") -> None:
    _company(
        company,
        company_id="CN:600001",
        reports=[{"report_id": report_id, "status": company_status, "task_id": "task-1"}],
    )
    _write_json(
        company / "reports" / report_id / "artifact_manifest.json",
        {
            "task_id": "task-1",
            "core": {"status": core_status, "ready": True, "bundle_sha256": SHA_A},
            "artifacts": {"report.md": {"exists": True, "sha256": SHA_B}},
        },
    )


def _market_report(
    wiki: Path,
    *,
    market: str,
    report_id: str,
    company_ready: bool = True,
    quality_status: str = "pass",
) -> Path:
    company = wiki / market / "companies" / "001-Test-Co"
    report_row: dict[str, object] = {
        "report_id": report_id,
        "retrieval_status": "ready" if company_ready else "staging",
        "wiki_ready": company_ready,
    }
    _company(company, company_id=f"{market.upper()}:001", reports=[report_row])
    package = company / "reports" / report_id
    (package / "report.md").parent.mkdir(parents=True, exist_ok=True)
    (package / "report.md").write_text("fixture\n", encoding="utf-8")
    _write_json(
        package / "manifest.json",
        {
            "schema_version": "market_evidence_package_v1",
            "market": market.upper(),
            "company_id": f"{market.upper()}:001",
            "report_id": report_id,
            "filing_id": f"{market.upper()}:filing-1",
            "parse_run_id": f"{market.upper()}:parse-1",
            "quality_status": quality_status,
            "artifact_hashes": {"report.md": SHA_A},
        },
    )
    return package


def test_cn_finalized_report_is_included_but_needs_review_is_not(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    ready_company = wiki / "companies" / "600001-Ready"
    review_company = wiki / "companies" / "600002-Review"
    _cn_report(ready_company, "2025-annual", company_status="ready")
    _cn_report(review_company, "2025-annual", company_status="needs_review")
    (ready_company / "analysis").mkdir()
    (ready_company / "factcheck").mkdir()
    (ready_company / "tracking").mkdir()
    (ready_company / "legal").mkdir()

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert [entry["path"] for entry in build.payload["entries"]] == [
        "data/wiki/companies/600001-Ready/reports/2025-annual"
    ]
    assert build.payload["summary"]["skipped_by_reason"]["company_report_not_finalized"] == 1


def test_legacy_cn_report_requires_ready_company_bundle_and_complete_hashes(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    company = wiki / "companies" / "600001-Legacy"
    _company(
        company,
        company_id="CN:600001",
        reports=[
            {
                "report_id": "2025-annual",
                "status": "ready",
                "task_id": "task-legacy",
                "artifact_bundle_sha256": SHA_A,
            }
        ],
    )
    _write_json(
        company / "reports" / "2025-annual" / "artifact_manifest.json",
        {
            "schema_version": 1,
            "task_id": "task-legacy",
            "artifacts": {
                "report.md": {"exists": True, "sha256": SHA_A},
                "document_full.json": {"exists": True, "sha256": SHA_B},
            },
        },
    )

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert [entry["path"] for entry in build.payload["entries"]] == [
        "data/wiki/companies/600001-Legacy/reports/2025-annual"
    ]


def test_market_package_requires_ready_index_identity_hashes_and_pass_quality(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    ready = _market_report(wiki, market="hk", report_id="2025-annual")
    _market_report(wiki, market="jp", report_id="2025-warning", quality_status="warning")
    _market_report(wiki, market="kr", report_id="2025-staging", company_ready=False)

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert [entry["path"] for entry in build.payload["entries"]] == [ready.relative_to(project).as_posix()]
    assert build.payload["entries"][0]["identity"]["parse_run_id"] == "HK:parse-1"


def test_market_report_identity_mismatch_is_not_borrowed_from_another_package(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    package = _market_report(wiki, market="hk", report_id="staging")
    manifest_path = package / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["report_id"] = "2025-annual"
    _write_json(manifest_path, payload)
    company = package.parents[1]
    company_payload = json.loads((company / "company.json").read_text(encoding="utf-8"))
    company_payload["reports"][0]["report_id"] = "2025-annual"
    _write_json(company / "company.json", company_payload)

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert build.payload["entries"] == []
    assert build.payload["summary"]["skipped_by_reason"]["report_identity_mismatch"] == 1


def test_missing_manifest_does_not_mark_report_immutable(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    company = wiki / "companies" / "600001-Test"
    _company(company, company_id="CN:600001", reports=[{"report_id": "staging", "status": "ready"}])
    (company / "reports" / "staging").mkdir(parents=True)

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert build.payload["entries"] == []
    assert build.payload["summary"]["skipped_by_reason"]["report_manifest_missing"] == 1


def test_finalized_deal_snapshot_is_included_without_locking_workflow(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    deal = wiki / "deals" / "DEAL-001"
    snapshot = deal / "evidence" / "snapshots" / "SNAP-001"
    _write_json(
        snapshot / "snapshot_manifest.json",
        {
            "schema_version": "siq.deal_evidence_snapshot.v1",
            "deal_id": "DEAL-001",
            "snapshot_id": "SNAP-001",
            "status": "finalized",
            "finalized": True,
            "artifact_hashes": {"evidence.json": SHA_A},
        },
    )
    (deal / "phases").mkdir()
    (deal / "discussion").mkdir()

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert [entry["path"] for entry in build.payload["entries"]] == [
        "data/wiki/deals/DEAL-001/evidence/snapshots/SNAP-001"
    ]


@pytest.mark.parametrize(
    ("deal_id", "snapshot_id"),
    (("DEAL-OTHER", "SNAP-001"), ("DEAL-001", "SNAP-OTHER")),
)
def test_deal_snapshot_manifest_identity_must_match_its_directory(
    tmp_path: Path,
    deal_id: str,
    snapshot_id: str,
) -> None:
    project, wiki = _project(tmp_path)
    snapshot = wiki / "deals" / "DEAL-001" / "evidence" / "snapshots" / "SNAP-001"
    _write_json(
        snapshot / "snapshot_manifest.json",
        {
            "schema_version": "siq.deal_evidence_snapshot.v1",
            "deal_id": deal_id,
            "snapshot_id": snapshot_id,
            "status": "finalized",
            "finalized": True,
            "artifact_hashes": {"evidence.json": SHA_A},
        },
    )

    build = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert build.payload["entries"] == []
    assert build.payload["summary"]["skipped_by_reason"]["deal_snapshot_identity_mismatch"] == 1


def test_symlink_escape_and_external_wiki_root_are_rejected(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    company = wiki / "companies" / "600001-Test"
    _company(company, company_id="CN:600001", reports=[{"report_id": "escaped", "status": "ready"}])
    (company / "reports").mkdir()
    (company / "reports" / "escaped").symlink_to(external, target_is_directory=True)

    with pytest.raises(ImmutableRegistrySecurityError):
        build_immutable_registry(project_root=project, wiki_root=wiki)
    with pytest.raises(ImmutableRegistrySecurityError):
        build_immutable_registry(project_root=project, wiki_root=external)


def test_output_is_deterministic_and_write_has_matching_digest(tmp_path: Path) -> None:
    project, wiki = _project(tmp_path)
    _cn_report(wiki / "companies" / "600001-Ready", "2025-annual", company_status="ready")

    first = build_immutable_registry(project_root=project, wiki_root=wiki)
    second = build_immutable_registry(project_root=project, wiki_root=wiki)

    assert first.content == second.content
    assert first.digest == second.digest
    output, digest_output = write_registry(
        first,
        project_root=project,
        output=Path("var/openshell/registry/immutable-paths.json"),
        digest_output=Path("var/openshell/registry/immutable-paths.sha256"),
    )
    assert output.read_bytes() == first.content
    assert digest_output.read_text(encoding="ascii") == (
        f"{hashlib.sha256(first.content).hexdigest()}  var/openshell/registry/immutable-paths.json\n"
    )
