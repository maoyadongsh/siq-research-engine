#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from sec_evidence_lib import DEFAULT_PARSER_RESULTS_ROOT, build_parser_result_from_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Build canonical US SEC parser artifacts from local HTML/iXBRL.")
    parser.add_argument("source", type=Path, help="Path to a local SEC .htm/.html filing")
    parser.add_argument("--metadata", type=Path, default=None, help="Optional finder metadata JSON")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_PARSER_RESULTS_ROOT,
        help="Canonical parser result root. Default: data/parser-results/us-sec",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite canonical parser artifacts if present")
    args = parser.parse_args()

    parser_result_dir = build_parser_result_from_source(
        args.source.resolve(),
        parser_results_root=args.output_root.resolve(),
        metadata_path=args.metadata,
        force=args.force,
    )
    print(parser_result_dir)


if __name__ == "__main__":
    main()
