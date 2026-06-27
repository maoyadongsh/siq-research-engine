#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sec_evidence_lib import normalize_metrics, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize SEC raw XBRL facts into SIQ canonical metrics.")
    parser.add_argument("--package", type=Path, required=True, help="Evidence package directory")
    args = parser.parse_args()

    package_dir = args.package.resolve()
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    facts_payload = json.loads((package_dir / "xbrl" / "facts_raw.json").read_text(encoding="utf-8"))
    table_payload = json.loads((package_dir / "tables" / "table_index.json").read_text(encoding="utf-8")) if (package_dir / "tables" / "table_index.json").exists() else {}
    metrics = normalize_metrics(manifest, facts_payload.get("facts") or [], [])
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"schema_version": "sec_normalized_metrics_v1", "metrics": metrics["normalized_metrics"]})
    write_json(package_dir / "metrics" / "financial_data.json", metrics["financial_data"])
    write_json(package_dir / "metrics" / "financial_checks.json", metrics["financial_checks"])
    print(f"metrics={len(metrics['normalized_metrics'])} tables={len(table_payload.get('tables') or [])}")


if __name__ == "__main__":
    main()
