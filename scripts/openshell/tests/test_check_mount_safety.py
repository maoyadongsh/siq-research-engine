from __future__ import annotations

from pathlib import Path

from scripts.openshell.check_mount_safety import scan_mount_root


def _write(path: Path, content: str = "safe\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sanitized_candidate_mount_passes(tmp_path: Path) -> None:
    _write(tmp_path / "apps/api/main.py")
    _write(tmp_path / "agents/hermes/profiles/siq_analysis/AGENTS.md")
    _write(tmp_path / "data/wiki/companies/600001-Test/reports/2025-annual/report.md")

    assert scan_mount_root(tmp_path) == []


def test_credentials_host_state_and_symlinks_are_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "services/finder/.env", "SECRET=hidden\n")
    _write(tmp_path / "data/hermes/home/profiles/siq_analysis/auth.json", "{}\n")
    _write(tmp_path / "var/openshell/xdg/state.json")
    _write(tmp_path / "safe.txt")
    (tmp_path / "outside-link").symlink_to(tmp_path / "safe.txt")

    findings = scan_mount_root(tmp_path)
    codes = {item.code for item in findings}

    assert {"credential_path_not_allowed", "forbidden_mount_subtree", "symlink_not_allowed"} <= codes


def test_private_key_marker_is_rejected_without_returning_content(tmp_path: Path) -> None:
    begin = "-----BEGIN " + "PRIVATE KEY-----"
    end = "-----END " + "PRIVATE KEY-----"
    key_block = f"{begin}\nc2VjcmV0LWtleS1tYXRlcmlhbC1tdXN0LW5vdC1sZWFr\n{end}\n"
    _write(tmp_path / "config.txt", key_block)

    findings = scan_mount_root(tmp_path)

    assert [(item.path, item.code) for item in findings] == [("config.txt", "private_key_material")]
    assert all("secret" not in item.path for item in findings)


def test_private_key_detection_source_code_is_not_key_material(tmp_path: Path) -> None:
    _write(
        tmp_path / "redact.py",
        'pattern = r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*-----END[A-Z ]*PRIVATE KEY-----"\n',
    )

    assert scan_mount_root(tmp_path) == []
