from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROTECTED_COMPANY_ENTRIES = (
    "_index.json",
    "company.json",
    "reports",
    "metrics",
    "evidence",
    "semantic",
    "graph",
)
DERIVED_COMPANY_DIRS = frozenset({"analysis", "factcheck", "tracking"})


@dataclass(frozen=True)
class FactSurfaceSnapshot:
    digest: str
    files: dict[str, str]
    covered_entries: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "files": dict(self.files),
            "covered_entries": list(self.covered_entries),
        }


def snapshot_company_fact_surface(company_dir: Path | str) -> FactSurfaceSnapshot:
    """Hash immutable company inputs while excluding all derived workspaces."""

    unresolved_root = Path(company_dir)
    if unresolved_root.is_symlink():
        raise AssertionError(f"symlink is not accepted as company directory: {unresolved_root}")
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise AssertionError(f"company directory does not exist: {root}")

    files: dict[str, str] = {}
    covered: list[str] = []
    for entry_name in PROTECTED_COMPANY_ENTRIES:
        entry = root / entry_name
        if not entry.exists():
            continue
        covered.append(entry_name)
        for path in _fact_files(entry):
            relative = path.relative_to(root).as_posix()
            if relative.split("/", 1)[0] in DERIVED_COMPANY_DIRS:
                raise AssertionError(f"derived path entered fact snapshot: {relative}")
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    for label, index_path in (
        ("companies/_index.json", root.parent / "_index.json"),
        ("market/_index.json", root.parent.parent / "_index.json"),
    ):
        if index_path.exists():
            if index_path.is_symlink() or not index_path.is_file():
                raise AssertionError(f"unsafe fact index path: {index_path}")
            covered.append(label)
            files[label] = hashlib.sha256(index_path.read_bytes()).hexdigest()

    digest = hashlib.sha256()
    for relative, file_hash in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return FactSurfaceSnapshot(
        digest=digest.hexdigest(),
        files=files,
        covered_entries=tuple(covered),
    )


def assert_fact_surface_unchanged(
    before: FactSurfaceSnapshot,
    after: FactSurfaceSnapshot,
) -> None:
    if before == after:
        return
    before_names = set(before.files)
    after_names = set(after.files)
    added = sorted(after_names - before_names)
    removed = sorted(before_names - after_names)
    changed = sorted(
        name
        for name in before_names & after_names
        if before.files[name] != after.files[name]
    )
    raise AssertionError(
        "company fact surface changed: "
        f"added={added}, removed={removed}, changed={changed}, "
        f"before={before.digest}, after={after.digest}"
    )


def _fact_files(entry: Path) -> Iterable[Path]:
    if entry.is_symlink():
        raise AssertionError(f"symlink is not accepted in fact surface: {entry}")
    if entry.is_file():
        yield entry
        return
    if not entry.is_dir():
        raise AssertionError(f"unsupported fact surface entry: {entry}")
    for path in sorted(entry.rglob("*")):
        if path.is_symlink():
            raise AssertionError(f"symlink is not accepted in fact surface: {path}")
        if path.is_file():
            yield path
