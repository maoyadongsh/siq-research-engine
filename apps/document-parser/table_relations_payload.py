"""Table relation response payload helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_table_relations_response_payload(payload: dict[str, Any], corrections: Any) -> dict[str, Any]:
    result = deepcopy(payload)
    relation_corrections = corrections.get("relations") if isinstance(corrections, dict) else {}
    if not isinstance(relation_corrections, dict):
        return result

    seen_relation_ids: set[str] = set()
    for relation in result.get("relations") or []:
        relation_id = str(relation.get("relation_id") or relation.get("id") or "")
        if relation_id:
            seen_relation_ids.add(relation_id)
        correction = relation_corrections.get(relation_id)
        if isinstance(correction, dict):
            relation["review_status"] = correction.get("review_status") or relation.get("review_status") or ""
            relation["review_note"] = correction.get("note") or ""
            relation["reviewed_at"] = correction.get("updated_at") or ""

    for relation_id, correction in relation_corrections.items():
        if relation_id in seen_relation_ids or not isinstance(correction, dict):
            continue
        result.setdefault("relations", []).append(
            {
                "relation_id": relation_id,
                "relation_type": "manual_review",
                "merge_status": "manual_review",
                "confidence": 0.0,
                "reasons": ["manual_review_without_candidate"],
                "review_status": correction.get("review_status") or "",
                "review_note": correction.get("note") or "",
                "reviewed_at": correction.get("updated_at") or "",
            }
        )

    result["corrections"] = corrections
    return result
