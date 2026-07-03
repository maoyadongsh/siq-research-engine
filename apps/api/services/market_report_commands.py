from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ESEF_SOURCE_SUFFIXES = {".zip", ".xhtml", ".html", ".htm", ".xml", ".xbrl"}
PARSER_RESULT_MARKETS = {"HK", "JP", "KR", "EU"}


class MarketPackageBuildPlanError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class MarketPackagePlanError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class MarketPackageBuildPlan:
    market: str
    source_path: Path
    script: Path
    output_root: Path
    metadata_path: Path | None
    parser_result_path: Path | None
    force: bool


@dataclass(frozen=True)
class MarketPackageImportPlan:
    market: str
    package_dir: Path
    script: Path


@dataclass(frozen=True)
class MarketVectorIngestPlan:
    market: str
    package_dir: Path
    script: Path
    dry_run: bool


@dataclass(frozen=True)
class MarketIngestionEvalPlan:
    script: Path
    output_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class UsSecRebuildPackagePlan:
    ticker: str
    package_dir: Path
    source_path: Path
    metadata_path: Path | None
    script: Path
    output_root: Path


def select_market_build_script(
    *,
    market: str,
    source_path: Path,
    market_build_scripts: dict[str, Path],
    eu_esef_package_build_script: Path,
) -> Path:
    if market == "EU" and source_path.suffix.lower() in ESEF_SOURCE_SUFFIXES:
        return eu_esef_package_build_script
    return market_build_scripts[market]


def market_build_requires_parser_result(
    *,
    market: str,
    source_path: Path,
    market_build_scripts: dict[str, Path],
    eu_esef_package_build_script: Path,
) -> bool:
    if market == "EU":
        return select_market_build_script(
            market=market,
            source_path=source_path,
            market_build_scripts=market_build_scripts,
            eu_esef_package_build_script=eu_esef_package_build_script,
        ) == market_build_scripts[market]
    return market == "HK"


def market_build_accepts_parser_result(
    *,
    market: str,
    script: Path,
    eu_esef_package_build_script: Path,
) -> bool:
    if market == "EU" and script == eu_esef_package_build_script:
        return False
    return market in PARSER_RESULT_MARKETS


def _resolve_repo_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def build_market_package_build_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    repo_root: Path,
    market_wiki_roots: Mapping[str, Path],
    market_build_scripts: Mapping[str, Path],
    eu_esef_package_build_script: Path,
    safe_download_path: Callable[[str], Path],
    adjacent_metadata_path: Callable[[Path], Path | None],
) -> MarketPackageBuildPlan:
    download_relative_path = payload.get("download_relative_path")
    source = payload.get("source_path") or payload.get("pdf_path")
    if download_relative_path:
        source_path = safe_download_path(str(download_relative_path))
    else:
        source_path = _resolve_repo_path(source, repo_root=repo_root) if source else Path()

    if not source and not download_relative_path:
        raise MarketPackageBuildPlanError(400, "source_path or download_relative_path is required")
    if not source_path.is_file():
        raise MarketPackageBuildPlanError(404, "source_path not found")

    script = select_market_build_script(
        market=market,
        source_path=source_path,
        market_build_scripts=dict(market_build_scripts),
        eu_esef_package_build_script=eu_esef_package_build_script,
    )
    if not script.is_file():
        raise MarketPackageBuildPlanError(404, f"Missing package build script: {script}")

    metadata = payload.get("metadata_path")
    metadata_path: Path | None = None
    if metadata:
        metadata_path = _resolve_repo_path(metadata, repo_root=repo_root)
        if not metadata_path.is_file():
            raise MarketPackageBuildPlanError(404, "metadata_path not found")
    else:
        metadata_path = adjacent_metadata_path(source_path)

    parser_result = payload.get("parser_result")
    if market_build_requires_parser_result(
        market=market,
        source_path=source_path,
        market_build_scripts=dict(market_build_scripts),
        eu_esef_package_build_script=eu_esef_package_build_script,
    ) and not parser_result:
        raise MarketPackageBuildPlanError(400, f"parser_result is required for {market} package builds")

    parser_result_path: Path | None = None
    if parser_result and market_build_accepts_parser_result(
        market=market,
        script=script,
        eu_esef_package_build_script=eu_esef_package_build_script,
    ):
        parser_result_path = _resolve_repo_path(parser_result, repo_root=repo_root)
        if not parser_result_path.exists():
            raise MarketPackageBuildPlanError(404, "parser_result not found")

    return MarketPackageBuildPlan(
        market=market,
        source_path=source_path,
        script=script,
        output_root=market_wiki_roots[market],
        metadata_path=metadata_path,
        parser_result_path=parser_result_path,
        force=bool(payload.get("force")),
    )


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


def build_market_package_import_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    market_import_scripts: Mapping[str, Path],
    safe_market_package_path: Callable[[str, str], Path],
) -> MarketPackageImportPlan:
    package_dir = safe_market_package_path(market, str(payload.get("package_path") or ""))
    script = market_import_scripts[market]
    if not script.is_file():
        raise MarketPackagePlanError(404, f"Missing package import script: {script}")
    return MarketPackageImportPlan(
        market=market,
        package_dir=package_dir,
        script=script,
    )


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


def build_market_vector_ingest_plan(
    *,
    payload: Mapping[str, Any],
    market: str,
    vector_ingest_script: Path,
    safe_market_package_path: Callable[[str, str], Path],
) -> MarketVectorIngestPlan:
    package_dir = safe_market_package_path(market, str(payload.get("package_path") or ""))
    if not vector_ingest_script.is_file():
        raise MarketPackagePlanError(404, f"Missing vector ingest script: {vector_ingest_script}")
    return MarketVectorIngestPlan(
        market=market,
        package_dir=package_dir,
        script=vector_ingest_script,
        dry_run=bool(payload.get("dry_run", True)),
    )


def _absolute_path(value: str | Path, *, repo_root: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def build_market_ingestion_eval_plan(
    *,
    payload: Mapping[str, Any],
    eval_script: Path,
    repo_root: Path,
    default_output: Path,
    default_markdown: Path,
) -> MarketIngestionEvalPlan:
    if not eval_script.is_file():
        raise MarketPackagePlanError(404, f"Missing eval script: {eval_script}")
    return MarketIngestionEvalPlan(
        script=eval_script,
        output_path=_absolute_path(payload.get("output") or default_output, repo_root=repo_root),
        markdown_path=_absolute_path(payload.get("markdown") or default_markdown, repo_root=repo_root),
    )


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


def build_us_sec_rebuild_package_plan(
    *,
    ticker: str,
    latest_case_item: Callable[[str], Mapping[str, Any] | None],
    safe_package_path: Callable[[str], Path],
    read_json_file: Callable[[Path, Any], Any],
    safe_under: Callable[[Path, Path], Path],
    package_build_script: Path,
    output_root: Path,
) -> UsSecRebuildPackagePlan:
    normalized_ticker = str(ticker or "").strip().upper()
    item = latest_case_item(normalized_ticker)
    if not item:
        raise MarketPackagePlanError(404, f"No package for ticker {normalized_ticker}")
    package_dir = safe_package_path(str(item.get("package_path") or ""))
    manifest = read_json_file(package_dir / "manifest.json", {}) or {}
    local_source = str(manifest.get("local_source_path") or "raw/filing.htm")
    source_path = safe_under(package_dir, package_dir / local_source)
    if not source_path.is_file():
        raise MarketPackagePlanError(404, "Raw SEC filing source not found in package")
    if not package_build_script.is_file():
        raise MarketPackagePlanError(404, f"Missing package build script: {package_build_script}")
    metadata_path = package_dir / "raw" / "filing.metadata.json"
    return UsSecRebuildPackagePlan(
        ticker=normalized_ticker,
        package_dir=package_dir,
        source_path=source_path,
        metadata_path=metadata_path if metadata_path.is_file() else None,
        script=package_build_script,
        output_root=output_root,
    )


def _tail(value: str | None, limit: int) -> str:
    return (value or "")[-limit:]


def _last_stdout_line(stdout: str | None) -> str | None:
    lines = (stdout or "").strip().splitlines()
    return lines[-1] if lines else None


def market_package_build_result_payload(
    *,
    completed: Any,
    command: str,
    package: dict[str, Any] | None = None,
    missing_path_message: str = "Package build did not print a package path",
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    if returncode != 0:
        return {
            "ok": False,
            "returncode": returncode,
            "stdout": _tail(stdout, 4000),
            "stderr": _tail(stderr, 4000),
            "command": command,
        }
    if package is None:
        return {
            "ok": False,
            "returncode": returncode,
            "stdout": _tail(stdout, 4000),
            "stderr": missing_path_message,
            "command": command,
        }
    return {
        "ok": True,
        "package": package,
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "command": command,
    }


def market_package_import_result_payload(*, completed: Any, command: str) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "parse_run_id": _last_stdout_line(stdout) if returncode == 0 else None,
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "command": command,
    }


def _json_object_from_stdout(stdout: str | None) -> dict[str, Any] | None:
    if not stdout:
        return None
    decoder = json.JSONDecoder()
    text = stdout.rstrip()
    best: tuple[int, dict[str, Any]] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text[index:])
        except Exception:
            continue
        absolute_end = index + end
        trailing = text[absolute_end:]
        if not isinstance(parsed, dict):
            continue
        if trailing.strip():
            # Allow separate log lines after a JSON block, but reject same-line
            # suffixes and later brace-looking fragments so nested objects do
            # not win over their containing summary object.
            if not trailing.lstrip(" \t").startswith(("\n", "\r")):
                continue
            if any("{" in line or "}" in line for line in trailing.splitlines() if line.strip()):
                continue
        if best is None or absolute_end > best[0]:
            best = (absolute_end, parsed)
    return best[1] if best else None


def market_vector_ingest_result_payload(*, completed: Any, dry_run: bool, command: str) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "dry_run": dry_run,
        "returncode": returncode,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "summary": _json_object_from_stdout(stdout),
        "command": command,
    }


def market_ingestion_eval_result_payload(
    *,
    completed: Any,
    report: Any,
    markdown_path: str,
    command: str,
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "report": report,
        "markdown_path": markdown_path,
        "command": command,
    }


def us_sec_case_set_ingest_result_payload(
    *,
    completed: Any,
    report: Any,
    command: str,
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    returncode = getattr(completed, "returncode", 1)
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "command": command,
        "stdout": _tail(stdout, 8000),
        "stderr": _tail(stderr, 8000),
        "report": report,
    }


def us_sec_rebuild_package_result_payload(
    *,
    completed: Any,
    ticker: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    return {
        "ok": True,
        "ticker": ticker.upper(),
        "stdout": _tail(stdout, 4000),
        "stderr": _tail(stderr, 4000),
        "package": package,
    }
