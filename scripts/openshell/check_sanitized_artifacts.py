#!/usr/bin/env python3
"""Reject secrets and machine-local content in explicitly selected evidence files.

The checker is intentionally conservative and read-only. It never walks a runtime
directory implicitly; callers must pass each file or directory that is allowed to
be inspected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

MAX_FILE_BYTES = 8 * 1024 * 1024

SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|access[_-]?token|auth(?:orization)?|cookie|credential|dsn|"
    r"passphrase|password|passwd|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
SENSITIVE_CONTENT_KEY_RE = re.compile(
    r"^(?:"
    r"attachment(?:[_-]?(?:body|content|data|payload|raw|text))?|"
    r"chat[_-]?history|content|conversation|document[_-]?text|messages|query|question|"
    r"model[_-]?output|prompt|raw[_-]?(?:input|output|response)|"
    r"request[_-]?body|response[_-]?body|system[_-]?prompt|tool[_-]?output|user[_-]?input"
    r")$",
    re.IGNORECASE,
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----", re.IGNORECASE)
ACCESS_KEY_ID_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)
CREDENTIAL_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)
LOCAL_PATH_RE = re.compile(r"(?:^|[\s\"'`=:])/(?:home|Users|tmp|private|root)(?:/|\b)[^\s\"'`]*")
ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|[\s\"'`{,])"
    r"[a-z][a-z0-9_-]*(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|access[_-]?token|passphrase|"
    r"password|passwd|secret|token|authorization|cookie|dsn)"
    r"[a-z0-9_-]*\s*[:=]\s*([^\s,}\]]+)"
)
SENSITIVE_CONTENT_LINE_RE = re.compile(
    r"(?i)^\s*(?:#{1,6}\s*)?(?:"
    r"attachment(?:[_ -]?(?:body|content|data|payload|raw|text))?|"
    r"chat[_ -]?history|content|conversation|document[_ -]?text|messages|query|question|"
    r"model[_ -]?output|prompt|raw[_ -]?(?:input|output|response)|"
    r"request[_ -]?body|response[_ -]?body|system[_ -]?prompt|tool[_ -]?output|user[_ -]?input"
    r")(?:\s*[:=].*|\s*)$"
)

REDACTED_VALUES = {
    "",
    "***",
    "<redacted>",
    "[redacted]",
    "configured",
    "invalid",
    "missing",
    "not_configured",
    "placeholder",
    "redacted",
    "unset",
}


@dataclass(frozen=True)
class Finding:
    path: str
    code: str
    line: int | None = None
    detail: str | None = None


def _display(path: Path) -> str:
    return path.as_posix()


def _is_redacted(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower().strip("\"'") in REDACTED_VALUES


def _json_findings(path: Path, text: str) -> list[Finding]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    findings: list[Finding] = []

    def visit(value: Any, key_path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                name = str(key)
                child_path = f"{key_path}.{name}" if key_path else name
                if SENSITIVE_KEY_RE.search(name) and not _is_redacted(child):
                    findings.append(Finding(_display(path), "json_sensitive_value", detail=child_path))
                if SENSITIVE_CONTENT_KEY_RE.fullmatch(name) and not _is_redacted(child):
                    findings.append(Finding(_display(path), "json_business_content", detail=child_path))
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{key_path}[{index}]")

    visit(payload)
    return findings


def _text_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        checks = (
            (PRIVATE_KEY_RE, "private_key"),
            (ACCESS_KEY_ID_RE, "access_key_id"),
            (BEARER_RE, "bearer_token"),
            (CREDENTIAL_URL_RE, "credential_url"),
            (LOCAL_PATH_RE, "local_absolute_path"),
        )
        for pattern, code in checks:
            if pattern.search(line):
                findings.append(Finding(_display(path), code, line=line_number))
        assignment = ASSIGNMENT_RE.search(line)
        if assignment:
            raw_value = assignment.group(1).strip().strip("\"'")
            if not _is_redacted(raw_value):
                findings.append(Finding(_display(path), "sensitive_assignment", line=line_number))
        if SENSITIVE_CONTENT_LINE_RE.fullmatch(line):
            findings.append(Finding(_display(path), "business_content_label", line=line_number))
    return findings


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for requested in paths:
        path = requested.expanduser()
        if path.is_symlink():
            yield path
            continue
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink() or child.is_file():
                    yield child
            continue
        yield path


def scan_content(path: str | Path, content: bytes, *, max_file_bytes: int = MAX_FILE_BYTES) -> list[Finding]:
    """Scan already-loaded content without echoing it in findings."""
    display_path = Path(path)
    display = _display(display_path)
    if len(content) > max_file_bytes:
        return [Finding(display, "file_too_large", detail=str(len(content)))]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding(display, "unreadable_file", detail="UnicodeDecodeError")]
    findings = _text_findings(display_path, text)
    if display_path.suffix.lower() == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError:
            findings.append(Finding(display, "invalid_json"))
        else:
            findings.extend(_json_findings(display_path, text))
    return findings


def scan_paths(paths: Iterable[Path], *, max_file_bytes: int = MAX_FILE_BYTES) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[Path] = set()
    for path in _iter_files(paths):
        display = _display(path)
        if path in seen:
            continue
        seen.add(path)
        if path.is_symlink():
            findings.append(Finding(display, "symlink_not_allowed"))
            continue
        if not path.exists():
            findings.append(Finding(display, "missing_file"))
            continue
        if not path.is_file():
            findings.append(Finding(display, "not_a_regular_file"))
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            findings.append(Finding(display, "unreadable_file", detail=type(exc).__name__))
            continue
        findings.extend(scan_content(path, content, max_file_bytes=max_file_bytes))
    return sorted(set(findings), key=lambda item: (item.path, item.line or 0, item.code, item.detail or ""))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="explicit evidence files or directories to scan")
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_file_bytes <= 0:
        _parser().error("--max-file-bytes must be positive")
    findings = scan_paths(args.paths, max_file_bytes=args.max_file_bytes)
    result = {"ok": not findings, "finding_count": len(findings), "findings": [asdict(item) for item in findings]}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    elif findings:
        for finding in findings:
            location = f":{finding.line}" if finding.line else ""
            suffix = f" ({finding.detail})" if finding.detail else ""
            print(f"{finding.path}{location}: {finding.code}{suffix}")
    else:
        print("sanitized artifact scan: PASS")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
