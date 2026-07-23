#!/usr/bin/env python3
"""Persist SIQ legal opinions into a company's wiki legal/ folder."""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path


DEFAULT_WIKI_ROOT = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or Path(__file__).resolve().parents[5] / "data" / "wiki"
)
COMPANY_RE = re.compile(r"^\d{6}-.+")
SAFE_FILENAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def resolve_company_dir(wiki_root: Path, company: str) -> Path:
    companies_dir = wiki_root / "companies"
    company = company.strip()
    if not company:
        raise SystemExit("company 不能为空，例如 000333-美的集团 或 000333")

    direct = companies_dir / company
    if direct.is_dir():
        return direct

    matches = sorted(path for path in companies_dir.glob(f"{company}-*") if path.is_dir())
    if len(matches) == 1:
        return matches[0]
    if not matches and COMPANY_RE.match(company):
        direct.mkdir(parents=True, exist_ok=True)
        return direct
    if not matches:
        raise SystemExit(f"未找到公司目录：{company}")
    raise SystemExit(f"公司代码匹配到多个目录，请使用完整目录名：{', '.join(path.name for path in matches)}")


def normalize_filename(value: str) -> str:
    value = SAFE_FILENAME_RE.sub("_", value.strip()).strip("._-")
    if not value:
        value = "legal_opinion"
    if not value.endswith(".html"):
        value = f"{value}.html"
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Save a legal opinion HTML into WIKI_ROOT/companies/<company>/legal/")
    parser.add_argument("company", help="公司目录名或股票代码，例如 000333-美的集团 或 000333")
    parser.add_argument("html_file", help="已生成的 HTML 文件路径")
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT), help="Wiki 根目录，默认读取 SIQ_WIKI_ROOT/WIKI_ROOT")
    parser.add_argument("--filename", help="保存后的文件名，默认 legal_opinion_<timestamp>.html")
    args = parser.parse_args()

    source = Path(args.html_file).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"HTML 文件不存在：{source}")
    if source.suffix.lower() != ".html":
        raise SystemExit("法律意见书必须保存为 .html 文件")

    company_dir = resolve_company_dir(Path(args.wiki_root).expanduser(), args.company)
    legal_dir = company_dir / "legal"
    legal_dir.mkdir(parents=True, exist_ok=True)

    filename = normalize_filename(args.filename or f"legal_opinion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    target = legal_dir / filename
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
