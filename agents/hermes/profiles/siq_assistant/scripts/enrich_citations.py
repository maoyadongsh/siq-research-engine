#!/usr/bin/env python3
"""
SIQ Assistant Citation Enricher

批量补全引用文本中的 PDF 页码、table_index、md_line、task_id。
这是 citation_contract.md 规定的强制流程的脚本实现。

用法:
    python enrich_citations.py --company "广汽集团" --input citations.txt
    python enrich_citations.py --company "601238" --input -  # 从 stdin 读取
    echo "source_type=wiki_metrics, file=metrics/key_metrics.json, metric=营业收入, period=2025, task_id=xxx, pdf_page=未返回, table_index=9, md_line=147" | python enrich_citations.py --company "广汽集团" --input -

输出:
    补全后的引用文本，包含 pdf_page、可打开链接。
"""

import argparse
import os
import sys
import re
from pathlib import Path

os.environ.setdefault("SIQ_WIKI_ROOT", "/home/maoyd/siq-research-engine/data/wiki")
os.environ.setdefault("SIQ_DEFAULT_SOURCE_TYPE", "wiki_metrics")

# 将 shared scripts 加入路径
sys.path.insert(0, str(Path("/home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts")))
from local_citations import (
    find_company_dir_from_text,
    enrich_citation_line,
    resolve_citation_refs,
    _with_urls,
    _format_citation_lines,
)

PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391").rstrip("/")


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def _extract_citation_lines(text: str) -> list[str]:
    """从文本中提取引用行（以 [N] 开头的行或包含 source_type= 的行）。"""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "source_type=" in stripped:
            lines.append(stripped)
        elif "source_type=" in stripped and "file=" in stripped:
            lines.append(stripped)
    return lines


def _is_header_line(line: str) -> bool:
    return line.strip() in ("## 引用来源", "## 引用來源", "## Citation Sources")


def enrich_citations(text: str, company_text: str) -> str:
    """对文本中的所有引用行进行补全。"""
    lines = text.splitlines()
    output_lines = []
    context_text = company_text + "\n" + text

    for line in lines:
        if _is_header_line(line):
            output_lines.append(line)
            continue
        if "source_type=" in line:
            enriched = enrich_citation_line(line, context_text)
            # 如果 enrich_citation_line 没有添加链接，手动添加
            if "[打开PDF页]" not in enriched and "pdf_page=" in enriched:
                enriched = _add_links(enriched)
            output_lines.append(enriched)
        else:
            output_lines.append(line)

    return "\n".join(output_lines)


def _add_links(line: str) -> str:
    """为已补全页码的引用行添加可打开链接。"""
    task_match = re.search(r"\b(?:evidence_id/)?task_id=([0-9a-fA-F-]{32,36})", line)
    page_match = re.search(r"\bpdf_page(?:_number)?=([0-9]+)", line)
    table_match = re.search(r"\btable_index=([0-9]+)", line)

    if not task_match or not page_match:
        return line

    task_id = task_match.group(1)
    page = page_match.group(1)
    table_index = table_match.group(1) if table_match else None

    links = [f"[打开PDF页]({public_api_url(f'/api/pdf_page/{task_id}/{page}')})"]
    links.append(f"[查看页来源]({public_api_url(f'/api/source/{task_id}/page/{page}')})")
    if table_index:
        links.append(f"[查看表格]({public_api_url(f'/api/source/{task_id}/table/{table_index}')})")

    return line + "，" + "，".join(links)


def generate_citations_from_refs(company_text: str, metric_text: str, period_text: str,
                                  source_type: str = "wiki_metrics",
                                  file_name: str = "metrics/key_metrics.json") -> str:
    """从 local_citations.py 解析结果直接生成格式化引用。"""
    result = resolve_citation_refs(
        company_text,
        metric_text,
        period_text,
        source_type=source_type,
        file_name=file_name,
    )
    return _format_citation_lines(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="补全 SIQ 引用中的 PDF 页码和链接")
    parser.add_argument("--company", required=True, help="公司简称、股票代码或 company_id")
    parser.add_argument("--input", default="-", help="输入文件路径，- 表示从 stdin 读取")
    parser.add_argument("--metric", default="", help="指标名（用于直接生成模式）")
    parser.add_argument("--period", default="", help="报告期（用于直接生成模式）")
    parser.add_argument("--source-type", default="wiki_metrics")
    parser.add_argument("--file", default="metrics/key_metrics.json")
    parser.add_argument("--output", default="-", help="输出文件路径，- 表示输出到 stdout")
    args = parser.parse_args()

    # 直接生成模式：不读取输入，直接从指标解析
    if args.metric:
        citations = generate_citations_from_refs(
            args.company, args.metric, args.period,
            args.source_type, args.file
        )
        if args.output == "-":
            print(citations)
        else:
            Path(args.output).write_text(citations, encoding="utf-8")
        return 0

    # 补全模式：读取输入文本，补全已有引用
    if args.input == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.input).read_text(encoding="utf-8")

    enriched = enrich_citations(text, args.company)

    if args.output == "-":
        print(enriched)
    else:
        Path(args.output).write_text(enriched, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
