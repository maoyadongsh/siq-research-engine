"""Versioned derived-agent artifact sidecar contract."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from .research_target import ContractValidationError, ResearchTargetV1

AGENT_ARTIFACT_SCHEMA_VERSION = "siq_agent_artifact_v2"
ARTIFACT_TYPES = frozenset({"analysis", "factcheck", "tracking"})
ARTIFACT_STATUSES = frozenset({"completed", "degraded", "failed", "legacy_unbound"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HASH = re.compile(r"^(?:sha256:)?[a-fA-F0-9]{64}$")


def _text(value: Any, field_name: str, *, required: bool = True) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise ContractValidationError(f"agent_artifact.{field_name} is required")
    return text or None


@dataclass(frozen=True)
class ArtifactQuality:
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in {"pass", "warning", "fail", "unknown"}:
            raise ContractValidationError("agent_artifact.quality.status is invalid")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "warnings", tuple(str(item).strip() for item in self.warnings if str(item).strip()))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArtifactQuality":
        return cls(status=payload.get("status", "unknown"), warnings=tuple(payload.get("warnings") or ()))


@dataclass(frozen=True)
class EvidenceSummary:
    citation_count: int = 0
    unresolved_count: int = 0

    def __post_init__(self) -> None:
        for name in ("citation_count", "unresolved_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) < 0:
                raise ContractValidationError(f"agent_artifact.evidence_summary.{name} must be non-negative")
            object.__setattr__(self, name, int(value))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceSummary":
        return cls(
            citation_count=payload.get("citation_count", 0),
            unresolved_count=payload.get("unresolved_count", 0),
        )


@dataclass(frozen=True)
class AgentArtifactV2:
    artifact_id: str
    artifact_type: str
    status: str
    created_at: str
    research_target: ResearchTargetV1 | None
    source_report_id: str | None
    source_family: str | None
    adapter_version: str | None
    upstream_artifact_ids: tuple[str, ...]
    html_file: str
    content_hash: str | None
    quality: ArtifactQuality
    evidence_summary: EvidenceSummary
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = AGENT_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_ARTIFACT_SCHEMA_VERSION:
            raise ContractValidationError(f"unsupported agent artifact schema: {self.schema_version}")
        artifact_id = _text(self.artifact_id, "artifact_id")
        if not _SAFE_ID.fullmatch(artifact_id or ""):
            raise ContractValidationError("agent_artifact.artifact_id is unsafe")
        object.__setattr__(self, "artifact_id", artifact_id)
        artifact_type = str(self.artifact_type or "").strip().lower()
        if artifact_type not in ARTIFACT_TYPES:
            raise ContractValidationError("agent_artifact.artifact_type is invalid")
        object.__setattr__(self, "artifact_type", artifact_type)
        status = str(self.status or "").strip().lower()
        if status not in ARTIFACT_STATUSES:
            raise ContractValidationError("agent_artifact.status is invalid")
        object.__setattr__(self, "status", status)
        created_at = _text(self.created_at, "created_at")
        try:
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractValidationError("agent_artifact.created_at must be ISO-8601") from exc
        if parsed_created_at.tzinfo is None:
            raise ContractValidationError("agent_artifact.created_at must include a timezone")
        object.__setattr__(self, "created_at", created_at)
        html_file = _text(self.html_file, "html_file")
        if "/" in html_file or "\\" in html_file or not html_file.lower().endswith(".html"):
            raise ContractValidationError("agent_artifact.html_file must be a basename ending in .html")
        object.__setattr__(self, "html_file", html_file)

        if status == "legacy_unbound":
            if self.research_target is not None:
                raise ContractValidationError("legacy_unbound artifact cannot claim a research target")
        else:
            if self.research_target is None:
                raise ContractValidationError("agent_artifact.research_target is required")
            if not isinstance(self.research_target, ResearchTargetV1):
                object.__setattr__(self, "research_target", ResearchTargetV1.from_dict(self.research_target))
            source_report_id = _text(self.source_report_id, "source_report_id")
            if source_report_id != self.research_target.source_report.report_id:
                raise ContractValidationError("agent_artifact source report identity mismatch")
            object.__setattr__(self, "source_report_id", source_report_id)
            object.__setattr__(self, "source_family", _text(self.source_family, "source_family"))
            if self.source_family != self.research_target.source_report.source_family:
                raise ContractValidationError("agent_artifact source family mismatch")
            object.__setattr__(self, "adapter_version", _text(self.adapter_version, "adapter_version"))
            if html_file != f"{artifact_id}.html":
                raise ContractValidationError("agent_artifact.html_file must match artifact_id")

        upstream = tuple(str(item or "").strip() for item in self.upstream_artifact_ids)
        if any(not _SAFE_ID.fullmatch(item) for item in upstream):
            raise ContractValidationError("agent_artifact upstream artifact id is unsafe")
        if artifact_type in {"factcheck", "tracking"} and status != "legacy_unbound" and not upstream:
            raise ContractValidationError("downstream artifact must bind an upstream analysis artifact")
        object.__setattr__(self, "upstream_artifact_ids", upstream)
        content_hash = _text(self.content_hash, "content_hash", required=status in {"completed", "degraded"})
        if content_hash and not _HASH.fullmatch(content_hash):
            raise ContractValidationError("agent_artifact.content_hash must be SHA-256")
        object.__setattr__(self, "content_hash", content_hash)
        if not isinstance(self.quality, ArtifactQuality):
            object.__setattr__(self, "quality", ArtifactQuality.from_dict(self.quality or {}))
        if not isinstance(self.evidence_summary, EvidenceSummary):
            object.__setattr__(
                self,
                "evidence_summary",
                EvidenceSummary.from_dict(self.evidence_summary or {}),
            )
        if not isinstance(self.metadata, Mapping):
            raise ContractValidationError("agent_artifact.metadata must be an object")
        try:
            json.dumps(self.metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("agent_artifact.metadata must be JSON serializable") from exc
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentArtifactV2":
        if not isinstance(payload, Mapping):
            raise ContractValidationError("agent artifact must be an object")
        schema = payload.get("schema_version")
        if schema != AGENT_ARTIFACT_SCHEMA_VERSION:
            artifact_type = str(payload.get("artifact_type") or "").strip().lower()
            raw_html = str(payload.get("html_file") or payload.get("html_url") or "").strip()
            html_file = PurePosixPath(raw_html.split("?", 1)[0]).name
            artifact_id = str(payload.get("artifact_id") or PurePosixPath(html_file).stem).strip()
            if artifact_id and not _SAFE_ID.fullmatch(artifact_id):
                artifact_id = "legacy_" + hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()[:32]
            created_at = str(payload.get("created_at") or "").strip()
            if artifact_type in ARTIFACT_TYPES and html_file.endswith(".html") and artifact_id and created_at:
                return cls.legacy_unbound(
                    artifact_id=artifact_id,
                    artifact_type=artifact_type,
                    html_file=html_file,
                    created_at=created_at,
                )
            raise ContractValidationError(f"unsupported agent artifact schema: {schema}")
        target = payload.get("research_target")
        return cls(
            schema_version=schema,
            artifact_id=payload.get("artifact_id", ""),
            artifact_type=payload.get("artifact_type", ""),
            status=payload.get("status", ""),
            created_at=payload.get("created_at", ""),
            research_target=ResearchTargetV1.from_dict(target) if isinstance(target, Mapping) else None,
            source_report_id=payload.get("source_report_id"),
            source_family=payload.get("source_family"),
            adapter_version=payload.get("adapter_version"),
            upstream_artifact_ids=tuple(payload.get("upstream_artifact_ids") or ()),
            html_file=payload.get("html_file", ""),
            content_hash=payload.get("content_hash"),
            quality=ArtifactQuality.from_dict(payload.get("quality") or {}),
            evidence_summary=EvidenceSummary.from_dict(payload.get("evidence_summary") or {}),
            metadata=payload.get("metadata") or {},
        )

    @classmethod
    def legacy_unbound(
        cls,
        *,
        artifact_id: str,
        artifact_type: str,
        html_file: str,
        created_at: str,
    ) -> "AgentArtifactV2":
        return cls(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            status="legacy_unbound",
            created_at=created_at,
            research_target=None,
            source_report_id=None,
            source_family=None,
            adapter_version=None,
            upstream_artifact_ids=(),
            html_file=html_file,
            content_hash=None,
            quality=ArtifactQuality(status="unknown"),
            evidence_summary=EvidenceSummary(),
        )

    @property
    def identity_status(self) -> str:
        return "legacy_unbound" if self.research_target is None else "exact"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["research_target"] = self.research_target.to_dict() if self.research_target else None
        payload["upstream_artifact_ids"] = list(self.upstream_artifact_ids)
        payload["quality"] = asdict(self.quality)
        payload["quality"]["warnings"] = list(self.quality.warnings)
        payload["evidence_summary"] = asdict(self.evidence_summary)
        payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "AGENT_ARTIFACT_SCHEMA_VERSION",
    "ARTIFACT_STATUSES",
    "ARTIFACT_TYPES",
    "AgentArtifactV2",
    "ArtifactQuality",
    "EvidenceSummary",
]
