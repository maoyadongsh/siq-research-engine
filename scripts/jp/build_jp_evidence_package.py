#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from jp_evidence_lib import REPO_ROOT, write_jp_evidence_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a JP EDINET market evidence package from XBRL zip/xml.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--parser-result", type=Path, default=None, help="Optional PDF parser result directory for fallback tables.")
    parser.add_argument("--output-root", type=Path, default=Path(os.environ.get("SIQ_JP_WIKI_ROOT", REPO_ROOT / "data" / "wiki" / "jp_reports")))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    package_dir = write_jp_evidence_package(
        args.source.resolve(),
        args.output_root.resolve(),
        args.metadata.resolve() if args.metadata else None,
        args.parser_result.resolve() if args.parser_result else None,
        force=args.force,
    )
    print(package_dir)


if __name__ == "__main__":
    main()
