#!/usr/bin/env python3
"""Print effective SIQ filesystem paths without creating or moving files."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _path(value: Any) -> str:
    return str(Path(value).expanduser().resolve())


def _sequence(values: Any) -> list[str]:
    return [_path(value) for value in values]


def _api_paths() -> dict[str, Any]:
    module = _load_module("siq_api_path_config_probe", REPO_ROOT / "apps" / "api" / "services" / "path_config.py")
    keys = (
        "PROJECT_ROOT",
        "DATA_ROOT",
        "RUNTIME_ROOT",
        "ARTIFACTS_ROOT",
        "BACKEND_DATA_ROOT",
        "PDF2MD_DATA_ROOT",
        "PDF_RESULTS_ROOT",
        "PDF_OUTPUT_ROOT",
        "DOCUMENT_PARSER_DATA_ROOT",
        "DOCUMENT_PARSER_RESULTS_ROOT",
        "REPORT_DOWNLOADS_ROOT",
        "WIKI_ROOT",
        "HERMES_HOME",
    )
    result = {key.lower(): _path(getattr(module, key)) for key in keys if hasattr(module, key)}
    result["pdf_result_root_candidates"] = _sequence(module.PDF_RESULT_ROOT_CANDIDATES)
    result["document_parser_result_root_candidates"] = _sequence(module.DOCUMENT_PARSER_RESULT_ROOT_CANDIDATES)
    result["report_download_root_candidates"] = _sequence(module.REPORT_DOWNLOAD_ROOT_CANDIDATES)
    result["wiki_root_candidates"] = _sequence(module.WIKI_ROOT_CANDIDATES)
    return result


def _parser_paths(service: str, module_path: Path, base_dir: Path) -> dict[str, Any]:
    module = _load_module(f"siq_{service}_path_config_probe", module_path)
    paths = module.resolve_app_paths(base_dir)
    result: dict[str, Any] = {}
    for key, value in paths.items():
        if key.endswith("_candidates"):
            result[key] = _sequence(value)
        elif isinstance(value, (str, os.PathLike)):
            result[key] = _path(value)
        else:
            result[key] = value
    return result


def collect_paths() -> dict[str, Any]:
    return {
        "schema_version": "siq_effective_paths_v1",
        "read_only": True,
        "repo_root": _path(REPO_ROOT),
        "api": _api_paths(),
        "pdf_parser": _parser_paths(
            "pdf_parser",
            REPO_ROOT / "apps" / "pdf-parser" / "path_config.py",
            REPO_ROOT / "apps" / "pdf-parser",
        ),
        "document_parser": _parser_paths(
            "document_parser",
            REPO_ROOT / "apps" / "document-parser" / "path_config.py",
            REPO_ROOT / "apps" / "document-parser",
        ),
        "notes": [
            "This command only resolves and prints paths.",
            "It does not create, move, copy, delete, or migrate files.",
            "Run it after exporting env vars to inspect that environment's effective paths.",
        ],
    }


def _print_table(paths: dict[str, Any]) -> None:
    print("SIQ effective paths (read-only)")
    print(f"repo_root: {paths['repo_root']}")
    print()
    for section in ("api", "pdf_parser", "document_parser"):
        print(f"[{section}]")
        for key, value in paths[section].items():
            if isinstance(value, list):
                print(f"{key}:")
                for item in value:
                    print(f"  - {item}")
            else:
                print(f"{key}: {value}")
        print()
    for note in paths["notes"]:
        print(f"NOTE: {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print effective SIQ filesystem paths without side effects.")
    parser.add_argument("--json", action="store_true", help="Print paths as JSON.")
    args = parser.parse_args()

    paths = collect_paths()
    if args.json:
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        return
    _print_table(paths)


if __name__ == "__main__":
    main()
