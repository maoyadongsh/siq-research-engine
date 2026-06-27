"""Schema extraction helpers for parsed document artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXTRACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "contract_terms_v1": {
        "template_id": "contract_terms_v1",
        "name": "合同条款",
        "description": "抽取合同主体、金额、期限和适用法律。",
        "instructions": "只从合同原文抽取字段；缺失字段返回 null，不要推断。",
        "schema": {
            "type": "object",
            "required": ["party_a", "party_b"],
            "properties": {
                "party_a": {"type": "string", "description": "甲方"},
                "party_b": {"type": "string", "description": "乙方"},
                "amount": {"type": "string", "description": "合同金额"},
                "term": {"type": "string", "description": "合同期限"},
                "effective_date": {"type": "string", "description": "生效日期"},
                "expiry_date": {"type": "string", "description": "到期日期"},
                "governing_law": {"type": "string", "description": "适用法律"},
            },
        },
        "aliases": {
            "party_a": ["party_a", "甲方", "委托方", "买方", "客户", "party a"],
            "party_b": ["party_b", "乙方", "受托方", "卖方", "供应商", "party b"],
            "amount": ["amount", "合同金额", "总金额", "价款", "金额"],
            "term": ["term", "合同期限", "服务期限", "期限"],
            "effective_date": ["effective_date", "生效日期", "生效时间"],
            "expiry_date": ["expiry_date", "到期日期", "终止日期"],
            "governing_law": ["governing_law", "适用法律", "管辖法律"],
        },
    },
    "research_report_summary_v1": {
        "template_id": "research_report_summary_v1",
        "name": "研报摘要",
        "description": "抽取研报标题、机构、核心观点和风险提示。",
        "instructions": "只抽取文中明确出现的信息；缺失字段返回 null。",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "标题"},
                "institution": {"type": "string", "description": "机构"},
                "target_company": {"type": "string", "description": "标的公司"},
                "rating": {"type": "string", "description": "评级"},
                "core_viewpoints": {"type": "array", "items": {"type": "string"}, "description": "核心观点"},
                "risk_warnings": {"type": "array", "items": {"type": "string"}, "description": "风险提示"},
            },
        },
        "aliases": {
            "title": ["title", "标题", "报告标题"],
            "institution": ["institution", "机构", "研究机构", "发布机构"],
            "target_company": ["target_company", "标的公司", "公司"],
            "rating": ["rating", "评级", "投资评级"],
            "core_viewpoints": ["core_viewpoints", "核心观点", "投资要点", "主要观点"],
            "risk_warnings": ["risk_warnings", "风险提示", "风险因素"],
        },
    },
    "invoice_basic_v1": {
        "template_id": "invoice_basic_v1",
        "name": "发票基础信息",
        "description": "抽取发票号码、购销方、日期和金额。",
        "instructions": "按发票原文抽取；未出现的字段返回 null。",
        "schema": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string", "description": "发票号码"},
                "seller": {"type": "string", "description": "销售方"},
                "buyer": {"type": "string", "description": "购买方"},
                "invoice_date": {"type": "string", "description": "开票日期"},
                "total_amount": {"type": "string", "description": "价税合计"},
                "tax_amount": {"type": "string", "description": "税额"},
            },
        },
        "aliases": {
            "invoice_number": ["invoice_number", "发票号码", "票据号码"],
            "seller": ["seller", "销售方", "销方", "卖方"],
            "buyer": ["buyer", "购买方", "购方", "买方"],
            "invoice_date": ["invoice_date", "开票日期", "日期"],
            "total_amount": ["total_amount", "价税合计", "合计金额", "总金额"],
            "tax_amount": ["tax_amount", "税额", "合计税额"],
        },
    },
    "meeting_minutes_v1": {
        "template_id": "meeting_minutes_v1",
        "name": "会议纪要",
        "description": "抽取会议标题、日期、参会人、结论和待办。",
        "instructions": "仅整理纪要中明确列出的事项；缺失字段返回 null。",
        "schema": {
            "type": "object",
            "properties": {
                "meeting_title": {"type": "string", "description": "会议标题"},
                "date": {"type": "string", "description": "会议日期"},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "参会人"},
                "decisions": {"type": "array", "items": {"type": "string"}, "description": "会议结论"},
                "action_items": {"type": "array", "items": {"type": "string"}, "description": "待办事项"},
            },
        },
        "aliases": {
            "meeting_title": ["meeting_title", "会议标题", "会议名称", "主题"],
            "date": ["date", "会议日期", "日期", "时间"],
            "attendees": ["attendees", "参会人", "参会人员", "与会人员"],
            "decisions": ["decisions", "会议结论", "决议", "结论"],
            "action_items": ["action_items", "待办事项", "行动项", "下一步"],
        },
    },
    "policy_document_v1": {
        "template_id": "policy_document_v1",
        "name": "政策文件",
        "description": "抽取政策标题、发布主体、生效日期、适用范围和义务。",
        "instructions": "按政策原文抽取，未明确出现的字段返回 null。",
        "schema": {
            "type": "object",
            "properties": {
                "policy_title": {"type": "string", "description": "政策标题"},
                "issuing_body": {"type": "string", "description": "发布主体"},
                "effective_date": {"type": "string", "description": "生效日期"},
                "scope": {"type": "string", "description": "适用范围"},
                "obligations": {"type": "array", "items": {"type": "string"}, "description": "主要义务"},
                "penalties": {"type": "array", "items": {"type": "string"}, "description": "处罚或责任"},
            },
        },
        "aliases": {
            "policy_title": ["policy_title", "政策标题", "标题", "文件名称"],
            "issuing_body": ["issuing_body", "发布主体", "发布单位", "发文机关"],
            "effective_date": ["effective_date", "生效日期", "施行日期"],
            "scope": ["scope", "适用范围", "适用对象", "范围"],
            "obligations": ["obligations", "主要义务", "要求", "义务"],
            "penalties": ["penalties", "处罚", "法律责任", "责任"],
        },
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def list_extraction_templates() -> list[dict[str, Any]]:
    return [
        {
            "template_id": template["template_id"],
            "name": template["name"],
            "description": template["description"],
            "instructions": template["instructions"],
            "schema": template["schema"],
        }
        for template in EXTRACTION_TEMPLATES.values()
    ]


def run_extraction(task_id: str, result_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    schema, template_id, template, instructions = _resolve_schema(payload)
    blocks = (read_json(result_dir / "blocks.json", {}) or {}).get("blocks") or []
    source_map = (read_json(result_dir / "source_map.json", {}) or {}).get("sources") or []
    markdown = (result_dir / "document.md").read_text(encoding="utf-8") if (result_dir / "document.md").exists() else ""
    no_cache = _truthy(payload.get("no_cache", payload.get("noCache", payload.get("force"))))
    cache_key = _cache_key(schema, template_id, instructions, markdown)
    cache_path = result_dir / "extraction" / "cache" / f"{cache_key}.json"
    if cache_path.exists() and not no_cache:
        cached = read_json(cache_path, {}) or {}
        cached["cached"] = True
        _write_extraction_outputs(result_dir, cached)
        _refresh_full_zip(result_dir)
        return cached

    schema_valid, schema_warnings = _validate_schema(schema)
    properties = schema.get("properties") if isinstance(schema, dict) and isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    source_by_block = {str(item.get("block_id") or ""): item for item in source_map if item.get("block_id")}

    result: dict[str, Any] = {}
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for field_name, field_schema in properties.items():
        value, evidence = _extract_field(str(field_name), field_schema if isinstance(field_schema, dict) else {}, blocks, source_by_block, template)
        result[str(field_name)] = value
        evidence_map[str(field_name)] = evidence

    field_reports = {
        field: {
            "status": "found" if value is not None else "missing",
            "required": field in required,
            "expected_type": _field_type(properties.get(field) if isinstance(properties.get(field), dict) else {}),
            "evidence_count": len(evidence_map.get(field) or []),
        }
        for field, value in result.items()
    }
    missing_fields = [field for field, value in result.items() if value is None]
    missing_required_fields = [field for field in missing_fields if field in required]
    non_null_fields = [field for field, value in result.items() if value is not None]
    non_null_with_evidence = [field for field in non_null_fields if evidence_map.get(field)]
    evidence_coverage_ratio = round(len(non_null_with_evidence) / len(non_null_fields), 4) if non_null_fields else 0.0

    warnings = [
        {
            "code": "rule_based_excerpt_only",
            "severity": "warning",
            "message": "当前抽取使用规则匹配，不调用 LLM；缺失字段保持 null。",
        }
    ]
    warnings.extend(schema_warnings)
    if missing_required_fields:
        warnings.append(
            {
                "code": "required_fields_missing",
                "severity": "warning",
                "message": "部分必填字段未在原文中找到。",
                "fields": missing_required_fields,
            }
        )

    extract_id = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
    validation_report = {
        "schema_version": "document_extraction_validation_v1",
        "task_id": task_id,
        "extract_id": extract_id,
        "schema_valid": schema_valid,
        "template_id": template_id,
        "field_count": len(properties),
        "missing_fields": missing_fields,
        "missing_required_fields": missing_required_fields,
        "field_reports": field_reports,
        "evidence_coverage_ratio": evidence_coverage_ratio,
        "no_hallucination_policy": "missing_fields_return_null",
        "warnings": warnings,
    }
    response = {
        "schema_version": "document_extraction_run_v1",
        "task_id": task_id,
        "extract_id": extract_id,
        "template_id": template_id,
        "template_name": template.get("name", "") if template else "",
        "status": "completed",
        "cached": False,
        "mode": payload.get("mode") or ("template" if template_id else "schema"),
        "instructions": instructions,
        "schema": schema,
        "result": result,
        "evidence_map": evidence_map,
        "validation_report": validation_report,
        "created_at": now_iso(),
    }
    write_json(cache_path, response)
    _write_extraction_outputs(result_dir, response)
    _refresh_full_zip(result_dir)
    return response


def _resolve_schema(payload: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    template_id = str(payload.get("template_id") or payload.get("templateId") or "").strip()
    template = EXTRACTION_TEMPLATES.get(template_id, {}) if template_id else {}
    schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    if not schema and template:
        schema = template.get("schema") or {}
    instructions = str(payload.get("instructions") or template.get("instructions") or "只从原文抽取，不确定则返回 null。")
    return schema, template_id if template else "", template, instructions


def _validate_schema(schema: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    warnings = []
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        warnings.append({"code": "unsupported_schema_type", "severity": "error", "message": "仅支持 object JSON Schema。"})
        return False, warnings
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        warnings.append({"code": "missing_schema_properties", "severity": "error", "message": "JSON Schema 需要包含 properties。"})
        return False, warnings
    return True, warnings


def _extract_field(
    field_name: str,
    field_schema: dict[str, Any],
    blocks: list[dict[str, Any]],
    source_by_block: dict[str, dict[str, Any]],
    template: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]]]:
    aliases = _field_aliases(field_name, field_schema, template)
    field_type = _field_type(field_schema)
    for block in blocks:
        text = _clean_markdown(str(block.get("text") or block.get("markdown") or ""))
        if not text.strip():
            continue
        for alias in aliases:
            value, quote = _match_labeled_value(alias, text)
            if value is None:
                continue
            normalized_value = _coerce_value(value, field_type)
            evidence = [_evidence_for(block, source_by_block, quote, field_name)]
            return normalized_value, evidence

    if field_name in {"title", "document_title", "policy_title", "meeting_title"}:
        for block in blocks:
            if str(block.get("type") or "") in {"title", "heading"}:
                text = _clean_markdown(str(block.get("text") or block.get("markdown") or "")).strip()
                if text:
                    return _coerce_value(text, field_type), [_evidence_for(block, source_by_block, text, field_name)]
    return None, []


def _field_aliases(field_name: str, field_schema: dict[str, Any], template: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    template_aliases = (template.get("aliases") or {}).get(field_name) if template else None
    if isinstance(template_aliases, list):
        aliases.extend(str(item) for item in template_aliases if item)
    aliases.append(field_name)
    aliases.append(field_name.replace("_", " "))
    description = field_schema.get("description")
    if description:
        aliases.append(str(description))
    title = field_schema.get("title")
    if title:
        aliases.append(str(title))
    seen = set()
    result = []
    for alias in aliases:
        key = alias.strip().lower()
        if alias.strip() and key not in seen:
            seen.add(key)
            result.append(alias.strip())
    return result


def _match_labeled_value(alias: str, text: str) -> tuple[str | None, str]:
    pattern = re.compile(rf"(?im)^\s*(?:[-*]\s*)?{re.escape(alias)}\s*[:：=]\s*(.+?)\s*$")
    match = pattern.search(text)
    if not match:
        return None, ""
    value = match.group(1).strip(" \t|")
    return (value or None), match.group(0).strip()


def _coerce_value(value: str, field_type: str) -> Any:
    cleaned = value.strip()
    if not cleaned:
        return None
    if field_type == "array":
        items = [item.strip(" \t-•") for item in re.split(r"[;；、,\n]+", cleaned) if item.strip(" \t-•")]
        return items or None
    if field_type in {"number", "integer"}:
        number = re.sub(r"[^0-9.\-]", "", cleaned)
        if not number:
            return cleaned
        try:
            return int(number) if field_type == "integer" else float(number)
        except ValueError:
            return cleaned
    if field_type == "boolean":
        lowered = cleaned.lower()
        if lowered in {"true", "yes", "1", "是"}:
            return True
        if lowered in {"false", "no", "0", "否"}:
            return False
    return cleaned


def _field_type(field_schema: dict[str, Any]) -> str:
    raw = field_schema.get("type") if isinstance(field_schema, dict) else "string"
    if isinstance(raw, list):
        raw = next((item for item in raw if item != "null"), "string")
    return str(raw or "string")


def _evidence_for(block: dict[str, Any], source_by_block: dict[str, dict[str, Any]], quote: str, field_name: str) -> dict[str, Any]:
    block_id = str(block.get("block_id") or "")
    source_entry = source_by_block.get(block_id) or {}
    source_ref = block.get("source_ref") or {}
    return {
        "field": field_name,
        "evidence_id": source_entry.get("evidence_id") or source_ref.get("evidence_id") or "",
        "block_id": block_id,
        "page_number": block.get("page_number") or source_entry.get("page_number") or 1,
        "bbox": block.get("bbox") or source_entry.get("bbox") or [],
        "quote": quote[:320],
        "open_source_url": source_entry.get("open_source_url") or "",
        "open_artifact_url": source_entry.get("open_artifact_url") or "",
    }


def _clean_markdown(text: str) -> str:
    text = re.sub(r"<!--\s*DOC_BLOCK:.*?-->", "", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _cache_key(schema: dict[str, Any], template_id: str, instructions: str, markdown: str) -> str:
    payload = {
        "schema": schema,
        "template_id": template_id,
        "instructions": instructions,
        "document_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_extraction_outputs(result_dir: Path, response: dict[str, Any]) -> None:
    extraction_dir = result_dir / "extraction"
    write_json(
        extraction_dir / "schema.json",
        {
            "schema_version": "document_extraction_schema_v1",
            "task_id": response.get("task_id"),
            "extract_id": response.get("extract_id"),
            "template_id": response.get("template_id", ""),
            "instructions": response.get("instructions", ""),
            "schema": response.get("schema") or {},
        },
    )
    write_json(
        extraction_dir / "result.json",
        {
            "schema_version": "document_extraction_result_v1",
            "task_id": response.get("task_id"),
            "extract_id": response.get("extract_id"),
            "template_id": response.get("template_id", ""),
            "status": response.get("status", "completed"),
            "cached": response.get("cached", False),
            "result": response.get("result") or {},
            "created_at": response.get("created_at"),
        },
    )
    write_json(
        extraction_dir / "evidence_map.json",
        {
            "schema_version": "document_extraction_evidence_v1",
            "task_id": response.get("task_id"),
            "extract_id": response.get("extract_id"),
            "evidence_map": response.get("evidence_map") or {},
        },
    )
    write_json(extraction_dir / "validation_report.json", response.get("validation_report") or {})


def _refresh_full_zip(result_dir: Path) -> None:
    zip_path = result_dir / "exports" / "full.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in result_dir.rglob("*"):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(result_dir).as_posix())


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
