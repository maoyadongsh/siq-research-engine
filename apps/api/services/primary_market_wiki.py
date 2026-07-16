"""Deal-scoped logical Wiki catalog for primary-market research projects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator

from services import deal_phase_artifacts, deal_store, ic_policy
from services.path_config import DOCUMENT_PARSER_RESULTS_ROOT

PRIMARY_MARKET_WIKI_SCHEMA = "siq_primary_market_wiki_tree_v1"
PRIMARY_MARKET_COMPANY_WIKI_SCHEMA = "siq_primary_market_company_wiki_v1"
WIKI_ROOT_DIR = "wiki"
CATALOG_PATH = f"{WIKI_ROOT_DIR}/wiki_tree.json"
README_PATH = f"{WIKI_ROOT_DIR}/README.md"
COMPANY_WIKI_ROOT = f"{WIKI_ROOT_DIR}/company"
COMPANY_WIKI_MATERIALS_ROOT = f"{COMPANY_WIKI_ROOT}/materials"
COMPANY_WIKI_INDEX_PATH = f"{COMPANY_WIKI_ROOT}/index.json"

MATERIAL_CATEGORY_ALIASES: dict[str, str] = {
    "bp": "teaser_bp",
    "business_plan": "teaser_bp",
    "business-plan": "teaser_bp",
    "teaser": "teaser_bp",
    "pitch_deck": "teaser_bp",
    "financial_model": "finance",
    "financial": "finance",
    "finance": "finance",
    "audit_report": "finance",
    "financial_statement": "finance",
    "legal": "legal",
    "legal_doc": "legal",
    "legal_document": "legal",
    "contract": "legal",
    "industry": "industry",
    "industry_report": "industry",
    "market_research": "industry",
    "interview": "interviews",
    "interview_note": "interviews",
    "meeting_note": "interviews",
    "minutes": "interviews",
    "prospectus": "prospectus",
    "ipo_prospectus": "prospectus",
    "other": "other",
    "": "other",
}

ROLE_DIRS: dict[str, str] = {
    "siq_ic_master_coordinator": "master_coordinator",
    "siq_ic_chairman": "chairman",
    "siq_ic_strategist": "strategy",
    "siq_ic_sector_expert": "sector",
    "siq_ic_finance_auditor": "finance",
    "siq_ic_legal_scanner": "legal",
    "siq_ic_risk_controller": "risk",
}

PHASE_LOGICAL_DIRS: dict[str, str] = {
    "R0": "company/research/r0",
    "R1": "company/research/r1",
    "R1.5": "company/research/r1_5",
    "R2": "company/research/r2",
    "R3": "company/research/r3",
    "R4": "company/decision",
}


def normalize_material_category(document_type: str | None) -> str:
    value = str(document_type or "").strip().lower().replace(" ", "_")
    return MATERIAL_CATEGORY_ALIASES.get(value, "other")


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    deal_store.ensure_deal_package_dirs(package_dir)
    return package_dir


def _safe_relative_path(value: Any) -> str | None:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = path.as_posix()
    lowered = normalized.lower()
    if lowered.startswith("data/wiki/companies/") or "/secondary_market/" in lowered:
        return None
    return normalized


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_source_path(
    package_dir: Path,
    source_path: Path,
    *,
    parse_task_id: str | None,
) -> str:
    resolved = source_path.resolve()
    try:
        return resolved.relative_to(package_dir.resolve()).as_posix()
    except ValueError as exc:
        if not parse_task_id:
            raise ValueError("parser_source_requires_bound_task") from exc
        task_root = (DOCUMENT_PARSER_RESULTS_ROOT / parse_task_id).resolve()
        relative = resolved.relative_to(task_root).as_posix()
        return f"parser_results/{parse_task_id}/{relative}"


def _source_artifacts(
    *,
    parsed_source: Path | None,
    structured_dir: Path | None,
    structured_used: bool,
) -> list[Path]:
    if structured_used and structured_dir is not None:
        paths = [
            structured_dir / name
            for name in (
                "content_list_enhanced.json",
                "content_list.json",
                "blocks.json",
                "archive_manifest.json",
                "artifact_manifest.json",
                "result_manifest.json",
                "manifest.json",
            )
            if (structured_dir / name).is_file()
        ]
        if paths:
            return paths
    return [parsed_source] if parsed_source is not None and parsed_source.is_file() else []


def _source_artifact_digest(paths: list[Path]) -> str:
    payload = [
        {"name": path.name, "sha256": _sha256_bytes(path.read_bytes())}
        for path in sorted(paths, key=lambda item: item.name)
    ]
    return _sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _validated_projection_source(
    path: Path | str,
    *,
    package_dir: Path,
    document_id: str,
    parse_task_id: str | None,
) -> Path:
    resolved = Path(path).resolve()
    deal_archive_root = (package_dir / "parsed_documents" / document_id).resolve()
    try:
        resolved.relative_to(deal_archive_root)
        return resolved
    except ValueError:
        pass
    if not parse_task_id:
        raise ValueError("parser_source_requires_bound_task")
    parser_root = (DOCUMENT_PARSER_RESULTS_ROOT / parse_task_id).resolve()
    try:
        resolved.relative_to(parser_root)
    except ValueError as exc:
        raise ValueError("parser_source_outside_material_namespace") from exc
    return resolved


def _structured_blocks(directory: Path) -> list[dict[str, Any]]:
    for name in ("content_list_enhanced.json", "content_list.json", "blocks.json"):
        payload = deal_store.read_json(directory / name, None)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            continue
        for key in ("blocks", "items", "content_list"):
            items = payload.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        pages = payload.get("pages")
        if isinstance(pages, list):
            blocks: list[dict[str, Any]] = []
            for page in pages:
                if not isinstance(page, dict):
                    continue
                page_no = page.get("page") or page.get("page_number") or page.get("page_idx")
                for item in page.get("blocks") or page.get("items") or []:
                    if isinstance(item, dict):
                        blocks.append({"_page": page_no, **item})
            if blocks:
                return blocks
    return []


def _block_value(block: dict[str, Any], *keys: str) -> Any:
    return next((block.get(key) for key in keys if block.get(key) not in (None, "")), None)


def _structured_markdown(directory: Path) -> str:
    sections: list[str] = []
    for index, block in enumerate(_structured_blocks(directory), start=1):
        text = str(_block_value(block, "text", "content", "markdown", "body") or "").strip()
        if not text:
            continue
        raw_page = _block_value(block, "page", "page_number", "page_no", "page_idx", "_page")
        try:
            page = int(raw_page) if raw_page is not None else None
        except (TypeError, ValueError):
            page = None
        if "page_idx" in block and page is not None and page >= 0:
            page += 1
        raw_id = str(_block_value(block, "block_id", "id") or f"block-{index}")
        block_id = re.sub(r"[^A-Za-z0-9_.:-]", "-", raw_id).strip("-") or f"block-{index}"
        attrs: list[str] = []
        if page is not None:
            attrs.append(f"page={page}")
        bbox = block.get("bbox")
        if isinstance(bbox, list) and bbox:
            attrs.append("bbox=" + ",".join(str(value) for value in bbox))
        source_ref = block.get("source_ref") if isinstance(block.get("source_ref"), dict) else {}
        evidence_id = re.sub(
            r"[^A-Za-z0-9_.:/-]",
            "-",
            str(source_ref.get("evidence_id") or "").strip(),
        ).replace("--", "-")
        if evidence_id:
            attrs.append(f"evidence={evidence_id}")
        suffix = f" {' '.join(attrs)}" if attrs else ""
        sections.append(f"<!-- DOC_BLOCK: {block_id}{suffix} -->\n{text}")
    return "\n\n".join(sections).strip()


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def project_material_to_company_wiki(
    deal_id: str,
    document_id: str,
    *,
    source_path: Path | str | None = None,
    structured_artifact_dir: Path | str | None = None,
    parse_task_id: str | None = None,
    parse_run_id: str | None = None,
    wiki_root: Path | str | None = None,
    projected_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote parser output into the Deal-only company Wiki namespace."""

    from services import deal_documents

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_document_id = deal_documents.validate_document_id(document_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    metadata_path = package_dir / "data_room" / "metadata" / f"{normalized_document_id}.json"
    metadata = deal_store.read_json(metadata_path, None)
    if (
        not isinstance(metadata, dict)
        or str(metadata.get("document_id") or "") != normalized_document_id
        or str(metadata.get("deal_id") or "") != normalized_deal_id
    ):
        raise FileNotFoundError(normalized_document_id)

    bound_task_id = str(parse_task_id or metadata.get("parse_task_id") or "").strip() or None
    if bound_task_id:
        bound_task_id = deal_documents.validate_parser_task_id(bound_task_id)
        metadata_task_id = str(metadata.get("parse_task_id") or "").strip()
        if metadata_task_id and metadata_task_id != bound_task_id:
            raise ValueError("parser_task_mismatch")

    parsed_source = (
        _validated_projection_source(
            source_path,
            package_dir=package_dir,
            document_id=normalized_document_id,
            parse_task_id=bound_task_id,
        )
        if source_path is not None
        else None
    )
    structured_dir = (
        _validated_projection_source(
            structured_artifact_dir,
            package_dir=package_dir,
            document_id=normalized_document_id,
            parse_task_id=bound_task_id,
        )
        if structured_artifact_dir is not None
        else parsed_source.parent if parsed_source is not None else None
    )
    markdown = _structured_markdown(structured_dir) if structured_dir is not None else ""
    structured_used = bool(markdown)
    if not markdown and parsed_source is not None:
        if not parsed_source.is_file():
            raise FileNotFoundError(parsed_source)
        markdown = parsed_source.read_text(encoding="utf-8").strip()
    if not markdown:
        raise ValueError("parsed_material_has_no_wiki_text")

    category = normalize_material_category(metadata.get("document_type"))
    wiki_path = f"{COMPANY_WIKI_MATERIALS_ROOT}/{category}/{normalized_document_id}.md"
    if "data/wiki/companies" in wiki_path.lower():
        raise ValueError("primary_market_company_wiki_namespace_violation")
    content = (markdown.rstrip() + "\n").encode("utf-8")
    target = package_dir / wiki_path
    source_artifacts = _source_artifacts(
        parsed_source=parsed_source,
        structured_dir=structured_dir,
        structured_used=structured_used,
    )
    if not source_artifacts:
        raise ValueError("parsed_material_source_artifacts_missing")
    source_ref = _safe_source_path(
        package_dir,
        source_artifacts[0],
        parse_task_id=bound_task_id,
    )
    source_artifact_records = [
        {
            "path": _safe_source_path(
                package_dir,
                path,
                parse_task_id=bound_task_id,
            ),
            "sha256": _sha256_bytes(path.read_bytes()),
        }
        for path in source_artifacts
    ]
    projected_at = deal_store.utc_now_iso()
    projection = {
        "schema_version": PRIMARY_MARKET_COMPANY_WIKI_SCHEMA,
        "namespace": "primary_market",
        "deal_id": normalized_deal_id,
        "document_id": normalized_document_id,
        "document_type": metadata.get("document_type") or "other",
        "category": category,
        "wiki_path": wiki_path,
        "wiki_sha256": _sha256_bytes(content),
        "source_path": source_ref,
        "source_sha256": _source_artifact_digest(source_artifacts),
        "source_artifacts": source_artifact_records,
        "parse_task_id": bound_task_id,
        "parse_run_id": parse_run_id or metadata.get("current_parse_run_id"),
        "projected_at": projected_at,
        "projected_by": projected_by,
    }
    index_path = package_dir / COMPANY_WIKI_INDEX_PATH
    changed = True
    with deal_store._locked_path(package_dir / COMPANY_WIKI_ROOT / "index.operation"):
        metadata = deal_store.read_json(metadata_path, None)
        if (
            not isinstance(metadata, dict)
            or metadata.get("deal_id") != normalized_deal_id
            or metadata.get("document_id") != normalized_document_id
        ):
            raise FileNotFoundError(normalized_document_id)
        index = deal_store.read_json(index_path, {}) or {}
        entries = dict(index.get("documents")) if isinstance(index.get("documents"), dict) else {}
        previous = entries.get(normalized_document_id)
        target_hash = _sha256_bytes(target.read_bytes()) if target.is_file() else ""
        changed = not (
            isinstance(previous, dict)
            and previous.get("wiki_sha256") == projection["wiki_sha256"] == target_hash
            and previous.get("source_sha256") == projection["source_sha256"]
            and previous.get("parse_task_id") == projection["parse_task_id"]
            and previous.get("parse_run_id") == projection["parse_run_id"]
        )
        if not changed:
            projection = previous
        else:
            _atomic_write_bytes(target, content)
            entries[normalized_document_id] = projection
            deal_store.write_json(
                index_path,
                {
                    "schema_version": PRIMARY_MARKET_COMPANY_WIKI_SCHEMA,
                    "namespace": "primary_market",
                    "deal_id": normalized_deal_id,
                    "root": COMPANY_WIKI_ROOT,
                    "excluded_roots": ["data/wiki/companies"],
                    "updated_at": projected_at,
                    "documents": entries,
                },
            )
        metadata.update(
            {
                "wiki_status": "ready",
                "wiki_path": wiki_path,
                "wiki_sha256": projection["wiki_sha256"],
                "wiki_source_path": source_ref,
                "wiki_source_sha256": projection["source_sha256"],
                "wiki_projected_at": projection.get("projected_at") or projected_at,
                "wiki_error": None,
                "wiki_retryable": None,
            }
        )
        deal_store.write_json(metadata_path, metadata)
    deal_documents._sync_manifest_documents(package_dir)
    rebuild_primary_market_wiki(normalized_deal_id, wiki_root=wiki_root, append_audit=False)
    if changed:
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "primary_market_company_wiki_projected",
                "document_id": normalized_document_id,
                "category": category,
                "wiki_path": wiki_path,
                "wiki_sha256": projection["wiki_sha256"],
                "parse_task_id": projection["parse_task_id"],
                "parse_run_id": projection["parse_run_id"],
                "projected_by": projected_by,
            },
            wiki_root=wiki_root,
        )
    return deal_store.redact_public_payload(projection)


def record_company_wiki_failure(
    deal_id: str,
    document_id: str,
    error: Exception | str,
    *,
    parse_task_id: str | None = None,
    parse_run_id: str | None = None,
    wiki_root: Path | str | None = None,
    projected_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from services import deal_documents

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_document_id = deal_documents.validate_document_id(document_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    metadata_path = package_dir / "data_room" / "metadata" / f"{normalized_document_id}.json"
    metadata = deal_store.read_json(metadata_path, None)
    if not isinstance(metadata, dict):
        raise FileNotFoundError(normalized_document_id)
    created_at = deal_store.utc_now_iso()
    receipt = {
        "schema_version": PRIMARY_MARKET_COMPANY_WIKI_SCHEMA,
        "status": "failed",
        "retryable": True,
        "namespace": "primary_market",
        "deal_id": normalized_deal_id,
        "document_id": normalized_document_id,
        "parse_task_id": parse_task_id or metadata.get("parse_task_id"),
        "parse_run_id": parse_run_id or metadata.get("current_parse_run_id"),
        "error_type": type(error).__name__ if isinstance(error, Exception) else "WikiProjectionError",
        "error": str(error)[:500],
        "created_at": created_at,
        "projected_by": projected_by,
    }
    receipt_path = package_dir / COMPANY_WIKI_ROOT / "receipts" / f"{normalized_document_id}.json"
    with deal_store._locked_path(package_dir / COMPANY_WIKI_ROOT / "index.operation"):
        metadata = deal_store.read_json(metadata_path, None)
        if not isinstance(metadata, dict):
            raise FileNotFoundError(normalized_document_id)
        index = deal_store.read_json(package_dir / COMPANY_WIKI_INDEX_PATH, {}) or {}
        entries = index.get("documents") if isinstance(index.get("documents"), dict) else {}
        current = entries.get(normalized_document_id) if isinstance(entries, dict) else None
        if isinstance(current, dict) and metadata.get("wiki_status") == "ready":
            current_path = package_dir / str(current.get("wiki_path") or "")
            if current_path.is_file() and _sha256_bytes(current_path.read_bytes()) == current.get("wiki_sha256"):
                return deal_store.redact_public_payload(current)
        deal_store.write_json(receipt_path, receipt)
        metadata.update(
            {
                "wiki_status": "failed",
                "wiki_error": receipt["error"],
                "wiki_error_type": receipt["error_type"],
                "wiki_retryable": True,
                "wiki_receipt_path": deal_store.relative_path(receipt_path, package_dir),
                "wiki_updated_at": created_at,
            }
        )
        deal_store.write_json(metadata_path, metadata)
    deal_documents._sync_manifest_documents(package_dir)
    rebuild_primary_market_wiki(normalized_deal_id, wiki_root=wiki_root, append_audit=False)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "primary_market_company_wiki_projection_failed",
            "document_id": normalized_document_id,
            "parse_task_id": receipt["parse_task_id"],
            "parse_run_id": receipt["parse_run_id"],
            "error_type": receipt["error_type"],
            "projected_by": projected_by,
        },
        wiki_root=wiki_root,
    )
    return deal_store.redact_public_payload(receipt)


def project_material_to_company_wiki_safe(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return project_material_to_company_wiki(*args, **kwargs)
    except Exception as exc:
        deal_id = str(args[0] if args else kwargs.get("deal_id") or "")
        document_id = str(args[1] if len(args) > 1 else kwargs.get("document_id") or "")
        return record_company_wiki_failure(
            deal_id,
            document_id,
            exc,
            parse_task_id=kwargs.get("parse_task_id"),
            parse_run_id=kwargs.get("parse_run_id"),
            wiki_root=kwargs.get("wiki_root"),
            projected_by=kwargs.get("projected_by"),
        )


def remove_material_company_wiki(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    from services import deal_documents

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_document_id = deal_documents.validate_document_id(document_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    removed_path: str | None = None
    with deal_store._locked_path(package_dir / COMPANY_WIKI_ROOT / "index.operation"):
        index_path = package_dir / COMPANY_WIKI_INDEX_PATH
        index = deal_store.read_json(index_path, {}) or {}
        entries = dict(index.get("documents")) if isinstance(index.get("documents"), dict) else {}
        projection = entries.pop(normalized_document_id, None)
        if isinstance(projection, dict):
            candidate = _safe_relative_path(projection.get("wiki_path"))
            if candidate and candidate.startswith(f"{COMPANY_WIKI_MATERIALS_ROOT}/"):
                target = (package_dir / candidate).resolve()
                try:
                    target.relative_to((package_dir / COMPANY_WIKI_MATERIALS_ROOT).resolve())
                except ValueError:
                    target = package_dir / "__invalid__"
                target.unlink(missing_ok=True)
                removed_path = candidate
        deal_store.write_json(
            index_path,
            {
                "schema_version": PRIMARY_MARKET_COMPANY_WIKI_SCHEMA,
                "namespace": "primary_market",
                "deal_id": normalized_deal_id,
                "root": COMPANY_WIKI_ROOT,
                "excluded_roots": ["data/wiki/companies"],
                "updated_at": deal_store.utc_now_iso(),
                "documents": entries,
            },
        )
        (package_dir / COMPANY_WIKI_ROOT / "receipts" / f"{normalized_document_id}.json").unlink(
            missing_ok=True
        )
    return {"status": "removed", "document_id": normalized_document_id, "wiki_path": removed_path}


def _material_entries(package_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for metadata_path in sorted((package_dir / "data_room" / "metadata").glob("DOC-*.json")):
        metadata = deal_store.read_json(metadata_path, {}) or {}
        if not isinstance(metadata, dict):
            continue
        document_id = str(metadata.get("document_id") or metadata_path.stem)
        category = normalize_material_category(metadata.get("document_type"))
        entries.append(
            {
                "entry_type": "uploaded_material",
                "document_id": document_id,
                "category": category,
                "logical_directory": f"company/materials/{category}",
                "title": metadata.get("original_filename") or document_id,
                "document_type": metadata.get("document_type") or "other",
                "canonical_path": _safe_relative_path(metadata.get("storage_path")),
                "metadata_path": deal_store.relative_path(metadata_path, package_dir),
                "sha256": metadata.get("sha256"),
                "status": metadata.get("status"),
                "parse_task_id": metadata.get("parse_task_id"),
                "parsed_artifact_path": _safe_relative_path(metadata.get("parsed_artifact_path")),
            }
        )
    return entries


def _iter_artifact_refs(value: Any, *, producer: str | None = None) -> Iterator[tuple[str, str | None]]:
    if isinstance(value, dict):
        next_producer = str(value.get("agent_id") or value.get("profile_id") or producer or "").strip() or None
        if next_producer:
            canonical = ic_policy.canonical_ic_profile_id(next_producer)
            next_producer = canonical if canonical in ic_policy.IC_PROFILE_IDS else next_producer
        for key, item in value.items():
            if key in {"artifact_path", "markdown_path", "report_path", "json_path"}:
                path = _safe_relative_path(item)
                if path:
                    yield path, next_producer
            elif key == "artifact_paths" and isinstance(item, dict):
                for path_value in item.values():
                    path = _safe_relative_path(path_value)
                    if path:
                        yield path, next_producer
            else:
                yield from _iter_artifact_refs(item, producer=next_producer)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_artifact_refs(item, producer=producer)


def _phase_entries(package_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for spec in deal_phase_artifacts.PHASE_ARTIFACTS:
        phase = spec["phase"]
        artifact_schema = {
            "R0": "project_intake",
            "R1": "expert_report",
            "R1.5": "dispute_ruling",
            "R2": "opinion_revision",
            "R3": "red_blue_review",
            "R4": "ic_decision",
        }[phase]
        source_payload = deal_store.read_json(package_dir / spec["json_path"], {}) or {}
        refs: list[tuple[str, str | None]] = [
            (spec["json_path"], None),
            (spec["markdown_path"], None),
            *_iter_artifact_refs(source_payload),
        ]
        for raw_path, producer in refs:
            path = _safe_relative_path(raw_path)
            if not path or not (package_dir / path).is_file():
                continue
            key = (phase, path, producer)
            if key in seen:
                continue
            seen.add(key)
            role_dir = ROLE_DIRS.get(str(producer or ""))
            logical_directory = PHASE_LOGICAL_DIRS[phase]
            if phase == "R1" and role_dir:
                logical_directory = f"company/research/r1/{role_dir}"
            entries.append(
                {
                    "entry_type": "agent_artifact",
                    "phase": phase,
                    "artifact_schema": artifact_schema,
                    "producer_profile": producer,
                    "logical_directory": logical_directory,
                    "canonical_path": path,
                    "title": Path(path).name,
                }
            )
    return entries


def _evidence_entries(package_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name, kind in (
        ("evidence_snapshot.json", "snapshot"),
        ("evidence_index.json", "index"),
        ("evidence_items.ndjson", "items"),
        ("evidence_quality_report.json", "quality_report"),
    ):
        path = package_dir / "evidence" / name
        if path.is_file():
            entries.append(
                {
                    "entry_type": "evidence_artifact",
                    "artifact_schema": kind,
                    "logical_directory": "company/evidence",
                    "canonical_path": deal_store.relative_path(path, package_dir),
                    "title": name,
                }
            )
    return entries


def _company_wiki_entries(package_dir: Path) -> list[dict[str, Any]]:
    payload = deal_store.read_json(package_dir / COMPANY_WIKI_INDEX_PATH, {}) or {}
    documents = payload.get("documents") if isinstance(payload.get("documents"), dict) else {}
    entries: list[dict[str, Any]] = []
    for document_id, projection in sorted(documents.items()):
        if not isinstance(projection, dict):
            continue
        metadata = deal_store.read_json(
            package_dir / "data_room" / "metadata" / f"{document_id}.json",
            None,
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("deal_id") != package_dir.name
            or metadata.get("document_id") != document_id
        ):
            continue
        wiki_path = _safe_relative_path(projection.get("wiki_path"))
        if not wiki_path or not wiki_path.startswith(f"{COMPANY_WIKI_MATERIALS_ROOT}/"):
            continue
        if not (package_dir / wiki_path).is_file():
            continue
        entries.append(
            {
                "entry_type": "company_wiki_projection",
                "document_id": document_id,
                "category": projection.get("category"),
                "logical_directory": f"company/materials/{projection.get('category') or 'other'}",
                "canonical_path": wiki_path,
                "sha256": projection.get("wiki_sha256"),
                "source_path": _safe_relative_path(projection.get("source_path")),
                "parse_task_id": projection.get("parse_task_id"),
                "parse_run_id": projection.get("parse_run_id"),
                "title": Path(wiki_path).name,
            }
        )
    return entries


def _directory_paths(material_categories: list[str]) -> list[str]:
    paths = [
        "00_project",
        *[f"01_materials/{category}" for category in material_categories],
        "02_parsed",
        "company",
        "company/profile",
        *[f"company/materials/{category}" for category in material_categories],
        "company/evidence",
        "company/research/r0",
        *[f"company/research/r1/{role_dir}" for role_dir in ROLE_DIRS.values()],
        "company/research/r1_5",
        "company/research/r2",
        "company/research/r3",
        "company/decision",
        "company/post_investment",
        "company/audit",
        "10_evidence",
        "20_research/r0",
        *[f"20_research/r1/{role_dir}" for role_dir in ROLE_DIRS.values()],
        "20_research/r1_5",
        "20_research/r2",
        "20_research/r3",
        "30_decision",
        "40_post_investment",
        "90_audit",
    ]
    return list(dict.fromkeys(paths))


def _private_collection_bindings() -> dict[str, dict[str, str]]:
    matrix = ic_policy.read_ic_profile_matrix()
    profiles = matrix.get("profiles") if isinstance(matrix.get("profiles"), list) else []
    bindings: dict[str, dict[str, str]] = {}
    for item in profiles:
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id") or "")
        if profile_id not in ic_policy.IC_PROFILE_IDS:
            continue
        retrieval = item.get("retrieval") if isinstance(item.get("retrieval"), dict) else {}
        physical = str(retrieval.get("private_collection") or "")
        if not physical:
            physical_collections = retrieval.get("physical_collections") or []
            physical = next(
                (str(value) for value in physical_collections if str(value) != "ic_collaboration_shared"),
                profile_id,
            )
        bindings[profile_id] = {"logical": profile_id, "physical": physical}
    return bindings


def rebuild_primary_market_wiki(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    created_by: dict[str, Any] | None = None,
    append_audit: bool = True,
) -> dict[str, Any]:
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    materials = _material_entries(package_dir)
    material_categories = sorted({str(item["category"]) for item in materials}) or ["other"]
    directories = _directory_paths(material_categories)
    logical_root = package_dir / WIKI_ROOT_DIR
    for relative in directories:
        (logical_root / relative).mkdir(parents=True, exist_ok=True)

    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    evidence_snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    entries = [
        *materials,
        *_company_wiki_entries(package_dir),
        *_evidence_entries(package_dir),
        *_phase_entries(package_dir),
    ]
    payload: dict[str, Any] = {
        "schema_version": PRIMARY_MARKET_WIKI_SCHEMA,
        "namespace": "primary_market",
        "deal_id": normalized_deal_id,
        "company_name": project_meta.get("company_name"),
        "generated_at": deal_store.utc_now_iso(),
        "evidence_snapshot_hash": evidence_snapshot.get("snapshot_hash"),
        "collection_bindings": {
            "shared_logical": "siq_deal_shared",
            "shared_physical": "ic_collaboration_shared",
            "shared_project_tag": normalized_deal_id,
            "private_by_profile": _private_collection_bindings(),
        },
        "namespace_guard": {
            "allowed_package_root": f"data/wiki/deals/{normalized_deal_id}",
            "excluded_roots": ["data/wiki/companies"],
            "secondary_market_access": "denied",
        },
        "directories": directories,
        "material_categories": material_categories,
        "entries": entries,
        "counts": {
            "directories": len(directories),
            "materials": len(materials),
            "company_wiki_projections": sum(
                item["entry_type"] == "company_wiki_projection" for item in entries
            ),
            "evidence_artifacts": sum(item["entry_type"] == "evidence_artifact" for item in entries),
            "agent_artifacts": sum(item["entry_type"] == "agent_artifact" for item in entries),
        },
    }
    stable = json.dumps({key: value for key, value in payload.items() if key != "generated_at"}, ensure_ascii=False, sort_keys=True)
    payload["catalog_hash"] = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    deal_store.write_json(package_dir / CATALOG_PATH, payload)
    (package_dir / README_PATH).write_text(
        "# Primary Market Deal Wiki\n\n"
        "`company/materials/` contains Deal-scoped Markdown projections promoted from verified parser artifacts.\n"
        "`wiki_tree.json` catalogs uploaded materials, parsed company Wiki projections, Evidence, and agent artifacts.\n"
        "The catalog is strictly scoped to this Deal and must never resolve data/wiki/companies.\n",
        encoding="utf-8",
    )
    if append_audit:
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "primary_market_wiki_rebuilt",
                "catalog_path": CATALOG_PATH,
                "catalog_hash": payload["catalog_hash"],
                "counts": payload["counts"],
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
    return deal_store.redact_public_payload(payload)


def read_primary_market_wiki(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    payload = deal_store.read_json(package_dir / CATALOG_PATH, None)
    if not isinstance(payload, dict):
        return rebuild_primary_market_wiki(deal_id, wiki_root=wiki_root, append_audit=False)
    return deal_store.redact_public_payload(payload)
