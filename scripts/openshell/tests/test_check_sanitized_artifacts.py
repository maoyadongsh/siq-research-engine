from __future__ import annotations

import json
from pathlib import Path

from scripts.openshell.check_sanitized_artifacts import scan_paths


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_clean_json_manifest_passes(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "manifest.json",
        json.dumps(
            {
                "schema_version": "test.v1",
                "api_key": "<redacted>",
                "token_status": "not_configured",
                "candidate": {"version": "0.0.83"},
            }
        ),
    )

    assert scan_paths([path]) == []


def test_sensitive_json_value_is_rejected_without_echoing_value(tmp_path: Path) -> None:
    path = _write(tmp_path / "manifest.json", '{"provider_token": "never-commit-this-value"}')

    findings = scan_paths([path])

    assert any(item.code == "json_sensitive_value" for item in findings)
    assert all("never-commit" not in (item.detail or "") for item in findings)


def test_prompt_messages_and_response_bodies_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "trace.json",
        json.dumps(
            {
                "prompt": "internal research question",
                "messages": [{"role": "user", "content": "private"}],
                "response_body": "private answer",
            }
        ),
    )

    findings = scan_paths([path])

    assert sum(item.code == "json_business_content" for item in findings) == 4
    assert all("internal research" not in (item.detail or "") for item in findings)


def test_question_query_and_generic_content_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "trace.json",
        json.dumps(
            {
                "question": "private research question",
                "query": "private retrieval query",
                "content": "private business content",
            }
        ),
    )

    findings = scan_paths([path])

    assert sum(item.code == "json_business_content" for item in findings) == 3


def test_markdown_business_content_sections_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "trace.md", "# Prompt\nprivate question\n\nUser Input: private\n")

    codes = {item.code for item in scan_paths([path])}

    assert "business_content_label" in codes


def test_invalid_json_evidence_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "manifest.json", "{not-json}\n")

    findings = scan_paths([path])

    assert any(item.code == "invalid_json" for item in findings)


def test_text_secrets_and_local_paths_are_rejected(tmp_path: Path) -> None:
    bearer = "Bearer " + ("a" * 20)
    private_key_marker = "-----BEGIN " + "PRIVATE KEY" + "-----"
    local_path = "/" + "home" + "/example/private.txt"
    path = _write(
        tmp_path / "evidence.md",
        f"Authorization: {bearer}\nsource: {local_path}\n{private_key_marker}\n",
    )

    codes = {item.code for item in scan_paths([path])}

    assert {"bearer_token", "local_absolute_path", "private_key"} <= codes


def test_prefixed_assignments_dsn_and_root_paths_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "evidence.md",
        "SIQ_API_KEY=not-redacted\n"
        "provider_token: not-redacted\n"
        "database_url=postgresql://user:password@example.invalid/siq\n"
        "output=/root/private/result.json\n"
        "cache=/home/example/cache\n",
    )

    codes = {item.code for item in scan_paths([path])}

    assert {"sensitive_assignment", "credential_url", "local_absolute_path"} <= codes


def test_access_key_id_passphrase_and_case_insensitive_private_key_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "evidence.md",
        "access_key_id=" + "AKIA" + "ABCDEFGHIJKLMNOP\n"
        "signing_passphrase=must-not-survive\n"
        "-----begin private key-----\n",
    )

    codes = {item.code for item in scan_paths([path])}

    assert {"access_key_id", "sensitive_assignment", "private_key"} <= codes


def test_directory_scan_is_explicit_and_symlinks_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    _write(root / "ok.md", "status: passed\n")
    link = root / "link.md"
    link.symlink_to(root / "ok.md")

    findings = scan_paths([root])

    assert any(item.code == "symlink_not_allowed" for item in findings)
