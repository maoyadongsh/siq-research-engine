from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api"))

from services import agent_memory_milvus, agent_memory_service  # noqa: E402


def test_memory_collection_manifest_matches_the_host_runtime_write_boundary() -> None:
    manifest = json.loads(
        (ROOT / "infra/openshell/data-broker/memory-collections.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest == {
        "allowed_agent_groups": ["primary_market", "secondary_market"],
        "allowed_logical_aliases": ["siq_agent_memory_active"],
        "allowed_operations": ["delete_by_id", "flush", "search", "upsert"],
        "executor": "host_fastapi_memory_service_only",
        "knowledge_collections_mutable": False,
        "required_schema_version": agent_memory_milvus.COLLECTION_SCHEMA_VERSION,
        "sandbox_direct_milvus": False,
        "schema_version": "siq.openshell.memory-collection-boundary.v1",
    }
    assert set(manifest["allowed_logical_aliases"]) == set(
        agent_memory_milvus.ALLOWED_RUNTIME_COLLECTION_ALIASES
    )
    assert agent_memory_service.infer_agent_group("siq_ic_chairman") == "primary_market"
    assert agent_memory_service.infer_agent_group("siq_analysis") == "secondary_market"
