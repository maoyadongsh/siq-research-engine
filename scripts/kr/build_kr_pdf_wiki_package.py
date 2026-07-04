from __future__ import annotations

import argparse
from pathlib import Path

from kr_pdf_wiki_lib import write_kr_pdf_wiki_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a KR PDF wiki evidence package")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--parser-result", required=True, type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/wiki/kr"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    package_dir = write_kr_pdf_wiki_package(
        args.pdf,
        args.parser_result,
        args.output_root,
        args.metadata,
        force=args.force,
    )
    print(package_dir)


if __name__ == "__main__":
    main()
