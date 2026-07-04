from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from kr_pdf_wiki_lib import normalize_kr_ticker


def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _manifest_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("downloads", "items", "cases"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _find_pdf(downloads_root: Path, manifest: dict[str, Any], parser_manifest: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    for key in ("pdf_path", "local_path", "path", "source_file"):
        value = parser_manifest.get(key)
        if value:
            candidates.append(Path(str(value)))

    ticker = normalize_kr_ticker(parser_manifest.get("ticker"))
    for item in _manifest_items(manifest):
        if normalize_kr_ticker(item.get("ticker")) != ticker:
            continue
        for key in ("pdf_path", "local_path", "path", "file"):
            value = item.get(key)
            if value:
                candidates.append(Path(str(value)))

    for candidate in candidates:
        paths = [candidate]
        if not candidate.is_absolute():
            paths.extend([downloads_root / candidate, downloads_root / candidate.name])
        for path in paths:
            if path.exists() and path.suffix.lower() == ".pdf":
                return path

    if downloads_root.exists() and ticker != "000000":
        matches = sorted(downloads_root.rglob(f"*{ticker}*.pdf"))
        if matches:
            return matches[0]
    return None


def discover_kr_cases(results_root: Path, manifest_path: Path | None, downloads_root: Path) -> list[dict[str, Any]]:
    manifest = _read_json(manifest_path)
    cases: list[dict[str, Any]] = []
    for parser_manifest_path in sorted(results_root.glob("*/manifest.json")):
        parser_manifest = _read_json(parser_manifest_path)
        if str(parser_manifest.get("market", "")).upper() != "KR":
            continue
        pdf_path = _find_pdf(downloads_root, manifest, parser_manifest)
        if not pdf_path:
            continue
        cases.append(
            {
                "market": "KR",
                "ticker": normalize_kr_ticker(parser_manifest.get("ticker")),
                "company_name": parser_manifest.get("company_name") or parser_manifest.get("company") or "",
                "report_year": parser_manifest.get("report_year") or parser_manifest.get("fiscal_year"),
                "report_type": parser_manifest.get("report_type") or "annual",
                "pdf_path": str(pdf_path),
                "parser_result_dir": str(parser_manifest_path.parent),
            }
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover KR parsed PDF cases")
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--downloads-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    cases = discover_kr_cases(args.results_root, args.manifest, args.downloads_root)
    if args.limit:
        cases = cases[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"market": "KR", "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {args.output}")


if __name__ == "__main__":
    main()
