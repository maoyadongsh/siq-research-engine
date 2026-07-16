from __future__ import annotations

import hashlib

from artifacts import artifact_summary


def test_artifact_summary_exposes_compatible_size_and_sha256(tmp_path):
    body = b"# verified document\n"
    (tmp_path / "document.md").write_bytes(body)

    summary = artifact_summary("task-integrity", tmp_path)

    document = summary["document.md"]
    assert document["exists"] is True
    assert document["size"] == len(body)
    assert document["size_bytes"] == len(body)
    assert document["sha256"] == hashlib.sha256(body).hexdigest()

    missing = summary["document_full.json"]
    assert missing["exists"] is False
    assert missing["size"] == 0
    assert missing["size_bytes"] == 0
    assert missing["sha256"] == ""
