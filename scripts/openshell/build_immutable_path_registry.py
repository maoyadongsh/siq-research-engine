#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.immutable_path_registry import (  # noqa: E402
    ImmutableRegistryError,
    build_immutable_registry,
    write_registry,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the fail-closed SIQ immutable path registry.")
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--wiki-root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("var/openshell/registry/immutable-paths.json"))
    parser.add_argument("--digest-output", type=Path)
    parser.add_argument("--generated-at", default=None, help="fixed RFC 3339 value; omitted for deterministic null")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the registry without writing files")
    mode.add_argument("--check", action="store_true", help="fail when existing output differs")
    parser.add_argument("--diff", action="store_true", help="print a unified diff when output differs")
    return parser


def _resolved(project_root: Path, value: Path) -> Path:
    return value if value.is_absolute() else project_root / value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    wiki_root = _resolved(project_root, args.wiki_root or Path("data/wiki"))
    output = _resolved(project_root, args.output)
    digest_output = _resolved(project_root, args.digest_output or args.output.with_suffix(".sha256"))
    try:
        build = build_immutable_registry(
            project_root=project_root,
            wiki_root=wiki_root,
            generated_at=args.generated_at,
        )
        if args.dry_run:
            sys.stdout.buffer.write(build.content)
            return 0

        existing = output.read_bytes() if output.is_file() and not output.is_symlink() else b""
        expected_digest = f"{build.digest}  {output.relative_to(project_root).as_posix()}\n".encode("ascii")
        existing_digest = (
            digest_output.read_bytes() if digest_output.is_file() and not digest_output.is_symlink() else b""
        )
        differs = existing != build.content or existing_digest != expected_digest
        if differs and args.diff:
            old_lines = existing.decode("utf-8", errors="replace").splitlines(keepends=True)
            new_lines = build.content.decode("utf-8").splitlines(keepends=True)
            sys.stdout.writelines(
                difflib.unified_diff(
                    old_lines, new_lines, fromfile="immutable-paths.current", tofile="immutable-paths.generated"
                )
            )
        if args.check:
            return 1 if differs else 0
        write_registry(
            build,
            project_root=project_root,
            output=output,
            digest_output=digest_output,
        )
        print(f"immutable registry: {build.payload['summary']['entry_count']} entries, sha256={build.digest}")
        return 0
    except ImmutableRegistryError as exc:
        print(f"immutable registry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
