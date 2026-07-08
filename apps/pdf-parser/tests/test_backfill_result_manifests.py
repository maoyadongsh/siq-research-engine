import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_result_manifests.py"
SPEC = importlib.util.spec_from_file_location("backfill_result_manifests", SCRIPT_PATH)
backfill_result_manifests = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(backfill_result_manifests)


def test_discover_result_dirs_accepts_repeated_task_ids(tmp_path):
    results_dir = tmp_path / "results"
    for name in ("task-a", "task-b", "task-c"):
        (results_dir / name).mkdir(parents=True)

    result_dirs = backfill_result_manifests.discover_result_dirs(
        results_dir,
        ["task-b", "task-a", "task-b", "missing"],
    )

    assert [path.name for path in result_dirs] == ["task-b", "task-a"]
