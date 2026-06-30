"""MinerU result persistence helpers for PDF parser results."""

from __future__ import annotations

import os


def save_mineru_result_artifacts(
    task,
    upstream_response,
    file_name,
    file_data,
    *,
    result_dir,
    write_json,
    save_images,
):
    directory = result_dir(task)
    os.makedirs(directory, exist_ok=True)
    summary = {
        "backend": upstream_response.get("backend"),
        "version": upstream_response.get("version"),
        "result_file": file_name,
        "file_keys": sorted(file_data.keys()) if isinstance(file_data, dict) else [],
    }
    write_json(os.path.join(directory, "result_payload_summary.json"), summary)

    artifact_map = {
        "middle_json": "middle.json",
        "model_output": "model_output.json",
        "content_list": "content_list.json",
    }
    for key, filename in artifact_map.items():
        if isinstance(file_data, dict) and key in file_data:
            write_json(os.path.join(directory, filename), file_data[key])

    image_count = 0
    if isinstance(file_data, dict) and "images" in file_data:
        image_count = save_images(file_data["images"], os.path.join(directory, "images"))
    return {
        "summary": summary,
        "image_count": image_count,
    }
