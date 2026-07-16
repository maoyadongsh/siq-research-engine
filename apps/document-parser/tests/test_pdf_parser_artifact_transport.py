from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pdf_parser_artifact_transport import (
    REQUIRED_ARTIFACTS,
    ArtifactTransportError,
    ArtifactTransportLimits,
    DownloadInfo,
    cleanup_staged_pdf_parser_artifacts,
    stage_pdf_parser_artifacts,
)

API_BASE = "https://pdf-parser.internal"
TASK_ID = "doc-transport-test"


def _json_bytes(payload) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _artifact_payloads(*, content_list=None) -> dict[str, bytes]:
    payloads = {}
    for name in REQUIRED_ARTIFACTS:
        if name.endswith(".md"):
            payloads[name] = b"# API staged document\n\nVerified content.\n"
        elif name == "content_list.json":
            payloads[name] = _json_bytes(content_list if content_list is not None else [])
        elif name == "document_full.json":
            payloads[name] = _json_bytes({"schema_version": 3, "task": {"task_id": TASK_ID}})
        else:
            payloads[name] = _json_bytes({"schema_version": 1})
    return payloads


def _bundle(payloads: dict[str, bytes], *, images: dict[str, bytes] | None = None):
    entries = []
    manifest_artifacts = {}
    for name in REQUIRED_ARTIFACTS:
        body = payloads[name]
        sha256 = hashlib.sha256(body).hexdigest()
        entries.append({"name": name, "sha256": sha256, "size_bytes": len(body)})
        manifest_artifacts[name] = {
            "name": name,
            "exists": True,
            "path": f"/foreign/container/results/{TASK_ID}/{name}",
            "sha256": sha256,
            "size_bytes": len(body),
        }
    bundle_sha256 = hashlib.sha256(
        "\n".join(
            f"{name}:{next(item['sha256'] for item in entries if item['name'] == name)}"
            for name in REQUIRED_ARTIFACTS
        ).encode()
    ).hexdigest()
    artifact_manifest = {
        "schema_version": "pdf_parser_artifact_manifest_v1",
        "task_id": TASK_ID,
        "core": {"bundle_sha256": bundle_sha256, "ready": True},
        "artifacts": manifest_artifacts,
    }
    hash_manifest = {
        "schema_version": "pdf_parser_hash_manifest_v1",
        "task_id": TASK_ID,
        "algorithm": "sha256",
        "bundle_sha256": bundle_sha256,
        "entries": entries,
    }
    metadata = {
        "schema_version": "pdf_parser_metadata_v1",
        "task_id": TASK_ID,
        "filename": f"/foreign/container/uploads/{TASK_ID}/issuer.pdf",
        "source_files": {"pdf": {"path": "/foreign/container/private/source.pdf"}},
        "parser": {"backend": "pipeline", "workspace": "/foreign/container/work"},
    }
    result_artifacts = {
        name: {
            "exists": True,
            "path": f"/unmounted/upstream/{TASK_ID}/{name}",
            "url": f"/api/artifact/{TASK_ID}/{name}",
        }
        for name in REQUIRED_ARTIFACTS
    }
    result_artifacts.update(
        {
            "middle.json": {"exists": False, "path": "", "url": ""},
            "model_output.json": {"exists": False, "path": "", "url": ""},
            "images": {
                "exists": bool(images),
                "path": f"/unmounted/upstream/{TASK_ID}/images" if images else "",
                "url": f"/api/artifact/{TASK_ID}/images" if images else "",
            },
        }
    )
    result_payload = {"markdown": "ignored transport copy", "artifacts": result_artifacts}

    mapping = {
        f"{API_BASE}/api/artifact/{TASK_ID}/artifact_manifest.json": _json_bytes(artifact_manifest),
        f"{API_BASE}/api/artifact/{TASK_ID}/hash_manifest.json": _json_bytes(hash_manifest),
        f"{API_BASE}/api/artifact/{TASK_ID}/metadata.json": _json_bytes(metadata),
    }
    for name, body in payloads.items():
        mapping[f"{API_BASE}/api/artifact/{TASK_ID}/{name}"] = body
    if images:
        image_items = []
        for name, body in images.items():
            image_items.append(
                {
                    "name": name,
                    "url": f"/api/artifact/{TASK_ID}/images/{name}",
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
            mapping[f"{API_BASE}/api/artifact/{TASK_ID}/images/{name}"] = body
        mapping[f"{API_BASE}/api/artifact/{TASK_ID}/images"] = _json_bytes(
            {
                "task_id": TASK_ID,
                "artifact": "images",
                "count": len(image_items),
                "images": image_items,
            }
        )
    return result_payload, mapping


def _fetcher(mapping: dict[str, bytes]):
    def fetch(url: str, destination: Path, headers, max_bytes: int) -> DownloadInfo:
        assert headers == {"X-PDF2MD-Token": "internal"}
        if url not in mapping:
            raise AssertionError(f"unexpected URL: {url}")
        body = mapping[url]
        if len(body) > max_bytes:
            raise ArtifactTransportError("test download exceeds configured size limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        return DownloadInfo(len(body), hashlib.sha256(body).hexdigest())

    return fetch


def _limits(**overrides) -> ArtifactTransportLimits:
    values = {
        "max_file_bytes": 1024 * 1024,
        "max_total_bytes": 16 * 1024 * 1024,
        "max_files": 100,
        "max_json_bytes": 1024 * 1024,
    }
    values.update(overrides)
    return ArtifactTransportLimits(**values)


def test_api_transport_ignores_foreign_absolute_paths_and_publishes_atomically(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"image-data"
    payload, mapping = _bundle(_artifact_payloads(), images={"chart.png": png})
    staging_root = tmp_path / "private-task" / ".pdf-parser-staging"

    staged = stage_pdf_parser_artifacts(
        task_id=TASK_ID,
        result_payload=payload,
        api_base=API_BASE,
        headers={"X-PDF2MD-Token": "internal"},
        staging_root=staging_root,
        limits=_limits(),
        fetcher=_fetcher(mapping),
    )

    assert staged.result_dir == staging_root / TASK_ID
    assert (staged.result_dir / "result.md").is_file()
    assert (staged.result_dir / "images" / "chart.png").read_bytes() == png
    staged_metadata = json.loads((staged.result_dir / "metadata.json").read_text(encoding="utf-8"))
    assert staged_metadata["filename"] == "issuer.pdf"
    assert staged_metadata["task"] == {"task_id": TASK_ID, "filename": "issuer.pdf"}
    assert staged_metadata["parser"] == {"backend": "pipeline"}
    assert "source_files" not in staged_metadata
    assert "/foreign/" not in json.dumps(staged_metadata)
    assert not list(staging_root.glob(f".{TASK_ID}.tmp-*"))
    assert cleanup_staged_pdf_parser_artifacts(
        staged.result_dir,
        task_id=TASK_ID,
        staging_root=staging_root,
    ) is True
    assert not staged.result_dir.exists()
    assert staging_root.is_dir()


def test_api_transport_rejects_result_artifact_traversal_before_download(tmp_path):
    payload, mapping = _bundle(_artifact_payloads())
    payload["artifacts"]["../secret.json"] = {
        "exists": True,
        "path": "/etc/passwd",
        "url": "/api/artifact/other/secret.json",
    }

    with pytest.raises(ArtifactTransportError, match="forbidden artifact names"):
        stage_pdf_parser_artifacts(
            task_id=TASK_ID,
            result_payload=payload,
            api_base=API_BASE,
            headers={"X-PDF2MD-Token": "internal"},
            staging_root=tmp_path / ".pdf-parser-staging",
            limits=_limits(),
            fetcher=_fetcher(mapping),
        )


def test_api_transport_rejects_bad_hash_and_preserves_existing_publish(tmp_path):
    payload, mapping = _bundle(_artifact_payloads())
    mapping[f"{API_BASE}/api/artifact/{TASK_ID}/result.md"] += b"tampered"
    staging_root = tmp_path / ".pdf-parser-staging"
    existing = staging_root / TASK_ID
    existing.mkdir(parents=True)
    (existing / "sentinel").write_text("old", encoding="utf-8")

    with pytest.raises(ArtifactTransportError, match="hash or size mismatch"):
        stage_pdf_parser_artifacts(
            task_id=TASK_ID,
            result_payload=payload,
            api_base=API_BASE,
            headers={"X-PDF2MD-Token": "internal"},
            staging_root=staging_root,
            limits=_limits(),
            fetcher=_fetcher(mapping),
        )

    assert (existing / "sentinel").read_text(encoding="utf-8") == "old"
    assert not list(staging_root.glob(f".{TASK_ID}.tmp-*"))


@pytest.mark.parametrize(
    ("limits", "match"),
    [
        (_limits(max_file_bytes=64), "size limit"),
        (_limits(max_total_bytes=512), "total size limit|size limit"),
        (_limits(max_files=2), "count exceeds"),
    ],
)
def test_api_transport_enforces_file_total_and_count_limits(tmp_path, limits, match):
    payload, mapping = _bundle(_artifact_payloads())

    with pytest.raises(ArtifactTransportError, match=match):
        stage_pdf_parser_artifacts(
            task_id=TASK_ID,
            result_payload=payload,
            api_base=API_BASE,
            headers={"X-PDF2MD-Token": "internal"},
            staging_root=tmp_path / ".pdf-parser-staging",
            limits=limits,
            fetcher=_fetcher(mapping),
        )


def test_api_transport_rejects_unsafe_content_list_image_path(tmp_path):
    payloads = _artifact_payloads(
        content_list=[{"type": "image", "img_path": "images/../../outside.png"}]
    )
    payload, mapping = _bundle(payloads)

    with pytest.raises(ArtifactTransportError, match="unsafe image path"):
        stage_pdf_parser_artifacts(
            task_id=TASK_ID,
            result_payload=payload,
            api_base=API_BASE,
            headers={"X-PDF2MD-Token": "internal"},
            staging_root=tmp_path / ".pdf-parser-staging",
            limits=_limits(),
            fetcher=_fetcher(mapping),
        )


def test_api_transport_serializes_same_task_publish_and_reuses_valid_stage(tmp_path):
    payload, mapping = _bundle(_artifact_payloads())
    staging_root = tmp_path / ".pdf-parser-staging"
    calls = 0
    calls_lock = threading.Lock()
    base_fetcher = _fetcher(mapping)

    def slow_fetcher(url, destination, headers, max_bytes):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.005)
        return base_fetcher(url, destination, headers, max_bytes)

    def stage():
        return stage_pdf_parser_artifacts(
            task_id=TASK_ID,
            result_payload=payload,
            api_base=API_BASE,
            headers={"X-PDF2MD-Token": "internal"},
            staging_root=staging_root,
            limits=_limits(),
            fetcher=slow_fetcher,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (future.result() for future in (pool.submit(stage), pool.submit(stage)))

    assert first.result_dir == second.result_dir == staging_root / TASK_ID
    assert calls == len(REQUIRED_ARTIFACTS) + 3
    assert not list(staging_root.glob(f".{TASK_ID}.tmp-*"))
    assert not list(staging_root.glob(f".{TASK_ID}.quarantine-*"))


def test_api_transport_recovers_corrupt_publish_and_crash_residue(tmp_path):
    payload, mapping = _bundle(_artifact_payloads())
    staging_root = tmp_path / ".pdf-parser-staging"
    first = stage_pdf_parser_artifacts(
        task_id=TASK_ID,
        result_payload=payload,
        api_base=API_BASE,
        headers={"X-PDF2MD-Token": "internal"},
        staging_root=staging_root,
        limits=_limits(),
        fetcher=_fetcher(mapping),
    )
    (first.result_dir / "result.md").write_text("corrupt", encoding="utf-8")
    residue = staging_root / f".{TASK_ID}.tmp-crashed"
    residue.mkdir()
    (residue / "partial").write_text("partial", encoding="utf-8")

    recovered = stage_pdf_parser_artifacts(
        task_id=TASK_ID,
        result_payload=payload,
        api_base=API_BASE,
        headers={"X-PDF2MD-Token": "internal"},
        staging_root=staging_root,
        limits=_limits(),
        fetcher=_fetcher(mapping),
    )

    assert recovered.result_dir == first.result_dir
    assert (recovered.result_dir / "result.md").read_bytes() == mapping[
        f"{API_BASE}/api/artifact/{TASK_ID}/result.md"
    ]
    assert not residue.exists()
    assert not list(staging_root.glob(f".{TASK_ID}.quarantine-*"))


def test_cleanup_requires_matching_trusted_staging_root(tmp_path):
    payload, mapping = _bundle(_artifact_payloads())
    trusted_root = tmp_path / "trusted" / ".pdf-parser-staging"
    staged = stage_pdf_parser_artifacts(
        task_id=TASK_ID,
        result_payload=payload,
        api_base=API_BASE,
        headers={"X-PDF2MD-Token": "internal"},
        staging_root=trusted_root,
        limits=_limits(),
        fetcher=_fetcher(mapping),
    )

    assert cleanup_staged_pdf_parser_artifacts(
        staged.result_dir,
        task_id=TASK_ID,
        staging_root=tmp_path / "other" / ".pdf-parser-staging",
    ) is False
    assert staged.result_dir.exists()
    assert cleanup_staged_pdf_parser_artifacts(
        staged.result_dir,
        task_id=TASK_ID,
        staging_root=trusted_root,
    ) is True
