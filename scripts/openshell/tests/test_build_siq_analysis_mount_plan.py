from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.openshell.build_siq_analysis_mount_plan import (
    BUSINESS_MOUNT_COUNT,
    HERMES_HOME_RELATIVE,
    PLAN_ROOT_RELATIVE,
    RUNTIME_DIRECTORIES,
    RUNTIME_STATE_DIRECTORY,
    SANDBOX_RUNTIME_STATE_ROOT,
    SQLITE_DATABASES,
    SQLITE_SIDECARS,
    WIKI_RELATIVE,
    MountPlanError,
    compile_mount_plan,
    main,
    validate_mount_plan,
    write_compiled_mount_plan,
)
from scripts.openshell.snapshot_siq_analysis_runtime import SNAPSHOT_ROOT_RELATIVE, SOURCE_RELATIVE, snapshot_runtime


def _prepare_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "project"
    runtime = project_root / SOURCE_RELATIVE
    runtime.mkdir(parents=True)
    (runtime / "config.yaml").write_text(
        "model:\n  provider: custom:test\n  default: test\n  key_env: SIQ_TEST_API_KEY\n",
        encoding="utf-8",
    )
    for database in SQLITE_DATABASES:
        with sqlite3.connect(runtime / database) as connection:
            connection.execute("CREATE TABLE state (value TEXT NOT NULL)")
            connection.execute("INSERT INTO state VALUES ('preserved')")
    for name in RUNTIME_DIRECTORIES:
        (runtime / name).mkdir()
    (runtime / "sessions/session.json").write_text('{"ok":true}\n', encoding="utf-8")

    analysis = project_root / WIKI_RELATIVE / "companies/600519-贵州茅台/analysis"
    analysis.mkdir(parents=True)
    (analysis / "README.md").write_text("task output\n", encoding="utf-8")
    snapshot = project_root / SNAPSHOT_ROOT_RELATIVE / "snapshot-one"
    snapshot_runtime(project_root=project_root, destination=snapshot)
    return project_root, snapshot, analysis


def _mount_by_target(compiled, target: Path) -> dict:
    return next(mount for mount in compiled.plan["docker"]["mounts"] if mount["target"] == target.as_posix())


def _all_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_strings(child)
    elif isinstance(value, str):
        yield value


def test_compile_emits_only_the_fixed_directory_mounts_and_redacted_summary(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)
    second = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    mounts = compiled.plan["docker"]["mounts"]
    wiki = project_root / WIKI_RELATIVE
    hermes_home = project_root / HERMES_HOME_RELATIVE
    assert len(mounts) == BUSINESS_MOUNT_COUNT
    assert mounts[0] == {
        "type": "bind",
        "source": wiki.as_posix(),
        "target": wiki.as_posix(),
        "read_only": True,
    }
    assert mounts[1] == {
        "type": "bind",
        "source": analysis.as_posix(),
        "target": analysis.as_posix(),
        "read_only": False,
    }
    assert _mount_by_target(compiled, SANDBOX_RUNTIME_STATE_ROOT) == {
        "type": "bind",
        "source": (snapshot / RUNTIME_STATE_DIRECTORY).as_posix(),
        "target": SANDBOX_RUNTIME_STATE_ROOT.as_posix(),
        "read_only": False,
    }
    for name in RUNTIME_DIRECTORIES:
        assert _mount_by_target(compiled, hermes_home / name)["source"] == (snapshot / name).as_posix()

    sources = {Path(mount["source"]) for mount in mounts}
    assert project_root not in sources
    assert hermes_home not in sources
    assert snapshot not in sources
    assert not any("contexts" in source.parts for source in sources)
    assert compiled.content == second.content
    assert compiled.summary_content == second.summary_content
    assert b'": "' not in compiled.content
    serialized_summary = compiled.summary_content.decode()
    assert project_root.as_posix() not in serialized_summary
    assert snapshot.name not in serialized_summary
    assert not any(value.startswith("/") for value in _all_strings(compiled.summary))
    assert compiled.summary["analysis_relative_path"] == "data/wiki/companies/600519-贵州茅台/analysis"
    assert compiled.summary["repository_root_mounted"] is False
    assert compiled.summary["hermes_home_mounted"] is False


def test_compile_accepts_explicit_fresh_snapshot_without_host_runtime_records(tmp_path: Path) -> None:
    project_root, _, analysis = _prepare_project(tmp_path)
    snapshot = project_root / SNAPSHOT_ROOT_RELATIVE / "fresh-snapshot"
    snapshot_runtime(project_root=project_root, destination=snapshot, fresh=True)

    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    assert not list((snapshot / RUNTIME_STATE_DIRECTORY).iterdir())
    assert all(not list((snapshot / name).iterdir()) for name in RUNTIME_DIRECTORIES)
    assert (
        _mount_by_target(compiled, SANDBOX_RUNTIME_STATE_ROOT)["source"]
        == (snapshot / RUNTIME_STATE_DIRECTORY).as_posix()
    )
    assert compiled.summary["mount_count"] == BUSINESS_MOUNT_COUNT


def test_fresh_snapshot_contract_rejects_runtime_copy_claims_and_materialized_state(tmp_path: Path) -> None:
    project_root, _, analysis = _prepare_project(tmp_path)
    snapshot = project_root / SNAPSHOT_ROOT_RELATIVE / "fresh-contract"
    snapshot_runtime(project_root=project_root, destination=snapshot, fresh=True)
    manifest_path = snapshot / "snapshot-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    tampered = dict(manifest)
    tampered["host_runtime_records_copied"] = True
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(MountPlanError, match="host runtime isolation"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (snapshot / RUNTIME_STATE_DIRECTORY / "state.db").write_bytes(b"host-state")
    with pytest.raises(MountPlanError, match="runtime state entries"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    (snapshot / RUNTIME_STATE_DIRECTORY / "state.db").unlink()
    (snapshot / "sessions/session.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(MountPlanError, match="runtime directory is not empty"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_default_output_is_content_addressed_under_ignored_var_and_idempotent(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    first = write_compiled_mount_plan(compiled, project_root=project_root)
    second = write_compiled_mount_plan(compiled, project_root=project_root)

    assert first == second
    plan_path, summary_path = first
    assert plan_path.parent == project_root / PLAN_ROOT_RELATIVE
    assert plan_path.name == f"{compiled.digest}.driver-config.json"
    assert plan_path.read_bytes() == compiled.content
    assert summary_path.read_bytes() == compiled.summary_content
    assert plan_path.stat().st_mode & 0o777 == 0o600
    assert summary_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "relative",
    [
        "data/wiki",
        "data/wiki/companies",
        "data/wiki/companies/600519-贵州茅台",
        "data/wiki/companies/600519-贵州茅台/reports",
        "data/wiki/companies/600519-贵州茅台/analysis/nested",
        "data/wiki/companies/other/analysis",
    ],
)
def test_rejects_non_task_or_nonexistent_analysis_boundaries(tmp_path: Path, relative: str) -> None:
    project_root, snapshot, _ = _prepare_project(tmp_path)
    candidate = project_root / relative
    if relative.endswith("reports") or relative.endswith("nested"):
        candidate.mkdir(parents=True)

    with pytest.raises(MountPlanError, match="(analysis directory|one company's direct|does not exist)"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=candidate)


def test_rejects_symlink_in_analysis_tree(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside\n", encoding="utf-8")
    (analysis / "escape").symlink_to(outside)

    with pytest.raises(MountPlanError, match="contains a symlink"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_rejects_sensitive_artifact_in_analysis_tree(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    (analysis / "auth.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(MountPlanError, match="forbidden runtime artifact"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_allows_normal_key_named_analysis_artifacts(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    (analysis / "key_metrics.json").write_text("{}\n", encoding="utf-8")

    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    assert compiled.summary["analysis_relative_path"].endswith("/analysis")


def test_supports_market_specific_company_roots(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    market_analysis = project_root / WIKI_RELATIVE / "us/companies/AAPL-Apple/analysis"
    market_analysis.mkdir(parents=True)
    (market_analysis / "report.html").write_text("ok\n", encoding="utf-8")

    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=market_analysis)

    assert compiled.summary["analysis_relative_path"] == "data/wiki/us/companies/AAPL-Apple/analysis"


def test_rejects_hard_link_in_writable_analysis_tree(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    protected = project_root / "protected-code.py"
    protected.write_text("do not modify\n", encoding="utf-8")
    (analysis / "code-alias.py").hardlink_to(protected)

    with pytest.raises(MountPlanError, match="hard link"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_rejects_snapshot_outside_managed_root_or_with_symlink(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    outside = tmp_path / "snapshot-outside"
    outside.mkdir()
    with pytest.raises(MountPlanError, match="managed runtime snapshot root"):
        compile_mount_plan(project_root=project_root, snapshot=outside, analysis_dir=analysis)

    alias = project_root / SNAPSHOT_ROOT_RELATIVE / "snapshot-alias"
    alias.symlink_to(snapshot, target_is_directory=True)
    with pytest.raises(MountPlanError, match="uses a symlink"):
        compile_mount_plan(project_root=project_root, snapshot=alias, analysis_dir=analysis)


def test_rejects_nonempty_or_unmanifested_sqlite_sidecar(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    (snapshot / RUNTIME_STATE_DIRECTORY / SQLITE_SIDECARS[0]).write_bytes(b"host-wal-must-not-be-copied")

    with pytest.raises(MountPlanError, match="sidecars must be empty"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_rejects_snapshot_digest_drift_and_auth_artifacts(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    (snapshot / "sessions/session.json").write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(MountPlanError, match="runtime directory digest is stale"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    (snapshot / "sessions/session.json").write_text('{"ok":true}\n', encoding="utf-8")
    (snapshot / "auth.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(MountPlanError, match="top-level entries"):
        compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_fixed_contract_validator_rejects_repo_root_and_dangerous_target(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)
    tampered = json.loads(compiled.content)
    tampered["docker"]["mounts"][0]["source"] = project_root.as_posix()
    with pytest.raises(MountPlanError, match="fixed siq_analysis contract"):
        validate_mount_plan(tampered, project_root=project_root, snapshot=snapshot, analysis_dir=analysis)

    tampered = json.loads(compiled.content)
    tampered["docker"]["mounts"][2]["target"] = (project_root / HERMES_HOME_RELATIVE).as_posix()
    with pytest.raises(MountPlanError, match="fixed siq_analysis contract"):
        validate_mount_plan(tampered, project_root=project_root, snapshot=snapshot, analysis_dir=analysis)


def test_output_cannot_escape_or_replace_existing_file(tmp_path: Path) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    compiled = compile_mount_plan(project_root=project_root, snapshot=snapshot, analysis_dir=analysis)
    outside = tmp_path / "outside.driver-config.json"
    with pytest.raises(MountPlanError, match="managed output root"):
        write_compiled_mount_plan(compiled, project_root=project_root, output=outside)

    output_root = project_root / PLAN_ROOT_RELATIVE
    output_root.mkdir(parents=True, exist_ok=True)
    existing = output_root / "existing.driver-config.json"
    existing.write_text("owned by user\n", encoding="utf-8")
    with pytest.raises(MountPlanError, match="conflicts"):
        write_compiled_mount_plan(compiled, project_root=project_root, output=existing)
    assert existing.read_text(encoding="utf-8") == "owned by user\n"


def test_cli_builds_fixed_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project_root, snapshot, analysis = _prepare_project(tmp_path)
    result = main(
        [
            "--project-root",
            str(project_root),
            "--snapshot",
            str(snapshot),
            "--analysis-dir",
            str(analysis),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["mount_count"] == BUSINESS_MOUNT_COUNT
    assert not Path(output["driver_config"]).is_absolute()
    assert not Path(output["summary"]).is_absolute()
    assert (project_root / output["driver_config"]).is_file()
    assert (project_root / output["summary"]).is_file()
