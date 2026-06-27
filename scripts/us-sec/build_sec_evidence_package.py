#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from sec_evidence_lib import REPO_ROOT, write_evidence_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SIQ US SEC evidence package from local HTML/iXBRL.")
    parser.add_argument("source", type=Path, help="Path to a local SEC .htm/.html filing")
    parser.add_argument("--metadata", type=Path, default=None, help="Optional finder metadata JSON")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("SIQ_US_SEC_WIKI_ROOT", REPO_ROOT / "data" / "wiki" / "us_sec")),
        help="US SEC wiki root. Default: data/wiki/us_sec",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing package directory")
    args = parser.parse_args()

    package_dir = write_evidence_package(args.source.resolve(), args.output_root.resolve(), args.metadata, force=args.force)
    print(package_dir)


if __name__ == "__main__":
    main()
