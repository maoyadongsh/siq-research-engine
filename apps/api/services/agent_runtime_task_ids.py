"""Task-id evidence helpers for the Hermes agent runtime."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

TASK_ID_FIELD_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
API_TASK_ID_RE = re.compile(r"/api/(?:pdf_page|source)/([0-9a-fA-F-]{32,36})(?:[/?#]|$)")


def is_task_id_like(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{32,36}", str(value or "").strip()))


def pdf2md_task_result_dir(task_id: str, *, roots: Sequence[Path]) -> Path | None:
    if not is_task_id_like(task_id):
        return None
    for root in roots:
        candidate = root / task_id
        if not candidate.is_dir():
            continue
        expected_artifacts = ("result.md", "result_complete.md", "document_full.json", "content_list.json")
        if any((candidate / name).exists() for name in expected_artifacts):
            return candidate
    return None


def pdf2md_task_output_dir(task_id: str, *, roots: Sequence[Path]) -> Path | None:
    if not is_task_id_like(task_id):
        return None
    for root in roots:
        candidate = root / task_id
        if candidate.exists():
            return candidate
    return None


def file_contains_bytes(path: Path, needle: bytes) -> bool:
    if not needle:
        return False
    try:
        with path.open("rb") as handle:
            overlap = max(len(needle) - 1, 0)
            previous = b""
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                haystack = previous + chunk
                if needle in haystack:
                    return True
                previous = haystack[-overlap:] if overlap else b""
    except Exception:
        return False


def company_wiki_contains_task_id(company_dir: Path, task_id: str) -> bool:
    if not company_dir.exists() or not is_task_id_like(task_id):
        return False
    needle = task_id.encode("utf-8")
    preferred_files: list[Path] = [
        company_dir / "company.json",
        *(company_dir / "reports").glob("*/artifact_manifest.json"),
        *(company_dir / "reports").glob("*/report.json"),
        *(company_dir / "reports").glob("*/document_full.json"),
        *(company_dir / "metrics").glob("**/*.json"),
        *(company_dir / "evidence").glob("*.json"),
        *(company_dir / "semantic").glob("*.json"),
    ]
    seen: set[Path] = set()
    for path in preferred_files:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if file_contains_bytes(path, needle):
            return True
    return False


def wiki_task_id_exists(
    task_id: str,
    message: str = "",
    context: Any | None = None,
    *,
    wiki_root: os.PathLike[str] | str,
    resolve_company_dirs: Callable[..., Sequence[Path]],
) -> bool:
    if not is_task_id_like(task_id):
        return False
    company_dirs = list(resolve_company_dirs(message, context, limit=6)) if message or context else []
    for company_dir in company_dirs:
        if company_wiki_contains_task_id(company_dir, task_id):
            return True

    companies_dir = Path(wiki_root) / "companies"
    if not companies_dir.exists():
        return False
    needle = task_id.encode("utf-8")
    try:
        manifests = list(companies_dir.glob("*/company.json"))
        manifests.extend(companies_dir.glob("*/reports/*/artifact_manifest.json"))
        manifests.extend(companies_dir.glob("*/reports/*/report.json"))
    except Exception:
        return False
    return any(path.is_file() and file_contains_bytes(path, needle) for path in manifests[:3000])


def task_id_exists(
    task_id: str,
    message: str = "",
    context: Any | None = None,
    *,
    pdf2md_result_roots: Sequence[Path],
    pdf2md_output_roots: Sequence[Path],
    wiki_root: os.PathLike[str] | str,
    resolve_company_dirs: Callable[..., Sequence[Path]],
) -> bool:
    task_id = str(task_id or "").strip()
    if not is_task_id_like(task_id):
        return False
    return bool(
        pdf2md_task_result_dir(task_id, roots=pdf2md_result_roots)
        or pdf2md_task_output_dir(task_id, roots=pdf2md_output_roots)
        or wiki_task_id_exists(
            task_id,
            message,
            context,
            wiki_root=wiki_root,
            resolve_company_dirs=resolve_company_dirs,
        )
    )


def extract_task_ids_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    task_ids = [match.group(1) for match in TASK_ID_FIELD_RE.finditer(text)]
    task_ids.extend(match.group(1) for match in API_TASK_ID_RE.finditer(text))
    return sorted(dict.fromkeys(task_id.strip() for task_id in task_ids if is_task_id_like(task_id)))


def invalid_task_ids_in_reply(
    message: str,
    context: Any | None,
    reply: str,
    *,
    pdf2md_result_roots: Sequence[Path],
    pdf2md_output_roots: Sequence[Path],
    wiki_root: os.PathLike[str] | str,
    resolve_company_dirs: Callable[..., Sequence[Path]],
) -> list[str]:
    return [
        task_id
        for task_id in extract_task_ids_from_text(reply)
        if not task_id_exists(
            task_id,
            message,
            context,
            pdf2md_result_roots=pdf2md_result_roots,
            pdf2md_output_roots=pdf2md_output_roots,
            wiki_root=wiki_root,
            resolve_company_dirs=resolve_company_dirs,
        )
    ]


__all__ = [
    "API_TASK_ID_RE",
    "TASK_ID_FIELD_RE",
    "company_wiki_contains_task_id",
    "extract_task_ids_from_text",
    "file_contains_bytes",
    "invalid_task_ids_in_reply",
    "is_task_id_like",
    "pdf2md_task_output_dir",
    "pdf2md_task_result_dir",
    "task_id_exists",
    "wiki_task_id_exists",
]
