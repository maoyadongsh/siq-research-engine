from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "check_tracked_state.py"
REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_FIXTURE_ROOT = REPO_ROOT / "artifacts/openshell/v0.6/logs-20260716-final"


def _module():
    spec = importlib.util.spec_from_file_location("siq_check_tracked_state_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "CI")
    return root


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest_entry(root: Path, path: str, classification: str) -> dict[str, object]:
    content = (root / path).read_bytes()
    return {
        "classification": classification,
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _write_manifest(root: Path, entries: list[dict[str, object]]) -> None:
    _write(
        root,
        "artifacts/openshell/tracked-artifacts.json",
        json.dumps(
            {
                "schema_version": "siq.openshell.tracked-artifacts.v1",
                "artifacts": sorted(entries, key=lambda item: str(item["path"])),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _copy_log_pair(root: Path) -> tuple[str, str]:
    relative_root = "artifacts/openshell/v0.6/log-contract"
    json_path = f"{relative_root}/logs.sanitized.json"
    markdown_path = f"{relative_root}/logs.sanitized.md"
    _write(root, json_path, (LOG_FIXTURE_ROOT / "logs.sanitized.json").read_text(encoding="ascii"))
    _write(root, markdown_path, (LOG_FIXTURE_ROOT / "logs.sanitized.md").read_text(encoding="ascii"))
    return json_path, markdown_path


def test_index_scan_accepts_manifest_bound_sanitized_artifacts(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    files = {
        "artifacts/openshell/README.md": ("public_document", "safe\n"),
        "artifacts/openshell/v0.6/baseline.json": ("baseline", '{"status":"passed"}\n'),
        "artifacts/openshell/v0.6/probe.sanitized.json": (
            "sanitized_evidence",
            '{"schema_version":"siq.openshell.sanitized-evidence.v1"}\n',
        ),
        "var/openshell/README.md": ("public_document", "safe\n"),
        "var/openshell/manifests/toolchain.sanitized.json": (
            "sanitized_manifest",
            '{"status":"reviewed_not_installed"}\n',
        ),
    }
    entries = []
    for path, (classification, content) in files.items():
        _write(root, path, content)
        entries.append(_manifest_entry(root, path, classification))
    json_path, markdown_path = _copy_log_pair(root)
    entries.extend(
        [
            _manifest_entry(root, json_path, "sanitized_log"),
            _manifest_entry(root, markdown_path, "sanitized_log"),
        ]
    )
    _write_manifest(root, entries)
    _git(root, "add", ".")

    assert module.scan_tracked_state(root, require_allowlist=True) == []


def test_suffix_alone_does_not_allow_an_unmanifested_artifact(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    _write(root, "artifacts/openshell/v0.6/approved.sanitized.json", "{}\n")
    approved = _manifest_entry(root, "artifacts/openshell/v0.6/approved.sanitized.json", "sanitized_evidence")
    _write_manifest(root, [approved])
    _write(root, "artifacts/openshell/v0.6/unreviewed.sanitized.json", "{}\n")
    _git(root, "add", ".")

    findings = module.scan_tracked_state(root)

    assert ("artifacts/openshell/v0.6/unreviewed.sanitized.json", "tracked_path_not_manifested") in {
        (item.path, item.code) for item in findings
    }


def test_index_scan_rejects_forced_runtime_path_and_manifested_symlink(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    _write(root, "var/openshell/backups/raw.json", '{"status":"raw"}\n')
    target = "artifacts/openshell/v0.6/target.sanitized.json"
    link = "artifacts/openshell/v0.6/link.sanitized.json"
    _write(root, target, "{}\n")
    (root / link).symlink_to(root / target)
    content = (root / target).read_bytes()
    _write_manifest(
        root,
        [
            {
                "classification": "sanitized_evidence",
                "path": link,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ],
    )
    _git(root, "add", "-f", "var/openshell/backups/raw.json", link)
    _git(root, "add", "artifacts/openshell/tracked-artifacts.json")

    codes = {(item.path, item.code) for item in module.scan_tracked_state(root)}

    assert ("var/openshell/backups/raw.json", "tracked_path_not_manifested") in codes
    assert (link, "tracked_nonregular_mode") in codes


def test_index_scan_reads_index_not_dirty_worktree(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    path = "artifacts/openshell/v0.6/probe.sanitized.json"
    _write(root, path, '{"status":"passed"}\n')
    _write_manifest(root, [_manifest_entry(root, path, "sanitized_evidence")])
    _git(root, "add", ".")
    _write(root, path, '{"provider_token":"must-not-be-staged"}\n')

    assert module.scan_tracked_state(root) == []


def test_index_scan_rejects_gitlink_at_manifested_path(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    path = "artifacts/openshell/v0.6/probe.sanitized.json"
    _write(root, path, "{}\n")
    _write_manifest(root, [_manifest_entry(root, path, "sanitized_evidence")])
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "seed")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},{path}")

    findings = module.scan_tracked_state(root)

    assert (path, "tracked_nonregular_mode") in {(item.path, item.code) for item in findings}


def test_required_manifest_rejects_a_vacuous_empty_index_and_empty_manifest(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)

    missing = module.scan_tracked_state(root, require_allowlist=True)
    assert [(item.path, item.code) for item in missing] == [
        (module.MANIFEST_PATH, "tracked_manifest_not_tracked")
    ]

    _write_manifest(root, [])
    _git(root, "add", ".")
    empty = module.scan_tracked_state(root, require_allowlist=True)
    assert (module.MANIFEST_PATH, "tracked_manifest_empty") in {(item.path, item.code) for item in empty}


def test_manifest_digest_size_and_missing_path_are_rejected(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    path = "artifacts/openshell/v0.6/probe.sanitized.json"
    _write(root, path, "{}\n")
    entry = _manifest_entry(root, path, "sanitized_evidence")
    entry["sha256"] = "0" * 64
    entry["size_bytes"] = 999
    missing = "artifacts/openshell/v0.6/missing.sanitized.json"
    _write_manifest(
        root,
        [
            entry,
            {
                "classification": "sanitized_evidence",
                "path": missing,
                "sha256": hashlib.sha256(b"{}\n").hexdigest(),
                "size_bytes": 3,
            },
        ],
    )
    _git(root, "add", ".")

    codes = {(item.path, item.code) for item in module.scan_tracked_state(root)}

    assert (path, "tracked_digest_mismatch") in codes
    assert (path, "tracked_size_mismatch") in codes
    assert (missing, "manifested_path_not_tracked") in codes


def test_manifest_rejects_unsafe_path_wrong_classification_and_duplicates(tmp_path: Path) -> None:
    module = _module()
    entries = [
        {
            "classification": "sanitized_evidence",
            "path": "../escape.sanitized.json",
            "sha256": "0" * 64,
            "size_bytes": 1,
        },
        {
            "classification": "sanitized_log",
            "path": "artifacts/openshell/v0.6/probe.sanitized.json",
            "sha256": "0" * 64,
            "size_bytes": 1,
        },
        {
            "classification": "sanitized_evidence",
            "path": "artifacts/openshell/v0.6/dup.sanitized.json",
            "sha256": "0" * 64,
            "size_bytes": 1,
        },
        {
            "classification": "sanitized_evidence",
            "path": "artifacts/openshell/v0.6/dup.sanitized.json",
            "sha256": "0" * 64,
            "size_bytes": 1,
        },
    ]
    content = json.dumps({"schema_version": module.MANIFEST_SCHEMA, "artifacts": entries}).encode()

    _, findings = module._manifest_entries(content)
    codes = {item.code for item in findings}

    assert "tracked_manifest_path_invalid" in codes
    assert "tracked_manifest_classification_invalid" in codes
    assert "tracked_manifest_duplicate_path" in codes


def test_index_log_contract_rejects_free_body_and_staged_markdown_tampering(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    json_path, markdown_path = _copy_log_pair(root)
    entries = [
        _manifest_entry(root, json_path, "sanitized_log"),
        _manifest_entry(root, markdown_path, "sanitized_log"),
    ]
    _write_manifest(root, entries)
    _git(root, "add", ".")

    # Dirty worktree content cannot affect an index-only decision.
    _write(root, markdown_path, "private dirty worktree body\n")
    assert module.scan_tracked_state(root) == []

    payload = json.loads((root / json_path).read_text(encoding="ascii"))
    payload["free_body"] = "private internal acquisition plans"
    _write(root, json_path, json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    entries = [
        _manifest_entry(root, json_path, "sanitized_log"),
        _manifest_entry(root, markdown_path, "sanitized_log"),
    ]
    _write_manifest(root, entries)
    _git(root, "add", ".")
    assert (json_path, "sanitized_log_contract_invalid") in {
        (item.path, item.code) for item in module.scan_tracked_state(root)
    }

    _write(root, json_path, (LOG_FIXTURE_ROOT / "logs.sanitized.json").read_text(encoding="ascii"))
    _write(root, markdown_path, "tampered sanitized markdown\n")
    entries = [
        _manifest_entry(root, json_path, "sanitized_log"),
        _manifest_entry(root, markdown_path, "sanitized_log"),
    ]
    _write_manifest(root, entries)
    _git(root, "add", ".")
    assert (json_path, "sanitized_log_markdown_mismatch") in {
        (item.path, item.code) for item in module.scan_tracked_state(root)
    }


def test_index_log_contract_requires_the_markdown_pair(tmp_path: Path) -> None:
    module = _module()
    root = _repo(tmp_path)
    json_path, _ = _copy_log_pair(root)
    _write_manifest(root, [_manifest_entry(root, json_path, "sanitized_log")])
    _git(root, "add", json_path, module.MANIFEST_PATH)

    assert (json_path, "sanitized_log_pair_missing") in {
        (item.path, item.code) for item in module.scan_tracked_state(root)
    }
