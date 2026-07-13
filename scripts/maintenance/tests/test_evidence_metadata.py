import hashlib
import importlib.util
import time
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "evidence_metadata.py"
    spec = importlib.util.spec_from_file_location("evidence_metadata_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attach_evidence_metadata_is_complete_and_path_safe(tmp_path):
    module = _load_module()
    artifact = tmp_path / "input.json"
    artifact.write_text('{"ok": true}\n', encoding="utf-8")

    report = module.attach_evidence_metadata(
        {"schema_version": "test_v1", "passed": True},
        repo_root=Path(__file__).resolve().parents[3],
        task_id="T10",
        environment_profile="test",
        command="python tool.py --input <configured-path>",
        result="pass",
        failures=[],
        started_at=time.monotonic(),
        artifacts=[artifact],
    )

    assert report["schema_version"] == "test_v1"
    assert report["base_commit"]
    assert isinstance(report["worktree_dirty"], bool)
    assert report["task_id"] == "T10"
    assert report["result"] == "pass"
    assert report["duration_seconds"] >= 0
    assert report["failures"] == []
    assert report["artifact_checksums"] == {
        "<external>/input.json": hashlib.sha256(artifact.read_bytes()).hexdigest()
    }
    assert str(tmp_path) not in str(report)
