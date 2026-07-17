#!/usr/bin/env python3
"""Export explicitly selected evidence as checked sanitized JSON and Markdown."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts.openshell import check_sanitized_artifacts
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import check_sanitized_artifacts


SCHEMA_VERSION = "siq.openshell.sanitized-evidence.v1"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_FILES = 64
ALLOWED_INPUT_SUFFIXES = {".json", ".md"}
DROP_KEY_RE = re.compile(
    r"(?:"
    r"api[_-]?key|access[_-]?key(?:[_-]?id)?|access[_-]?token|auth(?:orization)?|cookie|credential|"
    r"database[_-]?url|dsn|passphrase|password|passwd|private[_-]?key|secret|token|"
    r"(?:system[_-]?)?prompt|user[_-]?input|request[_-]?body|response[_-]?body|user[_-]?home|"
    r"chat[_-]?history|conversation|document[_-]?text|messages|model[_-]?output|"
    r"raw[_-]?(?:input|output|response)|tool[_-]?output"
    r")",
    re.IGNORECASE,
)
# Runtime identity and probe nonce fields prove a single local run but add no
# review value after the result has been reduced to aggregate security checks.
DROP_RUNTIME_IDENTITY_KEYS = frozenset(
    {
        "container_id",
        "probe_id",
        "run_nonce_sha256",
        "runtime_snapshot",
        "sandbox_id",
        "sandbox_name",
        "sentinel_marker",
        "sentinel_name",
        "policy",
        "mount_plan",
    }
)
ATTACHMENT_CONTAINER_RE = re.compile(r"attachments?", re.IGNORECASE)
ATTACHMENT_BODY_KEY_RE = re.compile(
    r"(?:body|bytes|content|data|payload|raw|text)",
    re.IGNORECASE,
)
ATTACHMENT_BODY_COMPOUND_KEY_RE = re.compile(
    r"(?:attachment.*(?:body|bytes|content|data|payload|raw|text)|"
    r"(?:body|bytes|content|data|payload|raw|text).*attachment)",
    re.IGNORECASE,
)
SENSITIVE_ROLE_RE = re.compile(r"(?:developer|system|user)", re.IGNORECASE)
ROLE_BODY_KEY_RE = re.compile(r"(?:body|content|message|prompt|text)", re.IGNORECASE)
PRIVATE_TEXT_KEY_RE = re.compile(r"(?:content|query|question)", re.IGNORECASE)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [^-\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----.*?"
    r"-----END [^-\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----",
    re.IGNORECASE | re.DOTALL,
)
PRIVATE_KEY_MARKER_RE = re.compile(r"(?im)^.*-----BEGIN [^-\n]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----.*$")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
CREDENTIAL_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@[^\s]+", re.IGNORECASE)
DATABASE_DSN_RE = re.compile(
    r"\b(?:mariadb|mongodb(?:\+srv)?|mssql|mysql|oracle|postgres(?:ql)?|redis)://[^\s\]})>'\"]+",
    re.IGNORECASE,
)
SENSITIVE_HEADER_LINE_RE = re.compile(r"(?im)^\s*(?:authorization|cookie|set-cookie)\s*[:=].*$")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?P<key>[A-Za-z][A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|access[_-]?token|"
    r"authorization|cookie|database[_-]?url|dsn|passphrase|password|passwd|secret|token)"
    r"[A-Za-z0-9_-]*)\s*(?P<separator>[:=])\s*(?P<value>[^\s,}\]]+)"
)
POSIX_MACHINE_PATH_RE = re.compile(
    r"(?P<prefix>^|[\s\"'`=:])/(?!/)(?:[A-Za-z0-9._~-]+)(?:/[^\s\"'`<>]*)?",
    re.MULTILINE,
)
WINDOWS_MACHINE_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s\"'`<>]+")
HOME_REFERENCE_RE = re.compile(r"(?<![A-Za-z0-9])(?:~|\$HOME|\$\{HOME\})(?:/[^\s\"'`<>]*)?")
MARKDOWN_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.*?)\s*$")
SENSITIVE_SECTION_RE = re.compile(
    r"^(?:"
    r"attachment(?:\s+(?:body|content|data|payload|text))?|"
    r"prompt(?:\s+(?:body|content|text))?|"
    r"user[ _-]?input|"
    r"附件(?:正文|内容|数据)?|提示词(?:正文|内容)?|用户输入"
    r")$",
    re.IGNORECASE,
)
SENSITIVE_MARKDOWN_LINE_RE = re.compile(
    r"^\s*(?:"
    r"attachment(?:[_ -]?(?:body|content|data|payload|text))?|"
    r"prompt|user[_ -]?input|附件(?:正文|内容)?|提示词|用户输入"
    r")\s*[:=]",
    re.IGNORECASE,
)


class EvidenceExportError(RuntimeError):
    """Stable export error that never includes source content or machine paths."""


def _walk_without_symlinks(path: Path, *, missing_code: str, symlink_code: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise EvidenceExportError(missing_code) from exc
        if stat.S_ISLNK(mode):
            raise EvidenceExportError(symlink_code)


def _safe_input_file(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    _walk_without_symlinks(
        candidate,
        missing_code="evidence_input_missing",
        symlink_code="evidence_input_symlink_not_allowed",
    )
    info = candidate.stat()
    if not stat.S_ISREG(info.st_mode):
        raise EvidenceExportError("evidence_input_regular_file_required")
    if candidate.suffix.lower() not in ALLOWED_INPUT_SUFFIXES:
        raise EvidenceExportError("evidence_input_suffix_not_allowed")
    if info.st_size > MAX_INPUT_BYTES:
        raise EvidenceExportError("evidence_input_too_large")
    return candidate


def _safe_output_root(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    parent = candidate.parent
    _walk_without_symlinks(
        parent,
        missing_code="evidence_output_parent_missing",
        symlink_code="evidence_output_symlink_not_allowed",
    )
    if candidate.exists():
        if candidate.is_symlink() or not candidate.is_dir():
            raise EvidenceExportError("evidence_output_root_invalid")
    else:
        candidate.mkdir(mode=0o700)
    if candidate.is_symlink() or not candidate.is_dir():
        raise EvidenceExportError("evidence_output_root_invalid")
    return candidate


def sanitize_text(value: str) -> str:
    sanitized = PRIVATE_KEY_BLOCK_RE.sub("<redacted>", value)
    sanitized = PRIVATE_KEY_MARKER_RE.sub("<redacted>", sanitized)
    sanitized = SENSITIVE_HEADER_LINE_RE.sub("", sanitized)
    sanitized = BEARER_RE.sub("Bearer <redacted>", sanitized)
    sanitized = CREDENTIAL_URL_RE.sub("<redacted-dsn>", sanitized)
    sanitized = DATABASE_DSN_RE.sub("<redacted-dsn>", sanitized)
    sanitized = SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}<redacted>",
        sanitized,
    )
    sanitized = POSIX_MACHINE_PATH_RE.sub(
        lambda match: f"{match.group('prefix')}<redacted-path>",
        sanitized,
    )
    sanitized = WINDOWS_MACHINE_PATH_RE.sub("<redacted-path>", sanitized)
    return HOME_REFERENCE_RE.sub("<redacted-home>", sanitized)


def sanitize_markdown(value: str) -> str:
    output: list[str] = []
    skipped_heading_level: int | None = None
    for raw_line in value.splitlines():
        heading = MARKDOWN_HEADING_RE.match(raw_line)
        if skipped_heading_level is not None:
            if heading and len(heading.group("marks")) <= skipped_heading_level:
                skipped_heading_level = None
            else:
                continue
        if heading and SENSITIVE_SECTION_RE.fullmatch(heading.group("title").strip()):
            skipped_heading_level = len(heading.group("marks"))
            continue
        if SENSITIVE_MARKDOWN_LINE_RE.match(raw_line):
            continue
        line = sanitize_text(raw_line).rstrip()
        if line or (output and output[-1]):
            output.append(line)
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + ("\n" if output else "")


def sanitize_json_value(
    value: Any,
    *,
    attachment_context: bool = False,
    role_sensitive: bool = False,
) -> Any:
    if isinstance(value, dict):
        role = value.get("role")
        current_role_sensitive = role_sensitive or (
            isinstance(role, str) and bool(SENSITIVE_ROLE_RE.fullmatch(role.strip()))
        )
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if key.lower() in DROP_RUNTIME_IDENTITY_KEYS:
                continue
            if (
                DROP_KEY_RE.search(key)
                or PRIVATE_TEXT_KEY_RE.fullmatch(key)
                or ATTACHMENT_BODY_COMPOUND_KEY_RE.search(key)
                or key.lower() in {"home", "home_dir", "home_path"}
            ):
                continue
            if ATTACHMENT_CONTAINER_RE.fullmatch(key) and not isinstance(child, (dict, list)):
                continue
            if attachment_context and ATTACHMENT_BODY_KEY_RE.fullmatch(key):
                continue
            if current_role_sensitive and ROLE_BODY_KEY_RE.fullmatch(key):
                continue
            child_attachment = attachment_context or bool(ATTACHMENT_CONTAINER_RE.fullmatch(key))
            result[key] = sanitize_json_value(
                child,
                attachment_context=child_attachment,
                role_sensitive=current_role_sensitive,
            )
        return result
    if isinstance(value, list):
        return [
            sanitize_json_value(
                item,
                attachment_context=attachment_context,
                role_sensitive=role_sensitive,
            )
            for item in value
        ]
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "<redacted-unsupported-value>"


def _audit_summary_markdown(summary: Mapping[str, Any]) -> str:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    overhead = metrics.get("gateway_overhead_ms") if isinstance(metrics.get("gateway_overhead_ms"), dict) else {}
    lines = [
        "# SIQ OpenShell Audit Summary",
        "",
        f"- Schema version: `{summary.get('schema_version', 'unknown')}`",
        f"- Source schema: `{summary.get('source_schema_version', 'unknown')}`",
        f"- Records: `{summary.get('record_count', 0)}`",
        f"- Policy denies: `{metrics.get('policy_deny_count', 0)}`",
        f"- Audit-only decisions: `{metrics.get('audit_only_count', 0)}`",
        f"- Sandbox start failures: `{metrics.get('sandbox_start_failures', 0)}`",
        f"- Tool failure rate: `{metrics.get('tool_failure_rate', 0)}`",
        f"- External upload blocks: `{metrics.get('external_upload_blocks', 0)}`",
        f"- Immutable write blocks: `{metrics.get('immutable_write_blocks', 0)}`",
        f"- Gateway overhead P50/P95 ms: `{overhead.get('p50')}` / `{overhead.get('p95')}`",
        "",
    ]
    for title, key in (
        ("Decisions", "decisions"),
        ("Profiles", "profiles"),
        ("Deny Rule/Error IDs", "deny_error_codes"),
        ("Operation Classes", "operation_classes"),
        ("Policy Digests", "policy_digests"),
    ):
        values = summary.get(key)
        if not isinstance(values, dict):
            continue
        lines.extend([f"## {title}", "", "| ID | Count |", "|---|---:|"])
        lines.extend(f"| `{name}` | {count} |" for name, count in sorted(values.items()))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json_markdown(payload: Any) -> str:
    if isinstance(payload, dict) and payload.get("schema_version") == "siq.openshell.audit-summary.v1":
        return _audit_summary_markdown(payload)
    rendered = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    return f"# Sanitized SIQ Evidence\n\n```json\n{rendered}\n```\n"


def _atomic_write(path: Path, content: bytes) -> None:
    if path.exists():
        raise EvidenceExportError("evidence_output_exists")
    if path.is_symlink():
        raise EvidenceExportError("evidence_output_symlink_not_allowed")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise EvidenceExportError("evidence_output_exists") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _output_paths(input_path: Path, output_root: Path) -> tuple[Path, Path]:
    stem = input_path.stem
    if stem.endswith(".sanitized"):
        stem = stem.removesuffix(".sanitized")
    if not stem or stem in {".", ".."}:
        raise EvidenceExportError("evidence_output_name_invalid")
    return (
        output_root / f"{stem}.sanitized.json",
        output_root / f"{stem}.sanitized.md",
    )


def export_evidence(inputs: Iterable[Path], *, output_root: Path) -> list[Path]:
    requested = list(inputs)
    if not requested or len(requested) > MAX_INPUT_FILES:
        raise EvidenceExportError("evidence_input_count_invalid")
    root = _safe_output_root(output_root)
    prepared: list[tuple[Path, bytes]] = []
    output_names: set[str] = set()
    seen_inputs: set[Path] = set()
    for requested_input in requested:
        input_path = _safe_input_file(requested_input)
        resolved = input_path.resolve(strict=True)
        if resolved in seen_inputs:
            raise EvidenceExportError("evidence_input_duplicate")
        seen_inputs.add(resolved)
        json_output, markdown_output = _output_paths(input_path, root)
        if json_output.name in output_names or markdown_output.name in output_names:
            raise EvidenceExportError("evidence_output_name_collision")
        output_names.update({json_output.name, markdown_output.name})
        try:
            source_text = input_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceExportError("evidence_input_utf8_required") from exc
        if input_path.suffix.lower() == ".json":
            try:
                sanitized_payload = sanitize_json_value(json.loads(source_text))
            except json.JSONDecodeError as exc:
                raise EvidenceExportError("evidence_input_json_invalid") from exc
            markdown = render_json_markdown(sanitized_payload)
            evidence = {
                "schema_version": SCHEMA_VERSION,
                "source_format": "json",
                "evidence": sanitized_payload,
            }
        else:
            markdown = sanitize_markdown(source_text)
            evidence = {
                "schema_version": SCHEMA_VERSION,
                "source_format": "markdown",
                "document": markdown,
            }
        json_content = (json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
        prepared.append((json_output, json_content))
        prepared.append((markdown_output, sanitize_markdown(markdown).encode()))

    outputs: list[Path] = []
    try:
        for path, content in prepared:
            _atomic_write(path, content)
            outputs.append(path)
        findings = check_sanitized_artifacts.scan_paths(outputs)
        if findings:
            raise EvidenceExportError("sanitized_artifact_validation_failed")
    except Exception:
        for path in outputs:
            path.unlink(missing_ok=True)
        raise
    return outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="explicit JSON or Markdown input")
    parser.add_argument("--output-root", type=Path, required=True, help="explicit sanitized output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        outputs = export_evidence(args.input, output_root=args.output_root)
        print(
            json.dumps(
                {"ok": True, "schema_version": SCHEMA_VERSION, "output_count": len(outputs)},
                sort_keys=True,
            )
        )
        return 0
    except (EvidenceExportError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
