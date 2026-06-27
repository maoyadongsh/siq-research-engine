#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from eu_pdf_evidence_lib import REPO_ROOT, write_eu_pdf_evidence_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an EU PDF market evidence package from a PDF parser result.")
    parser.add_argument("source_path", type=Path)
    parser.add_argument("--parser-result", type=Path, required=True, help="Directory containing document_full.json")
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("SIQ_EU_WIKI_ROOT", REPO_ROOT / "data" / "wiki" / "eu_reports")),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    package_dir = write_eu_pdf_evidence_package(
        args.source_path.resolve(),
        args.parser_result.resolve(),
        args.output_root.resolve(),
        args.metadata.resolve() if args.metadata else None,
        force=args.force,
    )
    print(package_dir)


if __name__ == "__main__":
    main()
