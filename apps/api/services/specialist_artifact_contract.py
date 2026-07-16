"""Shared contract and audit finalization for specialist report artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from services.agent_runtime_answer_audit import (
    ANSWER_AUDIT_TRACE_SCHEMA,
    record_answer_audit_trace,
    redact_audit_value,
    stable_hash,
)

SpecialistArtifactType = Literal["factcheck", "tracking", "legal"]


class SpecialistArtifactValidation(BaseModel):
    ok: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SpecialistArtifactContract(BaseModel):
    schema_version: str = "siq_specialist_artifact_v1"
    artifact_type: SpecialistArtifactType
    company_id: str
    source_report_path: str
    output_path: str
    html_url: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    validation_result: SpecialistArtifactValidation
    audit_trace_id: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_citations(items: Any, *, default_source_type: str) -> list[dict[str, Any]]:
    if isinstance(items, Mapping):
        values: Sequence[Any] = list(items.values())
    elif isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        values = items
    else:
        values = []

    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        citation = {
            str(key): value
            for key, value in raw.items()
            if value not in (None, "", [], {})
        }
        citation.setdefault("source_type", default_source_type)
        if "source_path" not in citation and citation.get("file"):
            citation["source_path"] = citation["file"]
        if "quote" not in citation and citation.get("text"):
            citation["quote"] = citation["text"]
        if "pdf_page" not in citation and citation.get("pdf_page_number") not in (None, ""):
            citation["pdf_page"] = citation["pdf_page_number"]
        identity = json.dumps(redact_audit_value(citation), ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        citations.append(dict(redact_audit_value(citation, max_string_length=1200)))
        if len(citations) >= 100:
            break
    return citations


def citation_has_locator(citation: Mapping[str, Any]) -> bool:
    has_source = any(
        citation.get(key) not in (None, "")
        for key in (
            "source_path",
            "file",
            "task_id",
            "pdf_task_id",
            "evidence_id",
            "report_id",
            "source_url",
            "local_source_id",
        )
    )
    has_locator = any(
        citation.get(key) not in (None, "")
        for key in (
            "chunk_index",
            "pdf_page",
            "pdf_page_number",
            "table_index",
            "table_id",
            "md_line",
            "section_id",
            "html_anchor",
            "xpath",
            "xbrl_fact_id",
            "fact_id",
            "quote",
        )
    )
    has_xbrl_locator = citation.get("xbrl_fact_id") not in (None, "") or (
        citation.get("xbrl_concept") not in (None, "")
        and citation.get("xbrl_context") not in (None, "")
    )
    return has_source and (has_locator or has_xbrl_locator)


def finalize_specialist_artifact(
    *,
    artifact_type: SpecialistArtifactType,
    company_id: str,
    source_report_path: str,
    output_path: str,
    html_url: str,
    citations: Sequence[Mapping[str, Any]],
    validation_result: SpecialistArtifactValidation,
    profile: str,
    message: str,
    session_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    specialist_facts: Mapping[str, Any] | None = None,
) -> SpecialistArtifactContract:
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    safe_citations = list(redact_audit_value(list(citations), max_string_length=1200))
    safe_metadata = dict(redact_audit_value(dict(metadata or {}), max_string_length=1200))
    audit_record: dict[str, Any] = {
        "schema_version": ANSWER_AUDIT_TRACE_SCHEMA,
        "created_at": created_at,
        "profile": profile,
        "session_id": session_id,
        "question_id": None,
        "message_hash": stable_hash(message),
        "message_preview": str(redact_audit_value(message, max_string_length=500)),
        "answer_hash": stable_hash({"output_path": output_path, "validation": validation_result.model_dump()}),
        "answer_preview": output_path,
        "resolved_company": {"company_id": company_id},
        "resolved_period": None,
        "query_plan": {"workflow": artifact_type},
        "wiki_facts": [],
        "postgres_facts": [],
        "legal_facts": [],
        "fallback_reason": None,
        "calculator_runs": [],
        "citations": safe_citations,
        "guardrail_result": {
            "evidence_contract_enforced": True,
            "citation_count": len(safe_citations),
            "validation_ok": validation_result.ok,
            "validation_failures": validation_result.failures,
        },
        "specialist_artifact": {
            "artifact_type": artifact_type,
            "company_id": company_id,
            "source_report_path": source_report_path,
            "output_path": output_path,
            "html_url": html_url,
            "validation_result": validation_result.model_dump(),
            "metadata": safe_metadata,
        },
    }
    if specialist_facts:
        audit_record.update(dict(redact_audit_value(dict(specialist_facts), max_string_length=1200)))
    recorded = record_answer_audit_trace(audit_record)
    return SpecialistArtifactContract(
        artifact_type=artifact_type,
        company_id=company_id,
        source_report_path=source_report_path,
        output_path=output_path,
        html_url=html_url,
        citations=safe_citations,
        validation_result=validation_result,
        audit_trace_id=str(recorded["trace_id"]),
        created_at=created_at,
        metadata=safe_metadata,
    )


def write_specialist_artifact_manifest(contract: SpecialistArtifactContract, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "SpecialistArtifactContract",
    "SpecialistArtifactValidation",
    "citation_has_locator",
    "finalize_specialist_artifact",
    "normalize_citations",
    "write_specialist_artifact_manifest",
]
