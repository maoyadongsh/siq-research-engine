import os
import re
from datetime import datetime, timezone


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
