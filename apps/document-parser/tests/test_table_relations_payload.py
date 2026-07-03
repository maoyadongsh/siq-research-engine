from __future__ import annotations

import sys
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from table_relations_payload import build_table_relations_response_payload  # noqa: E402


def test_table_relations_payload_merges_existing_relation_corrections_without_mutating_input() -> None:
    payload = {
        "schema_version": "document_table_relations_v1",
        "task_id": "task-a",
        "relations": [
            {
                "relation_id": "rel-1",
                "relation_type": "continuation",
                "merge_status": "auto_merged",
                "review_status": "accepted",
            }
        ],
    }
    corrections = {
        "schema_version": "document_table_merge_corrections_v1",
        "relations": {
            "rel-1": {
                "review_status": "rejected",
                "note": "not the same logical table",
                "updated_at": "2026-07-04T10:00:00Z",
            }
        },
    }

    result = build_table_relations_response_payload(payload, corrections)

    assert result["relations"][0]["review_status"] == "rejected"
    assert result["relations"][0]["review_note"] == "not the same logical table"
    assert result["relations"][0]["reviewed_at"] == "2026-07-04T10:00:00Z"
    assert result["corrections"] == corrections
    assert payload["relations"][0]["review_status"] == "accepted"
    assert "corrections" not in payload


def test_table_relations_payload_appends_orphan_corrections_as_manual_review_relations() -> None:
    corrections = {
        "relations": {
            "rel-manual": {
                "review_status": "needs_review",
                "note": "analyst marked a missing candidate",
                "updated_at": "2026-07-04T11:00:00Z",
            }
        }
    }

    result = build_table_relations_response_payload(
        {"task_id": "task-a", "relations": []},
        corrections,
    )

    assert result["relations"] == [
        {
            "relation_id": "rel-manual",
            "relation_type": "manual_review",
            "merge_status": "manual_review",
            "confidence": 0.0,
            "reasons": ["manual_review_without_candidate"],
            "review_status": "needs_review",
            "review_note": "analyst marked a missing candidate",
            "reviewed_at": "2026-07-04T11:00:00Z",
        }
    ]
    assert result["corrections"] == corrections


def test_table_relations_payload_ignores_invalid_corrections() -> None:
    payload = {
        "task_id": "task-a",
        "relations": [
            {
                "relation_id": "rel-1",
                "relation_type": "continuation",
                "merge_status": "auto_merged",
            }
        ],
    }

    assert build_table_relations_response_payload(payload, {}) == payload
    assert build_table_relations_response_payload(payload, {"relations": []}) == payload
    assert build_table_relations_response_payload(payload, {"relations": {"rel-2": "bad"}}) == {
        "task_id": "task-a",
        "relations": [
            {
                "relation_id": "rel-1",
                "relation_type": "continuation",
                "merge_status": "auto_merged",
            }
        ],
        "corrections": {"relations": {"rel-2": "bad"}},
    }
