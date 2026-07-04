from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kr_pdf_wiki_lib import normalize_kr_ticker, write_kr_pdf_wiki_package


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ingest_kr_case_set(
    case_set_path: Path,
    output_root: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    payload = _read_json(case_set_path)
    selected = payload.get("cases", [])
    if not isinstance(selected, list):
        selected = []
    if ticker:
        wanted = normalize_kr_ticker(ticker)
        selected = [case for case in selected if isinstance(case, dict) and normalize_kr_ticker(case.get("ticker")) == wanted]
    if limit:
        selected = selected[:limit]

    packages = []
    failures = []
    for case in selected:
        if not isinstance(case, dict):
            failures.append({"case": case, "error": "case must be an object"})
            continue
        try:
            package_dir = write_kr_pdf_wiki_package(
                Path(case["pdf_path"]),
                Path(case["parser_result_dir"]),
                output_root,
                None,
                force=force,
            )
            packages.append(str(package_dir))
        except Exception as exc:
            failures.append({"case": case, "error": str(exc)})
    result = {
        "market": "KR",
        "case_set": str(case_set_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selected": len(selected),
        "created": len(packages),
        "failed": len(failures),
        "packages": packages,
        "failures": failures,
    }
    _write_json(output_root / "_meta" / "ingest_manifest.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest KR PDF case set into wiki packages")
    parser.add_argument("--case-set", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/wiki/kr"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ticker")
    args = parser.parse_args()
    result = ingest_kr_case_set(args.case_set, args.output_root, force=args.force, limit=args.limit, ticker=args.ticker)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
