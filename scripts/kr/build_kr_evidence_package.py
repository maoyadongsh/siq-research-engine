#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from kr_evidence_lib import REPO_ROOT, write_kr_evidence_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a KR DART market evidence package from XBRL/XML/API json.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--parser-result", type=Path, default=None, help="Optional PDF parser result directory for fallback tables.")
    parser.add_argument("--output-root", type=Path, default=Path(os.environ.get("SIQ_KR_WIKI_ROOT", REPO_ROOT / "data" / "wiki" / "kr")))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.source.suffix.lower() == ".pdf" and args.parser_result:
        from kr_pdf_wiki_lib import write_kr_pdf_wiki_package

        package_dir = write_kr_pdf_wiki_package(
            args.source.resolve(),
            args.parser_result.resolve(),
            args.output_root.resolve(),
            args.metadata.resolve() if args.metadata else None,
            force=args.force,
        )
        print(package_dir)
        return

    package_dir = write_kr_evidence_package(
        args.source.resolve(),
        args.output_root.resolve(),
        args.metadata.resolve() if args.metadata else None,
        args.parser_result.resolve() if args.parser_result else None,
        force=args.force,
    )
    print(package_dir)


if __name__ == "__main__":
    main()
