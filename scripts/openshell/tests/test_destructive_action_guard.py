from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.openshell import destructive_action_guard as guard_module  # noqa: E402
from scripts.openshell.destructive_action_guard import (  # noqa: E402
    DestructiveActionGuard,
    DestructiveActionGuardError,
    InotifyEvent,
    SandboxTerminator,
    restore_deletion_snapshot,
)
from scripts.openshell.security_audit import SecurityRunContext  # noqa: E402


class FakeTerminator(SandboxTerminator):
    def __init__(self, hook=None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.hook = hook

    def terminate(self, *, sandbox_id: str, reason_code: str) -> None:
        self.calls.append((sandbox_id, reason_code))
        if self.hook is not None:
            self.hook()


class FailingTerminator(SandboxTerminator):
    def terminate(self, *, sandbox_id: str, reason_code: str) -> None:
        del sandbox_id, reason_code
        raise RuntimeError("fixture fence failure")


def _context(run_id: str) -> SecurityRunContext:
    return SecurityRunContext(
        profile="siq_analysis",
        sandbox_id=f"sandbox-{run_id}",
        run_id=run_id,
        session_id=f"session-{run_id}",
        policy_digest="a" * 64,
    )


def _project(tmp_path: Path, *, company: str = "acme") -> tuple[Path, Path]:
    project = tmp_path / "project"
    analysis = project / "data" / "wiki" / "companies" / company / "analysis"
    analysis.mkdir(parents=True)
    return project, analysis


def _write_baseline(root: Path, count: int, *, directory: str = "persistent") -> None:
    destination = root / directory
    destination.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (destination / f"file-{index:04d}.txt").write_text(f"baseline-{index}\n", encoding="utf-8")


def _audit_records(project: Path) -> list[dict]:
    audit_files = sorted((project / "var" / "openshell" / "audit").glob("*.jsonl"))
    return [
        json.loads(line)
        for audit_file in audit_files
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _node_binary() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; skipping Node filesystem API coverage")
    return node


def _run_node_delete(node: str, *, operation: str, directory: Path, count: int | None = None) -> None:
    if operation == "unlink":
        assert count is not None
        script = (
            "const fs = require('node:fs');\n"
            "const path = require('node:path');\n"
            "const root = process.argv[1];\n"
            f"for (let i = 0; i < {count}; i += 1) "
            "fs.unlinkSync(path.join(root, `file-${String(i).padStart(4, '0')}.txt`));\n"
        )
    elif operation == "rm":
        script = (
            "const fs = require('node:fs');\n"
            "const path = require('node:path');\n"
            "fs.rmSync(path.join(process.argv[1], 'bulk'), { recursive: true, force: true });\n"
        )
    else:
        raise AssertionError(f"unsupported Node delete operation: {operation}")
    result = subprocess.run(
        [node, "-e", script, str(directory)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


# Linux syscall numbers are stable for the reviewed architectures used by the
# SIQ/OpenShell hosts. Unsupported hosts skip this portability-specific test.
_UNLINKAT_SYSCALLS = {"aarch64": 35, "x86_64": 263}


def _direct_unlinkat(directory: Path, names: list[str]) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("direct unlinkat coverage requires Linux")
    syscall_number = _UNLINKAT_SYSCALLS.get(platform.machine())
    if syscall_number is None:
        pytest.skip(f"direct unlinkat syscall number is not reviewed for {platform.machine()!r}")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
    except (AttributeError, OSError, TypeError) as exc:
        pytest.skip(f"libc direct syscall support is unavailable: {exc}")
    syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    syscall.restype = ctypes.c_long
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(directory, directory_flags)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.ENOTSUP}:
            pytest.skip(f"directory fd support is unavailable: {exc}")
        raise
    try:
        for name in names:
            result = syscall(
                ctypes.c_long(syscall_number),
                ctypes.c_int(directory_fd),
                ctypes.c_char_p(os.fsencode(name)),
                ctypes.c_int(0),
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), directory / name)
    finally:
        os.close(directory_fd)


def test_snapshot_is_fixed_private_atomic_and_excludes_ephemeral_files(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path, company="600104-上汽集团")
    _write_baseline(analysis, 3)
    ephemeral = analysis / ".work" / "task"
    ephemeral.mkdir(parents=True)
    (ephemeral / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("snapshot-contract"),
        terminator=terminator,
    ) as deletion_guard:
        snapshot = deletion_guard.prepare()

        assert snapshot.path == (project / "var/openshell/siq-analysis/deletion-snapshots/snapshot-contract")
        assert snapshot.analysis_relative_path == "data/wiki/companies/600104-上汽集团/analysis"
        assert set(snapshot.files) == {
            "persistent/file-0000.txt",
            "persistent/file-0001.txt",
            "persistent/file-0002.txt",
        }
        assert not (snapshot.path / "files/.work").exists()
        assert snapshot.path.stat().st_mode & 0o777 == 0o700
        assert all(
            path.stat().st_mode & 0o777 == 0o700 for path in (snapshot.path / "files").rglob("*") if path.is_dir()
        )
        assert all(
            path.stat().st_mode & 0o777 == 0o600 for path in (snapshot.path / "files").rglob("*") if path.is_file()
        )
        manifest = json.loads((snapshot.path / "snapshot-manifest.json").read_text(encoding="utf-8"))
        assert manifest["baseline_file_count"] == 3
        assert manifest["safeguards"] == {
            "credentials_copied": False,
            "ephemeral_directories_copied": False,
            "hardlinks_allowed": False,
            "special_files_allowed": False,
            "symlinks_allowed": False,
        }
        assert not list(snapshot.path.parent.glob(".snapshot-contract.staging-*"))

    assert terminator.calls == []


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo", "setid", "credential"])
def test_snapshot_rejects_unsafe_or_credential_entries(tmp_path: Path, unsafe_kind: str) -> None:
    project, analysis = _project(tmp_path)
    source = analysis / "source.txt"
    source.write_text("safe\n", encoding="utf-8")
    if unsafe_kind == "symlink":
        (analysis / "link.txt").symlink_to(source)
    elif unsafe_kind == "hardlink":
        os.link(source, analysis / "hardlink.txt")
    elif unsafe_kind == "fifo":
        os.mkfifo(analysis / "pipe")
    elif unsafe_kind == "setid":
        source.chmod(source.stat().st_mode | stat.S_ISUID)
    else:
        (analysis / ".env").write_text("API_KEY=not-copied\n", encoding="utf-8")

    deletion_guard = DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context(f"unsafe-{unsafe_kind}"),
        terminator=FakeTerminator(),
    )
    with pytest.raises(DestructiveActionGuardError):
        deletion_guard.prepare()
    deletion_guard.close()

    snapshots = project / "var/openshell/siq-analysis/deletion-snapshots"
    assert not (snapshots / f"unsafe-{unsafe_kind}").exists()


@pytest.mark.parametrize("name", ["api_key.txt", "OPENAI_API_KEY", "secret_value.json"])
def test_snapshot_rejects_credential_like_names(tmp_path: Path, name: str) -> None:
    project, analysis = _project(tmp_path)
    (analysis / name).write_text("opaque-value-that-must-not-be-snapshotted\n", encoding="utf-8")

    deletion_guard = DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context(f"unsafe-name-{name.replace('.', '-')}"),
        terminator=FakeTerminator(),
    )
    with pytest.raises(DestructiveActionGuardError):
        deletion_guard.prepare()
    deletion_guard.close()


def test_snapshot_can_be_restored_after_terminator_failure(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    observed: list[str] = []

    def record_trigger(_reason: str, paths: tuple[str, ...]) -> None:
        observed.extend(paths)

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("restore-after-fence-failure"),
        terminator=FailingTerminator(),
        before_terminate=record_trigger,
    ) as deletion_guard:
        snapshot = deletion_guard.prepare()
        for index in range(20):
            (analysis / f"persistent/file-{index:04d}.txt").unlink()
        with pytest.raises(DestructiveActionGuardError, match="termination failed"):
            deletion_guard.monitor(timeout_seconds=2.0)

    assert observed
    assert not (analysis / "persistent/file-0000.txt").exists()
    restored = restore_deletion_snapshot(
        project_root=project,
        analysis_root=analysis,
        snapshot_path=snapshot.path,
        observed_paths=observed,
    )
    assert restored == 20
    assert (analysis / "persistent/file-0000.txt").read_text(encoding="utf-8") == "baseline-0\n"


def test_small_shell_deletion_and_new_ephemeral_cleanup_are_allowed(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    work = analysis / ".work"
    work.mkdir()
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("small-delete"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        transient_dir = work / "new-cache"
        transient_dir.mkdir()
        first_poll = deletion_guard.monitor(timeout_seconds=0.05)
        assert first_poll.triggered is False
        transient = transient_dir / "new.json"
        transient.write_text("{}\n", encoding="utf-8")
        transient.unlink()

        rm = shutil.which("rm")
        assert rm is not None
        subprocess.run(
            [rm, "-f", analysis / "persistent/file-0000.txt", analysis / "persistent/file-0001.txt"],
            check=True,
        )
        result = deletion_guard.monitor(timeout_seconds=0.2)

        assert result.triggered is False
        assert result.observed_deleted_file_count == 2
        assert not (analysis / "persistent/file-0000.txt").exists()
        assert terminator.calls == []
        assert _audit_records(project) == []


@pytest.mark.parametrize("mechanism", ["node_unlink", "direct_unlinkat"])
def test_small_alternative_filesystem_cleanup_is_allowed(tmp_path: Path, mechanism: str) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context(f"small-{mechanism}"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        names = ["file-0000.txt", "file-0001.txt"]
        if mechanism == "node_unlink":
            node = _node_binary()
            _run_node_delete(node, operation="unlink", directory=analysis / "persistent", count=len(names))
        else:
            _direct_unlinkat(analysis / "persistent", names)

        result = deletion_guard.monitor(timeout_seconds=1.0)

        assert result.triggered is False
        assert result.observed_deleted_file_count == len(names)
        assert all(not (analysis / "persistent" / name).exists() for name in names)
        assert terminator.calls == []
        assert _audit_records(project) == []


def test_node_unlink_sync_deletion_is_guarded_and_recovered(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("node-unlink-sync"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        _run_node_delete(
            _node_binary(),
            operation="unlink",
            directory=analysis / "persistent",
            count=20,
        )
        result = deletion_guard.monitor(timeout_seconds=2.0)

        assert result.triggered is True
        assert result.reason_code == "deletion_ratio_threshold"
        assert result.observed_deleted_file_count == 20
        assert result.restored_file_count == 20
        assert terminator.calls == [("sandbox-node-unlink-sync", "deletion_ratio_threshold")]
        for index in range(20):
            assert (analysis / f"persistent/file-{index:04d}.txt").read_text(encoding="utf-8") == (
                f"baseline-{index}\n"
            )


def test_node_rm_sync_recursive_deletion_is_guarded_and_recovered(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 501, directory="bulk")
    _write_baseline(analysis, 699, directory="keep")
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("node-rm-sync"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        _run_node_delete(_node_binary(), operation="rm", directory=analysis)
        result = deletion_guard.monitor(timeout_seconds=5.0)

        assert result.triggered is True
        assert result.reason_code == "deletion_count_gt_500"
        assert result.observed_deleted_file_count == 501
        assert result.restored_file_count == 501
        assert terminator.calls == [("sandbox-node-rm-sync", "deletion_count_gt_500")]
        assert (analysis / "bulk/file-0000.txt").read_text(encoding="utf-8") == "baseline-0\n"
        assert (analysis / "bulk/file-0500.txt").read_text(encoding="utf-8") == "baseline-500\n"


def test_direct_unlinkat_deletion_is_guarded_and_recovered(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    terminator = FakeTerminator()
    names = [f"file-{index:04d}.txt" for index in range(20)]

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("direct-unlinkat"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        _direct_unlinkat(analysis / "persistent", names)
        result = deletion_guard.monitor(timeout_seconds=2.0)

        assert result.triggered is True
        assert result.reason_code == "deletion_ratio_threshold"
        assert result.observed_deleted_file_count == 20
        assert result.restored_file_count == 20
        assert terminator.calls == [("sandbox-direct-unlinkat", "deletion_ratio_threshold")]
        for name in names:
            assert (analysis / "persistent" / name).exists()


def test_python_ratio_deletion_terminates_restores_and_audits_only_target_root(
    tmp_path: Path,
) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    other_company = project / "data/wiki/companies/other/analysis"
    other_company.mkdir(parents=True)
    other_file = other_company / "untouched.txt"
    other_file.write_text("other company\n", encoding="utf-8")
    session_file = project / "data/hermes/home/profiles/siq_analysis/sessions/session.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("session\n", encoding="utf-8")
    missing_at_termination: list[bool] = []
    terminator = FakeTerminator(
        lambda: missing_at_termination.append(not (analysis / "persistent/file-0000.txt").exists())
    )

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("ratio-delete"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        code = (
            "import pathlib,sys\n"
            "root=pathlib.Path(sys.argv[1])\n"
            "[(root / f'file-{i:04d}.txt').unlink() for i in range(20)]\n"
        )
        subprocess.run(
            [sys.executable, "-c", code, analysis / "persistent"],
            check=True,
        )
        result = deletion_guard.monitor(timeout_seconds=2.0)

        assert result.triggered is True
        assert result.reason_code == "deletion_ratio_threshold"
        assert result.observed_deleted_file_count == 20
        assert result.restored_file_count == 20
        assert missing_at_termination == [True]
        assert terminator.calls == [("sandbox-ratio-delete", "deletion_ratio_threshold")]
        for index in range(20):
            assert (analysis / f"persistent/file-{index:04d}.txt").read_text(encoding="utf-8") == f"baseline-{index}\n"
        assert not list(analysis.rglob("*.restore-*"))

    assert other_file.read_text(encoding="utf-8") == "other company\n"
    assert session_file.read_text(encoding="utf-8") == "session\n"
    records = _audit_records(project)
    assert len(records) == 1
    assert records[0]["operation_class"] == "filesystem.delete"
    assert records[0]["decision"] == "deny"
    assert records[0]["error_code"] == "deletion_ratio_threshold"
    assert records[0]["target"]["scope"] == "task_analysis"
    assert "data/wiki/companies/acme/analysis" not in json.dumps(records[0])


def test_shell_recursive_deletion_over_500_uses_absolute_threshold(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 501, directory="bulk")
    _write_baseline(analysis, 699, directory="keep")
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("absolute-delete"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        rm = shutil.which("rm")
        assert rm is not None
        subprocess.run([rm, "-rf", analysis / "bulk"], check=True)
        result = deletion_guard.monitor(timeout_seconds=5.0)

        assert result.triggered is True
        assert result.reason_code == "deletion_count_gt_500"
        assert result.observed_deleted_file_count == 501
        assert result.restored_file_count == 501
        assert (analysis / "bulk/file-0000.txt").read_text(encoding="utf-8") == "baseline-0\n"
        assert (analysis / "bulk/file-0500.txt").read_text(encoding="utf-8") == "baseline-500\n"
        assert (analysis / "keep/file-0698.txt").read_text(encoding="utf-8") == "baseline-698\n"


def test_analysis_root_self_delete_triggers_even_without_baseline_files(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("root-delete"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        analysis.rmdir()
        result = deletion_guard.monitor(timeout_seconds=2.0)

        assert result.triggered is True
        assert result.reason_code == "analysis_root_self_deleted"
        assert analysis.is_dir()
        assert terminator.calls == [("sandbox-root-delete", "analysis_root_self_deleted")]


def test_overflow_event_fails_closed_and_uses_fixed_terminator_interface(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 1)
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("overflow"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        reason = deletion_guard._process_events(
            [
                InotifyEvent(
                    watch_descriptor=-1,
                    mask=guard_module.IN_Q_OVERFLOW,
                    cookie=0,
                    name="",
                )
            ]
        )
        assert reason == "inotify_queue_overflow"
        result = deletion_guard._respond_to_trigger(reason)
        assert result.triggered is True
        assert terminator.calls == [("sandbox-overflow", "inotify_queue_overflow")]

    with pytest.raises(DestructiveActionGuardError, match="SandboxTerminator"):
        DestructiveActionGuard(
            project_root=project,
            analysis_root=analysis,
            audit_context=_context("bad-terminator"),
            terminator="rm -rf anything",  # type: ignore[arg-type]
        )


def test_recovery_refuses_symlink_replacement_after_termination(tmp_path: Path) -> None:
    project, analysis = _project(tmp_path)
    _write_baseline(analysis, 40)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    terminator = FakeTerminator()

    with DestructiveActionGuard(
        project_root=project,
        analysis_root=analysis,
        audit_context=_context("unsafe-recovery"),
        terminator=terminator,
    ) as deletion_guard:
        deletion_guard.prepare()
        for index in range(20):
            (analysis / f"persistent/file-{index:04d}.txt").unlink()
        (analysis / "persistent/file-0000.txt").symlink_to(outside)

        with pytest.raises(DestructiveActionGuardError, match="recovery failed"):
            deletion_guard.monitor(timeout_seconds=2.0)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert terminator.calls == [("sandbox-unsafe-recovery", "deletion_ratio_threshold")]
    records = _audit_records(project)
    assert records[-1]["error_code"] == "deletion_recovery_failed"


def test_threshold_boundaries_are_fixed() -> None:
    assert guard_module._threshold_reason(deleted_count=500, baseline_count=2000) is None
    assert guard_module._threshold_reason(deleted_count=501, baseline_count=2000) == "deletion_count_gt_500"
    assert guard_module._threshold_reason(deleted_count=19, baseline_count=38) is None
    assert guard_module._threshold_reason(deleted_count=20, baseline_count=40) == "deletion_ratio_threshold"
