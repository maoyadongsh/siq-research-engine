from __future__ import annotations

from pathlib import Path
from typing import Any


def market_package_build_args(
    *,
    executable: str,
    script: Path,
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    parser_result_path: Path | None = None,
    force: bool = False,
) -> list[str]:
    args = [executable, str(script), str(source_path)]
    if metadata_path is not None:
        args.extend(["--metadata", str(metadata_path)])
    if parser_result_path is not None:
        args.extend(["--parser-result", str(parser_result_path)])
    args.extend(["--output-root", str(output_root)])
    if force:
        args.append("--force")
    return args


def market_package_import_args(
    *,
    executable: str,
    script: Path,
    market: str,
    package_dir: Path,
    payload: dict[str, Any],
) -> list[str]:
    args = [executable, str(script)]
    if market == "US":
        args.extend(["--package", str(package_dir)])
    else:
        args.append(str(package_dir))

    database_url = payload.get("database_url")
    if database_url:
        args.extend(["--database-url", str(database_url)])
    if payload.get("ddl") or payload.get("run_ddl"):
        args.append("--ddl")
    return args


def market_vector_ingest_args(
    *,
    executable: str,
    script: Path,
    package_dir: Path,
    payload: dict[str, Any],
) -> tuple[list[str], bool]:
    args = [
        executable,
        str(script),
        "--package",
        str(package_dir),
        "--batch-tag",
        str(payload.get("batch_tag") or "market-evidence"),
    ]
    for key, flag in (
        ("collection", "--collection"),
        ("embed_url", "--embed-url"),
        ("embed_model", "--embed-model"),
        ("vector_dim", "--vector-dim"),
    ):
        value = payload.get(key)
        if value not in (None, ""):
            args.extend([flag, str(value)])
    dry_run = bool(payload.get("dry_run", True))
    if dry_run:
        args.append("--dry-run")
    return args, dry_run


def _absolute_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def market_ingestion_eval_args(
    *,
    executable: str,
    script: Path,
    payload: dict[str, Any],
    repo_root: Path,
    default_output: Path,
    default_markdown: Path,
) -> tuple[list[str], Path, Path]:
    output = _absolute_path(payload.get("output") or default_output, repo_root=repo_root)
    markdown = _absolute_path(payload.get("markdown") or default_markdown, repo_root=repo_root)
    return (
        [
            executable,
            str(script),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        output,
        markdown,
    )


def us_sec_ingest_args(
    *,
    executable: str,
    script: Path,
    case_set_path: Path,
    report_path: Path,
    payload: dict[str, Any],
    tickers: str = "",
    batch_tag: str = "",
) -> list[str]:
    args = [
        executable,
        str(script),
        "--case-set",
        str(case_set_path),
        "--report",
        str(report_path),
    ]
    if payload.get("include_fail"):
        args.append("--include-fail")
    if payload.get("postgres"):
        args.append("--postgres")
    if payload.get("milvus"):
        args.append("--milvus")
    if payload.get("ddl"):
        args.append("--ddl")
    if payload.get("dry_run", True):
        args.append("--dry-run")
    if tickers:
        args.extend(["--tickers", tickers])
    if batch_tag:
        args.extend(["--batch-tag", batch_tag])
    return args


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
