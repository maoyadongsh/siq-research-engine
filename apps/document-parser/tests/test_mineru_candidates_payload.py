from pathlib import Path

from mineru_candidates_payload import build_mineru_import_candidates_payload


def test_build_mineru_import_candidates_payload_serializes_roots_and_candidates():
    roots = [Path("/data/a"), Path("/data/b")]
    candidates = [{"source_dir": "/data/a/case", "title": "Case"}]

    payload = build_mineru_import_candidates_payload(roots, candidates)
    candidates.append({"source_dir": "/data/b/late", "title": "Late"})

    assert payload == {
        "schema_version": "mineru_import_candidates_v1",
        "allowed_roots": ["/data/a", "/data/b"],
        "candidates": [{"source_dir": "/data/a/case", "title": "Case"}],
    }
