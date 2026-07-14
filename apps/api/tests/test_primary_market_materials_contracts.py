from __future__ import annotations

from pathlib import Path

import pytest

from services import primary_market_materials as materials


def test_primary_market_schema_constants_are_versioned() -> None:
    assert materials.DEAL_DOCUMENT_SCHEMA_V2 == "siq_deal_document_v2"
    assert materials.PRIMARY_MARKET_PARSE_RUN_SCHEMA == "siq_primary_market_parse_run_v1"
    assert materials.PRIMARY_MARKET_ANALYSIS_SOURCE_SCHEMA == "siq_primary_market_analysis_source_v1"
    assert materials.DEAL_EVIDENCE_SNAPSHOT_SCHEMA == "siq_deal_evidence_snapshot_v1"


@pytest.mark.parametrize(
    ("validator", "valid", "invalid"),
    [
        (materials.validate_document_id, "doc-0123456789abcdef", "DOC-../../etc"),
        (materials.validate_parse_run_id, "prun-20260713-0123456789abcdef", "PRUN-../escape"),
        (
            materials.validate_source_id,
            "PM:DEAL-EXAMPLE-001:DOC-0123456789ABCDEF:PRUN-20260713-0123456789ABCDEF",
            "PM:DEAL:../source",
        ),
        (materials.validate_evidence_snapshot_hash, "a" * 64, "not-a-sha256"),
    ],
)
def test_identifier_validators_accept_canonical_values_and_reject_unsafe_values(
    validator,
    valid: str,
    invalid: str,
) -> None:
    result = validator(valid)
    assert result

    with pytest.raises(ValueError):
        validator(invalid)


@pytest.mark.parametrize(
    ("validator", "valid", "invalid"),
    [
        (materials.validate_market, "cn", "US"),
        (materials.validate_exchange, "sse", "NYSE"),
        (materials.validate_board, "star", "pink_sheets"),
        (materials.validate_filing_stage, "registration_draft", "rumor"),
        (
            materials.validate_document_profile,
            "cn_a_share_prospectus",
            "../../custom_profile",
        ),
        (materials.validate_parser_kind, "pdf", "shell"),
    ],
)
def test_enum_validators_are_strict_and_normalize_case(validator, valid: str, invalid: str) -> None:
    assert validator(valid)
    with pytest.raises(ValueError):
        validator(invalid)


def test_deal_paths_are_fixed_and_confined_to_target_deal(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    deal_id = "DEAL-PMM-001"
    document_id = "DOC-0123456789ABCDEF"
    parse_run_id = "PRUN-20260713-0123456789ABCDEF"
    package = (wiki_root / "deals" / deal_id).resolve()

    assert materials.deal_raw_pdf_path(deal_id, document_id, wiki_root=wiki_root) == (
        package / "data_room" / "raw" / f"{document_id}.pdf"
    )
    assert materials.deal_document_metadata_path(deal_id, document_id, wiki_root=wiki_root) == (
        package / "data_room" / "metadata" / f"{document_id}.json"
    )
    assert materials.deal_parse_run_dir(
        deal_id,
        document_id,
        parse_run_id,
        wiki_root=wiki_root,
    ) == package / "parsed_documents" / document_id / "runs" / parse_run_id
    assert materials.deal_current_parse_run_path(
        deal_id,
        document_id,
        wiki_root=wiki_root,
    ) == package / "parsed_documents" / document_id / "current.json"
    assert materials.deal_analysis_sources_path(deal_id, wiki_root=wiki_root) == (
        package / "sources" / "analysis_sources.json"
    )
    assert materials.deal_evidence_snapshot_path(deal_id, wiki_root=wiki_root) == (
        package / "evidence" / "evidence_snapshot.json"
    )


@pytest.mark.parametrize(
    ("deal_id", "document_id", "parse_run_id"),
    [
        ("../DEAL-PMM-001", "DOC-0123456789ABCDEF", "PRUN-20260713-0123456789ABCDEF"),
        ("DEAL-PMM-001", "../DOC-0123456789ABCDEF", "PRUN-20260713-0123456789ABCDEF"),
        ("DEAL-PMM-001", "DOC-0123456789ABCDEF", "../PRUN-20260713-0123456789ABCDEF"),
    ],
)
def test_deal_parse_run_path_rejects_path_traversal(
    tmp_path: Path,
    deal_id: str,
    document_id: str,
    parse_run_id: str,
) -> None:
    with pytest.raises(ValueError):
        materials.deal_parse_run_dir(
            deal_id,
            document_id,
            parse_run_id,
            wiki_root=tmp_path / "wiki",
        )


def test_deal_paths_reject_internal_symlink_escape(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / "DEAL-PMM-001"
    outside = tmp_path / "outside"
    outside.mkdir()
    parse_root = package / "parsed_documents" / "DOC-0123456789ABCDEF"
    parse_root.mkdir(parents=True)
    (parse_root / "runs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes deal package"):
        materials.deal_parse_run_dir(
            "DEAL-PMM-001",
            "DOC-0123456789ABCDEF",
            "PRUN-20260713-0123456789ABCDEF",
            wiki_root=wiki_root,
        )


def test_v1_uploaded_document_normalizes_to_v2_without_losing_legacy_fields() -> None:
    payload = {
        "schema_version": "siq_deal_document_v1",
        "document_id": "DOC-0123456789ABCDEF",
        "deal_id": "DEAL-PMM-001",
        "document_type": "prospectus",
        "status": "uploaded",
        "parse_task_id": None,
        "parsed_artifact_path": None,
    }

    normalized = materials.normalize_deal_document(payload)

    assert normalized["schema_version"] == materials.DEAL_DOCUMENT_SCHEMA_V2
    assert normalized["legacy_schema_version"] == "siq_deal_document_v1"
    assert normalized["status"] == "uploaded"
    assert normalized["document_status"] == "active"
    assert normalized["parse_status"] == "not_started"
    assert normalized["analysis_source_status"] == "pending"
    assert normalized["current_parse_run_id"] is None


@pytest.mark.parametrize(
    ("artifact_exists", "expected_parse_status"),
    [(False, "queued"), (True, "succeeded")],
)
def test_v1_parse_bound_document_keeps_document_parser_compatibility(
    artifact_exists: bool,
    expected_parse_status: str,
) -> None:
    normalized = materials.normalize_deal_document(
        {
            "schema_version": "siq_deal_document_v1",
            "document_id": "DOC-0123456789ABCDEF",
            "deal_id": "DEAL-PMM-001",
            "status": "parse_bound",
            "parse_task_id": "legacy-task-01",
            "parsed_artifact_path": "document.md",
            "parser_artifact_exists": artifact_exists,
        }
    )

    assert normalized["parser_kind"] == "document"
    assert normalized["parse_status"] == expected_parse_status
    assert normalized["parse_task_id"] == "legacy-task-01"
    assert normalized["parsed_artifact_path"] == "document.md"


def test_v2_document_normalization_validates_controlled_fields() -> None:
    with pytest.raises(ValueError, match="exchange"):
        materials.normalize_deal_document(
            {
                "schema_version": materials.DEAL_DOCUMENT_SCHEMA_V2,
                "document_id": "DOC-0123456789ABCDEF",
                "deal_id": "DEAL-PMM-001",
                "market": "CN",
                "exchange": "NYSE",
                "board": "star",
                "filing_stage": "registration_draft",
            }
        )


@pytest.mark.parametrize(
    ("state_kind", "current", "target"),
    [
        ("document", "active", "superseded"),
        ("parse", "not_started", "submitting"),
        ("parse", "submitting", "queued"),
        ("parse", "parsing", "archiving"),
        ("parse", "archiving", "succeeded"),
        ("source", "pending", "review_required"),
        ("source", "review_required", "ready"),
        ("source", "ready", "disabled"),
        ("index", "not_requested", "queued"),
        ("index", "indexing", "indexed"),
    ],
)
def test_state_transition_accepts_declared_edges(state_kind: str, current: str, target: str) -> None:
    assert materials.validate_state_transition(state_kind, current, target) == target


@pytest.mark.parametrize(
    ("state_kind", "current", "target"),
    [
        ("document", "superseded", "active"),
        ("parse", "not_started", "succeeded"),
        ("parse", "queued", "archiving"),
        ("source", "blocked", "ready"),
        ("index", "not_requested", "indexed"),
    ],
)
def test_state_transition_rejects_illegal_edges(state_kind: str, current: str, target: str) -> None:
    with pytest.raises(ValueError, match="illegal"):
        materials.validate_state_transition(state_kind, current, target)


def test_state_transition_is_idempotent_but_rejects_unknown_states() -> None:
    assert materials.validate_state_transition("parse", "queued", "queued") == "queued"
    with pytest.raises(ValueError, match="unknown state kind"):
        materials.validate_state_transition("workflow", "pending", "ready")
    with pytest.raises(ValueError, match="invalid parse state"):
        materials.validate_state_transition("parse", "unknown", "queued")


def test_new_parse_run_id_is_canonical_and_unique() -> None:
    first = materials.new_parse_run_id()
    second = materials.new_parse_run_id()

    assert materials.validate_parse_run_id(first) == first
    assert materials.validate_parse_run_id(second) == second
    assert first != second


def test_primary_market_source_id_binds_deal_document_and_parse_run() -> None:
    source_id = materials.primary_market_source_id(
        "DEAL-PMM-001",
        "doc-0123456789abcdef",
        "prun-20260713-0123456789abcdef",
    )

    assert source_id == (
        "PM:DEAL-PMM-001:DOC-0123456789ABCDEF:PRUN-20260713-0123456789ABCDEF"
    )
