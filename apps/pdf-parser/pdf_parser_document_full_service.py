import os
import re
from datetime import datetime, timezone

from financial_extractor import parse_html_table as financial_parse_html_table
from pdf_source_viewer import coerce_json_artifact, printed_page_numbers_by_pdf_page


def _coerce_bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return []
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return []
    return bbox


def _count_table_rows(table_html):
    return len(re.findall(r"<tr\b", table_html or "", flags=re.IGNORECASE))


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _table_body_column_count(table_html):
    try:
        grid = financial_parse_html_table(table_html)
    except Exception:
        grid = []
    return max((len(row) for row in grid), default=0)


def _table_relation_column_count(table):
    structure = table.get("structure") if isinstance(table.get("structure"), dict) else {}
    for value in (
        structure.get("expanded_columns"),
        structure.get("column_count"),
        table.get("column_count"),
    ):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _table_relation_row_count(table):
    structure = table.get("structure") if isinstance(table.get("structure"), dict) else {}
    for value in (
        table.get("rows"),
        structure.get("expanded_rows"),
        structure.get("row_count"),
    ):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _table_relation_title(table):
    return table.get("heading") or table.get("preview") or table.get("unit") or ""


def _table_relation_table_id(page_number, bbox, fallback):
    bbox_part = "-".join(str(int(round(float(item)))) for item in bbox)
    return f"pt-p{int(page_number or 1):04d}-{bbox_part or fallback}"


def _normalize_enhanced_table_for_relations(table, fallback_index=0):
    if not isinstance(table, dict):
        return None
    bbox = _coerce_bbox(table.get("bbox"))
    if not bbox:
        return None
    page_number = table.get("pdf_page_number") or table.get("page_number")
    try:
        page_number = int(page_number or 0)
    except (TypeError, ValueError):
        page_number = 0
    if page_number <= 0:
        return None
    table_index = table.get("table_index")
    source_id = table.get("content_table_source_id") or table.get("source_table_index")
    table_id = table.get("table_id") or f"pt-{int(table_index or fallback_index or 0):06d}"
    if not table_index:
        table_id = _table_relation_table_id(page_number, bbox, f"e{fallback_index}")
    row_count = _table_relation_row_count(table)
    column_count = _table_relation_column_count(table)
    title = _table_relation_title(table)
    return {
        "table_id": table_id,
        "table_index": table_index,
        "content_table_source_id": source_id,
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": table.get("printed_page_number"),
        "bbox": bbox,
        "title": title,
        "caption": title,
        "html": table.get("table_html") or table.get("html") or "",
        "markdown": table.get("markdown") or "",
        "text": table.get("preview") or "",
        "quality": {
            "row_count": row_count,
            "column_count": column_count,
        },
        "missing_body": bool(table.get("missing_body") or not (table.get("table_html") or table.get("html") or table.get("markdown") or table.get("preview"))),
        "source": table.get("source") or "enhanced_table",
    }


def _normalize_content_table_block_for_relations(item, table_ordinal, printed_pages):
    if not isinstance(item, dict) or item.get("type") != "table":
        return None
    bbox = _coerce_bbox(item.get("bbox"))
    if not bbox:
        return None
    page_idx = item.get("page_idx")
    if not isinstance(page_idx, int):
        return None
    page_number = page_idx + 1
    table_body = str(item.get("table_body") or "").strip()
    row_count = _count_table_rows(table_body) if table_body else 0
    column_count = _table_body_column_count(table_body) if table_body else 0
    return {
        "table_id": _table_relation_table_id(page_number, bbox, f"c{table_ordinal}"),
        "table_index": None,
        "content_table_source_id": table_ordinal if table_body else None,
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": printed_pages.get(page_number),
        "bbox": bbox,
        "title": "",
        "caption": "",
        "html": table_body,
        "markdown": "",
        "text": _strip_html(table_body) if table_body else "",
        "quality": {
            "row_count": row_count,
            "column_count": int(column_count or 0),
        },
        "missing_body": not bool(table_body),
        "source": "content_list_table_block",
    }


def _relation_merge_key(table):
    page_number = int(table.get("page_number") or 0)
    bbox = _coerce_bbox(table.get("bbox"))
    if bbox:
        return (page_number, tuple(round(value, 2) for value in bbox))
    return (page_number, table.get("table_id") or "")


def _merge_relation_table(existing, incoming):
    merged = dict(incoming)
    merged.update(existing)
    existing_quality = existing.get("quality") if isinstance(existing.get("quality"), dict) else {}
    incoming_quality = incoming.get("quality") if isinstance(incoming.get("quality"), dict) else {}
    merged["quality"] = {
        "row_count": existing_quality.get("row_count") or incoming_quality.get("row_count") or 0,
        "column_count": existing_quality.get("column_count") or incoming_quality.get("column_count") or 0,
    }
    merged["html"] = existing.get("html") or incoming.get("html") or ""
    merged["markdown"] = existing.get("markdown") or incoming.get("markdown") or ""
    merged["text"] = existing.get("text") or incoming.get("text") or ""
    merged["title"] = existing.get("title") or incoming.get("title") or ""
    merged["caption"] = existing.get("caption") or incoming.get("caption") or ""
    merged["table_index"] = existing.get("table_index") or incoming.get("table_index")
    merged["content_table_source_id"] = existing.get("content_table_source_id") or incoming.get("content_table_source_id")
    merged["missing_body"] = bool(existing.get("missing_body") and incoming.get("missing_body"))
    return merged


def relation_blocks_from_content_list(content_list):
    content_list = coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    blocks = []
    for index, item in enumerate(content_list, start=1):
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        page_number = int(page_idx) + 1 if isinstance(page_idx, int) else 1
        block_type = item.get("type") or "unknown"
        block = {
            "block_id": item.get("block_id") or f"pb-{index:06d}",
            "type": block_type,
            "page_number": page_number,
            "bbox": item.get("bbox") or [],
            "text": item.get("text") or "",
            "markdown": item.get("text") or "",
            "sub_type": item.get("sub_type") or "",
            "reading_order": index,
        }
        if block_type == "table":
            block["text"] = _strip_html(item.get("table_body") or "")
            block["markdown"] = block["text"]
        elif block_type == "list":
            block["text"] = " ".join(str(value or "") for value in item.get("list_items") or [])
            block["markdown"] = block["text"]
        blocks.append(block)
    return blocks


def relation_tables_from_artifacts(enhanced, content_list):
    enhanced = enhanced if isinstance(enhanced, dict) else {}
    merged = {}
    for index, table in enumerate(enhanced.get("tables") or [], start=1):
        normalized = _normalize_enhanced_table_for_relations(table, fallback_index=index)
        if not normalized:
            continue
        key = _relation_merge_key(normalized)
        merged[key] = _merge_relation_table(merged[key], normalized) if key in merged else normalized

    content_list = coerce_json_artifact(content_list)
    printed_pages = printed_page_numbers_by_pdf_page(content_list)
    table_ordinal = 0
    if isinstance(content_list, list):
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "table" and item.get("table_body"):
                table_ordinal += 1
            normalized = _normalize_content_table_block_for_relations(item, table_ordinal, printed_pages)
            if not normalized:
                continue
            key = _relation_merge_key(normalized)
            merged[key] = _merge_relation_table(merged[key], normalized) if key in merged else normalized

    return sorted(
        merged.values(),
        key=lambda item: (
            int(item.get("page_number") or 0),
            _coerce_bbox(item.get("bbox"))[1] if _coerce_bbox(item.get("bbox")) else 0,
            _coerce_bbox(item.get("bbox"))[0] if _coerce_bbox(item.get("bbox")) else 0,
        ),
    )


def augment_table_relations(relations_payload, relation_tables):
    table_by_id = {str(table.get("table_id") or ""): table for table in relation_tables}
    relations = relations_payload.get("relations") if isinstance(relations_payload, dict) else []
    if not isinstance(relations, list):
        return relations_payload
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        from_table = table_by_id.get(str(relation.get("from_table_id") or relation.get("source_table_id") or ""))
        to_table = table_by_id.get(str(relation.get("to_table_id") or relation.get("target_table_id") or ""))
        if from_table:
            relation["from_table_index"] = from_table.get("table_index")
            relation["from_bbox"] = from_table.get("bbox") or []
            relation["from_page_number"] = from_table.get("page_number")
        if to_table:
            relation["to_table_index"] = to_table.get("table_index")
            relation["to_bbox"] = to_table.get("bbox") or []
            relation["to_page_number"] = to_table.get("page_number")
    return relations_payload


def build_table_relations_artifact_payload(
    task,
    markdown,
    *,
    enhanced=None,
    content_list=None,
    build_table_relations,
    now_iso,
    table_relation_ruleset_version,
):
    task_id = task.get("task_id") or ""
    relation_tables = relation_tables_from_artifacts(enhanced if isinstance(enhanced, dict) else {}, content_list)
    blocks = relation_blocks_from_content_list(content_list)
    payload = build_table_relations(task_id, relation_tables, blocks=blocks, markdown=markdown or "")
    payload = augment_table_relations(payload, relation_tables)
    payload.update(
        {
            "schema_version": "document_table_relations_v1",
            "ruleset_version": payload.get("ruleset_version") or table_relation_ruleset_version,
            "task_id": task_id,
            "filename": task.get("filename"),
            "generated_at": now_iso(),
            "physical_table_count": len(relation_tables),
        }
    )
    return payload


def file_reference_payload(path, url=None, kind=None):
    if not path:
        return None
    exists = os.path.exists(path)
    payload = {
        "path": path if exists else "",
        "exists": exists,
        "url": url or "",
    }
    if kind:
        payload["kind"] = kind
    if exists and os.path.isfile(path):
        payload["size_bytes"] = os.path.getsize(path)
        payload["mtime"] = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()
    return payload


def image_resource_index(task, result_dir):
    task_id = task["task_id"]
    images_dir = os.path.join(result_dir(task), "images")
    resources = []
    if not os.path.isdir(images_dir):
        return {
            "directory": file_reference_payload(images_dir, f"/api/artifact/{task_id}/images", kind="directory"),
            "items": [],
            "summary": {"count": 0, "total_size_bytes": 0},
        }
    total_size = 0
    for name in sorted(os.listdir(images_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        path = os.path.join(images_dir, name)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        total_size += size
        resources.append(
            {
                "name": name,
                "path": path,
                "url": f"/api/artifact/{task_id}/images/{name}",
                "size_bytes": size,
            }
        )
    return {
        "directory": file_reference_payload(images_dir, f"/api/artifact/{task_id}/images", kind="directory"),
        "items": resources,
        "summary": {"count": len(resources), "total_size_bytes": total_size},
    }


def pdf_page_resource_index(task, result_dir):
    task_id = task["task_id"]
    page_dir = os.path.join(result_dir(task), "pdf_pages")
    resources = []
    if os.path.isdir(page_dir):
        for name in sorted(os.listdir(page_dir)):
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(page_dir, name)
            match = re.search(r"page_(\d+)\.png$", name)
            resources.append(
                {
                    "page_number": int(match.group(1)) if match else None,
                    "name": name,
                    "path": path,
                    "url": f"/api/pdf_page/{task_id}/{int(match.group(1))}" if match else "",
                    "size_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
                }
            )
    return {
        "directory": file_reference_payload(page_dir, kind="directory"),
        "items": resources,
        "summary": {"rendered_page_count": len(resources), "total_size_bytes": sum(item.get("size_bytes") or 0 for item in resources)},
    }


def build_document_full_json(
    task,
    markdown,
    enhanced,
    quality_report,
    *,
    financial_data=None,
    financial_checks=None,
    table_relations=None,
    result_dir,
    load_json_artifact,
    artifact_status,
    markdown_page_index,
    now_iso,
    document_full_schema_version,
):
    task_id = task["task_id"]
    task_result_dir = result_dir(task)
    content_list = load_json_artifact(task, "content_list.json")
    middle_json = load_json_artifact(task, "middle.json")
    model_output = load_json_artifact(task, "model_output.json")
    payload_summary = load_json_artifact(task, "result_payload_summary.json")
    markdown_path = task.get("markdown_path") or os.path.join(task_result_dir, "result.md")
    complete_path = os.path.join(task_result_dir, "result_complete.md")
    return {
        "schema_version": document_full_schema_version,
        "generated_at": now_iso(),
        "task": {
            "task_id": task.get("task_id"),
            "mineru_task_id": task.get("mineru_task_id"),
            "filename": task.get("filename"),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "created_at": task.get("created_at"),
            "completed_at": task.get("completed_at"),
            "pdf_page_count": task.get("pdf_page_count"),
            "submit_config": task.get("submit_config") or {},
        },
        "source_files": {
            "pdf": file_reference_payload(task.get("upload_path"), kind="pdf"),
            "markdown": file_reference_payload(markdown_path, f"/api/artifact/{task_id}/result.md", kind="markdown"),
            "complete_markdown": file_reference_payload(complete_path, f"/api/artifact/{task_id}/result_complete.md", kind="markdown"),
        },
        "markdown": {
            "content": markdown or "",
            "chars": len(markdown or ""),
            "line_count": len(str(markdown or "").splitlines()),
            "pages": markdown_page_index(markdown, content_list=content_list),
        },
        "content_list": content_list,
        "content_list_enhanced": enhanced,
        "middle_json": middle_json,
        "model_output": model_output,
        "result_payload_summary": payload_summary,
        "quality_report": quality_report,
        "table_relations": table_relations,
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "resources": {
            "images": image_resource_index(task, result_dir),
            "pdf_pages": pdf_page_resource_index(task, result_dir),
        },
        "artifacts": artifact_status(task),
        "notes": [
            "本 JSON 保存 PDF 的完整解析信息、结构化索引和证据引用。",
            "为控制体积并保持可浏览性，PDF 原文件、页面截图和图片资源以 path/url 引用，不以内嵌 base64 保存。",
        ],
    }
