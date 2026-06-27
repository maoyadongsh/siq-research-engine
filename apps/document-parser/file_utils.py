"""File detection and safe path helpers."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path

from contracts import SUPPORTED_EXTENSIONS


DOCUMENT_KIND_BY_EXTENSION = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".jp2": "image",
    ".webp": "image",
    ".gif": "image",
    ".bmp": "image",
    ".doc": "word",
    ".docx": "word",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".xls": "excel",
    ".xlsx": "excel",
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}


def safe_client_filename(filename: str | None) -> str:
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[\r\n\x00]", "_", name)
    name = re.sub(r"[^\w.\- ()\[\]\u4e00-\u9fff]", "_", name, flags=re.UNICODE)
    return name or "document.txt"


def document_kind_for_extension(extension: str) -> str:
    return DOCUMENT_KIND_BY_EXTENSION.get(extension.lower(), "unknown")


def validate_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {extension or '(none)'}")
    return extension


def guess_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(filename)[0] or fallback


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_artifact_path(result_dir: Path, artifact: str) -> Path:
    normalized = artifact.strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError("Invalid artifact path")
    path = (result_dir / normalized).resolve()
    path.relative_to(result_dir.resolve())
    return path
