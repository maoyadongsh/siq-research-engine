from __future__ import annotations

from pathlib import Path


def us_sec_rebuild_package_args(
    *,
    executable: str,
    script: Path,
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    force: bool = True,
) -> list[str]:
    args = [executable, str(script), str(source_path)]
    if force:
        args.append("--force")
    if metadata_path is not None:
        args.extend(["--metadata", str(metadata_path)])
    args.extend(["--output-root", str(output_root)])
    return args
